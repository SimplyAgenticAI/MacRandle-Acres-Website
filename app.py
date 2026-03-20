import os
import re
import json
import base64
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, request, redirect, url_for, session, flash, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash

from openai import OpenAI
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DB_PATH = os.getenv("DB_PATH", "ted_email_assistant.db")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
SETUP_CODE = os.getenv("SETUP_CODE", "ted-setup-2026")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def now_iso():
    return datetime.utcnow().isoformat()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_owner INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT UNIQUE,
            thread_id TEXT,
            sender_name TEXT,
            sender_email TEXT,
            recipient TEXT,
            subject TEXT,
            snippet TEXT,
            body TEXT,
            internal_date TEXT,
            category TEXT DEFAULT 'unreviewed',
            priority_score INTEGER DEFAULT 0,
            needs_reply INTEGER DEFAULT 0,
            is_spam INTEGER DEFAULT 0,
            ai_summary TEXT,
            ai_reason TEXT,
            suggested_reply TEXT,
            labels_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def owner_exists():
    conn = get_db()
    row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    conn.close()
    return bool(row)


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


init_db()


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def parse_headers(headers):
    result = {}
    for h in headers:
        result[h.get("name", "").lower()] = h.get("value", "")
    return result


def parse_email_address(raw):
    raw = raw or ""
    m = re.match(r'\s*"?([^"<]*)"?\s*<([^>]+)>', raw)
    if m:
        return (m.group(1).strip() or m.group(2).strip(), m.group(2).strip())
    return raw.strip(), raw.strip()


def decode_b64url(data):
    if not data:
        return ""
    data += "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_plain_text(payload):
    if not payload:
        return ""

    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if mime_type == "text/plain" and data:
        return decode_b64url(data)

    if mime_type == "text/html" and data:
        html = decode_b64url(data)
        html = re.sub(r"(?i)<br\s*/?>", "\n", html)
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    parts = payload.get("parts", []) or []
    chunks = []
    for part in parts:
        chunk = extract_plain_text(part)
        if chunk:
            chunks.append(chunk)
    return "\n\n".join(chunks).strip()


def get_google_flow(state=None):
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
        state=state,
    )


def get_saved_credentials():
    raw = get_setting("google_token_json")
    if not raw:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        set_setting("google_token_json", creds.to_json())
    return creds


def get_gmail_service():
    creds = get_saved_credentials()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def upsert_email(row):
    conn = get_db()
    exists = conn.execute("SELECT id FROM emails WHERE gmail_id=?", (row["gmail_id"],)).fetchone()
    fields = [
        "gmail_id", "thread_id", "sender_name", "sender_email", "recipient", "subject", "snippet",
        "body", "internal_date", "category", "priority_score", "needs_reply", "is_spam",
        "ai_summary", "ai_reason", "suggested_reply", "labels_json", "created_at", "updated_at"
    ]
    values = [row.get(k) for k in fields]

    if exists:
        set_clause = ", ".join([f"{k}=?" for k in fields[1:]])
        conn.execute(
            f"UPDATE emails SET {set_clause} WHERE gmail_id=?",
            values[1:] + [row["gmail_id"]],
        )
    else:
        placeholders = ", ".join(["?"] * len(fields))
        conn.execute(
            f"INSERT INTO emails ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
    conn.commit()
    conn.close()


def fetch_and_store_messages(limit=75, query="in:inbox newer_than:180d"):
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected yet.")

    result = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    messages = result.get("messages", []) or []
    count = 0

    for msg in messages:
        full = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        payload = full.get("payload", {})
        headers = parse_headers(payload.get("headers", []))
        sender_name, sender_email = parse_email_address(headers.get("from", ""))

        row = {
            "gmail_id": full.get("id"),
            "thread_id": full.get("threadId", ""),
            "sender_name": sender_name,
            "sender_email": sender_email,
            "recipient": headers.get("to", ""),
            "subject": headers.get("subject", "(No Subject)"),
            "snippet": full.get("snippet", ""),
            "body": extract_plain_text(payload),
            "internal_date": full.get("internalDate", ""),
            "category": "unreviewed",
            "priority_score": 0,
            "needs_reply": 0,
            "is_spam": 0,
            "ai_summary": "",
            "ai_reason": "",
            "suggested_reply": "",
            "labels_json": json.dumps(full.get("labelIds", [])),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        upsert_email(row)
        count += 1

    return count


def gmail_modify(gmail_id, add_labels=None, remove_labels=None):
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected yet.")
    body = {"addLabelIds": add_labels or [], "removeLabelIds": remove_labels or []}
    service.users().messages().modify(userId="me", id=gmail_id, body=body).execute()


def gmail_trash(gmail_id):
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected yet.")
    service.users().messages().trash(userId="me", id=gmail_id).execute()


def gmail_send_reply(thread_id, to_email, subject, body_text):
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected yet.")
    raw = (
        f"To: {to_email}\r\n"
        f"Subject: Re: {subject}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body_text}"
    )
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")
    service.users().messages().send(
        userId="me",
        body={"raw": encoded, "threadId": thread_id}
    ).execute()


def safe_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def analyze_email_with_ai(subject, sender, snippet, body):
    body = (body or "")[:12000]

    if not client:
        return {
            "category": "important",
            "priority_score": 60,
            "needs_reply": True,
            "is_spam": False,
            "summary": "OpenAI is not connected yet, so this is a fallback review.",
            "reason": "Fallback mode because OPENAI_API_KEY is missing.",
            "suggested_reply": "Thanks for your email. I received this and will follow up shortly.",
        }

    prompt = f"""
You are Ted's private executive email assistant.
Return strict JSON only with these keys:
category, priority_score, needs_reply, is_spam, summary, reason, suggested_reply

Rules:
- category must be one of: spam, promo, important, client, finance, personal, other
- priority_score must be an integer from 0 to 100
- needs_reply must be true or false
- is_spam must be true or false
- summary must be 1 to 2 short sentences
- reason must briefly explain the classification
- suggested_reply must be plain text if a reply is needed, otherwise an empty string
- be conservative about marking real email as spam

Email:
From: {sender}
Subject: {subject}
Snippet: {snippet}
Body: {body}
"""

    response = client.responses.create(model=OPENAI_MODEL, input=prompt)
    text = getattr(response, "output_text", "") or ""
    data = safe_json_loads(text)
    if not data:
        text = text.replace("```json", "").replace("```", "").strip()
        data = safe_json_loads(text)
    if not data:
        return {
            "category": "other",
            "priority_score": 50,
            "needs_reply": False,
            "is_spam": False,
            "summary": "AI response could not be parsed cleanly.",
            "reason": "Parser fallback",
            "suggested_reply": "",
        }
    return data


def analyze_one_email(email_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
    conn.close()
    if not row:
        return False

    data = analyze_email_with_ai(
        row["subject"],
        f'{row["sender_name"]} <{row["sender_email"]}>',
        row["snippet"],
        row["body"],
    )

    conn = get_db()
    conn.execute("""
        UPDATE emails
        SET category=?, priority_score=?, needs_reply=?, is_spam=?, ai_summary=?, ai_reason=?, suggested_reply=?, updated_at=?
        WHERE id=?
    """, (
        data.get("category", "other"),
        int(data.get("priority_score", 50)),
        1 if data.get("needs_reply") else 0,
        1 if data.get("is_spam") else 0,
        data.get("summary", ""),
        data.get("reason", ""),
        data.get("suggested_reply", ""),
        now_iso(),
        email_id,
    ))
    conn.commit()
    conn.close()
    return True


def analyze_all_unreviewed(limit=30):
    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM emails WHERE category='unreviewed' ORDER BY COALESCE(internal_date, '') DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    total = 0
    for row in rows:
        if analyze_one_email(row["id"]):
            total += 1
    return total


def render_page(body):
    return render_template_string("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ted Email Assistant</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, Arial, sans-serif; background: #0b1220; color: #e5e7eb; }
    .wrap { max-width: 1500px; margin: 0 auto; padding: 20px; }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 18px; }
    .brand { font-size: 28px; font-weight: 800; }
    .muted { color: #94a3b8; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 18px; padding: 16px; box-shadow: 0 10px 30px rgba(0,0,0,.18); }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
    .stat { background: #0f172a; border: 1px solid #1e293b; border-radius: 16px; padding: 14px; }
    .stat .num { font-size: 28px; font-weight: 800; }
    .controls { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 16px; }
    .btn { background: #2563eb; color: white; border: 0; border-radius: 12px; padding: 10px 14px; font-weight: 700; cursor: pointer; text-decoration: none; display: inline-block; }
    .btn.gray { background: #334155; }
    .btn.green { background: #15803d; }
    .btn.red { background: #b91c1c; }
    .btn.orange { background: #b45309; }
    .grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 18px; }
    .table-wrap { overflow: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 12px 10px; border-bottom: 1px solid #1f2937; vertical-align: top; }
    th { color: #94a3b8; font-size: 13px; }
    a { color: #bfdbfe; text-decoration: none; }
    .pill { display: inline-block; padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; }
    .pill.spam { background: #3f1d1d; color: #fecaca; }
    .pill.promo { background: #3b0764; color: #e9d5ff; }
    .pill.important { background: #172554; color: #bfdbfe; }
    .pill.client { background: #052e16; color: #bbf7d0; }
    .pill.finance { background: #3f2a00; color: #fde68a; }
    .pill.personal { background: #1f2937; color: #cbd5e1; }
    .pill.other { background: #0f172a; color: #cbd5e1; }
    .pill.unreviewed { background: #1e293b; color: #cbd5e1; }
    .flash { background: #1d4ed8; border-radius: 12px; padding: 10px 12px; margin-bottom: 12px; }
    input, select, textarea { width: 100%; background: #0b1220; color: #e5e7eb; border: 1px solid #334155; border-radius: 12px; padding: 10px; }
    textarea { min-height: 180px; }
    .detail h2, .detail h3 { margin-top: 0; }
    .bodybox { white-space: pre-wrap; line-height: 1.45; background: #0b1220; border: 1px solid #1f2937; border-radius: 14px; padding: 14px; max-height: 420px; overflow: auto; }
    .auth-card { max-width: 520px; margin: 70px auto; }
    label { display: block; margin-bottom: 6px; font-weight: 600; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } .stats { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 640px) { .stats, .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for msg in messages %}<div class="flash">{{ msg }}</div>{% endfor %}
      {% endif %}
    {% endwith %}
    {{ body|safe }}
  </div>
</body>
</html>
""", body=body)


def category_pill(category):
    category = (category or "other").lower()
    allowed = {"spam", "promo", "important", "client", "finance", "personal", "other", "unreviewed"}
    css = category if category in allowed else "other"
    label = category.title()
    return f'<span class="pill {css}">{label}</span>'


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if owner_exists():
        return redirect(url_for("login"))

    if request.method == "POST":
        setup_code = request.form.get("setup_code", "").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if setup_code != SETUP_CODE:
            flash("Invalid setup code.")
        elif not name or not email or not password:
            flash("Please fill in every field.")
        elif password != confirm:
            flash("Passwords do not match.")
        else:
            conn = get_db()
            conn.execute(
                "INSERT INTO users(name, email, password_hash, is_owner, created_at) VALUES(?, ?, ?, 1, ?)",
                (name, email, generate_password_hash(password), now_iso()),
            )
            conn.commit()
            user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
            conn.close()
            session["user_id"] = user_id
            session["user_name"] = name
            session["user_email"] = email
            flash("Owner account created successfully.")
            return redirect(url_for("dashboard"))

    body = """
    <div class="card auth-card">
      <div class="brand">Ted Email Assistant Setup</div>
      <p class="muted">Create the first owner account. This only appears the first time.</p>
      <form method="post">
        <label>Setup code</label>
        <input type="text" name="setup_code" placeholder="Enter setup code">
        <div style="height:12px"></div>
        <label>Name</label>
        <input type="text" name="name" placeholder="Ted">
        <div style="height:12px"></div>
        <label>Email</label>
        <input type="email" name="email" placeholder="ted@example.com">
        <div style="height:12px"></div>
        <label>Password</label>
        <input type="password" name="password" placeholder="Create a password">
        <div style="height:12px"></div>
        <label>Confirm password</label>
        <input type="password" name="confirm_password" placeholder="Confirm password">
        <div style="height:16px"></div>
        <button class="btn" type="submit">Create owner account</button>
      </form>
    </div>
    """
    return render_page(body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not owner_exists():
        return redirect(url_for("setup"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.")
        else:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            flash("Logged in successfully.")
            return redirect(url_for("dashboard"))

    body = """
    <div class="card auth-card">
      <div class="brand">Ted Email Assistant</div>
      <p class="muted">Log in to review, sort, draft, and manage Ted's inbox.</p>
      <form method="post">
        <label>Email</label>
        <input type="email" name="email" placeholder="ted@example.com">
        <div style="height:12px"></div>
        <label>Password</label>
        <input type="password" name="password" placeholder="Enter password">
        <div style="height:16px"></div>
        <button class="btn" type="submit">Login</button>
      </form>
    </div>
    """
    return render_page(body)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/google/connect")
@require_login
def google_connect():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
        flash("Google OAuth environment variables are missing.")
        return redirect(url_for("dashboard"))

    flow = get_google_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/google/callback")
def google_callback():
    state = session.get("google_oauth_state")
    if not state:
        flash("Missing Google OAuth state.")
        return redirect(url_for("dashboard"))
    flow = get_google_flow(state=state)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    set_setting("google_token_json", creds.to_json())
    flash("Gmail connected successfully.")
    return redirect(url_for("dashboard"))


@app.route("/")
@require_login
def dashboard():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) AS c FROM emails").fetchone()["c"]
    unreviewed = conn.execute("SELECT COUNT(*) AS c FROM emails WHERE category='unreviewed'").fetchone()["c"]
    spam = conn.execute("SELECT COUNT(*) AS c FROM emails WHERE is_spam=1 OR category='spam'").fetchone()["c"]
    needs_reply = conn.execute("SELECT COUNT(*) AS c FROM emails WHERE needs_reply=1").fetchone()["c"]

    filter_category = request.args.get("category", "all")
    search = (request.args.get("search", "") or "").strip()
    where = []
    params = []

    if filter_category != "all":
        if filter_category == "needs_reply":
            where.append("needs_reply=1")
        elif filter_category == "spam":
            where.append("(is_spam=1 OR category='spam')")
        else:
            where.append("category=?")
            params.append(filter_category)

    if search:
        like = f"%{search}%"
        where.append("(subject LIKE ? OR sender_name LIKE ? OR sender_email LIKE ? OR snippet LIKE ?)")
        params.extend([like, like, like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    emails = conn.execute(
        f"SELECT * FROM emails {where_sql} ORDER BY priority_score DESC, COALESCE(internal_date, '') DESC, id DESC LIMIT 250",
        params,
    ).fetchall()
    conn.close()

    rows_html = []
    for e in emails:
        rows_html.append(f"""
        <tr>
          <td><a href="/email/{e['id']}">{e['subject'] or '(No Subject)'}</a><div class="muted">{(e['ai_summary'] or e['snippet'] or '')[:180]}</div></td>
          <td>{e['sender_name'] or e['sender_email'] or ''}</td>
          <td>{category_pill(e['category'])}</td>
          <td>{int(e['priority_score'] or 0)}</td>
          <td>{"Yes" if e["needs_reply"] else "No"}</td>
        </tr>
        """)

    gmail_connected = bool(get_saved_credentials())
    openai_connected = bool(client)

    body = f"""
    <div class="top">
      <div>
        <div class="brand">Ted Email Assistant</div>
        <div class="muted">AI triage, spam detection, draft replies, and inbox cleanup.</div>
      </div>
      <div class="controls">
        <a class="btn gray" href="/logout">Logout</a>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><div class="muted">Total Stored</div><div class="num">{total}</div></div>
      <div class="stat"><div class="muted">Unreviewed</div><div class="num">{unreviewed}</div></div>
      <div class="stat"><div class="muted">Spam</div><div class="num">{spam}</div></div>
      <div class="stat"><div class="muted">Needs Reply</div><div class="num">{needs_reply}</div></div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="controls">
          <a class="btn" href="/sync">Sync Inbox</a>
          <a class="btn green" href="/analyze">Analyze Unreviewed</a>
          <a class="btn orange" href="/?category=needs_reply">Needs Reply</a>
          <a class="btn red" href="/?category=spam">Spam</a>
          <a class="btn gray" href="/?category=important">Important</a>
          <a class="btn gray" href="/?category=client">Client</a>
          <a class="btn gray" href="/?category=finance">Finance</a>
          <a class="btn gray" href="/">All</a>
        </div>

        <form method="get" class="controls">
          <div style="flex:1;min-width:220px;">
            <input type="text" name="search" value="{search}" placeholder="Search subject, sender, or snippet">
          </div>
          <div style="width:180px;">
            <select name="category">
              <option value="all" {"selected" if filter_category == "all" else ""}>All</option>
              <option value="important" {"selected" if filter_category == "important" else ""}>Important</option>
              <option value="client" {"selected" if filter_category == "client" else ""}>Client</option>
              <option value="finance" {"selected" if filter_category == "finance" else ""}>Finance</option>
              <option value="promo" {"selected" if filter_category == "promo" else ""}>Promo</option>
              <option value="personal" {"selected" if filter_category == "personal" else ""}>Personal</option>
              <option value="other" {"selected" if filter_category == "other" else ""}>Other</option>
              <option value="spam" {"selected" if filter_category == "spam" else ""}>Spam</option>
              <option value="needs_reply" {"selected" if filter_category == "needs_reply" else ""}>Needs Reply</option>
            </select>
          </div>
          <div><button class="btn" type="submit">Filter</button></div>
        </form>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Subject</th>
                <th>Sender</th>
                <th>Category</th>
                <th>Priority</th>
                <th>Reply?</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows_html) if rows_html else '<tr><td colspan="5" class="muted">No emails found.</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h3 style="margin-top:0;">Status</h3>
        <p><strong>Logged in as:</strong> {session.get("user_name", "")}</p>
        <p><strong>Gmail connected:</strong> {"Yes" if gmail_connected else "No"}</p>
        <p><strong>OpenAI connected:</strong> {"Yes" if openai_connected else "No"}</p>
        <div class="controls">
          <a class="btn" href="/google/connect">Connect Gmail</a>
        </div>
        <hr style="border-color:#1f2937;border-style:solid;border-width:1px 0 0 0;">
        <h3>Best workflow</h3>
        <p class="muted">1. Sync inbox</p>
        <p class="muted">2. Analyze unreviewed</p>
        <p class="muted">3. Open the highest priority emails</p>
        <p class="muted">4. Review or edit the suggested reply</p>
        <p class="muted">5. Send, archive, or trash</p>
      </div>
    </div>
    """
    return render_page(body)


@app.route("/sync")
@require_login
def sync_route():
    try:
        count = fetch_and_store_messages(limit=75)
        flash(f"Synced {count} emails from Gmail.")
    except Exception as e:
        flash(f"Sync failed: {e}")
    return redirect(url_for("dashboard"))


@app.route("/analyze")
@require_login
def analyze_route():
    try:
        total = analyze_all_unreviewed(limit=30)
        flash(f"Analyzed {total} emails.")
    except Exception as e:
        flash(f"Analyze failed: {e}")
    return redirect(url_for("dashboard"))


@app.route("/email/<int:email_id>", methods=["GET", "POST"])
@require_login
def email_detail(email_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
    conn.close()

    if not row:
        flash("Email not found.")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "reanalyze":
                analyze_one_email(email_id)
                flash("Email reanalyzed.")
            elif action == "send_reply":
                reply_text = request.form.get("reply_text", "").strip()
                if not reply_text:
                    flash("Reply text cannot be empty.")
                    return redirect(url_for("email_detail", email_id=email_id))
                gmail_send_reply(row["thread_id"], row["sender_email"], row["subject"], reply_text)
                conn = get_db()
                conn.execute("UPDATE emails SET needs_reply=0, suggested_reply=?, updated_at=? WHERE id=?", (
                    reply_text, now_iso(), email_id
                ))
                conn.commit()
                conn.close()
                flash("Reply sent.")
            elif action == "save_reply":
                reply_text = request.form.get("reply_text", "")
                conn = get_db()
                conn.execute("UPDATE emails SET suggested_reply=?, updated_at=? WHERE id=?", (
                    reply_text, now_iso(), email_id
                ))
                conn.commit()
                conn.close()
                flash("Draft reply saved.")
            elif action == "archive":
                gmail_modify(row["gmail_id"], remove_labels=["INBOX"])
                flash("Email archived in Gmail.")
            elif action == "trash":
                gmail_trash(row["gmail_id"])
                flash("Email moved to trash in Gmail.")
            elif action == "set_category":
                new_category = request.form.get("new_category", "other")
                is_spam = 1 if new_category == "spam" else 0
                conn = get_db()
                conn.execute(
                    "UPDATE emails SET category=?, is_spam=?, updated_at=? WHERE id=?",
                    (new_category, is_spam, now_iso(), email_id),
                )
                conn.commit()
                conn.close()
                flash("Category updated.")
        except Exception as e:
            flash(f"Action failed: {e}")

        return redirect(url_for("email_detail", email_id=email_id))

    body = f"""
    <div class="top">
      <div>
        <div class="brand">Email Detail</div>
        <div class="muted"><a href="/">← Back to dashboard</a></div>
      </div>
    </div>

    <div class="grid">
      <div class="card detail">
        <h2>{row["subject"] or "(No Subject)"}</h2>
        <p><strong>From:</strong> {row["sender_name"] or row["sender_email"]} &lt;{row["sender_email"] or ""}&gt;</p>
        <p><strong>Category:</strong> {category_pill(row["category"])}</p>
        <p><strong>Priority score:</strong> {int(row["priority_score"] or 0)}</p>
        <p><strong>Needs reply:</strong> {"Yes" if row["needs_reply"] else "No"}</p>

        <h3>AI Summary</h3>
        <div class="bodybox">{row["ai_summary"] or "Not analyzed yet."}</div>

        <h3>Why It Was Classified This Way</h3>
        <div class="bodybox">{row["ai_reason"] or ""}</div>

        <h3>Email Body</h3>
        <div class="bodybox">{row["body"] or row["snippet"] or ""}</div>
      </div>

      <div class="card">
        <h3 style="margin-top:0;">Actions</h3>

        <form method="post">
          <input type="hidden" name="action" value="reanalyze">
          <button class="btn" type="submit">Reanalyze</button>
        </form>

        <div style="height:14px"></div>

        <form method="post">
          <input type="hidden" name="action" value="set_category">
          <label>Change category</label>
          <select name="new_category">
            <option value="important" {"selected" if row["category"] == "important" else ""}>Important</option>
            <option value="client" {"selected" if row["category"] == "client" else ""}>Client</option>
            <option value="finance" {"selected" if row["category"] == "finance" else ""}>Finance</option>
            <option value="promo" {"selected" if row["category"] == "promo" else ""}>Promo</option>
            <option value="personal" {"selected" if row["category"] == "personal" else ""}>Personal</option>
            <option value="other" {"selected" if row["category"] == "other" else ""}>Other</option>
            <option value="spam" {"selected" if row["category"] == "spam" else ""}>Spam</option>
          </select>
          <div style="height:10px"></div>
          <button class="btn gray" type="submit">Save Category</button>
        </form>

        <hr style="border-color:#1f2937;border-style:solid;border-width:1px 0 0 0; margin:18px 0;">

        <h3>Draft Reply</h3>

        <form method="post">
          <input type="hidden" name="action" value="save_reply">
          <textarea name="reply_text">{row["suggested_reply"] or ""}</textarea>
          <div class="controls">
            <button class="btn gray" type="submit">Save Draft</button>
          </div>
        </form>

        <form method="post">
          <input type="hidden" name="action" value="send_reply">
          <textarea name="reply_text">{row["suggested_reply"] or ""}</textarea>
          <div class="controls">
            <button class="btn green" type="submit">Send Reply</button>
          </div>
        </form>

        <hr style="border-color:#1f2937;border-style:solid;border-width:1px 0 0 0; margin:18px 0;">

        <form method="post" onsubmit="return confirm('Archive this email in Gmail?')">
          <input type="hidden" name="action" value="archive">
          <button class="btn orange" type="submit">Archive in Gmail</button>
        </form>

        <div style="height:10px"></div>

        <form method="post" onsubmit="return confirm('Move this email to trash in Gmail?')">
          <input type="hidden" name="action" value="trash">
          <button class="btn red" type="submit">Trash in Gmail</button>
        </form>
      </div>
    </div>
    """
    return render_page(body)


@app.route("/health")
def health():
    return {"ok": True, "app": "Ted Email Assistant", "time": now_iso()}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
