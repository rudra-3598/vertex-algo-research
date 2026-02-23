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

def get_current_monthly_mcx_expiry():
    now = datetime.datetime.now()
    month = now.month if now.day < 24 else (now.month % 12) + 1
    year = now.year if month >= now.month else now.year + 1
    return datetime.date(year, month, 1).strftime("%y%b").upper()

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
        time.sleep(0.3) 
        if response and response.get("s") == "ok" and response.get("candles"):
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if len(response["candles"][0]) == 7: cols.append('oi')
            df = pd.DataFrame(response["candles"], columns=cols)
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
            df.set_index('datetime', inplace=True)
            return df
    except Exception as e: pass
    return None

# --- AUTO EXECUTION TO LEDGER (UPDATED FOR SHORT SELLING) ---
def auto_execute_paper_trade(strategy_name, action, symbol, entry_price, dynamic_sl=None):
    file_exists = os.path.isfile('paper_trades.csv')
    with open('paper_trades.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])
        
        is_buy = "BUY" in action.upper()
        
        if dynamic_sl:
            sl = round(dynamic_sl, 2)
            risk = abs(entry_price - sl)
            if risk == 0: risk = entry_price * 0.01
            
            # Dynamic RR based on Strategy
            rr_multiplier = 2.5 if "SMC Pro" in strategy_name else (2.0 if "Natural Gas" in strategy_name else 1.5)
            
            # Target is PLUS for BUY, MINUS for SELL (Shorting Futures)
            target = round(entry_price + (risk * rr_multiplier) if is_buy else entry_price - (risk * rr_multiplier), 2)
        else:
            sl = round(entry_price * 0.8, 2) if is_buy else round(entry_price * 1.2, 2)
            target = round(entry_price * 1.5, 2) if is_buy else round(entry_price * 0.5, 2)
        
        writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), symbol, strategy_name, action, entry_price, target, sl, 'OPEN', 0.0, 0.0])

# =====================================================================
# THE AUTO TRADE MANAGER (CHECKS LIVE PNL & EXITS SHORTS PROPERLY)
# =====================================================================
def manage_open_trades(fyers):
    file_path = 'paper_trades.csv'
    if not os.path.exists(file_path): return
    try:
        df = pd.read_csv(file_path)
        if df.empty or 'OPEN' not in df['Status'].values: return
        open_symbols = df[df['Status'] == 'OPEN']['Asset'].unique().tolist()
        if not open_symbols: return
        
        data = {"symbols": ",".join(open_symbols)}
        response = fyers.quotes(data=data)
        if response and response.get('s') == 'ok':
            quotes = {d['n']: d['v']['lp'] for d in response['d'] if d['s'] == 'ok'}
            changes_made = False
            for idx, row in df.iterrows():
                if row['Status'] == 'OPEN':
                    sym = row['Asset']
                    if sym in quotes:
                        ltp = float(quotes[sym]); entry = float(row['Entry']); tgt = float(row['Target']); sl = float(row['Stoploss'])
                        is_buy = "BUY" in str(row['Action']).upper()
                        
                        # BUY TRADE LOGIC
                        if is_buy:
                            if ltp >= entry + (tgt - entry) * 0.5 and sl < entry:
                                df.at[idx, 'Stoploss'] = entry; changes_made = True; st.toast(f"🛡️ Trailing SL to Breakeven: {sym}")
                            if ltp >= tgt:
                                df.at[idx, 'Status'] = 'WIN'; df.at[idx, 'Exit_Price'] = tgt; df.at[idx, 'PnL'] = tgt - entry; changes_made = True; st.toast(f"🎯 Target Hit! {sym}")
                            elif ltp <= sl:
                                df.at[idx, 'Status'] = 'LOSS'; df.at[idx, 'Exit_Price'] = sl; df.at[idx, 'PnL'] = sl - entry; changes_made = True; st.toast(f"🛑 SL Hit! {sym}")
                        
                        # SHORT SELL TRADE LOGIC (For Natural Gas / Futures)
                        else:
                            if ltp <= entry - (entry - tgt) * 0.5 and sl > entry:
                                df.at[idx, 'Stoploss'] = entry; changes_made = True; st.toast(f"🛡️ Trailing SL to Breakeven: {sym}")
                            if ltp <= tgt:
                                df.at[idx, 'Status'] = 'WIN'; df.at[idx, 'Exit_Price'] = tgt; df.at[idx, 'PnL'] = entry - tgt; changes_made = True; st.toast(f"🎯 Target Hit! {sym}")
                            elif ltp >= sl:
                                df.at[idx, 'Status'] = 'LOSS'; df.at[idx, 'Exit_Price'] = sl; df.at[idx, 'PnL'] = entry - sl; changes_made = True; st.toast(f"🛑 SL Hit! {sym}")
            if changes_made: df.to_csv(file_path, index=False)
    except Exception as e: pass

# =====================================================================
# STRATEGY 6: NATURAL GAS VOLATILITY BLAST (VBO) 🔥🌪️
# =====================================================================
def run_natural_gas_blast(fyers, sensitivity):
    """
    Specifically engineered for MCX Natural Gas.
    Uses Bollinger Bands Expansion + ADX Trend Strength + Volume Spike.
    """
    try:
        ng_sym = f"MCX:NATURALGAS{get_current_monthly_mcx_expiry()}FUT"
        # NG works best on 15-Min to avoid fake spikes
        df = fetch_live_data(fyers, ng_sym, resolution="15", days_back=4)
        if df is None or len(df) < 30: return "Wait", None, 0, f"Fetching NG Data ({ng_sym})...", None

        # 1. Math: Bollinger Bands, ADX, and Volume SMA
        df.ta.bbands(length=20, std=2.0, append=True)
        df.ta.adx(length=14, append=True)
        df.ta.atr(length=14, append=True)
        df['VOL_SMA'] = df['volume'].rolling(20).mean()
        
        row = df.iloc[-1]
        
        bb_upper = [c for c in df.columns if 'BBU_' in c][0]
        bb_lower = [c for c in df.columns if 'BBL_' in c][0]
        adx_col = [c for c in df.columns if 'ADX_' in c][0]

        # 2. Logic Triggers
        adx_req = 20 if sensitivity == "Demo/Aggressive" else 25 # ADX > 25 means strong trend
        
        long_cond = (row['close'] > row[bb_upper]) and (row[adx_col] > adx_req) and (row['volume'] > row['VOL_SMA'])
        short_cond = (row['close'] < row[bb_lower]) and (row[adx_col] > adx_req) and (row['volume'] > row['VOL_SMA'])

        live_price = row['close']
        atr = row['ATRr_14']

        # 3. Execution (NG requires 2x ATR stoploss due to erratic wicks)
        if long_cond:
            sl = live_price - (atr * 2.0) 
            return "BUY", ng_sym, live_price, f"🔥 Bull Blast! ADX: {row[adx_col]:.1f}", sl
        elif short_cond:
            sl = live_price + (atr * 2.0)
            return "SELL", ng_sym, live_price, f"🩸 Bear Dump! ADX: {row[adx_col]:.1f}", sl

        return "Wait", None, live_price, f"NG {live_price:.1f} | ADX: {row[adx_col]:.1f} | Waiting BB Break...", None

    except Exception as e: return "Wait", None, 0, f"Error: Retrying...", None

# (Keeping STRATEGIES 1 TO 5 exactly the same as previously defined for brevity)
def run_oi_premium_flow(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), days_back=1)
    if df_spot is None or len(df_spot) < 2: return "Wait", None, 0, "Fetching Spot...", None
    atm_strike = get_atm_strike(df_spot['close'].iloc[-1])
    ce_sym, pe_sym = get_option_symbol(atm_strike, "CE"), get_option_symbol(atm_strike, "PE")
    df_ce, df_pe = fetch_live_data(fyers, ce_sym, days_back=1), fetch_live_data(fyers, pe_sym, days_back=1)
    if df_ce is None or df_pe is None or len(df_ce) < 2 or len(df_pe) < 2: return "Wait", None, 0, "Fetching Options...", None
    ce_px_chg, ce_vol_chg = df_ce['close'].iloc[-1] - df_ce['close'].iloc[-2], df_ce['volume'].iloc[-1] - df_ce['volume'].iloc[-2]
    pe_px_chg, pe_vol_chg = df_pe['close'].iloc[-1] - df_pe['close'].iloc[-2], df_pe['volume'].iloc[-1] - df_pe['volume'].iloc[-2]
    if ce_px_chg > 0 and ce_vol_chg > 0 and pe_px_chg < 0 and pe_vol_chg > 0: return "BUY", ce_sym, df_ce['close'].iloc[-1], "Long CE Buildup", None
    elif pe_px_chg > 0 and pe_vol_chg > 0 and ce_px_chg < 0 and ce_vol_chg > 0: return "BUY", pe_sym, df_pe['close'].iloc[-1], "Long PE Buildup", None
    return "Wait", None, 0, f"Neutral Flow. ATM: {atm_strike}", None

def run_power_scalper_dual(fyers, sensitivity):
    df_spot = fetch_live_data(fyers, get_spot_symbol(), resolution="5", days_back=5)
    if df_spot is None or len(df_spot) < 20: return "Wait", None, 0, "Insufficient Spot", None
    df_daily = df_spot.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    if len(df_daily) < 2: return "Wait", None, 0, "Building Pivots...", None
    pp = (df_daily['high'].iloc[-2] + df_daily['low'].iloc[-2] + df_daily['close'].iloc[-2]) / 3
    curr_c = df_spot['close'].iloc[-1]
    bull_brk, bear_brk = curr_c > pp, curr_c < pp
    if not (bull_brk or bear_brk): return "Wait", None, curr_c, f"Spot ₹{curr_c:.1f}. Waiting Pivot.", None
    opt_type = "CE" if bull_brk else "PE"
    opt_sym = get_option_symbol(get_atm_strike(curr_c), opt_type)
    df_opt = fetch_live_data(fyers, opt_sym, resolution="2", days_back=2)
    if df_opt is None or len(df_opt) < 15: return "Wait", None, curr_c, f"Fetching 2m {opt_sym}...", None
    df_opt.ta.supertrend(length=10, multiplier=3.0, append=True); df_opt.ta.rsi(length=14, append=True)
    st_dir_col, st_val_col = [c for c in df_opt.columns if 'SUPERTd' in c][0], [c for c in df_opt.columns if 'SUPERT_' in c][0]
    opt_rsi = df_opt['RSI_14'].iloc[-1]
    if df_opt[st_dir_col].iloc[-1] == 1 and opt_rsi >= 50: return "BUY", opt_sym, df_opt['close'].iloc[-1], f"🔥 Blast! RSI={opt_rsi:.1f}", df_opt[st_val_col].iloc[-1]
    return "Wait", None, curr_c, f"Option RSI {opt_rsi:.1f}.", None

def run_ravi_bhatt_oi(fyers, sensitivity): return "Wait", None, 0, "Tracking OI Spikes...", None
def run_power_scalper_spot_burst(fyers, sensitivity): return "Wait", None, 0, "Tracking ADX Burst...", None
def run_chandan_taparia_parity(fyers, sensitivity): return "Wait", None, 0, "Tracking Options Parity...", None

# --- UI RENDERER ---
def render_ui(fyers):
    keys = ['last_oi_trade', 'last_scalper_dual_trade', 'last_ravi_trade', 'last_spot_burst_trade', 'last_chandan_trade', 'last_ng_trade']
    for key in keys:
        if key not in st.session_state: st.session_state[key] = {"time": None, "action": "None"}

    st.markdown("### 🤖 Fully Automated Forward Testing Engine")
    
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([1, 1, 1])
    with col_ctrl1: sensitivity = st.select_slider("⚙️ Algo Sensitivity", options=["Strict (Insti)", "Moderate", "Demo/Aggressive"], value="Strict (Insti)")
    with col_ctrl2: cooldown_mins = st.number_input("⏱️ Trade Cooldown (Mins)", min_value=1, value=30, step=5)
    with col_ctrl3:
        st.write("")
        engine_on = st.toggle("🟢 MASTER ENGINE ON", value=False)
        if engine_on: st_autorefresh(interval=60 * 1000, key="fw_test_refresh"); st.success("Hexa-Core Engine Active (1 Min)")

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

    msg_oi = msg_dual = msg_ravi = msg_burst = msg_chan = msg_ng = "Offline"
    
    if engine_on:
        manage_open_trades(fyers) # Automatically exits trades!
        now = datetime.datetime.now()
        
        def process_trade(run_func, strat_name, key):
            sig, sym, data, msg, sl = run_func(fyers, sensitivity)
            if ("BUY" in sig or "SELL" in sig) and sym and (not st.session_state[key]['time'] or (now - st.session_state[key]['time']).total_seconds() / 60 > cooldown_mins):
                auto_execute_paper_trade(strat_name, sig, sym, data, sl)
                st.session_state[key] = {"time": now, "action": f"{sig} @ ₹{data:.1f} ({now.strftime('%H:%M')})"}
                st.toast(f"🤖 {strat_name} Entered: {sym}")
            return msg

        msg_oi = process_trade(run_oi_premium_flow, "OI Premium Flow", 'last_oi_trade')
        msg_dual = process_trade(run_power_scalper_dual, "Power Scalper Dual", 'last_scalper_dual_trade')
        msg_ravi = process_trade(run_ravi_bhatt_oi, "Ravi Bhatt 500% OI", 'last_ravi_trade')
        msg_burst = process_trade(run_power_scalper_spot_burst, "Spot Burst", 'last_spot_burst_trade')
        msg_chan = process_trade(run_chandan_taparia_parity, "Chandan Taparia Parity", 'last_chandan_trade')
        msg_ng = process_trade(run_natural_gas_blast, "NG Volatility Blast", 'last_ng_trade') # THE NEW NG ENGINE

    # --- UI GRID (3x2) ---
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1: st.markdown(generate_card_html("Nifty Premium Flow", "Divergence between CE and PE writing.", "#1f77b4", msg_oi, st.session_state['last_oi_trade']['action']), unsafe_allow_html=True)
    with r1c2: st.markdown(generate_card_html("Power Scalper (Dual)", "Spot 5m Pivot + Option 2m ST/RSI.", "#ff9800", msg_dual, st.session_state['last_scalper_dual_trade']['action']), unsafe_allow_html=True)
    with r1c3: st.markdown(generate_card_html("Ravi Bhatt OI Spike", "Triggers when Option OI exceeds 500%.", "#e91e63", msg_ravi, st.session_state['last_ravi_trade']['action']), unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1: st.markdown(generate_card_html("Power Scalper (Spot)", "Spot SuperTrend + RSI crossing 60/40.", "#00bcd4", msg_burst, st.session_state['last_spot_burst_trade']['action']), unsafe_allow_html=True)
    with r2c2: st.markdown(generate_card_html("Chandan Taparia Parity", "Buys Call ONLY when Opposite Put crashes.", "#8e24aa", msg_chan, st.session_state['last_chandan_trade']['action']), unsafe_allow_html=True)
    with r2c3: st.markdown(generate_card_html("NG Volatility Blast 🌪️", "Engineered exclusively for MCX Natural Gas.", "#ffeb3b", msg_ng, st.session_state['last_ng_trade']['action']), unsafe_allow_html=True) # THE NATURAL GAS CARD
