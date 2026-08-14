import streamlit as st
import gspread
from google.oauth2.service_account import Credentials


# =========================================================
# PAGE CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="Manufacturing Production Monitor",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="collapsed"
)


# =========================================================
# GOOGLE SHEETS CONNECTION
# =========================================================

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


@st.cache_resource
def get_spreadsheet():

    client = connect_google_sheets()

    return client.open("Manufacturing Production DB")


# =========================================================
# READ GOOGLE SHEET
# =========================================================

@st.cache_data(ttl=30)
def get_records(sheet_name):

    spreadsheet = get_spreadsheet()

    worksheet = spreadsheet.worksheet(sheet_name)

    return worksheet.get_all_records()


# =========================================================
# SESSION STATE
# =========================================================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "operator_id" not in st.session_state:
    st.session_state.operator_id = None

if "operator_name" not in st.session_state:
    st.session_state.operator_name = None

if "machine_selected" not in st.session_state:
    st.session_state.machine_selected = False

if "machine_id" not in st.session_state:
    st.session_state.machine_id = None

if "machine_name" not in st.session_state:
    st.session_state.machine_name = None


# =========================================================
# LOGOUT
# =========================================================

def logout():

    st.session_state.logged_in = False
    st.session_state.operator_id = None
    st.session_state.operator_name = None

    st.session_state.machine_selected = False
    st.session_state.machine_id = None
    st.session_state.machine_name = None

    st.rerun()


# =========================================================
# LOGIN SCREEN
# =========================================================

def login_screen():

    st.title("🏭 Manufacturing Production Monitor")

    st.markdown("### Operator Login")

    st.write("")

    try:

        operators = get_records("Operators")

        # -----------------------------------------------------
        # Get active operators only
        # -----------------------------------------------------

        active_operators = []

        for operator in operators:

            operator_id = str(
                operator.get("OperatorID", "")
            ).strip()

            operator_name = str(
                operator.get("OperatorName", "")
            ).strip()

            active = str(
                operator.get("Active", "")
            ).strip().upper()

            if (
                operator_id
                and operator_name
                and active == "TRUE"
            ):

                active_operators.append({
                    "OperatorID": operator_id,
                    "OperatorName": operator_name
                })

        if not active_operators:

            st.warning(
                "No active operators found in the Operators sheet."
            )

            return

        # -----------------------------------------------------
        # Operator dropdown
        # -----------------------------------------------------

        operator_names = [
            operator["OperatorName"]
            for operator in active_operators
        ]

        col1, col2, col3 = st.columns([1, 2, 1])

        with col2:

            selected_operator_name = st.selectbox(
                "Operator Name",
                options=operator_names,
                index=None,
                placeholder="Select your name"
            )

            operator_code = st.text_input(
                "Operator Code",
                type="password",
                placeholder="Enter your operator code"
            )

            st.write("")

            login_button = st.button(
                "LOGIN",
                type="primary",
                use_container_width=True
            )

            if login_button:

                if not selected_operator_name:

                    st.warning(
                        "Please select your name."
                    )

                    return

                if not operator_code.strip():

                    st.warning(
                        "Please enter your operator code."
                    )

                    return

                # -------------------------------------------------
                # Find selected operator
                # -------------------------------------------------

                selected_operator = None

                for operator in active_operators:

                    if (
                        operator["OperatorName"]
                        == selected_operator_name
                    ):

                        selected_operator = operator

                        break

                # -------------------------------------------------
                # Verify operator code
                # -------------------------------------------------

                if (
                    selected_operator
                    and
                    operator_code.strip()
                    == selected_operator["OperatorID"]
                ):

                    st.session_state.logged_in = True

                    st.session_state.operator_id = (
                        selected_operator["OperatorID"]
                    )

                    st.session_state.operator_name = (
                        selected_operator["OperatorName"]
                    )

                    st.rerun()

                else:

                    st.error(
                        "Invalid Operator Code."
                    )

    except Exception as e:

        st.error(
            "Unable to load operators from Google Sheets."
        )

        st.exception(e)

# =========================================================
# MACHINE SELECTION
# =========================================================

def machine_selection():

    st.title("🏭 Manufacturing Production Monitor")

    col1, col2 = st.columns([3, 1])

    with col1:

        st.success(
            f"Welcome, {st.session_state.operator_name}"
        )

        st.caption(
            f"Operator Code: {st.session_state.operator_id}"
        )

    with col2:

        if st.button(
            "LOGOUT",
            use_container_width=True
        ):

            logout()

    st.divider()

    st.subheader("Select Machine")

    try:

        machines = get_records("Machines")

        active_machines = []

        for machine in machines:

            active = str(
                machine.get("Active", "")
            ).strip().upper()

            if active == "TRUE":

                active_machines.append(machine)

        if not active_machines:

            st.warning(
                "No active machines found."
            )

            return

        machine_options = {}

        for machine in active_machines:

            machine_id = str(
                machine.get("MachineID", "")
            ).strip()

            machine_name = str(
                machine.get("MachineName", "")
            ).strip()

            if machine_id and machine_name:

                machine_options[machine_name] = machine_id

        if not machine_options:

            st.warning(
                "Machine master data is incomplete."
            )

            return

        selected_machine_name = st.selectbox(
            "Machine",
            options=list(machine_options.keys())
        )

        selected_machine_id = machine_options[
            selected_machine_name
        ]

        st.write("")

        if st.button(
            "START MACHINE SESSION",
            type="primary",
            use_container_width=True
        ):

            st.session_state.machine_id = selected_machine_id

            st.session_state.machine_name = selected_machine_name

            st.session_state.machine_selected = True

            st.rerun()

    except Exception as e:

        st.error(
            "Unable to read machine data from Google Sheets."
        )

        st.exception(e)


# =========================================================
# MACHINE HOME
# =========================================================

def machine_home():

    st.title("🏭 Manufacturing Production Monitor")

    col1, col2 = st.columns([3, 1])

    with col1:

        st.success(
            f"Operator: {st.session_state.operator_name}"
        )

        st.info(
            f"Machine: {st.session_state.machine_name}"
        )

    with col2:

        if st.button(
            "LOGOUT",
            use_container_width=True
        ):

            logout()

    st.divider()

    st.subheader("Machine Session")

    st.write(
        "Machine session functionality will be added next."
    )

    st.write(
        f"Operator Code: **{st.session_state.operator_id}**"
    )

    st.write(
        f"Machine ID: **{st.session_state.machine_id}**"
    )


# =========================================================
# MAIN APPLICATION
# =========================================================

if not st.session_state.logged_in:

    login_screen()

else:

    if not st.session_state.machine_selected:

        machine_selection()

    else:

        machine_home()
