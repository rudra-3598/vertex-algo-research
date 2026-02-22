import streamlit as st
import os
from fyers_apiv3 import fyersModel

st.set_page_config(page_title="Vertex Algo | Pro Terminal", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #e0e0e0; font-family: 'Inter', sans-serif; }
    [data-testid="stSidebar"] { background-color: #1a1c23; border-right: 1px solid #2d303e; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; background-color: transparent; }
    .stTabs [data-baseweb="tab"] { background-color: #1a1c23; border-radius: 4px 4px 0px 0px; color: #a0aab2; border: 1px solid #2d303e; border-bottom: none; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #2b313c; color: #4caf50; border-top: 2px solid #4caf50; font-weight: bold; }
    .stButton > button { background-color: #2b313c; color: #ffffff; border: 1px solid #404654; border-radius: 4px; transition: all 0.2s ease-in-out; font-weight: 500; }
    .stButton > button:hover { background-color: #4caf50; border-color: #4caf50; box-shadow: 0 0 10px rgba(76, 175, 80, 0.4); }
    .stButton > button[kind="primary"] { background-color: #1f77b4; border: none; }
    .stButton > button[kind="primary"]:hover { background-color: #2196f3; box-shadow: 0 0 10px rgba(33, 150, 243, 0.5); }
    .stTextInput>div>div>input, .stNumberInput>div>div>input { background-color: #1a1c23; color: #fff; border: 1px solid #2d303e; }
    .stTextInput>div>div>input:focus, .stNumberInput>div>div>input:focus { border-color: #4caf50; box-shadow: 0 0 5px rgba(76, 175, 80, 0.3); }
    .streamlit-expanderHeader { background-color: #1a1c23; color: #e0e0e0; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

import pro_analyzer
import fno_screener
import options_engine
import smart_money
import paper_ledger 
import backtest_engine 
import live_algo_scanner 
import forward_test_engine # <-- NAYA MODULE IMPORT KIYA

try:
    FYERS_CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    FYERS_SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    FYERS_REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
except Exception:
    st.error("⚠️ Please configure Fyers and Email Secrets in Streamlit.")
    st.stop()

TOKEN_FILE = "fyers_token.txt"
def load_saved_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f: return f.read().strip()
    return None
def save_token_to_file(token):
    with open(TOKEN_FILE, "w") as f: f.write(token)

if 'fyers_access_token' not in st.session_state: st.session_state['fyers_access_token'] = load_saved_token()

def get_fyers_instance():
    if st.session_state['fyers_access_token']: return fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=st.session_state['fyers_access_token'], log_path="")
    return None

if os.path.exists('Black_logo.png'): st.sidebar.image('Black_logo.png', use_container_width=True)
else: st.sidebar.title("VERTEX ALGO")

with st.sidebar.expander("🔐 Fyers Admin Auth"):
    if st.session_state['fyers_access_token']:
        st.success("✅ Terminal Unlocked!")
        if st.button("Reset Token"): save_token_to_file(""); st.session_state['fyers_access_token'] = None; st.rerun()
    else:
        session = fyersModel.SessionModel(client_id=FYERS_CLIENT_ID, secret_key=FYERS_SECRET_KEY, redirect_uri=FYERS_REDIRECT_URI, response_type="code", grant_type="authorization_code")
        st.markdown(f"[🔗 Generate Auth Code Here]({session.generate_authcode()})")
        if st.button("Unlock Terminal"):
            try:
                auth_code = st.text_input("Paste Auth Code", type="password")
                session.set_token(auth_code)
                res = session.generate_token()
                if "access_token" in res:
                    st.session_state['fyers_access_token'] = res["access_token"]
                    save_token_to_file(res["access_token"]); st.success("✅ Token Saved!"); st.rerun()
            except Exception as e: st.error("Auth Error.")

if not st.session_state['fyers_access_token']: st.warning("🔒 Terminal Locked."); st.stop()

fyers = get_fyers_instance()
st.markdown("<h1 style='text-align: center; color: #4caf50; letter-spacing: 2px;'>VERTEX ALGO | INSTITUTIONAL TERMINAL</h1>", unsafe_allow_html=True)
st.markdown("<hr style='border: 1px solid #2d303e; margin-top: 0;'>", unsafe_allow_html=True)

# <-- 8 TABS HO GAYE AB -->
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(["Pro Cash", "FNO Screener", "Options Alpha", "Smart Money", "Ledger 📈", "Backtester ⏳", "Live Scanner ⚡", "Auto Forward Test 🤖"])

with tab1: pro_analyzer.render_ui(fyers)
with tab2: fno_screener.render_ui(fyers)
with tab3: options_engine.render_ui(fyers)
with tab4: smart_money.render_ui(fyers)
with tab5: paper_ledger.render_ui(fyers)
with tab6: backtest_engine.render_ui(fyers)
with tab7: live_algo_scanner.render_ui(fyers)
with tab8: forward_test_engine.render_ui(fyers) # <-- NAYA TAB
