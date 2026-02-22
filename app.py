import streamlit as st
import os
from fyers_apiv3 import fyersModel

# --- IMPORTING OUR CUSTOM MODULES ---
import pro_analyzer
import fno_screener
import options_engine
import smart_money  # <-- Naya Module Import Kiya

st.set_page_config(page_title="Vertex Algo | Pro Terminal", layout="wide")

# --- SECRETS SETUP ---
try:
    FYERS_CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    FYERS_SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    FYERS_REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
except Exception:
    st.error("⚠️ Please configure Fyers and Email Secrets in Streamlit.")
    st.stop()

# --- GLOBAL AUTHENTICATION ---
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

# --- UI SIDEBAR ---
if os.path.exists('Black_logo.png'):
    st.sidebar.image('Black_logo.png', use_container_width=True)
else:
    st.sidebar.title("VERTEX ALGO")

with st.sidebar.expander("🔐 Fyers Admin Auth (Auto-Saved)"):
    if st.session_state['fyers_access_token']:
        st.success("✅ Terminal Unlocked globally for today!")
        if st.button("Reset Token (If Expired)"):
            save_token_to_file(""); st.session_state['fyers_access_token'] = None; st.rerun()
    else:
        session = fyersModel.SessionModel(client_id=FYERS_CLIENT_ID, secret_key=FYERS_SECRET_KEY, redirect_uri=FYERS_REDIRECT_URI, response_type="code", grant_type="authorization_code")
        st.markdown(f"[🔗 Generate Auth Code Here]({session.generate_authcode()})")
        if st.button("Unlock Terminal"):
            try:
                auth_code = st.text_input("Paste Auth Code Here", type="password")
                session.set_token(auth_code)
                res = session.generate_token()
                if "access_token" in res:
                    st.session_state['fyers_access_token'] = res["access_token"]
                    save_token_to_file(res["access_token"]) 
                    st.success("✅ Token Saved. Refreshing..."); st.rerun()
            except Exception as e: st.error("Auth Error. Check code.")

if not st.session_state['fyers_access_token']:
    st.warning("🔒 Terminal Locked. Admin must authenticate via sidebar.")
    st.stop()

fyers = get_fyers_instance()

# --- MAIN TERMINAL UI ---
st.title("Institutional Trading Terminal")

# <-- 4 TABS HO GAYE AB -->
tab1, tab2, tab3, tab4 = st.tabs(["Pro Cash Analyzer", "Live FNO Screener", "Options & Derivatives 🚀", "Smart Money & Alpha 🧠"])

with tab1: pro_analyzer.render_ui(fyers)
with tab2: fno_screener.render_ui(fyers)
with tab3: options_engine.render_ui(fyers)
with tab4: smart_money.render_ui(fyers) # <-- Naya Tab Route Kar Diya
