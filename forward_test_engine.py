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
def fetch_live_data(fyers, symbol, resolution="5", days_back=2):
    now = datetime.datetime.now()
    range_from = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    range_to = now.strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": str(resolution), "date_format": "1", "range_from": range_from, "range_to": range_to, "cont_flag": "1", "oi_flag": "1"}
    response = fyers.history(data=data)
    if response.get("s") == "ok" and response.get("candles"):
        cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if len(response["candles"][0]) == 7: cols.append('oi')
        df = pd.DataFrame(response["candles"], columns=cols)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
        df.set_index('datetime', inplace=True)
        return df
    return None

# --- AUTO EXECUTION TO LEDGER ---
def auto_execute_paper_trade(strategy_name, action, entry_price, dynamic_sl=None):
    file_exists = os.path.isfile('paper_trades.csv')
    with open('paper_trades.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])
        
        if dynamic_sl:
            sl = round(dynamic_sl, 2)
            risk = entry_price - sl if entry_price > sl else entry_price * 0.1
            target = round(entry_price + (risk * 2), 2)
        elif "Ravi Bhatt" in strategy_name:
            sl = round(entry_price * 0.80, 2) 
            target = round(entry_price * 1.50, 2) 
        elif "Chandan Taparia" in strategy_name:
            sl = round(entry_price * 0.85, 2) 
            target = round(entry_price * 1.40, 2) 
        else:
            sl = round(entry_price * 0.7, 2)
            target = round(entry_price * 1.5, 2)
        
        writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "NIFTY", strategy_name, action, entry_price, target, sl, 'OPEN', 0.0, 0.0])

# =====================================================================
# STRATEGY ALGORITHMS
# =====================================================================

def run_oi_premium_flow(fyers):
    try:
        df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
        if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot Data...", None
        atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
        ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
        df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=1), fetch_live_data(fyers, pe_sym, days_back=1)
        
        if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Live Option Chain...", None
        
        ce_px_chg = df_ce['close'].iloc[-1] - df_ce['close'].iloc[-2]
        ce_vol_chg = df_ce['volume'].iloc[-1] - df_ce['volume'].iloc[-2]
        pe_px_chg = df_pe['close'].iloc[-1] - df_pe['close'].iloc[-2]
        pe_vol_chg = df_pe['volume'].iloc[-1] - df_pe['volume'].iloc[-2]

        if ce_px_chg > 0 and ce_vol_chg > 0 and pe_px_chg < 0 and pe_vol_chg > 0: return "BUY CE", df_ce['close'].iloc[-1], f"Long CE Buildup detected on {atm_strike}CE", None
        elif pe_px_chg > 0 and pe_vol_chg > 0 and ce_px_chg < 0 and ce_vol_chg > 0: return "BUY PE", df_pe['close'].iloc[-1], f"Long PE Buildup detected on {atm_strike}PE", None
        return "Wait", 0, f"Neutral Smart Money Flow. ATM: {atm_strike}", None
    except Exception as e: return "Wait", 0, f"API Error: {e}", None

def run_power_scalper_dual(fyers):
    try:
        df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=5)
        if df_spot is None or len(df_spot) < 20: return "Wait", 0, "Insufficient Spot Data", None
        df_daily = df_spot.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        if len(df_daily) < 2: return "Wait", 0, "Building Pivot Data...", None
        
        prev_h, prev_l, prev_c = df_daily['high'].iloc[-2], df_daily['low'].iloc[-2], df_daily['close'].iloc[-2]
        pp = (prev_h + prev_l + prev_c) / 3
        r1, s1 = (2 * pp) - prev_l, (2 * pp) - prev_h
        
        curr_close, prev_close = df_spot['close'].iloc[-1], df_spot['close'].iloc[-2]
        bullish_breakout = (curr_close > pp and prev_close <= pp) or (curr_close > r1 and prev_close <= r1)
        bearish_breakout = (curr_close < pp and prev_close >= pp) or (curr_close < s1 and prev_close >= s1)

        if not (bullish_breakout or bearish_breakout): return "Wait", curr_close, f"Spot ₹{curr_close:.1f}. Waiting for 5m Pivot close.", None

        opt_type = "CE" if bullish_breakout else "PE"
        opt_sym = get_option_symbol(get_atm_strike(curr_close), opt_type)
        df_opt = fetch_live_data(fyers, opt_sym, resolution="2", days_back=2)
        if df_opt is None or len(df_opt) < 15: return "Wait", curr_close, f"Pivot Broken! Fetching 2m {opt_sym}...", None
        
        df_opt.ta.supertrend(length=10, multiplier=3.0, append=True)
        df_opt.ta.rsi(length=14, append=True)
        st_dir_col, st_val_col = [c for c in df_opt.columns if 'SUPERTd' in c][0], [c for c in df_opt.columns if 'SUPERT_' in c][0]
        
        opt_rsi = df_opt['RSI_14'].iloc[-1]
        if df_opt[st_dir_col].iloc[-1] == 1 and opt_rsi >= 60:
            return f"BUY {opt_type}", df_opt['close'].iloc[-1], f"🔥 Gamma Blast! 2m RSI={opt_rsi:.1f}", df_opt[st_val_col].iloc[-1]
        return "Wait", curr_close, f"Breakout confirmed, but 2m RSI is {opt_rsi:.1f} (Needs >60).", None
    except Exception as e: return "Wait", 0, f"API Error: {e}", None

def run_ravi_bhatt_oi(fyers):
    try:
        df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
        if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot...", None
        atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
        ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
        df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=2), fetch_live_data(fyers, pe_sym, days_back=2)
        if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Insti OI...", None

        if 'oi' not in df_ce.columns: df_ce['oi'] = df_ce['volume'].cumsum()
        if 'oi' not in df_pe.columns: df_pe['oi'] = df_pe['volume'].cumsum()

        base_oi_ce, base_oi_pe = max(df_ce['oi'].iloc[0], 1), max(df_pe['oi'].iloc[0], 1)
        curr_oi_ce, curr_oi_pe = df_ce['oi'].iloc[-1], df_pe['oi'].iloc[-1]
        
        ce_oi_change, pe_oi_change = ((curr_oi_ce - base_oi_ce) / base_oi_ce) * 100, ((curr_oi_pe - base_oi_pe) / base_oi_pe) * 100
        
        if pe_oi_change >= 500: return "BUY CE", df_ce['close'].iloc[-1], f"🔥 PE OI Spiked {pe_oi_change:.1f}%! Insti Put Selling.", None
        elif ce_oi_change >= 500: return "BUY PE", df_pe['close'].iloc[-1], f"🔥 CE OI Spiked {ce_oi_change:.1f}%! Insti Call Selling.", None
        return "Wait", df_spot['close'].iloc[-1], f"OI Tracker: CE +{ce_oi_change:.0f}% | PE +{pe_oi_change:.0f}%", None
    except Exception as e: return "Wait", 0, f"API Error: {e}", None

def run_power_scalper_spot_burst(fyers):
    try:
        df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=3)
        if df_spot is None or len(df_spot) < 20: return "Wait", 0, "Fetching Spot...", None
        df_spot.ta.supertrend(length=10, multiplier=3.0, append=True)
        df_spot.ta.rsi(length=14, append=True)
        st_cols = [c for c in df_spot.columns if 'SUPERTd' in c]
        if not st_cols: return "Wait", 0, "Calculating...", None
        
        curr_st = df_spot[st_cols[0]].iloc[-1]
        curr_rsi, prev_rsi = df_spot['RSI_14'].iloc[-1], df_spot['RSI_14'].iloc[-2]
        curr_spot = df_spot['close'].iloc[-1]

        signal, opt_type = "Wait", ""
        if curr_st == 1 and curr_rsi >= 60 and prev_rsi < 60: signal, opt_type = "BUY CE", "CE"
        elif curr_st == -1 and curr_rsi <= 40 and prev_rsi > 40: signal, opt_type = "BUY PE", "PE"

        if signal != "Wait":
            opt_sym = get_option_symbol(get_atm_strike(curr_spot), opt_type)
            df_opt = fetch_live_data(fyers, opt_sym, resolution="5", days_back=1)
            if df_opt is not None and not df_opt.empty: return signal, df_opt['close'].iloc[-1], f"⚡ Spot RSI Burst ({curr_rsi:.1f})!", None
        return "Wait", curr_spot, f"Spot ST: {'Bull' if curr_st==1 else 'Bear'} | RSI: {curr_rsi:.1f} (Needs cross)", None
    except Exception as e: return "Wait", 0, f"API Error: {e}", None

def run_chandan_taparia_parity(fyers):
    try:
        df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="15", days_back=3)
        if df_spot is None or len(df_spot) < 15: return "Wait", 0, "Fetching Trend...", None
        df_spot.ta.ema(length=20, append=True)
        curr_spot = df_spot['close'].iloc[-1]
        spot_trend = 1 if curr_spot > df_spot['EMA_20'].iloc[-1] else -1
        
        atm_strike = get_atm_strike(curr_spot)
        
        if spot_trend == 1:
            pe_sym = get_option_symbol(atm_strike, "PE")
            df_pe = fetch_live_data(fyers, pe_sym, resolution="15", days_back=4)
            if df_pe is None or len(df_pe) < 10: return "Wait", curr_spot, "Fetching Put Support...", None
            pe_support = df_pe['low'].rolling(window=20).min().iloc[-2] 
            curr_pe_ltp = df_pe['close'].iloc[-1]
            if curr_pe_ltp < pe_support:
                ce_sym = get_option_symbol(atm_strike, "CE")
                df_ce = fetch_live_data(fyers, ce_sym, resolution="5", days_back=1)
                if df_ce is not None and not df_ce.empty: return "BUY CE", df_ce['close'].iloc[-1], f"🚀 Put Crashed Support {pe_support:.1f}", None
            return "Wait", curr_spot, f"Bullish Bias. Waiting Put to crash < {pe_support:.1f}", None
        else:
            ce_sym = get_option_symbol(atm_strike, "CE")
            df_ce = fetch_live_data(fyers, ce_sym, resolution="15", days_back=4)
            if df_ce is None or len(df_ce) < 10: return "Wait", curr_spot, "Fetching Call Support...", None
            ce_support = df_ce['low'].rolling(window=20).min().iloc[-2]
            curr_ce_ltp = df_ce['close'].iloc[-1]
            if curr_ce_ltp < ce_support:
                pe_sym = get_option_symbol(atm_strike, "PE")
                df_pe = fetch_live_data(fyers, pe_sym, resolution="5", days_back=1)
                if df_pe is not None and not df_pe.empty: return "BUY PE", df_pe['close'].iloc[-1], f"📉 Call Crashed Support {ce_support:.1f}", None
            return "Wait", curr_spot, f"Bearish Bias. Waiting Call to crash < {ce_support:.1f}", None
    except Exception as e: return "Wait", 0, f"API Error: {e}", None


# --- UI RENDERER ---
def render_ui(fyers):
    # Initialize Memory for Cooldowns and Last Actions
    keys = ['last_oi_trade', 'last_scalper_dual_trade', 'last_ravi_trade', 'last_spot_burst_trade', 'last_chandan_trade']
    for key in keys:
        if key not in st.session_state: 
            st.session_state[key] = {"time": None, "action": "None"}

    st.markdown("### 🤖 Fully Automated Forward Testing Engine")
    st.write("Penta-Core Engine silently runs multi-timeframe algos in the background and auto-executes into your Ledger.")
    
    head_col1, head_col2 = st.columns([3, 1])
    with head_col1: st.info("Cooldown Timer: Max 1 trade per strategy every 30 minutes to prevent noise trading.")
    with head_col2:
        engine_on = st.toggle("🟢 Master Engine ON", value=False)
        if engine_on:
            st_autorefresh(interval=3 * 60 * 1000, key="fw_test_refresh")
            st.success("Auto-Pilot Running in Background")

    st.markdown("---")
    
    def generate_card_html(title, desc, status_color, msg, last_action):
        # This HTML contains the new "Live Terminal" block
        return f"""
        <div style="background-color: #1a1c23; border: 1px solid #2d303e; border-top: 5px solid {status_color}; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 20px; height: 100%;">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">{title}</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px; height: 35px;">{desc}</p>
            <div style="margin-top: 10px; margin-bottom: 15px;">
                <span style="background-color: #2b313c; color: {status_color}; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">Status: {'SCANNING' if engine_on else 'OFFLINE'}</span>
            </div>
            <div style="background-color: #000; border-radius: 5px; padding: 10px; border: 1px solid #333; font-family: 'Courier New', Courier, monospace; font-size: 12px;">
                <div style="color: #0f0; margin-bottom: 5px;">> {msg}</div>
                <div style="color: #ff9800;">> Last Exec: {last_action}</div>
            </div>
        </div>
        """

    # --- EXECUTE LOGIC FIRST (So HTML renders with updated text) ---
    msg_oi, msg_dual, msg_ravi, msg_burst, msg_chan = ["Engine Offline"] * 5
    
    if engine_on:
        now = datetime.datetime.now()
        
        # 1. Premium Flow
        sig, data, msg_oi, sl = run_oi_premium_flow(fyers)
        if "BUY" in sig and (not st.session_state['last_oi_trade']['time'] or (now - st.session_state['last_oi_trade']['time']).total_seconds() / 60 > 30):
            auto_execute_paper_trade("OI Premium Flow", sig, data)
            st.session_state['last_oi_trade'] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
            st.toast(f"🤖 OI Algo Executed: {sig}")

        # 2. Power Scalper Dual
        sig, data, msg_dual, sl = run_power_scalper_dual(fyers)
        if "BUY" in sig and (not st.session_state['last_scalper_dual_trade']['time'] or (now - st.session_state['last_scalper_dual_trade']['time']).total_seconds() / 60 > 30):
            auto_execute_paper_trade("Power Scalper Dual", sig, data, sl)
            st.session_state['last_scalper_dual_trade'] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
            st.toast(f"⚡ Scalper Executed: {sig}")

        # 3. Ravi Bhatt
        sig, data, msg_ravi, sl = run_ravi_bhatt_oi(fyers)
        if "BUY" in sig and (not st.session_state['last_ravi_trade']['time'] or (now - st.session_state['last_ravi_trade']['time']).total_seconds() / 60 > 30):
            auto_execute_paper_trade("Ravi Bhatt 500% OI", sig, data)
            st.session_state['last_ravi_trade'] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
            st.toast(f"📊 Ravi Bhatt Executed: {sig}")

        # 4. Spot Burst
        sig, data, msg_burst, sl = run_power_scalper_spot_burst(fyers)
        if "BUY" in sig and (not st.session_state['last_spot_burst_trade']['time'] or (now - st.session_state['last_spot_burst_trade']['time']).total_seconds() / 60 > 30):
            auto_execute_paper_trade("Spot Burst", sig, data)
            st.session_state['last_spot_burst_trade'] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
            st.toast(f"🚀 Spot Burst Executed: {sig}")

        # 5. Chandan Taparia
        sig, data, msg_chan, sl = run_chandan_taparia_parity(fyers)
        if "BUY" in sig and (not st.session_state['last_chandan_trade']['time'] or (now - st.session_state['last_chandan_trade']['time']).total_seconds() / 60 > 30):
            auto_execute_paper_trade("Chandan Taparia Parity", sig, data)
            st.session_state['last_chandan_trade'] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
            st.toast(f"🧠 Taparia Executed: {sig}")

    # --- RENDER ROW 1 ---
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1: st.markdown(generate_card_html("Nifty Premium Flow", "Divergence between CE and PE smart money writing.", "#1f77b4", msg_oi, st.session_state['last_oi_trade']['action']), unsafe_allow_html=True)
    with r1c2: st.markdown(generate_card_html("Power Scalper (Dual)", "Spot 5m Pivot breakouts + Option 2m SuperTrend/RSI.", "#ff9800", msg_dual, st.session_state['last_scalper_dual_trade']['action']), unsafe_allow_html=True)
    with r1c3: st.markdown(generate_card_html("Ravi Bhatt OI Spike", "Triggers when Change in Option OI exceeds 500%.", "#e91e63", msg_ravi, st.session_state['last_ravi_trade']['action']), unsafe_allow_html=True)

    # --- RENDER ROW 2 ---
    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1: st.markdown(generate_card_html("Power Scalper (Spot)", "Spot SuperTrend + RSI violently crossing 60/40.", "#00bcd4", msg_burst, st.session_state['last_spot_burst_trade']['action']), unsafe_allow_html=True)
    with r2c2: st.markdown(generate_card_html("Chandan Taparia Parity", "Buys Call ONLY when the Opposite Put crashes below support.", "#8e24aa", msg_chan, st.session_state['last_chandan_trade']['action']), unsafe_allow_html=True)
    with r2c3: 
        st.markdown(generate_card_html("[Empty Slot 6]", "Your next million-dollar strategy goes here.", "#404654", "System Idle", "None"), unsafe_allow_html=True)
