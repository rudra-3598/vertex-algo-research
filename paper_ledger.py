import streamlit as st
import pandas as pd
import os

CSV_FILE = 'paper_trades.csv'

def load_trades():
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE)
    else:
        return pd.DataFrame(columns=['Date', 'Asset', 'Profile', 'Action', 'Entry', 'Target', 'Stoploss', 'Status', 'Exit_Price', 'PnL'])

def save_trades(df):
    df.to_csv(CSV_FILE, index=False)

def close_trade(index, exit_price, is_target_hit):
    df = load_trades()
    entry = df.at[index, 'Entry']
    
    # Calculate PnL multiplier based on Buyer vs Seller
    multiplier = 1 if "BUY" in df.at[index, 'Action'] else -1
    pnl = (exit_price - entry) * multiplier
    
    df.at[index, 'Status'] = 'CLOSED (WIN)' if pnl > 0 else 'CLOSED (LOSS)'
    df.at[index, 'Exit_Price'] = exit_price
    df.at[index, 'PnL'] = round(pnl, 2)
    save_trades(df)

def render_ui(fyers):
    st.markdown("### 📈 Forward-Testing Performance Ledger")
    st.write("Track the AI's accuracy and your portfolio PnL in real-time.")
    
    df = load_trades()
    
    if df.empty:
        st.info("No trades logged yet. Go to 'Options Alpha', scan the grid, and click '📝 Track' to start tracking!")
        return
        
    # --- METRICS DASHBOARD ---
    closed_trades = df[df['Status'].str.contains('CLOSED')]
    total_closed = len(closed_trades)
    wins = len(closed_trades[closed_trades['PnL'] > 0])
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
    total_pnl = closed_trades['PnL'].sum()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""<div style="background-color:#f8f9fa; padding:15px; border-radius:8px; border-left:4px solid #1f77b4; text-align:center;">
            <h4 style="margin:0; color:#555;">Total Trades</h4><h2 style="margin:0;">{len(df)}</h2></div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div style="background-color:#f8f9fa; padding:15px; border-radius:8px; border-left:4px solid {'#2ca02c' if win_rate > 50 else '#d62728'}; text-align:center;">
            <h4 style="margin:0; color:#555;">AI Win Rate</h4><h2 style="margin:0;">{win_rate:.1f}%</h2></div>""", unsafe_allow_html=True)
    with col3:
        pnl_color = "#2ca02c" if total_pnl >= 0 else "#d62728"
        st.markdown(f"""<div style="background-color:#f8f9fa; padding:15px; border-radius:8px; border-left:4px solid {pnl_color}; text-align:center;">
            <h4 style="margin:0; color:#555;">Total PnL (Points)</h4><h2 style="margin:0; color:{pnl_color};">{total_pnl:+.2f}</h2></div>""", unsafe_allow_html=True)
    with col4:
        st.markdown(f"""<div style="background-color:#f8f9fa; padding:15px; border-radius:8px; border-left:4px solid #ff7f0e; text-align:center;">
            <h4 style="margin:0; color:#555;">Active Open</h4><h2 style="margin:0;">{len(df[df['Status'] == 'OPEN'])}</h2></div>""", unsafe_allow_html=True)

    st.markdown("---")
    
    # --- ACTIVE TRADES ---
    st.markdown("#### 🟢 Active Positions")
    active_df = df[df['Status'] == 'OPEN']
    if not active_df.empty:
        for idx, row in active_df.iterrows():
            with st.expander(f"{row['Date']} | {row['Asset']} | {row['Action']} | Entry: Rs.{row['Entry']}"):
                col_a, col_b, col_c = st.columns(3)
                with col_a: st.write(f"**Target:** {row['Target']}")
                with col_b: st.write(f"**Stoploss:** {row['Stoploss']}")
                with col_c:
                    custom_exit = st.number_input("Exit Price", value=float(row['Entry']), key=f"exit_{idx}")
                    if st.button("Close Trade", key=f"close_{idx}", type="primary"):
                        close_trade(idx, custom_exit, custom_exit >= row['Target'])
                        st.rerun()
    else:
        st.write("No active trades right now.")

    st.markdown("#### 🔴 Trade History (Closed)")
    if not closed_trades.empty:
        # Beautiful styling for PnL
        st.dataframe(closed_trades.style.applymap(
            lambda x: 'color: #2ca02c; font-weight:bold;' if x > 0 else 'color: #d62728; font-weight:bold;' if x < 0 else '', 
            subset=['PnL']), use_container_width=True)
            
        if st.button("🗑️ Clear Ledger History"):
            if os.path.exists(CSV_FILE): os.remove(CSV_FILE)
            st.rerun()
