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

# --- AUTO EXPIRY & STRIKE LOGIC ---
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

# --- QUANTITATIVE ANALYSIS ENGINE (SPOT) ---
def analyze_spot_technicals(df):
    if df is None or len(df) < 50: return "Neutral", 0, 0, 0, 0, False, "Insufficient Data"
    
    # Advanced Indicators
    df.ta.vwap(append=True)
    df.ta.ema(length=9, append=True)
    df.ta.ema(length=21, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.rsi(length=14, append=True)
    
    # Squeeze Detection (Bollinger Bands inside Keltner Channels = Volatility Contraction)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.kc(length=20, scalar=1.5, append=True)
    
    close = df['close'].iloc[-1]
    vwap = df['VWAP_D'].iloc[-1]
    ema9, ema21, ema50 = df['EMA_9'].iloc[-1], df['EMA_21'].iloc[-1], df['EMA_50'].iloc[-1]
    atr, rsi = df['ATRr_14'].iloc[-1], df['RSI_14'].iloc[-1]
    
    # Squeeze Logic: If BB lower band > KC lower band AND BB upper band < KC upper band
    is_squeeze = (df['BBL_20_2.0'].iloc[-1] > df['KCLe_20_1.5'].iloc[-1]) and (df['BBU_20_2.0'].iloc[-1] < df['KCUe_20_1.5'].iloc[-1])
    
    # Dynamic SL, Target, and R:R
    if close > vwap and ema9 > ema21 and rsi > 55:
        trend = "🟢 BULLISH EXPANSION"
        sl = max(vwap, ema21) - (atr * 0.5) 
        tgt = close + (atr * 3.0) 
        rationale = f"Spot exhibits strong bullish momentum (RSI: {rsi:.1f}). Structure is making higher highs above VWAP (Rs.{vwap:.2f}) and 21-EMA."
    elif close < vwap and ema9 < ema21 and rsi < 45:
        trend = "🔴 BEARISH DISTRIBUTION"
        sl = min(vwap, ema21) + (atr * 0.5) 
        tgt = close - (atr * 3.0)
        rationale = f"Spot faces heavy institutional rejection below VWAP (Rs.{vwap:.2f}) and 21-EMA. Momentum is bearish (RSI: {rsi:.1f})."
    else:
        trend = "⚪ NEUTRAL - CHOPPY"
        sl, tgt, rationale = close, close, "Spot is trapped in a sideways volatility box. Capital deployment here carries negative expected value."
        
    return trend, close, sl, tgt, atr, is_squeeze, rationale

# --- CHART GENERATOR ---
def generate_dual_chart(df_spot, df_opt, spot_sym, opt_sym):
    df_s_plot, df_o_plot = df_spot.tail(80), df_opt.tail(80)
    
    ap_s = [
        mpf.make_addplot(df_s_plot['VWAP_D'], color='#1f77b4', width=1.5, title="VWAP"), 
        mpf.make_addplot(df_s_plot['EMA_21'], color='#ff7f0e', width=1.5, title="21 EMA")
    ]
    mc = mpf.make_marketcolors(up='#2d5a00', down='#d93025', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)
    
    mpf.plot(df_s_plot, type='candle', style=s, addplot=ap_s, volume=True, title=f"SPOT MARKET: {spot_sym}", savefig=dict(fname='spot_chart.png', dpi=120, bbox_inches='tight'))
    mpf.plot(df_o_plot, type='candle', style=s, volume=True, title=f"DERIVATIVE PREMIUM: {opt_sym}", savefig=dict(fname='opt_chart.png', dpi=120, bbox_inches='tight'))
    return 'spot_chart.png', 'opt_chart.png'

# --- EMAIL REPORT LOGIC (QUANT TEAR SHEET) ---
def email_detailed_setup(fyers, symbol, profile, email, max_risk):
    auto_expiry = get_current_monthly_expiry()
    spot_symbol = f"NSE:{symbol}-EQ" if symbol not in ["NIFTY", "BANKNIFTY"] else f"NSE:{symbol}-INDEX"
    
    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 5)
    trend, spot_price, spot_sl, spot_tgt, spot_atr, is_squeeze, spot_rationale = analyze_spot_technicals(df_spot)
    
    if "NEUTRAL" in trend:
        st.warning(f"Market is currently Neutral for {symbol}. No email sent to protect capital.")
        return
        
    atm_strike = get_atm_strike(symbol, spot_price)
    
    if "BULLISH" in trend:
        if profile == "Option Buyer":
            opt_symbol, action = f"NSE:{symbol}{auto_expiry}{atm_strike}CE", f"BUY {atm_strike} CE"
        else:
            opt_symbol, action = f"NSE:{symbol}{auto_expiry}{int(atm_strike - (spot_atr*4))}PE", f"SELL {int(atm_strike - (spot_atr*4))} PE"
    else:
        if profile == "Option Buyer":
            opt_symbol, action = f"NSE:{symbol}{auto_expiry}{atm_strike}PE", f"BUY {atm_strike} PE"
        else:
            opt_symbol, action = f"NSE:{symbol}{auto_expiry}{int(atm_strike + (spot_atr*4))}CE", f"SELL {int(atm_strike + (spot_atr*4))} CE"

    df_opt = fetch_fyers_data(fyers, opt_symbol, "15", 5)
    if df_opt is None:
        st.error(f"Failed to fetch exact Option data for {opt_symbol}")
        return
        
    df_opt.ta.atr(length=14, append=True)
    opt_price = df_opt['close'].iloc[-1]
    opt_atr = df_opt['ATRr_14'].iloc[-1]
    
    # Dynamic Option SL, Target & Position Sizing
    if profile == "Option Buyer":
        opt_sl = opt_price - (opt_atr * 1.5)
        opt_tgt = opt_price + (opt_atr * 4.0)
    else:
        opt_sl = opt_price + (opt_atr * 2.0)
        opt_tgt = opt_price * 0.1 
        
    risk_per_unit = abs(opt_price - opt_sl)
    rr_ratio = abs(opt_tgt - opt_price) / risk_per_unit if risk_per_unit > 0 else 0
    rec_quantity = int(max_risk / risk_per_unit) if risk_per_unit > 0 else 0
    
    spot_img, opt_img = generate_dual_chart(df_spot, df_opt, symbol, opt_symbol)
    
    theme_color = "#2d5a00" if "BULLISH" in trend else "#d93025"
    squeeze_badge = '<span style="background-color: #ff9800; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">🚨 IMMINENT VOLATILITY SQUEEZE DETECTED</span>' if is_squeeze else ''
    
    html = f"""
    <html><body style="font-family: 'Helvetica Neue', Arial, sans-serif; padding: 20px; color: #222;">
        <h1 style="color: {theme_color}; border-bottom: 3px solid {theme_color}; padding-bottom: 10px; margin-bottom: 5px;">VERTEX ALGO | QUANTITATIVE TEAR SHEET</h1>
        <p style="margin-top: 0; color: #666; font-size: 14px;">Institutional Derivative Desk Report | Generated: {datetime.datetime.now().strftime('%d %b %Y, %H:%M')}</p>
        
        <h2 style="margin-bottom: 5px;">Asset: {symbol} | Trade Profile: {profile}</h2>
        {squeeze_badge}
        
        <table style="width: 100%; margin-top: 20px; border-collapse: collapse;">
            <tr>
                <td style="width: 50%; vertical-align: top; padding-right: 15px;">
                    <div style="background-color: #f8f9fa; padding: 20px; border-top: 4px solid {theme_color}; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                        <h3 style="margin-top: 0; color: #333;">EXACT EXECUTION MATRIX</h3>
                        <p style="font-size: 18px; font-weight: bold; color: {theme_color}; margin-bottom: 15px;">ACTION: {action}</p>
                        <p><strong>Premium Entry Zone:</strong> Rs. {opt_price:.2f}</p>
                        <p><strong>Primary Target:</strong> Rs. {opt_tgt:.2f}</p>
                        <p><strong>Dynamic Stoploss:</strong> Rs. {opt_sl:.2f}</p>
                        <hr style="border: 0; border-top: 1px solid #ddd; margin: 15px 0;">
                        <p><strong>Risk/Reward Ratio:</strong> 1 : {rr_ratio:.2f}</p>
                        <p><strong>Suggested Qty (Risking Rs.{max_risk}):</strong> {rec_quantity} Units</p>
                    </div>
                </td>
                <td style="width: 50%; vertical-align: top; padding-left: 15px;">
                    <div style="background-color: #fff; padding: 20px; border: 1px solid #eaeaea; border-radius: 6px;">
                        <h3 style="margin-top: 0; color: #333;">AI TECHNICAL RATIONALE</h3>
                        <p style="line-height: 1.6;"><strong>Spot Confluence:</strong> {spot_rationale} The underlying Spot ATR is {spot_atr:.2f}, indicating healthy daily ranges.</p>
                        <p style="line-height: 1.6;"><strong>Options Geometry:</strong> {profile} strategy deployed. We are targeting strike {opt_symbol}. The premium has an ATR of {opt_atr:.2f}. The mathematical stoploss is placed perfectly outside this volatility noise band to avoid premature stop-hunting by algorithms.</p>
                    </div>
                </td>
            </tr>
        </table>
        
        <h3 style="margin-top: 30px; border-bottom: 1px solid #ccc; padding-bottom: 5px;">INSTITUTIONAL DUAL-CHART ANALYSIS</h3>
        <div style="text-align: center; margin-bottom: 20px;">
            <img src="{os.path.abspath(spot_img)}" style="width: 100%; max-width: 800px; border: 1px solid #ccc; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
        </div>
        <div style="text-align: center;">
            <img src="{os.path.abspath(opt_img)}" style="width: 100%; max-width: 800px; border: 1px solid #ccc; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
        </div>
        
        <p style="font-size: 10px; color: #888; margin-top: 40px; text-align: justify;">DISCLAIMER: This tear sheet is generated for educational and analytical purposes only. Options trading involves extreme risk. Vertex Algo is not SEBI registered. The suggested quantity is a mathematical calculation, not financial advice.</p>
    </body></html>
    """
    pdf_file = f"Vertex_Alpha_{symbol}.pdf"
    with open('temp_report.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp_report.html', pdf_file, options={'enable-local-file-access': None})
    
    try:
        SENDER_EMAIL = st.secrets["EMAIL_USER"]
        APP_PASS = st.secrets["EMAIL_PASS"]
        msg = EmailMessage()
        msg['Subject'], msg['From'], msg['To'] = f'🔥 Alpha Setup: {action}', SENDER_EMAIL, email
        msg.set_content("Please find your institutional Quant Tear Sheet attached.")
        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
        st.success(f"✅ Quant Tear Sheet emailed for {symbol}!")
    except Exception as e:
        st.error(f"Email failed: {e}")

# --- UI RENDERER (Called by app.py) ---
def render_ui(fyers):
    st.markdown("### 🌐 Master 208-Stock Options Grid (Alpha Engine)")
    st.write("Scans Spot structure, detects TTM Volatility Squeezes, and auto-calculates Options targets using ATR math.")
    
    with st.container():
        st.markdown("""<div style="background-color: #1e1e1e; padding: 15px; border-radius: 8px; color: white; margin-bottom: 20px;">
        <h4 style="margin:0; color: #4caf50;">Institutional Risk Engine Dashboard</h4>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #aaa;">Configure your strict capital exposure below. The AI will size your positions accordingly.</p>
        </div>""", unsafe_allow_html=True)
        
        col1, col2, col3, col4 = st.columns([1.5, 1, 1.5, 1.5])
        with col1: trader_profile = st.radio("Strategy Bias", ["Option Buyer", "Option Seller"], horizontal=True)
        with col2: max_risk = st.number_input("Max Risk per Trade (₹)", min_value=500, value=2500, step=500)
        with col3: user_email = st.text_input("Delivery Email")
        with col4: 
            st.write("")
            scan_btn = st.button("🚀 Run Alpha 208-Grid Scan", use_container_width=True, type="primary")

    if scan_btn:
        st.markdown("---")
        progress = st.progress(0)
        status_text = st.empty()
        
        cols = st.columns(3) 
        
        total_scan = len(fno_base_universe) 
        for i, sym in enumerate(fno_base_universe):
            status_text.text(f"Scanning Volatility & Structure: {sym} ({i+1}/{total_scan})")
            
            spot_symbol = f"NSE:{sym}-EQ" if sym not in ["NIFTY", "BANKNIFTY"] else f"NSE:{sym}-INDEX"
            try:
                df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 5)
                trend, spot_price, spot_sl, spot_tgt, atr, is_squeeze, rationale = analyze_spot_technicals(df_spot)
                
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
                            <span>{trader_profile}</span>
                        </div>
                        {squeeze_html}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if "NEUTRAL" not in trend:
                        if st.button(f"⚡ Generate Tear Sheet", key=f"btn_{sym}"):
                            if user_email:
                                with st.spinner(f"Compiling Institutional Tear Sheet for {sym}..."):
                                    email_detailed_setup(fyers, sym, trader_profile, user_email, max_risk)
                            else:
                                st.error("Enter delivery email at the top.")
            except Exception as e:
                pass
                
            time.sleep(0.3) 
            progress.progress((i + 1) / total_scan)
            
        status_text.text(f"Scan Complete! All {total_scan} FNO assets evaluated.")
