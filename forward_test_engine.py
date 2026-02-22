import streamlit as st
import pandas as pd
import datetime
import calendar
import time
import os
import csv
from streamlit_autorefresh import st_autorefresh

# --- HELPER FUNCTIONS ---
def get_current_weekly_expiry(symbol="NIFTY"):
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

def get_atm_strike(ltp):
    return round(ltp / 50) * 50

def get_spot_symbol():
    return "NSE:NIFTY50-INDEX"

def get_option_symbol(strike, type_ce_pe):
    expiry = get_current_weekly_expiry("NIFTY")
    return f"NSE:NIFTY{expiry}{strike}{type_ce_pe}"

# --- FYERS API FETCHER ---
def fetch_live_data(fyers, symbol, resolution="5", days_back=1):
    now = datetime.datetime.now()
    range_from = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    range_to = now.strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": str(resolution), "date_format": "1", "range_from": range_from, "range_to": range_to, "cont_flag": "1"}
    response = fyers.history(data=data)
    if response.get("s") == "ok" and response.get("candles"):
        df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    return None

# --- AUTO EXECUTION TO LEDGER ---
def auto_execute_paper_trade(strategy_name, action, entry_price):
    """Automatically silently writes the trade to paper_trades.csv"""
    file_exists = os.path.isfile('paper_trades.csv')
    with open('paper_trades.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])
        
        target = round(entry_price * 1.5, 2) # 50% ROI Target
        sl = round(entry_price * 0.7, 2)     # 30% SL
        
        writer.writerow([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 
            "NIFTY", 
            strategy_name, 
            action, 
            entry_price, 
            target, 
            sl, 
            'OPEN', 
            0.0, 0.0
        ])

# --- STRATEGY 1: NIFTY OI PREMIUM FLOW ---
def run_oi_premium_flow(fyers):
    """
    Pure OI/Volume Logic: Compares CE vs PE data.
    If CE Vol & Price goes UP (Long Buildup) AND PE Vol UP & Price DOWN (Short Buildup) -> BUY CE
    """
    # 1. Get Spot Price for ATM
    df_spot = fetch_live_data(fyers, get_spot_symbol())
    if df_spot is None or len(df_spot) < 2: return "Wait", "Fetching Nifty Spot..."
    
    current_spot = df_spot['close'].iloc[-1]
    atm_strike = get_atm_strike(current_spot)
    
    # 2. Fetch ATM CE and PE Data
    ce_sym = get_option_symbol(atm_strike, "CE")
    pe_sym = get_option_symbol(atm_strike, "PE")
    
    df_ce = fetch_live_data(fyers, ce_sym)
    df_pe = fetch_live_data(fyers, pe_sym)
    
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2:
        return "Wait", "Fetching Option Chain Data..."

    # 3. OI/Volume Momentum Math (Comparing last 2 candles)
    ce_price_change = df_ce['close'].iloc[-1] - df_ce['close'].iloc[-2]
    ce_vol_change = df_ce['volume'].iloc[-1] - df_ce['volume'].iloc[-2]
    ce_ltp = df_ce['close'].iloc[-1]
    
    pe_price_change = df_pe['close'].iloc[-1] - df_pe['close'].iloc[-2]
    pe_vol_change = df_pe['volume'].iloc[-1] - df_pe['volume'].iloc[-2]
    pe_ltp = df_pe['close'].iloc[-1]

    # LOGIC 1: BULLISH (CE Long Buildup + PE Short Writing)
    if ce_price_change > 0 and ce_vol_change > 0 and pe_price_change < 0 and pe_vol_change > 0:
        return "BUY CE", ce_ltp
        
    # LOGIC 2: BEARISH (PE Long Buildup + CE Short Writing)
    elif pe_price_change > 0 and pe_vol_change > 0 and ce_price_change < 0 and ce_vol_change > 0:
        return "BUY PE", pe_ltp

    return "Neutral", f"Smart Money is consolidating. ATM Strike: {atm_strike}"

# --- UI RENDERER ---
def render_ui(fyers):
    # Initialize Memory for Auto-Trades to avoid spamming the CSV every 5 mins
    if 'last_oi_trade_time' not in st.session_state:
        st.session_state['last_oi_trade_time'] = None

    st.markdown("### 🤖 Fully Automated Forward Testing Engine")
    st.write("Turn on the engine. It will silently run in the background, detect strategy criteria, and automatically execute paper trades into your Ledger.")
    
    # ---------------------------------------------------------
    # TOP CONTROL PANEL
    # ---------------------------------------------------------
    head_col1, head_col2 = st.columns([3, 1])
    with head_col1:
        st.info("System will execute a maximum of 1 trade per strategy every 30 minutes to prevent over-trading.")
    with head_col2:
        engine_on = st.toggle("🟢 Master Engine ON", value=False)
        if engine_on:
            # Refreshes the page every 3 minutes silently
            st_autorefresh(interval=3 * 60 * 1000, key="fw_test_refresh")
            st.success("Auto-Pilot Running in Background")

    st.markdown("---")
    st.markdown("#### Active Strategy Cards")
    
    # ---------------------------------------------------------
    # STRATEGY CARD 1: NIFTY OI PREMIUM FLOW
    # ---------------------------------------------------------
    col_s1, col_s2, col_s3 = st.columns(3)
    
    with col_s1:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #1f77b4; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">Nifty OI Premium Flow</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Pure Open Interest & Volume logic. Detects divergence between CE and PE smart money writing.</p>
            <div style="margin-top: 15px;">
                <span style="background-color: #2b313c; color: #4caf50; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: ACTIVE</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Background Execution Logic
        if engine_on:
            signal, data = run_oi_premium_flow(fyers)
            
            # Rate Limiter: Only take a trade if 30 mins have passed since the last one
            now = datetime.datetime.now()
            can_trade = True
            if st.session_state['last_oi_trade_time'] is not None:
                time_diff = (now - st.session_state['last_oi_trade_time']).total_seconds() / 60
                if time_diff < 30:
                    can_trade = False

            if "BUY" in signal and can_trade:
                auto_execute_paper_trade("Nifty OI Flow Auto", signal, data)
                st.session_state['last_oi_trade_time'] = now
                st.toast(f"🤖 Auto-Executed: {signal} at Rs.{data}")
            elif not can_trade:
                st.caption(f"Cooling down. Last trade was recently.")
            else:
                st.caption(f"Live Status: {data}")

    # ---------------------------------------------------------
    # STRATEGY CARD 2: PLACEHOLDER FOR FUTURE
    # ---------------------------------------------------------
    with col_s2:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #404654; padding: 20px; border-radius: 8px; opacity: 0.6;">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">[Empty Slot]</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Add your next proprietary algorithm here. System scales horizontally.</p>
            <div style="margin-top: 15px;">
                <span style="background-color: #2b313c; color: #888; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: OFFLINE</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
