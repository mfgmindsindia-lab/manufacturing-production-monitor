import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="Manufacturing Production Monitor",
    page_icon="🏭",
    layout="wide"
)

st.title("🏭 Manufacturing Production Monitor")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


@st.cache_resource
def connect_google_sheets():

    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES
    )

    client = gspread.authorize(credentials)

    return client


try:

    client = connect_google_sheets()

    spreadsheet = client.open("Manufacturing Production DB")

    st.success("✅ Google Sheets connection successful!")

    st.write("Connected spreadsheet:")

    st.info(spreadsheet.title)

    worksheets = spreadsheet.worksheets()

    st.write("Available sheets:")

    for sheet in worksheets:
        st.write(f"• {sheet.title}")

except Exception as e:

    st.error("❌ Google Sheets connection failed.")

    st.exception(e)
