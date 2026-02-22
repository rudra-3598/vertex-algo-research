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

if 'fyers_access_token' not in st.session_state: st.session_state['fyers_access_token'] = None

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
        except Exception as e:
            st.error(f"Error reading nse_stock_data.csv: {e}")
            return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ"]
    else:
        st.error("❌ 'nse_stock_data.csv' file aapke GitHub mein nahi mili! Kripya upload karein.")
        return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ", "NSE:ITC-EQ"]

@st.cache_data
def load_fno_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [f"NSE:{t}-EQ" for t in df['SYMBOL'].dropna().unique()]
        except Exception as e:
            st.error(f"Error reading nse_fno_stocks.csv: {e}")
            return ["NSE:NIFTY-EQ"]
    else:
        st.error("❌ 'nse_fno_stocks.csv' file aapke GitHub mein nahi mili! Kripya upload karein.")
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

# --- PRO LOGIC ENGINE WITH DETAILED METRICS ---
def analyze_intraday(fyers, symbol):
    df_5m = fetch_fyers_data(fyers, symbol, "5", 4)
    df_15m = fetch_fyers_data(fyers, symbol, "15", 8)
    if df_5m is None or df_15m is None: return None
    
    df_5m.ta.vwap(append=True); df_5m.ta.ema(length=9, append=True); df_5m.ta.rsi(length=14, append=True)
    df_15m.ta.vwap(append=True); df_15m.ta.ema(length=9, append=True); df_15m.ta.rsi(length=14, append=True)
    
    price, vwap_5m, ema9_5m, rsi_15m = df_5m['close'].iloc[-1], df_5m['VWAP_D'].iloc[-1], df_5m['EMA_9'].iloc[-1], df_15m['RSI_14'].iloc[-1]
    
    chart_5m = generate_chart(df_5m, f"{symbol} - 5 Min (VWAP & 9-EMA)", "chart_5m.png", True)
    chart_15m = generate_chart(df_15m, f"{symbol} - 15 Min", "chart_15m.png", True)
    
    if price > vwap_5m and ema9_5m > vwap_5m and rsi_15m > 55:
        trend, action, color = "Bullish Momentum", "BUY (LONG)", "#2d5a00"
        sl, tgt = vwap_5m * 0.995, price + ((price - (vwap_5m * 0.995)) * 2)
        rationale = "Price is sustaining above Session VWAP on the 5m timeframe, supported by the 9-EMA. The 15m RSI confirms strong buying momentum, aligning multiple timeframes for a high-probability long scalp."
    elif price < vwap_5m and ema9_5m < vwap_5m and rsi_15m < 45:
        trend, action, color = "Bearish Breakdown", "SELL (SHORT)", "#d93025"
        sl, tgt = vwap_5m * 1.005, price - (((vwap_5m * 1.005) - price) * 2)
        rationale = "Price faces heavy rejection below Session VWAP. The 9-EMA has crossed below VWAP, and 15m RSI is weak. Institutional selling pressure is dominant."
    else:
        trend, action, color = "Choppy / VWAP Magnet", "NO TRADE", "#666666"
        sl, tgt, rationale = price, price, "Price is hovering near the VWAP. Institutional volumes are conflicting. Wait for a clear directional breakout before deploying capital."

    metrics = {"RSI (15m)": f"{rsi_15m:.2f}", "Session VWAP": f"Rs. {vwap_5m:.2f}", "9-EMA (5m)": f"Rs. {ema9_5m:.2f}", "Trend Strength": "High" if abs(rsi_15m-50)>10 else "Neutral"}
    return {"price": price, "trend": trend, "action": action, "sl": sl, "tgt": tgt, "color": color, "rationale": rationale, "metrics": metrics, "charts": [chart_5m, chart_15m]}

def analyze_swing(fyers, symbol):
    df_1h = fetch_fyers_data(fyers, symbol, "60", 30); df_4h = fetch_fyers_data(fyers, symbol, "240", 90); df_1d = fetch_fyers_data(fyers, symbol, "1D", 365)
    if df_1h is None or df_4h is None or df_1d is None: return None
    
    for df in [df_1h, df_4h, df_1d]: df.ta.ema(length=20, append=True); df.ta.ema(length=50, append=True); df.ta.rsi(length=14, append=True); df.ta.macd(append=True)
    
    price, ema50_1d, rsi_1d, macd_1d = df_1d['close'].iloc[-1], df_1d['EMA_50'].iloc[-1], df_1d['RSI_14'].iloc[-1], df_1d['MACDh_12_26_9'].iloc[-1]
    rsi_4h = df_4h['RSI_14'].iloc[-1]
    
    chart_1h = generate_chart(df_1h, f"{symbol} - 1 Hour", "chart_1h.png")
    chart_4h = generate_chart(df_4h, f"{symbol} - 4 Hour", "chart_4h.png")
    chart_1d = generate_chart(df_1d, f"{symbol} - Daily", "chart_1d.png")
    
    if price > ema50_1d and macd_1d > 0 and rsi_4h > 55:
        trend, action, color = "Swing Bullish", "BUY (LONG)", "#2d5a00"
        sl, tgt = df_1d['EMA_20'].iloc[-1], price + ((price - df_1d['EMA_20'].iloc[-1]) * 2.5)
        rationale = "Daily structure remains firmly bullish above the 50-EMA. Positive MACD divergence confirms trend continuation. The 4H and 1H pullbacks provide an optimal risk-to-reward entry point."
    elif price < ema50_1d and macd_1d < 0 and rsi_4h < 45:
        trend, action, color = "Swing Bearish", "SELL (SHORT)", "#d93025"
        sl, tgt = df_1d['EMA_20'].iloc[-1], price - ((df_1d['EMA_20'].iloc[-1] - price) * 2.5)
        rationale = "Asset is trading in a defined daily downtrend below the 50-EMA. Negative MACD histograms and lower-highs on the 4H chart confirm sustained distribution by larger players."
    else:
        trend, action, color = "Consolidation", "NO TRADE", "#666666"
        sl, tgt, rationale = price, price, "Conflicting signals across multiple timeframes. The daily trend lacks momentum, suggesting a sideways accumulation/distribution phase."

    metrics = {"Daily RSI": f"{rsi_1d:.2f}", "4H RSI": f"{rsi_4h:.2f}", "50-EMA (Daily)": f"Rs. {ema50_1d:.2f}", "MACD Hist": f"{macd_1d:.2f}"}
    return {"price": price, "trend": trend, "action": action, "sl": sl, "tgt": tgt, "color": color, "rationale": rationale, "metrics": metrics, "charts": [chart_1h, chart_4h, chart_1d]}

def analyze_investor(fyers, symbol):
    df_1d = fetch_fyers_data(fyers, symbol, "1D", 700)
    if df_1d is None: return None
    df_1d.ta.ema(length=50, append=True); df_1d.ta.ema(length=200, append=True); df_1d.ta.rsi(length=14, append=True)
    
    price, ema50, ema200, rsi = df_1d['close'].iloc[-1], df_1d['EMA_50'].iloc[-1], df_1d['EMA_200'].iloc[-1], df_1d['RSI_14'].iloc[-1]
    chart_1d = generate_chart(df_1d, f"{symbol} - Daily (50 & 200 EMA)", "chart_1d_inv.png")
    
    if price < ema200 * 1.05 and price > ema200 * 0.95:
        trend, action, color = "Accumulation Zone", "SIP / BUY", "#2d5a00"
        rationale = "Price has successfully retraced to the institutional 200-EMA support level. Historically, establishing long positions in this specific zone yields the highest asymmetric returns for value investors."
    elif price < ema200 * 0.85:
        trend, action, color = "Deep Value / Oversold", "ACCUMULATE", "#2d5a00"
        rationale = "The asset is trading at a severe discount to its historical 200-Day moving average. Assuming stable corporate fundamentals, this represents a rare deep-value capitulation entry."
    else:
        trend, action, color = "Overextended", "HOLD", "#666666"
        rationale = "Price is extended significantly beyond the 200-EMA mean. Deploying fresh capital at these valuations carries higher systematic risk. Wait for a healthy macroeconomic correction."
        
    metrics = {"200-Day EMA": f"Rs. {ema200:.2f}", "50-Day EMA": f"Rs. {ema50:.2f}", "Daily RSI": f"{rsi:.2f}", "Deviation from Mean": f"{((price-ema200)/ema200)*100:.1f}%"}
    return {"price": price, "trend": trend, "action": action, "sl": price*0.80, "tgt": price*1.50, "color": color, "rationale": rationale, "metrics": metrics, "charts": [chart_1d]}

# --- PROFESSIONAL PDF GENERATOR ---
def generate_pro_pdf(ticker, mode, data):
    logo_path = os.path.abspath('Black_logo.png')
    logo_html = f'<img src="{logo_path}" style="height: 40px;">' if os.path.exists('Black_logo.png') else '<h2>VERTEX ALGO</h2>'
    
    metrics_html = "".join([f"<tr><td style='padding:8px; border:1px solid #ddd;'><strong>{k}</strong></td><td style='padding:8px; border:1px solid #ddd;'>{v}</td></tr>" for k, v in data['metrics'].items()])
    charts_html = "".join([f'<div style="text-align:center; margin-top:20px;"><img src="{os.path.abspath(chart)}" style="width:100%; border:1px solid #ccc; box-shadow: 0px 4px 8px rgba(0,0,0,0.1);"></div>' for chart in data['charts'] if chart and os.path.exists(chart)])

    html = f"""
    <html><head><meta charset="utf-8"><style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 20px; font-size: 13px; }}
        .header-table {{ width: 100%; border-bottom: 3px solid {data['color']}; padding-bottom: 10px; margin-bottom: 20px; }}
        .banner {{ background-color: {data['color']}; color: white; padding: 15px; text-align: center; font-size: 20px; font-weight: bold; letter-spacing: 2px; border-radius: 4px; }}
        .content-table {{ width: 100%; margin-top: 20px; border-collapse: collapse; }}
        .content-table td {{ vertical-align: top; padding: 10px; }}
        .box {{ background-color: #f9f9f9; padding: 15px; border-top: 4px solid {data['color']}; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        h3 {{ margin-top: 0; color: #333; text-transform: uppercase; font-size: 14px; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
        .metrics-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }}
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
        
        <div class="banner">ACTION: {data['action']}</div>
        
        <table class="content-table">
            <tr>
                <td style="width: 50%;">
                    <div class="box">
                        <h3>Trade Execution Matrix</h3>
                        <p><strong>Setup Status:</strong> {data['trend']}</p>
                        <p><strong>Current Market Price:</strong> Rs. {data['price']:.2f}</p>
                        <p><strong>Entry Zone:</strong> Near Rs. {data['price']:.2f}</p>
                        <p><strong>Primary Target:</strong> <span style="color: #2d5a00; font-weight: bold;">Rs. {data['tgt']:.2f}</span></p>
                        <p><strong>Strict Stoploss:</strong> <span style="color: #d93025; font-weight: bold;">Rs. {data['sl']:.2f}</span></p>
                    </div>
                </td>
                <td style="width: 50%;">
                    <div class="box">
                        <h3>Quantitative Indicators</h3>
                        <table class="metrics-table">{metrics_html}</table>
                    </div>
                </td>
            </tr>
        </table>
        
        <div class="box" style="margin-top: 10px;">
            <h3>Technical Trade Rationale</h3>
            <p style="line-height: 1.6;">{data['rationale']}</p>
        </div>
        
        <h3 style="margin-top: 30px; text-align: center;">Multi-Timeframe Chart Analysis</h3>
        {charts_html}
        
        <div class="footer">
            <strong>STRICT DISCLAIMER:</strong> This quantitative research report is generated strictly for educational and informational purposes only. Vertex Algo is an analytical tool and is not registered with SEBI. Equities and derivatives trading involves significant financial risk. Consult with your certified financial advisor before taking any market action. We do not guarantee accuracy or financial returns.
        </div>
    </body></html>
    """
    filename = f"{ticker.replace(':', '_')}_{mode}_Premium.pdf"
    with open('temp.html', 'w') as f: f.write(html)
    pdfkit.from_file('temp.html', filename, options={'enable-local-file-access': None, 'margin-top': '10mm', 'margin-right': '10mm', 'margin-bottom': '10mm', 'margin-left': '10mm'})
    return filename

# --- UI APP ---
st.sidebar.image('Black_logo.png', width=150) if os.path.exists('Black_logo.png') else st.sidebar.title("VERTEX ALGO")

with st.sidebar.expander("🔐 Fyers Admin Auth (Daily)"):
    session = fyersModel.SessionModel(client_id=FYERS_CLIENT_ID, secret_key=FYERS_SECRET_KEY, redirect_uri=FYERS_REDIRECT_URI, response_type="code", grant_type="authorization_code")
    st.markdown(f"[🔗 Generate Auth Code Here]({session.generate_authcode()})")
    if st.button("Unlock Terminal"):
        try:
            session.set_token(st.text_input("Paste Auth Code Here", type="password"))
            res = session.generate_token()
            if "access_token" in res:
                st.session_state['fyers_access_token'] = res["access_token"]
                st.success("✅ Terminal Unlocked!")
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
    if st.button("Scan FNO Universe"):
        if screener_email:
            st.info("Scanning Top 50 FNO stocks safely to respect Fyers API limits...")
            triggered = []
            progress = st.progress(0)
            
            for i, sym in enumerate(fno_universe[:50]):
                df = fetch_fyers_data(fyers, sym, "15", 5) # Faster 15m scan
                if df is not None and len(df) > 10:
                    df.ta.vwap(append=True); df.ta.rsi(length=14, append=True)
                    price, vwap, rsi = df['close'].iloc[-1], df['VWAP_D'].iloc[-1], df['RSI_14'].iloc[-1]
                    
                    if price > vwap and rsi > 55: triggered.append((sym, "BUY (LONG)", price, vwap*0.995, price+((price-vwap)*2), "#d4edda", "#155724"))
                    elif price < vwap and rsi < 45: triggered.append((sym, "SELL (SHORT)", price, vwap*1.005, price-((vwap-price)*2), "#f8d7da", "#721c24"))
                
                time.sleep(0.3)
                progress.progress((i + 1) / 50)
                
            if triggered:
                st.success(f"🔥 Found {len(triggered)} FNO Setups!")
                
                # BEAUTIFUL MASTER SCREENER PDF
                rows = "".join([f"<tr style='background-color:{bg}; color:{fg};'><td><strong>{t[0]}</strong></td><td>{t[1]}</td><td>{t[2]:.2f}</td><td>{t[3]:.2f}</td><td>{t[4]:.2f}</td></tr>" for t in triggered])
                html = f"""
                <html><head><style>
                    body {{ font-family: Arial; padding: 20px; }}
                    h1 {{ color: #2d5a00; border-bottom: 2px solid #2d5a00; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                    th, td {{ padding: 12px; text-align: left; border: 1px solid #ddd; }}
                    th {{ background-color: #333; color: white; }}
                </style></head><body>
                    <h1>VERTEX ALGO | LIVE FNO SCREENER</h1>
                    <p>Scanned at: {datetime.datetime.now().strftime('%d %b %Y, %H:%M:%S')}</p>
                    <table><tr><th>Symbol</th><th>Action</th><th>Entry</th><th>Stoploss</th><th>Target</th></tr>{rows}</table>
                    <p style='margin-top:40px; font-size:10px; color:#777;'>DISCLAIMER: This is just for educational purpose, consult with your advisor before taking any action.</p>
                </body></html>
                """
                with open('screen.html', 'w') as f: f.write(html)
                pdfkit.from_file('screen.html', "Master_Screener.pdf", options={'enable-local-file-access': None})
                
                msg = EmailMessage()
                msg['Subject'], msg['From'], msg['To'] = '🔥 Vertex FNO Screener', SENDER_EMAIL, screener_email
                msg.set_content("Live setups detected.")
                with open("Master_Screener.pdf", 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename="Master_Screener.pdf")
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp: smtp.login(SENDER_EMAIL, APP_PASS); smtp.send_message(msg)
                
                with open("Master_Screener.pdf", "rb") as pdf: st.download_button("📥 Download Master Screener", data=pdf, file_name="Master_Screener.pdf", mime="application/pdf")
            else: st.info("No clear setups found right now.")
