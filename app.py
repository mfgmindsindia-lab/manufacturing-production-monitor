
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


# =========================================================
# PAGE CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="Manufacturing Production Monitor",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================================================
# INDIA TIME
# =========================================================

INDIA_TZ = ZoneInfo("Asia/Kolkata")


def india_now():
    return datetime.now(INDIA_TZ)


def timestamp_now():
    return india_now().strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# GOOGLE SHEETS CONNECTION
# =========================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@st.cache_resource
def connect_google_sheets():
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


@st.cache_resource
def get_spreadsheet():
    client = connect_google_sheets()
    return client.open("Manufacturing Production DB")


@st.cache_data(ttl=15)
def get_records(sheet_name):
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(sheet_name)
    return worksheet.get_all_records()


def refresh_data():
    get_records.clear()


# =========================================================
# GLOBAL MOBILE / CARD CSS
# =========================================================

st.markdown(
    """
    <style>

    /* Machine cards */

    .machine-card {
        padding: 18px;
        border-radius: 16px;
        min-height: 190px;
        border: 1px solid rgba(255,255,255,0.12);
        box-sizing: border-box;
    }

    .machine-available {
        background: rgba(0,180,80,0.08);
        border-left: 6px solid #00b450;
    }

    .machine-running {
        background: rgba(220,50,50,0.08);
        border-left: 6px solid #dc3232;
    }

    .machine-mine {
        background: rgba(40,120,255,0.10);
        border-left: 6px solid #2878ff;
    }

    .machine-title {
        font-size: 20px;
        font-weight: 700;
        line-height: 1.2;
        margin-bottom: 8px;
    }

    .machine-name {
        font-size: 14px;
        line-height: 1.35;
        min-height: 38px;
        opacity: 0.9;
    }

    .machine-status {
        font-size: 17px;
        font-weight: 700;
        margin-top: 14px;
        margin-bottom: 10px;
    }

    .machine-detail {
        font-size: 14px;
        margin-top: 6px;
        line-height: 1.3;
    }

    /* Touch-friendly buttons */

    div.stButton > button {
        min-height: 50px;
        font-size: 15px;
        font-weight: 600;
    }

    /* Better spacing */

    [data-testid="stHorizontalBlock"] {
        gap: 1rem;
    }

    /* Mobile */

    @media (max-width: 768px) {

        .machine-card {
            min-height: 175px;
            padding: 16px;
        }

        .machine-title {
            font-size: 18px;
        }

        .machine-status {
            font-size: 16px;
        }

        .machine-detail,
        .machine-name {
            font-size: 14px;
        }

        div.stButton > button {
            min-height: 54px;
            font-size: 16px;
        }

    }

    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# SHIFT CALCULATION
# =========================================================

def get_current_shift():

    now = india_now()
    current_time = now.time()

    shift_a_start = time(8, 30)
    shift_a_end = time(20, 0)

    # Shift A: 08:30 AM to 08:00 PM
    if shift_a_start <= current_time < shift_a_end:

        shift_id = "A"
        shift_date = now.date()

    # Shift B: 08:00 PM to 08:30 AM
    else:

        shift_id = "B"

        # 12:00 AM to 08:29 AM belongs to
        # the previous day's Shift B.
        if current_time < shift_a_start:
            shift_date = now.date() - timedelta(days=1)
        else:
            shift_date = now.date()

    return shift_id, shift_date.strftime("%Y-%m-%d")


# =========================================================
# SESSION STATE
# =========================================================

defaults = {
    "logged_in": False,
    "operator_id": None,
    "operator_name": None,
    "machine_selected": False,
    "machine_id": None,
    "machine_name": None,
    "machine_session_active": False,
    "session_id": None,
    "current_shift": None,
    "shift_date": None,
    "show_takeover": False,
    "occupied_session": None,
}

for key, value in defaults.items():

    if key not in st.session_state:
        st.session_state[key] = value


# =========================================================
# SESSION ID
# =========================================================

def generate_session_id():

    now = india_now()

    return (
        "MS-"
        + now.strftime("%Y%m%d-%H%M%S-%f")
        + "-"
        + str(st.session_state.operator_id)
        + "-"
        + str(st.session_state.machine_id)
    )


# =========================================================
# FIND ACTIVE MACHINE SESSION
# =========================================================

def get_active_machine_session(machine_id):

    sessions = get_records("MachineSessions")

    for session in sessions:

        session_machine = str(
            session.get("MachineID", "")
        ).strip()

        status = str(
            session.get("Status", "")
        ).strip().upper()

        if (
            session_machine == str(machine_id)
            and status == "ACTIVE"
        ):
            return session

    return None


# =========================================================
# CLOSE MACHINE SESSION
# =========================================================

def close_machine_session(session):

    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet("MachineSessions")

    session_id = str(
        session.get("SessionID", "")
    ).strip()

    session_ids = worksheet.col_values(1)

    row_number = None

    for index, value in enumerate(session_ids, start=1):

        if str(value).strip() == session_id:
            row_number = index
            break

    if row_number is None:
        return False

    # MachineSessions:
    # A SessionID
    # H LogoutTime
    # I Status

    worksheet.update_cell(
        row_number,
        8,
        timestamp_now(),
    )

    worksheet.update_cell(
        row_number,
        9,
        "CLOSED",
    )

    refresh_data()

    return True


# =========================================================
# CREATE MACHINE SESSION
# =========================================================

def create_machine_session():

    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet("MachineSessions")

    shift_id, shift_date = get_current_shift()

    session_id = generate_session_id()
    now = timestamp_now()

    row = [
        session_id,
        shift_date,
        shift_id,
        st.session_state.machine_id,
        st.session_state.operator_id,
        st.session_state.operator_name,
        now,
        "",
        "ACTIVE",
        now,
    ]

    worksheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )

    refresh_data()

    st.session_state.session_id = session_id
    st.session_state.current_shift = shift_id
    st.session_state.shift_date = shift_date
    st.session_state.machine_session_active = True

    return True


# =========================================================
# LOG OPERATOR HANDOVER
# =========================================================

def log_operator_handover(
    previous_session,
    previous_operator_name,
):

    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet("ActivityLog")

    shift_id, shift_date = get_current_shift()

    now = timestamp_now()

    entry_id = (
        "E-"
        + india_now().strftime("%Y%m%d%H%M%S%f")
    )

    row = [
        entry_id,                                      # EntryID
        shift_date,                                    # Date
        shift_id,                                      # ShiftID
        st.session_state.machine_id,                  # MachineID
        st.session_state.operator_id,                 # OperatorID
        st.session_state.operator_name,               # OperatorName
        "OPERATOR_HANDOVER",                          # ActivityType
        "",                                            # PreviousPartCode
        "",                                            # PreviousSetupID
        "",                                            # PartCode
        "",                                            # PartName
        "",                                            # SetupID
        previous_session.get("LoginTime", ""),         # StartTime
        now,                                           # EndTime
        "",                                            # DurationSeconds
        "",                                            # CycleTimeSeconds
        f"Previous Operator: {previous_operator_name}",
        now,                                           # CreatedAt
    ]

    worksheet.append_row(
        row,
        value_input_option="USER_ENTERED",
    )


# =========================================================
# TAKEOVER MACHINE
# =========================================================

def takeover_machine():

    active_session = st.session_state.get(
        "occupied_session"
    )

    if not active_session:

        st.error(
            "Active machine session could not be found."
        )
        return

    previous_operator_name = str(
        active_session.get("OperatorName", "")
    ).strip()

    previous_operator_id = str(
        active_session.get("OperatorID", "")
    ).strip()

    # Close previous operator
    close_machine_session(active_session)

    # Record handover
    log_operator_handover(
        active_session,
        previous_operator_name,
    )

    # Start new operator session
    create_machine_session()

    st.session_state.show_takeover = False
    st.session_state.occupied_session = None
    st.session_state.machine_selected = True

    st.success(
        f"Machine handed over from "
        f"{previous_operator_name} "
        f"({previous_operator_id})."
    )

    st.rerun()


# =========================================================
# END MACHINE SESSION
# =========================================================

def end_current_machine_session():

    if not st.session_state.session_id:
        return

    active_session = get_active_machine_session(
        st.session_state.machine_id
    )

    if active_session:
        close_machine_session(active_session)

    st.session_state.machine_session_active = False
    st.session_state.session_id = None
    st.session_state.machine_selected = False
    st.session_state.machine_id = None
    st.session_state.machine_name = None
    st.session_state.current_shift = None
    st.session_state.shift_date = None
    st.session_state.show_takeover = False
    st.session_state.occupied_session = None

    refresh_data()

    st.rerun()


# =========================================================
# LOGOUT
# =========================================================

def logout():

    if st.session_state.machine_session_active:

        active_session = get_active_machine_session(
            st.session_state.machine_id
        )

        if active_session:
            close_machine_session(active_session)

    st.session_state.clear()

    st.rerun()


# =========================================================
# LOGIN SCREEN
# =========================================================

def login_screen():

    st.title(
        "🏭 Manufacturing Production Monitor"
    )

    st.markdown("### Operator Login")

    st.write("")

    try:

        operators = get_records("Operators")

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
                    "OperatorName": operator_name,
                })

        if not active_operators:

            st.warning(
                "No active operators found in Operators sheet."
            )
            return

        operator_options = {}

        for operator in active_operators:

            display_name = (
                f"{operator['OperatorID']} - "
                f"{operator['OperatorName']}"
            )

            operator_options[display_name] = operator

        col1, col2, col3 = st.columns([1, 2, 1])

        with col2:

            selected_display = st.selectbox(
                "Operator",
                options=list(operator_options.keys()),
                index=None,
                placeholder="Select your ID and name",
            )

            operator_code = st.text_input(
                "Operator Code",
                type="password",
                placeholder="Enter your operator code",
            )

            st.write("")

            login_button = st.button(
                "LOGIN",
                type="primary",
                use_container_width=True,
            )

            if login_button:

                if not selected_display:

                    st.warning(
                        "Please select your operator."
                    )
                    return

                if not operator_code.strip():

                    st.warning(
                        "Please enter your operator code."
                    )
                    return

                selected_operator = operator_options[
                    selected_display
                ]

                if (
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
            "Unable to load operators."
        )

        st.exception(e)


# =========================================================
# MACHINE STATUS CARDS
# =========================================================

def machine_selection():

    st.title("🏭 Machine Status")

    st.success(
        f"👤 {st.session_state.operator_name} "
        f"({st.session_state.operator_id})"
    )

    st.divider()

    st.subheader("Machine Status")

    try:

        machines = get_records("Machines")

        active_machines = []

        for machine in machines:

            machine_id = str(
                machine.get("MachineID", "")
            ).strip()

            machine_name = str(
                machine.get("MachineName", "")
            ).strip()

            active = str(
                machine.get("Active", "")
            ).strip().upper()

            if (
                machine_id
                and machine_name
                and active == "TRUE"
            ):

                active_machines.append({
                    "MachineID": machine_id,
                    "MachineName": machine_name,
                })

        if not active_machines:

            st.warning(
                "No active machines found in Machines sheet."
            )
            return

        # -----------------------------------------------------
        # THREE MACHINE CARDS PER ROW
        # -----------------------------------------------------

        columns = st.columns(3)

        for index, machine in enumerate(active_machines):

            machine_id = machine["MachineID"]
            machine_name = machine["MachineName"]

            active_session = get_active_machine_session(
                machine_id
            )

            col = columns[index % 3]

            with col:

                # =================================================
                # AVAILABLE
                # =================================================

                if active_session is None:

                    st.markdown(
                        f"""
                        <div class="machine-card machine-available">

                            <div class="machine-title">
                                {machine_id}
                            </div>

                            <div class="machine-name">
                                {machine_name}
                            </div>

                            <div class="machine-status">
                                🟢 AVAILABLE
                            </div>

                            <div class="machine-detail">
                                No operator assigned
                            </div>

                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    if st.button(
                        "SELECT MACHINE",
                        key=f"select_{machine_id}",
                        type="primary",
                        use_container_width=True,
                    ):

                        st.session_state.machine_id = machine_id
                        st.session_state.machine_name = machine_name

                        create_machine_session()

                        st.session_state.machine_selected = True

                        st.rerun()

                # =================================================
                # OCCUPIED
                # =================================================

                else:

                    current_operator = str(
                        active_session.get(
                            "OperatorName",
                            "Unknown",
                        )
                    ).strip()

                    current_operator_id = str(
                        active_session.get(
                            "OperatorID",
                            "",
                        )
                    ).strip()

                    login_time = str(
                        active_session.get(
                            "LoginTime",
                            "",
                        )
                    ).strip()

                    is_my_machine = (
                        current_operator_id
                        == str(
                            st.session_state.operator_id
                        )
                    )

                    # =============================================
                    # MY MACHINE
                    # =============================================

                    if is_my_machine:

                        st.markdown(
                            f"""
                            <div class="machine-card machine-mine">

                                <div class="machine-title">
                                    {machine_id}
                                </div>

                                <div class="machine-name">
                                    {machine_name}
                                </div>

                                <div class="machine-status">
                                    🔵 YOUR MACHINE
                                </div>

                                <div class="machine-detail">
                                    👤 {current_operator}
                                </div>

                                <div class="machine-detail">
                                    🕐 Started: {login_time}
                                </div>

                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if st.button(
                            "OPEN MACHINE",
                            key=f"open_{machine_id}",
                            type="primary",
                            use_container_width=True,
                        ):

                            st.session_state.machine_id = machine_id
                            st.session_state.machine_name = machine_name
                            st.session_state.session_id = (
                                active_session.get("SessionID")
                            )
                            st.session_state.current_shift = (
                                active_session.get("ShiftID")
                            )
                            st.session_state.shift_date = (
                                active_session.get("Date")
                            )
                            st.session_state.machine_session_active = True
                            st.session_state.machine_selected = True

                            st.rerun()

                    # =============================================
                    # OTHER OPERATOR
                    # =============================================

                    else:

                        st.markdown(
                            f"""
                            <div class="machine-card machine-running">

                                <div class="machine-title">
                                    {machine_id}
                                </div>

                                <div class="machine-name">
                                    {machine_name}
                                </div>

                                <div class="machine-status">
                                    🔴 RUNNING
                                </div>

                                <div class="machine-detail">
                                    👤 {current_operator}
                                    ({current_operator_id})
                                </div>

                                <div class="machine-detail">
                                    🕐 Started: {login_time}
                                </div>

                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if st.button(
                            "TAKE OVER",
                            key=f"takeover_{machine_id}",
                            use_container_width=True,
                        ):

                            st.session_state.machine_id = machine_id
                            st.session_state.machine_name = machine_name
                            st.session_state.occupied_session = (
                                active_session
                            )
                            st.session_state.machine_selected = True
                            st.session_state.show_takeover = True

                            st.rerun()

    except Exception as e:

        st.error(
            "Unable to load machine status."
        )

        st.exception(e)


# =========================================================
# MACHINE HOME
# =========================================================

def machine_home():

    # =====================================================
    # TAKEOVER CONFIRMATION
    # =====================================================

    if st.session_state.get(
        "show_takeover",
        False,
    ):

        active_session = st.session_state.get(
            "occupied_session"
        )

        st.title("⚠️ Machine Already Occupied")

        if active_session:

            previous_operator = active_session.get(
                "OperatorName",
                "Unknown",
            )

            previous_operator_id = active_session.get(
                "OperatorID",
                "",
            )

            login_time = active_session.get(
                "LoginTime",
                "",
            )

            st.warning(
                f"Machine **{st.session_state.machine_name}** "
                f"is currently being operated by "
                f"**{previous_operator} "
                f"({previous_operator_id})**."
            )

            st.write(
                f"Current session started: **{login_time}**"
            )

            st.write("")

            col1, col2 = st.columns(2)

            with col1:

                if st.button(
                    "TAKE OVER MACHINE",
                    type="primary",
                    use_container_width=True,
                ):

                    takeover_machine()

            with col2:

                if st.button(
                    "CANCEL",
                    use_container_width=True,
                ):

                    st.session_state.show_takeover = False
                    st.session_state.occupied_session = None
                    st.session_state.machine_selected = False
                    st.session_state.machine_id = None
                    st.session_state.machine_name = None

                    st.rerun()

        return

    # =====================================================
    # NORMAL MACHINE HOME
    # =====================================================

    st.title(
        "🏭 Manufacturing Production Monitor"
    )

    col1, col2 = st.columns([3, 1])

    with col1:

        st.success(
            f"Operator: "
            f"{st.session_state.operator_name}"
        )

        st.info(
            f"Machine: "
            f"{st.session_state.machine_name}"
        )

    with col2:

        if st.button(
            "END MACHINE SESSION",
            use_container_width=True,
        ):

            end_current_machine_session()

    st.divider()

    st.subheader(
        "Current Machine Session"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Operator",
            st.session_state.operator_name,
        )

    with col2:

        st.metric(
            "Machine",
            st.session_state.machine_name,
        )

    with col3:

        st.metric(
            "Shift",
            st.session_state.current_shift,
        )

    st.write("")

    st.info(
        f"Session ID: "
        f"{st.session_state.session_id}"
    )

    st.success(
        "Machine is currently assigned to you."
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
