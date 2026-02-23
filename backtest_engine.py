import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import calendar
import os

# --- DATABASES ---
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
    response = fyers.history(data=data)
    if response.get("s") == "ok" and response.get("candles"):
        df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
        df.set_index('datetime', inplace=True)
        return df
    return None

def calculate_option_pnl(spot_pnl, holding_time_mins, spot_price):
    delta_pnl = spot_pnl * 0.50 
    theta_decay = holding_time_mins * (spot_price * 0.0000015) 
    return delta_pnl - theta_decay

# =====================================================================
# STRATEGY 1: ADAPTIVE SMC (PRO LEVEL - EXTREMELY SAFE)
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
# STRATEGY 2: NATURAL GAS VOLATILITY BLAST (VBO) 🌪️
# =====================================================================
def run_natural_gas_blast(df, is_option_mode=False):
    """Backtesting Logic for NG Volatility Blast"""
    df.ta.bbands(length=20, std=2.0, append=True)
    df.ta.adx(length=14, append=True)
    df.ta.atr(length=14, append=True)
    df['VOL_SMA'] = df['volume'].rolling(20).mean()
    df.dropna(inplace=True)

    bb_upper = [c for c in df.columns if 'BBU_' in c][0]
    bb_lower = [c for c in df.columns if 'BBL_' in c][0]
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
            equity_curve.append(cumulative_pnl)
            continue

        if not in_trade:
            # Condition: BB Breakout + ADX > 25 (Trend Strength) + High Volume
            long_cond = (row['close'] > row[bb_upper]) and (row[adx_col] > 25) and (row['volume'] > row['VOL_SMA'])
            short_cond = (row['close'] < row[bb_lower]) and (row[adx_col] > 25) and (row['volume'] > row['VOL_SMA'])

            if long_cond:
                in_trade, trade_type, entry_price, entry_time = True, "LONG", row['close'], index
                sl = entry_price - (row['ATRr_14'] * 2.0) # 2x ATR Stoploss for NG
                tgt = entry_price + ((entry_price - sl) * 2.0) # 1:2 RR
            elif short_cond:
                in_trade, trade_type, entry_price, entry_time = True, "SHORT", row['close'], index
                sl = entry_price + (row['ATRr_14'] * 2.0)
                tgt = entry_price - ((sl - entry_price) * 2.0)

        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve

# =====================================================================
# STRATEGY 3 & 4: FALLBACKS (Power Scalper & Confluence)
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

# --- UI RENDERER ---
def render_ui(fyers):
    st.markdown("### ⏳ The Multi-Strategy Time Machine")
    
    selected_strategy = st.selectbox("Select Trading Algorithm", [
        "NG Volatility Blast (BB + ADX) 🌪️",
        "Adaptive SMC (Trend Safe + FVG) 🛡️",
        "Power Scalper (YT: SuperTrend + RSI Blast) ⚡", 
        "Triple Confluence (VWAP + EMA21 + RSI)"
    ])
    st.markdown("---")
    
    col_m1, col_m2, col_m3 = st.columns([1, 1.5, 1])
    with col_m1: market_type = st.radio("Select Market", ["MCX (Commodities Futures)", "NSE (Equities/Indices)"])
    with col_m2:
        if "NSE" in market_type:
            test_asset = st.selectbox("Asset to Backtest", fno_base_universe)
            fetch_sym = get_spot_symbol(test_asset)
        else:
            test_asset = st.selectbox("Commodity Future", mcx_universe)
            fetch_sym = f"MCX:{test_asset}{get_current_monthly_mcx_expiry()}FUT"
            
    with col_m3:
        test_tf = st.selectbox("Timeframe", ["1", "5", "15", "30"], index=2, format_func=lambda x: f"{x} Min")
        test_duration = st.slider("Lookback (Days)", min_value=5, max_value=90, value=30, step=5)
            
    st.markdown("---")
    is_opt_mode = st.checkbox("🔥 Run Backtest on ATM Options (Greek Simulator)", value=False)
    st.write("")
    
    run_btn = st.button(f"🔬 Run {selected_strategy.split('(')[0]}Backtest", use_container_width=True, type="primary")
        
    if run_btn:
        st.markdown("---")
        with st.spinner(f"Fetching data for {fetch_sym}..."):
            df = fetch_historical_data(fyers, fetch_sym, test_tf, test_duration)
            
            if df is not None and not df.empty:
                with st.spinner("Executing Virtual Trades..."):
                    if "NG Volatility" in selected_strategy:
                        trades_df, equity_curve = run_natural_gas_blast(df, is_option_mode=is_opt_mode)
                    elif "Adaptive SMC" in selected_strategy:
                        trades_df, equity_curve = run_adaptive_smc(df, is_option_mode=is_opt_mode)
                    elif "Power Scalper" in selected_strategy:
                        trades_df, equity_curve = run_power_scalper(df, is_option_mode=is_opt_mode)
                    else:
                        trades_df, equity_curve = run_triple_confluence(df, is_option_mode=is_opt_mode)
                    
                if not trades_df.empty:
                    total_trades = len(trades_df)
                    wins = len(trades_df[trades_df['PnL'] > 0])
                    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
                    total_points = trades_df['PnL'].sum()
                    
                    pnl_label = "Premium Points (Options)" if is_opt_mode else "Net PnL (Points)"
                    m1, m2, m3, m4 = st.columns(4)
                    with m1: st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #404654; text-align:center;"><h4 style="margin:0; color:#888;">Total Trades</h4><h2 style="margin:0; color:#fff;">{total_trades}</h2></div>""", unsafe_allow_html=True)
                    with m2: 
                        color = "#4caf50" if win_rate >= 35 else "#ff5252" 
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {color}; text-align:center;"><h4 style="margin:0; color:#888;">Win Rate</h4><h2 style="margin:0; color:{color};">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
                    with m3:
                        pnl_color = "#4caf50" if total_points > 0 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {pnl_color}; text-align:center;"><h4 style="margin:0; color:#888;">{pnl_label}</h4><h2 style="margin:0; color:{pnl_color};">{total_points:+.2f}</h2></div>""", unsafe_allow_html=True)
                    with m4: 
                        rr_text = "1:2 (NG Blast)" if "NG Volatility" in selected_strategy else "1:2.5 (SMC)" if "Adaptive SMC" in selected_strategy else "Dynamic"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #1f77b4; text-align:center;"><h4 style="margin:0; color:#888;">Risk:Reward</h4><h2 style="margin:0; color:#1f77b4; font-size:16px; margin-top:10px;">{rr_text}</h2></div>""", unsafe_allow_html=True)
                    
                    st.markdown("### 📈 Cumulative Equity Curve")
                    st.area_chart(equity_curve, color="#1f77b4")
                    
                    st.markdown("### 📜 Detailed Trade Log")
                    st.dataframe(trades_df.style.applymap(lambda x: 'background-color: rgba(44, 160, 44, 0.2); color: #4caf50;' if x > 0 else 'background-color: rgba(214, 39, 40, 0.2); color: #ff5252;' if x < 0 else '', subset=['PnL']), use_container_width=True)
                else:
                    st.warning("No valid setups found. The strict trend and volume filters blocked the trades.")
            else:
                st.error("Data Fetch Failed.")
