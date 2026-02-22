import streamlit as st
import pandas as pd
import pandas_ta as ta
import mplfinance as mpf
import datetime
import time
import os
import pdfkit
import smtplib
from email.message import EmailMessage
from fyers_apiv3 import fyersModel

st.set_page_config(page_title="Vertex Algo | Pro Terminal", layout="wide")

# --- SECRETS & SETUP ---
try:
    SENDER_EMAIL = st.secrets["EMAIL_USER"]
    APP_PASS = st.secrets["EMAIL_PASS"]
    FYERS_CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    FYERS_SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    FYERS_REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
except Exception:
    st.error("⚠️ Please configure Fyers and Email Secrets in Streamlit.")
    st.stop()

# --- GLOBAL AUTHENTICATION (NO MORE REPEATED LOGINS) ---
TOKEN_FILE = "fyers_token.txt"

def load_saved_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return f.read().strip()
    return None

def save_token_to_file(token):
    with open(TOKEN_FILE, "w") as f:
        f.write(token)

if 'fyers_access_token' not in st.session_state:
    st.session_state['fyers_access_token'] = load_saved_token()

def get_fyers_instance():
    if st.session_state['fyers_access_token']:
        return fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=st.session_state['fyers_access_token'], log_path="")
    return None

# --- DATABASES ---
@st.cache_data
def load_all_stocks():
    if os.path.exists("nse_stock_data.csv"):
        try:
            df = pd.read_csv("nse_stock_data.csv")
            return [f"NSE:{t.replace('.NS', '')}-EQ" for t in df['tic'].dropna().unique()]
        except Exception as e: return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ"]
    return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ", "NSE:TCS-EQ"]

@st.cache_data
def load_fno_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [f"NSE:{t}-EQ" for t in df['SYMBOL'].dropna().unique()]
        except Exception as e: return ["NSE:NIFTY-EQ"]
    return load_all_stocks()[:50]

all_universe = load_all_stocks()
fno_universe = load_fno_stocks()

# --- FYERS MULTI-TIMEFRAME FETCHING ---
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

# --- CHART GENERATOR ---
def generate_chart(df, title, filename, is_intraday=False):
    if df is None or df.empty or len(df) < 20: return None
    df_plot = df.tail(100)
    
    ap = []
    if is_intraday and 'VWAP_D' in df_plot.columns: ap.append(mpf.make_addplot(df_plot['VWAP_D'], color='#1f77b4', width=1.5))
    if 'EMA_9' in df_plot.columns: ap.append(mpf.make_addplot(df_plot['EMA_9'], color='#ff7f0e', width=1.2))
    if 'EMA_50' in df_plot.columns: ap.append(mpf.make_addplot(df_plot['EMA_50'], color='#2ca02c', width=1.5))

    mc = mpf.make_marketcolors(up='#2d5a00', down='#d93025', edge='inherit', wick='inherit', volume='in')
    s  = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=False)
    
    mpf.plot(df_plot, type='candle', style=s, addplot=ap, volume=True, 
             title=f"\n{title}", savefig=dict(fname=filename, dpi=150, bbox_inches='tight'), figratio=(12, 4))
    return filename

# --- PRO LOGIC ENGINE WITH DETAILED TIMEFRAME BREAKDOWNS ---
def analyze_intraday(fyers, symbol):
    df_5m = fetch_fyers_data(fyers, symbol, "5", 4)
    df_15m = fetch_fyers_data(fyers, symbol, "15", 8)
    if df_5m is None or df_15m is None: return None
    
    df_5m.ta.vwap(append=True); df_5m.ta.ema(length=9, append=True); df_5m.ta.rsi(length=14, append=True); df_5m.ta.macd(append=True)
    df_15m.ta.vwap(append=True); df_15m.ta.ema(length=9, append=True); df_15m.ta.rsi(length=14, append=True); df_15m.ta.macd(append=True)
    
    price = df_5m['close'].iloc[-1]
    
    # 5m Detailed Text
    rsi_5m, macd_5m, vol_5m, vwap_5m, ema9_5m = df_5m['RSI_14'].iloc[-1], df_5m['MACDh_12_26_9'].iloc[-1], df_5m['volume'].iloc[-1], df_5m['VWAP_D'].iloc[-1], df_5m['EMA_9'].iloc[-1]
    text_5m = f"Detailed 5-Min Analysis: Current RSI is {rsi_5m:.2f}. The MACD histogram stands at {macd_5m:.2f}. Volume recorded in the latest candle is {vol_5m:,.0f}. Price is trading {'above' if price > vwap_5m else 'below'} the Session VWAP (Rs. {vwap_5m:.2f}) and {'above' if price > ema9_5m else 'below'} the 9-EMA."
    
    # 15m Detailed Text
    rsi_15m, macd_15m, vol_15m = df_15m['RSI_14'].iloc[-1], df_15m['MACDh_12_26_9'].iloc[-1], df_15m['volume'].iloc[-1]
    text_15m = f"Detailed 15-Min Analysis: The structural 15-Min RSI evaluates to {rsi_15m:.2f}. MACD reads {macd_15m:.2f} with a total period volume of {vol_15m:,.0f}. This timeframe provides the institutional bias for our shorter-term entries."
    
    chart_5m = generate_chart(df_5m, f"{symbol} - 5 Min Chart", "chart_5m.png", True)
    chart_15m = generate_chart(df_15m, f"{symbol} - 15 Min Chart", "chart_15m.png", True)
    
    if price > vwap_5m and ema9_5m > vwap_5m and rsi_15m > 55:
        trend, action, color = "Bullish Momentum", "BUY (LONG)", "#2d5a00"
        sl, tgt = vwap_5m * 0.995, price + ((price - (vwap_5m * 0.995)) * 2)
    elif price < vwap_5m and ema9_5m < vwap_5m and rsi_15m < 45:
        trend, action, color = "Bearish Breakdown", "SELL (SHORT)", "#d93025"
        sl, tgt = vwap_5m * 1.005, price - (((vwap_5m * 1.005) - price) * 2)
    else:
        trend, action, color, sl, tgt = "Neutral Market", "NO TRADE", "#666666", price, price

    return {"price": price, "trend": trend, "action": action, "sl": sl, "tgt": tgt, "color": color, "charts": [{"path": chart_5m, "text": text_5m, "title": "5-Minute Scalp Chart"}, {"path": chart_15m, "text": text_15m, "title": "15-Minute Structural Chart"}]}

def analyze_swing(fyers, symbol):
    df_1h = fetch_fyers_data(fyers, symbol, "60", 30); df_4h = fetch_fyers_data(fyers, symbol, "240", 90); df_1d = fetch_fyers_data(fyers, symbol, "1D", 365)
    if df_1h is None or df_4h is None or df_1d is None: return None
    
    for df in [df_1h, df_4h, df_1d]: df.ta.ema(length=20, append=True); df.ta.ema(length=50, append=True); df.ta.rsi(length=14, append=True); df.ta.macd(append=True)
    
    price = df_1d['close'].iloc[-1]
    
    text_1h = f"1-Hour Analysis: RSI is {df_1h['RSI_14'].iloc[-1]:.2f}. MACD histogram is {df_1h['MACDh_12_26_9'].iloc[-1]:.2f}. Volume is {df_1h['volume'].iloc[-1]:,.0f}. This provides the exact timing for our swing execution."
    text_4h = f"4-Hour Analysis: Mid-term RSI validates at {df_4h['RSI_14'].iloc[-1]:.2f}. MACD is {df_4h['MACDh_12_26_9'].iloc[-1]:.2f}. A critical timeframe to filter out intraday noise."
    text_1d = f"Daily Analysis: The primary trend decider. RSI is {df_1d['RSI_14'].iloc[-1]:.2f}. Daily MACD is {df_1d['MACDh_12_26_9'].iloc[-1]:.2f} and volume stands at {df_1d['volume'].iloc[-1]:,.0f}. Price is evaluated against the 50-EMA (Rs. {df_1d['EMA_50'].iloc[-1]:.2f})."
    
    chart_1h = generate_chart(df_1h, f"{symbol} - 1 Hour", "chart_1h.png")
    chart_4h = generate_chart(df_4h, f"{symbol} - 4 Hour", "chart_4h.png")
    chart_1d = generate_chart(df_1d, f"{symbol} - Daily", "chart_1d.png")
    
    if price > df_1d['EMA_50'].iloc[-1] and df_1d['MACDh_12_26_9'].iloc[-1] > 0 and df_4h['RSI_14'].iloc[-1] > 55:
        trend, action, color = "Swing Bullish", "BUY (LONG)", "#2d5a00"
        sl, tgt = df_1d['EMA_20'].iloc[-1], price + ((price - df_1d['EMA_20'].iloc[-1]) * 2.5)
    elif price < df_1d['EMA_50'].iloc[-1] and df_1d['MACDh_12_26_9'].iloc[-1] < 0 and df_4h['RSI_14'].iloc[-1] < 45:
        trend, action, color = "Swing Bearish", "SELL (SHORT)", "#d93025"
        sl, tgt = df_1d['EMA_20'].iloc[-1], price - ((df_1d['EMA_20'].iloc[-1] - price) * 2.5)
    else:
        trend, action, color, sl, tgt = "Sideways / Choppy", "NO TRADE", "#666666", price, price

    return {"price": price, "trend": trend, "action": action, "sl": sl, "tgt": tgt, "color": color, "charts": [{"path": chart_1d, "text": text_1d, "title": "Daily Trend Chart"}, {"path": chart_4h, "text": text_4h, "title": "4-Hour Chart"}, {"path": chart_1h, "text": text_1h, "title": "1-Hour Entry Chart"}]}

def analyze_investor(fyers, symbol):
    df_1d = fetch_fyers_data(fyers, symbol, "1D", 700)
    if df_1d is None: return None
    df_1d.ta.ema(length=200, append=True); df_1d.ta.rsi(length=14, append=True); df_1d.ta.macd(append=True)
    
    price, ema200 = df_1d['close'].iloc[-1], df_1d['EMA_200'].iloc[-1]
    
    text_1d = f"Macro Daily Analysis: RSI is currently at {df_1d['RSI_14'].iloc[-1]:.2f}. MACD stands at {df_1d['MACDh_12_26_9'].iloc[-1]:.2f} with a heavy volume profile of {df_1d['volume'].iloc[-1]:,.0f} shares. Long term institutional average (200-EMA) is at Rs. {ema200:.2f}."
    chart_1d = generate_chart(df_1d, f"{symbol} - Macro Daily (200 EMA)", "chart_1d_inv.png")
    
    if price < ema200 * 1.05 and price > ema200 * 0.95:
        trend, action, color = "Accumulation Zone", "SIP / BUY", "#2d5a00"
    elif price < ema200 * 0.85:
        trend, action, color = "Deep Value / Oversold", "ACCUMULATE", "#2d5a00"
    else:
        trend, action, color = "Overextended / Neutral", "NO TRADE", "#666666"
        
    return {"price": price, "trend": trend, "action": action, "sl": price*0.80, "tgt": price*1.50, "color": color, "charts": [{"path": chart_1d, "text": text_1d, "title": "Macro Daily Investment Chart"}]}

# --- PROFESSIONAL PDF GENERATOR ---
def generate_pro_pdf(ticker, mode, data):
    logo_path = os.path.abspath('Black_logo.png')
    logo_html = f'<img src="{logo_path}" style="height: 40px;">' if os.path.exists('Black_logo.png') else '<h2>VERTEX ALGO</h2>'
    
    # ☕ THE NEUTRAL HANDLING PROTOCOL
    if data['action'] == "NO TRADE":
        action_html = f"""
        <div style="background-color: #f4f4f4; padding: 40px; text-align: center; border: 2px dashed #999; border-radius: 8px; margin: 30px 0;">
            <h2 style="color: #444; margin-bottom: 5px;">☕ Sip a tea, we'll update you soon if there is any trade.</h2>
            <p style="color: #666; font-size: 14px;">The current mathematical setup evaluates to <strong>{data['trend']}</strong>. Capital preservation is our top priority. The system will alert you when high-probability momentum returns.</p>
        </div>
        """
    else:
        action_html = f"""
        <div class="banner">ACTION: {data['action']}</div>
        <table class="content-table">
            <tr>
                <td style="width: 100%;">
                    <div class="box">
                        <h3>Trade Execution Matrix</h3>
                        <p><strong>Setup Status:</strong> {data['trend']}</p>
                        <p><strong>Current Market Price:</strong> Rs. {data['price']:.2f}</p>
                        <p><strong>Entry Zone:</strong> Near Rs. {data['price']:.2f}</p>
                        <p><strong>Primary Target:</strong> <span style="color: #2d5a00; font-weight: bold;">Rs. {data['tgt']:.2f}</span></p>
                        <p><strong>Strict Stoploss:</strong> <span style="color: #d93025; font-weight: bold;">Rs. {data['sl']:.2f}</span></p>
                    </div>
                </td>
            </tr>
        </table>
        """

    # CHART + DETAILED ANALYSIS HTML
    charts_html = ""
    for item in data['charts']:
        if item['path'] and os.path.exists(item['path']):
            charts_html += f"""
            <div style="page-break-inside: avoid; margin-bottom: 30px;">
                <h3 style="text-align: center; color: #333; margin-top: 20px;">{item['title']}</h3>
                <div style="text-align:center;"><img src="{os.path.abspath(item['path'])}" style="width:100%; border:1px solid #ccc; box-shadow: 0px 4px 8px rgba(0,0,0,0.1);"></div>
                <div class="box" style="margin-top: 10px; background-color: #fff; border-left: 4px solid {data['color']};">
                    <p style="margin: 0;"><strong>A to Z Breakdown:</strong> {item['text']}</p>
                </div>
            </div>
            """

    html = f"""
    <html><head><meta charset="utf-8"><style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 20px; font-size: 13px; line-height: 1.6; }}
        .header-table {{ width: 100%; border-bottom: 3px solid {data['color']}; padding-bottom: 10px; margin-bottom: 20px; }}
        .banner {{ background-color: {data['color']}; color: white; padding: 15px; text-align: center; font-size: 20px; font-weight: bold; letter-spacing: 2px; border-radius: 4px; }}
        .content-table {{ width: 100%; margin-top: 20px; border-collapse: collapse; }}
        .content-table td {{ vertical-align: top; padding: 10px; }}
        .box {{ background-color: #f9f9f9; padding: 15px; border-top: 4px solid {data['color']}; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        h3 {{ margin-top: 0; color: #333; text-transform: uppercase; font-size: 14px; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
        .footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #ddd; font-size: 10px; color: #666; text-align: justify; line-height: 1.5; }}
    </style></head><body>
        <table class="header-table">
            <tr>
                <td style="width: 50%;">{logo_html}</td>
                <td style="width: 50%; text-align: right;">
                    <h2 style="margin: 0; color: #333;">INSTITUTIONAL RESEARCH</h2>
                    <span style="color: #666;">Asset: <strong>{ticker}</strong> | Date: {datetime.datetime.now().strftime('%d %b %Y')}</span>
                </td>
            </tr>
        </table>
        
        {action_html}
        {charts_html}
        
        <div class="footer">
            <strong>STRICT DISCLAIMER:</strong> This is just for educational purpose, consult with your advisor before taking any action. Vertex Algo is a quantitative analysis tool and does not guarantee market returns.
        </div>
    </body></html>
    """
    filename = f"{ticker.replace(':', '_')}_{mode}_Premium.pdf"
    with open('temp.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp.html', filename, options={'enable-local-file-access': None, 'margin-top': '10mm', 'margin-right': '10mm', 'margin-bottom': '10mm', 'margin-left': '10mm'})
    return filename

# --- UI APP ---
if os.path.exists('Black_logo.png'):
    st.sidebar.image('Black_logo.png', use_container_width=True) 
else:
    st.sidebar.title("VERTEX ALGO")

with st.sidebar.expander("🔐 Fyers Admin Auth (Auto-Saved)"):
    if st.session_state['fyers_access_token']:
        st.success("✅ Terminal Unlocked globally for today!")
        if st.button("Reset Token (If Expired)"):
            save_token_to_file("")
            st.session_state['fyers_access_token'] = None
            st.rerun()
    else:
        session = fyersModel.SessionModel(client_id=FYERS_CLIENT_ID, secret_key=FYERS_SECRET_KEY, redirect_uri=FYERS_REDIRECT_URI, response_type="code", grant_type="authorization_code")
        st.markdown(f"[🔗 Generate Auth Code Here]({session.generate_authcode()})")
        if st.button("Unlock Terminal"):
            try:
                session.set_token(st.text_input("Paste Auth Code Here", type="password"))
                res = session.generate_token()
                if "access_token" in res:
                    st.session_state['fyers_access_token'] = res["access_token"]
                    save_token_to_file(res["access_token"]) # This saves it permanently for the day!
                    st.success("✅ Token Saved. Refreshing...")
                    st.rerun()
            except Exception as e: st.error("Auth Error. Check code.")

if not st.session_state['fyers_access_token']:
    st.warning("🔒 Terminal Locked. Admin must authenticate.")
    st.stop()

fyers = get_fyers_instance()
st.title("Institutional Trading Terminal")
tab1, tab2 = st.tabs(["Pro Asset Analyzer", "Live FNO Screener"])

with tab1:
    col1, col2 = st.columns([2, 1])
    with col1:
        selected_ticker = st.selectbox("Search Stock (Full NSE Universe)", all_universe)
        user_email = st.text_input("Delivery Email Address")
    with col2: mode = st.radio("Select Profile", ["Intraday (5m/15m)", "Swing (1H/4H/1D)", "Investor (1D/1W)"])

    if st.button("Generate Premium Report"):
        if selected_ticker and user_email:
            with st.spinner(f"Compiling Institutional Multi-Timeframe Data for {selected_ticker}..."):
                if "Intraday" in mode: data = analyze_intraday(fyers, selected_ticker)
                elif "Swing" in mode: data = analyze_swing(fyers, selected_ticker)
                else: data = analyze_investor(fyers, selected_ticker)
                
                if data:
                    pdf_file = generate_pro_pdf(selected_ticker, mode.split()[0], data)
                    msg = EmailMessage()
                    msg['Subject'], msg['From'], msg['To'] = f'Vertex Algo PRO: {selected_ticker}', SENDER_EMAIL, user_email
                    msg.set_content("Your detailed institutional setup is attached.")
                    with open(pdf_file, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_file)
                    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                        smtp.login(SENDER_EMAIL, APP_PASS)
                        smtp.send_message(msg)
                    st.success(f"✅ Report Emailed! Action: {data['action']}")
                    with open(pdf_file, "rb") as pdf: st.download_button("📥 Download Premium Report", data=pdf, file_name=pdf_file, mime="application/pdf")
                else: st.error("Data fetch failed. Check limits.")

with tab2:
    st.markdown("### Live FNO Master Screener")
    screener_email = st.text_input("Email to send Master Screener Table")
    if st.button("Scan Full FNO Universe"):
        if screener_email:
            total_stocks = len(fno_universe)
            st.info(f"Scanning all {total_stocks} FNO stocks safely... This will take about 1-2 minutes to respect Fyers API rate limits. Please do not refresh.")
            triggered = []
            progress = st.progress(0)
            status_text = st.empty()
            
            for i, sym in enumerate(fno_universe):
                status_text.text(f"Analyzing: {sym} ({i+1}/{total_stocks})")
                
                try:
                    df = fetch_fyers_data(fyers, sym, "15", 5)
                    if df is not None and len(df) > 10:
                        df.ta.vwap(append=True); df.ta.rsi(length=14, append=True)
                        price, vwap, rsi = df['close'].iloc[-1], df['VWAP_D'].iloc[-1], df['RSI_14'].iloc[-1]
                        
                        if price > vwap and rsi > 55: 
                            triggered.append((sym, "BUY (LONG)", price, vwap*0.995, price+((price-vwap)*2), "#d4edda", "#155724"))
                        elif price < vwap and rsi < 45: 
                            triggered.append((sym, "SELL (SHORT)", price, vwap*1.005, price-((vwap-price)*2), "#f8d7da", "#721c24"))
                except Exception as e:
                    pass # Ignore if any single stock fails and move to the next
                
                time.sleep(0.3) # RATE LIMIT PROTECTOR
                progress.progress((i + 1) / total_stocks)
                
            status_text.text("Scan Complete!")
            
            if triggered:
                st.success(f"🔥 Found {len(triggered)} High-Probability FNO Setups!")
                rows = "".join([f"<tr style='background-color:{t[5]}; color:{t[6]};'><td><strong>{t[0]}</strong></td><td>{t[1]}</td><td>{t[2]:.2f}</td><td>{t[3]:.2f}</td><td>{t[4]:.2f}</td></tr>" for t in triggered])
                html = f"<html><body><h1>VERTEX ALGO | LIVE FNO SCREENER</h1><p>Scanned at: {datetime.datetime.now().strftime('%d %b %Y, %H:%M:%S')}</p><table style='width:100%; text-align:left; border-collapse:collapse;' border='1'><tr><th>Symbol</th><th>Action</th><th>Entry</th><th>Stoploss</th><th>Target</th></tr>{rows}</table><p style='margin-top:20px; font-size:10px; color:#777;'>DISCLAIMER: This is just for educational purpose, consult with your advisor before taking any action.</p></body></html>"
                
                with open('screen.html', 'w') as f: f.write(html)
                pdfkit.from_file('screen.html', "Master_Screener.pdf", options={'enable-local-file-access': None})
                
                msg = EmailMessage()
                msg['Subject'], msg['From'], msg['To'] = f'🔥 Vertex FNO Screener ({len(triggered)} Trades)', SENDER_EMAIL, screener_email
                msg.set_content("Live setups detected across the entire FNO universe.")
                with open("Master_Screener.pdf", 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename="Master_Screener.pdf")
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
                
                with open("Master_Screener.pdf", "rb") as pdf: st.download_button("📥 Download Full Master Screener", data=pdf, file_name="Master_Screener.pdf", mime="application/pdf")
            else: 
                st.info("No clear setups found in the entire universe right now. Market might be completely sideways.")
