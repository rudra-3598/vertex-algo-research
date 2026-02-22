import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import time
import os
import pdfkit
import smtplib
from email.message import EmailMessage

# --- DATABASES ---
@st.cache_data
def load_fno_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [f"NSE:{t}-EQ" for t in df['SYMBOL'].dropna().unique()]
        except Exception as e: return ["NSE:NIFTY-EQ"]
    return ["NSE:HDFCBANK-EQ", "NSE:RELIANCE-EQ", "NSE:TCS-EQ"][:50]

fno_universe = load_fno_stocks()

# --- FYERS DATA FETCHER ---
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

# --- UI RENDERER (Called by app.py) ---
def render_ui(fyers):
    st.markdown("### Live FNO Master Screener")
    st.write("Scans the entire FNO universe for immediate Intraday setups based on VWAP & Momentum.")
    
    screener_email = st.text_input("Email to send Master Screener Table")
    
    if st.button("Scan Full FNO Universe"):
        if screener_email:
            try:
                SENDER_EMAIL = st.secrets["EMAIL_USER"]
                APP_PASS = st.secrets["EMAIL_PASS"]
            except:
                st.error("Please configure Email Secrets in Streamlit settings.")
                return

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
                rows = "".join([f"<tr style='background-color:{t[5]}; color:{t[6]};'><td style='padding:8px;'><strong>{t[0]}</strong></td><td style='padding:8px;'>{t[1]}</td><td style='padding:8px;'>{t[2]:.2f}</td><td style='padding:8px;'>{t[3]:.2f}</td><td style='padding:8px;'>{t[4]:.2f}</td></tr>" for t in triggered])
                html = f"<html><head><meta charset='utf-8'></head><body style='font-family: Arial, sans-serif;'><h1>VERTEX ALGO | LIVE FNO SCREENER</h1><p>Scanned at: {datetime.datetime.now().strftime('%d %b %Y, %H:%M:%S')}</p><table style='width:100%; text-align:left; border-collapse:collapse;' border='1'><tr><th style='padding:8px; background-color:#333; color:white;'>Symbol</th><th style='padding:8px; background-color:#333; color:white;'>Action</th><th style='padding:8px; background-color:#333; color:white;'>Entry</th><th style='padding:8px; background-color:#333; color:white;'>Stoploss</th><th style='padding:8px; background-color:#333; color:white;'>Target</th></tr>{rows}</table><p style='margin-top:20px; font-size:10px; color:#777;'>DISCLAIMER: This is just for educational purpose, consult with your advisor before taking any action.</p></body></html>"
                
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
        else:
            st.warning("Please enter an email address to receive the PDF report.")
