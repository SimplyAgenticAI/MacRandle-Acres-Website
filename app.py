"""
MacRandle Acres website — Flask server with a two-view model:

  • Visitors  → the public site, read-only, showing the latest saved content.
  • Admin     → sign in at /admin/login (ADMIN_PASSWORD), then an "Edit page"
                bar appears and any text becomes click-to-type editable.
                Saving writes to content.json and goes live for everyone.

Run locally:   python app.py           (http://localhost:5000)
Render start:  gunicorn app:app

Environment variables (set these on Render → Environment):
  SECRET_KEY      — signs the admin session cookie (Render can auto-generate).
  ADMIN_PASSWORD  — the password to reach edit mode. If unset, editing is
                    disabled and the site is simply read-only for everyone.
"""
import os
import re
import json

from flask import (Flask, request, session, redirect, jsonify,
                   send_from_directory, Response)

BASE = os.path.dirname(os.path.abspath(__file__))
CONTENT_PATH = os.path.join(BASE, "content.json")
BLOCKED_FILES = {"app.py", "content.json", "requirements.txt", ".gitignore", "render.yaml", "README.md"}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()


# ---------- content store ----------
def load_content():
    try:
        with open(CONTENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_content(data):
    with open(CONTENT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_SCRIPT_RE = re.compile(r"<script.*?</script\s*>", re.I | re.S)
_ON_ATTR_RE = re.compile(r"""\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_JS_URL_RE = re.compile(r"javascript:", re.I)


def sanitize(value):
    """Editable regions are admin-authored, but strip active content anyway."""
    if not isinstance(value, str):
        return ""
    value = value[:20000]
    value = _SCRIPT_RE.sub("", value)
    value = _ON_ATTR_RE.sub("", value)
    value = _JS_URL_RE.sub("", value)
    return value


# ---------- page rendering ----------
def render_index():
    with open(os.path.join(BASE, "index.html"), encoding="utf-8") as f:
        html = f.read()
    is_admin = bool(session.get("admin"))
    payload = json.dumps(load_content(), ensure_ascii=False).replace("</", "<\\/")
    inject = ("<script>window.__ADMIN__=%s;window.__CONTENT__=%s;</script>"
              % ("true" if is_admin else "false", payload))
    html = html.replace("</head>", inject + "\n</head>", 1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return render_index()


@app.route("/api/content", methods=["GET", "POST"])
def api_content():
    if request.method == "GET":
        return jsonify(ok=True, content=load_content())
    if not session.get("admin"):
        return jsonify(ok=False, error="unauthorized"), 401
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify(ok=False, error="bad payload"), 400
    current = load_content()
    for key, val in incoming.items():
        current[str(key)] = sanitize(val)
    save_content(current)
    return jsonify(ok=True, saved=len(incoming))


# ---------- admin auth ----------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        return _login_page("Editing isn't configured yet — set the ADMIN_PASSWORD environment variable."), 200
    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/")
        return _login_page("Incorrect password."), 401
    return _login_page("")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/")


def _login_page(message):
    note = ("<p class='err'>%s</p>" % message) if message else ""
    return Response(LOGIN_HTML.replace("<!--MSG-->", note), mimetype="text/html")


# ---------- static sibling assets (logo.jpg, etc.) ----------
@app.route("/<path:path>")
def assets(path):
    if path in BLOCKED_FILES or path.endswith((".py", ".json")):
        return ("Not found", 404)
    full = os.path.join(BASE, path)
    if os.path.isfile(full):
        return send_from_directory(BASE, path)
    return ("Not found", 404)


LOGIN_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MacRandle Acres — Admin</title>
<style>
body{font-family:'Inter',system-ui,sans-serif;background:linear-gradient(160deg,#234F3D,#152e24);color:#f6f4ec;
  min-height:100vh;display:grid;place-items:center;margin:0}
.box{background:rgba(0,0,0,.22);border:1px solid rgba(224,184,98,.32);padding:36px 30px;border-radius:18px;
  width:min(340px,90vw);text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.4)}
h1{font-family:Georgia,serif;font-size:23px;margin:0 0 4px}
.sub{opacity:.72;font-size:13px;margin:0 0 20px}
input{width:100%;padding:12px 14px;border-radius:10px;border:1px solid rgba(246,244,236,.25);
  background:rgba(255,255,255,.06);color:#fff;font-size:14px;margin-bottom:12px;outline:none}
input:focus{border-color:#e0b862}
button{width:100%;padding:12px;border-radius:10px;border:none;background:#e0b862;color:#2a2005;
  font-weight:700;font-size:14px;cursor:pointer}
.err{color:#ffc2b4;font-size:13px;margin:0 0 12px}
a{color:#e0b862;font-size:12px;display:inline-block;margin-top:16px;text-decoration:none}
</style></head><body>
<form class=box method=post>
  <h1>MacRandle Acres</h1><div class=sub>Admin sign-in</div>
  <!--MSG-->
  <input type=password name=password placeholder="Admin password" autofocus autocomplete=current-password>
  <button type=submit>Sign in</button>
  <a href="/">&larr; Back to site</a>
</form></body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
