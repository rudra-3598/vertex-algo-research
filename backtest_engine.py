import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import os

# --- DATABASES ---
@st.cache_data
def load_fno_base_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [t for t in df['SYMBOL'].dropna().unique()]
        except: return ["NIFTY", "BANKNIFTY", "RELIANCE"]
    return ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "SBI"]

fno_base_universe = load_fno_base_stocks()

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

# --- DIXON TRADING VIEW STRATEGY LOGIC ---
def run_dixon_backtest(df, use_time_filter=True, skip_tuesday=True):
    # 1. Calculate Indicators
    df.ta.supertrend(length=10, multiplier=3.0, append=True)
    df.ta.vwap(append=True)
    df.ta.atr(length=14, append=True)
    
    st_cols = [c for c in df.columns if 'SUPERTd' in c]
    if not st_cols: return pd.DataFrame(), []
    st_dir_col = st_cols[0]
    
    # 2. Extract Time & Day Features
    df['dayofweek'] = df.index.dayofweek 
    df['hour'] = df.index.hour
    df['minute'] = df.index.minute
    
    # Vectorized Signals (Prevents loop-skipping bugs)
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

    trade_log = []
    equity_curve = [0]
    cumulative_pnl = 0
    
    in_trade = False
    trade_type = None
    entry_price = 0
    atr_at_entry = 0
    highest_seen = 0
    lowest_seen = 0
    trail_active = False
    trail_sl = 0

    # 3. Trade Simulation Loop
    for index, row in df.iterrows():
        # Exit Logic
        if in_trade:
            close, high, low = row['close'], row['high'], row['low']
            vwap = row['VWAP_D']
            st_dir = row[st_dir_col] 
            
            if trade_type == "LONG":
                highest_seen = max(highest_seen, high)
                # Activate Trail if Profit >= 1 ATR
                if not trail_active and highest_seen >= entry_price + (1.0 * atr_at_entry):
                    trail_active = True
                    trail_sl = highest_seen - (1.0 * atr_at_entry)
                
                if trail_active:
                    current_trail_sl = highest_seen - (1.0 * atr_at_entry)
                    trail_sl = max(trail_sl, current_trail_sl) # Trail ONLY goes up
                    
                    if low <= trail_sl: # Trailing Stop Hit
                        pnl = trail_sl - entry_price
                        cumulative_pnl += pnl
                        trade_log.append({"Date": index, "Type": "LONG", "Entry": entry_price, "Exit": trail_sl, "PnL": pnl, "Reason": "Trailing SL"})
                        in_trade = False
                
                # Hard Stoploss (Close < VWAP or ST flips Bearish)
                if in_trade and (close < vwap or st_dir == -1):
                    pnl = close - entry_price
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "LONG", "Entry": entry_price, "Exit": close, "PnL": pnl, "Reason": "Hard SL (Flip/VWAP)"})
                    in_trade = False

            elif trade_type == "SHORT":
                lowest_seen = min(lowest_seen, low)
                # Activate Trail if Profit >= 1 ATR
                if not trail_active and lowest_seen <= entry_price - (1.0 * atr_at_entry):
                    trail_active = True
                    trail_sl = lowest_seen + (1.0 * atr_at_entry)
                
                if trail_active:
                    current_trail_sl = lowest_seen + (1.0 * atr_at_entry)
                    trail_sl = min(trail_sl, current_trail_sl) # Trail ONLY goes down
                    
                    if high >= trail_sl: # Trailing Stop Hit
                        pnl = entry_price - trail_sl
                        cumulative_pnl += pnl
                        trade_log.append({"Date": index, "Type": "SHORT", "Entry": entry_price, "Exit": trail_sl, "PnL": pnl, "Reason": "Trailing SL"})
                        in_trade = False
                
                # Hard Stoploss (Close > VWAP or ST flips Bullish)
                if in_trade and (close > vwap or st_dir == 1):
                    pnl = entry_price - close
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "SHORT", "Entry": entry_price, "Exit": close, "PnL": pnl, "Reason": "Hard SL (Flip/VWAP)"})
                    in_trade = False

        # Entry Logic (Can enter immediately on the same candle if previous trade closed)
        if not in_trade: 
            if row['long_cond']:
                in_trade = True
                trade_type = "LONG"
                entry_price = row['close']
                atr_at_entry = row['ATRr_14']
                highest_seen = entry_price
                trail_active = False
            elif row['short_cond']:
                in_trade = True
                trade_type = "SHORT"
                entry_price = row['close']
                atr_at_entry = row['ATRr_14']
                lowest_seen = entry_price
                trail_active = False
        
        equity_curve.append(cumulative_pnl)

    return pd.DataFrame(trade_log), equity_curve

# --- UI RENDERER ---
def render_ui(fyers):
    st.markdown("### ⏳ The Dixon Strategy Time Machine")
    st.write("Running your proprietary Pine Script: **SuperTrend (10,3) + VWAP + ATR Trailing SL**")
    
    col1, col2, col3, col4 = st.columns([1.5, 1, 1.5, 1])
    with col1:
        test_asset = st.selectbox("Asset to Backtest", fno_base_universe)
    with col2:
        test_tf = st.selectbox("Timeframe", ["1", "5", "15"], index=1, format_func=lambda x: f"{x} Min")
    with col3:
        test_duration = st.slider("Lookback Period (Days)", min_value=5, max_value=60, value=15, step=5)
    with col4:
        st.write("")
        run_btn = st.button("🔬 Run Strategy", use_container_width=True, type="primary")

    with st.expander("⚙️ Adjust Strategy Filters (Turn off to see more trades)"):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            use_time = st.checkbox("Strict Time Filter (Long PM / Short AM)", value=True)
        with col_f2:
            skip_tue = st.checkbox("Skip Tuesdays", value=True)
        
    if run_btn:
        st.markdown("---")
        with st.spinner(f"Fetching {test_duration} days of {test_tf}-Min tick data for {test_asset}..."):
            df = fetch_historical_data(fyers, get_spot_symbol(test_asset), test_tf, test_duration)
            
            if df is not None and not df.empty:
                with st.spinner("Compiling Pine Script Logic in Python..."):
                    trades_df, equity_curve = run_dixon_backtest(df, use_time_filter=use_time, skip_tuesday=skip_tue)
                    
                if not trades_df.empty:
                    total_trades = len(trades_df)
                    wins = len(trades_df[trades_df['PnL'] > 0])
                    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
                    total_points = trades_df['PnL'].sum()
                    
                    # --- METRICS UI ---
                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #404654; text-align:center;">
                            <h4 style="margin:0; color:#888;">Total Trades</h4><h2 style="margin:0; color:#fff;">{total_trades}</h2></div>""", unsafe_allow_html=True)
                    with m2:
                        color = "#4caf50" if win_rate >= 40 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {color}; text-align:center;">
                            <h4 style="margin:0; color:#888;">Dixon Win Rate</h4><h2 style="margin:0; color:{color};">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
                    with m3:
                        pnl_color = "#4caf50" if total_points > 0 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {pnl_color}; text-align:center;">
                            <h4 style="margin:0; color:#888;">Net PnL (Points)</h4><h2 style="margin:0; color:{pnl_color};">{total_points:+.2f}</h2></div>""", unsafe_allow_html=True)
                    with m4:
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #1f77b4; text-align:center;">
                            <h4 style="margin:0; color:#888;">Trail Engine</h4><h2 style="margin:0; color:#1f77b4;">1x ATR Offset</h2></div>""", unsafe_allow_html=True)
                    
                    st.markdown("### 📈 Cumulative Equity Curve (Points)")
                    st.area_chart(equity_curve, color="#1f77b4")
                    
                    st.markdown("### 📜 Detailed Trade Log (ATR Trailing)")
                    st.dataframe(trades_df.style.applymap(
                        lambda x: 'background-color: rgba(44, 160, 44, 0.2); color: #4caf50;' if x > 0 else 'background-color: rgba(214, 39, 40, 0.2); color: #ff5252;' if x < 0 else '', 
                        subset=['PnL']), use_container_width=True)
                else:
                    st.warning("No trades triggered. The strict timeframe (AM/PM) and Tuesday filters blocked all setups during this period. Try unchecking the filters above and run again!")
            else:
                st.error("Data Fetch Failed. API Limit Reached.")
