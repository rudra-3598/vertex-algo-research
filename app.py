import streamlit as st
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import os
import pdfkit
import smtplib
from email.message import EmailMessage

st.set_page_config(page_title="Vertex Algo | Institutional Terminal", layout="wide")

# --- 1. SECRETS & SETUP ---
try:
    SENDER_EMAIL = st.secrets["EMAIL_USER"]
    APP_PASS = st.secrets["EMAIL_PASS"]
    DHAN_CLIENT_ID = st.secrets["DHAN_CLIENT_ID"]
    DHAN_TOKEN = st.secrets["DHAN_ACCESS_TOKEN"]
except Exception:
    st.error("⚠️ Please configure DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN and Email Secrets in Streamlit.")
    st.stop()

HEADERS = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'access-token': DHAN_TOKEN,
    'client-id': DHAN_CLIENT_ID
}

# --- 2. SMART AUTO-MAPPER (Dhan Security IDs) ---
@st.cache_data(ttl=86400) # Cache for 24 hours
def load_security_map():
    try:
        # Download Dhan Master Scrip List
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df_master = pd.read_csv(url, low_memory=False)
        # Filter only NSE Equity
        df_nse = df_master[df_master['SEM_EXM_EXCH_ID'] == 'NSE']
        # Create a dictionary mapping Custom Symbol (e.g. HDFCBANK) to Security ID (e.g. 1333)
        return dict(zip(df_nse['SEM_CUSTOM_SYMBOL'], df_nse['SEM_SMST_SECURITY_ID']))
    except Exception as e:
        st.error(f"Error loading Dhan Security Map: {e}")
        return {}

# Load your custom stock list for the search dropdown
@st.cache_data
def load_stock_universe():
    if os.path.exists("nse_stock_data.csv"):
        df = pd.read_csv("nse_stock_data.csv")
        # Extract unique tickers and remove '.NS' for matching with Dhan
        tickers = df['tic'].dropna().unique().tolist()
        return [t.replace('.NS', '') for t in tickers]
    return ["HDFCBANK", "RELIANCE", "TCS", "INFY", "TATASTEEL"] # Fallback

security_map = load_security_map()
universe = load_stock_universe()

# --- 3. DHAN API DATA FETCHING ENGINE ---
def fetch_dhan_data(security_id, mode="Intraday"):
    now = datetime.datetime.now()
    if mode == "Intraday":
        # Fetch last 5 days of 5-min data for VWAP and EMAs
        from_date = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
        to_date = now.strftime("%Y-%m-%d %H:%M:%S")
        url = "https://api.dhan.co/v2/charts/intraday"
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "5",
            "fromDate": from_date,
            "toDate": to_date
        }
    else:
        # Fetch historical daily data for Swing/Investor
        from_date = (now - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        url = "https://api.dhan.co/v2/charts/historical"
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "fromDate": from_date,
            "toDate": to_date
        }

    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code == 200:
        data = response.json()
        if "data" in data and "close" in data["data"]:
            df = pd.DataFrame(data["data"])
            # Convert epoch timestamp to datetime
            if "timestamp" in df.columns:
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
                df.set_index('datetime', inplace=True)
            return df
    return pd.DataFrame()

# --- 4. MULTI-TIER TRADING LOGIC ---
def calculate_setup(df, mode):
    latest = df.iloc[-1]
    price = latest['close']
    
    if mode == "Intraday":
        # VWAP & 5-Min Momentum
        df.ta.vwap(append=True)
        df.ta.ema(length=9, append=True)
        df.ta.rsi(length=14, append=True)
        
        vwap = df['VWAP_D'].iloc[-1]
        ema9 = df['EMA_9'].iloc[-1]
        
        if price > vwap and price > ema9:
            trend, action = "Intraday Bullish", "BUY (LONG)"
            sl = vwap - (vwap * 0.005) # 0.5% SL below VWAP
            tgt = price + ((price - sl) * 2)
        elif price < vwap and price < ema9:
            trend, action = "Intraday Bearish", "SELL (SHORT)"
            sl = vwap + (vwap * 0.005)
            tgt = price - ((sl - price) * 2)
        else:
            trend, action, sl, tgt = "Choppy near VWAP", "WAIT", price, price
            
        commentary = f"Intraday Analysis: The stock is currently trading at Rs.{price:.2f}. The Session VWAP is situated at Rs.{vwap:.2f}. With the current price action relative to the VWAP and 9-EMA, the institutional momentum is {trend}. For intraday scalping, strictly adhere to the calculated Risk:Reward levels."

    elif mode == "Swing":
        # Daily Momentum & ADX
        df.ta.ema(length=50, append=True)
        df.ta.macd(append=True)
        df.ta.atr(length=14, append=True)
        
        ema50 = df['EMA_50'].iloc[-1]
        macd_hist = df['MACDh_12_26_9'].iloc[-1]
        atr = df['ATRr_14'].iloc[-1]
        
        if price > ema50 and macd_hist > 0:
            trend, action = "Swing Bullish", "BUY (LONG)"
            sl = price - (1.5 * atr)
            tgt = price + (2 * (1.5 * atr))
        else:
            trend, action = "Swing Bearish / Neutral", "WAIT"
            sl, tgt = price, price
            
        commentary = f"Swing Analysis (Daily): Trading at Rs.{price:.2f}, the asset is evaluated against the 50-Day EMA (Rs.{ema50:.2f}). The MACD momentum indicates a {trend} environment. Positional traders should target a holding period of 3-10 days."

    else: # Investor
        # Long term 200-EMA
        df.ta.ema(length=200, append=True)
        ema200 = df['EMA_200'].iloc[-1] if 'EMA_200' in df.columns else price
        
        if price < ema200 * 1.05 and price > ema200 * 0.95:
            trend, action = "Accumulation Zone", "SIP / BUY"
        elif price > ema200:
            trend, action = "Overextended", "HOLD"
        else:
            trend, action = "Deep Value", "ACCUMULATE"
            
        sl = price * 0.85 # 15% investment SL
        tgt = price * 1.30 # 30% investment Target
        commentary = f"Long-Term Investor View: Institutional accumulation is analyzed near the 200-Day EMA (Rs.{ema200:.2f}). The current structure classifies the asset in an '{trend}'. Investors should focus on corporate fundamentals and allocate capital strategically."

    return trend, action, price, sl, tgt, commentary

# --- 5. REPORT GENERATOR ---
def generate_pdf(ticker, mode, trend, action, price, sl, tgt, commentary):
    html = f"""
    <html><head><style>
        body {{ font-family: Arial, sans-serif; color: #1a1a1a; margin: 30px; }}
        h1 {{ color: #2d5a00; border-bottom: 2px solid #bdf271; padding-bottom: 10px; }}
        .box {{ background-color: #f4fdf0; padding: 20px; border-left: 5px solid #2d5a00; margin-top: 20px; }}
    </style></head>
    <body>
        <h1>VERTEX ALGO | {mode.upper()} RESEARCH</h1>
        <h2>Ticker: {ticker} | CMP: Rs. {price:.2f}</h2>
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
    filename = f"{ticker}_{mode}_Report.pdf"
    with open('temp.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp.html', filename, options={'enable-local-file-access': None})
    return filename

# --- UI FRONTEND ---
if os.path.exists('Black_logo.png'):
    st.image('Black_logo.png', width=200)

st.title("Institutional Trading Terminal")

col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("### Search & Analyze")
    # Autocomplete Search Box using your CSV universe
    selected_ticker = st.selectbox("Search Stock (NSE)", universe)
    user_email = st.text_input("Delivery Email Address")

with col2:
    st.markdown("### Trading Style")
    mode = st.radio("Select Profile", ["Intraday", "Swing", "Investor"])

if st.button("Analyze & Generate Report"):
    if selected_ticker and user_email:
        sec_id = security_map.get(selected_ticker)
        
        if not sec_id:
            st.error(f"❌ Security ID for {selected_ticker} not found in Dhan Master list.")
        else:
            with st.spinner(f"Fetching Lightning-Fast Dhan API Data for {selected_ticker} ({mode})..."):
                df = fetch_dhan_data(sec_id, mode)
                
                if df.empty or len(df) < 50:
                    st.error("❌ Not enough data fetched from Dhan API. Check market hours or API limits.")
                else:
                    trend, action, price, sl, tgt, commentary = calculate_setup(df, mode)
                    pdf_file = generate_pdf(selected_ticker, mode, trend, action, price, sl, tgt, commentary)
                    
                    # Email Logic
                    try:
                        msg = EmailMessage()
                        msg['Subject'] = f'Vertex Algo: {mode} Report - {selected_ticker}'
                        msg['From'] = SENDER_EMAIL
                        msg['To'] = user_email
                        msg.set_content(f"Hello,\n\nYour {mode} quantitative analysis for {selected_ticker} is attached.\n\nTeam Vertex Algo")
                        with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
                        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                            smtp.login(SENDER_EMAIL, APP_PASS)
                            smtp.send_message(msg)
                        
                        st.success(f"✅ {mode} Setup: **{action}**! PDF sent to {user_email}")
                        with open(pdf_file, "rb") as pdf:
                            st.download_button("📥 Download Report", data=pdf, file_name=pdf_file, mime="application/pdf")
                    except Exception as e:
                        st.error(f"Email failed: {e}")
    else:
        st.warning("Please enter Email.")
