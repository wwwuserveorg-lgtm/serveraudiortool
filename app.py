"""UServe Contractor Service Review Console — Streamlit edition.

For production, set DATABASE_URL to a managed Postgres database and replace the
SQLite connection function below with the provider's SQLAlchemy connection.
SQLite is appropriate for a single-user or test deployment only.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "userve_audits.db"
LOGO_PATH = APP_DIR / "assets" / "logo.png"
WINDOWS = {"Morning (7–9 AM)": (7, 9), "Daytime (9 AM–5:30 PM)": (9, 17.5), "Evening (5:30–9 PM)": (17.5, 21)}
WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIER_COLORS = {
    "Diamond Elite": "#1F3B25",
    "Diamond": "#355E3B",
    "Gold": "#B8860B",
    "Silver": "#6B7280",
    "Green": "#5C7A4A",
    "Blue": "#3B5BA5",
    "Red": "#B3261E",
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def setup() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS servers (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
          company TEXT, territory TEXT,
          audit_day INTEGER DEFAULT 0, audit_time TEXT DEFAULT '09:00', active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS server_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL,
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS live_audits (
          server_id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, review_date TEXT NOT NULL,
          completion_rate REAL, score REAL, tier TEXT, first_attempt_rate REAL,
          average_days_to_service REAL, attempt_quality REAL, report_notes TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_completions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL, week_start TEXT NOT NULL,
          completed_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(server_id, week_start)
        );
        """)


def rows(query: str, params: tuple = ()) -> list[dict]:
    with db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def execute(query: str, params: tuple = ()) -> None:
    with db() as conn:
        conn.execute(query, params)
        conn.commit()


def current_week() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def generate_server_token(server_id: int) -> tuple[str, datetime]:
    """Create a fresh 15-minute access code for a server, invalidating any unused one."""
    execute("DELETE FROM server_tokens WHERE server_id = ? AND used_at IS NULL", (server_id,))
    token = secrets.token_hex(4).upper()  # 8-character code, e.g. "A1B2C3D4"
    created = datetime.now()
    expires = created + timedelta(minutes=15)
    execute(
        "INSERT INTO server_tokens (server_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (server_id, token, created.isoformat(), expires.isoformat()),
    )
    return token, expires


def consume_server_token(token: str) -> dict | None:
    """Validate an unused, unexpired token and mark it used. Returns the token row, or None."""
    matches = rows(
        "SELECT * FROM server_tokens WHERE token = ? AND used_at IS NULL AND expires_at > ?",
        (token.strip().upper(), datetime.now().isoformat()),
    )
    if not matches:
        return None
    match = matches[0]
    execute("UPDATE server_tokens SET used_at = ? WHERE id = ?", (datetime.now().isoformat(), match["id"]))
    return match


def save_live_audit(server_id: int, snapshot: dict) -> None:
    execute(
        """INSERT INTO live_audits (server_id, snapshot, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(server_id) DO UPDATE SET snapshot = excluded.snapshot, updated_at = excluded.updated_at""",
        (server_id, json.dumps(snapshot), datetime.now().isoformat()),
    )


def load_live_audit(server_id: int) -> dict | None:
    matches = rows("SELECT snapshot, updated_at FROM live_audits WHERE server_id = ?", (server_id,))
    return json.loads(matches[0]["snapshot"]) if matches else None


def normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def column(frame: pd.DataFrame, *candidates: str) -> str | None:
    normalized = {normal(c): c for c in frame.columns}
    for candidate in candidates:
        if normal(candidate) in normalized:
            return normalized[normal(candidate)]
    for key, original in normalized.items():
        if any(normal(candidate) in key for candidate in candidates):
            return original
    return None


def parse_date(value: object) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(str(value), errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def attempts_from_text(value: object) -> list[datetime]:
    """Parse attempts separated by <br />, line breaks, or normal text."""
    text = html.unescape(str(value or "")).replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    found = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*[AP]M\b", text, flags=re.I)
    parsed = [parse_date(item) for item in found]
    return [item for item in parsed if item]


def classify_attempts(events: list[datetime]) -> dict[str, int]:
    result = {"Morning (7–9 AM)": 0, "Daytime (9 AM–5:30 PM)": 0, "Evening (5:30–9 PM)": 0, "Weekend": 0}
    for event in events:
        hour = event.hour + event.minute / 60
        for label, (start, end) in WINDOWS.items():
            if start <= hour < end:
                result[label] += 1
                break
        if event.weekday() >= 5:
            result["Weekend"] += 1
    return result


def tier(score: float) -> str:
    if score >= 75: return "Diamond Elite"
    if score >= 70: return "Diamond"
    if score >= 65: return "Gold"
    if score >= 60: return "Silver"
    if score >= 51: return "Green"
    if score >= 26: return "Blue"
    return "Red"


def tier_badge_html(score: float) -> str:
    label = tier(score)
    color = TIER_COLORS.get(label, "#355E3B")
    return (
        f'<span style="background:{color};color:#fff;padding:3px 12px;border-radius:999px;'
        f'font-size:0.8rem;font-weight:600;letter-spacing:.02em;">{label}</span>'
    )


def inject_theme() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid #D8E3D6;
            border-radius: 10px;
            padding: 14px 16px 10px 16px;
        }
        [data-testid="stMetricLabel"] { color: #355E3B; font-weight: 600; }
        section[data-testid="stSidebar"] { background: #1F3B25; }
        section[data-testid="stSidebar"] * { color: #F2F5F0 !important; }
        div.stButton > button, div[data-testid="stFormSubmitButton"] > button {
            background: #355E3B; color: #fff; border: 1px solid #355E3B; border-radius: 6px;
        }
        div.stButton > button:hover, div[data-testid="stFormSubmitButton"] > button:hover {
            background: #274430; border-color: #274430; color: #fff;
        }
        [data-testid="stExpander"] { border: 1px solid #D8E3D6; border-radius: 8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def brand_header(title: str, subtitle: str = "") -> None:
    logo_col, text_col = st.columns([1, 6])
    with logo_col:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
        else:
            st.markdown(
                '<div style="background:#355E3B;color:#fff;border-radius:8px;padding:14px 6px;'
                'text-align:center;font-weight:700;letter-spacing:.05em;">USERVE</div>',
                unsafe_allow_html=True,
            )
    with text_col:
        st.markdown(f'<h1 style="color:#1F3B25;margin-bottom:0;">{title}</h1>', unsafe_allow_html=True)
        if subtitle:
            st.caption(subtitle)


def score_reports(frames: list[pd.DataFrame]) -> tuple[dict, pd.DataFrame]:
    data = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if data.empty:
        return {"score": 0, "completion": None, "first_rate": None, "speed": None, "quality": None, "events": [], "warnings": ["No usable records were found."], "rows": 0}, data
    status_col = column(data, "job status", "status", "service code")
    assigned_col = column(data, "date given to server", "date assigned", "date assigned/ received", "job recieved by server")
    served_col = column(data, "date served")
    court_col = column(data, "court date", "court date-time")
    attempts_col = column(data, "attempts")
    total_col = column(data, "total attempts")
    clean = data.dropna(how="all").copy()
    statuses = clean[status_col].map(normal) if status_col else pd.Series("", index=clean.index)
    completed = statuses.str.contains(r"served|completed|returned|non-serve", regex=True, na=False)
    served = statuses.str.contains(r"^served|completed", regex=True, na=False)
    # Open inventory reports can lack statuses; they must never be counted as completed service.
    completion = (served[completed].mean() * 100) if completed.any() else None
    assigned = clean[assigned_col].map(parse_date) if assigned_col else pd.Series([None] * len(clean))
    served_dates = clean[served_col].map(parse_date) if served_col else pd.Series([None] * len(clean))
    intervals = [(end - start).days for start, end in zip(assigned, served_dates) if start and end and end >= start]
    speed = sum(intervals) / len(intervals) if intervals else None
    event_sets = clean[attempts_col].map(attempts_from_text) if attempts_col else pd.Series([[] for _ in range(len(clean))])
    events = [event for group in event_sets for event in group]
    timely = [min(group) - assigned.iloc[idx] for idx, group in enumerate(event_sets) if group and assigned.iloc[idx]]
    first_rate = (sum(item <= timedelta(days=3) for item in timely) / len(timely) * 100) if timely else None
    coverage = classify_attempts(events)
    unable = statuses.str.contains("unablecontact|unable contact|avoiding|time", regex=True, na=False)
    required = 0
    compliant = 0
    for idx in clean[unable].index:
        if court_col and assigned_col:
            court, given = parse_date(clean.at[idx, court_col]), parse_date(clean.at[idx, assigned_col])
            if court and given and (court - given).days < 5:
                continue
        required += 1
        attempts = event_sets.loc[idx] if idx in event_sets.index else []
        parts = classify_attempts(attempts)
        if len(attempts) >= 5 and parts["Morning (7–9 AM)"] and parts["Daytime (9 AM–5:30 PM)"] and parts["Evening (5:30–9 PM)"] and parts["Weekend"]:
            compliant += 1
    quality = compliant / required * 100 if required else None
    # 100-point score. N/M categories are excluded, rather than treated as zero.
    parts: list[tuple[float, float]] = []
    if completion is not None: parts.append((25, min(25, max(0, completion / 65 * 25))))
    if first_rate is not None: parts.append((15, first_rate / 100 * 15))
    if quality is not None: parts.append((20, quality / 100 * 20))
    if speed is not None: parts.append((10, max(0, min(10, (15 - speed) / 15 * 10))))
    if events: parts.append((10, min(10, (coverage["Morning (7–9 AM)"] > 0) * 2.5 + (coverage["Daytime (9 AM–5:30 PM)"] > 0) * 2.5 + (coverage["Evening (5:30–9 PM)"] > 0) * 2.5 + (coverage["Weekend"] > 0) * 2.5)))
    score = sum(points for _, points in parts) / sum(maximum for maximum, _ in parts) * 100 if parts else 0
    warnings = []
    if completion is None: warnings.append("No completed or returned jobs were identified; open jobs are excluded from completion rate.")
    if not events: warnings.append("No dated attempt entries were found in the Attempts column.")
    if required and quality is not None and quality < 100: warnings.append(f"{required - compliant} non-serve return(s) do not meet the five-attempt coverage requirement.")
    return {"score": round(score, 1), "completion": completion, "first_rate": first_rate, "speed": speed, "quality": quality, "events": events, "coverage": coverage, "warnings": warnings, "rows": len(clean), "completed": int(completed.sum()), "served": int(served.sum())}, clean


def add_server_form() -> None:
    with st.expander("Add contractor", expanded=False):
        with st.form("add_server", clear_on_submit=True):
            name = st.text_input("Contractor / server name *")
            company, territory = st.columns(2)
            company_value = company.text_input("Company")
            territory_value = territory.text_input("Territory")
            audit_day, audit_time = st.columns(2)
            day_value = audit_day.selectbox("Weekly audit day", WEEK_DAYS)
            time_value = audit_time.time_input("Audit time", value=datetime.strptime("09:00", "%H:%M").time())
            if st.form_submit_button("Add active contractor"):
                if not name.strip(): st.error("A contractor name is required.")
                else:
                    try:
                        execute("INSERT INTO servers (name, company, territory, audit_day, audit_time, active) VALUES (?, ?, ?, ?, ?, 1)", (name.strip(), company_value.strip(), territory_value.strip(), WEEK_DAYS.index(day_value), time_value.strftime("%H:%M")))
                        st.success(f"Added {name.strip()}.")
                    except sqlite3.IntegrityError: st.error("That contractor is already in the roster.")


def service_audit(servers: list[dict]) -> None:
    brand_header("Service Audit", "Review one accepted-assignment portfolio. Scores reflect documented results; open jobs do not count as completed services.")
    if not servers:
        st.info("Add a contractor in Management before beginning an audit.")
        return
    selected = st.selectbox("Contractor", servers, format_func=lambda x: f"{x['name']}{' · ' + x['territory'] if x['territory'] else ''}")
    token_note, token_button = st.columns([3, 1])
    token_note.caption("Generate a one-time access code so this contractor can view this review live, during the audit.")
    if token_button.button("Generate access code", key=f"gen-token-{selected['id']}"):
        code, expires = generate_server_token(selected["id"])
        st.session_state[f"latest_token_{selected['id']}"] = (code, expires)
    latest = st.session_state.get(f"latest_token_{selected['id']}")
    if latest:
        code, expires = latest
        remaining = expires - datetime.now()
        if remaining.total_seconds() > 0:
            st.info(f"Access code for {selected['name']}: **{code}** · valid for {int(remaining.total_seconds() // 60)} more minute(s), single use.")
        else:
            st.session_state.pop(f"latest_token_{selected['id']}", None)
    files = st.file_uploader("Upload the 90-day CSV report(s)", type=["csv"], accept_multiple_files=True, help="Upload the Weekly Server Audit Upload Report and any Server Performance or Server Status CSVs.")
    frames = []
    rejected = []
    for file in files or []:
        try:
            frame = pd.read_csv(file, dtype=str, keep_default_na=False)
            frames.append(frame)
        except Exception as error:
            rejected.append(f"{file.name}: {error}")
    if rejected:
        st.error("Some files could not be read: " + " | ".join(rejected))
    if not frames:
        st.info("Upload one or more CSV reports to calculate this review.")
        return
    result, cleaned = score_reports(frames)
    st.caption(f"{result['rows']:,} usable source rows · {result['completed']:,} completed/returned jobs considered · {len(result['events']):,} dated attempt events")
    cols = st.columns(5)
    cols[0].metric("Overall score", f"{result['score']:.1f}/100")
    cols[0].markdown(tier_badge_html(result['score']), unsafe_allow_html=True)
    cols[1].metric("Completion rate", "N/M" if result['completion'] is None else f"{result['completion']:.1f}%", "65% standard")
    cols[2].metric("First attempt ≤3 days", "N/M" if result['first_rate'] is None else f"{result['first_rate']:.1f}%")
    cols[3].metric("Non-serve attempt quality", "N/M" if result['quality'] is None else f"{result['quality']:.1f}%")
    cols[4].metric("Average days to service", "N/M" if result['speed'] is None else f"{result['speed']:.1f}")
    if result.get("coverage"):
        st.subheader("Attempt coverage")
        st.bar_chart(pd.DataFrame({"attempts": result["coverage"]}))
    for warning in result["warnings"]:
        st.warning(warning)
    st.subheader("Auditor discussion points")
    if result['completion'] is not None and result['completion'] < 65: st.error("Completion is below the 65% acceptable rate. Review territory fit, documentation and replacement risk.")
    if result['first_rate'] is not None and result['first_rate'] < 80: st.warning("Improve initial attempts: accepted assignments should receive a first attempt within three business days unless earlier action is required.")
    if result.get("coverage", {}).get("Evening (5:30–9 PM)", 0) == 0 or result.get("coverage", {}).get("Weekend", 0) == 0: st.warning("Coverage is limited. Discuss independently adding evening and Saturday attempts where appropriate.")
    st.subheader("Urgent jobs")
    court = column(cleaned, "court date", "court date-time")
    job = column(cleaned, "job id", "jobid")
    name = column(cleaned, "servee name", "defendant")
    urgent_jobs: list[dict] = []
    if court:
        urgency = cleaned.copy()
        urgency["_court"] = urgency[court].map(parse_date)
        cutoff = datetime.now() + timedelta(days=10)
        urgency = urgency[urgency["_court"].notna() & (urgency["_court"] <= cutoff)].sort_values("_court")
        if len(urgency):
            urgent_view = urgency[[c for c in [job, name, court] if c]].head(100)
            st.dataframe(urgent_view, use_container_width=True, hide_index=True)
            urgent_jobs = urgent_view.to_dict("records")
        else: st.success("No reported court dates are within the next 10 days.")
    else: st.info("Court-date field not found in the uploaded reports.")
    note = st.text_area("Audit note / reconciliation explanation")
    save_live_audit(selected["id"], {
        "score": result["score"], "tier": tier(result["score"]),
        "completion": result["completion"], "first_rate": result["first_rate"],
        "quality": result["quality"], "speed": result["speed"],
        "coverage": result["coverage"], "warnings": result["warnings"],
        "note": note, "urgent_jobs": urgent_jobs,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    if st.button("Archive this weekly review", type="primary"):
        execute("INSERT INTO reviews (server_id, review_date, completion_rate, score, tier, first_attempt_rate, average_days_to_service, attempt_quality, report_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (selected["id"], date.today().isoformat(), result["completion"], result["score"], tier(result["score"]), result["first_rate"], result["speed"], result["quality"], note))
        execute("INSERT OR IGNORE INTO audit_completions (server_id, week_start) VALUES (?, ?)", (selected["id"], current_week().isoformat()))
        st.success("Weekly review archived. The contractor has been removed from this week's audit queue.")


def management() -> None:
    brand_header("Management", "Schedule weekly contractor reviews, manage the roster, monitor trends, and remove an incorrect saved audit when necessary.")
    add_server_form()
    servers = rows("SELECT * FROM servers ORDER BY active DESC, audit_day, audit_time, name")
    active = [server for server in servers if server["active"]]
    completed_ids = {item["server_id"] for item in rows("SELECT server_id FROM audit_completions WHERE week_start = ?", (current_week().isoformat(),))}
    queue = [server for server in active if server["id"] not in completed_ids]
    a, b, c = st.columns(3)
    a.metric("Active contractors", len(active))
    b.metric("Completed this week", len(completed_ids))
    c.metric("Still to review", len(queue))
    st.subheader("This week's scheduled audit queue")
    if queue:
        for server in queue:
            left, middle, right = st.columns([4, 2, 1])
            left.write(f"**{server['name']}**  \n{server['company'] or 'Independent contractor'}")
            middle.write(f"{WEEK_DAYS[server['audit_day']]} · {server['audit_time']}")
            if right.button("Complete", key=f"complete-{server['id']}"):
                execute("INSERT OR IGNORE INTO audit_completions (server_id, week_start) VALUES (?, ?)", (server["id"], current_week().isoformat()))
                st.rerun()
    else: st.success("All active contractors are complete for this week.")
    st.subheader("Contractor roster")
    for server in servers:
        with st.expander(f"{server['name']} · {'Active' if server['active'] else 'Inactive'}"):
            st.write(f"{server['company'] or 'Independent contractor'}")
            one, two, three = st.columns(3)
            if one.button("Mark inactive" if server['active'] else "Reactivate", key=f"active-{server['id']}"):
                execute("UPDATE servers SET active = ? WHERE id = ?", (0 if server["active"] else 1, server["id"]))
                st.rerun()
            if server['id'] in completed_ids and two.button("Undo completion", key=f"undo-{server['id']}"):
                execute("DELETE FROM audit_completions WHERE server_id = ? AND week_start = ?", (server["id"], current_week().isoformat()))
                st.rerun()
    st.subheader("Portfolio performance and saved audit history")
    reviews = rows("SELECT r.*, s.name FROM reviews r JOIN servers s ON s.id = r.server_id ORDER BY r.review_date DESC, r.created_at DESC")
    if reviews:
        frame = pd.DataFrame(reviews)
        st.dataframe(frame[["review_date", "name", "score", "tier", "completion_rate", "first_attempt_rate", "average_days_to_service"]], use_container_width=True, hide_index=True)
        trends = frame.copy(); trends["review_date"] = pd.to_datetime(trends["review_date"])
        st.line_chart(trends.pivot_table(index="review_date", columns="name", values="score", aggfunc="last"))
        st.subheader("Delete incorrect saved audit")
        review_options = {f"{r['review_date']} — {r['name']} — {r['score']:.1f}": r for r in reviews}
        delete_choice = st.selectbox("Saved audit", list(review_options))
        if st.button("Delete selected saved audit", type="secondary"):
            execute("DELETE FROM reviews WHERE id = ?", (review_options[delete_choice]["id"],))
            st.success("Saved audit deleted."); st.rerun()
    else: st.info("No weekly reviews have been archived yet.")


def contractor_login() -> None:
    brand_header("Contractor Access", "Enter the one-time access code your auditor gave you to view your current weekly review.")
    with st.form("contractor_login_form", clear_on_submit=True):
        code = st.text_input("Access code", max_chars=12)
        submitted = st.form_submit_button("View my review")
    if submitted:
        if not code.strip():
            st.error("Enter the access code your auditor gave you.")
            return
        token = consume_server_token(code)
        if not token:
            st.error("That code is invalid or has expired. Ask your auditor to generate a new one.")
            return
        st.session_state["server_session"] = {
            "server_id": token["server_id"],
            "expires_at": token["expires_at"],
        }
        st.rerun()


def contractor_session_view(server_session: dict) -> None:
    server_id = server_session["server_id"]
    expires_at = datetime.fromisoformat(server_session["expires_at"])
    remaining = expires_at - datetime.now()
    server_rows = rows("SELECT * FROM servers WHERE id = ?", (server_id,))
    server = server_rows[0] if server_rows else None
    if not server:
        brand_header("Your Current Weekly Review")
        st.error("This contractor record could not be found. Ask your auditor for a new code.")
        st.session_state.pop("server_session", None)
        return
    brand_header("Your Current Weekly Review", f"{server['name']}{' · ' + server['territory'] if server['territory'] else ''}")
    if st_autorefresh is not None:
        st_autorefresh(interval=30_000, key="contractor_live_refresh")
    top1, top2, top3 = st.columns([2, 1, 1])
    top1.info(f"Session active · expires in {max(0, int(remaining.total_seconds() // 60))}m {max(0, int(remaining.total_seconds() % 60))}s")
    if top2.button("Refresh"):
        st.rerun()
    if top3.button("End session"):
        st.session_state.pop("server_session", None)
        st.rerun()
    snap = load_live_audit(server_id)
    if not snap:
        st.info("Your auditor hasn't uploaded any reports for this week's review yet. Check back soon.")
        return
    st.caption(f"Last updated {snap['generated_at']}")
    cols = st.columns(5)
    cols[0].metric("Overall score", f"{snap['score']:.1f}/100")
    cols[0].markdown(tier_badge_html(snap['score']), unsafe_allow_html=True)
    cols[1].metric("Completion rate", "N/M" if snap['completion'] is None else f"{snap['completion']:.1f}%", "65% standard")
    cols[2].metric("First attempt ≤3 days", "N/M" if snap['first_rate'] is None else f"{snap['first_rate']:.1f}%")
    cols[3].metric("Non-serve attempt quality", "N/M" if snap['quality'] is None else f"{snap['quality']:.1f}%")
    cols[4].metric("Average days to service", "N/M" if snap['speed'] is None else f"{snap['speed']:.1f}")
    if snap.get("coverage"):
        st.subheader("Attempt coverage")
        st.bar_chart(pd.DataFrame({"attempts": snap["coverage"]}))
    for warning in snap["warnings"]:
        st.warning(warning)
    st.subheader("Urgent jobs")
    if snap.get("urgent_jobs"):
        st.dataframe(pd.DataFrame(snap["urgent_jobs"]), use_container_width=True, hide_index=True)
    else:
        st.success("No reported court dates are within the next 10 days.")
    st.subheader("Auditor note")
    st.write(snap.get("note") or "No note recorded yet.")


def dashboard() -> None:
    brand_header("Dashboard", "This week's audit progress and combined urgent jobs across all in-progress reviews.")
    servers = rows("SELECT * FROM servers WHERE active = 1 ORDER BY name")
    completed_ids = {item["server_id"] for item in rows("SELECT server_id FROM audit_completions WHERE week_start = ?", (current_week().isoformat(),))}
    a, b, c = st.columns(3)
    a.metric("Active contractors", len(servers))
    b.metric("Completed this week", len(completed_ids))
    c.metric("Still to review", len(servers) - len(completed_ids))
    st.subheader("Combined urgent jobs (next 10 days)")
    combined = []
    for server in servers:
        snap = load_live_audit(server["id"])
        if snap and snap.get("urgent_jobs"):
            for job in snap["urgent_jobs"]:
                combined.append({"Contractor": server["name"], **job})
    if combined:
        st.dataframe(pd.DataFrame(combined), use_container_width=True, hide_index=True)
    else:
        st.info("No urgent jobs have been recorded yet from this week's in-progress audits. Urgent jobs appear here as soon as an auditor uploads CSVs for a contractor in Service Audit.")


def main() -> None:
    st.set_page_config(page_title="UServe Contractor Service Review", page_icon="US", layout="wide")
    inject_theme()
    setup()
    server_session = st.session_state.get("server_session")
    if server_session:
        if datetime.now() < datetime.fromisoformat(server_session["expires_at"]):
            contractor_session_view(server_session)
            return
        st.session_state.pop("server_session", None)
        st.warning("Your session has expired. Ask your auditor for a new access code.")
    if LOGO_PATH.exists():
        st.sidebar.image(str(LOGO_PATH), use_container_width=True)
    else:
        st.sidebar.title("UServe")
    st.sidebar.caption("Contractor Service Review Console")
    section = st.sidebar.radio("Workspace", ["Dashboard", "Service Audit", "Management", "Contractor Login"])
    st.sidebar.divider()
    st.sidebar.caption("90-day review window · 65% completion standard")
    if section == "Dashboard": dashboard()
    elif section == "Service Audit": service_audit(rows("SELECT * FROM servers WHERE active = 1 ORDER BY name"))
    elif section == "Management": management()
    else: contractor_login()


if __name__ == "__main__":
    main()
