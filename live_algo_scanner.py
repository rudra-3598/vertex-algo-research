import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import calendar
import time
import os
import csv
from streamlit_autorefresh import st_autorefresh

# --- HELPER FUNCTIONS ---
def get_current_weekly_expiry(symbol):
    """Placeholder logic: Usually NSE weekly expiries are Thursdays (Nifty) or Wednesdays (BankNifty)"""
    now = datetime.datetime.now()
    # Simplified logic for current month expiry string (e.g., "26FEB")
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
    else: return round(ltp / 10) * 10

def get_spot_symbol(sym):
    if sym == "NIFTY": return "NSE:NIFTY50-INDEX"
    elif sym == "BANKNIFTY": return "NSE:NIFTYBANK-INDEX"
    return f"NSE:{sym}-EQ"

def get_option_symbol(sym, strike, type_ce_pe):
    expiry = get_current_weekly_expiry(sym)
    return f"NSE:{sym}{expiry}{strike}{type_ce_pe}"

# --- FYERS API FETCHER ---
def fetch_live_data(fyers, symbol, resolution, days_back=2):
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

# --- POWER SCALPER LOGIC (YT STRATEGY) ---
def analyze_power_scalper(fyers, asset_symbol):
    """
    Step 1: Check 5-Min Spot for Pivot Breakout (R1/S1).
    Step 2: If breakout, fetch ATM Option on 2-Min.
    Step 3: Check Option 2-Min for SuperTrend & RSI > 60.
    """
    spot_sym = get_spot_symbol(asset_symbol)
    df_spot = fetch_live_data(fyers, spot_sym, "5", 5)
    
    if df_spot is None or len(df_spot) < 20:
        return {"Status": "Error", "Message": "Insufficient Spot Data", "Signal": "NONE"}

    # 1. Calculate Standard Pivots on Spot (Simplified daily pivots)
    # Finding yesterday's H, L, C for today's pivots
    df_daily = df_spot.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    if len(df_daily) < 2: return {"Status": "Wait", "Message": "Building Pivot Data...", "Signal": "NONE"}
    
    prev_h = df_daily['high'].iloc[-2]
    prev_l = df_daily['low'].iloc[-2]
    prev_c = df_daily['close'].iloc[-2]
    
    pivot = (prev_h + prev_l + prev_c) / 3
    r1 = (2 * pivot) - prev_l
    s1 = (2 * pivot) - prev_h
    
    current_spot = df_spot['close'].iloc[-1]
    atm_strike = get_atm_strike(asset_symbol, current_spot)
    
    is_r1_break = current_spot > r1
    is_s1_break = current_spot < s1
    
    # If no breakout on 5-min Spot, don't waste API calls on Options
    if not (is_r1_break or is_s1_break):
        return {"Status": "Scanning Spot", "Message": f"Spot ₹{current_spot:.1f} inside R1({r1:.1f}) & S1({s1:.1f}) range. No Breakout.", "Signal": "NONE", "Spot": current_spot}

    # 2. Breakout Found! Fetch 2-Min Option Data
    opt_type = "CE" if is_r1_break else "PE"
    opt_sym = get_option_symbol(asset_symbol, atm_strike, opt_type)
    
    df_opt = fetch_live_data(fyers, opt_sym, "2", 2)
    if df_opt is None or len(df_opt) < 15:
        return {"Status": "Fetching Option", "Message": f"Breakout detected! Waiting for {opt_sym} volume...", "Signal": "NONE", "Spot": current_spot}

    # 3. Calculate SuperTrend & RSI on 2-Min Option Chart
    df_opt.ta.supertrend(length=10, multiplier=3.0, append=True)
    df_opt.ta.rsi(length=14, append=True)
    df_opt.ta.atr(length=14, append=True)
    
    st_cols = [c for c in df_opt.columns if 'SUPERTd' in c]
    if not st_cols: return {"Status": "Wait", "Message": "Calculating Option Indicators...", "Signal": "NONE"}
    st_dir_col = st_cols[0]
    
    current_st = df_opt[st_dir_col].iloc[-1]
    current_rsi = df_opt['RSI_14'].iloc[-1]
    current_opt_price = df_opt['close'].iloc[-1]
    current_atr = df_opt['ATRr_14'].iloc[-1]

    # FINAL MOMENTUM CHECK
    if current_st == 1 and current_rsi >= 60:
        signal = f"BUY {opt_sym}"
        sl = current_opt_price - current_atr
        tgt = current_opt_price + (current_atr * 1.5)
        return {"Status": "🔥 GAMMA BLAST", "Message": f"Spot Breakout + Option RSI is {current_rsi:.1f}!", "Signal": signal, "Entry": current_opt_price, "SL": sl, "Target": tgt, "Spot": current_spot, "Asset": asset_symbol}
    
    return {"Status": "Wait", "Message": f"Breakout on Spot, but Option ({opt_type}) RSI is {current_rsi:.1f} (Needs > 60).", "Signal": "NONE", "Spot": current_spot}

# --- UI RENDERER ---
def render_ui(fyers):
    st.markdown("### ⚡ Live Algo Command Center")
    st.write("Auto-scans Live Multi-Timeframe setups. When Nifty 5-Min breaks out AND Option 2-Min RSI > 60, it fires an alert!")
    
    col_a, col_b = st.columns([3, 1])
    with col_b:
        auto_pilot = st.toggle("🤖 Live Auto-Scan (3 Min)", value=False)
        if auto_pilot:
            st_autorefresh(interval=3 * 60 * 1000, key="algo_refresh")
            st.success("Auto-Pilot Active")
            
    st.markdown("---")
    
    assets_to_scan = ["NIFTY", "BANKNIFTY", "RELIANCE"] # Start with Top 3 for speed
    
    if st.button("🚀 Run Manual Deep Scan Now", type="primary") or auto_pilot:
        scan_results = []
        progress_bar = st.progress(0)
        status_txt = st.empty()
        
        for i, asset in enumerate(assets_to_scan):
            status_txt.text(f"Scanning Dual-Charts for {asset}...")
            try:
                result = analyze_power_scalper(fyers, asset)
                scan_results.append(result)
            except Exception as e:
                pass
            time.sleep(0.5) # Prevents Fyers API overload
            progress_bar.progress((i + 1) / len(assets_to_scan))
            
        status_txt.text(f"Scan Complete at {datetime.datetime.now().strftime('%H:%M:%S')}!")
        
        # Display Results
        if scan_results:
            st.session_state['algo_scan_results'] = scan_results
    
    if 'algo_scan_results' in st.session_state:
        for res in st.session_state['algo_scan_results']:
            if res['Signal'] == "NONE":
                st.info(f"**{res.get('Spot', '')}** | Status: {res['Status']} | {res['Message']}")
            else:
                # WE GOT A TRADE ALERT!
                st.markdown(f"""
                <div style="background-color: rgba(44, 160, 44, 0.15); border-left: 5px solid #4caf50; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #4caf50;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <h2 style="margin: 0; color: #4caf50;">{res['Status']}</h2>
                        <h3 style="margin: 0; color: #fff;">{res['Signal']}</h3>
                    </div>
                    <p style="color: #ccc; font-size: 14px; margin-top: 5px;">{res['Message']}</p>
                    <div style="display: flex; gap: 20px; margin-top: 15px;">
                        <div style="background-color: #1a1c23; padding: 10px; border-radius: 5px;"><span style="color: #888;">Entry:</span> <strong style="color: #fff;">₹{res['Entry']:.2f}</strong></div>
                        <div style="background-color: #1a1c23; padding: 10px; border-radius: 5px;"><span style="color: #888;">Target:</span> <strong style="color: #4caf50;">₹{res['Target']:.2f}</strong></div>
                        <div style="background-color: #1a1c23; padding: 10px; border-radius: 5px;"><span style="color: #888;">Stoploss:</span> <strong style="color: #ff5252;">₹{res['SL']:.2f}</strong></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # --- ALGO TO LEDGER LINK ---
                if st.button(f"📝 Send {res['Signal']} to Paper Ledger", key=f"algo_track_{res['Asset']}"):
                    file_exists = os.path.isfile('paper_trades.csv')
                    with open('paper_trades.csv', 'a', newline='') as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])
                        
                        writer.writerow([
                            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 
                            res['Asset'], 
                            "Algo Scalper", 
                            res['Signal'], 
                            round(res['Entry'], 2), 
                            round(res['Target'], 2), 
                            round(res['SL'], 2), 
                            'OPEN', 
                            0.0, 0.0
                        ])
                    st.success(f"✅ Trade {res['Signal']} Executed in Paper Ledger!")
