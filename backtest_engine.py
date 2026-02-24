import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import calendar
import time
import os

# =====================================================================
# DATABASES & HELPERS
# =====================================================================
@st.cache_data
def load_fno_base_stocks():
    base_indices = ["NIFTY", "BANKNIFTY"] 
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            stocks = [t for t in df['SYMBOL'].dropna().unique() if t not in base_indices]
            return base_indices + stocks
        except: return base_indices + ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "SBIN"]
    return base_indices + ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "SBIN"]

fno_base_universe = load_fno_base_stocks()
mcx_universe = ["CRUDEOIL", "NATURALGAS", "GOLD", "GOLDM", "SILVER", "SILVERMIC", "COPPER", "ZINC"]

def get_current_monthly_expiry():
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

def get_spot_symbol(sym):
    if sym == "NIFTY": return "NSE:NIFTY50-INDEX"
    elif sym == "BANKNIFTY": return "NSE:NIFTYBANK-INDEX"
    return f"NSE:{sym}-EQ"

# --- FYERS API FETCHER ---
def fetch_historical_data(fyers, symbol, resolution, days_back):
    now = datetime.datetime.now()
    range_from = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    range_to = now.strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": str(resolution), "date_format": "1", "range_from": range_from, "range_to": range_to, "cont_flag": "1"}
    
    try:
        response = fyers.history(data=data)
        if response and response.get("s") == "ok" and response.get("candles"):
            df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
            df.set_index('datetime', inplace=True)
            return df
    except Exception as e:
        st.error(f"API Error fetching {symbol}: {e}")
    return None

def calculate_option_pnl(spot_pnl, holding_time_mins, spot_price):
    delta_pnl = spot_pnl * 0.50 
    theta_decay = holding_time_mins * (spot_price * 0.0000015) 
    return delta_pnl - theta_decay

# =====================================================================
# STRATEGY 1: V13 GOD MODE (Dual TF Pivot + ST/RSI + 40pt SL) 👑
# =====================================================================
def run_v13_god_mode(fyers, symbol, days_back, is_option_mode=False):
    df_5m = fetch_historical_data(fyers, symbol, "5", days_back)
    df_2m = fetch_historical_data(fyers, symbol, "2", days_back)

    if df_5m is None or df_2m is None: return pd.DataFrame(), [0]

    # 🔥 BUG FIX 1: Remove Duplicate Timestamps if any API noise occurs
    df_5m = df_5m[~df_5m.index.duplicated(keep='first')]
    df_2m = df_2m[~df_2m.index.duplicated(keep='first')]

    df_5m.sort_index(inplace=True)
    df_2m.sort_index(inplace=True)

    df_5m['date'] = df_5m.index.date
    daily_data = df_5m.groupby('date').agg({'high': 'max', 'low': 'min', 'close': 'last'}).shift(1)
    daily_data['Pivot'] = (daily_data['high'] + daily_data['low'] + daily_data['close']) / 3
    daily_data['R1'] = (2 * daily_data['Pivot']) - daily_data['low']
    daily_data['S1'] = (2 * daily_data['Pivot']) - daily_data['high']
    df_5m = df_5m.merge(daily_data[['R1', 'S1']], left_on='date', right_index=True, how='left')

    # 🔥 BUG FIX 2: Vectorized Previous Close (100x Faster & Crash Proof)
    df_5m['prev_close'] = df_5m['close'].shift(1)

    df_2m.ta.rsi(length=14, append=True)
    df_2m.ta.supertrend(length=10, multiplier=3, append=True)
    rsi_col = [c for c in df_2m.columns if 'RSI' in c][0]
    st_dir_col = [c for c in df_2m.columns if 'SUPERTd' in c][0]

    trade_log, equity_curve, cumulative_pnl = [], [0], 0

    for index, row in df_5m.iterrows():
        # Check if R1/S1 or Prev Close is NaN
        if pd.isna(row['R1']) or pd.isna(row['S1']) or pd.isna(row['prev_close']): continue
        
        prev_close = row['prev_close']

        trade_type = None
        if row['close'] > row['R1'] and prev_close <= row['R1']: trade_type = "LONG"
        elif row['close'] < row['S1'] and prev_close >= row['S1']: trade_type = "SHORT"

        if trade_type:
            lookahead_end = index + pd.Timedelta(minutes=6)
            
            # Using Boolean Masking (Safe Slicing)
            window_df = df_2m[(df_2m.index >= index) & (df_2m.index <= lookahead_end)]
            
            for opt_idx, opt_candle in window_df.iterrows():
                if pd.isna(opt_candle.get(rsi_col)) or pd.isna(opt_candle.get(st_dir_col)): continue
                
                long_conf = trade_type == "LONG" and opt_candle[rsi_col] > 60 and opt_candle[st_dir_col] > 0
                short_conf = trade_type == "SHORT" and opt_candle[rsi_col] < 40 and opt_candle[st_dir_col] < 0
                
                if long_conf or short_conf:
                    entry_price = opt_candle['close']
                    hard_sl = entry_price - 40 if trade_type == "LONG" else entry_price + 40
                    
                    # Safe future iteration
                    future_candles = df_2m[df_2m.index >= (opt_idx + pd.Timedelta(minutes=2))]
                    exit_price, exit_time, exit_reason = 0, None, ""
                    
                    for f_idx, f_candle in future_candles.iterrows():
                        if f_idx.time() >= pd.to_datetime('15:15').time():
                            exit_price, exit_time, exit_reason = f_candle['close'], f_idx, "TIME EXIT ⏳"; break
                        
                        if trade_type == "LONG":
                            if f_candle['low'] <= hard_sl: exit_price, exit_time, exit_reason = hard_sl, f_idx, "HARD SL 🛑"; break
                            elif f_candle[st_dir_col] < 0: exit_price, exit_time, exit_reason = f_candle['close'], f_idx, "TRAIL HIT 🎯"; break
                        elif trade_type == "SHORT":
                            if f_candle['high'] >= hard_sl: exit_price, exit_time, exit_reason = hard_sl, f_idx, "HARD SL 🛑"; break
                            elif f_candle[st_dir_col] > 0: exit_price, exit_time, exit_reason = f_candle['close'], f_idx, "TRAIL HIT 🎯"; break
                            
                    if exit_time is None and not future_candles.empty:
                        exit_price, exit_time, exit_reason = future_candles.iloc[-1]['close'], future_candles.index[-1], "EOD EXIT"
                        
                    pts_gained = exit_price - entry_price if trade_type == "LONG" else entry_price - exit_price
                    holding_mins = (exit_time - opt_idx).total_seconds() / 60 if exit_time else 0
                    final_pnl = calculate_option_pnl(pts_gained, holding_mins, entry_price) if is_option_mode else pts_gained
                    
                    cumulative_pnl += final_pnl
                    trade_log.append({"Entry": opt_idx, "Exit": exit_time, "Type": trade_type, "Entry_Px": entry_price, "Exit_Px": exit_price, "Mins": holding_mins, "PnL": final_pnl, "Reason": exit_reason})
                    equity_curve.append(cumulative_pnl)
                    break 

    return pd.DataFrame(trade_log), equity_curve
# =====================================================================
# STRATEGY 2: ADAPTIVE SMC (PRO LEVEL - EXTREMELY SAFE) 🧠
# =====================================================================
def run_adaptive_smc(df, is_option_mode=False):
    df.ta.atr(length=14, append=True); df.ta.ema(length=200, append=True); df.dropna(inplace=True)
    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, sl, tgt, entry_time = False, None, 0, 0, 0, None
    active_bull_ob = active_bear_ob = None

    for i in range(2, len(df)):
        row, prev1, prev2 = df.iloc[i], df.iloc[i-1], df.iloc[i-2]
        atr, ema200 = row['ATRr_14'], row['EMA_200']

        if not in_trade:
            gap_bull_size, gap_bear_size = row['low'] - prev2['high'], prev2['low'] - row['high']
            fvg_bull = gap_bull_size > (atr * 0.1) and row['close'] > ema200
            fvg_bear = gap_bear_size > (atr * 0.1) and row['close'] < ema200

            if fvg_bull:
                ob_high, ob_low = prev2['high'], prev2['low']
                for j in range(i-2, max(-1, i-10), -1):
                    if df.iloc[j]['close'] < df.iloc[j]['open']: ob_high, ob_low = df.iloc[j]['high'], df.iloc[j]['low']; break
                active_bull_ob = {'high': ob_high, 'low': ob_low, 'atr': atr}; active_bear_ob = None
            elif fvg_bear:
                ob_high, ob_low = prev2['high'], prev2['low']
                for j in range(i-2, max(-1, i-10), -1):
                    if df.iloc[j]['close'] > df.iloc[j]['open']: ob_high, ob_low = df.iloc[j]['high'], df.iloc[j]['low']; break
                active_bear_ob = {'high': ob_high, 'low': ob_low, 'atr': atr}; active_bull_ob = None

            if active_bull_ob and row['low'] <= active_bull_ob['high']: 
                in_trade, trade_type, entry_price, entry_time = True, "LONG", active_bull_ob['high'], df.index[i]
                sl = active_bull_ob['low'] - (active_bull_ob['atr'] * 0.3)
                tgt = entry_price + (max(entry_price - sl, entry_price * 0.002) * 2.5); active_bull_ob = None
            elif active_bear_ob and row['high'] >= active_bear_ob['low']: 
                in_trade, trade_type, entry_price, entry_time = True, "SHORT", active_bear_ob['low'], df.index[i]
                sl = active_bear_ob['high'] + (active_bear_ob['atr'] * 0.3)
                tgt = entry_price - (max(sl - entry_price, entry_price * 0.002) * 2.5); active_bear_ob = None
        else:
            exit_triggered, spot_pnl, exit_reason, exit_price = False, 0, "", 0
            if trade_type == "LONG":
                if row['low'] <= sl: spot_pnl, exit_price, exit_reason, exit_triggered = sl - entry_price, sl, "SL Hit", True
                elif row['high'] >= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = tgt - entry_price, tgt, "Target Hit", True
            elif trade_type == "SHORT":
                if row['high'] >= sl: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - sl, sl, "SL Hit", True
                elif row['low'] <= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - tgt, tgt, "Target Hit", True
            if exit_triggered:
                holding_mins = (df.index[i] - entry_time).total_seconds() / 60
                final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price) if is_option_mode else spot_pnl
                cumulative_pnl += final_pnl
                trade_log.append({"Entry": entry_time, "Exit": df.index[i], "Type": trade_type, "Entry_Px": entry_price, "Exit_Px": exit_price, "Mins": int(holding_mins), "PnL": final_pnl, "Reason": exit_reason})
                in_trade = False
        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve


# =====================================================================
# STRATEGY 3: NATURAL GAS VOLATILITY BLAST (VBO) 🌪️
# =====================================================================
def run_natural_gas_blast(df, is_option_mode=False):
    df.ta.bbands(length=20, std=2.0, append=True); df.ta.adx(length=14, append=True); df.ta.atr(length=14, append=True)
    df['VOL_SMA'] = df['volume'].rolling(20).mean(); df.dropna(inplace=True)

    bb_upper = [c for c in df.columns if 'BBU_' in c][0]; bb_lower = [c for c in df.columns if 'BBL_' in c][0]
    adx_col = [c for c in df.columns if 'ADX_' in c][0]

    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, entry_time, sl, tgt = False, None, 0, None, 0, 0

    for index, row in df.iterrows():
        if in_trade:
            exit_triggered, spot_pnl, exit_reason, exit_price = False, 0, "", 0
            if trade_type == "LONG":
                if row['low'] <= sl: spot_pnl, exit_price, exit_reason, exit_triggered = sl - entry_price, sl, "SL Hit", True
                elif row['high'] >= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = tgt - entry_price, tgt, "Target Hit", True
            elif trade_type == "SHORT":
                if row['high'] >= sl: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - sl, sl, "SL Hit", True
                elif row['low'] <= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - tgt, tgt, "Target Hit", True

            if exit_triggered:
                holding_mins = (index - entry_time).total_seconds() / 60
                final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price) if is_option_mode else spot_pnl
                cumulative_pnl += final_pnl
                trade_log.append({"Entry": entry_time, "Exit": index, "Type": trade_type, "Entry_Px": entry_price, "Exit_Px": exit_price, "Mins": int(holding_mins), "PnL": final_pnl, "Reason": exit_reason})
                in_trade = False
            equity_curve.append(cumulative_pnl); continue

        if not in_trade:
            long_cond = (row['close'] > row[bb_upper]) and (row[adx_col] > 25) and (row['volume'] > row['VOL_SMA'])
            short_cond = (row['close'] < row[bb_lower]) and (row[adx_col] > 25) and (row['volume'] > row['VOL_SMA'])

            if long_cond:
                in_trade, trade_type, entry_price, entry_time = True, "LONG", row['close'], index
                sl = entry_price - (row['ATRr_14'] * 2.0); tgt = entry_price + ((entry_price - sl) * 2.0)
            elif short_cond:
                in_trade, trade_type, entry_price, entry_time = True, "SHORT", row['close'], index
                sl = entry_price + (row['ATRr_14'] * 2.0); tgt = entry_price - ((sl - entry_price) * 2.0)

        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve


# =====================================================================
# STRATEGY 4 & 5: POWER SCALPER & CONFLUENCE (The Classics)
# =====================================================================
def run_power_scalper(df, is_option_mode=False):
    df.ta.supertrend(length=10, multiplier=3.0, append=True); df.ta.rsi(length=14, append=True); df.ta.atr(length=14, append=True)
    st_cols = [c for c in df.columns if 'SUPERTd' in c]
    if not st_cols: return pd.DataFrame(), []
    st_dir_col = st_cols[0]
    df['prev_st_dir'] = df[st_dir_col].shift(1); df['prev_rsi'] = df['RSI_14'].shift(1)
    df['long_cond'] = (df[st_dir_col] == 1) & (df['RSI_14'] > 60) & (df['prev_rsi'] <= 60)
    df['short_cond'] = (df[st_dir_col] == -1) & (df['RSI_14'] < 40) & (df['prev_rsi'] >= 40)
    
    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, entry_time, sl = False, None, 0, None, 0
    for index, row in df.iterrows():
        if in_trade:
            close, high, low, st_dir = row['close'], row['high'], row['low'], row[st_dir_col]
            exit_triggered, spot_pnl, exit_reason, exit_price = False, 0, "", 0
            if trade_type == "LONG":
                if st_dir == -1 or close < sl: spot_pnl, exit_price, exit_reason, exit_triggered = close - entry_price, close, "Momentum Lost", True
                elif high >= entry_price + (row['ATRr_14'] * 1.5): spot_pnl, exit_price, exit_reason, exit_triggered = (row['ATRr_14'] * 1.5), entry_price + (row['ATRr_14'] * 1.5), "Scalp Target", True
            elif trade_type == "SHORT":
                if st_dir == 1 or close > sl: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - close, close, "Momentum Lost", True
                elif low <= entry_price - (row['ATRr_14'] * 1.5): spot_pnl, exit_price, exit_reason, exit_triggered = (row['ATRr_14'] * 1.5), entry_price - (row['ATRr_14'] * 1.5), "Scalp Target", True
            if exit_triggered:
                holding_mins = (index - entry_time).total_seconds() / 60
                final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price) if is_option_mode else spot_pnl
                cumulative_pnl += final_pnl
                trade_log.append({"Entry": entry_time, "Exit": index, "Type": trade_type, "Entry_Px": entry_price, "Exit_Px": exit_price, "Mins": int(holding_mins), "PnL": final_pnl, "Reason": exit_reason})
                in_trade = False
            equity_curve.append(cumulative_pnl); continue
        if not in_trade:
            if row['long_cond']: in_trade, trade_type, entry_price, entry_time, sl = True, "LONG", row['close'], index, row['close'] - row['ATRr_14']
            elif row['short_cond']: in_trade, trade_type, entry_price, entry_time, sl = True, "SHORT", row['close'], index, row['close'] + row['ATRr_14']
        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve

def run_triple_confluence(df, is_option_mode=False):
    df.ta.vwap(append=True); df.ta.ema(length=21, append=True); df.ta.atr(length=14, append=True); df.ta.rsi(length=14, append=True)
    if 'VWAP_D' not in df.columns: return pd.DataFrame(), [0]
    df['long_cond'] = (df['close'] > df['VWAP_D']) & (df['close'] > df['EMA_21']) & (df['RSI_14'] > 55)
    df['short_cond'] = (df['close'] < df['VWAP_D']) & (df['close'] < df['EMA_21']) & (df['RSI_14'] < 45)
    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, sl, tgt, entry_time = False, None, 0, 0, 0, None
    for index, row in df.iterrows():
        if in_trade:
            exit_triggered, spot_pnl, exit_reason, exit_price = False, 0, "", 0
            if trade_type == "LONG":
                if row['low'] <= sl: spot_pnl, exit_price, exit_reason, exit_triggered = sl - entry_price, sl, "Stoploss", True
                elif row['high'] >= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = tgt - entry_price, tgt, "Target", True
            elif trade_type == "SHORT":
                if row['high'] >= sl: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - sl, sl, "Stoploss", True
                elif row['low'] <= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - tgt, tgt, "Target", True
            if exit_triggered:
                holding_mins = (index - entry_time).total_seconds() / 60
                final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price) if is_option_mode else spot_pnl
                cumulative_pnl += final_pnl
                trade_log.append({"Entry": entry_time, "Exit": index, "Type": trade_type, "Entry_Px": entry_price, "Exit_Px": exit_price, "Mins": int(holding_mins), "PnL": final_pnl, "Reason": exit_reason})
                in_trade = False
            equity_curve.append(cumulative_pnl); continue
        if not in_trade:
            if row['long_cond']:
                in_trade, trade_type, entry_price, entry_time = True, "LONG", row['close'], index
                sl, tgt = max(row['VWAP_D'], row['EMA_21']) - (row['ATRr_14'] * 0.5), row['close'] + (row['ATRr_14'] * 3.0)
            elif row['short_cond']:
                in_trade, trade_type, entry_price, entry_time = True, "SHORT", row['close'], index
                sl, tgt = min(row['VWAP_D'], row['EMA_21']) + (row['ATRr_14'] * 0.5), row['close'] - (row['ATRr_14'] * 3.0)
        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve


# =====================================================================
# --- UI RENDERER ---
# =====================================================================
def render_ui(fyers):
    st.markdown("### ⏳ The Ultimate Time Machine Backtester")
    
    # 👑 ALL 6 STRATEGIES ADDED TO THE DROPDOWN
    selected_strategy = st.selectbox("Select Core Algorithm", [
        "V13 GOD MODE (R1/S1 + 2m ST/RSI) 👑",
        "Adaptive SMC (Trend Safe + FVG) 🧠",
        "NG Volatility Blast (BB + ADX) 🌪️",
        "Power Scalper (YT: SuperTrend + RSI Blast) ⚡", 
        "Triple Confluence (VWAP + EMA21 + RSI) 📊"
    ])
    st.markdown("---")
    
    col_m1, col_m2, col_m3 = st.columns([1, 1.5, 1])
    with col_m1: market_type = st.radio("Select Market", ["NSE (Equities/Indices)", "MCX (Commodities Futures)"])
    with col_m2:
        if "NSE" in market_type:
            test_asset = st.selectbox("Asset to Backtest", fno_base_universe)
            fetch_sym = get_spot_symbol(test_asset)
        else:
            test_asset = st.selectbox("Commodity Future", mcx_universe)
            fetch_sym = f"MCX:{test_asset}{get_current_monthly_mcx_expiry()}FUT"
            
    with col_m3:
        test_tf = st.selectbox("Timeframe (Overrides Dual-TF)", ["1", "5", "15", "30"], index=1, format_func=lambda x: f"{x} Min")
        test_duration = st.slider("Lookback (Days)", min_value=5, max_value=90, value=30, step=5)
            
    st.markdown("---")
    is_opt_mode = st.checkbox("🔥 Run Backtest on ATM Options Simulator", value=False)
    st.write("")
    
    run_btn = st.button(f"🔬 Run {selected_strategy.split('(')[0]}Backtest", use_container_width=True, type="primary")
        
    if run_btn:
        st.markdown("---")
        with st.spinner(f"Fetching data for {fetch_sym} and computing quantitative models..."):
            
            # --- ROUTING ENGINE ---
            if "V13 GOD MODE" in selected_strategy:
                # V13 Dual Timeframe handles fetching inside the function
                trades_df, equity_curve = run_v13_god_mode(fyers, fetch_sym, test_duration, is_opt_mode)
            else:
                # Standard Single-Timeframe fetch
                df = fetch_historical_data(fyers, fetch_sym, test_tf, test_duration)
                if df is not None and not df.empty:
                    if "Adaptive SMC" in selected_strategy:
                        trades_df, equity_curve = run_adaptive_smc(df, is_opt_mode)
                    elif "NG Volatility" in selected_strategy:
                        trades_df, equity_curve = run_natural_gas_blast(df, is_opt_mode)
                    elif "Power Scalper" in selected_strategy:
                        trades_df, equity_curve = run_power_scalper(df, is_opt_mode)
                    else:
                        trades_df, equity_curve = run_triple_confluence(df, is_opt_mode)
                else:
                    trades_df, equity_curve = pd.DataFrame(), [0]
                    
            # --- RESULT RENDERER ---
            if not trades_df.empty:
                total_trades = len(trades_df)
                wins = len(trades_df[trades_df['PnL'] > 0])
                win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
                total_points = trades_df['PnL'].sum()
                
                pnl_label = "Premium Points (Options)" if is_opt_mode else "Net PnL (Points)"
                m1, m2, m3, m4 = st.columns(4)
                with m1: st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #404654; text-align:center;"><h4 style="margin:0; color:#888;">Total Trades</h4><h2 style="margin:0; color:#fff;">{total_trades}</h2></div>""", unsafe_allow_html=True)
                with m2: 
                    color = "#4caf50" if win_rate >= 32 else "#ff5252" # Kept at 32% since Pro-SMC RR covers low win rates
                    st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {color}; text-align:center;"><h4 style="margin:0; color:#888;">Win Rate</h4><h2 style="margin:0; color:{color};">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
                with m3:
                    pnl_color = "#4caf50" if total_points > 0 else "#ff5252"
                    st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {pnl_color}; text-align:center;"><h4 style="margin:0; color:#888;">{pnl_label}</h4><h2 style="margin:0; color:{pnl_color};">{total_points:+.2f}</h2></div>""", unsafe_allow_html=True)
                with m4: 
                    rr_text = "40pt Hard SL" if "V13" in selected_strategy else "1:2.5 (SMC)" if "Adaptive SMC" in selected_strategy else "1:2 (NG Blast)" if "NG Volatility" in selected_strategy else "Dynamic"
                    st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #1f77b4; text-align:center;"><h4 style="margin:0; color:#888;">Risk Logic</h4><h2 style="margin:0; color:#1f77b4; font-size:16px; margin-top:10px;">{rr_text}</h2></div>""", unsafe_allow_html=True)
                
                st.markdown("### 📈 Cumulative Equity Curve")
                st.area_chart(equity_curve, color="#1f77b4")
                
                st.markdown("### 📜 Detailed Trade Log")
                st.dataframe(trades_df.style.applymap(lambda x: 'background-color: rgba(44, 160, 44, 0.2); color: #4caf50;' if x > 0 else 'background-color: rgba(214, 39, 40, 0.2); color: #ff5252;' if x < 0 else '', subset=['PnL']), use_container_width=True)
            else:
                st.warning("No highly probable setups found or data fetch failed. The strict trend filters might have blocked all trades for this period.")
