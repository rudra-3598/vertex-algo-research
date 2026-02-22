import streamlit as st
import pandas as pd
import pandas_ta as ta
import mplfinance as mpf
import datetime
import calendar
import time
import os
import pdfkit
import smtplib
from email.message import EmailMessage

# --- DATABASES ---
@st.cache_data
def load_fno_base_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [t for t in df['SYMBOL'].dropna().unique()]
        except: return ["NIFTY", "BANKNIFTY", "RELIANCE"]
    return ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "SBI"]

fno_base_universe = load_fno_base_stocks()

# --- STATE MANAGEMENT (TO PREVENT DATA VANISHING) ---
if 'grid_scan_results' not in st.session_state:
    st.session_state['grid_scan_results'] = []

# --- HELPER FUNCTIONS ---
def get_current_monthly_expiry():
    now = datetime.datetime.now()
    cal = calendar.monthcalendar(now.year, now.month)
    last_thursday = cal[-1][calendar.THURSDAY] if cal[-1][calendar.THURSDAY] != 0 else cal[-2][calendar.THURSDAY]
    expiry_date = datetime.date(now.year, now.month, last_thursday)
    if now.date() > expiry_date:
        next_month = now.month + 1 if now.month < 12 else 1
        next_year = now.year if now.month < 12 else now.year + 1
        cal = calendar.monthcalendar(next_year, next_month)
        last_thursday = cal[-1][calendar.THURSDAY] if cal[-1][calendar.THURSDAY] != 0 else cal[-2][calendar.THURSDAY]
        expiry_date = datetime.date(next_year, next_month, last_thursday)
    return expiry_date.strftime("%y%b").upper()

def get_atm_strike(symbol, ltp):
    if symbol == "NIFTY": return round(ltp / 50) * 50
    elif symbol == "BANKNIFTY": return round(ltp / 100) * 100
    elif ltp < 500: return round(ltp / 5) * 5
    elif ltp < 1000: return round(ltp / 10) * 10
    elif ltp < 3000: return round(ltp / 20) * 20
    else: return round(ltp / 50) * 50

def get_spot_symbol(sym):
    if sym == "NIFTY": return "NSE:NIFTY50-INDEX"
    elif sym == "BANKNIFTY": return "NSE:NIFTYBANK-INDEX"
    return f"NSE:{sym}-EQ"

def get_fut_symbol(sym, expiry):
    if sym in ["NIFTY", "BANKNIFTY"]: return f"NSE:{sym}{expiry}FUT" # Assuming basic index fut format
    return f"NSE:{sym}{expiry}FUT"

# --- FYERS API FETCHER ---
def fetch_fyers_data(fyers, symbol, resolution, days_back):
    now = datetime.datetime.now()
    range_from = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    range_to = now.strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": str(resolution), "date_format": "1", "range_from": range_from, "range_to": range_to, "cont_flag": "1"}
    response = fyers.history(data=data)
    if response.get("s") == "ok" and response.get("candles"):
        df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
        df.set_index('datetime', inplace=True)
        return df
    return None

# --- ENGINE 1: HOT OI BUILDUP (The 5-Min Radar) ---
def analyze_oi_buildup(df_fut):
    if df_fut is None or len(df_fut) < 2: return "Neutral OI", "Data Unavailable"
    prev_close, curr_close = df_fut['close'].iloc[-2], df_fut['close'].iloc[-1]
    prev_vol, curr_vol = df_fut['volume'].iloc[-2], df_fut['volume'].iloc[-1]
    
    price_change = ((curr_close - prev_close) / prev_close) * 100
    vol_change = ((curr_vol - prev_vol) / prev_vol) * 100 if prev_vol > 0 else 0
    
    if price_change > 0.05 and vol_change > 2: return "Long Buildup", f"Price Up {price_change:.2f}%, Vol Up {vol_change:.1f}%"
    elif price_change < -0.05 and vol_change > 2: return "Short Buildup", f"Price Down {price_change:.2f}%, Vol Up {vol_change:.1f}%"
    elif price_change > 0.05 and vol_change < 0: return "Short Covering", f"Price Up {price_change:.2f}%, Vol Down (Covering)"
    elif price_change < -0.05 and vol_change < 0: return "Long Unwinding", f"Price Down {price_change:.2f}%, Vol Down (Unwinding)"
    return "Neutral", "No major OI/Volume shift"

# --- ENGINE 2: QUANT SPOT TA ---
def analyze_spot_technicals(df):
    if df is None or len(df) < 30: return "⚪ INSUFFICIENT DATA", 0, 0, 0, 0, False, "Not enough data fetched."
    try:
        df.ta.vwap(append=True)
        df.ta.ema(length=21, append=True)
        df.ta.atr(length=14, append=True)
        df.ta.rsi(length=14, append=True)
        
        bb = df.ta.bbands(length=20, std=2)
        kc = df.ta.kc(length=20, scalar=1.5)
        is_squeeze = False
        if bb is not None and kc is not None:
            is_squeeze = (bb.iloc[-1, 0] > kc.iloc[-1, 0]) and (bb.iloc[-1, 2] < kc.iloc[-1, 2])
            
        close, vwap, ema21 = df['close'].iloc[-1], df['VWAP_D'].iloc[-1] if 'VWAP_D' in df.columns else df['close'].iloc[-1], df['EMA_21'].iloc[-1]
        atr, rsi = df['ATRr_14'].iloc[-1], df['RSI_14'].iloc[-1]
        
        if close > vwap and close > ema21 and rsi > 55:
            trend = "🟢 BULLISH EXPANSION"
            sl, tgt = max(vwap, ema21) - (atr * 0.5), close + (atr * 3.0)
            rationale = f"Spot is structurally Bullish. Trading above 21-EMA (Rs.{ema21:.1f}) and VWAP. RSI shows momentum at {rsi:.1f}."
        elif close < vwap and close < ema21 and rsi < 45:
            trend = "🔴 BEARISH DISTRIBUTION"
            sl, tgt = min(vwap, ema21) + (atr * 0.5), close - (atr * 3.0)
            rationale = f"Spot is structurally Bearish. Heavy rejection below VWAP and 21-EMA (Rs.{ema21:.1f}). RSI shows weakness at {rsi:.1f}."
        else:
            trend = "⚪ NEUTRAL - CHOPPY"
            sl, tgt, rationale = close, close, "Spot is trapped in a sideways range. Waiting for directional breakout."
            
        return trend, close, sl, tgt, atr, is_squeeze, rationale
    except Exception as e: return "⚪ CALCULATION ERROR", 0, 0, 0, 0, False, str(e)

# --- CHART GENERATOR ---
def generate_dual_chart(df_spot, df_opt, spot_sym, opt_sym):
    df_s_plot, df_o_plot = df_spot.tail(80), df_opt.tail(80)
    ap_s = [mpf.make_addplot(df_s_plot['VWAP_D'], color='#1f77b4', width=1.5)] if 'VWAP_D' in df_s_plot.columns else []
    mc = mpf.make_marketcolors(up='#2d5a00', down='#d93025', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)
    
    mpf.plot(df_s_plot, type='candle', style=s, addplot=ap_s, volume=True, title=f"SPOT: {spot_sym}", savefig=dict(fname='spot_chart.png', dpi=100, bbox_inches='tight'))
    mpf.plot(df_o_plot, type='candle', style=s, volume=True, title=f"OPTION PREMIUM: {opt_sym}", savefig=dict(fname='opt_chart.png', dpi=100, bbox_inches='tight'))
    return 'spot_chart.png', 'opt_chart.png'

# --- EMAIL REPORT LOGIC (TRIPLE CONFLUENCE) ---
def email_detailed_setup(fyers, symbol, profile, email, max_risk):
    auto_expiry = get_current_monthly_expiry()
    spot_symbol = get_spot_symbol(symbol)
    fut_symbol = get_fut_symbol(symbol, auto_expiry)
    
    # 1. Spot Analysis
    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 5)
    trend, spot_price, spot_sl, spot_tgt, spot_atr, is_squeeze, spot_rationale = analyze_spot_technicals(df_spot)
    
    # 2. Future/OI Analysis
    df_fut = fetch_fyers_data(fyers, fut_symbol, "15", 2)
    oi_status, oi_details = analyze_oi_buildup(df_fut)
    
    atm_strike = get_atm_strike(symbol, spot_price)
    
    # 3. Option Selection & Analysis
    if "BULLISH" in trend:
        opt_symbol = f"NSE:{symbol}{auto_expiry}{atm_strike}CE" if profile == "Option Buyer" else f"NSE:{symbol}{auto_expiry}{int(atm_strike - (spot_atr*4))}PE"
        action = f"BUY {atm_strike} CE" if profile == "Option Buyer" else f"SELL {int(atm_strike - (spot_atr*4))} PE"
    else:
        opt_symbol = f"NSE:{symbol}{auto_expiry}{atm_strike}PE" if profile == "Option Buyer" else f"NSE:{symbol}{auto_expiry}{int(atm_strike + (spot_atr*4))}CE"
        action = f"BUY {atm_strike} PE" if profile == "Option Buyer" else f"SELL {int(atm_strike + (spot_atr*4))} CE"

    df_opt = fetch_fyers_data(fyers, opt_symbol, "15", 5)
    if df_opt is None:
        st.error(f"Failed to fetch Option data for {opt_symbol}")
        return
        
    df_opt.ta.atr(length=14, append=True)
    df_opt.ta.vwap(append=True)
    opt_price = df_opt['close'].iloc[-1]
    opt_atr = df_opt['ATRr_14'].iloc[-1] if 'ATRr_14' in df_opt.columns else opt_price * 0.05
    opt_vwap = df_opt['VWAP_D'].iloc[-1] if 'VWAP_D' in df_opt.columns else opt_price
    
    if profile == "Option Buyer": 
        opt_sl, opt_tgt = opt_price - (opt_atr * 1.5), opt_price + (opt_atr * 4.0)
    else: 
        opt_sl, opt_tgt = opt_price + (opt_atr * 2.0), opt_price * 0.1 
        
    risk_per_unit = abs(opt_price - opt_sl)
    rec_quantity = int(max_risk / risk_per_unit) if risk_per_unit > 0 else 0
    
    # --- DEEP AI SUMMARY ---
    ai_summary = f"""
    <strong>The Triple Confluence Logic:</strong><br>
    <strong>1. Cash Market (Spot):</strong> {spot_rationale} ATR Volatility stands at {spot_atr:.2f}.<br>
    <strong>2. Derivatives Activity (OI):</strong> The Futures chart reveals <strong>{oi_status}</strong> ({oi_details}), confirming that institutional money is aligning with the Spot trend.<br>
    <strong>3. Premium Analysis:</strong> The {opt_symbol} is trading at Rs. {opt_price:.2f} (VWAP: Rs. {opt_vwap:.2f}). Because you are an <em>{profile}</em>, we are establishing a strict Volatility-Based Stoploss exactly outside the {opt_atr:.2f} ATR band to avoid algorithm stop-hunting.
    """
    
    spot_img, opt_img = generate_dual_chart(df_spot, df_opt, symbol, opt_symbol)
    
    html = f"""
    <html><body style="font-family: Arial, sans-serif; padding: 20px; color: #222;">
        <h1 style="color: #2d5a00; border-bottom: 3px solid #2d5a00; padding-bottom: 10px;">VERTEX ALGO | DEEP QUANT REPORT</h1>
        <h2>Asset: {symbol} | Trade Profile: {profile}</h2>
        <div style="background-color: #f8f9fa; padding: 20px; border-left: 5px solid #2d5a00; margin-bottom:20px;">
            <h3 style="margin-top:0;">ACTION: {action}</h3>
            <p><strong>Entry Zone:</strong> Rs. {opt_price:.2f}</p>
            <p><strong>Primary Target:</strong> Rs. {opt_tgt:.2f}</p>
            <p><strong>Dynamic Stoploss (ATR Based):</strong> Rs. {opt_sl:.2f}</p>
            <hr>
            <p><strong>Suggested Qty (Max Risk Rs.{max_risk}):</strong> {rec_quantity} Units</p>
        </div>
        <div style="background-color: #fff; padding: 15px; border: 1px solid #ddd; margin-bottom:20px;">
            <h3 style="margin-top:0;">AI TRIPLE CONFLUENCE SUMMARY</h3>
            <p style="line-height:1.6;">{ai_summary}</p>
        </div>
        <img src="{os.path.abspath(spot_img)}" style="width: 100%; margin-bottom: 15px; border: 1px solid #ccc;">
        <img src="{os.path.abspath(opt_img)}" style="width: 100%; border: 1px solid #ccc;">
    </body></html>
    """
    pdf_file = f"Vertex_{symbol}_TearSheet.pdf"
    with open('temp_report.html', 'w') as f: f.write(html)
    
    try:
        pdfkit.from_file('temp_report.html', pdf_file, options={'enable-local-file-access': None})
    except Exception as e:
        st.error(f"PDF Generation Failed. Ensure wkhtmltopdf is in packages.txt. Error: {e}")
        return
    
    try:
        SENDER_EMAIL = st.secrets["EMAIL_USER"]
        APP_PASS = st.secrets["EMAIL_PASS"]
        msg = EmailMessage()
        msg['Subject'], msg['From'], msg['To'] = f'Vertex Deep Setup: {action}', SENDER_EMAIL, email
        msg.set_content("Your highly detailed Spot + OI + Options AI Report is attached.")
        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
        st.success(f"✅ Deep Tear Sheet emailed successfully for {symbol}!")
    except Exception as e: st.error(f"Email failed to send. Check App Password. Error: {e}")

# --- MAIN UI RENDERER ---
def render_ui(fyers):
    st.markdown("### 🚀 Vertex Institutional Derivatives Engine")
    
    col_a, col_b, col_c = st.columns([1.5, 1, 1.5])
    with col_a: trader_profile = st.radio("Strategy Bias", ["Option Buyer", "Option Seller"], horizontal=True)
    with col_b: max_risk = st.number_input("Max Risk/Trade (₹)", min_value=500, value=2500, step=500)
    with col_c: user_email = st.text_input("Delivery Email")

    tab_radar, tab_grid = st.tabs(["🔥 5-Min Hot OI Radar", "📊 Alpha 208-Stock Grid (Cards)"])
    
    with tab_radar:
        # ... (Same OI Radar code as previous version) ...
        st.write("Scan this every 5 minutes to catch sudden volume and Open Interest spikes in Futures.")
        if st.button("📡 Scan Live OI Radar Now", type="primary"):
            st.info("Scanning Futures market...")
            auto_expiry = get_current_monthly_expiry()
            buildup_data = []
            progress = st.progress(0)
            status_text = st.empty()
            for i, sym in enumerate(fno_base_universe):
                status_text.text(f"Pinging Futures Data: {sym} ({i+1}/{len(fno_base_universe)})")
                scan_sym = get_fut_symbol(sym, auto_expiry)
                try:
                    df_scan = fetch_fyers_data(fyers, scan_sym, "5", 1)
                    trend, details = analyze_oi_buildup(df_scan)
                    if "Neutral" not in trend:
                        ltp = df_scan['close'].iloc[-1] if df_scan is not None else 0
                        buildup_data.append({"Asset": sym, "LTP": round(ltp, 2), "Live Bias": trend, "Details": details})
                except: pass
                time.sleep(0.15)
                progress.progress((i + 1) / len(fno_base_universe))
            status_text.text("Radar Scan Complete!")
            if buildup_data:
                st.dataframe(pd.DataFrame(buildup_data).style.applymap(lambda x: 'background-color: #d4edda' if 'Long' in str(x) else 'background-color: #f8d7da' if 'Short' in str(x) else '', subset=['Live Bias']), use_container_width=True)

    with tab_grid:
        st.markdown("#### Deep Technical Setup Grid")
        st.write("Calculates Spot TA. Clicking Email will combine Spot + OI + Option Premium into one report.")
        
        if st.button("🚀 Run Full Grid Scan", use_container_width=True):
            st.markdown("---")
            progress2 = st.progress(0)
            status_text2 = st.empty()
            
            temp_results = []
            total_scan = len(fno_base_universe) 
            
            for i, sym in enumerate(fno_base_universe):
                status_text2.text(f"Scanning Spot Structure: {sym} ({i+1}/{total_scan})")
                spot_symbol = get_spot_symbol(sym)
                try:
                    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 4)
                    trend, spot_price, spot_sl, spot_tgt, atr, is_squeeze, rationale = analyze_spot_technicals(df_spot)
                    temp_results.append({"sym": sym, "trend": trend, "spot_price": spot_price, "atr": atr, "is_squeeze": is_squeeze})
                except Exception as e: pass
                time.sleep(0.2) 
                progress2.progress((i + 1) / total_scan)
            
            # SAVE TO SESSION STATE SO IT DOESN'T VANISH
            st.session_state['grid_scan_results'] = temp_results
            status_text2.text(f"Scan Complete! All cards rendered.")

        # DRAW CARDS FROM SESSION STATE
        if st.session_state['grid_scan_results']:
            cols = st.columns(3)
            for i, item in enumerate(st.session_state['grid_scan_results']):
                col = cols[i % 3]
                with col:
                    sym, trend, spot_price, atr, is_squeeze = item['sym'], item['trend'], item['spot_price'], item['atr'], item['is_squeeze']
                    bg_color = "#e8f5e9" if "BULLISH" in trend else "#ffebee" if "BEARISH" in trend else "#f8f9fa"
                    border = "#4caf50" if "BULLISH" in trend else "#f44336" if "BEARISH" in trend else "#ced4da"
                    squeeze_html = '<div style="margin-top: 5px;"><span style="background-color: #ff9800; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold;">🚨 SQUEEZE DETECTED</span></div>' if is_squeeze else ""
                    
                    st.markdown(f"""
                    <div style="background-color: {bg_color}; border-left: 5px solid {border}; padding: 15px; border-radius: 6px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; color: #111; font-size: 18px;">{sym}</h3>
                            <span style="font-size: 12px; color: #666; font-weight: bold;">₹{spot_price:.1f}</span>
                        </div>
                        <p style="margin: 8px 0; font-size: 13px; font-weight: 700; color: {border};">{trend}</p>
                        <div style="font-size: 11px; color: #555;">Vol (ATR): {atr:.2f}</div>
                        {squeeze_html}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if "NEUTRAL" not in trend:
                        if st.button(f"⚡ Tear Sheet for {sym}", key=f"btn_{sym}"):
                            if user_email:
                                with st.spinner(f"Running TRIPLE CONFLUENCE checks for {sym}..."):
                                    email_detailed_setup(fyers, sym, trader_profile, user_email, max_risk)
                            else:
                                st.error("Enter delivery email at the top.")
