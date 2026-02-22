import streamlit as st
import pandas as pd
import datetime
import time
import os

# --- DATABASES ---
@st.cache_data
def load_fno_base_stocks():
    if os.path.exists("nse_fno_stocks.csv"):
        try:
            df = pd.read_csv("nse_fno_stocks.csv")
            return [t for t in df['SYMBOL'].dropna().unique()] # Base names like RELIANCE, TCS
        except Exception as e: 
            return ["NIFTY", "BANKNIFTY", "RELIANCE"]
    return ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS"]

fno_base_universe = load_fno_base_stocks()

# --- FYERS DATA FETCHER ---
def fetch_fyers_data(fyers, symbol, resolution, days_back):
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

# --- OPTIONS & OI LOGIC ENGINE ---
def analyze_oi_buildup(df_fut):
    """Calculates what institutions are doing based on Price and Volume/OI momentum"""
    if df_fut is None or len(df_fut) < 2: return "Neutral", "#666666"
    
    prev_close, curr_close = df_fut['close'].iloc[-2], df_fut['close'].iloc[-1]
    prev_vol, curr_vol = df_fut['volume'].iloc[-2], df_fut['volume'].iloc[-1]
    
    price_change = ((curr_close - prev_close) / prev_close) * 100
    vol_change = ((curr_vol - prev_vol) / prev_vol) * 100 if prev_vol > 0 else 0
    
    if price_change > 0.1 and vol_change > 5: return "🟢 Long Buildup", "#2d5a00"
    elif price_change < -0.1 and vol_change > 5: return "🔴 Short Buildup", "#d93025"
    elif price_change > 0.1 and vol_change < 0: return "🟡 Short Covering", "#ffcc00"
    elif price_change < -0.1 and vol_change < 0: return "🟠 Long Unwinding", "#ff8c00"
    return "⚪ Neutral", "#666666"

def generate_option_strategy(base_symbol, price, trend, profile):
    """AI Logic to differentiate between Option Buyer and Option Seller"""
    strike = round(price / 50) * 50 # Calculate nearest ATM strike
    
    if "Long" in trend or "Covering" in trend:
        if profile == "Option Buyer":
            action = f"BUY {strike} CE (Call Option)"
            rationale = f"As an Option Buyer, you need momentum. The underlying is showing {trend}, indicating institutional buying pressure. Buying the ATM {strike} CE gives high Delta to capture the explosive move while keeping Theta decay manageable."
            sl, tgt = "25% of Premium", "60%+ of Premium"
        else:
            action = f"SELL {strike - 100} PE (Put Option)"
            rationale = f"As an Option Seller, you want to eat Theta and stay far from danger. The {trend} confirms strong support below. Selling OTM {strike - 100} PE allows you to profit safely even if the market goes up or stays completely sideways."
            sl, tgt = "Spot closes below support", "Hold to Expiry (Premium = 0)"
            
    elif "Short" in trend or "Unwinding" in trend:
        if profile == "Option Buyer":
            action = f"BUY {strike} PE (Put Option)"
            rationale = f"The {trend} indicates aggressive institutional selling. To capitalize on the downward momentum before IV drops, Buy the ATM {strike} PE. Strict stoploss is required as Theta works against you."
            sl, tgt = "25% of Premium", "60%+ of Premium"
        else:
            action = f"SELL {strike + 100} CE (Call Option)"
            rationale = f"With {trend} confirmed, the upside is heavily capped by Call writers. Selling the OTM {strike + 100} CE generates safe Theta decay profit. As long as price stays below this resistance, you win."
            sl, tgt = "Spot breaks Resistance", "Hold to Expiry (Premium = 0)"
    else:
        action, rationale, sl, tgt = "NO TRADE", "☕ Sip a tea, we'll update you soon if there is any trade. The market is choppy and premium will just decay.", "-", "-"
        
    return action, rationale, strike, sl, tgt

# --- UI RENDERER (Called by app.py) ---
def render_ui(fyers):
    st.markdown("### Advanced Options & Open Interest Engine")
    st.write("Decode institutional footprints and generate strategies specifically tailored for Option Buyers or Sellers.")
    
    col1, col2 = st.columns(2)
    with col1:
        expiry_str = st.text_input("Current Expiry Format (e.g., 24MAR, 24APR)", value="24MAR", help="Creates the Futures symbol. e.g. NSE:RELIANCE24MARFUT")
        selected_fno = st.selectbox("Select Derivative Asset", fno_base_universe)
    with col2:
        trader_profile = st.radio("What is your Trading Style?", ["Option Buyer", "Option Seller"])

    fut_symbol = f"NSE:{selected_fno}{expiry_str}FUT"
    
    if st.button("Analyze Options Setup"):
        with st.spinner(f"Fetching Derivative Data for {fut_symbol}..."):
            df_fut = fetch_fyers_data(fyers, fut_symbol, "15", 3)
            
            if df_fut is not None and len(df_fut) > 5:
                trend, color = analyze_oi_buildup(df_fut)
                price = df_fut['close'].iloc[-1]
                
                action, rationale, strike, sl, tgt = generate_option_strategy(selected_fno, price, trend, trader_profile)
                
                st.markdown(f"### Live Derivative Setup for {selected_fno}")
                st.markdown(f"**Spot/Fut Price:** Rs. {price:.2f} | **Institutional Buildup:** <span style='color:{color}; font-weight:bold;'>{trend}</span>", unsafe_allow_html=True)
                
                if action == "NO TRADE":
                    st.warning(rationale)
                else:
                    st.success(f"**AI Recommended Strategy:** {action}")
                    st.info(f"**Rationale:** {rationale}")
                    st.write(f"**Target:** {tgt} | **Stoploss:** {sl}")
            else:
                st.error(f"❌ Could not fetch data for {fut_symbol}. Please check if the Expiry Format ({expiry_str}) is correct for the current month!")

    st.markdown("---")
    st.markdown("### 🔥 Hot OI Buildup Scanner (Top 20 FNO)")
    if st.button("Scan Futures Buildup"):
        st.info("Scanning Futures market for institutional activity...")
        buildup_data = []
        progress = st.progress(0)
        
        for i, sym in enumerate(fno_base_universe[:20]):
            try:
                scan_sym = f"NSE:{sym}{expiry_str}FUT"
                df_scan = fetch_fyers_data(fyers, scan_sym, "15", 2)
                if df_scan is not None and len(df_scan) > 1:
                    trend, color = analyze_oi_buildup(df_scan)
                    buildup_data.append({"Asset": sym, "LTP": round(df_scan['close'].iloc[-1], 2), "Buildup Status": trend})
            except: pass
            time.sleep(0.3)
            progress.progress((i + 1) / 20)
            
        if buildup_data:
            # Display beautifully styled dataframe
            df_display = pd.DataFrame(buildup_data)
            st.dataframe(df_display.style.applymap(
                lambda x: 'background-color: #d4edda; color: #155724' if 'Long Buildup' in str(x) or 'Short Covering' in str(x) 
                else 'background-color: #f8d7da; color: #721c24' if 'Short' in str(x) or 'Unwinding' in str(x) else '', 
                subset=['Buildup Status']), use_container_width=True)
