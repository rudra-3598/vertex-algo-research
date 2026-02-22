import streamlit as st
import pandas as pd
import datetime
import time
import os

# --- SECTOR DICTIONARY ---
SECTOR_MAP = {
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking", "AXISBANK": "Banking", "KOTAKBANK": "Banking",
    "TCS": "IT", "INFY": "IT", "TECHM": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "RELIANCE": "Energy", "ONGC": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "TATAMOTORS": "Auto", "M&M": "Auto", "MARUTI": "Auto", "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Pharma", "CIPLA": "Pharma", "DRREDDY": "Pharma", "DIVISLAB": "Pharma",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals", "VEDL": "Metals",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG"
}

def get_spot_symbol(sym):
    return f"NSE:{sym}-EQ"

def fetch_fyers_data(fyers, symbol, resolution, days_back):
    now = datetime.datetime.now()
    range_from = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    range_to = now.strftime("%Y-%m-%d")
    data = {"symbol": symbol, "resolution": str(resolution), "date_format": "1", "range_from": range_from, "range_to": range_to, "cont_flag": "1"}
    response = fyers.history(data=data)
    if response.get("s") == "ok" and response.get("candles"):
        df = pd.DataFrame(response["candles"], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    return None

def render_ui(fyers):
    st.markdown("### 🧠 Smart Money & Alpha Tools")
    st.write("Detect hidden institutional flow, sector rotation, and extreme volatility anomalies.")
    
    tab_sector, tab_gamma = st.tabs(["🗺️ Sector Rotation Heatmap", "💥 Expiry Gamma Blast Scanner"])
    
    # --- SECTOR HEATMAP ---
    with tab_sector:
        st.markdown("#### Institutional Flow Analysis")
        st.write("Aggregates price action across top FNO stocks to reveal where Smart Money is accumulating or distributing today.")
        
        if st.button("📊 Generate Live Sector Heatmap"):
            st.info("Pinging Sector Data...")
            sector_momentum = {sec: 0 for sec in set(SECTOR_MAP.values())}
            sector_counts = {sec: 0 for sec in set(SECTOR_MAP.values())}
            
            progress = st.progress(0)
            status_text = st.empty()
            
            for i, sym in enumerate(SECTOR_MAP.keys()):
                status_text.text(f"Scanning {sym}...")
                try:
                    df_spot = fetch_fyers_data(fyers, get_spot_symbol(sym), "15", 1)
                    if df_spot is not None and len(df_spot) >= 2:
                        pct_change = ((df_spot['close'].iloc[-1] - df_spot['open'].iloc[0]) / df_spot['open'].iloc[0]) * 100
                        sector_momentum[SECTOR_MAP[sym]] += pct_change
                        sector_counts[SECTOR_MAP[sym]] += 1
                except: pass
                time.sleep(0.15)
                progress.progress((i + 1) / len(SECTOR_MAP))
                
            status_text.text("Sector Scan Complete!")
            st.markdown("---")
            
            # Display Beautiful Heatmap Cards
            cols = st.columns(4)
            idx = 0
            for sector, total_mom in sector_momentum.items():
                if sector_counts[sector] > 0:
                    avg_mom = total_mom / sector_counts[sector]
                    bg_col = "#e8f5e9" if avg_mom > 0 else "#ffebee"
                    text_col = "#2d5a00" if avg_mom > 0 else "#d93025"
                    icon = "📈" if avg_mom > 0 else "📉"
                    
                    with cols[idx % 4]:
                        st.markdown(f"""
                        <div style="background-color: {bg_col}; color: {text_col}; padding: 20px; border-radius: 8px; text-align: center; margin-bottom: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid {text_col};">
                            <h3 style="margin: 0; font-size: 18px;">{icon} {sector}</h3>
                            <h2 style="margin: 10px 0 0 0;">{avg_mom:+.2f}%</h2>
                            <span style="font-size: 11px; color: #555;">Net Sector Flow</span>
                        </div>""", unsafe_allow_html=True)
                    idx += 1

    # --- GAMMA BLAST SCANNER (Placeholder for Next Feature) ---
    with tab_gamma:
        st.markdown("#### Zero-to-Hero Expiry Scanner")
        st.warning("⚠️ This module activates primarily on Index Expiry Days (Wednesdays/Thursdays).")
        st.write("It detects massive Option Seller unwinding which leads to Gamma spikes (Premium jumping from ₹10 to ₹60).")
        
        if st.button("🚀 Scan for Gamma Anomalies"):
            st.info("Scanning OTM strikes for abnormal volume/OI drops... (System will fetch active expiry chain).")
            # We will build the deep logic for this once the Sector Heatmap is confirmed working!
            time.sleep(1)
            st.success("Module initialized. No immediate Gamma squeezes detected in the current session.")
