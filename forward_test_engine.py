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
    
    try:
        response = fyers.history(data=data)
        time.sleep(0.3) # 🛡️ Anti-Crash Delay
        if response and response.get("s") == "ok" and response.get("candles"):
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if len(response["candles"][0]) == 7: cols.append('oi')
            df = pd.DataFrame(response["candles"], columns=cols)
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
            df.set_index('datetime', inplace=True)
            return df
    except Exception as e: pass
    return None

# --- AUTO EXECUTION TO LEDGER ---
def auto_execute_paper_trade(strategy_name, action, entry_price, dynamic_sl=None):
    file_exists = os.path.isfile('paper_trades.csv')
    with open('paper_trades.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])
        
        if "SMC Pro" in strategy_name and dynamic_sl:
            sl = round(dynamic_sl, 2)
            risk = entry_price - sl if entry_price > sl else entry_price * 0.1
            target = round(entry_price + (risk * 2.5), 2) # SMC 1:2.5 High RR Target
        elif dynamic_sl:
            sl = round(dynamic_sl, 2)
            risk = entry_price - sl if entry_price > sl else entry_price * 0.1
            target = round(entry_price + (risk * 1.5), 2)
        elif "Ravi Bhatt" in strategy_name:
            sl = round(entry_price * 0.80, 2) ; target = round(entry_price * 1.50, 2) 
        elif "Chandan Taparia" in strategy_name:
            sl = round(entry_price * 0.85, 2) ; target = round(entry_price * 1.40, 2) 
        else:
            sl = round(entry_price * 0.7, 2); target = round(entry_price * 1.5, 2)
        
        writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "NIFTY", strategy_name, action, entry_price, target, sl, 'OPEN', 0.0, 0.0])

# =====================================================================
# STRATEGY 1 TO 5 (Keeping it short for brevity, exact same as before)
# =====================================================================
def run_oi_premium_flow(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
    if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot...", None
    atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
    ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
    df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=1), fetch_live_data(fyers, pe_sym, days_back=1)
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Options...", None
    ce_px_chg, ce_vol_chg = df_ce['close'].iloc[-1] - df_ce['close'].iloc[-2], df_ce['volume'].iloc[-1] - df_ce['volume'].iloc[-2]
    pe_px_chg, pe_vol_chg = df_pe['close'].iloc[-1] - df_pe['close'].iloc[-2], df_pe['volume'].iloc[-1] - df_pe['volume'].iloc[-2]
    if ce_px_chg > 0 and ce_vol_chg > 0 and pe_px_chg < 0 and pe_vol_chg > 0: return "BUY CE", df_ce['close'].iloc[-1], "Long CE Buildup", None
    elif pe_px_chg > 0 and pe_vol_chg > 0 and ce_px_chg < 0 and ce_vol_chg > 0: return "BUY PE", df_pe['close'].iloc[-1], "Long PE Buildup", None
    return "Wait", 0, f"Neutral Smart Money Flow. ATM: {atm_strike}", None

def run_power_scalper_dual(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=5)
    if df_spot is None or len(df_spot) < 20: return "Wait", 0, "Insufficient Spot", None
    df_daily = df_spot.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    if len(df_daily) < 2: return "Wait", 0, "Building Pivots...", None
    pp = (df_daily['high'].iloc[-2] + df_daily['low'].iloc[-2] + df_daily['close'].iloc[-2]) / 3
    r1, s1 = (2 * pp) - df_daily['low'].iloc[-2], (2 * pp) - df_daily['high'].iloc[-2]
    curr_c, prev_c = df_spot['close'].iloc[-1], df_spot['close'].iloc[-2]
    if sensitivity == "Demo/Aggressive": bull_brk, bear_brk = curr_c > pp, curr_c < pp
    else: bull_brk, bear_brk = (curr_c > pp and prev_c <= pp) or (curr_c > r1 and prev_c <= r1), (curr_c < pp and prev_c >= pp) or (curr_c < s1 and prev_c >= s1)
    if not (bull_brk or bear_brk): return "Wait", curr_c, f"Spot ₹{curr_c:.1f}. Waiting for Pivot.", None
    opt_type = "CE" if bull_brk else "PE"
    opt_sym = get_option_symbol(get_atm_strike(curr_c), opt_type)
    df_opt = fetch_live_data(fyers, opt_sym, resolution="2", days_back=2)
    if df_opt is None or len(df_opt) < 15: return "Wait", curr_c, f"Fetching 2m {opt_sym}...", None
    df_opt.ta.supertrend(length=10, multiplier=3.0, append=True); df_opt.ta.rsi(length=14, append=True)
    st_dir_col, st_val_col = [c for c in df_opt.columns if 'SUPERTd' in c][0], [c for c in df_opt.columns if 'SUPERT_' in c][0]
    opt_rsi, req_rsi = df_opt['RSI_14'].iloc[-1], 50 if sensitivity == "Demo/Aggressive" else 60
    if df_opt[st_dir_col].iloc[-1] == 1 and opt_rsi >= req_rsi: return f"BUY {opt_type}", df_opt['close'].iloc[-1], f"🔥 Gamma Blast! RSI={opt_rsi:.1f}", df_opt[st_val_col].iloc[-1]
    return "Wait", curr_c, f"Option RSI is {opt_rsi:.1f} (Needs >{req_rsi}).", None

def run_ravi_bhatt_oi(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
    if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot...", None
    atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
    ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
    df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=2), fetch_live_data(fyers, pe_sym, days_back=2)
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Insti OI...", None
    if 'oi' not in df_ce.columns: df_ce['oi'] = df_ce['volume'].cumsum()
    if 'oi' not in df_pe.columns: df_pe['oi'] = df_pe['volume'].cumsum()
    ce_oi_change = ((df_ce['oi'].iloc[-1] - max(df_ce['oi'].iloc[0], 1)) / max(df_ce['oi'].iloc[0], 1)) * 100
    pe_oi_change = ((df_pe['oi'].iloc[-1] - max(df_pe['oi'].iloc[0], 1)) / max(df_pe['oi'].iloc[0], 1)) * 100
    threshold = 20 if sensitivity == "Demo/Aggressive" else 500
    if pe_oi_change >= threshold: return "BUY CE", df_ce['close'].iloc[-1], f"🔥 PE OI Spiked {pe_oi_change:.1f}%!", None
    elif ce_oi_change >= threshold: return "BUY PE", df_pe['close'].iloc[-1], f"🔥 CE OI Spiked {ce_oi_change:.1f}%!", None
    return "Wait", df_spot['close'].iloc[-1], f"OI Tracker: CE +{ce_oi_change:.0f}% | PE +{pe_oi_change:.0f}%", None

def run_power_scalper_spot_burst(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=3)
    if df_spot is None or len(df_spot) < 20: return "Wait", 0, "Fetching Spot...", None
    df_spot.ta.supertrend(length=10, multiplier=3.0, append=True); df_spot.ta.rsi(length=14, append=True)
    st_cols = [c for c in df_spot.columns if 'SUPERTd' in c]
    if not st_cols: return "Wait", 0, "Calculating...", None
    curr_st, curr_rsi, prev_rsi, curr_spot = df_spot[st_cols[0]].iloc[-1], df_spot['RSI_14'].iloc[-1], df_spot['RSI_14'].iloc[-2], df_spot['close'].iloc[-1]
    req_rsi = 50 if sensitivity == "Demo/Aggressive" else 60
    signal, opt_type = "Wait", ""
    if curr_st == 1 and curr_rsi >= req_rsi: signal, opt_type = "BUY CE", "CE"
    elif curr_st == -1 and curr_rsi <= (100 - req_rsi): signal, opt_type = "BUY PE", "PE"
    if signal != "Wait":
        df_opt = fetch_live_data(fyers, get_option_symbol(get_atm_strike(curr_spot), opt_type), resolution="5", days_back=1)
        if df_opt is not None and not df_opt.empty: return signal, df_opt['close'].iloc[-1], f"⚡ Spot RSI Burst ({curr_rsi:.1f})!", None
    return "Wait", curr_spot, f"ST: {'Bull' if curr_st==1 else 'Bear'} | RSI: {curr_rsi:.1f} (Needs {req_rsi})", None

def run_chandan_taparia_parity(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="15", days_back=3)
    if df_spot is None or len(df_spot) < 15: return "Wait", 0, "Fetching Trend...", None
    df_spot.ta.ema(length=20, append=True)
    curr_spot = df_spot['close'].iloc[-1]
    spot_trend = 1 if curr_spot > df_spot['EMA_20'].iloc[-1] else -1
    atm_strike, lookback = get_atm_strike(curr_spot), 5 if sensitivity == "Demo/Aggressive" else 20
    
    if spot_trend == 1:
        df_pe = fetch_live_data(fyers, get_option_symbol(atm_strike, "PE"), resolution="15", days_back=4)
        if df_pe is None or len(df_pe) < 10: return "Wait", curr_spot, "Fetching Put Support...", None
        pe_support = df_pe['low'].rolling(window=lookback).min().iloc[-2] 
        if df_pe['close'].iloc[-1] < pe_support:
            df_ce = fetch_live_data(fyers, get_option_symbol(atm_strike, "CE"), resolution="5", days_back=1)
            if df_ce is not None and not df_ce.empty: return "BUY CE", df_ce['close'].iloc[-1], f"🚀 Put Crashed Support {pe_support:.1f}", None
        return "Wait", curr_spot, f"Waiting Put < {pe_support:.1f}", None
    else:
        df_ce = fetch_live_data(fyers, get_option_symbol(atm_strike, "CE"), resolution="15", days_back=4)
        if df_ce is None or len(df_ce) < 10: return "Wait", curr_spot, "Fetching Call Support...", None
        ce_support = df_ce['low'].rolling(window=lookback).min().iloc[-2]
        if df_ce['close'].iloc[-1] < ce_support:
            df_pe = fetch_live_data(fyers, get_option_symbol(atm_strike, "PE"), resolution="5", days_back=1)
            if df_pe is not None and not df_pe.empty: return "BUY PE", df_pe['close'].iloc[-1], f"📉 Call Crashed Support {ce_support:.1f}", None
        return "Wait", curr_spot, f"Waiting Call < {ce_support:.1f}", None

# =====================================================================
# STRATEGY 6: ADAPTIVE SMC PRO (FVG + ORDER BLOCK MITIGATION) 🔥
# =====================================================================
def run_live_smc_pro(fyers, sensitivity):
    try:
        # Fetch 6 days of 5-min data to accurately compute 200 EMA and look back for Order Blocks
        df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=6)
        if df_spot is None or len(df_spot) < 205: return "Wait", 0, "Warming up EMA 200 Math...", None

        df_spot.ta.atr(length=14, append=True)
        df_spot.ta.ema(length=200, append=True)
        df_spot.dropna(inplace=True)

        active_bull_ob = None
        active_bear_ob = None
        
        # 1. SCAN HISTORY FOR UNMITIGATED ORDER BLOCKS (Exclude current live candle)
        history_df = df_spot.iloc[:-1] 
        
        for i in range(2, len(history_df)):
            row = history_df.iloc[i]
            prev1 = history_df.iloc[i-1]
            prev2 = history_df.iloc[i-2]
            
            atr, ema200 = row['ATRr_14'], row['EMA_200']
            
            gap_bull, gap_bear = row['low'] - prev2['high'], prev2['low'] - row['high']
            req_gap = atr * 0.05 if sensitivity == "Demo/Aggressive" else atr * 0.1
            
            fvg_bull = gap_bull > req_gap and row['close'] > ema200
            fvg_bear = gap_bear > req_gap and row['close'] < ema200
            
            # Map the block
            if fvg_bull:
                ob_high, ob_low = prev2['high'], prev2['low']
                for j in range(i-2, max(-1, i-10), -1):
                    if history_df.iloc[j]['close'] < history_df.iloc[j]['open']:
                        ob_high, ob_low = history_df.iloc[j]['high'], history_df.iloc[j]['low']
                        break
                active_bull_ob = {'high': ob_high, 'low': ob_low, 'atr': atr}
                active_bear_ob = None
            elif fvg_bear:
                ob_high, ob_low = prev2['high'], prev2['low']
                for j in range(i-2, max(-1, i-10), -1):
                    if history_df.iloc[j]['close'] > history_df.iloc[j]['open']:
                        ob_high, ob_low = history_df.iloc[j]['high'], history_df.iloc[j]['low']
                        break
                active_bear_ob = {'high': ob_high, 'low': ob_low, 'atr': atr}
                active_bull_ob = None
                
            # If price later mitigated this zone in history, invalidate it!
            if active_bull_ob and row['low'] <= active_bull_ob['high']: active_bull_ob = None
            if active_bear_ob and row['high'] >= active_bear_ob['low']: active_bear_ob = None

        # 2. CHECK THE LIVE CANDLE FOR SNIPER ENTRY
        live_candle = df_spot.iloc[-1]
        live_price = live_candle['close']
        atm_strike = get_atm_strike(live_price)
        
        # Bullish Mitigation
        if active_bull_ob and live_candle['low'] <= active_bull_ob['high']:
            ce_sym = get_option_symbol(atm_strike, "CE")
            df_opt = fetch_live_data(fyers, ce_sym, resolution="5", days_back=1)
            if df_opt is not None and not df_opt.empty:
                sl = active_bull_ob['low'] - (active_bull_ob['atr'] * 0.3)
                return "BUY CE", df_opt['close'].iloc[-1], f"🧠 Bull OB Mitigated! Spot Entry: {active_bull_ob['high']:.1f}", sl
                
        # Bearish Mitigation
        elif active_bear_ob and live_candle['high'] >= active_bear_ob['low']:
            pe_sym = get_option_symbol(atm_strike, "PE")
            df_opt = fetch_live_data(fyers, pe_sym, resolution="5", days_back=1)
            if df_opt is not None and not df_opt.empty:
                sl = active_bear_ob['high'] + (active_bear_ob['atr'] * 0.3)
                return "BUY PE", df_opt['close'].iloc[-1], f"🧠 Bear OB Mitigated! Spot Entry: {active_bear_ob['low']:.1f}", sl
                
        # 3. NO ENTRY, JUST STATUS UPDATES
        status_msg = f"Spot: ₹{live_price:.1f}. "
        if active_bull_ob: status_msg += f"Waiting for Bull OB dip to {active_bull_ob['high']:.1f}"
        elif active_bear_ob: status_msg += f"Waiting for Bear OB rally to {active_bear_ob['low']:.1f}"
        else: status_msg += "Scanning for new Valid FVG/OB structures..."
        
        return "Wait", live_price, status_msg, None
        
    except Exception as e:
        return "Wait", 0, f"Error: Retrying...", None


# --- UI RENDERER ---
def render_ui(fyers):
    # Initialize Memory
    keys = ['last_oi_trade', 'last_scalper_dual_trade', 'last_ravi_trade', 'last_spot_burst_trade', 'last_chandan_trade', 'last_smc_trade']
    for key in keys:
        if key not in st.session_state: st.session_state[key] = {"time": None, "action": "None"}

    st.markdown("### 🤖 Fully Automated Forward Testing Engine")
    
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([1, 1, 1])
    with col_ctrl1: sensitivity = st.select_slider("⚙️ Algo Sensitivity", options=["Strict (Insti)", "Moderate", "Demo/Aggressive"], value="Strict (Insti)")
    with col_ctrl2: cooldown_mins = st.number_input("⏱️ Trade Cooldown (Mins)", min_value=1, value=30, step=5)
    with col_ctrl3:
        st.write("")
        engine_on = st.toggle("🟢 MASTER ENGINE ON", value=False)
        if engine_on: st_autorefresh(interval=60 * 1000, key="fw_test_refresh"); st.success("Hexa-Core Active (1 Min Scan)")

    st.markdown("---")
    
    def generate_card_html(title, desc, status_color, msg, last_action):
        return f"""
        <div style="background-color: #1a1c23; border: 1px solid #2d303e; border-top: 5px solid {status_color}; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 20px; height: 100%;">
            <h3 style="margin: 0; color: #fff; font-size: 16px;">{title}</h3>
            <p style="color: #888; font-size: 11px; margin-top: 5px; height: 30px;">{desc}</p>
            <div style="margin-top: 5px; margin-bottom: 10px;">
                <span style="background-color: #2b313c; color: {status_color}; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;">Status: {'SCANNING' if engine_on else 'OFFLINE'}</span>
            </div>
            <div style="background-color: #000; border-radius: 5px; padding: 10px; border: 1px solid #333; font-family: 'Courier New', Courier, monospace; font-size: 11px;">
                <div style="color: #0f0; margin-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">> {msg}</div>
                <div style="color: #ff9800;">> Exec: {last_action}</div>
            </div>
        </div>
        """

    msg_oi = msg_dual = msg_ravi = msg_burst = msg_chan = msg_smc = "Offline"
    
    if engine_on:
        now = datetime.datetime.now()
        
        # Helper for executing and logging
        def process_trade(run_func, strat_name, key):
            sig, data, msg, sl = run_func(fyers, sensitivity)
            if "BUY" in sig and (not st.session_state[key]['time'] or (now - st.session_state[key]['time']).total_seconds() / 60 > cooldown_mins):
                auto_execute_paper_trade(strat_name, sig, data, sl)
                st.session_state[key] = {"time": now, "action": f"{sig} @ ₹{data} ({now.strftime('%H:%M')})"}
                st.toast(f"🤖 {strat_name} Executed: {sig}")
            return msg

        msg_oi = process_trade(run_oi_premium_flow, "OI Premium Flow", 'last_oi_trade')
        msg_dual = process_trade(run_power_scalper_dual, "Power Scalper Dual", 'last_scalper_dual_trade')
        msg_ravi = process_trade(run_ravi_bhatt_oi, "Ravi Bhatt 500% OI", 'last_ravi_trade')
        msg_burst = process_trade(run_power_scalper_spot_burst, "Spot Burst", 'last_spot_burst_trade')
        msg_chan = process_trade(run_chandan_taparia_parity, "Chandan Taparia Parity", 'last_chandan_trade')
        msg_smc = process_trade(run_live_smc_pro, "Adaptive SMC Pro", 'last_smc_trade') # THE NEW ENGINE

    # --- UI GRID (3x2) ---
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1: st.markdown(generate_card_html("Nifty Premium Flow", "Divergence between CE and PE writing.", "#1f77b4", msg_oi, st.session_state['last_oi_trade']['action']), unsafe_allow_html=True)
    with r1c2: st.markdown(generate_card_html("Power Scalper (Dual)", "Spot 5m Pivot + Option 2m ST/RSI.", "#ff9800", msg_dual, st.session_state['last_scalper_dual_trade']['action']), unsafe_allow_html=True)
    with r1c3: st.markdown(generate_card_html("Ravi Bhatt OI Spike", "Triggers when Option OI exceeds 500%.", "#e91e63", msg_ravi, st.session_state['last_ravi_trade']['action']), unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1: st.markdown(generate_card_html("Power Scalper (Spot)", "Spot SuperTrend + RSI crossing 60/40.", "#00bcd4", msg_burst, st.session_state['last_spot_burst_trade']['action']), unsafe_allow_html=True)
    with r2c2: st.markdown(generate_card_html("Chandan Taparia Parity", "Buys Call ONLY when Opposite Put crashes.", "#8e24aa", msg_chan, st.session_state['last_chandan_trade']['action']), unsafe_allow_html=True)
    with r2c3: st.markdown(generate_card_html("Adaptive SMC Pro 🧠", "Scans Unmitigated FVG & Order Blocks.", "#ffeb3b", msg_smc, st.session_state['last_smc_trade']['action']), unsafe_allow_html=True) # THE GOLDEN CARD
