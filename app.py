import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import time
import os
import pdfkit
import smtplib
from email.message import EmailMessage
from fyers_apiv3 import fyersModel

st.set_page_config(page_title="Vertex Algo | Institutional Terminal", layout="wide")

# --- 1. SECRETS & SETUP ---
try:
    SENDER_EMAIL = st.secrets["EMAIL_USER"]
    APP_PASS = st.secrets["EMAIL_PASS"]
    FYERS_CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    FYERS_SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    FYERS_REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
except Exception:
    st.error("⚠️ Please configure Fyers and Email Secrets in Streamlit.")
    st.stop()

# --- 2. FYERS DAILY AUTHENTICATION (SESSION STATE) ---
if 'fyers_access_token' not in st.session_state:
    st.session_state['fyers_access_token'] = None

def get_fyers_instance():
    if st.session_state['fyers_access_token']:
        return fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=st.session_state['fyers_access_token'], log_path="")
    return None

# --- 3. CSV TO FYERS SYMBOL MAPPER ---
@st.cache_data
def load_fyers_universe():
    if os.path.exists("nse_stock_data.csv"):
        df = pd.read_csv("nse_stock_data.csv")
        # Convert TATASTEEL.NS -> NSE:TATASTEEL-EQ
        tickers = df['tic'].dropna().unique().tolist()
        formatted_tickers = [f"NSE:{t.replace('.NS', '')}-EQ" for t in tickers]
        return formatted_tickers
    return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ", "NSE:TCS-EQ"] # Fallback

universe = load_fyers_universe()

# --- 4. DATA FETCHING (RATE-LIMIT PROTECTED) ---
def fetch_fyers_historical(fyers, symbol, mode="Intraday"):
    now = datetime.datetime.now()
    if mode == "Intraday":
        # 5 Days of 5-min data (Limit is 100 days per call, so 5 is well within limits)
        range_from = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        range_to = now.strftime("%Y-%m-%d")
        res_code = "5"
    else:
        # 365 Days of Daily data for Swing/Investor
        range_from = (now - datetime.timedelta(days=364)).strftime("%Y-%m-%d")
        range_to = now.strftime("%Y-%m-%d")
        res_code = "1D"

    data = {
        "symbol": symbol,
        "resolution": res_code,
        "date_format": "1",
        "range_from": range_from,
        "range_to": range_to,
        "cont_flag": "1"
    }
    
    response = fyers.history(data=data)
    if response.get("s") == "ok":
        df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
        # Convert UTC to IST
        df['datetime'] = df['datetime'] + pd.Timedelta(hours=5, minutes=30)
        df.set_index('datetime', inplace=True)
        return df
    return pd.DataFrame()

# --- 5. THE 3-TIER QUANT ENGINE ---
def calculate_setup(df, mode):
    latest = df.iloc[-1]
    price = latest['close']
    
    if mode == "Intraday":
        df.ta.vwap(append=True)
        df.ta.ema(length=9, append=True)
        vwap = df['VWAP_D'].iloc[-1]
        ema9 = df['EMA_9'].iloc[-1]
        
        if price > vwap and price > ema9:
            trend, action, sl, tgt = "Intraday Bullish", "BUY (LONG)", vwap - (vwap*0.005), price + ((price - (vwap - (vwap*0.005))) * 2)
        elif price < vwap and price < ema9:
            trend, action, sl, tgt = "Intraday Bearish", "SELL (SHORT)", vwap + (vwap*0.005), price - (((vwap + (vwap*0.005)) - price) * 2)
        else:
            trend, action, sl, tgt = "Choppy / Neutral", "WAIT", price, price
            
        commentary = f"Intraday Analysis: Current price is Rs.{price:.2f}. Session VWAP stands at Rs.{vwap:.2f}. The momentum evaluates to {trend} relative to institutional average prices."

    elif mode == "Swing":
        df.ta.ema(length=50, append=True)
        df.ta.macd(append=True)
        df.ta.atr(length=14, append=True)
        ema50 = df['EMA_50'].iloc[-1]
        macd_hist = df['MACDh_12_26_9'].iloc[-1]
        atr = df['ATRr_14'].iloc[-1]
        
        if price > ema50 and macd_hist > 0:
            trend, action, sl, tgt = "Swing Bullish", "BUY (LONG)", price - (1.5 * atr), price + (3 * atr)
        elif price < ema50 and macd_hist < 0:
            trend, action, sl, tgt = "Swing Bearish", "SELL (SHORT)", price + (1.5 * atr), price - (3 * atr)
        else:
            trend, action, sl, tgt = "Consolidation", "WAIT", price, price
            
        commentary = f"Swing Analysis: Operating against a 50-EMA of Rs.{ema50:.2f}. MACD momentum indicates {trend}. Ideal holding period 3-10 days."

    else: # Investor
        df.ta.ema(length=200, append=True)
        ema200 = df['EMA_200'].iloc[-1] if 'EMA_200' in df.columns else price
        
        if price < ema200 * 1.05 and price > ema200 * 0.95:
            trend, action = "Accumulation Zone", "SIP / BUY"
        elif price > ema200:
            trend, action = "Overextended", "HOLD"
        else:
            trend, action = "Deep Value", "ACCUMULATE"
        sl, tgt = price * 0.85, price * 1.30
        commentary = f"Investor View: Asset structure relative to 200-EMA (Rs.{ema200:.2f}) indicates '{trend}'. Focus on long-term value accumulation."

    return trend, action, price, sl, tgt, commentary

# --- 6. PDF GENERATOR ---
def generate_pdf(ticker, mode, trend, action, price, sl, tgt, commentary, is_screener=False):
    html = f"""
    <html><head><style>
        body {{ font-family: Arial, sans-serif; color: #1a1a1a; margin: 30px; line-height: 1.6; }}
        h1 {{ color: #2d5a00; border-bottom: 2px solid #bdf271; padding-bottom: 10px; }}
        .box {{ background-color: #f4fdf0; padding: 20px; border-left: 5px solid #2d5a00; margin-top: 20px; }}
    </style></head>
    <body>
        <h1>VERTEX ALGO | {'SCREENER' if is_screener else mode.upper()} REPORT</h1>
        <h2>Asset: {ticker} | CMP: Rs. {price:.2f}</h2>
        <div class="box">
            <h3>Trade Action: {action}</h3>
            <p><strong>Status:</strong> {trend}</p>
            <p><strong>Entry:</strong> Near Rs. {price:.2f}</p>
            <p><strong>Target:</strong> Rs. {tgt:.2f}</p>
            <p><strong>Strict Stoploss:</strong> Rs. {sl:.2f}</p>
        </div>
        <h3>Quantitative Commentary</h3>
        <p>{commentary}</p>
    </body></html>
    """
    filename = f"{ticker.replace(':', '_')}_Vertex_Report.pdf"
    with open('temp.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp.html', filename, options={'enable-local-file-access': None})
    return filename

# --- UI FRONTEND ---
st.sidebar.image('Black_logo.png', width=150) if os.path.exists('Black_logo.png') else st.sidebar.title("VERTEX ALGO")

# 1. Master Admin Panel (For Daily Auth)
with st.sidebar.expander("🔐 Fyers Admin Auth (Daily)"):
    session = fyersModel.SessionModel(client_id=FYERS_CLIENT_ID, secret_key=FYERS_SECRET_KEY, redirect_uri=FYERS_REDIRECT_URI, response_type="code", grant_type="authorization_code")
    st.markdown(f"[🔗 Generate Auth Code Here]({session.generate_authcode()})")
    
    auth_code = st.text_input("Paste Auth Code Here", type="password")
    if st.button("Unlock Terminal"):
        try:
            session.set_token(auth_code)
            response = session.generate_token()
            if "access_token" in response:
                st.session_state['fyers_access_token'] = response["access_token"]
                st.success("✅ Terminal Unlocked for 24 Hours!")
            else:
                st.error("Failed to generate token. Check auth code.")
        except Exception as e:
            st.error(f"Auth Error: {e}")

if not st.session_state['fyers_access_token']:
    st.warning("🔒 Terminal Locked. Admin needs to authenticate via the sidebar.")
    st.stop()

fyers = get_fyers_instance()

# 2. Main Terminal Tabs
st.title("Institutional Trading Terminal")
tab1, tab2 = st.tabs(["Single Asset Analyzer", "Live Auto-Screener"])

with tab1:
    st.markdown("### Deep Dive Analyzer")
    col1, col2 = st.columns([2, 1])
    with col1:
        selected_ticker = st.selectbox("Search Stock (e.g. NSE:HDFCBANK-EQ)", universe)
        user_email = st.text_input("Delivery Email Address")
    with col2:
        mode = st.radio("Select Profile", ["Intraday", "Swing", "Investor"])

    if st.button("Generate & Email Report"):
        if selected_ticker and user_email:
            with st.spinner("Fetching Fyers Institutional Data..."):
                df = fetch_fyers_historical(fyers, selected_ticker, mode)
                if not df.empty:
                    trend, action, price, sl, tgt, commentary = calculate_setup(df, mode)
                    pdf_file = generate_pdf(selected_ticker, mode, trend, action, price, sl, tgt, commentary)
                    
                    try:
                        msg = EmailMessage()
                        msg['Subject'], msg['From'], msg['To'] = f'Vertex Algo: {mode} Report', SENDER_EMAIL, user_email
                        msg.set_content("Find your detailed institutional setup attached.")
                        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
                        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                            smtp.login(SENDER_EMAIL, APP_PASS)
                            smtp.send_message(msg)
                        st.success(f"✅ {mode} Setup: **{action}**! PDF emailed.")
                        with open(pdf_file, "rb") as pdf:
                            st.download_button("📥 Download Report", data=pdf, file_name=pdf_file, mime="application/pdf")
                    except Exception as e:
                        st.error(f"Email failed: {e}")
                else:
                    st.error("Data fetch failed. Check symbol or market hours.")

with tab2:
    st.markdown("### Ready Trade Setups (Live Scalp Screener)")
    st.write("Scans the Top 50 stocks from your universe for immediate Intraday setups based on 5-Min VWAP & Momentum.")
    screener_email = st.text_input("Email to send Master Screener Report")
    
    if st.button("Scan Market Now"):
        if screener_email:
            scan_universe = universe[:50] # Taking first 50 to respect rate limits quickly
            triggered_stocks = []
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, sym in enumerate(scan_universe):
                status_text.text(f"Scanning {sym}... ({i+1}/50)")
                df = fetch_fyers_historical(fyers, sym, "Intraday")
                
                if not df.empty and len(df) > 10:
                    trend, action, price, sl, tgt, commentary = calculate_setup(df, "Intraday")
                    # If it's a confirmed BUY or SELL (not WAIT)
                    if action in ["BUY (LONG)", "SELL (SHORT)"]:
                        triggered_stocks.append({"Symbol": sym, "Action": action, "Entry": price, "Target": tgt, "SL": sl})
                
                time.sleep(0.3) # RATE LIMIT PROTECTOR: 3 calls per second max
                progress_bar.progress((i + 1) / 50)
                
            status_text.text("Scan Complete!")
            
            if triggered_stocks:
                st.success(f"🔥 Found {len(triggered_stocks)} Live Setups!")
                st.dataframe(pd.DataFrame(triggered_stocks))
                
                # Combine into one Master HTML/PDF
                master_html = "<html><body><h1>VERTEX ALGO | MASTER SCREENER</h1>"
                for trade in triggered_stocks:
                    master_html += f"<h3>{trade['Symbol']} -> {trade['Action']}</h3><p>Entry: {trade['Entry']:.2f} | Tgt: {trade['Target']:.2f} | SL: {trade['SL']:.2f}</p><hr>"
                master_html += "</body></html>"
                
                with open('screener.html', 'w') as f: f.write(master_html)
                pdfkit.from_file('screener.html', "Master_Screener.pdf", options={'enable-local-file-access': None})
                
                # Email the Screener
                msg = EmailMessage()
                msg['Subject'], msg['From'], msg['To'] = '🔥 Vertex Algo: Live Master Screener', SENDER_EMAIL, screener_email
                msg.set_content("Live trade setups detected in the last 15 minutes. Report attached.")
                with open("Master_Screener.pdf", 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename="Master_Screener.pdf")
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(SENDER_EMAIL, APP_PASS)
                    smtp.send_message(msg)
                
                with open("Master_Screener.pdf", "rb") as pdf:
                    st.download_button("📥 Download Master Report", data=pdf, file_name="Master_Screener.pdf", mime="application/pdf")
            else:
                st.info("No immediate trade setups found right now. The market might be choppy. Wait for the next 15-min candle.")
        else:
            st.warning("Please enter an email address.")
