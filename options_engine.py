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

# --- INSTITUTIONAL TECHNICAL ANALYSIS (SPOT) ---
def analyze_spot_technicals(df):
    if df is None or len(df) < 30: return "Neutral", 0, 0, 0, 0, "Insufficient Data"
    
    df.ta.vwap(append=True)
    df.ta.ema(length=9, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.atr(length=14, append=True)
    
    close = df['close'].iloc[-1]
    vwap = df['VWAP_D'].iloc[-1]
    ema9 = df['EMA_9'].iloc[-1]
    ema50 = df['EMA_50'].iloc[-1]
    atr = df['ATRr_14'].iloc[-1]
    
    # Dynamic SL and Target using Volatility (ATR)
    if close > vwap and ema9 > ema50:
        trend = "🟢 BULLISH SETUP"
        sl = max(vwap, ema50) - (atr * 0.5) # Support based SL
        tgt = close + (atr * 2.5) # Reward based on volatility
        rationale = f"Spot is trading confidently above VWAP (Rs.{vwap:.2f}) and 50-EMA. Market structure is making higher highs."
    elif close < vwap and ema9 < ema50:
        trend = "🔴 BEARISH SETUP"
        sl = min(vwap, ema50) + (atr * 0.5) # Resistance based SL
        tgt = close - (atr * 2.5)
        rationale = f"Spot faces heavy institutional rejection below VWAP (Rs.{vwap:.2f}) and 50-EMA. Structure is completely bearish."
    else:
        trend = "⚪ NEUTRAL - WAIT"
        sl, tgt, rationale = close, close, "Spot is trapped in a sideways range around the VWAP. Deploying capital here risks Theta decay."
        
    return trend, close, sl, tgt, atr, rationale

# --- CHART GENERATOR ---
def generate_dual_chart(df_spot, df_opt, spot_sym, opt_sym):
    df_s_plot, df_o_plot = df_spot.tail(80), df_opt.tail(80)
    
    ap_s = [mpf.make_addplot(df_s_plot['VWAP_D'], color='blue', width=1.5), mpf.make_addplot(df_s_plot['EMA_50'], color='green', width=1.5)]
    mc = mpf.make_marketcolors(up='#2d5a00', down='#d93025', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)
    
    mpf.plot(df_s_plot, type='candle', style=s, addplot=ap_s, volume=True, title=f"SPOT: {spot_sym}", savefig=dict(fname='spot_chart.png', dpi=100))
    mpf.plot(df_o_plot, type='candle', style=s, volume=True, title=f"OPTION: {opt_sym}", savefig=dict(fname='opt_chart.png', dpi=100))
    return 'spot_chart.png', 'opt_chart.png'

# --- EMAIL REPORT LOGIC ---
def email_detailed_setup(fyers, symbol, profile, email):
    auto_expiry = get_current_monthly_expiry()
    spot_symbol = f"NSE:{symbol}-EQ" if symbol not in ["NIFTY", "BANKNIFTY"] else f"NSE:{symbol}-INDEX"
    
    df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 4)
    trend, spot_price, spot_sl, spot_tgt, atr, spot_rationale = analyze_spot_technicals(df_spot)
    
    if "NEUTRAL" in trend:
        st.warning(f"Market is currently Neutral for {symbol}. No email sent to protect capital.")
        return
        
    atm_strike = get_atm_strike(symbol, spot_price)
    
    # Strategy Logic based on User Profile
    if "BULLISH" in trend:
        if profile == "Option Buyer":
            opt_symbol = f"NSE:{symbol}{auto_expiry}{atm_strike}CE"
            action = f"BUY {atm_strike} CE"
        else:
            opt_symbol = f"NSE:{symbol}{auto_expiry}{int(atm_strike - (atr*3))}PE" # OTM Put
            action = f"SELL {int(atm_strike - (atr*3))} PE"
    else:
        if profile == "Option Buyer":
            opt_symbol = f"NSE:{symbol}{auto_expiry}{atm_strike}PE"
            action = f"BUY {atm_strike} PE"
        else:
            opt_symbol = f"NSE:{symbol}{auto_expiry}{int(atm_strike + (atr*3))}CE" # OTM Call
            action = f"SELL {int(atm_strike + (atr*3))} CE"

    df_opt = fetch_fyers_data(fyers, opt_symbol, "15", 4)
    if df_opt is None:
        st.error(f"Failed to fetch exact Option data for {opt_symbol}")
        return
        
    # Option specific TA
    df_opt.ta.atr(length=14, append=True)
    opt_price = df_opt['close'].iloc[-1]
    opt_atr = df_opt['ATRr_14'].iloc[-1]
    
    if profile == "Option Buyer":
        opt_sl = opt_price - (opt_atr * 1.5)
        opt_tgt = opt_price + (opt_atr * 3)
    else:
        opt_sl = opt_price + (opt_atr * 1.5)
        opt_tgt = opt_price * 0.1 # Max profit for seller is when premium dies
        
    spot_img, opt_img = generate_dual_chart(df_spot, df_opt, symbol, opt_symbol)
    
    # Generate Elite PDF
    html = f"""
    <html><body style="font-family: Arial; padding: 20px; color: #333;">
        <h1 style="color: #2d5a00; border-bottom: 2px solid #ccc;">VERTEX ALGO | A-Z OPTIONS RESEARCH</h1>
        <h2>Asset: {symbol} | Profile: {profile}</h2>
        <div style="background-color: #f9f9f9; padding: 20px; border-left: 5px solid #2d5a00; margin-bottom: 20px;">
            <h3>TRADE EXECUTION: {action}</h3>
            <p><strong>Option Premium Entry:</strong> Rs. {opt_price:.2f}</p>
            <p><strong>Option Target (Vol-Adjusted):</strong> Rs. {opt_tgt:.2f}</p>
            <p><strong>Option Stoploss (Dynamic ATR):</strong> Rs. {opt_sl:.2f}</p>
        </div>
        <h3>AI Detailed Summary (Why this trade?)</h3>
        <p><strong>Spot Analysis:</strong> {spot_rationale} Spot Support/Resistance mapped at SL {spot_sl:.2f} and TGT {spot_tgt:.2f}.</p>
        <p><strong>Derivative Strategy:</strong> As an {profile}, this strike ({opt_symbol}) was chosen dynamically. ATR indicates a healthy volatility of {opt_atr:.2f} in the premium, validating the strict technical Stoploss placed.</p>
        
        <h3>Dual Institutional Charts</h3>
        <img src="{os.path.abspath(spot_img)}" style="width:100%; margin-bottom: 10px; border:1px solid #ccc;">
        <img src="{os.path.abspath(opt_img)}" style="width:100%; border:1px solid #ccc;">
        <p style="font-size: 10px; color: #777; margin-top: 30px;">Disclaimer: Educational purpose only.</p>
    </body></html>
    """
    pdf_file = f"{symbol}_Pro_Setup.pdf"
    with open('temp_report.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp_report.html', pdf_file, options={'enable-local-file-access': None})
    
    try:
        SENDER_EMAIL = st.secrets["EMAIL_USER"]
        APP_PASS = st.secrets["EMAIL_PASS"]
        msg = EmailMessage()
        msg['Subject'], msg['From'], msg['To'] = f'Vertex Setup: {action}', SENDER_EMAIL, email
        msg.set_content("Please find your institutional dual-chart setup attached.")
        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
        st.success(f"✅ Deep AI Setup Emailed successfully for {symbol}!")
    except Exception as e:
        st.error(f"Email failed: {e}")

# --- UI RENDERER (Called by app.py) ---
def render_ui(fyers):
    st.markdown("### 🌐 Master 208-Stock Options Grid (Live)")
    st.write("Scans technical spot structure and auto-calculates Options dynamic targets using Volatility (ATR).")
    
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1: trader_profile = st.radio("Style", ["Option Buyer", "Option Seller"])
    with col2: user_email = st.text_input("Delivery Email")
    with col3: 
        st.info("Updates Live FNO data automatically.")
        scan_btn = st.button("🚀 Run Institutional 208 FNO Scan Now", use_container_width=True)

    if scan_btn:
        st.markdown("---")
        progress = st.progress(0)
        status_text = st.empty()
        
        # Grid layout for Individual Boxes
        cols = st.columns(3) 
        
        # Limit to 30 for UI safety in this code, change to len(fno_base_universe) for all 208
        total_scan = 30 
        for i, sym in enumerate(fno_base_universe[:total_scan]):
            status_text.text(f"Scanning Volatility & Structure: {sym} ({i+1}/{total_scan})")
            
            spot_symbol = f"NSE:{sym}-EQ" if sym not in ["NIFTY", "BANKNIFTY"] else f"NSE:{sym}-INDEX"
            try:
                df_spot = fetch_fyers_data(fyers, spot_symbol, "15", 3)
                trend, spot_price, spot_sl, spot_tgt, atr, rationale = analyze_spot_technicals(df_spot)
                
                # Render Individual Box in Grid
                col = cols[i % 3]
                with col:
                    bg_color = "#e8f5e9" if "BULLISH" in trend else "#ffebee" if "BEARISH" in trend else "#f5f5f5"
                    border = "#4caf50" if "BULLISH" in trend else "#f44336" if "BEARISH" in trend else "#9e9e9e"
                    
                    st.markdown(f"""
                    <div style="background-color: {bg_color}; border-left: 5px solid {border}; padding: 15px; border-radius: 5px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <h4 style="margin: 0; color: #333;">{sym}</h4>
                        <p style="margin: 5px 0; font-size: 14px; font-weight: bold; color: {border};">{trend}</p>
                        <p style="margin: 0; font-size: 12px; color: #666;">Spot CMP: Rs. {spot_price:.2f}</p>
                        <p style="margin: 0; font-size: 11px; color: #888;">ATR (Vol): {atr:.2f}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Individual Email Button inside the loop
                    if "NEUTRAL" not in trend:
                        if st.button(f"📧 Email {sym} Setup", key=f"btn_{sym}"):
                            if user_email:
                                with st.spinner(f"Generating Dual-Chart Report for {sym}..."):
                                    email_detailed_setup(fyers, sym, trader_profile, user_email)
                            else:
                                st.error("Please enter email at the top.")
            except Exception as e:
                pass
                
            time.sleep(0.3) # Protect API limits
            progress.progress((i + 1) / total_scan)
            
        status_text.text("Scan Complete! Review the setups below.")
