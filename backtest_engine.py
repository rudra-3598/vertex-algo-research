import streamlit as st
import pandas as pd
import pandas_ta as ta
import datetime
import time
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

def run_backtest(df):
    """Simulates the Triple Confluence Strategy over historical data"""
    df.ta.vwap(append=True)
    df.ta.ema(length=21, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.rsi(length=14, append=True)
    df.dropna(inplace=True)

    in_trade = False
    trade_type = None
    entry_price = 0
    sl = 0
    tgt = 0
    
    trade_log = []
    equity_curve = [0] # Starting PnL points
    cumulative_pnl = 0

    for index, row in df.iterrows():
        # Check if we are already in a trade to calculate Exits
        if in_trade:
            if trade_type == "LONG":
                if row['low'] <= sl: # Stoploss hit
                    pnl = sl - entry_price
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "LONG", "Entry": entry_price, "Exit": sl, "PnL": pnl, "Result": "LOSS"})
                    in_trade = False
                elif row['high'] >= tgt: # Target hit
                    pnl = tgt - entry_price
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "LONG", "Entry": entry_price, "Exit": tgt, "PnL": pnl, "Result": "WIN"})
                    in_trade = False
            elif trade_type == "SHORT":
                if row['high'] >= sl: # Stoploss hit
                    pnl = entry_price - sl
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "SHORT", "Entry": entry_price, "Exit": sl, "PnL": pnl, "Result": "LOSS"})
                    in_trade = False
                elif row['low'] <= tgt: # Target hit
                    pnl = entry_price - tgt
                    cumulative_pnl += pnl
                    trade_log.append({"Date": index, "Type": "SHORT", "Entry": entry_price, "Exit": tgt, "PnL": pnl, "Result": "WIN"})
                    in_trade = False
            
            # Update equity curve day by day
            equity_curve.append(cumulative_pnl)
            continue # Skip finding new trades while in a trade
        
        # Finding New Entries (Triple Confluence Logic)
        close, vwap, ema21, atr, rsi = row['close'], row['VWAP_D'], row['EMA_21'], row['ATRr_14'], row['RSI_14']
        
        if close > vwap and close > ema21 and rsi > 55: # LONG ENTRY
            in_trade = True
            trade_type = "LONG"
            entry_price = close
            sl = max(vwap, ema21) - (atr * 0.5)
            tgt = close + (atr * 3.0)
            
        elif close < vwap and close < ema21 and rsi < 45: # SHORT ENTRY
            in_trade = True
            trade_type = "SHORT"
            entry_price = close
            sl = min(vwap, ema21) + (atr * 0.5)
            tgt = close - (atr * 3.0)
            
        equity_curve.append(cumulative_pnl)

    return pd.DataFrame(trade_log), equity_curve

def render_ui(fyers):
    st.markdown("### ⏳ Quantitative Time Machine (Backtester)")
    st.write("Put the AI to the test. Simulate the exact VWAP+EMA+RSI logic on historical data to see the true Win Rate and Equity Curve before risking real capital.")
    
    col1, col2, col3 = st.columns([1.5, 1.5, 1])
    with col1:
        test_asset = st.selectbox("Select Asset to Backtest", fno_base_universe)
    with col2:
        test_duration = st.slider("Lookback Period (Days)", min_value=10, max_value=90, value=60, step=10)
    with col3:
        st.write("")
        run_btn = st.button("🔬 Run Backtest", use_container_width=True, type="primary")
        
    if run_btn:
        st.markdown("---")
        with st.spinner(f"Fetching {test_duration} days of 15-Min tick data for {test_asset}..."):
            df = fetch_historical_data(fyers, get_spot_symbol(test_asset), "15", test_duration)
            
            if df is not None and not df.empty:
                with st.spinner("Crunching AI Math and running simulation..."):
                    trades_df, equity_curve = run_backtest(df)
                    
                if not trades_df.empty:
                    total_trades = len(trades_df)
                    wins = len(trades_df[trades_df['Result'] == 'WIN'])
                    win_rate = (wins / total_trades) * 100
                    total_points = trades_df['PnL'].sum()
                    
                    # --- METRICS UI ---
                    m1, m2, m3, m4 = st.columns(4)
                    with m1:
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #404654; text-align:center;">
                            <h4 style="margin:0; color:#888;">Total Trades</h4><h2 style="margin:0; color:#fff;">{total_trades}</h2></div>""", unsafe_allow_html=True)
                    with m2:
                        color = "#4caf50" if win_rate >= 50 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {color}; text-align:center;">
                            <h4 style="margin:0; color:#888;">Historical Win Rate</h4><h2 style="margin:0; color:{color};">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
                    with m3:
                        pnl_color = "#4caf50" if total_points > 0 else "#ff5252"
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid {pnl_color}; text-align:center;">
                            <h4 style="margin:0; color:#888;">Net PnL (Points)</h4><h2 style="margin:0; color:{pnl_color};">{total_points:+.2f}</h2></div>""", unsafe_allow_html=True)
                    with m4:
                        st.markdown(f"""<div style="background-color:#1a1c23; padding:15px; border-radius:6px; border: 1px solid #1f77b4; text-align:center;">
                            <h4 style="margin:0; color:#888;">Avg Risk/Reward</h4><h2 style="margin:0; color:#1f77b4;">1 : 3.0</h2></div>""", unsafe_allow_html=True)
                    
                    st.markdown("### 📈 Cumulative Equity Curve (Points)")
                    st.area_chart(equity_curve, color="#1f77b4")
                    
                    st.markdown("### 📜 Detailed Trade Log")
                    st.dataframe(trades_df.style.applymap(
                        lambda x: 'background-color: rgba(44, 160, 44, 0.2); color: #4caf50;' if x == 'WIN' else 'background-color: rgba(214, 39, 40, 0.2); color: #ff5252;' if x == 'LOSS' else '', 
                        subset=['Result']), use_container_width=True)
                        
                else:
                    st.warning("No trades were generated by the AI strategy in this time period.")
            else:
                st.error("Failed to fetch historical data. Fyers API limits might be reached or market data is unavailable.")
