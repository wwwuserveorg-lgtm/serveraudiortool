from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
from urllib.parse import urlencode
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


APP_TITLE = "UServe Contractor Service Review"
BASE = Path(__file__).resolve().parent
try:
    DB_URL = st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))
except Exception:
    DB_URL = os.getenv("DATABASE_URL", "")
try:
    PUBLIC_APP_URL = str(st.secrets.get("PUBLIC_APP_URL", os.getenv("PUBLIC_APP_URL", ""))).strip().rstrip("/")
except Exception:
    PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
REQUIRED_REPORTS = ["Jobs with attempts", "Jobs with no attempts", "Historical job detail"]
REPORT_TYPES = REQUIRED_REPORTS + ["Server performance", "Daily activity log"]

st.set_page_config(page_title=APP_TITLE, page_icon="🟢", layout="wide")
st.markdown(
    """
    <style>
    .stApp{background:#f4f7f5}.block-container{padding-top:1rem;max-width:1500px}
    [data-testid="stSidebar"]{background:#102b1c}.brand-box{background:#fff;border-radius:14px;padding:12px;margin-bottom:12px}
    .hero{background:linear-gradient(120deg,#0c4d29,#198846);color:white;border-radius:18px;padding:24px 28px;margin-bottom:16px}
    .hero h1{margin:0;font-size:2rem}.hero p{margin:.4rem 0 0;color:#dcefe3}
    .card{background:white;border:1px solid #dfe8e2;border-radius:14px;padding:16px;margin-bottom:12px}
    .warning-card{background:#fff8e8;border:1px solid #edd39a;border-left:5px solid #bd7b05;border-radius:12px;padding:14px;margin:8px 0}
    .danger-card{background:#fff0ee;border:1px solid #efc3bd;border-left:5px solid #b9362b;border-radius:12px;padding:14px;margin:8px 0}
    .good-card{background:#eaf7ef;border:1px solid #b9dfc6;border-left:5px solid #18743a;border-radius:12px;padding:14px;margin:8px 0}
    .tier{display:inline-block;padding:5px 10px;border-radius:20px;background:#e7f4eb;color:#136633;font-weight:800}
    div[data-testid="stMetric"]{background:white;border:1px solid #dfe8e2;padding:14px;border-radius:12px}
    </style>
    """,
    unsafe_allow_html=True,
)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def db_connect():
    if DB_URL:
        from sqlalchemy import create_engine
        return create_engine(DB_URL, pool_pre_ping=True).connect()
    return sqlite3.connect(BASE / "userve_auditor.db", check_same_thread=False)


def execute(sql: str, params: tuple = (), fetch: bool = False):
    conn = db_connect()
    try:
        if DB_URL:
            from sqlalchemy import text
            result = conn.execute(text(sql), params if isinstance(params, dict) else {f"p{i}": v for i, v in enumerate(params)})
            if not fetch:
                conn.commit()
            return [dict(row._mapping) for row in result] if fetch else None
        cur = conn.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()] if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


def init_db():
    conn = db_connect()
    statements = [
        """CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, company TEXT DEFAULT '', active INTEGER DEFAULT 1, frequency TEXT DEFAULT 'weekly', audit_day TEXT DEFAULT 'Monday', audit_time TEXT DEFAULT '09:00', created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, report_type TEXT NOT NULL, content TEXT NOT NULL, filename TEXT DEFAULT '', imported_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS audits (id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, audit_date TEXT NOT NULL, score REAL NOT NULL, tier TEXT NOT NULL, payload TEXT NOT NULL, completed INTEGER DEFAULT 0, reviewer_notes TEXT DEFAULT '', shared_notes TEXT DEFAULT '', audit_status TEXT DEFAULT 'draft', completed_at TEXT DEFAULT '', updated_at TEXT DEFAULT '', created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS server_tokens (token_hash TEXT PRIMARY KEY, audit_id INTEGER NOT NULL, expires_at TEXT NOT NULL, ended INTEGER DEFAULT 0, created_at TEXT DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS followup_notes (id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, note TEXT NOT NULL, status TEXT DEFAULT 'open', created_by TEXT DEFAULT '', source_audit_id INTEGER, created_at TEXT NOT NULL, closed_at TEXT DEFAULT '', closed_by TEXT DEFAULT '')""",
    ]
    try:
        for statement in statements:
            if DB_URL:
                statement = statement.replace("id INTEGER PRIMARY KEY", "id SERIAL PRIMARY KEY")
                conn.exec_driver_sql(statement)
            else:
                conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


init_db()


def migrate_db():
    """Add newer fields without deleting existing audits."""
    additions = {
        "audits": [
            ("reviewer_notes", "TEXT DEFAULT ''"),
            ("shared_notes", "TEXT DEFAULT ''"),
            ("audit_status", "TEXT DEFAULT 'draft'"),
            ("completed_at", "TEXT DEFAULT ''"),
            ("updated_at", "TEXT DEFAULT ''"),
        ],
        "server_tokens": [("created_at", "TEXT DEFAULT ''")],
    }
    for table, columns in additions.items():
        for column, definition in columns:
            try:
                run(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception:
                pass


def persistence_label() -> tuple[str, str]:
    if DB_URL:
        return "Permanent database connected", "success"
    return "Temporary SQLite mode — history can be lost when Streamlit restarts", "warning"


def read_sql(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = db_connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def q(sql: str, params: tuple = ()) -> list[dict]:
    conn = db_connect()
    try:
        if DB_URL:
            from sqlalchemy import text
            named = {f"p{i}": value for i, value in enumerate(params)}
            for i in range(len(params)):
                sql = sql.replace("?", f":p{i}", 1)
            result = conn.execute(text(sql), named)
            return [dict(row._mapping) for row in result]
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def run(sql: str, params: tuple = ()):
    conn = db_connect()
    try:
        if DB_URL:
            from sqlalchemy import text
            named = {f"p{i}": value for i, value in enumerate(params)}
            for i in range(len(params)):
                sql = sql.replace("?", f":p{i}", 1)
            conn.execute(text(sql), named)
        else:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


migrate_db()


def norm_col(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def getv(row: dict, *names: str) -> str:
    mapping = {norm_col(k): v for k, v in row.items()}
    for name in names:
        if norm_col(name) in mapping:
            return str(mapping[norm_col(name)] or "").strip()
    return ""


def parse_date(value: Any):
    if value is None or str(value).strip() in ("", "nan", "NaT", "00/00/0000"):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


ATTEMPT_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*([AP]M)\s*:\s*(.*)$", re.I | re.S)


def parse_attempts(value: Any) -> tuple[list[dict], list[str]]:
    entries = [part.strip() for part in re.split(r"<br\s*/?>|\r?\n", str(value or ""), flags=re.I) if part.strip()]
    parsed, invalid = [], []
    for entry in entries:
        match = ATTEMPT_RE.match(entry)
        if not match:
            invalid.append(entry)
            continue
        at = pd.to_datetime(f"{match.group(1)} {match.group(2)} {match.group(3)}", errors="coerce")
        if pd.isna(at):
            invalid.append(entry)
            continue
        minute = at.hour * 60 + at.minute
        window = "AM 7–9" if 420 <= minute < 540 else "Daytime 9–5:30" if 540 <= minute < 1050 else "Evening 5:30–9" if 1050 <= minute <= 1260 else "Other"
        parsed.append({"at": at.to_pydatetime(), "note": match.group(4).strip(), "window": window, "weekend": at.dayofweek == 5})
    return parsed, invalid


def dataframe_from_text(text: str) -> pd.DataFrame:
    text = clean_pasted_text(text)
    if not text:
        return pd.DataFrame()
    if text.startswith("["):
        return pd.DataFrame(json.loads(text))
    lines = text.splitlines()
    likely_headers = [0]
    header_terms = ("job id", "jobid", "servee name", "year", "lawfirm file", "received date")
    for index, line in enumerate(lines[:30]):
        normalized = norm_col(line)
        if sum(term in normalized for term in header_terms) >= 2 and index not in likely_headers:
            likely_headers.append(index)
    for start in likely_headers:
        candidate = "\n".join(lines[start:])
        for separator in ("\t", ",", "|"):
            try:
                frame = pd.read_csv(io.StringIO(candidate), sep=separator, dtype=str, keep_default_na=False, engine="python")
                if len(frame.columns) > 1:
                    if norm_col(frame.columns[0]) in ("", "unnamed: 0"):
                        frame = frame.iloc[:, 1:]
                    return frame
            except Exception:
                pass
    return pd.DataFrame()


def clean_pasted_text(text: str) -> str:
    return str(text or "").replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ").strip()


def detect_report(text: str, filename: str = "") -> str:
    text = clean_pasted_text(text)
    low = re.sub(r"\s+", " ", text[:20000].lower())
    if "performance report results" in low or ("% served" in low and "assigned->serve" in low):
        return "Server performance"
    if "jobid:" in low and "status:" in low and "dt:" in low:
        return "Daily activity log"
    frame = dataframe_from_text(text)
    columns = {norm_col(column) for column in frame.columns}
    joined_columns = " | ".join(sorted(columns))
    if (
        "attempts" in columns
        and any(name in columns for name in ("date served", "service code", "non-serve code", "lawfirm file no", "job recieved by server"))
    ) or ("lawfirm file" in joined_columns and "date served" in joined_columns and "attempts" in joined_columns):
        return "Historical job detail"
    if any("total attempts" in column for column in columns) or (
        "last attempt days ago" in joined_columns and any(term in joined_columns for term in ("1st week", "morning", "evening", "weekend"))
    ):
        return "Jobs with attempts"
    if (
        any("days from filing" in column for column in columns)
        or any("days from received" in column for column in columns)
        or any("first attempt: days prior" in column for column in columns)
        or ("date assigned/ received/filed" in joined_columns and "court date" in joined_columns)
    ):
        return "Jobs with no attempts"
    # Fallback recognition for pasted reports whose rows are irregular but whose
    # visible header is still identifiable.
    if "total attempts" in low and "last attempt days ago" in low:
        return "Jobs with attempts"
    if "days from filing/ assign/receive" in low or "first attempt: days prior to date received" in low:
        return "Jobs with no attempts"
    if "date served" in low and "attempts" in low and ("service code" in low or "lawfirm file" in low):
        return "Historical job detail"
    return "Historical job detail" if filename.lower().endswith((".csv", ".xlsx")) and "attempt" in low else "Needs mapping"


def file_to_text(uploaded) -> str:
    raw = uploaded.getvalue()
    if uploaded.name.lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(io.BytesIO(raw), dtype=str).fillna("")
        return frame.to_csv(index=False)
    return raw.decode("utf-8-sig", errors="replace")


def current_reports(server_id: int) -> dict[str, dict]:
    rows = q("SELECT * FROM reports WHERE server_id=? ORDER BY id DESC", (server_id,))
    result = {}
    for row in rows:
        result.setdefault(row["report_type"], row)
    return result


def submit_pasted_report(server_id: int, text_key: str, type_key: str, message_key: str):
    """Accept a pasted report and clear the widget only after a successful save."""
    pasted = clean_pasted_text(st.session_state.get(text_key, ""))
    chosen_type = st.session_state.get(type_key, "Auto-detect")
    if not pasted:
        st.session_state[message_key] = ("error", "Paste a report before submitting.")
        return
    report_type = detect_report(pasted) if chosen_type == "Auto-detect" else chosen_type
    if report_type == "Needs mapping":
        st.session_state[message_key] = (
            "error",
            "The report type could not be recognized. The pasted report was kept in the box—select its report type and submit again.",
        )
        return
    run(
        "INSERT INTO reports(server_id,report_type,content,filename,imported_at) VALUES(?,?,?,?,?)",
        (server_id, report_type, pasted, "Pasted report", now_iso()),
    )
    st.session_state[text_key] = ""
    st.session_state[message_key] = ("success", f"{report_type} received and saved.")


def submit_followup_note(server_id: int, note_key: str, auditor: str, message_key: str):
    note = str(st.session_state.get(note_key, "")).strip()
    if not note:
        st.session_state[message_key] = ("error", "Enter a note before saving.")
        return
    latest = q("SELECT id FROM audits WHERE server_id=? ORDER BY id DESC LIMIT 1", (server_id,))
    source_audit_id = latest[0]["id"] if latest else None
    run(
        "INSERT INTO followup_notes(server_id,note,status,created_by,source_audit_id,created_at,closed_at,closed_by) VALUES(?,?,?,?,?,?,?,?)",
        (server_id, note, "open", auditor, source_audit_id, now_iso(), "", ""),
    )
    st.session_state[note_key] = ""
    st.session_state[message_key] = ("success", "Open note saved. It will appear on the next review until marked closed.")


def historical_jobs(text: str) -> list[dict]:
    frame = dataframe_from_text(text)
    jobs = []
    for _, series in frame.iterrows():
        row = series.to_dict()
        job_id = getv(row, "Job Id", "Job ID")
        if not job_id.isdigit():
            continue
        attempts, invalid = parse_attempts(getv(row, "Attempts"))
        served = parse_date(getv(row, "Date Served"))
        served_time = getv(row, "Time Served")
        outcome_at = None
        if served:
            outcome_at = served
            if served_time:
                combined = pd.to_datetime(f"{served.date()} {served_time}", errors="coerce")
                if not pd.isna(combined):
                    outcome_at = combined.to_pydatetime()
        address = " ".join(filter(None, [getv(row, "Servee Address", "Address"), getv(row, "Servee Apt", "Apt"), getv(row, "Servee City"), getv(row, "Servee State"), getv(row, "Servee Zip")]))
        jobs.append({
            "id": job_id, "name": getv(row, "Servee Name", "Defendant"), "status": getv(row, "Job Status").lower(),
            "lawfirm": getv(row, "Lawfirm File No", "Law Firm File No"), "client_id": getv(row, "Client ID", "3rd Party Ref"),
            "document": getv(row, "Document"), "address": address, "attempts": attempts, "invalid": invalid,
            "raw_attempt_count": len([x for x in re.split(r"<br\s*/?>|\r?\n", getv(row, "Attempts"), flags=re.I) if x.strip()]),
            "date_given": parse_date(getv(row, "Date Given to Server")), "server_received": parse_date(getv(row, "Job Recieved By Server", "Job Received By Server")),
            "court": parse_date(getv(row, "Court Date")), "date_served": served, "outcome_at": outcome_at,
            "service_code": norm_key(getv(row, "Service Code", "NON-Serve Code")).upper(), "manner": getv(row, "Manner"),
        })
    return jobs


def inventory_rows(text: str, attempted: bool) -> list[dict]:
    frame = dataframe_from_text(text)
    rows = []
    for _, series in frame.iterrows():
        row = series.to_dict()
        job_id = getv(row, "Job Id", "Job ID")
        if not re.fullmatch(r"\d{5,}", job_id):
            continue
        days = getv(row, "Days from File/ Assign/Receive", "Days from Filing/ Assign/Receive", "days from Received")
        total = getv(row, "Total Attempts")
        rows.append({"id": job_id, "name": getv(row, "Servee Name"), "days": float(days or 0), "total": int(float(total or 0)) if attempted else 0, "court": parse_date(getv(row, "Court Date", "Court Date-Time")), "assigned": parse_date(getv(row, "Date Given to Server", "Date Assigned/ Received", "Assigned date"))})
    return rows


def duplicate_timestamp_issues(jobs: list[dict]) -> list[dict]:
    events = []
    for job in jobs:
        events += [{"job": job, "at": event["at"], "type": "Attempt", "note": event["note"]} for event in job["attempts"]]
        if job["status"] == "completed" and job["outcome_at"]:
            events.append({"job": job, "at": job["outcome_at"], "type": "Served", "note": job["manner"]})
    groups = {}
    for event in events:
        groups.setdefault(event["at"], []).append(event)
    issues = []
    for at, group in groups.items():
        for event in group:
            job = event["job"]
            matches = []
            for other in group:
                other_job = other["job"]
                if other_job["id"] == job["id"]:
                    continue
                same_file = bool(norm_key(job["lawfirm"])) and norm_key(job["lawfirm"]) == norm_key(other_job["lawfirm"])
                same_person_address = bool(norm_key(job["name"]) and norm_key(job["address"])) and norm_key(job["name"]) == norm_key(other_job["name"]) and norm_key(job["address"]) == norm_key(other_job["address"])
                if not same_file and not same_person_address:
                    matches.append(other)
            if matches:
                issues.append({"job_id": job["id"], "servee": job["name"], "issue": "Identical timestamp appears on a different matter/address", "found": f"{at:%m/%d/%Y %I:%M %p} · {event['type']}", "matches": "; ".join(f"#{item['job']['id']} {item['job']['name']}" for item in matches)})
    return issues


def reconcile(attempted: list[dict], unattempted: list[dict], jobs: list[dict]) -> list[dict]:
    by_id = {job["id"]: job for job in jobs}
    issues = []
    for row in attempted:
        job = by_id.get(row["id"])
        if not job:
            issues.append({"job_id": row["id"], "servee": row["name"], "issue": "Job missing from historical detail", "expected": f"{row['total']} attempts", "found": "No matching Job ID"})
        elif job["invalid"]:
            issues.append({"job_id": row["id"], "servee": row["name"], "issue": "Attempt entry cannot be parsed", "expected": "Readable dated attempts", "found": f"{len(job['invalid'])} unreadable entries"})
        elif row["total"] != job["raw_attempt_count"]:
            issues.append({"job_id": row["id"], "servee": row["name"], "issue": "Attempt totals do not match", "expected": f"{row['total']} in open-jobs report", "found": f"{job['raw_attempt_count']} in historical detail"})
    for row in unattempted:
        job = by_id.get(row["id"])
        if job and job["raw_attempt_count"]:
            issues.append({"job_id": row["id"], "servee": row["name"], "issue": "Listed as no attempts but attempt history exists", "expected": "0 attempts", "found": f"{job['raw_attempt_count']} dated attempts"})
    return issues


def tier(score: float) -> str:
    if score <= 25: return "Red"
    if score <= 50: return "Blue"
    if score < 60: return "Green"
    if score < 65: return "Silver"
    if score <= 70: return "Gold"
    if score <= 75: return "Diamond"
    return "Diamond Elite"


def build_review(reports: dict[str, dict]) -> dict:
    attempted = inventory_rows(reports["Jobs with attempts"]["content"], True)
    unattempted = inventory_rows(reports["Jobs with no attempts"]["content"], False)
    jobs = historical_jobs(reports["Historical job detail"]["content"])
    today = datetime.now()
    closed = [job for job in jobs if job["status"] in ("completed", "returned") and job["date_served"] and 0 <= (today - job["date_served"]).days <= 90]
    served = [job for job in closed if job["status"] == "completed"]
    completion = 100 * len(served) / len(closed) if closed else 0
    ownership_delays = []
    for job in jobs:
        owned = job["server_received"] or job["date_given"]
        if owned and job["attempts"] and job["attempts"][0]["at"] >= owned:
            ownership_delays.append((job["attempts"][0]["at"] - owned).days)
    first_rate = 100 * sum(delay <= 3 for delay in ownership_delays) / len(ownership_delays) if ownership_delays else 0
    attempts = [event for job in jobs for event in job["attempts"]]
    off_hours = 100 * sum(event["weekend"] or event["window"] in ("AM 7–9", "Evening 5:30–9") for event in attempts) / len(attempts) if attempts else 0
    detailed = 100 * sum(len(event["note"]) >= 25 for event in attempts) / len(attempts) if attempts else 0
    aged_no_attempt = [row for row in unattempted if row["days"] > 2]
    inventory_rate = 100 * (len(attempted) + len(unattempted) - len(aged_no_attempt)) / max(1, len(attempted) + len(unattempted))
    service_days = []
    for job in served:
        owned = job["server_received"] or job["date_given"]
        if owned and job["date_served"] and job["date_served"] >= owned:
            service_days.append((job["date_served"] - owned).days)
    speed_avg = sum(service_days) / len(service_days) if service_days else None
    court_risks = []
    for row in attempted + unattempted:
        days_to_court = (row["court"] - today).days if row["court"] else None
        days_since_assign = (today - row["assigned"]).days if row["assigned"] else None
        if (days_to_court is not None and days_to_court < 10) or row["days"] > 7 or (row["total"] == 0 and row["days"] > 2):
            court_risks.append({**row, "days_to_court": days_to_court, "days_since_assigned": days_since_assign, "reason": "Court date under 10 days" if days_to_court is not None and days_to_court < 10 else "No attempt is overdue" if row["total"] == 0 else "Activity is stale"})
    scores = {
        "Service effectiveness": min(25, completion / 65 * 25),
        "First-attempt speed": min(15, first_rate / 100 * 15) if ownership_delays else None,
        "Attempt quality": min(20, ((off_hours + detailed) / 2) / 100 * 20) if attempts else None,
        "Inventory control": min(15, inventory_rate / 100 * 15),
        "Speed to service": 10 if speed_avg is not None and speed_avg <= 7 else 7 if speed_avg is not None and speed_avg <= 14 else 3 if speed_avg is not None else None,
        "Court-date protection": max(0, 10 - min(10, len([job for job in court_risks if job["days_to_court"] is not None]))),
        "Reporting & documentation": min(5, detailed / 100 * 5) if attempts else None,
    }
    measured = sum(value is not None for value in scores.values())
    score = round(sum(value or 0 for value in scores.values()) * 7 / measured, 1) if measured else 0
    reconciliation = reconcile(attempted, unattempted, jobs)
    duplicate_issues = duplicate_timestamp_issues(jobs)
    activity = []
    for job in jobs:
        for event in job["attempts"]:
            if event["at"] >= today - timedelta(days=7): activity.append({"Date": event["at"].date(), "Window": "Saturday" if event["weekend"] else event["window"], "Events": 1})
    recommendations = []
    if completion < 65: recommendations.append("Review non-serve reasons and address quality; completion is below the 65% standard.")
    if first_rate < 85 and ownership_delays: recommendations.append("Improve first-attempt compliance within three business days of accepted assignments.")
    if off_hours < 35 and attempts: recommendations.append("Increase morning, evening, and Saturday coverage while retaining control over routes and timing.")
    if aged_no_attempt: recommendations.append(f"Resolve {len(aged_no_attempt)} accepted assignment(s) with no attempt after two days.")
    return {"score": score, "tier": tier(score), "completion": completion, "first_rate": first_rate, "off_hours": off_hours, "inventory_rate": inventory_rate, "speed_avg": speed_avg, "scores": scores, "reconciliation": reconciliation, "duplicate_issues": duplicate_issues, "urgent": court_risks, "activity": activity, "recommendations": recommendations, "jobs": len(jobs), "closed": len(closed), "attempts": len(attempts)}


def logo():
    path = BASE / "userve-logo.png"
    if path.exists(): st.image(str(path), use_container_width=True)


def server_view():
    token = str(st.query_params.get("token", "") or st.session_state.get("server_token", "")).strip()
    st.markdown('<div class="hero"><h1>Contractor Service Review</h1><p>Temporary read-only review session</p></div>', unsafe_allow_html=True)
    if not token:
        token = st.text_input("Access token", type="password")
        if st.button("Open review", type="primary") and token:
            st.session_state.server_token = token.strip()
            st.rerun()
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = q("SELECT t.*, a.payload, a.score, a.tier, a.audit_date, a.shared_notes, a.audit_status FROM server_tokens t JOIN audits a ON a.id=t.audit_id WHERE t.token_hash=?", (token_hash,))
    if not rows or rows[0]["ended"] or datetime.fromisoformat(rows[0]["expires_at"]) < datetime.utcnow():
        st.error("This link is invalid, expired, or has ended.")
        return
    review = json.loads(rows[0]["payload"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", rows[0]["score"]); c2.metric("Status", rows[0]["tier"]); c3.metric("Review date", rows[0]["audit_date"])
    st.subheader("Performance breakdown")
    st.dataframe(pd.DataFrame([{"Category": key, "Points": value if value is not None else "N/M"} for key, value in review["scores"].items()]), hide_index=True, use_container_width=True)
    st.subheader("Urgent jobs")
    st.dataframe(pd.DataFrame(review["urgent"]), hide_index=True, use_container_width=True)
    st.subheader("Recommendations")
    for item in review["recommendations"]: st.info(item)
    if rows[0].get("shared_notes"):
        st.subheader("Review notes")
        st.write(rows[0]["shared_notes"])
    if st.button("Refresh review"):
        st.rerun()
    if st.button("End my view"):
        run("UPDATE server_tokens SET ended=1 WHERE token_hash=?", (token_hash,)); st.session_state.pop("server_token", None); st.rerun()


if st.query_params.get("view") == "server":
    server_view(); st.stop()

with st.sidebar:
    st.markdown('<div class="brand-box">', unsafe_allow_html=True); logo(); st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Weekly Contractor Service Reviews")
    auditor_name = st.selectbox("Auditor", ["auditor1", "auditor2", "auditor3", "auditor4", "auditor5"], key="active_auditor")
    persistence_text, persistence_kind = persistence_label()
    getattr(st, persistence_kind)(persistence_text)

servers = q("SELECT * FROM servers ORDER BY active DESC, name")
if not servers:
    run("INSERT INTO servers(name,company,active,frequency,audit_day,audit_time,created_at) VALUES(?,?,?,?,?,?,?)", ("Liz Mills", "", 1, "weekly", "Monday", "09:00", now_iso()))
    servers = q("SELECT * FROM servers ORDER BY name")

server_names = [row["name"] for row in servers]
selected_name = st.sidebar.selectbox("Contractor", server_names)
server = next(row for row in servers if row["name"] == selected_name)

st.markdown(f'<div class="hero"><h1>{server["name"]}</h1><p>{APP_TITLE} · {server["frequency"].title()} review</p></div>', unsafe_allow_html=True)
tabs = st.tabs(["Service Audit", "Reports", "Management", "History", "Server View"])

reports = current_reports(server["id"])
review = st.session_state.get(f"review_{server['id']}")

with tabs[1]:
    st.subheader("Add reports")
    left, right = st.columns([1.2, 1])
    with left:
        paste_type_key = f"paste_type_{server['id']}"
        paste_text_key = f"paste_text_{server['id']}"
        paste_message_key = f"paste_message_{server['id']}"
        chosen_type = st.selectbox("Report type", ["Auto-detect"] + REPORT_TYPES, key=paste_type_key)
        pasted = st.text_area(
            "Paste report",
            height=240,
            placeholder="Paste the complete report, including column headings…",
            key=paste_text_key,
        )
        if pasted.strip() and chosen_type == "Auto-detect":
            detected_type = detect_report(pasted)
            if detected_type == "Needs mapping":
                st.warning("Type not recognized yet. Choose the report type above; your pasted text will remain in place.")
            else:
                st.caption(f"Detected as: **{detected_type}**")
        st.button(
            "Add pasted report",
            type="primary",
            disabled=not pasted.strip(),
            on_click=submit_pasted_report,
            args=(server["id"], paste_text_key, paste_type_key, paste_message_key),
        )
        paste_message = st.session_state.pop(paste_message_key, None)
        if paste_message:
            getattr(st, paste_message[0])(paste_message[1])
    with right:
        upload_type = st.selectbox("Uploaded-file report type", ["Auto-detect"] + REPORT_TYPES, key=f"upload_type_{server['id']}")
        uploads = st.file_uploader("Upload CSV or Excel files", type=["csv", "tsv", "xlsx", "xls"], accept_multiple_files=True)
        if uploads and st.button("Add uploaded files"):
            accepted = 0
            unrecognized = []
            for upload in uploads:
                text = file_to_text(upload)
                report_type = detect_report(text, upload.name) if upload_type == "Auto-detect" else upload_type
                if report_type == "Needs mapping":
                    unrecognized.append(upload.name)
                    continue
                run("INSERT INTO reports(server_id,report_type,content,filename,imported_at) VALUES(?,?,?,?,?)", (server["id"], report_type, text, upload.name, now_iso()))
                accepted += 1
            if accepted:
                st.success(f"{accepted} file(s) received.")
            if unrecognized:
                st.error("Not added because the report type was not recognized: " + ", ".join(unrecognized) + ". Select the report type and submit again.")
            if accepted and not unrecognized:
                st.rerun()
    reports = current_reports(server["id"])
    st.subheader("Reports in this review")
    for report_type in REPORT_TYPES:
        if report_type in reports:
            row = reports[report_type]
            c1, c2, c3 = st.columns([3, 2, 1]); c1.success(report_type); c2.caption(f"{row['filename']} · {row['imported_at']}")
            if c3.button("Remove", key=f"remove_{row['id']}"): run("DELETE FROM reports WHERE id=?", (row["id"],)); st.rerun()
        else: st.caption(f"○ {report_type}{' — required' if report_type in REQUIRED_REPORTS else ' — optional'}")
    ready = all(item in reports for item in REQUIRED_REPORTS)
    if st.button("Run Contractor Service Review", type="primary", disabled=not ready):
        st.session_state[f"review_{server['id']}"] = build_review(reports); st.success("Review calculated. Open Service Audit."); st.rerun()

with tabs[0]:
    review = st.session_state.get(f"review_{server['id']}")
    if not review:
        st.info("Add the three required reports and run the Contractor Service Review from the Reports tab.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Overall score", review["score"]); c2.metric("Status", review["tier"]); c3.metric("Completion", f"{review['completion']:.1f}%"); c4.metric("Open priorities", len(review["urgent"]))
        st.subheader("Performance breakdown")
        st.dataframe(pd.DataFrame([{"Category": key, "Points": round(value,1) if value is not None else "N/M"} for key, value in review["scores"].items()]), hide_index=True, use_container_width=True)
        st.subheader("Reconciliation")
        issues = review["reconciliation"]
        if issues:
            st.warning(f"{len(issues)} reconciliation issue(s) found. The score remains provisional until reviewed.")
            st.dataframe(pd.DataFrame(issues).rename(columns={"job_id":"Job ID","servee":"Servee","issue":"Reconciliation issue","expected":"Expected","found":"Found"}), hide_index=True, use_container_width=True)
        else: st.success("Attempt counts reconcile across reports.")
        if review["duplicate_issues"]:
            with st.expander(f"Cross-job duplicate timestamps · {len(review['duplicate_issues'])}"):
                st.dataframe(pd.DataFrame(review["duplicate_issues"]), hide_index=True, use_container_width=True)
        st.subheader("Urgent jobs")
        urgent_frame = pd.DataFrame(review["urgent"])
        st.dataframe(urgent_frame, hide_index=True, use_container_width=True) if not urgent_frame.empty else st.success("No urgent jobs identified.")
        st.subheader("Last seven days")
        activity = pd.DataFrame(review["activity"])
        if not activity.empty:
            chart = activity.groupby(["Date","Window"], as_index=False)["Events"].sum(); st.plotly_chart(px.bar(chart, x="Date", y="Events", color="Window", barmode="stack"), use_container_width=True)
        else: st.caption("No dated activity was recorded in the last seven days.")
        st.subheader("Reviewer guidance")
        for item in review["recommendations"] or ["No immediate performance recommendation generated."]: st.info(item)
        st.subheader("Open follow-up notes")
        st.caption("These notes carry forward to every future review until an auditor marks them closed.")
        open_notes = q("SELECT * FROM followup_notes WHERE server_id=? AND status='open' ORDER BY id", (server["id"],))
        if not open_notes:
            st.success("No open follow-up notes for this contractor.")
        for note in open_notes:
            note_text, note_action = st.columns([6, 1.2])
            note_text.warning(f"{note['note']}\n\nOpened {note['created_at'][:10]} by {note.get('created_by') or 'auditor'}")
            if note_action.button("Mark closed", key=f"close_followup_{note['id']}", use_container_width=True):
                run("UPDATE followup_notes SET status='closed',closed_at=?,closed_by=? WHERE id=?", (now_iso(), auditor_name, note["id"]))
                st.success("Note closed. It will not appear on the next audit.")
                st.rerun()

        followup_key = f"new_followup_{server['id']}"
        followup_message_key = f"followup_message_{server['id']}"
        st.text_area("Add an open note for this and future audits", key=followup_key, height=90)
        st.button(
            "Save open note",
            type="primary",
            on_click=submit_followup_note,
            args=(server["id"], followup_key, auditor_name, followup_message_key),
        )
        followup_message = st.session_state.pop(followup_message_key, None)
        if followup_message:
            getattr(st, followup_message[0])(followup_message[1])
        closed_notes = q("SELECT * FROM followup_notes WHERE server_id=? AND status='closed' ORDER BY id DESC", (server["id"],))
        if closed_notes:
            with st.expander(f"Closed follow-up notes ({len(closed_notes)})"):
                st.dataframe(
                    pd.DataFrame([{"Note": item["note"], "Opened": item["created_at"][:10], "Closed": (item.get("closed_at") or "")[:10], "Closed by": item.get("closed_by") or ""} for item in closed_notes]),
                    hide_index=True,
                    use_container_width=True,
                )

        st.subheader("Current audit notes")
        active_drafts = q("SELECT * FROM audits WHERE server_id=? AND completed=0 ORDER BY id DESC LIMIT 1", (server["id"],))
        active_draft = active_drafts[0] if active_drafts else None
        reviewer_notes = st.text_area(
            "Internal auditor notes (not shown to contractor)",
            value=(active_draft.get("reviewer_notes", "") if active_draft else ""),
            height=130,
            key=f"reviewer_notes_{server['id']}",
        )
        shared_notes = st.text_area(
            "Call notes shared in Server View",
            value=(active_draft.get("shared_notes", "") if active_draft else ""),
            height=110,
            key=f"shared_notes_{server['id']}",
        )

        def save_current_audit(mark_complete: bool):
            stamp = now_iso()
            payload = json.dumps(review, default=str)
            if active_draft:
                run(
                    "UPDATE audits SET audit_date=?,score=?,tier=?,payload=?,completed=?,reviewer_notes=?,shared_notes=?,audit_status=?,completed_at=?,updated_at=? WHERE id=?",
                    (str(date.today()), review["score"], review["tier"], payload, int(mark_complete), reviewer_notes, shared_notes, "completed" if mark_complete else "draft", stamp if mark_complete else "", stamp, active_draft["id"]),
                )
                return active_draft["id"]
            run(
                "INSERT INTO audits(server_id,audit_date,score,tier,payload,completed,reviewer_notes,shared_notes,audit_status,completed_at,updated_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (server["id"], str(date.today()), review["score"], review["tier"], payload, int(mark_complete), reviewer_notes, shared_notes, "completed" if mark_complete else "draft", stamp if mark_complete else "", stamp, stamp),
            )
            saved = q("SELECT id FROM audits WHERE server_id=? ORDER BY id DESC LIMIT 1", (server["id"],))
            return saved[0]["id"] if saved else None

        save_col, complete_col = st.columns(2)
        if save_col.button("Save audit notes now", use_container_width=True):
            save_current_audit(False)
            st.success("Current audit and notes saved as a draft.")
            st.rerun()
        if complete_col.button("Complete audit and save", type="primary", use_container_width=True):
            audit_id = save_current_audit(True)
            if audit_id:
                run("UPDATE server_tokens SET ended=1 WHERE audit_id=?", (audit_id,))
            st.success("Audit completed, saved to history, and removed from the current due list.")
            st.session_state.pop(f"reviewer_notes_{server['id']}", None)
            st.session_state.pop(f"shared_notes_{server['id']}", None)
            st.rerun()

with tabs[2]:
    st.subheader("Contractor roster and audit schedule")
    with st.expander("Add contractor", expanded=False):
        with st.form("add_server"):
            name = st.text_input("Contractor name"); company = st.text_input("Company")
            frequency = st.selectbox("Review frequency", ["weekly","biweekly","monthly"]); day = st.selectbox("Review day", ["Monday","Tuesday","Wednesday","Thursday","Friday"]); when = st.time_input("Review time")
            if st.form_submit_button("Add contractor") and name.strip():
                try: run("INSERT INTO servers(name,company,active,frequency,audit_day,audit_time,created_at) VALUES(?,?,?,?,?,?,?)", (name.strip(), company.strip(), 1, frequency, day, when.strftime("%H:%M"), now_iso())); st.success("Contractor added."); st.rerun()
                except Exception: st.error("That contractor already exists.")
    roster = pd.DataFrame(q("SELECT id,name,company,active,frequency,audit_day,audit_time FROM servers ORDER BY active DESC,name"))
    st.dataframe(roster, hide_index=True, use_container_width=True)
    c1, c2, c3 = st.columns(3)
    if c1.button("Mark selected inactive"): run("UPDATE servers SET active=0 WHERE id=?", (server["id"],)); st.rerun()
    if c2.button("Mark selected active"): run("UPDATE servers SET active=1 WHERE id=?", (server["id"],)); st.rerun()
    if c3.button("Delete selected contractor"):
        run("DELETE FROM reports WHERE server_id=?", (server["id"],)); run("DELETE FROM audits WHERE server_id=?", (server["id"],)); run("DELETE FROM servers WHERE id=?", (server["id"],)); st.rerun()
    st.subheader("Audits due")
    due = q("SELECT s.* FROM servers s WHERE active=1 ORDER BY audit_day,audit_time")
    completed_this_week = {row["server_id"] for row in q("SELECT server_id FROM audits WHERE completed=1 AND audit_date>=?", ((date.today()-timedelta(days=date.today().weekday())).isoformat(),))}
    due_frame = pd.DataFrame([{**row, "status": "Complete" if row["id"] in completed_this_week else "Due"} for row in due])
    st.dataframe(due_frame[["name","frequency","audit_day","audit_time","status"]] if not due_frame.empty else due_frame, hide_index=True, use_container_width=True)

with tabs[3]:
    st.subheader("Saved audit history")
    audits = q("SELECT a.*,s.name FROM audits a JOIN servers s ON s.id=a.server_id WHERE a.server_id=? ORDER BY a.id DESC", (server["id"],))
    if not audits: st.info("No saved audits for this contractor yet.")
    for audit in audits:
        status = audit.get("audit_status") or ("completed" if audit.get("completed") else "draft")
        with st.expander(f"{audit['audit_date']} · {audit['score']} · {audit['tier']} · {status.title()}"):
            payload = json.loads(audit["payload"]); st.json({"score":payload["score"],"tier":payload["tier"],"completion":payload["completion"],"recommendations":payload["recommendations"]})
            st.markdown("**Internal auditor notes**")
            st.write(audit.get("reviewer_notes") or "No internal notes saved.")
            st.markdown("**Notes shared with contractor**")
            st.write(audit.get("shared_notes") or "No shared notes saved.")
            if st.button("Delete this audit", key=f"delete_audit_{audit['id']}"): run("DELETE FROM server_tokens WHERE audit_id=?", (audit["id"],)); run("DELETE FROM audits WHERE id=?", (audit["id"],)); st.rerun()
    if len(audits) > 1:
        trend = pd.DataFrame([{"Date": row["audit_date"], "Score": row["score"]} for row in reversed(audits)]); st.plotly_chart(px.line(trend, x="Date", y="Score", markers=True), use_container_width=True)

with tabs[4]:
    st.subheader("Temporary contractor view")
    st.caption("This view uses the Streamlit app itself. The contractor does not need a ChatGPT account.")
    audits = q("SELECT * FROM audits WHERE server_id=? ORDER BY completed DESC,id DESC LIMIT 1", (server["id"],))
    if not audits: st.info("Save an audit before generating a contractor view.")
    else:
        if not PUBLIC_APP_URL:
            st.warning("Add PUBLIC_APP_URL to Streamlit Secrets before generating a link. Example: https://your-app.streamlit.app")
        if st.button("Generate 15-minute server link", type="primary", disabled=not bool(PUBLIC_APP_URL)):
            run("UPDATE server_tokens SET ended=1 WHERE audit_id=?", (audits[0]["id"],))
            token = secrets.token_urlsafe(18)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = datetime.utcnow() + timedelta(minutes=15)
            run("INSERT INTO server_tokens(token_hash,audit_id,expires_at,ended,created_at) VALUES(?,?,?,0,?)", (token_hash, audits[0]["id"], expires.isoformat(), now_iso()))
            st.session_state[f"server_link_{server['id']}"] = f"{PUBLIC_APP_URL}/?{urlencode({'view':'server','token':token})}"
        link = st.session_state.get(f"server_link_{server['id']}")
        if link:
            st.success("Server link ready. It expires 15 minutes after it was generated.")
            st.text_input("Copy this complete link", value=link, key=f"copy_link_{server['id']}")
            st.link_button("Test server view in a new tab", link)
        if st.button("End all active server views for this audit"):
            run("UPDATE server_tokens SET ended=1 WHERE audit_id=?", (audits[0]["id"],))
            st.session_state.pop(f"server_link_{server['id']}", None)
            st.success("Server access ended.")
            st.rerun()
