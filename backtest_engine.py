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
mcx_universe = ["CRUDEOIL", "GOLD", "GOLDM", "SILVER", "SILVERMIC", "NATURALGAS", "COPPER", "ZINC"]

# --- HELPER FUNCTIONS ---
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

# --- OPTIONS MATH SIMULATOR ---
def calculate_option_pnl(spot_pnl, holding_time_mins, spot_price):
    """
    Simulates ATM Option Premium PnL:
    1. Delta Approximation = 0.50 (ATM options move 50% of the spot price)
    2. Theta Decay = Premium loses roughly 0.0000015 of Spot Value per minute held.
    """
    delta_pnl = spot_pnl * 0.50 
    theta_decay = holding_time_mins * (spot_price * 0.0000015) 
    option_pnl = delta_pnl - theta_decay
    return option_pnl

# =====================================================================
# STRATEGY 1: DIXON SUPERTREND (USER'S PINE SCRIPT)
# =====================================================================
def run_dixon_backtest(df, use_time_filter=True, skip_tuesday=True, is_option_mode=False):
    df.ta.supertrend(length=10, multiplier=3.0, append=True)
    df.ta.vwap(append=True); df.ta.atr(length=14, append=True)
    
    st_cols = [c for c in df.columns if 'SUPERTd' in c]
    if not st_cols: return pd.DataFrame(), []
    st_dir_col = st_cols[0]
    
    df['dayofweek'], df['hour'], df['minute'] = df.index.dayofweek, df.index.hour, df.index.minute
    df['prev_st_dir'] = df[st_dir_col].shift(1)
    df['st_buy_signal'] = (df[st_dir_col] == 1) & (df['prev_st_dir'] == -1)
    df['st_sell_signal'] = (df[st_dir_col] == -1) & (df['prev_st_dir'] == 1)
    
    df['is_tuesday'] = df['dayofweek'] == 1
    df['is_afternoon'] = (df['hour'] > 12) | ((df['hour'] == 12) & (df['minute'] >= 30))
    df['is_morning'] = ~df['is_afternoon']
    df['trade_allowed'] = ~df['is_tuesday'] if skip_tuesday else True
    
    if use_time_filter:
        df['long_cond'] = df['st_buy_signal'] & (df['close'] > df['VWAP_D']) & df['trade_allowed'] & df['is_afternoon']
        df['short_cond'] = df['st_sell_signal'] & (df['close'] < df['VWAP_D']) & df['trade_allowed'] & df['is_morning']
    else:
        df['long_cond'] = df['st_buy_signal'] & (df['close'] > df['VWAP_D']) & df['trade_allowed']
        df['short_cond'] = df['st_sell_signal'] & (df['close'] < df['VWAP_D']) & df['trade_allowed']

    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, atr_at_entry, entry_time = False, None, 0, 0, None
    highest_seen, lowest_seen, trail_active, trail_sl = 0, 0, False, 0

    for index, row in df.iterrows():
        if in_trade:
            close, high, low, vwap, st_dir = row['close'], row['high'], row['low'], row['VWAP_D'], row[st_dir_col] 
            exit_triggered = False
            spot_pnl = 0
            exit_reason = ""
            exit_price = 0
            
            if trade_type == "LONG":
                highest_seen = max(highest_seen, high)
                if not trail_active and highest_seen >= entry_price + (1.0 * atr_at_entry):
                    trail_active, trail_sl = True, highest_seen - (1.0 * atr_at_entry)
                if trail_active:
                    trail_sl = max(trail_sl, highest_seen - (1.0 * atr_at_entry))
                    if low <= trail_sl:
                        spot_pnl, exit_price, exit_reason, exit_triggered = trail_sl - entry_price, trail_sl, "Trailing SL", True
                
                if not exit_triggered and (close < vwap or st_dir == -1):
                    spot_pnl, exit_price, exit_reason, exit_triggered = close - entry_price, close, "Hard SL", True

            elif trade_type == "SHORT":
                lowest_seen = min(lowest_seen, low)
                if not trail_active and lowest_seen <= entry_price - (1.0 * atr_at_entry):
                    trail_active, trail_sl = True, lowest_seen + (1.0 * atr_at_entry)
                if trail_active:
                    trail_sl = min(trail_sl, lowest_seen + (1.0 * atr_at_entry))
                    if high >= trail_sl:
                        spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - trail_sl, trail_sl, "Trailing SL", True
                
                if not exit_triggered and (close > vwap or st_dir == 1):
                    spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - close, close, "Hard SL", True

            if exit_triggered:
                holding_mins = (index - entry_time).total_seconds() / 60
                
                # Apply Option Greeks Math if Mode is ON
                if is_option_mode:
                    final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price)
                    trade_type_txt = f"{trade_type} (BUY ATM {'CE' if trade_type=='LONG' else 'PE'})"
                else:
                    final_pnl = spot_pnl
                    trade_type_txt = trade_type
                    
                cumulative_pnl += final_pnl
                trade_log.append({"Entry_Date": entry_time, "Exit_Date": index, "Type": trade_type_txt, "Spot Entry": entry_price, "Spot Exit": exit_price, "Hold Time(m)": int(holding_mins), "Final PnL": final_pnl, "Reason": exit_reason})
                in_trade = False

        if not in_trade: 
            if row['long_cond']: in_trade, trade_type, entry_price, atr_at_entry, highest_seen, trail_active, entry_time = True, "LONG", row['close'], row['ATRr_14'], row['close'], False, index
            elif row['short_cond']: in_trade, trade_type, entry_price, atr_at_entry, lowest_seen, trail_active, entry_time = True, "SHORT", row['close'], row['ATRr_14'], row['close'], False, index
            
        equity_curve.append(cumulative_pnl)
    return pd.DataFrame(trade_log), equity_curve

# =====================================================================
# STRATEGY 2: TRIPLE CONFLUENCE (VWAP + EMA21 + RSI)
# =====================================================================
def run_triple_confluence(df, is_option_mode=False):
    df.ta.vwap(append=True); df.ta.ema(length=21, append=True)
    df.ta.atr(length=14, append=True); df.ta.rsi(length=14, append=True)
    
    df['long_cond'] = (df['close'] > df['VWAP_D']) & (df['close'] > df['EMA_21']) & (df['RSI_14'] > 55)
    df['short_cond'] = (df['close'] < df['VWAP_D']) & (df['close'] < df['EMA_21']) & (df['RSI_14'] < 45)
    
    trade_log, equity_curve, cumulative_pnl = [], [0], 0
    in_trade, trade_type, entry_price, sl, tgt, entry_time = False, None, 0, 0, 0, None

    for index, row in df.iterrows():
        if in_trade:
            exit_triggered = False
            spot_pnl = 0
            exit_reason = ""
            exit_price = 0
            
            if trade_type == "LONG":
                if row['low'] <= sl: spot_pnl, exit_price, exit_reason, exit_triggered = sl - entry_price, sl, "Stoploss", True
                elif row['high'] >= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = tgt - entry_price, tgt, "Target", True
            elif trade_type == "SHORT":
                if row['high'] >= sl: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - sl, sl, "Stoploss", True
                elif row['low'] <= tgt: spot_pnl, exit_price, exit_reason, exit_triggered = entry_price - tgt, tgt, "Target", True

            if exit_triggered:
                holding_mins = (index - entry_time).total_seconds() / 60
                
                if is_option_mode:
                    final_pnl = calculate_option_pnl(spot_pnl, holding_mins, entry_price)
                    trade_type_txt = f"{trade_type} (BUY ATM {'CE' if trade_type=='LONG' else 'PE'})"
                else:
                    final_pnl = spot_pnl
                    trade_type_txt = trade_type
                    
                cumulative_pnl += final_pnl
                trade_log.append({"Entry_Date": entry_time, "Exit_Date": index, "Type": trade_type_txt, "Spot Entry": entry_price, "Spot Exit": exit_price, "Hold Time(m)": int(holding_mins), "Final PnL": final_pnl, "Reason": exit_reason})
                in_trade = False
            
            equity_curve.append(cumulative_pnl)
            continue

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
    st.write("Backtest your algorithms. Select **Options Mode** to simulate exact ATM Premium decay using Delta/Theta math.")
    
    selected_strategy = st.selectbox("Select Trading Algorithm", ["Dixon (SuperTrend + VWAP + ATR Trail)", "Triple Confluence (VWAP + EMA21 + RSI)"])
    st.markdown("---")
    
    col_m1, col_m2, col_m3 = st.columns([1, 1.5, 1])
    with col_m1:
        market_type = st.radio("Select Market", ["NSE (Equities/Indices)", "MCX (Commodities Futures)"])
    with col_m2:
        if "NSE" in market_type:
            test_asset = st.selectbox("Asset to Backtest", fno_base_universe)
            fetch_sym = get_spot_symbol(test_asset)
        else:
            test_asset = st.selectbox("Commodity Future", mcx_universe)
            fetch_sym = f"MCX:{test_asset}{get_current_monthly_expiry()}FUT"
            
    with col_m3:
        test_tf = st.selectbox("Timeframe", ["1", "5", "15"], index=1, format_func=lambda x: f"{x} Min")
        test_duration = st.slider("Lookback (Days)", min_value=5, max_value=60, value=15, step=5)

    if "Dixon" in selected_strategy:
        with st.expander("⚙️ Adjust Dixon Strategy Filters"):
            col_f1, col_f2 = st.columns(2)
            with col_f1: use_time = st.checkbox("Strict Time Filter (Long PM / Short AM)", value=True)
            with col_f2: skip_tue = st.checkbox("Skip Tuesdays", value=True)
            
    st.markdown("---")
    # THE OPTIONS BACKTESTING TOGGLE
    is_opt_mode = st.checkbox("🔥 Run Backtest on ATM Options (Greek Simulator)", value=False, help="Converts Spot PnL to expected ATM Premium PnL applying 0.5 Delta and Theta Time Decay.")
    st.write("")
    
    run_btn = st.button(f"🔬 Run {selected_strategy} Backtest", use_container_width=True, type="primary")
        
    if run_btn:
        st.markdown("---")
        with st.spinner(f"Fetching data from Exchange for {fetch_sym}..."):
            df = fetch_historical_data(fyers, fetch_sym, test_tf, test_duration)
            
            if df is not None and not df.empty:
                with st.spinner("Compiling Math & Executing Virtual Trades..."):
                    if "Dixon" in selected_strategy:
                        trades_df, equity_curve = run_dixon_backtest(df, use_time_filter=use_time, skip_tuesday=skip_tue, is_option_mode=is_opt_mode)
                    else:
                        trades_df, equity_curve = run_triple_confluence(df, is_option_mode=is_opt_mode)
                    
                if not trades_df.empty:
                    total_trades = len(trades_df)
                    wins = len(trades_df[trades_df['Final PnL'] > 0])
                    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
                    total_points = trades_df['Final PnL'].sum()
                    
                    # --- METRICS UI ---
                    pnl_label = "Premium Points (Options Mode)" if is_opt_mode else "Net PnL (Spot Points)"
                    m1, m2, m3, m4 = st.columns(4)
                    with m1: st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #404654; text-align:center;"><h4 style="margin:0; color:#888;">Total Trades</h4><h2 style="margin:0; color:#fff;">{total_trades}</h2></div>""", unsafe_allow_html=True)
                    with m2: 
                        color = "#4caf50" if win_rate >= 40 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {color}; text-align:center;"><h4 style="margin:0; color:#888;">Win Rate</h4><h2 style="margin:0; color:{color};">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
                    with m3:
                        pnl_color = "#4caf50" if total_points > 0 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {pnl_color}; text-align:center;"><h4 style="margin:0; color:#888;">{pnl_label}</h4><h2 style="margin:0; color:{pnl_color};">{total_points:+.2f}</h2></div>""", unsafe_allow_html=True)
                    with m4: st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #1f77b4; text-align:center;"><h4 style="margin:0; color:#888;">Mode</h4><h2 style="margin:0; color:#1f77b4; font-size:16px; margin-top:10px;">{'ATM Option Buyer' if is_opt_mode else 'Spot/Futures'}</h2></div>""", unsafe_allow_html=True)
                    
                    st.markdown("### 📈 Cumulative Equity Curve")
                    st.area_chart(equity_curve, color="#1f77b4")
                    
                    st.markdown("### 📜 Detailed Trade Log")
                    st.dataframe(trades_df.style.applymap(lambda x: 'background-color: rgba(44, 160, 44, 0.2); color: #4caf50;' if x > 0 else 'background-color: rgba(214, 39, 40, 0.2); color: #ff5252;' if x < 0 else '', subset=['Final PnL']), use_container_width=True)
                else:
                    st.warning("No trades triggered. Try relaxing the strategy filters or increasing the lookback period.")
            else:
                st.error("Data Fetch Failed.")
