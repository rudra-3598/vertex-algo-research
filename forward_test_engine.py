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
        else:
            sl = round(entry_price * 0.7, 2)
            target = round(entry_price * 1.5, 2)
        
        writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "NIFTY", strategy_name, action, entry_price, target, sl, 'OPEN', 0.0, 0.0])

# =====================================================================
# STRATEGY 1: NIFTY OI PREMIUM FLOW
# =====================================================================
def run_oi_premium_flow(fyers):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
    if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot...", None
    
    atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
    ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
    df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=1), fetch_live_data(fyers, pe_sym, days_back=1)
    
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Options...", None

    ce_px_chg, ce_vol_chg = df_ce['close'].iloc[-1] - df_ce['close'].iloc[-2], df_ce['volume'].iloc[-1] - df_ce['volume'].iloc[-2]
    pe_px_chg, pe_vol_chg = df_pe['close'].iloc[-1] - df_pe['close'].iloc[-2], df_pe['volume'].iloc[-1] - df_pe['volume'].iloc[-2]

    if ce_px_chg > 0 and ce_vol_chg > 0 and pe_px_chg < 0 and pe_vol_chg > 0: return "BUY CE", df_ce['close'].iloc[-1], "OI Breakout (CE Builtup)", None
    elif pe_px_chg > 0 and pe_vol_chg > 0 and ce_px_chg < 0 and ce_vol_chg > 0: return "BUY PE", df_pe['close'].iloc[-1], "OI Breakout (PE Builtup)", None
    return "Wait", 0, f"Neutral Flow. ATM: {atm_strike}", None

# =====================================================================
# STRATEGY 2: POWER SCALPER (Dual-Timeframe Pivot + ST/RSI)
# =====================================================================
def run_power_scalper_dual(fyers):
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

    if not (bullish_breakout or bearish_breakout): return "Wait", curr_close, f"Spot ₹{curr_close:.1f}. Waiting for 5-Min Pivot close.", None

    opt_type = "CE" if bullish_breakout else "PE"
    opt_sym = get_option_symbol(get_atm_strike(curr_close), opt_type)
    df_opt = fetch_live_data(fyers, opt_sym, resolution="2", days_back=2)
    if df_opt is None or len(df_opt) < 15: return "Wait", curr_close, f"Spot Breakout! Fetching {opt_sym}...", None

    df_opt.ta.supertrend(length=10, multiplier=3.0, append=True)
    df_opt.ta.rsi(length=14, append=True)
    
    st_dir_col, st_val_col = [c for c in df_opt.columns if 'SUPERTd' in c][0], [c for c in df_opt.columns if 'SUPERT_' in c][0]
    if df_opt[st_dir_col].iloc[-1] == 1 and df_opt['RSI_14'].iloc[-1] >= 60:
        return f"BUY {opt_type}", df_opt['close'].iloc[-1], f"🔥 Pivot + 2m RSI. BUY {opt_sym}!", df_opt[st_val_col].iloc[-1]

    return "Wait", curr_close, f"Breakout Confirmed, but {opt_sym} missing 2m RSI/ST Momentum.", None

# =====================================================================
# STRATEGY 3: RAVI BHATT 500% OI SPIKE
# =====================================================================
def run_ravi_bhatt_oi(fyers):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
    if df_spot is None or len(df_spot) < 2: return "Wait", 0, "Fetching Spot...", None
    
    current_spot = df_spot['close'].iloc[-1]
    atm_strike = get_atm_strike(current_spot)
    ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
    df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=2), fetch_live_data(fyers, pe_sym, days_back=2)
    
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", 0, "Fetching Insti OI Data...", None

    if 'oi' not in df_ce.columns: df_ce['oi'] = df_ce['volume'].cumsum()
    if 'oi' not in df_pe.columns: df_pe['oi'] = df_pe['volume'].cumsum()

    base_oi_ce, base_oi_pe = max(df_ce['oi'].iloc[0], 1), max(df_pe['oi'].iloc[0], 1)
    curr_oi_ce, curr_oi_pe = df_ce['oi'].iloc[-1], df_pe['oi'].iloc[-1]
    
    ce_oi_change, pe_oi_change = ((curr_oi_ce - base_oi_ce) / base_oi_ce) * 100, ((curr_oi_pe - base_oi_pe) / base_oi_pe) * 100
    
    if pe_oi_change >= 500: return "BUY CE", df_ce['close'].iloc[-1], f"🔥 PE OI Spiked {pe_oi_change:.1f}%! Insti Put Selling.", None
    elif ce_oi_change >= 500: return "BUY PE", df_pe['close'].iloc[-1], f"🔥 CE OI Spiked {ce_oi_change:.1f}%! Insti Call Selling.", None

    return "Wait", current_spot, f"Tracking OI Change: CE +{ce_oi_change:.0f}% | PE +{pe_oi_change:.0f}%", None

# =====================================================================
# STRATEGY 4: POWER SCALPER (Spot ST + RSI Momentum Burst)
# =====================================================================
def run_power_scalper_spot_burst(fyers):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=3)
    if df_spot is None or len(df_spot) < 20: return "Wait", 0, "Insufficient Spot Data", None

    df_spot.ta.supertrend(length=10, multiplier=3.0, append=True)
    df_spot.ta.rsi(length=14, append=True)
    
    st_cols = [c for c in df_spot.columns if 'SUPERTd' in c]
    if not st_cols: return "Wait", 0, "Calculating Indicators...", None
    st_dir_col = st_cols[0]

    curr_st = df_spot[st_dir_col].iloc[-1]
    curr_rsi, prev_rsi = df_spot['RSI_14'].iloc[-1], df_spot['RSI_14'].iloc[-2]
    curr_spot = df_spot['close'].iloc[-1]

    signal, opt_type = "Wait", ""
    if curr_st == 1 and curr_rsi >= 60 and prev_rsi < 60: signal, opt_type = "BUY CE", "CE"
    elif curr_st == -1 and curr_rsi <= 40 and prev_rsi > 40: signal, opt_type = "BUY PE", "PE"

    if signal != "Wait":
        atm_strike = get_atm_strike(curr_spot)
        opt_sym = get_option_symbol(atm_strike, opt_type)
        df_opt = fetch_live_data(fyers, opt_sym, resolution="5", days_back=1)
        if df_opt is not None and not df_opt.empty:
            return signal, df_opt['close'].iloc[-1], f"⚡ Spot RSI Burst ({curr_rsi:.1f})! Buying ATM {opt_type}.", None

    return "Wait", curr_spot, f"Spot ST: {'Bull' if curr_st==1 else 'Bear'} | RSI: {curr_rsi:.1f} (Need 60X)", None

# --- UI RENDERER ---
def render_ui(fyers):
    if 'last_oi_trade_time' not in st.session_state: st.session_state['last_oi_trade_time'] = None
    if 'last_scalper_dual_trade_time' not in st.session_state: st.session_state['last_scalper_dual_trade_time'] = None
    if 'last_ravi_trade_time' not in st.session_state: st.session_state['last_ravi_trade_time'] = None
    if 'last_spot_burst_trade_time' not in st.session_state: st.session_state['last_spot_burst_trade_time'] = None

    st.markdown("### 🤖 Fully Automated Forward Testing Engine")
    st.write("Quad-Core Engine silently runs multi-timeframe algos in the background and auto-executes into your Ledger.")
    
    head_col1, head_col2 = st.columns([3, 1])
    with head_col1: st.info("Cooldown Timer: Max 1 trade per strategy every 30 minutes to prevent noise trading.")
    with head_col2:
        engine_on = st.toggle("🟢 Master Engine ON", value=False)
        if engine_on:
            st_autorefresh(interval=3 * 60 * 1000, key="fw_test_refresh")
            st.success("Auto-Pilot Running in Background")

    st.markdown("---")
    
    # --- ROW 1 ---
    col_r1c1, col_r1c2 = st.columns(2)
    
    with col_r1c1:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #1f77b4; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 20px;">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">Nifty Premium Flow</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Detects divergence between CE and PE smart money writing based on Volume & Price.</p>
            <div style="margin-top: 15px;"><span style="background-color: #2b313c; color: #4caf50; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: ACTIVE</span></div>
        </div>
        """, unsafe_allow_html=True)
        if engine_on:
            signal, data, msg, st_sl = run_oi_premium_flow(fyers)
            can_trade = True if not st.session_state['last_oi_trade_time'] else (datetime.datetime.now() - st.session_state['last_oi_trade_time']).total_seconds() / 60 > 30
            if "BUY" in signal and can_trade:
                auto_execute_paper_trade("OI Premium Flow", signal, data)
                st.session_state['last_oi_trade_time'] = datetime.datetime.now(); st.toast(f"🤖 Algo Executed: {signal}")
            else: st.caption(f"Live Status: {msg}" if can_trade else "Cooling down.")

    with col_r1c2:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #ff9800; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 20px;">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">Power Scalper (Dual-TF Pivot)</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Spot 5-Min Pivot breakouts aligned with Option 2-Min SuperTrend & RSI > 60.</p>
            <div style="margin-top: 15px;"><span style="background-color: #2b313c; color: #ff9800; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: ACTIVE ⚡</span></div>
        </div>
        """, unsafe_allow_html=True)
        if engine_on:
            signal, data, msg, st_sl = run_power_scalper_dual(fyers)
            can_trade = True if not st.session_state['last_scalper_dual_trade_time'] else (datetime.datetime.now() - st.session_state['last_scalper_dual_trade_time']).total_seconds() / 60 > 30
            if "BUY" in signal and can_trade:
                auto_execute_paper_trade("Power Scalper (Dual)", signal, data, dynamic_sl=st_sl)
                st.session_state['last_scalper_dual_trade_time'] = datetime.datetime.now(); st.toast(f"⚡ Algo Executed: {signal}")
            else: st.caption(f"Live Status: {msg}" if can_trade else "Cooling down.")

    # --- ROW 2 ---
    col_r2c1, col_r2c2 = st.columns(2)

    with col_r2c1:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #e91e63; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">Ravi Bhatt OI Spike</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Pure Option Buying Math. Triggers when Change in Option OI exceeds 500%.</p>
            <div style="margin-top: 15px;"><span style="background-color: #2b313c; color: #e91e63; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: ACTIVE 📊</span></div>
        </div>
        """, unsafe_allow_html=True)
        if engine_on:
            signal, data, msg, st_sl = run_ravi_bhatt_oi(fyers)
            can_trade = True if not st.session_state['last_ravi_trade_time'] else (datetime.datetime.now() - st.session_state['last_ravi_trade_time']).total_seconds() / 60 > 30
            if "BUY" in signal and can_trade:
                auto_execute_paper_trade("Ravi Bhatt 500% OI", signal, data)
                st.session_state['last_ravi_trade_time'] = datetime.datetime.now(); st.toast(f"📊 Algo Executed: {signal}")
            else: st.caption(f"Live Status: {msg}" if can_trade else "Cooling down.")

    with col_r2c2:
        st.markdown(f"""
        <div style="background-color: #1a1c23; border-top: 5px solid #00bcd4; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
            <h3 style="margin: 0; color: #fff; font-size: 18px;">Power Scalper (Spot Burst)</h3>
            <p style="color: #888; font-size: 12px; margin-top: 5px;">Pure Momentum. Spot SuperTrend is aligned and Spot RSI violently crosses 60/40.</p>
            <div style="margin-top: 15px;"><span style="background-color: #2b313c; color: #00bcd4; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Status: ACTIVE 🚀</span></div>
        </div>
        """, unsafe_allow_html=True)
        if engine_on:
            signal, data, msg, st_sl = run_power_scalper_spot_burst(fyers)
            can_trade = True if not st.session_state['last_spot_burst_trade_time'] else (datetime.datetime.now() - st.session_state['last_spot_burst_trade_time']).total_seconds() / 60 > 30
            if "BUY" in signal and can_trade:
                auto_execute_paper_trade("Power Scalper (Burst)", signal, data)
                st.session_state['last_spot_burst_trade_time'] = datetime.datetime.now(); st.toast(f"🚀 Algo Executed: {signal}")
            else: st.caption(f"Live Status: {msg}" if can_trade else "Cooling down.")
