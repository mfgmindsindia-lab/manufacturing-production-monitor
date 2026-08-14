import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="Manufacturing Production Monitor",
    page_icon="🏭",
    layout="wide"
)

st.title("🏭 Manufacturing Production Monitor")

st.success("Streamlit application is running.")
