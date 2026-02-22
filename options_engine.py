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
    if df_fut is None or len(df_fut) < 2: return "Neutral", "#666666"
    
    prev_close, curr_close = df_fut['close'].iloc[-2], df_fut['close'].iloc[-1]
    prev_vol, curr_vol = df_fut['volume'].iloc[-2], df_fut['volume'].iloc[-1]
    
    price_change = ((curr_close - prev_close) / prev_close) * 100
    vol_change = ((curr_vol - prev_vol) / prev_vol) * 100 if prev_vol > 0 else 0
    
    if price_change > 0.05 and vol_change > 2: return "🟢 Long Buildup", "#2d5a00"
    elif price_change < -0.05 and vol_change > 2: return "🔴 Short Buildup", "#d93025"
    elif price_change > 0.05 and vol_change < 0: return "🟡 Short Covering", "#ffcc00"
    elif price_change < -0.05 and vol_change < 0: return "🟠 Long Unwinding", "#ff8c00"
    return "⚪ Neutral", "#666666"

# --- ENGINE 2: QUANT SPOT & OPTIONS TA (The Alpha Grid) ---
def analyze_spot_technicals(df):
    if df is None or len(df) < 30: return "⚪ INSUFFICIENT DATA", 0, 0, 0, 0, False, "Not enough data fetched."
    
    try:
        df.ta.vwap(append=True)
        df.ta.ema(length=9, append=True)
        df.ta.ema(length=21, append=True)
        df.ta.ema(length=50, append=True)
        df.ta.atr(length=14, append=True)
        df.ta.rsi(length=14, append=True)
        
        # Safe Squeeze Detection
        bb = df.ta.bbands(length=20, std=2)
        kc = df.ta.kc(length=20, scalar=1.5)
        is_squeeze = False
        if bb is not None and kc is not None:
            # BB lower > KC lower AND BB upper < KC upper
            is_squeeze = (bb.iloc[-1, 0] > kc.iloc[-1, 0]) and (bb.iloc[-1, 2] < kc.iloc[-1, 2])
            
        close = df['close'].iloc[-1]
        vwap = df['VWAP_D'].iloc[-1] if 'VWAP_D' in df.columns else close
        ema9, ema21 = df['EMA_9'].iloc[-1], df['EMA_21'].iloc[-1]
        atr, rsi = df['ATRr_14'].iloc[-1], df['RSI_14'].iloc[-1]
        
        if close > vwap and ema9 > ema21 and rsi > 55:
            trend = "🟢 BULLISH EXPANSION"
            sl, tgt = max(vwap, ema21) - (atr * 0.5), close + (atr * 3.0)
            rationale = f"Bullish momentum (RSI: {rsi:.1f}) above VWAP (Rs.{vwap:.2f}) and 21-EMA."
        elif close < vwap and ema9 < ema21 and rsi < 45:
            trend = "🔴 BEARISH DISTRIBUTION"
            sl, tgt = min(vwap, ema21) + (atr * 0.5), close - (atr * 3.0)
            rationale = f"Heavy rejection below VWAP (Rs.{vwap:.2f}) and 21-EMA. RSI: {rsi:.1f}."
        else:
            trend = "⚪ NEUTRAL - CHOPPY"
            sl, tgt, rationale = close, close, "Trapped in sideways volatility. Wait."
            
        return trend, close, sl, tgt, atr, is_squeeze, rationale
    except Exception as e:
        return "⚪ CALCULATION ERROR", 0, 0, 0, 0, False, str(e)

# --- CHART GENERATOR ---
def generate_dual_chart(df_spot, df_opt, spot_sym, opt_sym):
    df_s_plot, df_o_plot = df_spot.tail(80), df_opt.tail(80)
    ap_s = [mpf.make_addplot(df_s_plot['VWAP_D'], color='#1f77b4', width=1.5)] if 'VWAP_D' in df_s_plot.columns else []
    mc = mpf.make_marketcolors(up='#2d5a00', down='#d93025', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)
    
    mpf.plot(df_s_plot, type='candle', style=s, addplot=ap_s, volume=True, title=f"SPOT: {spot_sym}", savefig=dict(fname='spot_chart.png', dpi=100, bbox_inches='tight'))
    mpf.plot(df_o_plot, type='candle', style=s, volume=True, title=f"OPTION: {opt_sym}", savefig=dict(fname='opt_chart.png', dpi=100, bbox_inches='tight'))
    return 'spot_chart.png', 'opt_chart.png'

# --- EMAIL REPORT LOGIC ---
def email_detailed_setup(fyers, symbol, profile, email, max_risk):
    auto_expiry = get_current_monthly_expiry()
    spot_symbol = get_spot_symbol(symbol)
    
    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 5)
    trend, spot_price, spot_sl, spot_tgt, spot_atr, is_squeeze, spot_rationale = analyze_spot_technicals(df_spot)
    
    if "NEUTRAL" in trend or "ERROR" in trend or "INSUFFICIENT" in trend:
        st.warning(f"Market is currently Neutral/Choppy for {symbol}. No setup generated.")
        return
        
    atm_strike = get_atm_strike(symbol, spot_price)
    
    if "BULLISH" in trend:
        if profile == "Option Buyer": opt_symbol, action = f"NSE:{symbol}{auto_expiry}{atm_strike}CE", f"BUY {atm_strike} CE"
        else: opt_symbol, action = f"NSE:{symbol}{auto_expiry}{int(atm_strike - (spot_atr*4))}PE", f"SELL {int(atm_strike - (spot_atr*4))} PE"
    else:
        if profile == "Option Buyer": opt_symbol, action = f"NSE:{symbol}{auto_expiry}{atm_strike}PE", f"BUY {atm_strike} PE"
        else: opt_symbol, action = f"NSE:{symbol}{auto_expiry}{int(atm_strike + (spot_atr*4))}CE", f"SELL {int(atm_strike + (spot_atr*4))} CE"

    df_opt = fetch_fyers_data(fyers, opt_symbol, "15", 5)
    if df_opt is None:
        st.error(f"Failed to fetch precise Option data for {opt_symbol}")
        return
        
    df_opt.ta.atr(length=14, append=True)
    opt_price = df_opt['close'].iloc[-1]
    opt_atr = df_opt['ATRr_14'].iloc[-1] if 'ATRr_14' in df_opt.columns else opt_price * 0.05
    
    if profile == "Option Buyer": opt_sl, opt_tgt = opt_price - (opt_atr * 1.5), opt_price + (opt_atr * 4.0)
    else: opt_sl, opt_tgt = opt_price + (opt_atr * 2.0), opt_price * 0.1 
        
    risk_per_unit = abs(opt_price - opt_sl)
    rr_ratio = abs(opt_tgt - opt_price) / risk_per_unit if risk_per_unit > 0 else 0
    rec_quantity = int(max_risk / risk_per_unit) if risk_per_unit > 0 else 0
    
    spot_img, opt_img = generate_dual_chart(df_spot, df_opt, symbol, opt_symbol)
    
    html = f"""
    <html><body style="font-family: Arial, sans-serif; padding: 20px; color: #222;">
        <h1 style="color: #2d5a00; border-bottom: 3px solid #2d5a00; padding-bottom: 10px;">VERTEX ALGO | QUANTITATIVE TEAR SHEET</h1>
        <h2>Asset: {symbol} | Trade Profile: {profile}</h2>
        <div style="background-color: #f8f9fa; padding: 20px; border-left: 5px solid #2d5a00;">
            <h3>ACTION: {action}</h3>
            <p><strong>Premium Entry Zone:</strong> Rs. {opt_price:.2f}</p>
            <p><strong>Primary Target:</strong> Rs. {opt_tgt:.2f}</p>
            <p><strong>Dynamic Stoploss (ATR Based):</strong> Rs. {opt_sl:.2f}</p>
            <hr>
            <p><strong>Risk/Reward Ratio:</strong> 1 : {rr_ratio:.2f}</p>
            <p><strong>Suggested Qty (Max Risk Rs.{max_risk}):</strong> {rec_quantity} Units</p>
        </div>
        <h3>AI TECHNICAL RATIONALE</h3>
        <p>{spot_rationale} Options ATR is {opt_atr:.2f}. Stoploss placed exactly outside the volatility band.</p>
        <img src="{os.path.abspath(spot_img)}" style="width: 100%; margin-bottom: 15px; border: 1px solid #ccc;">
        <img src="{os.path.abspath(opt_img)}" style="width: 100%; border: 1px solid #ccc;">
    </body></html>
    """
    pdf_file = f"Vertex_{symbol}_TearSheet.pdf"
    with open('temp_report.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp_report.html', pdf_file, options={'enable-local-file-access': None})
    
    try:
        SENDER_EMAIL = st.secrets["EMAIL_USER"]
        APP_PASS = st.secrets["EMAIL_PASS"]
        msg = EmailMessage()
        msg['Subject'], msg['From'], msg['To'] = f'Alpha Setup: {action}', SENDER_EMAIL, email
        msg.set_content("Please find your institutional Quant Tear Sheet attached.")
        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
        st.success(f"✅ Quant Tear Sheet emailed for {symbol}!")
    except Exception as e: st.error(f"Email failed: {e}")

# --- MAIN UI RENDERER ---
def render_ui(fyers):
    st.markdown("### 🚀 Vertex Institutional Derivatives Engine")
    
    # Global Inputs
    col_a, col_b, col_c = st.columns([1.5, 1, 1.5])
    with col_a: trader_profile = st.radio("Strategy Bias", ["Option Buyer", "Option Seller"], horizontal=True)
    with col_b: max_risk = st.number_input("Max Risk/Trade (₹)", min_value=500, value=2500, step=500)
    with col_c: user_email = st.text_input("Delivery Email")

    tab_radar, tab_grid = st.tabs(["🔥 5-Min Hot OI Radar", "📊 Alpha 208-Stock Grid (Cards)"])
    
    # ---------------------------------------------
    # TAB 1: The Restored HOT OI Radar (5-Min Scan)
    # ---------------------------------------------
    with tab_radar:
        st.markdown("#### Watch Institutional Positions Build Up (Live)")
        st.write("Scan this every 5 minutes to catch sudden volume and Open Interest spikes in Futures.")
        
        if st.button("📡 Scan Live OI Radar Now", type="primary"):
            st.info("Scanning Futures market...")
            auto_expiry = get_current_monthly_expiry()
            buildup_data = []
            progress = st.progress(0)
            status_text = st.empty()
            
            # Using full list but keeping it fast by requesting only last 2 candles
            for i, sym in enumerate(fno_base_universe):
                status_text.text(f"Pinging Futures Data: {sym} ({i+1}/{len(fno_base_universe)})")
                scan_sym = f"NSE:{sym}{auto_expiry}FUT" if sym not in ["NIFTY", "BANKNIFTY"] else f"NSE:{sym}FUT" # Basic fallback for indices if needed
                
                try:
                    df_scan = fetch_fyers_data(fyers, scan_sym, "5", 1) # 5-min timeframe, 1 day back
                    trend, color = analyze_oi_buildup(df_scan)
                    if "Neutral" not in trend: # Only show action
                        ltp = df_scan['close'].iloc[-1] if df_scan is not None else 0
                        buildup_data.append({"Asset": sym, "LTP": round(ltp, 2), "Live Institutional Bias": trend})
                except: pass
                
                time.sleep(0.15) # Faster sleep since we request less data
                progress.progress((i + 1) / len(fno_base_universe))
                
            status_text.text("Radar Scan Complete!")
            if buildup_data:
                df_display = pd.DataFrame(buildup_data)
                st.dataframe(df_display.style.applymap(
                    lambda x: 'background-color: #d4edda; color: #155724' if 'Long Buildup' in str(x) or 'Short Covering' in str(x) 
                    else 'background-color: #f8d7da; color: #721c24' if 'Short' in str(x) or 'Unwinding' in str(x) else '', 
                    subset=['Live Institutional Bias']), use_container_width=True)
            else:
                st.info("No major OI spikes detected in the last 5 minutes. Market is calm.")

    # ---------------------------------------------
    # TAB 2: The Alpha Grid (Cards for 208 Stocks)
    # ---------------------------------------------
    with tab_grid:
        st.markdown("#### Deep Technical Setup Grid")
        st.write("Generates individual setup cards and A-to-Z PDF Tear Sheets based on ATR volatility.")
        
        if st.button("🚀 Run Full Grid Scan", use_container_width=True):
            st.markdown("---")
            progress2 = st.progress(0)
            status_text2 = st.empty()
            
            cols = st.columns(3) 
            total_scan = len(fno_base_universe) 
            
            for i, sym in enumerate(fno_base_universe):
                status_text2.text(f"Scanning Spot Structure: {sym} ({i+1}/{total_scan})")
                spot_symbol = get_spot_symbol(sym)
                
                try:
                    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 4)
                    trend, spot_price, spot_sl, spot_tgt, atr, is_squeeze, rationale = analyze_spot_technicals(df_spot)
                    
                    # BUG FIX: Card rendering is now safely inside the loop and catches all scenarios
                    col = cols[i % 3]
                    with col:
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
                            <div style="font-size: 11px; color: #555; display: flex; justify-content: space-between;">
                                <span>Vol (ATR): {atr:.2f}</span>
                            </div>
                            {squeeze_html}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Only show Email button if it's a valid setup
                        if "BULLISH" in trend or "BEARISH" in trend:
                            if st.button(f"⚡ Tear Sheet for {sym}", key=f"btn_{sym}"):
                                if user_email:
                                    with st.spinner(f"Compiling Setup for {sym}..."):
                                        email_detailed_setup(fyers, sym, trader_profile, user_email, max_risk)
                                else:
                                    st.error("Enter delivery email at the top.")
                except Exception as e:
                    # Fallback card if absolute failure occurs (API block, etc)
                    col = cols[i % 3]
                    with col:
                        st.markdown(f"""<div style="background-color:#fff; border: 1px solid #f00; padding: 10px; margin-bottom:10px;"><h4 style="margin:0;">{sym}</h4><p style="font-size:10px;color:red;">API Error/Limit</p></div>""", unsafe_allow_html=True)
                    
                time.sleep(0.3) # Absolute requirement for Fyers Rate Limit
                progress2.progress((i + 1) / total_scan)
                
            status_text2.text(f"Scan Complete! All cards rendered.")
