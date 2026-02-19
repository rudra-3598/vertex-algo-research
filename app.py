import streamlit as st
import yfinance as yf
import pandas_ta as ta
import mplfinance as mpf
import pandas as pd
import pdfkit
import os
import smtplib
from email.message import EmailMessage

st.set_page_config(page_title="Vertex Algo | Institutional Research", layout="wide")

def format_financials(df):
    if df is None or df.empty: return "<p>Data not available.</p>"
    df = df.iloc[:, :4]
    df.columns = [c.strftime('%b %Y') if hasattr(c, 'strftime') else str(c) for c in df.columns]
    df = df.dropna(how='all')
    return df.to_html(classes="table", border=0, na_rep="-", float_format=lambda x: f"{x:,.0f}")

def fmt_pct(val):
    if val == 'N/A' or val is None: return 'N/A'
    try: return f"{float(val)*100:.2f}%"
    except: return str(val)

def generate_smart_commentary(ticker, trend, price, rsi, adx, rev_growth, roe):
    # This acts as our Lightning-Fast AI replacement
    tech_text = f"In the current market landscape, {ticker} is exhibiting a {trend} trajectory relative to its long-term moving averages. At the current price of Rs. {price:.2f}, the momentum indicators show a Relative Strength Index (RSI) of {rsi:.2f}, indicating that the stock is {'oversold and ripe for reversal' if rsi < 40 else 'overbought and may face resistance' if rsi > 70 else 'in a neutral momentum zone'}. Furthermore, the Average Directional Index (ADX) reading of {adx:.2f} suggests a {'strong' if adx > 25 else 'weak'} underlying trend strength."
    
    ca_text = f"From a fundamental and operational standpoint, the company has reported a recent revenue growth of {rev_growth}. The Return on Equity (ROE) stands at {roe}, reflecting the management's current efficiency in generating returns from shareholder capital. Given the quantitative technical setup and these fundamental balance sheet metrics, our institutional view leans towards a strict adherence to the calculated risk-reward parameters. Stoploss levels must be respected due to current market volatilities."
    return tech_text, ca_text

def build_v6_premium_report(ticker):
    stock = yf.Ticker(ticker)
    df = stock.history(period="1y")
    info = stock.info
    if df.empty: return None
        
    company_name = info.get('longName', ticker)
    sector = info.get('sector', 'N/A')
    industry = info.get('industry', 'N/A')
    summary = info.get('longBusinessSummary', 'Company profile data is currently unavailable.')
    
    mkt_cap = info.get('marketCap', 'N/A')
    if mkt_cap != 'N/A': mkt_cap = f"Rs. {mkt_cap / 10000000:,.2f} Cr" 
    pe_ratio = info.get('forwardPE', 'N/A')
    rev_growth = fmt_pct(info.get('revenueGrowth', 'N/A'))
    roe = fmt_pct(info.get('returnOnEquity', 'N/A'))
    debt_equity = info.get('debtToEquity', 'N/A')
    op_margin = fmt_pct(info.get('operatingMargins', 'N/A'))

    # Financials
    try:
        income_stmt_html = format_financials(stock.quarterly_incomestmt)
        balance_sheet_html = format_financials(stock.quarterly_balance_sheet)
        holders = stock.major_holders
        if holders is not None and not holders.empty:
            holders_html = "<table class='table'><tr><th>Value</th><th>Category</th></tr>"
            for index, row in holders.iterrows():
                val, cat = (row.iloc[0], row.iloc[1]) if len(row)>1 else (0, "Unknown")
                if isinstance(val, str) and not str(val).replace('.','',1).isdigit(): val, cat = cat, val
                try:
                    num = float(val)
                    val_str = f"{num*100:.2f}%" if num < 2.0 else f"{num:,.0f}"
                except: val_str = str(val)
                holders_html += f"<tr><td><strong>{val_str}</strong></td><td>{cat}</td></tr>"
            holders_html += "</table>"
        else:
            holders_html = "<p>Data not available.</p>"
    except:
        income_stmt_html = balance_sheet_html = holders_html = "<p>Data error</p>"

    # Technicals
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.macd(append=True)
    df.ta.atr(length=14, append=True)
    df.ta.adx(length=14, append=True) 
    
    latest = df.iloc[-1]
    price, atr, rsi, macd_hist, adx = latest['Close'], latest['ATRr_14'], latest['RSI_14'], latest['MACDh_12_26_9'], latest['ADX_14']
    
    if price > latest['EMA_200'] and rsi > 55 and macd_hist > 0 and adx > 20:
        trend, trade_type, trade_color, box_color = "Strong Bullish", "BUY (LONG)", "#2d5a00", "#f4fdf0"
        sl_price, target_price = price - (1.5 * atr), price + (2 * (1.5 * atr))
    elif price < latest['EMA_200'] and rsi < 45 and macd_hist < 0 and adx > 20:
        trend, trade_type, trade_color, box_color = "Strong Bearish", "SELL (SHORT)", "#d93025", "#fff0f0"
        sl_price, target_price = price + (1.5 * atr), price - (2 * (1.5 * atr))
    else:
        trend, trade_type, trade_color, box_color = "Neutral / Choppy", "NO TRADE (WAIT)", "#666666", "#f4f4f4"
        sl_price = target_price = price

    df_chart = df.tail(120) 
    ap = [mpf.make_addplot(df_chart['EMA_50'], color='blue', width=1.5), mpf.make_addplot(df_chart['EMA_200'], color='red', width=1.5)]
    chart_path = 'stock_chart.png'
    mpf.plot(df_chart, type='candle', style='yahoo', addplot=ap, volume=True, savefig=dict(fname=chart_path, dpi=300, bbox_inches='tight'), figratio=(14,4))

    tech_summary, ca_analysis = generate_smart_commentary(ticker, trend, price, rsi, adx, rev_growth, roe)

    logo_path = 'Black_logo.png'
    logo_html = f'<img src="{logo_path}" class="logo">' if os.path.exists(logo_path) else '<h2>VERTEX ALGO</h2>'

    html_content = f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1a1a1a; margin: 20px; font-size: 11px; }}
        .avoid-break {{ page-break-inside: avoid; }} .page-break {{ page-break-before: always; }}
        .header {{ border-bottom: 3px solid #bdf271; padding-bottom: 10px; margin-bottom: 20px; display: table; width: 100%; }}
        .header-left {{ display: table-cell; vertical-align: middle; width: 50%; }}
        .header-right {{ display: table-cell; text-align: right; vertical-align: middle; width: 50%; }}
        .logo {{ max-height: 180px; width: auto; object-fit: contain; }} 
        .title {{ color: #333; margin: 0; font-size: 22px; text-transform: uppercase; letter-spacing: 1px; }}
        .subtitle {{ color: #666; font-size: 13px; margin-top: 5px; }}
        .row {{ display: table; width: 100%; margin-bottom: 15px; }}
        .col-main {{ display: table-cell; width: 60%; padding-right: 20px; vertical-align: top; }}
        .col-side {{ display: table-cell; width: 40%; vertical-align: top; }}
        h2 {{ color: #000; border-bottom: 2px solid #333; padding-bottom: 5px; font-size: 16px; text-transform: uppercase; margin-top: 25px; }}
        h3 {{ color: #000; border-bottom: 1px solid #ccc; padding-bottom: 5px; font-size: 13px; text-transform: uppercase; background-color: #f4f4f4; padding: 5px; margin-top: 0; }}
        p {{ line-height: 1.6; text-align: justify; margin-top: 5px; font-size: 11px; }}
        .table {{ width: 100%; border-collapse: collapse; font-size: 10px; margin-top: 10px; margin-bottom: 15px; }}
        .table th, .table td {{ border: 1px solid #e0e0e0; padding: 6px; text-align: right; }}
        .table th {{ background-color: #f8f9fa; color: #333; text-align: left; }}
        .table td:first-child {{ text-align: left; font-weight: bold; color: #555; }}
        .trade-box {{ background-color: {box_color}; border-left: 5px solid {trade_color}; padding: 15px; margin-top: 15px; font-size: 13px; }}
        .chart-img {{ width: 100%; max-height: 250px; object-fit: contain; border: 1px solid #eee; margin-top: 10px; }}
        .ca-box {{ background-color: #f9f9f9; border-top: 3px solid #333; padding: 15px; margin-top: 20px; border-bottom: 1px solid #ddd; }}
    </style></head><body>
        <div class="header">
            <div class="header-left">{logo_html}</div>
            <div class="header-right">
                <h1 class="title">Institutional Equity Research</h1>
                <div class="subtitle"><strong>Ticker:</strong> {ticker} | <strong>Date:</strong> {pd.Timestamp.now().strftime('%d %b %Y')}</div>
            </div>
        </div>
        <div class="row">
            <div class="col-main">
                <div class="avoid-break">
                    <h3>Company Profile & Business Presence</h3>
                    <p><strong>{company_name}</strong> (Sector: {sector} | Industry: {industry})</p>
                    <p>{summary[:600]}...</p> 
                </div>
                <div class="avoid-break" style="margin-top: 15px;">
                    <h3>Technical & Momentum Overview</h3>
                    <p>{tech_summary}</p>
                </div>
                <div class="trade-box avoid-break">
                    <h3 style="background:none; border:none; padding:0;">Vertex Quantitative Setup</h3>
                    <div><strong>Action:</strong> <span style="color:{trade_color}; font-weight:bold; font-size: 16px;">{trade_type}</span></div>
                    <div style="margin-top:8px;"><strong>Status:</strong> {trend}</div>
                    <div style="margin-top:8px;"><strong>Entry:</strong> Near Rs. {price:.2f} | <strong>Target:</strong> Rs. {target_price:.2f}</div>
                    <div style="margin-top:8px;"><strong>Strict Stoploss:</strong> Rs. {sl_price:.2f} (ATR Based)</div>
                </div>
            </div>
            <div class="col-side">
                <div class="avoid-break">
                    <h3>Key Financial Health Metrics</h3>
                    <table class="table">
                        <tr><th>CMP</th><td>Rs. {price:.2f}</td></tr>
                        <tr><th>Market Cap</th><td>{mkt_cap}</td></tr>
                        <tr><th>Forward P/E</th><td>{pe_ratio}</td></tr>
                        <tr><th>Rev Growth (YoY)</th><td><strong style="color:green;">{rev_growth}</strong></td></tr>
                        <tr><th>Return on Equity</th><td>{roe}</td></tr>
                        <tr><th>Debt to Equity</th><td>{debt_equity}</td></tr>
                        <tr><th>Operating Margin</th><td>{op_margin}</td></tr>
                    </table>
                </div>
                <div class="avoid-break">
                    <h3>Major Shareholding Pattern</h3>
                    {holders_html}
                </div>
            </div>
        </div>
        <div class="avoid-break" style="text-align: center;">
            <h3 style="text-align: left;">Technical Chart (120 Days with Confluence EMAs)</h3>
            <img src="{chart_path}" class="chart-img">
        </div>
        <div class="page-break"></div>
        <div class="header">
            <div class="header-left">{logo_html}</div>
            <div class="header-right">
                <h1 class="title">Deep Financial Statements</h1>
                <div class="subtitle">{company_name}</div>
            </div>
        </div>
        <div class="ca-box avoid-break">
            <h2 style="margin-top: 0; border: none; font-size: 15px; color: #2d5a00;">Operational Analysis (CA View)</h2>
            <p style="font-size: 12px; line-height: 1.6;">{ca_analysis}</p>
        </div>
        <div class="row avoid-break" style="margin-top: 20px;">
            <div class="col-main" style="width: 50%;">
                <h3>Quarterly Income Statement</h3>
                {income_stmt_html}
            </div>
            <div class="col-side" style="width: 50%;">
                <h3>Quarterly Balance Sheet</h3>
                {balance_sheet_html}
            </div>
        </div>
    </body></html>"""
    
    pdf_filename = f"{ticker}_Vertex_Masterpiece.pdf"
    with open('temp.html', 'w') as f: f.write(html_content)
    pdfkit.from_file('temp.html', pdf_filename, options={'enable-local-file-access': None, 'margin-top': '0.5in', 'margin-right': '0.5in', 'margin-bottom': '0.5in', 'margin-left': '0.5in', 'encoding': "UTF-8"})
    return pdf_filename

def send_email(sender_email, app_password, receiver_email, pdf_filename, ticker):
    try:
        msg = EmailMessage()
        msg['Subject'] = f'Vertex Algo: Premium Institutional Report - {ticker}'
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg.set_content(f"Hello,\n\nPlease find attached the premium institutional quantitative research report for {ticker} generated by Vertex Algo.\n\nHappy Trading,\nTeam Vertex")
        with open(pdf_filename, 'rb') as f: msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_filename)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, app_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Email Error: {e}")
        return False

# UI Setup
if os.path.exists('Black_logo.png'): st.image('Black_logo.png', width=200)
else: st.title("VERTEX ALGO")

st.markdown("### Generate & Email Institutional Research Reports")

st.sidebar.header("⚙️ Admin Email Settings")
sender_email = st.sidebar.text_input("Your Vertex Gmail")
app_pass = st.sidebar.text_input("Gmail App Password", type="password")

ticker_input = st.text_input("Stock Ticker (e.g., HDFCBANK.NS, RELIANCE.NS)")
user_email = st.text_input("Your Email Address")

if st.button("Generate & Email Premium Report"):
    if not sender_email or not app_pass: st.warning("⚠️ Please configure Admin email settings.")
    elif ticker_input and user_email:
        with st.spinner(f"Compiling Institutional Report for {ticker_input}..."):
            pdf_file = build_v6_premium_report(ticker_input.upper())
            if pdf_file:
                st.success("Report generated! Emailing...")
                if send_email(sender_email, app_pass, user_email, pdf_file, ticker_input.upper()):
                    st.balloons()
                    st.success("🚀 Premium Report delivered to your inbox!")
                    with open(pdf_file, "rb") as pdf:
                        st.download_button("📥 Download PDF directly", data=pdf, file_name=pdf_file, mime="application/pdf")
            else: st.error("❌ Invalid Ticker.")
    else: st.error("Please fill all details.")
