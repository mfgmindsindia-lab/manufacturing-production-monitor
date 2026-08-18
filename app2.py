import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
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


@st.cache_data(ttl=180)
def get_master_records(sheet_name):
    """
    Same as get_records, but for reference data that changes rarely
    (PartMaster, Setup) - a longer TTL means far fewer Google Sheets
    API round trips per operator session, which is what was making
    selecting a part feel slow on every rerun.
    """
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(sheet_name)
    return worksheet.get_all_records()


def refresh_data():
    get_records.clear()
    get_master_records.clear()


def safe_get_records(sheet_name):
    """
    Like get_records, but returns an empty list instead of raising
    if the worksheet doesn't exist yet (e.g. ProductionLog/ChangeoverLog
    before any production has ever been logged).
    """
    try:
        return get_records(sheet_name)
    except Exception:
        return []


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
    "manager_logged_in": False,
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
    "production_active": False,
    "production_run_id": None,
    "production_start_time": None,
    "production_part": None,
    "production_setup": None,
    "production_cycle_time": None,
    "changeover_active": False,
    "changeover_id": None,
    "changeover_start_time": None,
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

    # Resume any production/changeover already in progress on
    # this machine, rather than resetting state for the new operator.
    sync_machine_production_state(st.session_state.machine_id)

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
# BACK TO MACHINE LIST (without ending the machine session)
# =========================================================

def back_to_machine_list():
    """
    Returns the operator to the machine grid WITHOUT closing the
    current machine's session in the sheet. This is what lets one
    operator run multiple machines at once: the machine they were
    just on stays ACTIVE and keeps producing/changing over in the
    background, and they can go select or open another machine.
    Reopening this machine later (via OPEN MACHINE) resumes exactly
    where they left off, via sync_machine_production_state.
    """

    st.session_state.machine_selected = False
    st.session_state.machine_id = None
    st.session_state.machine_name = None
    st.session_state.session_id = None
    st.session_state.current_shift = None
    st.session_state.shift_date = None
    st.session_state.machine_session_active = False
    st.session_state.show_takeover = False
    st.session_state.occupied_session = None

    st.session_state.production_active = False
    st.session_state.production_run_id = None
    st.session_state.production_part = None
    st.session_state.production_setup = None
    st.session_state.production_cycle_time = None
    st.session_state.production_start_time = None

    st.session_state.changeover_active = False
    st.session_state.changeover_id = None
    st.session_state.changeover_start_time = None

    refresh_data()

    st.rerun()


# =========================================================
# LOGOUT
# =========================================================

def close_all_operator_sessions(operator_id):
    """
    Closes every ACTIVE machine session belonging to this operator -
    not just the one currently open in this browser tab. Needed
    because an operator can now hold multiple machines at once via
    SWITCH / ADD MACHINE, so a plain logout must sweep all of them.
    """
    sessions = get_records("MachineSessions")

    for session in sessions:
        session_operator_id = str(
            session.get("OperatorID", "")
        ).strip()

        status = str(
            session.get("Status", "")
        ).strip().upper()

        if (
            session_operator_id == str(operator_id)
            and status == "ACTIVE"
        ):
            close_machine_session(session)


def logout():

    if st.session_state.get("operator_id"):
        close_all_operator_sessions(st.session_state.operator_id)

    st.session_state.clear()

    st.rerun()


# =========================================================
# MANAGER LOGOUT
# =========================================================

def manager_logout():
    st.session_state.clear()
    st.rerun()


# =========================================================
# LOGIN SCREEN
# =========================================================

def login_screen():

    st.title(
        "🏭 Manufacturing Production Monitor"
    )

    tab_operator, tab_manager = st.tabs(
        ["Operator Login", "Manager Login"]
    )

    with tab_operator:
        operator_login_tab()

    with tab_manager:
        manager_login_tab()


def operator_login_tab():

    st.subheader("Operator Login")

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


def manager_login_tab():

    st.subheader("Manager Login")

    st.write("")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:

        manager_password = st.text_input(
            "Manager Password",
            type="password",
            placeholder="Enter manager password",
            key="manager_password_input",
        )

        st.write("")

        manager_login_button = st.button(
            "MANAGER LOGIN",
            type="primary",
            use_container_width=True,
        )

        if manager_login_button:

            configured_password = str(
                st.secrets.get("manager_password", "")
            ).strip()

            if not configured_password:

                st.error(
                    "Manager password is not configured. "
                    "Add manager_password to st.secrets."
                )
                return

            if manager_password.strip() == configured_password:

                st.session_state.manager_logged_in = True
                st.rerun()

            else:

                st.error("Invalid manager password.")


# =========================================================
# MACHINE STATUS CARDS
# =========================================================

def machine_selection():

    st.title("🏭 Machine Status")

    col_op, col_logout = st.columns([4, 1])

    with col_op:
        st.success(
            f"👤 {st.session_state.operator_name} "
            f"({st.session_state.operator_id})"
        )

    with col_logout:
        if st.button(
            "🚪 LOGOUT",
            use_container_width=True,
            help="Ends any machine sessions you have open and logs you out.",
        ):
            logout()

    st.divider()

    try:
        machines = get_records("Machines")
        active_machines = []

        for machine in machines:
            machine_id = str(machine.get("MachineID", "")).strip()
            machine_name = str(machine.get("MachineName", "")).strip()
            active = str(machine.get("Active", "")).strip().upper()

            if machine_id and machine_name and active == "TRUE":
                active_machines.append({
                    "MachineID": machine_id,
                    "MachineName": machine_name,
                })

        if not active_machines:
            st.warning("No active machines found in Machines sheet.")
            return

        # ---------------------------------------------------
        # Attach each machine's active session (if any) and a
        # sort priority so the operator's own machine(s) always
        # float to the top - no hunting through the grid.
        #   0 = my machine(s)
        #   1 = available machines
        #   2 = machines other operators are running
        # ---------------------------------------------------

        for machine in active_machines:
            active_session = get_active_machine_session(
                machine["MachineID"]
            )
            machine["active_session"] = active_session

            if active_session is None:
                machine["sort_priority"] = 1
            else:
                session_operator_id = str(
                    active_session.get("OperatorID", "")
                ).strip()

                if session_operator_id == str(
                    st.session_state.operator_id
                ):
                    machine["sort_priority"] = 0
                else:
                    machine["sort_priority"] = 2

        active_machines.sort(
            key=lambda m: (m["sort_priority"], m["MachineID"])
        )

        my_machine_count = sum(
            1 for m in active_machines if m["sort_priority"] == 0
        )

        if my_machine_count > 0:
            st.caption(
                f"You currently have {my_machine_count} machine"
                f"{'s' if my_machine_count != 1 else ''} open. "
                f"Select another below to run more than one at a time."
            )

        # Exactly three native Streamlit cards per row.
        for start in range(0, len(active_machines), 3):
            row_machines = active_machines[start:start + 3]
            columns = st.columns(3)

            for index, machine in enumerate(row_machines):
                machine_id = machine["MachineID"]
                machine_name = machine["MachineName"]
                active_session = machine["active_session"]

                with columns[index]:

                    if active_session is None:
                        with st.container(border=True):
                            st.subheader(machine_id)
                            st.write(machine_name)
                            st.success("🟢 AVAILABLE")
                            st.caption("No operator assigned")

                            if st.button(
                                "SELECT MACHINE",
                                key=f"select_{machine_id}",
                                type="primary",
                                use_container_width=True,
                            ):
                                st.session_state.machine_id = machine_id
                                st.session_state.machine_name = machine_name
                                create_machine_session()
                                sync_machine_production_state(machine_id)
                                st.session_state.machine_selected = True
                                st.rerun()

                    else:
                        current_operator = str(
                            active_session.get("OperatorName", "Unknown")
                        ).strip()

                        current_operator_id = str(
                            active_session.get("OperatorID", "")
                        ).strip()

                        login_time = str(
                            active_session.get("LoginTime", "")
                        ).strip()

                        is_my_machine = (
                            current_operator_id
                            == str(st.session_state.operator_id)
                        )

                        if is_my_machine:
                            with st.container(border=True):
                                st.subheader(machine_id)
                                st.write(machine_name)
                                st.info("🔵 YOUR MACHINE")
                                st.write(f"👤 {current_operator}")
                                st.caption(f"🕐 Started: {login_time}")

                                if st.button(
                                    "OPEN MACHINE",
                                    key=f"open_{machine_id}",
                                    type="primary",
                                    use_container_width=True,
                                ):
                                    st.session_state.machine_id = machine_id
                                    st.session_state.machine_name = machine_name
                                    st.session_state.session_id = active_session.get("SessionID")
                                    st.session_state.current_shift = active_session.get("ShiftID")
                                    st.session_state.shift_date = active_session.get("Date")
                                    st.session_state.machine_session_active = True
                                    sync_machine_production_state(machine_id)
                                    st.session_state.machine_selected = True
                                    st.rerun()

                        else:
                            with st.container(border=True):
                                st.subheader(machine_id)
                                st.write(machine_name)
                                st.error("🔴 RUNNING")
                                st.write(
                                    f"👤 {current_operator} "
                                    f"({current_operator_id})"
                                )
                                st.caption(f"🕐 Started: {login_time}")

                                if st.button(
                                    "TAKE OVER",
                                    key=f"takeover_{machine_id}",
                                    use_container_width=True,
                                ):
                                    st.session_state.machine_id = machine_id
                                    st.session_state.machine_name = machine_name
                                    st.session_state.occupied_session = active_session
                                    st.session_state.machine_selected = True
                                    st.session_state.show_takeover = True
                                    st.rerun()

    except Exception as e:
        st.error("Unable to load machine status.")
        st.exception(e)



# =========================================================
# PRODUCTION / CHANGEOVER HELPERS
# =========================================================

def get_or_create_worksheet(sheet_name, headers):
    spreadsheet = get_spreadsheet()

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=len(headers),
        )
        worksheet.append_row(headers, value_input_option="USER_ENTERED")

    return worksheet


def get_latest_running_production(machine_id):
    """
    Returns the most recent ProductionLog row for this machine
    that is still RUNNING, regardless of which login session
    started it. Used to restore state after a logout.
    """
    rows = safe_get_records("ProductionLog")

    matches = [
        r for r in rows
        if str(r.get("MachineID", "")).strip() == str(machine_id)
        and str(r.get("Status", "")).strip().upper() == "RUNNING"
    ]

    if not matches:
        return None

    matches.sort(key=lambda r: str(r.get("StartTime", "")))

    return matches[-1]


def get_latest_open_changeover(machine_id):
    """
    Returns the most recent ChangeoverLog row for this machine
    that is still OPEN, regardless of which login session
    started it. Used to restore state after a logout.
    """
    rows = safe_get_records("ChangeoverLog")

    matches = [
        r for r in rows
        if str(r.get("MachineID", "")).strip() == str(machine_id)
        and str(r.get("Status", "")).strip().upper() == "OPEN"
    ]

    if not matches:
        return None

    matches.sort(key=lambda r: str(r.get("StartTime", "")))

    return matches[-1]


def sync_machine_production_state(machine_id):
    """
    Restores production/changeover UI state from the sheet data
    for this machine. This is essential because an operator may
    log out mid-changeover (or mid-production) - the OPEN/RUNNING
    row in the sheet stays exactly as it was, but st.session_state
    is wiped on logout. Without this, reopening the machine would
    incorrectly reset to "Start Production" instead of resuming
    the changeover or production that was already in progress.
    """

    open_changeover = get_latest_open_changeover(machine_id)

    if open_changeover:

        st.session_state.changeover_active = True
        st.session_state.changeover_id = open_changeover.get("ChangeoverID")
        st.session_state.changeover_start_time = open_changeover.get("StartTime")

        st.session_state.production_active = False
        st.session_state.production_run_id = None
        st.session_state.production_part = None
        st.session_state.production_setup = None
        st.session_state.production_cycle_time = None
        st.session_state.production_start_time = None

        return

    running_production = get_latest_running_production(machine_id)

    if running_production:

        st.session_state.production_active = True
        st.session_state.production_run_id = running_production.get("RunID")
        st.session_state.production_start_time = running_production.get("StartTime")
        st.session_state.production_part = running_production.get("PartName")
        st.session_state.production_setup = running_production.get("SetupName")

        cycle_seconds = running_production.get("CycleTimeSeconds")

        try:
            st.session_state.production_cycle_time = int(cycle_seconds)
        except (TypeError, ValueError):
            st.session_state.production_cycle_time = None

        st.session_state.changeover_active = False
        st.session_state.changeover_id = None
        st.session_state.changeover_start_time = None

        return

    # Nothing in progress on this machine - clean slate.
    st.session_state.production_active = False
    st.session_state.production_run_id = None
    st.session_state.production_part = None
    st.session_state.production_setup = None
    st.session_state.production_cycle_time = None
    st.session_state.production_start_time = None

    st.session_state.changeover_active = False
    st.session_state.changeover_id = None
    st.session_state.changeover_start_time = None


def normalize_cycle_time(value):
    """
    Converts operator input:
        2.30 -> 150 seconds
        5.15 -> 315 seconds
        0.45 -> 45 seconds

    The two digits after the decimal are interpreted as seconds.
    """
    value = str(value).strip()

    if not value:
        raise ValueError("Cycle time is required.")

    if "." not in value:
        raise ValueError(
            "Enter cycle time as MM.SS, for example 2.30."
        )

    minutes_text, seconds_text = value.split(".", 1)

    if not minutes_text.isdigit() or not seconds_text.isdigit():
        raise ValueError(
            "Cycle time must be in MM.SS format, for example 2.30."
        )

    seconds_text = seconds_text[:2].ljust(2, "0")

    minutes = int(minutes_text)
    seconds = int(seconds_text)

    if seconds >= 60:
        raise ValueError(
            "Seconds must be between 00 and 59."
        )

    return minutes * 60 + seconds


def format_cycle_time(seconds):
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""

    return f"{total // 60}.{total % 60:02d}"


def get_part_master():
    try:
        return get_master_records("PartMaster")
    except Exception:
        return []


def get_setup_master():
    try:
        return get_master_records("Setup")
    except Exception:
        return []


def get_part_options():
    rows = get_part_master()
    options = []

    for row in rows:
        part_code = str(row.get("PartCode", "")).strip()
        part = str(row.get("PartName", "")).strip()
        active = str(row.get("Active", "TRUE")).strip().upper()

        if part and active in ("TRUE", "YES", "1", "ACTIVE", ""):
            options.append({
                "PartCode": part_code,
                "PartName": part,
            })

    return options


def get_part_display_map():
    """
    Builds a lookup of:
        "PartCode - PartName" -> {"PartCode": ..., "PartName": ...}

    so the selectbox can be searched by either code or name.
    Falls back to just PartName if PartCode is blank.
    """
    rows = get_part_options()
    display_map = {}

    for row in rows:
        part_code = row["PartCode"]
        part_name = row["PartName"]

        if part_code:
            display_label = f"{part_code} - {part_name}"
        else:
            display_label = part_name

        display_map[display_label] = row

    return display_map


def get_setup_options():
    rows = get_setup_master()
    setups = []

    for row in rows:
        setup = str(
            row.get("SetupName", row.get("Setup", ""))
        ).strip()

        active = str(
            row.get("Active", "TRUE")
        ).strip().upper()

        if setup and active in ("TRUE", "YES", "1", "ACTIVE", ""):
            setups.append(setup)

    return sorted(set(setups))


def start_production(part_name, setup_name, cycle_time_text):
    cycle_seconds = normalize_cycle_time(cycle_time_text)

    now = timestamp_now()
    run_id = (
        "PR-"
        + india_now().strftime("%Y%m%d%H%M%S%f")
        + "-"
        + str(st.session_state.machine_id)
    )

    worksheet = get_or_create_worksheet(
        "ProductionLog",
        [
            "RunID",
            "ShiftDate",
            "ShiftID",
            "MachineID",
            "MachineSessionID",
            "OperatorID",
            "OperatorName",
            "PartName",
            "SetupName",
            "CycleTimeText",
            "CycleTimeSeconds",
            "StartTime",
            "EndTime",
            "Status",
            "CreatedAt",
        ],
    )

    shift_id, shift_date = get_current_shift()

    worksheet.append_row(
        [
            run_id,
            shift_date,
            shift_id,
            st.session_state.machine_id,
            st.session_state.session_id,
            st.session_state.operator_id,
            st.session_state.operator_name,
            part_name,
            setup_name,
            cycle_time_text,
            cycle_seconds,
            now,
            "",
            "RUNNING",
            now,
        ],
        value_input_option="USER_ENTERED",
    )

    refresh_data()

    st.session_state.production_active = True
    st.session_state.production_run_id = run_id
    st.session_state.production_start_time = now
    st.session_state.production_part = part_name
    st.session_state.production_setup = setup_name
    st.session_state.production_cycle_time = cycle_seconds
    st.session_state.changeover_active = False
    st.session_state.changeover_id = None
    st.session_state.changeover_start_time = None


def close_production_run():
    run_id = st.session_state.get("production_run_id")

    if not run_id:
        return

    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet("ProductionLog")
    rows = worksheet.get_all_values()

    if not rows:
        return

    header = rows[0]

    try:
        run_col = header.index("RunID") + 1
        end_col = header.index("EndTime") + 1
        status_col = header.index("Status") + 1
    except ValueError:
        return

    for row_number, row in enumerate(rows[1:], start=2):
        if row and str(row[run_col - 1]).strip() == str(run_id):
            worksheet.update_cell(
                row_number,
                end_col,
                timestamp_now(),
            )
            worksheet.update_cell(
                row_number,
                status_col,
                "CLOSED",
            )
            break

    refresh_data()


def start_changeover():
    if not st.session_state.production_active:
        st.warning("There is no active production run.")
        return

    close_production_run()

    changeover_id = (
        "CO-"
        + india_now().strftime("%Y%m%d%H%M%S%f")
    )

    now = timestamp_now()

    worksheet = get_or_create_worksheet(
        "ChangeoverLog",
        [
            "ChangeoverID",
            "ShiftDate",
            "ShiftID",
            "MachineID",
            "MachineSessionID",
            "OperatorID",
            "OperatorName",
            "PreviousPart",
            "PreviousSetup",
            "NewPart",
            "NewSetup",
            "StartTime",
            "EndTime",
            "DurationSeconds",
            "Status",
            "CreatedAt",
        ],
    )

    shift_id, shift_date = get_current_shift()

    worksheet.append_row(
        [
            changeover_id,
            shift_date,
            shift_id,
            st.session_state.machine_id,
            st.session_state.session_id,
            st.session_state.operator_id,
            st.session_state.operator_name,
            st.session_state.production_part or "",
            st.session_state.production_setup or "",
            "",
            "",
            now,
            "",
            "",
            "OPEN",
            now,
        ],
        value_input_option="USER_ENTERED",
    )

    st.session_state.production_active = False
    st.session_state.production_run_id = None
    st.session_state.changeover_active = True
    st.session_state.changeover_id = changeover_id
    st.session_state.changeover_start_time = now

    refresh_data()


def complete_changeover(
    new_part,
    new_setup,
    cycle_time_text,
):
    if not st.session_state.changeover_active:
        st.warning("No changeover is currently open.")
        return

    cycle_seconds = normalize_cycle_time(cycle_time_text)

    now = timestamp_now()

    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet("ChangeoverLog")
    rows = worksheet.get_all_values()

    if not rows:
        return

    header = rows[0]

    try:
        id_col = header.index("ChangeoverID") + 1
        new_part_col = header.index("NewPart") + 1
        new_setup_col = header.index("NewSetup") + 1
        end_col = header.index("EndTime") + 1
        duration_col = header.index("DurationSeconds") + 1
        status_col = header.index("Status") + 1
    except ValueError:
        st.error("ChangeoverLog headers are incomplete.")
        return

    target_row = None
    start_value = None

    for row_number, row in enumerate(rows[1:], start=2):
        if row and str(row[id_col - 1]).strip() == str(
            st.session_state.changeover_id
        ):
            target_row = row_number
            start_value = row[header.index("StartTime")]
            break

    if target_row is None:
        st.error("Changeover record could not be found.")
        return

    try:
        start_dt = datetime.strptime(
            start_value,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=INDIA_TZ)

        end_dt = datetime.strptime(
            now,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=INDIA_TZ)

        duration_seconds = int(
            (end_dt - start_dt).total_seconds()
        )
    except Exception:
        duration_seconds = ""

    worksheet.update_cell(target_row, new_part_col, new_part)
    worksheet.update_cell(target_row, new_setup_col, new_setup)
    worksheet.update_cell(target_row, end_col, now)
    worksheet.update_cell(target_row, duration_col, duration_seconds)
    worksheet.update_cell(target_row, status_col, "CLOSED")

    refresh_data()

    start_production(
        new_part,
        new_setup,
        cycle_time_text,
    )


def production_entry():

    st.title("🏭 Production Entry")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.info(
            f"Operator: {st.session_state.operator_name}"
        )

    with col2:
        st.info(
            f"Machine: {st.session_state.machine_name}"
        )

    with col3:
        st.info(
            f"Shift: {st.session_state.current_shift}"
        )

    st.divider()

    part_display_map = get_part_display_map()

    if not part_display_map:
        st.warning(
            "No active PartMaster data found. "
            "Add PartCode and PartName to PartMaster."
        )
        return

    part_display_labels = sorted(part_display_map.keys())

    setup_options = get_setup_options()

    # =====================================================
    # ACTIVE PRODUCTION
    # =====================================================

    if st.session_state.production_active:

        st.success("🟢 PRODUCTION RUNNING")

        c1, c2 = st.columns(2)

        with c1:
            st.metric(
                "Part",
                st.session_state.production_part,
            )

        with c2:
            st.metric(
                "Setup",
                st.session_state.production_setup,
            )

        st.write(
            f"Cycle Time: "
            f"**{format_cycle_time(st.session_state.production_cycle_time)}**"
        )

        st.write(
            f"Started: **{st.session_state.production_start_time}**"
        )

        st.divider()

        if st.button(
            "🔄 START CHANGEOVER",
            type="primary",
            use_container_width=True,
        ):
            start_changeover()
            st.rerun()

        return

    # =====================================================
    # CHANGEOVER IN PROGRESS
    # =====================================================

    if st.session_state.changeover_active:

        st.warning("🟠 CHANGEOVER IN PROGRESS")

        st.write(
            f"Changeover started: "
            f"**{st.session_state.changeover_start_time}**"
        )

        st.divider()

        st.subheader("New Production")

        with st.form("changeover_new_production_form"):

            selected_part_display = st.selectbox(
                "Part (search by code or name)",
                part_display_labels,
                index=None,
                placeholder="Search by part code or name",
                key="changeover_part",
            )

            if setup_options:
                selected_setup = st.selectbox(
                    "Setup",
                    setup_options,
                    key="changeover_setup",
                )
            else:
                st.warning(
                    "No Setup master data found."
                )
                selected_setup = ""

            cycle_time = st.text_input(
                "Cycle Time (MM.SS)",
                placeholder="Example: 2.30",
                key="changeover_cycle",
            )

            submitted = st.form_submit_button(
                "▶ START NEW PRODUCTION",
                type="primary",
                use_container_width=True,
            )

        if submitted:

            selected_part = (
                part_display_map[selected_part_display]["PartName"]
                if selected_part_display
                else ""
            )

            if not selected_part:
                st.error("Select a part.")
                return

            if not selected_setup:
                st.error("Select a setup.")
                return

            try:
                complete_changeover(
                    selected_part,
                    selected_setup,
                    cycle_time,
                )
                st.success("Changeover completed. Production started.")
                st.rerun()

            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error("Unable to complete changeover.")
                st.exception(e)

        return

    # =====================================================
    # NEW PRODUCTION
    # =====================================================

    st.subheader("Start Production")

    with st.form("new_production_form"):

        selected_part_display = st.selectbox(
            "Part (search by code or name)",
            part_display_labels,
            index=None,
            placeholder="Search by part code or name",
            key="production_part_select",
        )

        if setup_options:
            selected_setup = st.selectbox(
                "Setup",
                setup_options,
                key="production_setup_select",
            )
        else:
            st.warning(
                "No Setup master data found."
            )
            selected_setup = ""

        cycle_time = st.text_input(
            "Cycle Time (MM.SS)",
            placeholder="Example: 2.30",
            key="production_cycle_input",
        )

        submitted = st.form_submit_button(
            "▶ START PRODUCTION",
            type="primary",
            use_container_width=True,
        )

    if submitted:

        selected_part = (
            part_display_map[selected_part_display]["PartName"]
            if selected_part_display
            else ""
        )

        if not selected_part:
            st.error("Select a part.")
            return

        if not selected_setup:
            st.error("Select a setup.")
            return

        try:
            start_production(
                selected_part,
                selected_setup,
                cycle_time,
            )
            st.success("Production started.")
            st.rerun()

        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error("Unable to start production.")
            st.exception(e)


# =========================================================
# MACHINE HOME
# =========================================================

def machine_home():

    # =====================================================
    # TAKEOVER CONFIRMATION
    # =====================================================

    if st.session_state.get("show_takeover", False):

        active_session = st.session_state.get("occupied_session")

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
    # MACHINE HEADER
    # =====================================================

    st.title("🏭 Manufacturing Production Monitor")

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        st.success(
            f"Operator: {st.session_state.operator_name}"
        )

    with col2:
        st.info(
            f"Machine: {st.session_state.machine_name}"
        )

    with col3:
        st.info(
            f"Shift: {st.session_state.current_shift}"
        )

    # =====================================================
    # SESSION CONTROLS
    # =====================================================

    col_switch, col_end, col_logout = st.columns(3)

    with col_switch:
        if st.button(
            "🔀 SWITCH / ADD MACHINE",
            use_container_width=True,
            help="Keeps this machine running and takes you back "
                 "to the machine list to open or select another one.",
        ):
            back_to_machine_list()

    with col_end:
        if st.button(
            "END MACHINE SESSION",
            use_container_width=True,
        ):
            end_current_machine_session()

    with col_logout:
        if st.button(
            "🚪 LOGOUT",
            use_container_width=True,
            help="Ends ALL your active machine sessions and logs you out.",
        ):
            logout()

    st.divider()

    # =====================================================
    # PRODUCTION ENTRY
    # =====================================================

    production_entry()


# =========================================================
# MANAGER DASHBOARD - DATA HELPERS
# =========================================================

SHIFT_LENGTH_MINUTES = 690  # 08:30 to 20:00 (and the mirrored night shift)


def get_machine_live_status():
    machines = safe_get_records("Machines")
    production_rows = safe_get_records("ProductionLog")
    changeover_rows = safe_get_records("ChangeoverLog")

    status_rows = []

    for machine in machines:

        machine_id = str(machine.get("MachineID", "")).strip()
        machine_name = str(machine.get("MachineName", "")).strip()
        active = str(machine.get("Active", "")).strip().upper()

        if not machine_id or not machine_name or active != "TRUE":
            continue

        session = get_active_machine_session(machine_id)

        if session is None:
            status_rows.append({
                "MachineID": machine_id,
                "MachineName": machine_name,
                "Status": "IDLE",
                "Operator": "",
                "Detail": "No operator logged in",
            })
            continue

        operator = str(session.get("OperatorName", "")).strip()
        session_id = str(session.get("SessionID", "")).strip()

        running_row = next(
            (
                r for r in production_rows
                if str(r.get("MachineSessionID", "")).strip() == session_id
                and str(r.get("Status", "")).strip().upper() == "RUNNING"
            ),
            None,
        )

        open_changeover = next(
            (
                r for r in changeover_rows
                if str(r.get("MachineSessionID", "")).strip() == session_id
                and str(r.get("Status", "")).strip().upper() == "OPEN"
            ),
            None,
        )

        if running_row:
            status = "RUNNING"
            detail = str(running_row.get("PartName", ""))
        elif open_changeover:
            status = "CHANGEOVER"
            detail = "In progress"
        else:
            status = "LOGGED IN"
            detail = "No active production"

        status_rows.append({
            "MachineID": machine_id,
            "MachineName": machine_name,
            "Status": status,
            "Operator": operator,
            "Detail": detail,
        })

    return status_rows


def get_production_dataframe():
    rows = safe_get_records("ProductionLog")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    if "StartTime" in df.columns:
        df["StartTime"] = pd.to_datetime(
            df["StartTime"], errors="coerce"
        )

    if "EndTime" in df.columns:
        df["EndTime"] = pd.to_datetime(
            df["EndTime"], errors="coerce"
        )

    return df


def get_changeover_dataframe():
    rows = safe_get_records("ChangeoverLog")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    if "StartTime" in df.columns:
        df["StartTime"] = pd.to_datetime(
            df["StartTime"], errors="coerce"
        )

    if "EndTime" in df.columns:
        df["EndTime"] = pd.to_datetime(
            df["EndTime"], errors="coerce"
        )

    if "DurationSeconds" in df.columns:
        df["DurationMinutes"] = pd.to_numeric(
            df["DurationSeconds"], errors="coerce"
        ) / 60

    return df


# =========================================================
# MANAGER DASHBOARD - TABS
# =========================================================

def manager_live_status_tab():

    status_rows = get_machine_live_status()

    if not status_rows:
        st.info("No active machines found.")
        return

    running = sum(1 for r in status_rows if r["Status"] == "RUNNING")
    changeover = sum(1 for r in status_rows if r["Status"] == "CHANGEOVER")
    idle = sum(1 for r in status_rows if r["Status"] == "IDLE")
    logged_in = sum(1 for r in status_rows if r["Status"] == "LOGGED IN")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Running", running)
    m2.metric("Changeover", changeover)
    m3.metric("Logged In (Idle)", logged_in)
    m4.metric("Idle (No Login)", idle)

    st.divider()

    for start in range(0, len(status_rows), 3):
        row_machines = status_rows[start:start + 3]
        columns = st.columns(3)

        for index, machine in enumerate(row_machines):
            with columns[index]:
                with st.container(border=True):
                    st.subheader(machine["MachineID"])
                    st.write(machine["MachineName"])

                    if machine["Status"] == "RUNNING":
                        st.success(f"🟢 RUNNING — {machine['Detail']}")
                    elif machine["Status"] == "CHANGEOVER":
                        st.warning("🟠 CHANGEOVER")
                    elif machine["Status"] == "LOGGED IN":
                        st.info("🔵 LOGGED IN — idle")
                    else:
                        st.error("⚪ IDLE — no operator")

                    if machine["Operator"]:
                        st.caption(f"👤 {machine['Operator']}")


def manager_production_tab():

    df = get_production_dataframe()

    if df.empty:
        st.info("No production data logged yet.")
        return

    date_filter = st.date_input(
        "Shift Date",
        value=india_now().date(),
        key="mgr_production_date",
    )

    filtered = df[df["ShiftDate"] == str(date_filter)]

    if filtered.empty:
        st.info("No production runs on this date.")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Runs", len(filtered))
    m2.metric("Machines Used", filtered["MachineID"].nunique())
    m3.metric("Parts Produced", filtered["PartName"].nunique())

    st.divider()

    runs_by_machine = (
        filtered.groupby("MachineID")
        .size()
        .reset_index(name="Runs")
    )

    fig = px.bar(
        runs_by_machine,
        x="MachineID",
        y="Runs",
        title="Production Runs by Machine",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        filtered[[
            "MachineID", "OperatorName", "PartName",
            "SetupName", "CycleTimeText", "StartTime",
            "EndTime", "Status",
        ]],
        use_container_width=True,
        hide_index=True,
    )


def manager_changeover_tab():

    df = get_changeover_dataframe()

    if df.empty:
        st.info("No changeover data logged yet.")
        return

    date_filter = st.date_input(
        "Shift Date",
        value=india_now().date(),
        key="mgr_changeover_date",
    )

    filtered = df[df["ShiftDate"] == str(date_filter)]

    if filtered.empty:
        st.info("No changeovers on this date.")
        return

    closed = filtered[filtered["Status"] == "CLOSED"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Changeovers", len(filtered))

    if not closed.empty and "DurationMinutes" in closed.columns:
        m2.metric(
            "Avg Duration (min)",
            f"{closed['DurationMinutes'].mean():.1f}",
        )
        m3.metric(
            "Longest (min)",
            f"{closed['DurationMinutes'].max():.1f}",
        )
    else:
        m2.metric("Avg Duration (min)", "-")
        m3.metric("Longest (min)", "-")

    st.divider()

    if not closed.empty and "DurationMinutes" in closed.columns:
        fig = px.bar(
            closed,
            x="MachineID",
            y="DurationMinutes",
            title="Changeover Duration by Machine (minutes)",
            hover_data=["PreviousPart", "NewPart"],
        )
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        filtered[[
            "MachineID", "OperatorName", "PreviousPart",
            "NewPart", "StartTime", "EndTime", "Status",
        ]],
        use_container_width=True,
        hide_index=True,
    )


def manager_utilization_tab():

    df = get_production_dataframe()

    if df.empty:
        st.info("No production data logged yet.")
        return

    date_filter = st.date_input(
        "Shift Date",
        value=india_now().date(),
        key="mgr_utilization_date",
    )

    filtered = df[df["ShiftDate"] == str(date_filter)].copy()

    if filtered.empty:
        st.info("No production runs on this date.")
        return

    now = india_now().replace(tzinfo=None)

    def run_duration_minutes(row):
        start = row["StartTime"]
        end = row["EndTime"]

        if pd.isna(start):
            return 0

        if pd.isna(end):
            end = now

        return max((end - start).total_seconds() / 60, 0)

    filtered["RunMinutes"] = filtered.apply(
        run_duration_minutes, axis=1
    )

    utilization = (
        filtered.groupby("MachineID")["RunMinutes"]
        .sum()
        .reset_index()
    )

    utilization["UtilizationPct"] = (
        utilization["RunMinutes"] / SHIFT_LENGTH_MINUTES * 100
    ).clip(upper=100)

    fig = px.bar(
        utilization,
        x="MachineID",
        y="UtilizationPct",
        title="Machine Utilization % (Production Time vs Shift Length)",
        range_y=[0, 100],
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        f"Utilization = total production run time ÷ "
        f"{SHIFT_LENGTH_MINUTES} min shift length. "
        f"Changeover and idle time are not counted as production."
    )

    st.dataframe(
        utilization.rename(columns={
            "RunMinutes": "Production Minutes",
            "UtilizationPct": "Utilization %",
        }),
        use_container_width=True,
        hide_index=True,
    )


def manager_dashboard():

    col1, col2 = st.columns([5, 1])

    with col1:
        st.title("📊 Manager KPI Dashboard")

    with col2:
        st.write("")
        if st.button("Logout", use_container_width=True):
            manager_logout()

    if st.button("🔄 Refresh Data"):
        refresh_data()
        st.rerun()

    st.caption(
        f"Live as of {timestamp_now()} IST "
        f"(data auto-refreshes every 15 seconds)"
    )

    tab_live, tab_production, tab_changeover, tab_utilization = st.tabs(
        [
            "Live Status",
            "Production",
            "Changeover",
            "Utilization",
        ]
    )

    with tab_live:
        manager_live_status_tab()

    with tab_production:
        manager_production_tab()

    with tab_changeover:
        manager_changeover_tab()

    with tab_utilization:
        manager_utilization_tab()


# =========================================================
# MAIN APPLICATION
# =========================================================

if st.session_state.manager_logged_in:

    manager_dashboard()

elif not st.session_state.logged_in:

    login_screen()

else:

    if not st.session_state.machine_selected:

        machine_selection()

    else:

        machine_home()
