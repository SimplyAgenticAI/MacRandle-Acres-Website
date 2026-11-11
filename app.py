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
import hmac
import hashlib
import secrets

from flask import (Flask, abort, request, session, redirect, jsonify,
                   send_from_directory, Response)

BASE = os.path.dirname(os.path.abspath(__file__))


def _resolve_data_dir():
    """Pick the most persistent writable location for our JSON stores.

    Prefers DATA_DIR, then common Render/hosting persistent-disk mount points
    (these paths only exist when a disk is actually mounted there, so their
    presence strongly implies durability), then falls back to the app folder.
    """
    candidates = []
    env = (os.getenv("DATA_DIR") or "").strip()
    if env:
        candidates.append(env)
    candidates += ["/data", "/var/data", "/mnt/data", "/persistent", "/disk"]
    for d in candidates:
        try:
            if d == env and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)  # honor an explicit DATA_DIR even if not yet created
            if os.path.isdir(d) and os.access(d, os.W_OK):
                return d
        except Exception:
            continue
    return BASE


DATA = _resolve_data_dir()
DATA_IS_PERSISTENT = (os.path.abspath(DATA) != os.path.abspath(BASE))
try:
    os.makedirs(DATA, exist_ok=True)
except Exception:
    DATA = BASE
    DATA_IS_PERSISTENT = False
SITE = (os.getenv("SITE_URL") or "https://macrandleacres.com").rstrip("/")
PAGES = {
    "main": {"html": "index.html", "content": "content.json"},
    "hub":  {"html": "hub.html",   "content": "hub_content.json"},
}
BLOCKED_FILES = {"app.py", "requirements.txt", ".gitignore", "render.yaml", "README.md",
                 "index.html", "content.json", "hub_content.json", "scorecards.json", "visits.json",
                 "leads.json", "bookings.json", "ai_employees.json", "subscribers.json",
                 "ai_employee_page.html"}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()

import time as _time


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return resp


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "?"


_RL = {}


def rate_ok(bucket, key, limit, window):
    """Simple in-memory rate limit: <=limit events per window seconds per key."""
    now = _time.time()
    d = _RL.setdefault(bucket, {})
    hits = [t for t in d.get(key, []) if now - t < window]
    if len(hits) >= limit:
        d[key] = hits
        return False
    hits.append(now)
    d[key] = hits
    if len(d) > 4000:
        for k in list(d):
            if not d[k] or now - d[k][-1] > window:
                d.pop(k, None)
    return True


@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nAllow: /\nDisallow: /admin\nSitemap: %s/sitemap.xml\n" % CANONICAL_ORIGIN,
                    mimetype="text/plain")


CANONICAL_ORIGIN = "https://www.macrandleacres.com"


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(BASE, "favicon.ico", mimetype="image/x-icon")


@app.route("/favicon-32.png")
def favicon_png():
    return send_from_directory(BASE, "favicon-32.png", mimetype="image/png")


@app.route("/apple-touch-icon.png")
def apple_touch_icon():
    return send_from_directory(BASE, "apple-touch-icon.png", mimetype="image/png")


@app.route("/logo.jpg")
def legacy_logo():
    return redirect("/logo-v5.jpg", code=301)


NOT_FOUND_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Page not found | Lucid Marketing, by MacRandle Acres</title>
<meta name="description" content="That page is not on MacRandle Acres. Lucid Marketing, by MacRandle Acres.">
<meta property="og:title" content="Page not found | Lucid Marketing, by MacRandle Acres">
<meta property="og:description" content="That page is not on MacRandle Acres. Lucid Marketing, by MacRandle Acres.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.macrandleacres.com/">
<meta property="og:image" content="https://www.macrandleacres.com/og-v5.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Page not found | Lucid Marketing, by MacRandle Acres">
<meta name="twitter:description" content="That page is not on MacRandle Acres. Lucid Marketing, by MacRandle Acres.">
<meta name="twitter:image" content="https://www.macrandleacres.com/og-v5.jpg">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>body{font-family:Inter,system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;margin:0;min-height:100vh;display:grid;place-items:center;text-align:center;padding:24px}
h1{color:#234F3D;font-size:32px;margin:0 0 8px}p{color:#5c635e;font-size:17px}a{color:#a97f2a;font-weight:700}
.tag{margin-top:18px;font-style:italic;color:#a97f2a}</style></head><body>
<div><h1>Page not found</h1><p><a href="/">Back to MacRandle Acres</a></p>
<p class="tag">Remove Friction. Accelerate Growth.</p></div></body></html>"""


@app.errorhandler(404)
def page_not_found(_e):
    return Response(NOT_FOUND_HTML, status=404, mimetype="text/html")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = "".join("<url><loc>%s%s</loc></url>" % (CANONICAL_ORIGIN, u) for u in
                   ("/", "/design", "/hub", "/book", "/ai-employee/start", "/privacy"))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % urls)
    return Response(xml, mimetype="application/xml")


@app.route("/privacy")
def privacy():
    return Response(PRIVACY_HTML.replace("__SITE__", SITE), mimetype="text/html")


# ---------- content store ----------
def _content_path(page):
    return os.path.join(DATA, PAGES[page]["content"])


def load_content(page="main"):
    try:
        with open(_content_path(page), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_content(page, data):
    with open(_content_path(page), "w", encoding="utf-8") as f:
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
def tracking_head():
    """Google Analytics + Meta Pixel snippets, injected only when their IDs are
    configured as env vars (GA_ID / META_PIXEL_ID). IDs are public by design."""
    out = []
    ga = re.sub(r"[^A-Za-z0-9\-]", "", os.getenv("GA_ID", "").strip())
    if ga:
        out.append(
            '<script async src="https://www.googletagmanager.com/gtag/js?id=%s"></script>'
            '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
            'gtag("js",new Date());gtag("config","%s");</script>' % (ga, ga))
    px = re.sub(r"[^0-9]", "", os.getenv("META_PIXEL_ID", "").strip())
    if px:
        out.append(
            '<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?'
            'n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;'
            'n.loaded=!0;n.version="2.0";n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;'
            's=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,"script",'
            '"https://connect.facebook.net/en_US/fbevents.js");fbq("init","%s");fbq("track","PageView");</script>'
            '<noscript><img height="1" width="1" style="display:none" '
            'src="https://www.facebook.com/tr?id=%s&ev=PageView&noscript=1"/></noscript>' % (px, px))
    return "".join(out)


def render_page(page):
    cfg = PAGES[page]
    with open(os.path.join(BASE, cfg["html"]), encoding="utf-8") as f:
        html = f.read()
    is_admin = bool(session.get("admin"))
    payload = json.dumps(load_content(page), ensure_ascii=False).replace("</", "<\\/")
    inject = ("<script>window.__ADMIN__=%s;window.__CONTENT__=%s;window.__PAGE__=%s;</script>"
              % ("true" if is_admin else "false", payload, json.dumps(page)))
    # Don't track the signed-in owner (keeps your own visits out of the stats)
    head = ("" if is_admin else tracking_head()) + inject
    html = html.replace("</head>", head + "\n</head>", 1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    # Visitors land straight on the main offer page (no chooser in the way).
    return render_page("main")


@app.route("/offer")
def offer():
    # Old address for the main page; keep links and bookmarks working.
    return redirect("/", code=301)


@app.route("/hub")
def hub():
    return render_page("hub")


@app.route("/hub.html")
def hub_html_redirect():
    # So cross-links to /hub.html work on the web service too (and on a static site).
    return redirect("/hub")


@app.route("/api/content", methods=["GET", "POST"])
def api_content():
    if request.method == "GET":
        page = request.args.get("page", "main")
        if page not in PAGES:
            page = "main"
        return jsonify(ok=True, content=load_content(page))
    if not session.get("admin"):
        return jsonify(ok=False, error="unauthorized"), 401
    body = request.get_json(silent=True)
    if isinstance(body, dict) and "page" in body and "content" in body:
        page, incoming = body.get("page"), body.get("content")
    else:
        page, incoming = "main", body
    if page not in PAGES:
        return jsonify(ok=False, error="unknown page"), 400
    if not isinstance(incoming, dict):
        return jsonify(ok=False, error="bad payload"), 400
    current = load_content(page)
    for key, val in incoming.items():
        current[str(key)] = sanitize(val)
    save_content(page, current)
    return jsonify(ok=True, saved=len(incoming), page=page)


# ---------- admin auth ----------
def _safe_next(value):
    # Internal paths only (safe charset) — airtight against open-redirect / injection.
    return value if (isinstance(value, str) and re.match(r"^/[A-Za-z0-9/_-]*$", value)) else "/"


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    nxt = _safe_next(request.values.get("next"))
    if not ADMIN_PASSWORD:
        return _login_page("Editing isn't configured yet — set the ADMIN_PASSWORD environment variable.", nxt), 200
    if request.method == "POST":
        if not rate_ok("login", client_ip(), 8, 900):
            return _login_page("Too many attempts. Please wait a few minutes and try again.", nxt), 429
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(nxt)
        return _login_page("Incorrect password.", nxt), 401
    return _login_page("", nxt)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(_safe_next(request.values.get("next")))


def _login_page(message, nxt="/"):
    note = ("<p class='err'>%s</p>" % message) if message else ""
    html = LOGIN_HTML.replace("<!--MSG-->", note).replace("__NEXT__", nxt)
    return Response(html, mimetype="text/html")


# ---------- static sibling assets (logo-v5.jpg, etc.) ----------
@app.route("/<path:path>")
def assets(path):
    if path in BLOCKED_FILES or path.endswith((".py", ".json")):
        abort(404)
    full = os.path.join(BASE, path)
    if os.path.isfile(full):
        return send_from_directory(BASE, path)
    abort(404)


LOGIN_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>MacRandle Acres, Admin</title>
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
  <input type=hidden name=next value="__NEXT__">
  <input type=password name=password placeholder="Admin password" autofocus autocomplete=current-password>
  <button type=submit>Sign in</button>
  <a href="__NEXT__">&larr; Back</a>
</form></body></html>"""


# ========== Client Growth Scorecards ==========
import datetime
from html import escape as _esc

SCORECARDS_PATH = os.path.join(DATA, "scorecards.json")
SCORE_METRICS = [
    ("reached", "People reached", "\U0001F440", "Meta Ads Manager → Reach (+ Business Suite for organic)"),
    ("clicks", "Link clicks", "\U0001F446", "Meta Ads Manager → Link clicks"),
    ("calls", "Calls from ads", "\U0001F4DE", "Meta Ads Manager (call ads) or your tracked number"),
    ("conversations", "Conversations started", "\U0001F4AC", "Meta Ads Manager → Messaging conversations started"),
    ("seeds", "Seeds planted", "\U0001F331", "New leads you added to nurture this month (your CRM)"),
    ("appointments", "Appointments booked", "\U0001F4C5", "Your CRM or calendar"),
    ("followers", "New followers", "\U0001F33F", "Meta Business Suite → Insights → Follows"),
]
CAL_URL = "/book"


def load_scorecards():
    try:
        with open(SCORECARDS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_scorecards(d):
    with open(SCORECARDS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


@app.route("/admin/scorecards")
def admin_scorecards():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/scorecards")
    store = load_scorecards()
    rows = ""
    for sid, sc in sorted(store.items(), key=lambda kv: kv[1].get("created", ""), reverse=True):
        rows += ('<tr><td>%s</td><td>%s</td><td><a href="/s/%s" target="_blank">/s/%s</a></td>'
                 '<td><button class="del" data-id="%s">Delete</button></td></tr>') % (
                 _esc(sc.get("client") or ""), _esc(sc.get("month") or ""), sid, sid, sid)
    if not rows:
        rows = '<tr><td colspan="4" style="opacity:.55">No scorecards yet, create your first above.</td></tr>'
    fields = ""
    for key, label, icon, src in SCORE_METRICS:
        fields += ('<label class="fld"><span>%s %s</span>'
                   '<input type="number" min="0" name="%s" value="0">'
                   '<small class="src">%s</small></label>') % (icon, _esc(label), key, _esc(src))
    html = (BUILDER_HTML.replace("__ROWS__", rows).replace("__FIELDS__", fields)
            .replace("__METRIC_KEYS__", json.dumps([m[0] for m in SCORE_METRICS])))
    return Response(html, mimetype="text/html")


@app.route("/api/scorecard", methods=["POST"])
def api_scorecard():
    if not session.get("admin"):
        return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    metrics = {}
    src = data.get("metrics") or {}
    for key, *_ in SCORE_METRICS:
        try:
            metrics[key] = max(0, int(float(src.get(key, 0) or 0)))
        except Exception:
            metrics[key] = 0
    sc = {
        "client": str(data.get("client", "")).strip()[:120],
        "month": str(data.get("month", "")).strip()[:60],
        "note": str(data.get("note", "")).strip()[:400],
        "metrics": metrics,
        "created": datetime.datetime.utcnow().isoformat(),
    }
    sid = os.urandom(4).hex()
    store = load_scorecards()
    store[sid] = sc
    save_scorecards(store)
    return jsonify(ok=True, id=sid, url="/s/" + sid)


@app.route("/api/scorecard/delete", methods=["POST"])
def api_scorecard_delete():
    if not session.get("admin"):
        return jsonify(ok=False, error="unauthorized"), 401
    sid = (request.get_json(silent=True) or {}).get("id")
    store = load_scorecards()
    if sid in store:
        del store[sid]
        save_scorecards(store)
    return jsonify(ok=True)


@app.route("/s/<sid>")
def public_scorecard(sid):
    sc = load_scorecards().get(sid)
    if not sc:
        return ("Scorecard not found", 404)
    m = sc.get("metrics") or {}
    tiles = ""
    for key, label, icon, _src in SCORE_METRICS:
        tiles += ('<div class="sc-tile"><div class="sc-ic">%s</div>'
                  '<div class="sc-num" data-to="%d">0</div><div class="sc-lbl">%s</div></div>') % (
                  icon, int(m.get(key, 0) or 0), _esc(label))
    note = _esc(sc.get("note") or "")
    note_html = ('<div class="sc-note"><b>Next month\'s focus:</b> %s</div>' % note) if note else ""
    html = (SCORECARD_HTML.replace("__CLIENT__", _esc(sc.get("client") or "your team"))
            .replace("__MONTH__", _esc(sc.get("month") or "Growth Scorecard"))
            .replace("__TILES__", tiles).replace("__NOTE__", note_html).replace("__CAL__", CAL_URL))
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


BUILDER_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Scorecard Builder</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:660px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}.sub{color:#5c635e;font-size:14px}
a.back{font-size:13px;color:#5c635e;text-decoration:none}
.panel{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:16px;padding:22px;box-shadow:0 12px 30px rgba(35,49,40,.08);margin-bottom:20px}
.row{display:flex;gap:12px;flex-wrap:wrap}
.fld{display:flex;flex-direction:column;gap:5px;font-size:12.5px;font-weight:600;color:#234F3D;flex:1 1 46%;margin-bottom:12px}
.fld.full{flex:1 1 100%}
.fld input,.fld textarea{font-family:inherit;font-size:14px;padding:10px 12px;border:1px solid rgba(35,79,61,.2);border-radius:9px;font-weight:400;color:#2D2D2D}
.fld .src{font-weight:400;font-size:11px;color:#8a8f88;margin-top:1px;line-height:1.35}
.guide ol{margin:0 0 0 18px;font-size:13.5px;line-height:1.7;color:#3a4038}
.guide li{margin-bottom:7px}.guide b{color:#234F3D}.guide .src2{color:#a97f2a;font-weight:600}
button.go{background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;border:none;font-weight:700;font-size:15px;padding:13px 26px;border-radius:11px;cursor:pointer}
#result{margin-top:16px;display:none;background:rgba(35,79,61,.06);border-radius:12px;padding:16px;font-size:14px}
#result a{color:#a97f2a;font-weight:700;word-break:break-all}
.rbtns{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}
.rbtns button,.rbtns a{font-size:13px;font-weight:700;padding:9px 16px;border-radius:9px;border:1px solid rgba(35,79,61,.25);background:#fff;color:#234F3D;cursor:pointer;text-decoration:none}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{text-align:left;padding:9px 8px;border-bottom:1px solid rgba(35,79,61,.1)}
th{color:#5c635e;font-weight:600}
.del{background:none;border:none;color:#b23;cursor:pointer;font-weight:700}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Growth Scorecard Builder</h1><div class="sub">Fill in a client's numbers, then send them the link.</div></div><a class="back" href="/">&larr; Site</a></div>
<div class="panel">
  <div class="row">
    <label class="fld"><span>Client / team name</span><input id="client" placeholder="e.g. The Smith Team"></label>
    <label class="fld"><span>Month</span><input id="month" placeholder="e.g. July 2026"></label>
  </div>
  <div class="row">__FIELDS__</div>
  <label class="fld full"><span>Next month's focus (optional)</span><textarea id="note" rows="2" placeholder="What you're prioritizing next"></textarea></label>
  <button class="go" id="create">Create scorecard</button>
  <div id="result"></div>
</div>
<div class="panel guide">
  <h1 style="font-size:17px;margin-bottom:6px">&#128203; Pull your numbers in ~10 minutes</h1>
  <div class="sub" style="margin-bottom:12px">Where to grab each number every month, no guessing.</div>
  <ol>
    <li><b>Meta Ads Manager</b> (set the date range to last month): <span class="src2">Reach</span> &rarr; People reached &middot; <span class="src2">Link clicks</span> &rarr; Link clicks &middot; <span class="src2">Messaging conversations started</span> &rarr; Conversations &middot; <span class="src2">Calls</span> (if running call ads) &rarr; Calls from ads.</li>
    <li><b>Meta Business Suite &rarr; Insights</b>: organic <span class="src2">Reach</span> &rarr; add to People reached &middot; <span class="src2">Follows</span> &rarr; New followers.</li>
    <li><b>Your CRM / calendar</b>: new leads added to nurture &rarr; <span class="src2">Seeds planted</span> &middot; booked calls &rarr; <span class="src2">Appointments booked</span>.</li>
    <li>Type them into the form above &rarr; <b>Create scorecard</b> &rarr; send the client the link. Done.</li>
  </ol>
</div>
<div class="panel">
  <h1 style="font-size:17px;margin-bottom:12px">Your scorecards</h1>
  <table><thead><tr><th>Client</th><th>Month</th><th>Link</th><th></th></tr></thead><tbody>__ROWS__</tbody></table>
</div></div>
<script>
var METRICS=__METRIC_KEYS__;
document.getElementById('create').addEventListener('click',function(){
  var m={};METRICS.forEach(function(k){var el=document.getElementsByName(k)[0];m[k]=parseInt(el&&el.value||0)||0;});
  var body={client:document.getElementById('client').value,month:document.getElementById('month').value,note:document.getElementById('note').value,metrics:m};
  fetch('/api/scorecard',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(body)})
   .then(function(r){return r.json();}).then(function(j){
     if(!j.ok){alert('Error: '+(j.error||'failed'));return;}
     var url=location.origin+j.url,r=document.getElementById('result');r.style.display='block';
     r.innerHTML='Scorecard ready: <a href="'+j.url+'" target="_blank">'+url+'</a>'+
       '<div class="rbtns"><button id="cpy">Copy link</button>'+
       '<a href="mailto:?subject=Your%20Growth%20Scorecard&body='+encodeURIComponent('Here is your monthly Growth Scorecard: '+url)+'">Email to client</a>'+
       '<a href="'+j.url+'" target="_blank">Open</a></div>';
     document.getElementById('cpy').addEventListener('click',function(){navigator.clipboard.writeText(url);this.textContent='Copied!';});
   }).catch(function(){alert('Save failed, are you signed in?');});
});
document.querySelectorAll('.del').forEach(function(b){b.addEventListener('click',function(){
  if(!confirm('Delete this scorecard?'))return;
  fetch('/api/scorecard/delete',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({id:b.getAttribute('data-id')})}).then(function(){location.reload();});
});});
</script></body></html>"""


SCORECARD_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Growth Scorecard, __CLIENT__</title>
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.6;padding:28px 16px}
.card{max-width:620px;margin:0 auto;background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:22px;overflow:hidden;box-shadow:0 22px 55px rgba(35,49,40,.14)}
.head{background:linear-gradient(160deg,#26543f,#1a3b2d);color:#f6f4ec;padding:30px}
.head .mark{width:40px;height:40px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);display:grid;place-items:center;font-weight:800;color:#234F3D;margin-bottom:14px}
.head .brand{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:#e0b862;font-weight:700}
.head h1{font-size:26px;font-weight:800;margin:4px 0 2px}
.head .who{opacity:.85;font-size:14px}
.body{padding:26px 30px 30px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.sc-tile{border:1px solid rgba(35,79,61,.1);border-radius:14px;padding:16px 18px;background:linear-gradient(180deg,rgba(199,154,59,.05),#fff)}
.sc-ic{font-size:20px;margin-bottom:6px}
.sc-num{font-size:30px;font-weight:800;color:#234F3D;line-height:1}
.sc-lbl{font-size:12.5px;color:#5c635e;margin-top:5px}
.sc-note{margin-top:18px;padding:14px 16px;border-radius:12px;background:rgba(35,79,61,.06);font-size:14px}
.cta{margin-top:22px;text-align:center}
.cta a{display:inline-block;background:linear-gradient(135deg,#e0b862,#a97f2a);color:#3a2a05;font-weight:700;text-decoration:none;padding:13px 26px;border-radius:12px}
.foot{text-align:center;font-size:12px;color:#8a8f88;margin-top:18px}
@media(max-width:520px){.grid{grid-template-columns:1fr}}
</style></head><body>
<div class="card">
  <div class="head"><div class="mark">M</div><div class="brand">MacRandle Acres, Growth Scorecard</div><h1>__MONTH__</h1><div class="who">Prepared for __CLIENT__</div></div>
  <div class="body">
    <div class="grid">__TILES__</div>
    __NOTE__
    <div class="cta"><a href="__CAL__">Questions about your numbers? Book a call</a></div>
    <div class="foot">Remove Friction. Accelerate Growth.</div>
  </div>
</div>
<script>
document.querySelectorAll('.sc-num').forEach(function(el){
  var to=parseInt(el.getAttribute('data-to'))||0,st=null;
  function step(ts){st=st||ts;var p=Math.min((ts-st)/1200,1);el.textContent=Math.round((1-Math.pow(1-p,3))*to).toLocaleString();if(p<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);
});
</script></body></html>"""


# ========== Lightweight, cookieless visit analytics ==========
import threading
from urllib.parse import urlparse as _urlparse

VISITS_PATH = os.path.join(DATA, "visits.json")
_visits_lock = threading.Lock()
_BOT_RE = re.compile(r"bot|crawl|spider|slurp|bing|preview|monitor|curl|wget|python-requests|facebookexternalhit|headless|render|uptime", re.I)
_TRACK_EXACT = {"/", "/offer", "/hub"}
_SELF_HOSTS = ("macrandleacres.com", "onrender.com")


def load_visits():
    try:
        with open(VISITS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _record_visit(path, referrer, ua):
    host = ""
    if referrer:
        try:
            host = (_urlparse(referrer).netloc or "").lower()
        except Exception:
            host = ""
    if not host:
        host = "direct"
    elif any(s in host for s in _SELF_HOSTS):
        host = "(internal)"
    rec = {"t": datetime.datetime.utcnow().isoformat(timespec="seconds"),
           "p": path, "r": host, "d": "mobile" if "Mobi" in (ua or "") else "desktop"}
    with _visits_lock:
        v = load_visits()
        v.append(rec)
        if len(v) > 4000:
            v = v[-4000:]
        try:
            with open(VISITS_PATH, "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False)
        except Exception:
            pass


@app.before_request
def _track_visit():
    if request.method != "GET":
        return
    p = request.path
    if not (p in _TRACK_EXACT or p.startswith("/s/")):
        return
    ua = request.headers.get("User-Agent", "")
    if _BOT_RE.search(ua):
        return
    _record_visit(p, request.referrer, ua)


@app.route("/admin/analytics")
def admin_analytics():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/analytics")
    from collections import Counter
    v = load_visits()
    days, pages, refs, devs = {}, Counter(), Counter(), Counter()
    for r in v:
        day = (r.get("t") or "")[:10]
        days[day] = days.get(day, 0) + 1
        pages[r.get("p", "?")] += 1
        rf = r.get("r", "direct")
        if rf != "(internal)":
            refs[rf] += 1
        devs[r.get("d", "?")] += 1
    today = datetime.date.today()
    series = [((today - datetime.timedelta(days=i)).isoformat(), 0) for i in range(13, -1, -1)]
    series = [(d[5:], days.get(d, 0)) for d, _ in series]
    mx = max([c for _, c in series] + [1])
    bars = ""
    for lbl, c in series:
        bars += ('<div class="bar"><div class="bwrap"><div class="bfill" style="height:%d%%" title="%d visits"></div></div>'
                 '<div class="blbl">%s</div></div>') % (int(c / mx * 100), c, lbl)
    todayc = days.get(today.isoformat(), 0)
    last7 = sum(days.get((today - datetime.timedelta(days=i)).isoformat(), 0) for i in range(7))

    def rows(counter):
        out = "".join("<tr><td>%s</td><td>%d</td></tr>" % (_esc(str(k)), c) for k, c in counter.most_common(8))
        return out or '<tr><td colspan="2" style="opacity:.5">No data yet</td></tr>'

    html = (ANALYTICS_HTML.replace("__TOTAL__", str(len(v))).replace("__TODAY__", str(todayc))
            .replace("__LAST7__", str(last7)).replace("__BARS__", bars)
            .replace("__PAGES__", rows(pages)).replace("__REFS__", rows(refs))
            .replace("__MOB__", str(devs.get("mobile", 0))).replace("__DESK__", str(devs.get("desktop", 0))))
    return Response(html, mimetype="text/html")


ANALYTICS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Site visits</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:760px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px}
.stat{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:14px;padding:18px;box-shadow:0 8px 22px rgba(35,49,40,.06)}
.stat .n{font-size:30px;font-weight:800;color:#234F3D}.stat .l{font-size:12px;color:#5c635e;margin-top:3px}
.panel{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:16px;padding:20px;box-shadow:0 8px 22px rgba(35,49,40,.06);margin-bottom:18px}
.panel h2{font-size:15px;color:#234F3D;margin-bottom:14px}
.chart{display:flex;align-items:flex-end;gap:6px}
.bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px}
.bwrap{width:100%;height:100px;display:flex;align-items:flex-end}
.bfill{width:100%;background:linear-gradient(180deg,#e0b862,#a97f2a);border-radius:4px 4px 0 0;min-height:2px}
.blbl{font-size:9px;color:#8a8f88}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:8px 6px;border-bottom:1px solid rgba(35,79,61,.08)}td:last-child{text-align:right;font-weight:700;color:#234F3D}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.dev{display:flex;gap:24px;font-size:14px}
.note{font-size:11.5px;color:#8a8f88;margin-top:10px;line-height:1.5}
@media(max-width:600px){.two{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}}
</style></head><body><div class="wrap">
<div class="top"><h1>Site visits</h1><a class="back" href="/">&larr; Site</a></div>
<div class="stats">
  <div class="stat"><div class="n">__TOTAL__</div><div class="l">Total visits tracked</div></div>
  <div class="stat"><div class="n">__TODAY__</div><div class="l">Today</div></div>
  <div class="stat"><div class="n">__LAST7__</div><div class="l">Last 7 days</div></div>
</div>
<div class="panel"><h2>Visits, last 14 days</h2><div class="chart">__BARS__</div></div>
<div class="two">
  <div class="panel"><h2>Top pages</h2><table>__PAGES__</table></div>
  <div class="panel"><h2>Top sources</h2><table>__REFS__</table></div>
</div>
<div class="panel"><h2>Devices</h2><div class="dev"><span>&#128241; Mobile: <b>__MOB__</b></span><span>&#128187; Desktop: <b>__DESK__</b></span></div>
<div class="note">Cookieless &amp; privacy-friendly, no personal data stored. Counts reset if the server redeploys (free tier). Want permanent, richer tracking (traffic sources, ad conversions)? Ask me to add Google Analytics or your Meta Pixel.</div></div>
</div></body></html>"""


# ========== Growth Audit lead capture ==========
LEADS_PATH = os.path.join(DATA, "leads.json")
_leads_lock = threading.Lock()
LEAD_QS = [("role", "Role"), ("team", "Team size"), ("response", "Lead response time"),
           ("ads", "Running ads"), ("pain", "Biggest frustration"), ("goal", "90-day win")]


def load_leads():
    try:
        with open(LEADS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def send_lead_email(rec):
    """Email the new lead to LEAD_EMAIL when SMTP is configured (else no-op)."""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = "".join(os.getenv("SMTP_PASS", "").split())  # Gmail shows app pw with spaces; they aren't part of it
    if not (host and user and pw):
        return
    to = os.getenv("LEAD_EMAIL", "").strip() or user
    try:
        import smtplib
        from email.message import EmailMessage
        m = EmailMessage()
        m["Subject"] = "New Growth Audit lead: %s" % rec.get("name", "")
        m["From"] = user
        m["To"] = to
        lines = ["New lead from macrandleacres.com", "",
                 "Name:  %s" % rec.get("name", ""),
                 "Email: %s" % rec.get("email", ""),
                 "Phone: %s" % rec.get("phone", "")]
        for key, lbl in LEAD_QS:
            if rec.get(key):
                lines.append("%s: %s" % (lbl, rec[key]))
        lines += ["", "See all leads: %s/admin/leads" % SITE]
        m.set_content("\n".join(lines))
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(m)
    except Exception:
        pass


@app.route("/api/lead", methods=["POST"])
def api_lead():
    if not rate_ok("lead", client_ip(), 6, 600):
        return jsonify(ok=False, error="Too many submissions, please try again in a few minutes."), 429
    data = request.get_json(silent=True) or {}
    if str(data.get("website", "")).strip():          # honeypot -> silently drop bots
        return jsonify(ok=True)
    name = str(data.get("name", "")).strip()[:120]
    email = str(data.get("email", "")).strip()[:160]
    if not name or "@" not in email or "." not in email:
        return jsonify(ok=False, error="Please add your name and a valid email."), 400
    rec = {"t": datetime.datetime.utcnow().isoformat(timespec="seconds"),
           "name": name, "email": email, "phone": str(data.get("phone", "")).strip()[:40]}
    for key, _ in LEAD_QS:
        rec[key] = str(data.get(key, "")).strip()[:600]
    with _leads_lock:
        v = load_leads()
        v.append(rec)
        if len(v) > 2000:
            v = v[-2000:]
        try:
            with open(LEADS_PATH, "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False)
        except Exception:
            pass
    threading.Thread(target=send_lead_email, args=(rec,), daemon=True).start()
    return jsonify(ok=True)


@app.route("/admin/leads")
def admin_leads():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/leads")
    v = load_leads()
    cards = ""
    for r in reversed(v):
        ans = ""
        for key, lbl in LEAD_QS:
            val = r.get(key, "")
            if val:
                ans += "<div class='ans'><b>%s:</b> %s</div>" % (lbl, _esc(val))
        phone = ""
        if r.get("phone"):
            phone = " &middot; <a class='le' href='tel:%s'>%s</a>" % (_esc(r["phone"]), _esc(r["phone"]))
        cards += ("<div class='lead'><div class='lh'><div class='lhl'>"
                  "<span class='ln'>%s</span><a class='le' href='mailto:%s'>%s</a>%s</div>"
                  "<span class='lt'>%s</span></div>%s</div>") % (
                  _esc(r.get("name", "")), _esc(r.get("email", "")), _esc(r.get("email", "")),
                  phone, _esc(r.get("t", "")[:16].replace("T", " ")), ans)
    if not cards:
        cards = "<div style='opacity:.5;padding:24px 4px'>No leads yet. They'll appear here the moment someone submits the Growth Audit form.</div>"
    return Response(LEADS_HTML.replace("__COUNT__", str(len(v))).replace("__CARDS__", cards), mimetype="text/html")


LEADS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Leads</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:720px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.count{font-size:13px;color:#5c635e;margin-bottom:16px}
.lead{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:14px;padding:18px 20px;box-shadow:0 8px 22px rgba(35,49,40,.06);margin-bottom:14px}
.lh{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px}
.lhl{display:flex;flex-direction:column;gap:2px}
.ln{font-weight:800;font-size:17px;color:#234F3D}
.le{font-size:13px;color:#a97f2a;font-weight:600;text-decoration:none}
.lt{font-size:11.5px;color:#8a8f88;white-space:nowrap}
.ans{font-size:13.5px;color:#3a4038;padding:4px 0;border-top:1px solid rgba(35,79,61,.07)}
.ans b{color:#234F3D}
</style></head><body><div class="wrap">
<div class="top"><h1>Growth Audit leads</h1><div><a class="back" href="/admin/leads/export.csv" style="margin-right:16px;font-weight:700">&#11015; Export CSV</a><a class="back" href="/">&larr; Site</a></div></div>
<div class="count">__COUNT__ total</div>
__CARDS__
</div></body></html>"""


@app.route("/admin/leads/export.csv")
def admin_leads_csv():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/leads")
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "name", "email", "phone"] + [lbl for _, lbl in LEAD_QS])
    for r in load_leads():
        w.writerow([r.get("t", ""), r.get("name", ""), r.get("email", ""), r.get("phone", "")]
                   + [r.get(k, "") for k, _ in LEAD_QS])
    resp = Response(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=leads.csv"
    return resp


PRIVACY_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Privacy Policy | Lucid Marketing, by MacRandle Acres</title>
<meta name="description" content="How MacRandle Acres handles booking details, email signups, and your first AI agent setup.">
<link rel="canonical" href="https://www.macrandleacres.com/privacy">
<meta property="og:title" content="Privacy Policy | Lucid Marketing, by MacRandle Acres">
<meta property="og:description" content="How MacRandle Acres handles booking details, email signups, and your first AI agent setup.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.macrandleacres.com/privacy">
<meta property="og:image" content="https://www.macrandleacres.com/og-v5.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Privacy Policy | Lucid Marketing, by MacRandle Acres">
<meta name="twitter:description" content="How MacRandle Acres handles booking details, email signups, and your first AI agent setup.">
<meta name="twitter:image" content="https://www.macrandleacres.com/og-v5.jpg">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.7;padding:40px 18px}
.wrap{max-width:680px;margin:0 auto}h1{color:#234F3D;font-size:28px;margin-bottom:6px}.sub{color:#8a8f88;font-size:13px;margin-bottom:24px}
h2{color:#234F3D;font-size:17px;margin:22px 0 6px}p{margin-bottom:10px;font-size:15px}a{color:#a97f2a;font-weight:600}
.back{display:inline-block;margin-top:26px;font-size:14px}</style></head><body><div class="wrap">
<h1>Privacy Policy</h1><div class="sub">MacRandle Acres &middot; Jeff Randle<br>Last updated: November 11, 2026</div>
<p>This site is operated by Jeff Randle (MacRandle Acres). We respect your privacy and keep things simple.</p>
<h2>What we collect</h2>
<p>If you book a call, we collect the name, email, and phone you provide. If you join the email list, we collect your email address. If you start a first AI agent setup, we collect your name, email, phone, business details, and the answers you provide so we can prepare your setup and consultation. We also collect basic, cookieless visit counts (page, referring site, device type) with no personal data. If analytics or advertising pixels are enabled, those third parties (Google, Meta) may set their own cookies per their policies.</p>
<h2>How we use it</h2>
<p>Only to respond to your request, prepare your growth audit, and improve the site. We do not sell your information.</p>
<h2>Your choices</h2>
<p>To access, correct, or delete your information, email <a href="mailto:Macrandleacres@gmail.com">Macrandleacres@gmail.com</a> and we will take care of it.</p>
<h2>Contact</h2>
<p><a href="mailto:Macrandleacres@gmail.com">Macrandleacres@gmail.com</a> &middot; Salisbury, MD.</p>
<a class="back" href="/">&larr; Back to site</a>
</div></body></html>"""


# ========== Native branded booking ==========
try:
    from zoneinfo import ZoneInfo
    BOOK_TZ = ZoneInfo(os.getenv("BOOK_TZ", "America/New_York"))
except Exception:
    BOOK_TZ = datetime.timezone(datetime.timedelta(hours=-4))  # Eastern fallback
BOOK_TZ_LABEL = os.getenv("BOOK_TZ_LABEL", "Eastern")
BOOKINGS_PATH = os.path.join(DATA, "bookings.json")
SUBSCRIBERS_PATH = os.path.join(DATA, "subscribers.json")
BOOK_SLOT_MIN = int(os.getenv("BOOK_SLOT_MIN", "30"))
BOOK_DAYS_AHEAD = 14
BOOK_MIN_NOTICE_H = 3
# Dates with no open slots, as YYYY-MM-DD. Add or remove dates in this set.
BOOK_BLACKOUT = {
    "2026-11-26",  # Thanksgiving
}
# weekday (Mon=0) -> list of (start_hour, end_hour), Eastern
BOOK_WINDOWS = {0: [(10, 16)], 1: [(10, 16)], 2: [(10, 16)], 3: [(10, 16)], 4: [(10, 15)]}
ORG_EMAIL = (os.getenv("LEAD_EMAIL") or "Macrandleacres@gmail.com").strip()


def load_bookings():
    try:
        with open(BOOKINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def _fmt_when(dt):
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(" at %I:%M %p").replace(" 0", " ") + " " + BOOK_TZ_LABEL


def gen_slots():
    now = datetime.datetime.now(BOOK_TZ)
    cutoff = now + datetime.timedelta(hours=BOOK_MIN_NOTICE_H)
    booked = {b.get("slot") for b in load_bookings()}
    out = []
    for dd in range(BOOK_DAYS_AHEAD + 1):
        day = (now + datetime.timedelta(days=dd)).date()
        if day.isoformat() in BOOK_BLACKOUT:
            continue
        wins = BOOK_WINDOWS.get(day.weekday())
        if not wins:
            continue
        slots = []
        for sh, eh in wins:
            t = datetime.datetime(day.year, day.month, day.day, sh, 0, tzinfo=BOOK_TZ)
            end = datetime.datetime(day.year, day.month, day.day, eh, 0, tzinfo=BOOK_TZ)
            while t < end:
                iso = t.isoformat()
                if t >= cutoff and iso not in booked:
                    slots.append({"iso": iso, "label": t.strftime("%I:%M %p").lstrip("0")})
                t += datetime.timedelta(minutes=BOOK_SLOT_MIN)
        if slots:
            out.append({"date": day.isoformat(), "label": day.strftime("%a, %b ") + str(day.day), "slots": slots})
    return out


@app.route("/healthz")
def healthz():
    # Lightweight, untracked endpoint for uptime pings + deploy-version check.
    return Response("ok build-DEPLOYCHECK-7", mimetype="text/plain")


@app.route("/api/slots")
def api_slots():
    return jsonify(ok=True, tz=BOOK_TZ_LABEL, days=gen_slots())


def load_subscribers():
    try:
        with open(SUBSCRIBERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_subscribers(rows):
    with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    if not rate_ok("sub", client_ip(), 8, 600):
        return jsonify(ok=False, error="Please try again shortly."), 429
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    if _clean_line(data.get("website") or ""):
        return jsonify(ok=True)
    email = _clean_line(data.get("email") or "")[:160]
    if not _EMAIL_RE.match(email):
        return jsonify(ok=False, error="Please enter a valid email."), 400
    key = email.lower()
    with _leads_lock:
        rows = load_subscribers()
        if not any((r.get("email") or "").lower() == key for r in rows):
            rows.append({
                "email": email,
                "t": datetime.datetime.utcnow().isoformat(timespec="seconds"),
            })
            if len(rows) > 5000:
                rows = rows[-5000:]
            try:
                save_subscribers(rows)
            except Exception:
                return jsonify(ok=False, error="Could not save that just now. Please try again."), 500
    return jsonify(ok=True)


@app.route("/admin/subscribers")
def admin_subscribers():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/subscribers")
    rows = ""
    for rec in sorted(load_subscribers(), key=lambda r: r.get("t", ""), reverse=True):
        rows += "<li><a href='mailto:%s'>%s</a> <span>%s</span></li>" % (
            _esc(rec.get("email", "")), _esc(rec.get("email", "")),
            _esc((rec.get("t") or "").replace("T", " ")[:16]))
    if not rows:
        rows = "<li class='empty'>No email signups yet.</li>"
    return Response(SUBSCRIBERS_HTML.replace("__ROWS__", rows), mimetype="text/html")


SUBSCRIBERS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Email signups</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:560px;margin:0 auto}h1{font-size:23px;color:#234F3D;margin-bottom:14px}
a.back{font-size:13px;color:#5c635e;text-decoration:none}ul{list-style:none;margin-top:16px}
li{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:12px;padding:12px 14px;margin-bottom:8px;display:flex;justify-content:space-between;gap:12px}
li a{color:#a97f2a;font-weight:700;text-decoration:none}li span{color:#8a918b;font-size:12px}
.empty{color:#5c635e}</style></head><body><div class="wrap">
<p><a class="back" href="/">&larr; Site</a></p>
<h1>Email signups</h1>
<ul>__ROWS__</ul>
</div></body></html>"""


def _clean_line(s):
    """Collapse control chars/newlines to spaces so user input can't break email headers or ICS/JSON lines."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(s)).strip()


def _clean_note(s):
    """Like _clean_line but keeps newlines (audit notes are free-text and never touch email headers)."""
    s = str(s).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", s).strip()


def _ics_esc(s):
    """Escape a value for an RFC 5545 text field (backslash, semicolon, comma, newline)."""
    s = str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def make_ics(bk):
    start = datetime.datetime.fromisoformat(bk["slot"])
    end = start + datetime.timedelta(minutes=BOOK_SLOT_MIN)
    def z(dt):
        return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    join = meeting_url(bk)
    desc = "Growth Audit call with Jeff Randle (MacRandle Acres)."
    if join:
        desc += " Join the video call: " + join
    parts = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MacRandle Acres//Booking//EN", "METHOD:REQUEST",
        "BEGIN:VEVENT", "UID:%s@macrandleacres.com" % bk.get("slot", ""),
        "DTSTAMP:" + z(datetime.datetime.now(datetime.timezone.utc)),
        "DTSTART:" + z(start), "DTEND:" + z(end),
        "SUMMARY:Growth Audit Call - MacRandle Acres",
        "DESCRIPTION:" + _ics_esc(desc),
        "ORGANIZER;CN=Jeff Randle:mailto:" + ORG_EMAIL,
        "ATTENDEE;CN=%s;RSVP=TRUE:mailto:%s" % (_ics_esc(bk.get("name", "")), _ics_esc(bk.get("email", ""))),
    ]
    if join:
        parts += ["LOCATION:" + _ics_esc(join), "URL:" + join]
    parts += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(parts)


def _cancel_token(slot, email):
    key = app.secret_key
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, ("%s|%s" % (slot, email or "")).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def _cancel_url(bk):
    from urllib.parse import quote
    return "%s/book/cancel?s=%s&t=%s" % (
        SITE, quote(bk.get("slot", "")), _cancel_token(bk.get("slot", ""), bk.get("email", "")))


def meeting_url(bk):
    room = bk.get("room", "")
    return ("%s/m/%s" % (SITE, room)) if room else ""


def google_cal_link(bk):
    from urllib.parse import quote
    start = datetime.datetime.fromisoformat(bk["slot"])
    end = start + datetime.timedelta(minutes=BOOK_SLOT_MIN)
    def z(dt):
        return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    join = meeting_url(bk)
    details = "Growth Audit call with Jeff Randle (MacRandle Acres). Guest: %s <%s>" % (
        bk.get("name", ""), bk.get("email", ""))
    if join:
        details += "\nJoin the video call: " + join
    url = ("https://calendar.google.com/calendar/render?action=TEMPLATE"
           "&text=" + quote("Growth Audit Call - MacRandle Acres")
           + "&dates=" + z(start) + "/" + z(end)
           + "&details=" + quote(details))
    if join:
        url += "&location=" + quote(join)
    return url


def send_booking_emails(bk):
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = "".join(os.getenv("SMTP_PASS", "").split())  # Gmail shows app pw with spaces; they aren't part of it
    if not (host and user and pw):
        return
    sender = os.getenv("SMTP_FROM", "").strip() or user
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = os.getenv("SMTP_SSL", "").strip().lower() in ("1", "true", "yes") or port == 465
    try:
        import smtplib
        from email.message import EmailMessage
        when = _fmt_when(datetime.datetime.fromisoformat(bk["slot"]))
        gcal = google_cal_link(bk)
        ics = make_ics(bk).encode("utf-8")
        first = (bk.get("name", "").split(" ") or [""])[0] or "there"
        for to, mine in ((ORG_EMAIL, True), (bk["email"], False)):
            m = EmailMessage()
            m["From"] = sender
            m["To"] = to
            m["Reply-To"] = bk["email"] if mine else ORG_EMAIL
            if mine:
                m["Subject"] = "New call booked: %s (%s)" % (bk["name"], when)
                join = meeting_url(bk)
                m.set_content(
                    "New Growth Audit call booked.\n\n"
                    "Name:  %s\nEmail: %s\nPhone: %s\nWhen:  %s\n\n"
                    "Join the video call:\n%s\n\n"
                    "Add it to your calendar:\n%s\n"
                    % (bk["name"], bk["email"], bk.get("phone", "") or "-", when, join or "(n/a)", gcal))
            else:
                m["Subject"] = "Your Growth Audit call is booked"
                join = meeting_url(bk)
                join_line = ("Join the video call here (works right in your browser):\n%s\n\n" % join) if join else ""
                m.set_content(
                    "Hi %s,\n\nYour Growth Audit call with Jeff Randle is booked for %s.\n\n"
                    "%s"
                    "The calendar invite is attached. Prefer one click? Add it here:\n%s\n\n"
                    "Need to reschedule or cancel? Use this link:\n%s\n\n"
                    "Talk soon!\nMacRandle Acres" % (first, when, join_line, gcal, _cancel_url(bk)))
            m.add_attachment(ics, maintype="text", subtype="calendar",
                             filename="invite.ics", params={"method": "REQUEST"})
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                    s.login(user, pw)
                    s.send_message(m)
            else:
                with smtplib.SMTP(host, port, timeout=15) as s:
                    s.starttls()
                    s.login(user, pw)
                    s.send_message(m)
    except Exception:
        pass


def send_cancel_notice(bk):
    """Notify Jeff that a client cancelled (best-effort, no-op without SMTP)."""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = "".join(os.getenv("SMTP_PASS", "").split())
    if not (host and user and pw):
        return
    sender = os.getenv("SMTP_FROM", "").strip() or user
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = os.getenv("SMTP_SSL", "").strip().lower() in ("1", "true", "yes") or port == 465
    try:
        import smtplib
        from email.message import EmailMessage
        when = _fmt_when(datetime.datetime.fromisoformat(bk["slot"]))
        m = EmailMessage()
        m["From"] = sender
        m["To"] = ORG_EMAIL
        m["Reply-To"] = bk.get("email", ORG_EMAIL)
        m["Subject"] = "Call CANCELLED: %s (%s)" % (bk.get("name", ""), when)
        m.set_content("A client cancelled their Growth Audit call. The time is now open again.\n\n"
                      "Name:  %s\nEmail: %s\nPhone: %s\nWas:   %s\n"
                      % (bk.get("name", ""), bk.get("email", ""), bk.get("phone", "") or "-", when))
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, pw)
                s.send_message(m)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(m)
    except Exception:
        pass


def _smtp_send(msg):
    """Connect + deliver one message. Returns True on success, False if SMTP unset or send fails."""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = "".join(os.getenv("SMTP_PASS", "").split())
    if not (host and user and pw):
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = os.getenv("SMTP_SSL", "").strip().lower() in ("1", "true", "yes") or port == 465
    try:
        import smtplib
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
        return True
    except Exception:
        return False


# Minutes-before-call -> internal label. First scheduler tick past a threshold fires it once.
REMIND_OFFSETS = [(24 * 60, "24h"), (60, "1h")]


def _reminder_phrase(st, now):
    mins = (st - now).total_seconds() / 60.0
    if mins <= 90:
        return "in about an hour"
    if st.date() == now.date():
        return "later today"
    if st.date() == (now + datetime.timedelta(days=1)).date():
        return "tomorrow"
    return "coming up"


def _send_reminder(bk, now):
    from email.message import EmailMessage
    st = datetime.datetime.fromisoformat(bk["slot"])
    when = _fmt_when(st)
    phrase = _reminder_phrase(st, now)
    first = (bk.get("name", "").split(" ") or [""])[0] or "there"
    join = meeting_url(bk)
    m = EmailMessage()
    m["From"] = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    m["To"] = bk.get("email", "")
    m["Reply-To"] = ORG_EMAIL
    m["Subject"] = "Reminder: your Growth Audit call %s" % phrase
    body = ("Hi %s,\n\nA quick reminder that your Growth Audit call with Jeff Randle is %s "
            "(%s).\n\n" % (first, phrase, when))
    if join:
        body += "Join the video call here (right in your browser):\n%s\n\n" % join
    body += ("Can't make it? Reschedule or cancel:\n%s\n\nSee you soon!\nMacRandle Acres"
             % _cancel_url(bk))
    m.set_content(body)
    return _smtp_send(m)


def process_reminders(now=None):
    """Fire any due 24h/1h reminders exactly once each. Safe to call every few minutes."""
    now = now or datetime.datetime.now(BOOK_TZ)
    sent = 0
    with _leads_lock:
        v = load_bookings()
        changed = False
        for b in v:
            slot = b.get("slot")
            if not slot:
                continue
            try:
                st = datetime.datetime.fromisoformat(slot)
            except Exception:
                continue
            if st <= now:
                continue
            done = b.setdefault("reminders", [])
            for mins, label in REMIND_OFFSETS:
                if label in done:
                    continue
                if now >= st - datetime.timedelta(minutes=mins):
                    if _send_reminder(b, now):
                        sent += 1
                    done.append(label)  # fire once even if SMTP is momentarily down
                    changed = True
        if changed:
            try:
                with open(BOOKINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump(v, f, ensure_ascii=False)
            except Exception:
                pass
    return sent


def _reminder_loop():
    while True:
        try:
            process_reminders()
        except Exception:
            pass
        _time.sleep(300)


@app.route("/api/book", methods=["POST"])
def api_book():
    if not rate_ok("book", client_ip(), 8, 600):
        return jsonify(ok=False, error="Too many attempts, please try again shortly."), 429
    data = request.get_json(silent=True) or {}
    if str(data.get("website", "")).strip():
        return jsonify(ok=True)
    slot = _clean_line(data.get("slot", ""))
    name = _clean_line(data.get("name", ""))[:120]
    email = _clean_line(data.get("email", ""))[:160]
    phone = _clean_line(data.get("phone", ""))[:40]
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify(ok=False, error="Please add your name and a valid email."), 400
    if not any(slot == s["iso"] for day in gen_slots() for s in day["slots"]):
        return jsonify(ok=False, error="That time isn't available, please pick another."), 409
    bk = {"t": datetime.datetime.utcnow().isoformat(timespec="seconds"), "slot": slot,
          "name": name, "email": email, "phone": phone,
          "room": "MacRandleAcres-" + secrets.token_urlsafe(9)}
    with _leads_lock:
        v = load_bookings()
        if any(b.get("slot") == slot for b in v):
            return jsonify(ok=False, error="That time was just taken, please pick another."), 409
        v.append(bk)
        try:
            with open(BOOKINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False)
        except Exception:
            pass
    threading.Thread(target=send_booking_emails, args=(bk,), daemon=True).start()
    return jsonify(ok=True, when=_fmt_when(datetime.datetime.fromisoformat(slot)),
                   gcal=google_cal_link(bk), ics=make_ics(bk), cancel=_cancel_url(bk),
                   meet=meeting_url(bk))


@app.route("/book")
def book_page():
    # Inject GA/Pixel so the booking conversion (book_call / Schedule) actually fires.
    # Optional query params prefill the contact fields (used by the AI employee intake).
    head = "" if session.get("admin") else tracking_head()
    service = "ai-employee" if request.args.get("service") == "ai-employee" else ""
    pre = {
        "name": _clean_line(request.args.get("name", ""))[:120],
        "email": _clean_line(request.args.get("email", ""))[:160],
        "phone": _clean_line(request.args.get("phone", ""))[:40],
        "service": service,
        "type": "agent" if request.args.get("type") == "agent" else "",
    }
    pre_json = json.dumps(pre, ensure_ascii=True).replace("<", "\\u003c")
    html = BOOK_HTML.replace("</head>", head + "\n</head>", 1)
    html = html.replace(
        'var PREFILL={"name":"","email":"","phone":"","service":"","type":""};',
        "var PREFILL=%s;" % pre_json,
        1)
    if request.args.get("type") == "agent":
        html = html.replace(
            "Book a Growth Audit call - MacRandle Acres",
            "Book a Lucid Marketing call - MacRandle Acres")
        html = html.replace(
            "Book a free Growth Audit call with Jeff Randle. Lucid Marketing, by MacRandle Acres.",
            "Book a call to set up your first AI agent. $149 one-time. Lucid Marketing, by MacRandle Acres.")
        html = html.replace(
            "https://www.macrandleacres.com/book",
            "https://www.macrandleacres.com/book?type=agent")
        html = html.replace(
            "<h1>Book your Growth Audit call</h1>",
            "<h1>Book a Lucid Marketing call</h1>", 1)
        html = html.replace(
            '<p id="sub">30 minutes with Jeff Randle. Pick a time that works.</p>',
            '<p id="sub">Pick a time that works.</p>', 1)
    elif service == "ai-employee":
        html = html.replace(
            "<title>Book a Growth Audit call - MacRandle Acres</title>",
            "<title>Book your setup consultation - MacRandle Acres</title>", 1)
        html = html.replace(
            "<h1>Book your Growth Audit call</h1>",
            "<h1>Book your setup consultation</h1>", 1)
        html = html.replace(
            '<p id="sub">30 minutes with Jeff Randle. Pick a time that works.</p>',
            '<p id="sub">30 minutes with Jeff Randle to plan your AI virtual employee. Pick a time that works.</p>',
            1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/m/<room>")
def meeting_page(room):
    room = re.sub(r"[^A-Za-z0-9_-]", "", room)[:80]
    if not room:
        return redirect("/book")
    bk = next((b for b in load_bookings() if b.get("room") == room), None)
    when = ""
    if bk and bk.get("slot"):
        try:
            when = _fmt_when(datetime.datetime.fromisoformat(bk["slot"]))
        except Exception:
            when = ""
    html = (MEET_HTML.replace("__ROOM__", room)
            .replace("__WHEN__", _esc(when) or "Growth Audit call"))
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    # This page must be allowed to use the camera/mic (the global policy blocks them).
    resp.headers["Permissions-Policy"] = (
        'camera=(self "https://meet.jit.si"), microphone=(self "https://meet.jit.si"), '
        'display-capture=(self "https://meet.jit.si"), fullscreen=(self "https://meet.jit.si"), autoplay=*')
    return resp


@app.route("/book/cancel", methods=["GET", "POST"])
def book_cancel():
    slot = _clean_line(request.values.get("s", ""))
    token = _clean_line(request.values.get("t", ""))
    bk = next((b for b in load_bookings() if b.get("slot") == slot), None)
    valid = bk is not None and hmac.compare_digest(token, _cancel_token(slot, bk.get("email", "")))

    def page(msg, btn=""):
        html = CANCEL_HTML.replace("__MSG__", msg).replace("__BTN__", btn)
        return Response(html, mimetype="text/html")

    if not valid:
        return page("This cancellation link is invalid, or the booking has already been cancelled. "
                    "Need a time? <a href='/book'>Book a call</a>.")
    if request.method == "POST":
        with _leads_lock:
            remaining = [b for b in load_bookings() if b.get("slot") != slot]
            try:
                with open(BOOKINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump(remaining, f, ensure_ascii=False)
            except Exception:
                pass
        threading.Thread(target=send_cancel_notice, args=(bk,), daemon=True).start()
        return page("Your call has been cancelled and the time is freed up. Thanks for letting us know.",
                    "<a class='btn' href='/book'>Book a new time</a>")
    when = _fmt_when(datetime.datetime.fromisoformat(slot))
    btn = ("<form method='post'>"
           "<input type='hidden' name='s' value='%s'>"
           "<input type='hidden' name='t' value='%s'>"
           "<button class='btn danger' type='submit'>Yes, cancel my call</button></form>"
           "<a class='sub' href='/book'>No, keep it &amp; pick a different time instead</a>"
           % (_esc(slot), _esc(token)))
    return page("You're about to cancel your Growth Audit call on <b>%s</b>." % _esc(when), btn)


@app.route("/admin/bookings")
def admin_bookings():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/bookings")
    now_iso = datetime.datetime.now(BOOK_TZ).isoformat()
    rows = ""
    for b in sorted(load_bookings(), key=lambda x: x.get("slot", ""), reverse=True):
        s = datetime.datetime.fromisoformat(b["slot"]) if b.get("slot") else None
        when = (s.strftime("%a, %b ") + str(s.day) + s.strftime(", %I:%M %p").replace(" 0", " ")) if s else ""
        tag = "upcoming" if b.get("slot", "") >= now_iso else "past"
        try:
            gl = google_cal_link(b) if b.get("slot") else "#"
        except Exception:
            gl = "#"
        join = meeting_url(b)
        join_html = ("<a class='join' href='%s' target='_blank' rel='noopener'>&#127909; Join</a>"
                     % _esc(join)) if join else ""
        rows += ("<tr class='%s'><td>%s <span class='tg'>%s</span></td><td>%s</td>"
                 "<td><a href='mailto:%s'>%s</a></td><td>%s</td>"
                 "<td class='act'>%s<a class='addcal' href='%s' target='_blank' rel='noopener'>Add &#8599;</a>"
                 "<form method='post' action='/admin/bookings/delete' class='delf' "
                 "onsubmit=\"return confirm('Remove this booking? This frees the time slot.')\">"
                 "<input type='hidden' name='slot' value='%s'>"
                 "<button class='del' title='Delete booking'>&#10005;</button></form></td></tr>") % (
                 tag, _esc(when), tag, _esc(b.get("name", "")), _esc(b.get("email", "")),
                 _esc(b.get("email", "")), _esc(b.get("phone", "")), join_html, _esc(gl), _esc(b.get("slot", "")))
    if not rows:
        rows = "<tr><td colspan=5 style='opacity:.5'>No calls booked yet.</td></tr>"
    return Response(BOOKINGS_HTML.replace("__ROWS__", rows), mimetype="text/html")


@app.route("/admin/smtp-test")
def admin_smtp_test():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/smtp-test")
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_PASS", "")
    pw_norm = "".join(pw.split())
    port = os.getenv("SMTP_PORT", "587").strip()
    frm = os.getenv("SMTP_FROM", "").strip() or user
    ssl_env = os.getenv("SMTP_SSL", "").strip()
    lines = ["MacRandle Acres - SMTP diagnostic",
             "build: DIAG-v4 (space-strip + dual-port)",
             "=" * 40,
             "SMTP_HOST   = %r" % host,
             "SMTP_USER   = %r" % user,
             "SMTP_PORT   = %r" % port,
             "SMTP_FROM   = %r" % frm,
             "SMTP_SSL    = %r" % ssl_env,
             "LEAD_EMAIL  = %r  (alert goes here)" % ORG_EMAIL,
             "SMTP_PASS   raw_len=%d  login_len=%d  (login strips spaces)"
             % (len(pw), len(pw_norm))]
    if not (host and user and pw_norm):
        lines.append("\nRESULT: One of HOST/USER/PASS is missing -> emails are OFF.")
        return Response("\n".join(lines), mimetype="text/plain")

    if request.args.get("send") != "1":
        lines.append("\nConfig looks complete. No test email sent (safe view).")
        lines.append("To actually send a live test email to %s, load:" % ORG_EMAIL)
        lines.append("    /admin/smtp-test?send=1")
        return Response("\n".join(lines), mimetype="text/plain")

    def _attempt(mode):
        import smtplib
        from email.message import EmailMessage
        m = EmailMessage()
        m["From"] = frm
        m["To"] = ORG_EMAIL
        m["Subject"] = "MacRandle SMTP test - it works (%s)" % mode
        m.set_content("If you can read this, your booking emails are working via %s." % mode)
        if mode == "ssl465":
            s = smtplib.SMTP_SSL(host, 465, timeout=20)
        else:
            s = smtplib.SMTP(host, 587, timeout=20)
            s.starttls()
        s.login(user, pw_norm)
        s.send_message(m)
        s.quit()

    ok = False
    for mode in ("starttls587", "ssl465"):
        try:
            _attempt(mode)
            lines.append("\nRESULT[%s]: OK - test email sent to %s. Check inbox + spam."
                         % (mode, ORG_EMAIL))
            ok = True
            break
        except Exception as e:
            lines.append("RESULT[%s]: FAILED -> %s: %s" % (mode, type(e).__name__, e))
    if not ok:
        lines.append("\nBoth ports failed. If this is 'BadCredentials', the app password itself "
                     "is wrong/revoked -> generate a fresh one at myaccount.google.com/apppasswords.")
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/admin/reminders/run")
def admin_reminders_run():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/bookings")
    n = process_reminders()
    smtp_on = bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_PASS", "").strip())
    msg = ("Reminder check ran. Sent %d reminder email(s) this pass.\n"
           "(Reminders also run automatically every ~5 minutes while the site is awake.)\n"
           "SMTP configured: %s") % (n, "yes" if smtp_on else "NO - reminders won't send until SMTP is set")
    return Response(msg, mimetype="text/plain")


@app.route("/admin/bookings/delete", methods=["POST"])
def admin_bookings_delete():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/bookings")
    slot = str(request.form.get("slot", "")).strip()
    if slot:
        with _leads_lock:
            v = [b for b in load_bookings() if b.get("slot") != slot]
            try:
                with open(BOOKINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump(v, f, ensure_ascii=False)
            except Exception:
                pass
    return redirect("/admin/bookings")


BOOK_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Book a Growth Audit call - MacRandle Acres</title>
<meta name="description" content="Book a free Growth Audit call with Jeff Randle. Lucid Marketing, by MacRandle Acres.">
<link rel="canonical" href="https://www.macrandleacres.com/book">
<meta property="og:title" content="Book a Growth Audit call - MacRandle Acres">
<meta property="og:description" content="Book a free Growth Audit call with Jeff Randle. Lucid Marketing, by MacRandle Acres.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.macrandleacres.com/book">
<meta property="og:image" content="https://www.macrandleacres.com/og-v5.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Book a Growth Audit call - MacRandle Acres">
<meta name="twitter:description" content="Book a free Growth Audit call with Jeff Randle. Lucid Marketing, by MacRandle Acres.">
<meta name="twitter:image" content="https://www.macrandleacres.com/og-v5.jpg">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.6;padding:30px 16px;
  background-image:radial-gradient(900px 500px at 50% -10%,rgba(199,154,59,.12),transparent 60%)}
.card{max-width:640px;margin:0 auto;background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:22px;overflow:hidden;box-shadow:0 22px 55px rgba(35,49,40,.14)}
.head{background:linear-gradient(160deg,#26543f,#1a3b2d);color:#f6f4ec;padding:30px}
.head .mark{width:40px;height:40px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);display:grid;place-items:center;font-weight:800;color:#234F3D;margin-bottom:14px}
.head .brand{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:#e0b862;font-weight:700}
.head h1{font-size:25px;font-weight:800;margin:4px 0 4px}
.head p{opacity:.85;font-size:14px}
.body{padding:24px 26px 28px}
.lbl{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#5c635e;margin:6px 0 10px}
.days{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}
.day{flex:0 0 auto;padding:10px 14px;border:1px solid rgba(35,79,61,.18);border-radius:11px;background:#fff;cursor:pointer;font-size:13.5px;font-weight:600;color:#234F3D;white-space:nowrap;transition:.15s}
.day:hover{border-color:#c79a3b}.day.on{background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;border-color:transparent}
.slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;margin-top:18px}
.slot{padding:11px 8px;border:1px solid rgba(35,79,61,.18);border-radius:10px;background:#fff;cursor:pointer;font-size:14px;font-weight:600;color:#234F3D;text-align:center;transition:.15s}
.slot:hover{border-color:#c79a3b;background:rgba(199,154,59,.06)}.slot.on{background:linear-gradient(135deg,#e0b862,#a97f2a);color:#2a2005;border-color:transparent}
.form{margin-top:22px;display:none}.form.show{display:block}
.fg{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.fld{display:flex;flex-direction:column;gap:5px;font-size:12.5px;font-weight:600;color:#234F3D}.fld.full{grid-column:1/-1}
.fld input{font-family:inherit;font-size:14.5px;padding:11px 13px;border:1px solid rgba(35,79,61,.2);border-radius:10px;color:#2D2D2D;font-weight:400;outline:none}
.fld input:focus{border-color:#c79a3b}
.pick{margin-top:14px;font-size:14px;color:#234F3D;font-weight:600}
.confirm{margin-top:16px;width:100%;padding:14px;border:none;border-radius:12px;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:700;font-size:15px;cursor:pointer}
.msg{font-size:13.5px;color:#b23;margin-top:8px}
.hp{position:absolute;left:-9999px}
.done{text-align:center;padding:26px 10px}.done .ic{font-size:44px;margin-bottom:12px}.done h2{font-size:25px;color:#234F3D;margin-bottom:8px}.done p{color:#5c635e;font-size:16px;max-width:420px;margin:0 auto}
.cal-btns{display:flex;flex-direction:column;gap:10px;max-width:300px;margin:20px auto 0}
.cbtn{display:block;padding:13px 16px;border-radius:12px;font-weight:700;font-size:14.5px;text-decoration:none;transition:transform .12s,box-shadow .12s}
.cbtn:hover{transform:translateY(-1px)}
.cbtn.g{background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;box-shadow:0 6px 18px rgba(26,59,45,.25)}
.cbtn.i{background:#fff;color:#234F3D;border:1.5px solid #d7ddd6}
.cbtn.join{background:linear-gradient(135deg,#e0b862,#a97f2a);color:#2a2005;box-shadow:0 6px 18px rgba(169,127,42,.3);font-size:15.5px;padding:15px 16px}
.done .cn{margin-top:16px;font-size:14px;color:#8a918b}
.empty{padding:26px 4px;color:#5c635e;font-size:15px}
@media(max-width:560px){.fg{grid-template-columns:1fr}}
</style></head><body>
<div class="card">
  <div class="head"><div class="mark">M</div><div class="brand">MacRandle Acres</div><h1>Book your Growth Audit call</h1><p id="sub">30 minutes with Jeff Randle. Pick a time that works.</p></div>
  <div class="body" id="body"><div class="empty">Loading available times…</div></div>
</div>
<script>
var PREFILL={"name":"","email":"","phone":"","service":"","type":""};
var entered={name:PREFILL.name||'',email:PREFILL.email||'',phone:PREFILL.phone||''};
var TZ='', DAYS=[], di=0, slot=null, body=document.getElementById('body');
fetch('/api/slots').then(function(r){return r.json();}).then(function(j){
  TZ=j.tz||''; DAYS=j.days||[];
  var intro = PREFILL.type==='agent'
    ? 'Pick a time that works. All times '
    : (PREFILL.service==='ai-employee'
    ? '30 minutes with Jeff Randle to plan your AI virtual employee. All times '
    : '30 minutes with Jeff Randle. All times ');
  document.getElementById('sub').textContent=intro+TZ+'.';
  render();
}).catch(function(){body.innerHTML='<div class="empty">Could not load times. Please email Macrandleacres@gmail.com.</div>';});
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function captureContact(){
  var n=document.getElementById('bn'), e=document.getElementById('be'), p=document.getElementById('bp');
  if(n) entered.name=n.value; if(e) entered.email=e.value; if(p) entered.phone=p.value;
}
function render(){
  captureContact();
  if(!DAYS.length){body.innerHTML='<div class="empty">No open times in the next couple of weeks. Please email <a href="mailto:Macrandleacres@gmail.com">Macrandleacres@gmail.com</a> and we\\'ll find a time.</div>';return;}
  var h='<div class="lbl">Choose a day</div><div class="days">';
  DAYS.forEach(function(d,i){h+='<div class="day'+(i===di?' on':'')+'" data-d="'+i+'">'+esc(d.label)+'</div>';});
  h+='</div><div class="lbl" style="margin-top:20px">Choose a time ('+esc(TZ)+')</div><div class="slots">';
  DAYS[di].slots.forEach(function(s){h+='<div class="slot'+(slot===s.iso?' on':'')+'" data-s="'+s.iso+'">'+esc(s.label)+'</div>';});
  h+='</div>'+formHtml();
  body.innerHTML=h;
  body.querySelectorAll('.day').forEach(function(el){el.onclick=function(){di=+el.getAttribute('data-d');slot=null;render();};});
  body.querySelectorAll('.slot').forEach(function(el){el.onclick=function(){slot=el.getAttribute('data-s');render();var f=document.querySelector('.form');if(f)f.classList.add('show');window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});};});
  wireForm();
}
function slotLabel(){for(var i=0;i<DAYS.length;i++){for(var k=0;k<DAYS[i].slots.length;k++){if(DAYS[i].slots[k].iso===slot)return DAYS[i].label+' at '+DAYS[i].slots[k].label;}}return '';}
function formHtml(){
  if(!slot)return '';
  return '<div class="form show"><div class="pick">📅 '+esc(slotLabel())+' '+esc(TZ)+'</div>'+
    '<div class="fg" style="margin-top:12px">'+
    '<label class="fld"><span>Your name *</span><input id="bn" autocomplete="name" value="'+esc(entered.name)+'"></label>'+
    '<label class="fld"><span>Email *</span><input id="be" type="email" autocomplete="email" value="'+esc(entered.email)+'"></label>'+
    '<label class="fld full"><span>Phone (optional)</span><input id="bp" autocomplete="tel" value="'+esc(entered.phone)+'"></label></div>'+
    '<input type="text" id="bw" class="hp" tabindex="-1" autocomplete="off">'+
    '<button class="confirm" id="bc">Confirm booking</button><div id="bm" class="msg"></div></div>';
}
function wireForm(){
  var b=document.getElementById('bc'); if(!b)return;
  b.onclick=function(){
    var name=(document.getElementById('bn').value||'').trim(), email=(document.getElementById('be').value||'').trim(),
        phone=(document.getElementById('bp').value||'').trim(), hp=(document.getElementById('bw').value||'').trim(), m=document.getElementById('bm');
    if(!name||email.indexOf('@')<0){m.textContent='Please add your name and a valid email.';return;}
    m.textContent='';b.disabled=true;b.textContent='Booking…';
    fetch('/api/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot:slot,name:name,email:email,phone:phone,website:hp})})
      .then(function(r){return r.json();}).then(function(j){
        if(j.ok){ if(window.fbq)fbq('track','Schedule'); if(window.gtag)gtag('event','book_call');
          var icsHref='data:text/calendar;charset=utf-8,'+encodeURIComponent(j.ics||'');
          var joinBtn = j.meet ? '<a class="cbtn join" href="'+esc(j.meet)+'" target="_blank" rel="noopener">🎥 Join the video call</a>' : '';
          body.innerHTML='<div class="done"><div class="ic">🌱</div><h2>You\\'re booked!</h2><p>'+esc(j.when)+'. Your call happens right here on the site \\u2014 no downloads. Save the link:</p>'+
            '<div class="cal-btns">'+
            joinBtn+
            '<a class="cbtn g" href="'+esc(j.gcal||'#')+'" target="_blank" rel="noopener">📅 Add to Google Calendar</a>'+
            '<a class="cbtn i" href="'+icsHref+'" download="growth-audit.ics">⬇ Apple / Outlook (.ics)</a>'+
            '</div><p class="cn">The join link is also in your email and calendar invite. Need to change it? <a href="'+esc(j.cancel||'#')+'">Reschedule or cancel</a>.</p></div>';
          window.scrollTo({top:0,behavior:'smooth'});
        } else { b.disabled=false;b.textContent='Confirm booking';
          if(j.error&&j.error.indexOf('available')>-1||j.error&&j.error.indexOf('taken')>-1){m.textContent=j.error+' Refreshing times…';setTimeout(function(){location.reload();},1500);}
          else m.textContent=j.error||'Something went wrong, please try again.'; }
      }).catch(function(){b.disabled=false;b.textContent='Confirm booking';m.textContent='Network error, please try again.';});
  };
}
</script></body></html>"""


BOOKINGS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Booked calls</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:720px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
table{width:100%;border-collapse:collapse;font-size:14px;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 8px 22px rgba(35,49,40,.06)}
td,th{text-align:left;padding:12px 14px;border-bottom:1px solid rgba(35,79,61,.09)}th{color:#5c635e;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
a{color:#a97f2a;font-weight:600;text-decoration:none}
a.addcal{display:inline-block;padding:5px 11px;border:1px solid rgba(35,79,61,.2);border-radius:100px;font-size:12.5px;color:#234F3D}
a.addcal:hover{background:rgba(35,79,61,.06);border-color:#c79a3b}
a.join{display:inline-block;padding:5px 12px;border-radius:100px;font-size:12.5px;font-weight:700;color:#2a2005;background:linear-gradient(135deg,#e0b862,#a97f2a);margin-right:8px}
a.join:hover{filter:brightness(1.05)}
td.act{white-space:nowrap}.delf{display:inline;margin-left:8px}
.del{border:none;background:none;color:#c0392b;font-size:14px;cursor:pointer;padding:4px 6px;border-radius:8px;opacity:.55}
.del:hover{opacity:1;background:rgba(192,57,43,.08)}
tr.past{opacity:.5}.tg{font-size:10px;font-weight:700;text-transform:uppercase;padding:2px 7px;border-radius:100px;margin-left:6px}
tr.upcoming .tg{background:rgba(35,79,61,.1);color:#234F3D}tr.past .tg{background:rgba(0,0,0,.06);color:#888}
</style></head><body><div class="wrap">
<div class="top"><h1>Booked calls</h1><a class="back" href="/">&larr; Site</a></div>
<table><thead><tr><th>When</th><th>Name</th><th>Email</th><th>Phone</th><th>Calendar</th></tr></thead><tbody>__ROWS__</tbody></table>
</div></body></html>"""


CANCEL_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Manage your call - MacRandle Acres</title>
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.6;padding:40px 16px;
  background-image:radial-gradient(900px 500px at 50% -10%,rgba(199,154,59,.12),transparent 60%)}
.card{max-width:480px;margin:0 auto;background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:22px;
  box-shadow:0 22px 55px rgba(35,49,40,.14);padding:34px 30px;text-align:center}
.mark{width:44px;height:44px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);
  display:grid;place-items:center;font-weight:800;color:#234F3D;margin:0 auto 16px}
h1{font-size:22px;color:#234F3D;margin-bottom:10px}p.m{color:#5c635e;font-size:16px;margin-bottom:22px}
.btn{display:inline-block;padding:13px 22px;border-radius:12px;font-weight:700;font-size:15px;cursor:pointer;
  text-decoration:none;border:none;color:#f6f4ec;background:linear-gradient(135deg,#2a5c47,#1a3b2d)}
.btn.danger{background:linear-gradient(135deg,#c0392b,#8e2a20)}
.sub{display:block;margin-top:16px;font-size:13.5px;color:#8a918b}
a{color:#a97f2a;font-weight:600}
</style></head><body>
<div class="card"><div class="mark">M</div><h1>MacRandle Acres</h1>
<p class="m">__MSG__</p>__BTN__</div>
</body></html>"""


MEET_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Growth Audit call - MacRandle Acres</title>
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%}
body{font-family:'Inter',system-ui,sans-serif;background:#12261d;color:#f6f4ec;display:flex;flex-direction:column}
#bar{flex:0 0 auto;display:flex;align-items:center;gap:12px;padding:12px 18px;background:linear-gradient(160deg,#26543f,#1a3b2d);border-bottom:1px solid rgba(199,154,59,.25)}
#bar .mk{width:34px;height:34px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);display:grid;place-items:center;font-weight:800;color:#234F3D}
#bar .t{font-weight:800;font-size:15px;line-height:1.15}#bar .s{font-size:12px;color:#e0b862;font-weight:600}
#bar .sp{margin-left:auto}#bar a{color:#cfe0d6;font-size:12.5px;text-decoration:none;font-weight:600}#bar a:hover{color:#fff}
#meet{flex:1 1 auto;min-height:0}
#fallback{position:absolute;inset:auto 0 0 0;top:58px;display:grid;place-items:center;padding:30px;text-align:center}
#fallback a{color:#e0b862;font-weight:700}
</style></head><body>
<div id="bar"><div class="mk">M</div><div><div class="t">MacRandle Acres</div><div class="s">Growth Audit call &middot; __WHEN__</div></div>
<div class="sp"></div><a href="https://meet.jit.si/__ROOM__" target="_blank" rel="noopener">Open in new tab &#8599;</a></div>
<div id="meet"><div id="fallback"><p>Loading your secure video room&hellip;<br><br>If it doesn't appear, <a href="https://meet.jit.si/__ROOM__" target="_blank" rel="noopener">click here to join in a new tab</a>.</p></div></div>
<script src="https://meet.jit.si/external_api.js"></script>
<script>
(function(){
  try{
    if(typeof JitsiMeetExternalAPI!=='function'){return;}
    var mount=document.getElementById('meet'); mount.innerHTML='';
    var api=new JitsiMeetExternalAPI('meet.jit.si',{
      roomName:'__ROOM__',
      parentNode:mount,
      width:'100%',height:'100%',
      configOverwrite:{prejoinPageEnabled:true,disableDeepLinking:true,subject:'Growth Audit call'},
      interfaceConfigOverwrite:{MOBILE_APP_PROMO:false,SHOW_JITSI_WATERMARK:false,SHOW_CHROME_EXTENSION_BANNER:false,DEFAULT_BACKGROUND:'#12261d'}
    });
    api.addEventListener('readyToClose',function(){window.location.href='/';});
  }catch(e){/* fallback link stays visible */}
})();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Facebook profile audit tool (admin) — per-client checklist with live scoring
# ---------------------------------------------------------------------------
AUDITS_PATH = os.path.join(DATA, "audits.json")
SETTINGS_PATH = os.path.join(DATA, "settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def get_ai_key():
    """API key from the Render env var (preferred) or, failing that, the in-app admin setting."""
    return (os.getenv("ANTHROPIC_API_KEY", "").strip()
            or str(load_settings().get("anthropic_api_key", "")).strip())

AUDIT_SECTIONS = [
    {"name": "Cover Photo", "icon": "\U0001F5BC️", "items": [
        {"id": "cover_size", "t": "Sized right — nothing important cut off",
         "h": "Check the desktop AND mobile crop. No heads, faces, or key words chopped off."},
        {"id": "cover_quality", "t": "Professional quality, not blurry or stretched",
         "h": "Sharp, well-composed, and correctly proportioned — not pixelated or squished."},
        {"id": "cover_cta", "t": "Has a clear value prop or call-to-action",
         "h": "Tells visitors who they help + what to do next (call, DM, visit site)."},
    ]},
    {"name": "Profile Photo", "icon": "\U0001F464", "items": [
        {"id": "pfp_headshot", "t": "Clear, current professional headshot",
         "h": "A real photo of them — not a logo, group shot, or a property."},
        {"id": "pfp_framing", "t": "Well-lit, centered, face fills the frame",
         "h": "Recognizable even as a tiny thumbnail; good lighting; face not tiny."},
    ]},
    {"name": "Bio / Intro", "icon": "\U0001F4DD", "items": [
        {"id": "bio_full", "t": "Bio makes the most of the space",
         "h": "The bio is prime real estate — fit the most important message in, don't leave it half-empty."},
        {"id": "bio_keywords", "t": "Has 'Realtor'/'Real Estate' + area keywords",
         "h": "Their city/area plus the words people actually search, so they're findable."},
        {"id": "bio_positioning", "t": "Says who they help + the outcome",
         "h": "Not just a title — the value (e.g. 'Helping Salisbury families find home')."},
    ]},
    {"name": "Links & Contact", "icon": "\U0001F517", "items": [
        {"id": "link_bio", "t": "Clickable link in the bio/link field",
         "h": "A working link to their site, landing page, or link-in-bio."},
        {"id": "link_dest", "t": "Link points to the right place",
         "h": "Goes to a lead magnet, booking page, or listings — not a dead/generic page."},
        {"id": "link_vanity", "t": "Clean vanity URL claimed",
         "h": "facebook.com/TheirName — no random numbers."},
    ]},
    {"name": "Pinned Post", "icon": "\U0001F4CC", "items": [
        {"id": "feat_pinned", "t": "Has a pinned intro post at the top",
         "h": "Introduces them + how they help, with a clear call-to-action."},
        {"id": "feat_current", "t": "Pinned post is current",
         "h": "Not an outdated listing or an old promo."},
    ]},
    {"name": "Content Strategy", "icon": "\U0001F4C8", "items": [
        {"id": "content_nolinks", "t": "Links kept OUT of post text",
         "h": "Put the link in the first comment instead — outbound links in a post suppress reach."},
        {"id": "content_recent", "t": "Posted within the last 7 days",
         "h": "The profile looks active and alive, not abandoned."},
        {"id": "content_mix", "t": "Value-driven mix, not all 'Just Listed/Sold'",
         "h": "Tips, local info, story, personality — not only listings."},
        {"id": "content_local", "t": "Local / community content present",
         "h": "Neighborhood spotlights, local events, market updates for their area."},
        {"id": "content_video", "t": "Uses native video / Reels",
         "h": "Facebook favors native video; realtors who use it get more reach."},
    ]},
    {"name": "Engagement & Proof", "icon": "\U0001F4AC", "items": [
        {"id": "eng_replies", "t": "Replies to comments & messages",
         "h": "Timely responses; conversations aren't left hanging."},
        {"id": "eng_reviews", "t": "Recommendations / testimonials visible",
         "h": "Social proof featured, or a Page with Recommendations turned on."},
    ]},
]
AUDIT_ITEM_IDS = [it["id"] for sec in AUDIT_SECTIONS for it in sec["items"]]


def load_audits():
    try:
        with open(AUDITS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def save_audits(v):
    try:
        tmp = AUDITS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, AUDITS_PATH)  # atomic on the same filesystem
        return True
    except Exception:
        try:
            with open(AUDITS_PATH, "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False)
            return True
        except Exception:
            return False


def _audit_ids(a):
    hidden = set(a.get("hidden", []) if isinstance(a.get("hidden"), list) else [])
    return [i for i in AUDIT_ITEM_IDS if i not in hidden] + \
           [c["id"] for c in a.get("custom", []) if isinstance(c, dict) and c.get("id")]


def _audit_progress(a):
    it = a.get("items", {})
    ids = _audit_ids(a)
    total = len(ids)
    reviewed = sum(1 for k in ids if it.get(k, {}).get("status"))
    fixes = sum(1 for k in ids if it.get(k, {}).get("status") == "fix")
    passes = sum(1 for k in ids if it.get(k, {}).get("status") == "pass")
    return {"total": total, "reviewed": reviewed, "fixes": fixes, "passes": passes,
            "pct": round(100.0 * reviewed / total) if total else 0}


@app.route("/admin/audits/debug")
def admin_audits_debug():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/audits")
    lines = ["AUDITS PERSISTENCE DEBUG", "=" * 30,
             "PERSISTENT STORAGE = %s" % ("YES - survives updates" if DATA_IS_PERSISTENT
                                          else "NO - TEMPORARY, wiped on every update (a disk must be mounted)"),
             "DATA_DIR env = %r" % os.getenv("DATA_DIR", ""),
             "DATA dir     = %r" % DATA,
             "/data mounted = %s" % os.path.isdir("/data"),
             "AUDITS_PATH  = %r" % AUDITS_PATH,
             "file exists  = %s" % os.path.exists(AUDITS_PATH)]
    try:
        d = os.path.dirname(AUDITS_PATH) or "."
        lines.append("dir writable = %s" % os.access(d, os.W_OK))
    except Exception as e:
        lines.append("dir writable = err %s" % e)
    try:
        if os.path.exists(AUDITS_PATH):
            mt = datetime.datetime.utcfromtimestamp(os.path.getmtime(AUDITS_PATH)).isoformat()
            lines.append("file mtime   = %sZ (UTC)" % mt)
            lines.append("file size    = %d bytes" % os.path.getsize(AUDITS_PATH))
    except Exception as e:
        lines.append("mtime        = err %s" % e)
    v = load_audits()
    lines.append("")
    lines.append("STORED AUDITS = %d" % len(v))
    for a in v:
        try:
            p = _audit_progress(a)
            noted = sum(1 for it in a.get("items", {}).values() if isinstance(it, dict) and it.get("note"))
            lines.append("  - id=%s | client=%r | created=%s | updated=%s | reviewed=%d/%d | notes=%d"
                         % (a.get("id"), a.get("client"), (a.get("created", "") or "")[:19],
                            (a.get("updated", "") or "")[:19], p["reviewed"], p["total"], noted))
        except Exception as e:
            lines.append("  - id=%s | (error reading: %s)" % (a.get("id"), e))
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/admin/settings")
def admin_settings():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/settings")
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    stored = str(load_settings().get("anthropic_api_key", "")).strip()
    if env_key:
        status = ("<div class='ok'>&#10003; Using an API key from Render (env var). "
                  "That takes priority; a key saved here is only used if the env var is removed.</div>")
    elif stored:
        tail = _esc(stored[-4:]) if len(stored) >= 4 else "&bull;&bull;&bull;&bull;"
        status = ("<div class='ok'>&#10003; A key is saved (ends in <b>%s</b>). AI features are ready.</div>"
                  "<form method='post' action='/admin/settings/ai-key' style='margin-top:10px'>"
                  "<input type='hidden' name='clear' value='1'>"
                  "<button class='rm' type='submit' onclick=\"return confirm('Remove the saved API key?')\">Remove saved key</button></form>") % tail
    else:
        status = "<div class='no'>No API key set yet. Paste one below to turn on the &ldquo;Optimize notes with AI&rdquo; button.</div>"
    saved = "<div class='flash'>Saved &#10003;</div>" if request.args.get("saved") else ""
    html = SETTINGS_HTML.replace("__STATUS__", status).replace("__FLASH__", saved)
    return Response(html, mimetype="text/html")


@app.route("/admin/settings/ai-key", methods=["POST"])
def admin_settings_ai_key():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/settings")
    s = load_settings()
    if request.form.get("clear"):
        s.pop("anthropic_api_key", None)
        save_settings(s)
        return redirect("/admin/settings?saved=1")
    key = _clean_line(request.form.get("key", ""))[:200]
    if key:
        s["anthropic_api_key"] = key
        save_settings(s)
    return redirect("/admin/settings?saved=1")


SETTINGS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Admin Settings - MacRandle Acres</title>
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:30px 16px}
.wrap{max-width:560px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.card{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:16px;padding:24px;box-shadow:0 8px 22px rgba(35,49,40,.06);margin-top:16px}
.card h2{font-size:16px;color:#234F3D;margin-bottom:6px}
.card p.d{font-size:13.5px;color:#5c635e;margin-bottom:14px;line-height:1.55}
.ok{background:#e8f3ec;color:#1c6b40;border-radius:10px;padding:11px 13px;font-size:14px;font-weight:600}
.no{background:#fbf0e7;color:#a4632a;border-radius:10px;padding:11px 13px;font-size:14px;font-weight:600}
.flash{background:#234F3D;color:#f6f4ec;border-radius:10px;padding:9px 13px;font-size:14px;font-weight:700;margin-bottom:14px;display:inline-block}
form.set{margin-top:16px;display:flex;gap:9px;flex-wrap:wrap}
form.set input{flex:1;min-width:220px;padding:12px 13px;border:1px solid rgba(35,79,61,.2);border-radius:10px;font-size:14px;font-family:inherit}
form.set input:focus{outline:2px solid rgba(199,154,59,.4)}
form.set button{border:none;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:700;font-size:14px;padding:12px 20px;border-radius:10px;cursor:pointer}
.rm{border:none;background:none;color:#c0392b;font-weight:700;font-size:13px;cursor:pointer;padding:6px 0;text-decoration:underline}
.hint{font-size:12.5px;color:#8a918b;margin-top:12px;line-height:1.55}.hint a{color:#a97f2a;font-weight:700}
</style></head><body><div class="wrap">
<div class="top"><h1>Admin Settings</h1><a class="back" href="/admin/audits">&larr; FB Audits</a></div>
__FLASH__
<div class="card">
  <h2>&#10024; AI note polishing (Anthropic API key)</h2>
  <p class="d">Powers the &ldquo;Optimize all notes with AI&rdquo; button in the audit tool. Paste your Anthropic API key once and it's stored on your server &mdash; no need to touch Render.</p>
  __STATUS__
  <form class="set" method="post" action="/admin/settings/ai-key" autocomplete="off">
    <input type="password" name="key" placeholder="sk-ant-..." autocomplete="off" spellcheck="false">
    <button type="submit">Save key</button>
  </form>
  <div class="hint">Get a key at <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a> &rarr; API Keys. It's stored on your own server and never shown again after saving. This is a paid Anthropic API (note polishing is very cheap).</div>
</div>
</div></body></html>"""


@app.route("/admin/audits")
def admin_audits():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/audits")
    rows = ""
    for a in sorted(load_audits(), key=lambda x: x.get("updated", ""), reverse=True):
        p = _audit_progress(a)
        created = (a.get("created", "") or "")[:10]
        flag = (" <span class='pill fix'>%d to fix</span>" % p["fixes"]) if p["fixes"] else ""
        rows += ("<tr><td><a href='/admin/audits/%s'>%s</a></td><td>%s</td>"
                 "<td><div class='mini'><span style='width:%d%%'></span></div>"
                 "<span class='pc'>%d%%</span>%s</td>"
                 "<td class='act'><a class='open' href='/admin/audits/%s'>Open</a>"
                 "<form method='post' action='/admin/audits/%s/delete' class='delf' "
                 "onsubmit=\"return confirm('Delete this audit?')\">"
                 "<button class='del' title='Delete'>&#10005;</button></form></td></tr>") % (
                 _esc(a.get("id", "")), _esc(a.get("client", "") or "Untitled"), _esc(created),
                 p["pct"], p["pct"], flag, _esc(a.get("id", "")), _esc(a.get("id", "")))
    if not rows:
        rows = "<tr><td colspan=4 style='opacity:.5'>No audits yet. Create your first one above.</td></tr>"
    return Response(AUDITS_LIST_HTML.replace("__ROWS__", rows), mimetype="text/html")


@app.route("/admin/audits/new", methods=["POST"])
def admin_audit_new():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/audits")
    client = _clean_line(request.form.get("client", ""))[:120] or "Untitled client"
    aid = secrets.token_urlsafe(6)
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with _leads_lock:
        v = load_audits()
        v.append({"id": aid, "client": client, "created": now, "updated": now, "items": {}})
        save_audits(v)
    return redirect("/admin/audits/" + aid)


@app.route("/admin/audits/<aid>")
def admin_audit_view(aid):
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/audits")
    a = next((x for x in load_audits() if x.get("id") == aid), None)
    if not a:
        return redirect("/admin/audits")
    sections = json.dumps(AUDIT_SECTIONS, ensure_ascii=False).replace("</", "<\\/")
    payload = json.dumps({"id": a["id"], "client": a.get("client", ""),
                          "items": a.get("items", {}), "custom": a.get("custom", []),
                          "hidden": a.get("hidden", [])},
                         ensure_ascii=False).replace("</", "<\\/")
    share_url = "%s/audit/%s" % (SITE, aid)
    banner = "" if DATA_IS_PERSISTENT else (
        "<div class='pbanner'>&#9888; Heads up: this server is on TEMPORARY storage, so audits can be lost "
        "on the next update. Contact your developer to mount a persistent disk. "
        "<a href='/admin/audits/debug' target='_blank'>details</a></div>")
    html = (AUDIT_HTML.replace("__SECTIONS__", sections)
            .replace("__AUDIT__", payload).replace("__AID__", _esc(aid))
            .replace("__SHAREURL__", _esc(share_url)).replace("__PERSISTBANNER__", banner))
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/admin/audits/<aid>", methods=["POST"])
def admin_audit_save(aid):
    if not session.get("admin"):
        return jsonify(ok=False), 403
    data = request.get_json(silent=True) or {}
    # Custom (per-audit) items the user added, whitelisted by id pattern + non-empty title.
    clean_custom, seen = [], set()
    raw_custom = data.get("custom", [])
    if isinstance(raw_custom, list):
        for cc in raw_custom[:80]:
            if not isinstance(cc, dict):
                continue
            cid = str(cc.get("id", ""))
            title = _clean_line(cc.get("t", ""))[:160]
            if re.match(r"^c_[A-Za-z0-9]{1,24}$", cid) and title and cid not in seen:
                seen.add(cid)
                clean_custom.append({"id": cid, "t": title})
    valid_ids = set(AUDIT_ITEM_IDS) | seen
    raw_hidden = data.get("hidden", [])
    clean_hidden = []
    if isinstance(raw_hidden, list):
        for hid in raw_hidden:
            if hid in AUDIT_ITEM_IDS and hid not in clean_hidden:
                clean_hidden.append(hid)
    incoming = data.get("items", {})
    clean_items = {}
    if isinstance(incoming, dict):
        for k, val in incoming.items():
            if k in valid_ids and isinstance(val, dict):
                st = val.get("status", "")
                st = st if st in ("pass", "fix", "na") else ""
                note = _clean_note(val.get("note", ""))[:400]
                if st or note:
                    clean_items[k] = {"status": st, "note": note}
    client = _clean_line(data.get("client", ""))[:120]
    with _leads_lock:
        v = load_audits()
        a = next((x for x in v if x.get("id") == aid), None)
        if not a:
            return jsonify(ok=False), 404
        a["items"] = clean_items
        a["custom"] = clean_custom
        a["hidden"] = clean_hidden
        if client:
            a["client"] = client
        stamp = datetime.datetime.utcnow().isoformat(timespec="microseconds")
        a["updated"] = stamp
        save_audits(v)
        prog = _audit_progress(a)
        # Read back from disk and confirm this exact save landed — never report
        # success unless the server can actually re-read it.
        try:
            back = next((x for x in load_audits() if x.get("id") == aid), None)
            persisted = bool(back and back.get("updated") == stamp)
        except Exception:
            persisted = False
    if not persisted:
        return jsonify(ok=False, error="The server could not confirm the save. Please try again."), 500
    return jsonify(ok=True, progress=prog, persistent=DATA_IS_PERSISTENT)


AUDIT_AI_SYSTEM = (
    "You refine a real estate growth advisor's rough Facebook-profile-audit notes into clear, "
    "professional, client-ready recommendations. These notes are shown directly to the client, so "
    "write each as direct, encouraging, specific guidance. Rules: keep the original meaning and every "
    "fact exactly - never invent details, numbers, names, or claims; fix grammar and tone; make each note "
    "a smooth, constructive recommendation of 1-2 short sentences. Respond with ONLY a JSON object of the "
    "form {\"notes\":[{\"id\":\"...\",\"note\":\"...\"}]} - no markdown, no prose, one entry per id you were given."
)


def _extract_json(s):
    s = (s or "").strip()
    if s.startswith("```"):
        parts = s.split("```")
        s = parts[1] if len(parts) >= 2 else s.strip("`")
        s = re.sub(r"^\s*json\s*", "", s.strip())
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        s = s[a:b + 1]
    return json.loads(s)


@app.route("/admin/audits/<aid>/optimize", methods=["POST"])
def admin_audit_optimize(aid):
    if not session.get("admin"):
        return jsonify(ok=False), 403
    key = get_ai_key()
    if not key:
        return jsonify(ok=False, error="AI isn't set up yet. Add your Anthropic API key under Admin → Settings."), 400
    data = request.get_json(silent=True) or {}
    raw = data.get("notes", [])
    notes = []
    if isinstance(raw, list):
        for n in raw[:60]:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", ""))[:40]
            note = _clean_note(n.get("note", ""))[:600]
            if nid and note:
                notes.append({"id": nid, "note": note,
                              "area": _clean_line(n.get("section", ""))[:80],
                              "item": _clean_line(n.get("title", ""))[:160]})
    if not notes:
        return jsonify(ok=False, error="No notes to optimize yet - add a few notes first."), 400
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        user = ("Polish these audit notes. Return every note by its id.\n\n"
                + json.dumps(notes, ensure_ascii=False))
        resp = client.messages.create(
            model=os.getenv("AUDIT_AI_MODEL", "claude-opus-5"),
            max_tokens=4000,
            system=AUDIT_AI_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            return jsonify(ok=False, error="The AI declined this request."), 502
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        parsed = _extract_json(text)
        valid = {n["id"] for n in notes}
        out = {}
        for it in parsed.get("notes", []):
            iid = str(it.get("id", ""))
            polished = _clean_note(it.get("note", ""))[:600]
            if iid in valid and polished:
                out[iid] = polished
        if not out:
            return jsonify(ok=False, error="The AI returned nothing usable, please try again."), 502
        return jsonify(ok=True, notes=out)
    except Exception as e:
        return jsonify(ok=False, error="AI request failed (%s). Check the API key and try again." % type(e).__name__), 502


@app.route("/admin/audits/<aid>/delete", methods=["POST"])
def admin_audit_delete(aid):
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/audits")
    with _leads_lock:
        v = [x for x in load_audits() if x.get("id") != aid]
        save_audits(v)
    return redirect("/admin/audits")


def _audit_grade(pct):
    if pct is None:
        return ("Audit in progress", "#8a918b")
    if pct >= 85:
        return ("Strong profile", "#2a7d4f")
    if pct >= 65:
        return ("Solid, with room to grow", "#2a5c47")
    if pct >= 40:
        return ("Needs attention", "#b8862b")
    return ("Big opportunities here", "#c0502b")


@app.route("/audit/<aid>")
def audit_report(aid):
    a = next((x for x in load_audits() if x.get("id") == aid), None)
    if not a:
        return Response(AUDIT_NOTFOUND_HTML, mimetype="text/html", status=404)
    items = a.get("items", {})
    hidden = set(a.get("hidden", []) if isinstance(a.get("hidden"), list) else [])
    passes, fixes = [], []
    for sec in AUDIT_SECTIONS:
        for it in sec["items"]:
            if it["id"] in hidden:
                continue
            st = items.get(it["id"], {})
            if st.get("status") == "pass":
                passes.append((sec["name"], it))
            elif st.get("status") == "fix":
                fixes.append((sec["name"], it, st.get("note", "")))
    for cc in a.get("custom", []):
        if not (isinstance(cc, dict) and cc.get("t")):
            continue
        st = items.get(cc.get("id", ""), {})
        it = {"t": cc["t"], "h": ""}
        if st.get("status") == "pass":
            passes.append(("Additional", it))
        elif st.get("status") == "fix":
            fixes.append(("Additional", it, st.get("note", "")))
    applicable = len(passes) + len(fixes)
    pct = round(100.0 * len(passes) / applicable) if applicable else None
    grade, gcolor = _audit_grade(pct)
    score_disp = ("%d%%" % pct) if pct is not None else "&mdash;"
    ring = ("conic-gradient(%s %d%%, rgba(35,79,61,.12) 0)" % (gcolor, pct)) if pct is not None \
        else "conic-gradient(rgba(35,79,61,.12) 0 100%)"

    if applicable == 0:
        summary = "This audit hasn't been scored yet."
    elif not fixes:
        summary = "Great news &mdash; your profile is in excellent shape across the board."
    else:
        summary = ("We reviewed %d areas of your Facebook presence. %d are working well, and "
                   "%d have a clear opportunity to bring in more of the right clients." %
                   (applicable, len(passes), len(fixes)))

    strengths = ""
    for name, it in passes:
        strengths += ("<li><span class='ok'>&#10003;</span><span class='st'>%s</span>"
                      "<span class='tag'>%s</span></li>") % (_esc(it["t"]), _esc(name))
    if not strengths:
        strengths = "<li class='none'>Strengths will appear here as the audit is completed.</li>"

    fixes_html = ""
    for i, (name, it, note) in enumerate(fixes, 1):
        has_hint = bool(it.get("h"))
        rec = _esc(note) if note else ("Update this so it follows the best practice below."
                                       if has_hint else "Worth improving on the profile.")
        hint_html = ("<div class='fhint'>&#9432; %s</div>" % _esc(it["h"])) if has_hint else ""
        fixes_html += (
            "<div class='fix'><div class='fh'><span class='num'>%d</span>"
            "<span class='ftag'>%s</span></div><div class='ft'>%s</div>"
            "<div class='frec'>%s</div>%s</div>") % (
            i, _esc(name), _esc(it["t"]), rec, hint_html)
    if not fixes_html:
        fixes_html = "<div class='allgood'>&#127881; Nothing to fix right now &mdash; nicely done!</div>"

    html = (AUDIT_REPORT_HTML
            .replace("__CLIENT__", _esc(a.get("client", "") or "Your"))
            .replace("__DATE__", _esc((a.get("updated", "") or a.get("created", "") or "")[:10]))
            .replace("__SCORE__", score_disp)
            .replace("__RING__", ring)
            .replace("__GRADE__", _esc(grade))
            .replace("__GCOLOR__", gcolor)
            .replace("__SUMMARY__", summary)
            .replace("__PASSN__", str(len(passes)))
            .replace("__FIXN__", str(len(fixes)))
            .replace("__STRENGTHS__", strengths)
            .replace("__FIXES__", fixes_html))
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


AUDITS_LIST_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>FB Profile Audits</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:760px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.sub{color:#5c635e;font-size:14px;margin-bottom:18px}
.new{display:flex;gap:10px;margin-bottom:22px}
.new input{flex:1;padding:12px 14px;border:1px solid rgba(35,79,61,.2);border-radius:11px;font-size:15px}
.new button{padding:12px 18px;border:none;border-radius:11px;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:700;font-size:15px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:14px;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 8px 22px rgba(35,49,40,.06)}
td,th{text-align:left;padding:12px 14px;border-bottom:1px solid rgba(35,79,61,.09);vertical-align:middle}
th{color:#5c635e;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
a{color:#a97f2a;font-weight:600;text-decoration:none}
.mini{display:inline-block;width:120px;height:8px;border-radius:100px;background:rgba(35,79,61,.12);overflow:hidden;vertical-align:middle;margin-right:8px}
.mini span{display:block;height:100%;background:linear-gradient(90deg,#2a5c47,#c79a3b)}
.pc{font-size:12.5px;color:#5c635e;font-weight:700}
.pill{font-size:10.5px;font-weight:700;text-transform:uppercase;padding:2px 8px;border-radius:100px;margin-left:8px}
.pill.fix{background:rgba(199,80,43,.12);color:#b23}
td.act{white-space:nowrap;text-align:right}a.open{margin-right:8px}
.delf{display:inline}.del{border:none;background:none;color:#c0392b;font-size:14px;cursor:pointer;padding:4px 6px;border-radius:8px;opacity:.55}.del:hover{opacity:1;background:rgba(192,57,43,.08)}
</style></head><body><div class="wrap">
<div class="top"><h1>Facebook Profile Audits</h1><a class="back" href="/">&larr; Site</a></div>
<p class="sub">Run a branded profile audit for each client, track what needs work, and hand them the fix list.</p>
<form class="new" method="post" action="/admin/audits/new">
<input name="client" placeholder="Client name (e.g. Jane Smith — Keller Williams)" autocomplete="off" required>
<button type="submit">+ New audit</button></form>
<table><thead><tr><th>Client</th><th>Started</th><th>Progress</th><th></th></tr></thead>
<tbody>__ROWS__</tbody></table>
</div></body></html>"""


AUDIT_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Profile audit</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:0 0 60px}
.bar{position:sticky;top:0;z-index:20;background:rgba(248,247,243,.94);backdrop-filter:blur(8px);border-bottom:1px solid rgba(35,79,61,.12);padding:14px 18px}
.bwrap{max-width:820px;margin:0 auto}
.brow{display:flex;align-items:center;gap:12px}
.brow a.back{font-size:13px;color:#5c635e;text-decoration:none;white-space:nowrap}
.cli{flex:1;font-size:18px;font-weight:800;color:#234F3D;border:none;background:none;padding:4px 6px;border-radius:8px}
.cli:focus{outline:2px solid rgba(199,154,59,.5);background:#fff}
.saved{font-size:12px;font-weight:700;white-space:nowrap;transition:.2s}
.saved.saved-ok{color:#2a7d4f}.saved.saved-dirty{color:#b8862b}.saved.saved-saving{color:#8a918b}
.saved.saved-err{color:#c0392b}
.pbanner{background:#c0392b;color:#fff;text-align:center;font-weight:700;font-size:13.5px;padding:10px 16px}
.pbanner a{color:#ffe;text-decoration:underline}
.savebtn{border:none;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:700;font-size:13px;padding:8px 17px;border-radius:9px;cursor:pointer;white-space:nowrap}
.savebtn:disabled{opacity:.7;cursor:default}
.prog{display:flex;align-items:center;gap:12px;margin-top:10px}
.track{flex:1;height:10px;border-radius:100px;background:rgba(35,79,61,.12);overflow:hidden}
.fill{height:100%;width:0;background:linear-gradient(90deg,#2a5c47,#c79a3b);transition:width .3s}
.stat{font-size:12.5px;color:#5c635e;font-weight:700;white-space:nowrap}
.wrap{max-width:820px;margin:0 auto;padding:22px 18px 0}
.sec{margin-bottom:22px}
.sec-h{display:flex;align-items:center;gap:9px;margin:0 2px 10px}
.sec-h .ic{font-size:19px}.sec-h .sec-t{font-size:15px;font-weight:800;color:#234F3D;letter-spacing:.01em}
.item{position:relative;background:#fff;border:1px solid rgba(35,79,61,.1);border-radius:14px;padding:14px 16px;margin-bottom:10px;box-shadow:0 4px 14px rgba(35,49,40,.04)}
.item.flag{border-color:rgba(199,80,43,.4);box-shadow:0 4px 14px rgba(199,80,43,.08)}
.it-t{font-size:15px;font-weight:700;color:#2D2D2D;padding-right:24px}
.it-h{font-size:13px;color:#6a716b;margin-top:3px;line-height:1.5}
.btns{display:flex;gap:8px;margin-top:11px;flex-wrap:wrap}
.st{border:1.5px solid rgba(35,79,61,.18);background:#fff;color:#5c635e;font-weight:700;font-size:13px;padding:7px 13px;border-radius:100px;cursor:pointer;transition:.12s}
.st:hover{border-color:#c79a3b}
.st-pass.on{background:#e8f3ec;border-color:#2a7d4f;color:#1c6b40}
.st-fix.on{background:#fbece7;border-color:#c0502b;color:#a83b1d}
.st-na.on{background:#eee;border-color:#aaa;color:#666}
.note-btn{border-style:dashed!important;color:#8a918b}
.note-btn.has{border-style:solid!important;background:#f6f4ec;border-color:#c79a3b;color:#a97f2a}
.notewrap{margin-top:10px}
textarea.note{width:100%;min-height:66px;padding:10px 12px;border:1px solid rgba(35,79,61,.15);border-radius:10px;font-size:13.5px;font-family:inherit;resize:vertical;line-height:1.5}
textarea.note:focus{outline:2px solid rgba(199,154,59,.4)}
.ci-title{width:100%;font-size:15px;font-weight:700;color:#2D2D2D;border:1px dashed rgba(35,79,61,.28);border-radius:8px;padding:7px 9px;font-family:inherit;background:#fff}
.ci-title:focus{outline:2px solid rgba(199,154,59,.4);border-style:solid}
.cidel{position:absolute;top:10px;right:10px;border:none;background:none;color:#c0392b;font-size:14px;cursor:pointer;opacity:.5;padding:2px 6px;border-radius:6px}
.cidel:hover{opacity:1;background:rgba(192,57,43,.08)}
.additem{display:flex;gap:8px;margin-top:4px}
.additem input{flex:1;padding:11px 13px;border:1px solid rgba(35,79,61,.2);border-radius:10px;font-size:14px;font-family:inherit}
.additem input:focus{outline:2px solid rgba(199,154,59,.4)}
.additem button{border:none;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:700;font-size:14px;padding:11px 18px;border-radius:10px;cursor:pointer;white-space:nowrap}
.sec-add .sec-t{color:#a97f2a}
.sec-hidden .sec-t{color:#8a918b}
.hrow{display:flex;align-items:center;justify-content:space-between;gap:12px;background:#f4f3ee;border:1px dashed rgba(35,79,61,.2);border-radius:10px;padding:9px 12px;margin-bottom:8px;font-size:13.5px;color:#6a716b}
.hrow small{display:block;color:#a0a6a0;font-weight:700;text-transform:uppercase;font-size:10px;letter-spacing:.04em;margin-top:2px}
.restore{border:none;background:#234F3D;color:#f6f4ec;font-weight:700;font-size:12px;padding:6px 13px;border-radius:8px;cursor:pointer;white-space:nowrap}
.summary{max-width:820px;margin:8px auto 0;padding:0 18px}
.sumbox{background:#2a1f14;color:#f6f0e4;border-radius:16px;padding:20px 22px}
.sumbox h3{font-size:15px;margin-bottom:4px;color:#e0b862}
.sumbox .m{font-size:13px;color:#c9bfad;opacity:.8;margin-bottom:14px}
.sumbox ol{margin:0 0 16px 18px;font-size:14px;line-height:1.6}
.sumbox li{margin-bottom:7px}.sumbox li .n{display:block;font-size:12.5px;color:#d8c79b;white-space:pre-wrap}
.sumbox .none{opacity:.7;font-size:14px}
.copy{border:none;background:#e0b862;color:#2a2005;font-weight:700;font-size:13.5px;padding:10px 16px;border-radius:10px;cursor:pointer}
.sharebar{background:#eef4f0;border-bottom:1px solid rgba(35,79,61,.12)}
.swrap{max-width:820px;margin:0 auto;padding:9px 18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12.5px;color:#5c635e}
.swrap .lk{font-weight:700;color:#234F3D}
.swrap .url{flex:1;min-width:180px;color:#2a5c47;font-weight:600;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.swrap .scopy{border:none;background:#234F3D;color:#f6f4ec;font-weight:700;font-size:12px;padding:6px 12px;border-radius:8px;cursor:pointer}
.swrap .sprev{color:#a97f2a;font-weight:700;text-decoration:none;white-space:nowrap}
.aiwrap{max-width:820px;margin:16px auto 0;padding:0 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.aibtn{border:none;background:linear-gradient(135deg,#6f4bb0,#4a2f86);color:#f6f4ff;font-weight:700;font-size:14px;padding:11px 18px;border-radius:11px;cursor:pointer;box-shadow:0 6px 16px rgba(74,47,134,.25)}
.aibtn:disabled{opacity:.65;cursor:default}
.aimsg{font-size:13px;color:#5c635e}.aimsg a{color:#a97f2a;font-weight:700;cursor:pointer}
</style></head><body>
__PERSISTBANNER__
<div class="bar"><div class="bwrap">
  <div class="brow"><a class="back" href="/admin/audits">&larr; All audits</a>
    <input class="cli" id="cli" value="" placeholder="Client name">
    <span class="saved" id="saved">Saved &#10003;</span>
    <button class="savebtn" id="savebtn" type="button">Save</button></div>
  <div class="prog"><div class="track"><div class="fill" id="fill"></div></div>
    <span class="stat" id="stat">0 of 0 reviewed</span></div>
</div></div>
<div class="sharebar"><div class="swrap"><span class="lk">&#128279; Client report:</span>
  <a class="url" id="surl" href="__SHAREURL__" target="_blank" rel="noopener">__SHAREURL__</a>
  <button class="scopy" id="scopy" type="button">Copy link</button>
  <a class="sprev" href="__SHAREURL__" target="_blank" rel="noopener">Preview &#8599;</a></div></div>
<div class="aiwrap"><button class="aibtn" id="aiopt" type="button">&#10024; Optimize all notes with AI</button><span class="aimsg" id="aimsg"></span></div>
<div class="wrap" id="sections"></div>
<div class="summary"><div class="sumbox">
  <h3>&#128295; Fix list for this client</h3>
  <div class="m">Everything you marked &ldquo;Needs work&rdquo; &mdash; copy it straight into a message or report.</div>
  <ol id="fixlist"></ol><div class="none" id="fixnone">Nothing flagged yet.</div>
  <button class="copy" id="copy" type="button">Copy fix list</button>
</div></div>
<script>
var SECTIONS=__SECTIONS__, A=__AUDIT__, AID="__AID__";
var items=(A&&A.items)||{};
var custom=(A&&A.custom)||[];
var hidden=(A&&A.hidden)||[];
var ITEM_BY_ID={};
SECTIONS.forEach(function(sec){sec.items.forEach(function(it){ITEM_BY_ID[it.id]={sec:sec.name,t:it.t};});});
var root=document.getElementById('sections');
var cli=document.getElementById('cli'); cli.value=(A&&A.client)||'';
var savedInd=document.getElementById('saved'), fillEl=document.getElementById('fill'), statEl=document.getElementById('stat');
var fixOl=document.getElementById('fixlist'), fixNone=document.getElementById('fixnone');
var timer=null;

function lbl(st){return st==='pass'?'\\u2713 Looks good':st==='fix'?'\\u26A0 Needs work':'N/A';}
function uid(){return 'c_'+Math.random().toString(36).slice(2,10);}

function makeItem(id, title, hint, isCustom){
  var cur=items[id]||{};
  var row=document.createElement('div'); row.className='item'+(cur.status==='fix'?' flag':'');
  if(isCustom){
    var del=document.createElement('button'); del.type='button'; del.className='cidel'; del.title='Remove item'; del.innerHTML='&#10005;';
    del.onclick=function(){ removeCustom(id); };
    row.appendChild(del);
    var ti=document.createElement('input'); ti.className='ci-title'; ti.value=title; ti.placeholder='Item title';
    ti.oninput=function(){ setCustomTitle(id, ti.value); };
    row.appendChild(ti);
  } else {
    var hb=document.createElement('button'); hb.type='button'; hb.className='cidel'; hb.title='Remove from this audit'; hb.innerHTML='&#10005;';
    hb.onclick=function(){ hideDefault(id); };
    row.appendChild(hb);
    var t=document.createElement('div'); t.className='it-t'; t.textContent=title; row.appendChild(t);
    if(hint){var hh=document.createElement('div'); hh.className='it-h'; hh.textContent=hint; row.appendChild(hh);}
  }
  var btns=document.createElement('div'); btns.className='btns';
  ['pass','fix','na'].forEach(function(s){
    var b=document.createElement('button'); b.type='button';
    b.className='st st-'+s+(cur.status===s?' on':''); b.textContent=lbl(s);
    b.onclick=function(){ setStatus(id, cur.status===s?'':s); };
    btns.appendChild(b);
  });
  var nb=document.createElement('button'); nb.type='button'; nb.className='st note-btn'+(cur.note?' has':'');
  nb.textContent=cur.note?'\\uD83D\\uDCDD Note':'\\uD83D\\uDCDD Add note';
  btns.appendChild(nb);
  row.appendChild(btns);
  var wrap=document.createElement('div'); wrap.className='notewrap'; wrap.style.display=cur.note?'block':'none';
  var ta=document.createElement('textarea'); ta.className='note'; ta.placeholder='Your notes for this item...'; ta.value=cur.note||'';
  ta.oninput=function(){ setNote(id, ta.value); var has=!!ta.value; nb.classList.toggle('has',has); nb.textContent=has?'\\uD83D\\uDCDD Note':'\\uD83D\\uDCDD Add note'; };
  wrap.appendChild(ta); row.appendChild(wrap);
  nb.onclick=function(){ var show=wrap.style.display==='none'; wrap.style.display=show?'block':'none'; if(show)ta.focus(); };
  return row;
}

function render(){
  root.innerHTML='';
  SECTIONS.forEach(function(sec){
    var vis=sec.items.filter(function(it){ return hidden.indexOf(it.id)<0; });
    if(!vis.length) return;
    var se=document.createElement('div'); se.className='sec';
    var h=document.createElement('div'); h.className='sec-h';
    var ic=document.createElement('span'); ic.className='ic'; ic.textContent=sec.icon||''; h.appendChild(ic);
    var st=document.createElement('span'); st.className='sec-t'; st.textContent=sec.name; h.appendChild(st);
    se.appendChild(h);
    vis.forEach(function(it){ se.appendChild(makeItem(it.id, it.t, it.h, false)); });
    root.appendChild(se);
  });
  var cs=document.createElement('div'); cs.className='sec sec-add';
  var ch=document.createElement('div'); ch.className='sec-h';
  var cic=document.createElement('span'); cic.className='ic'; cic.textContent='\\u2795'; ch.appendChild(cic);
  var ct=document.createElement('span'); ct.className='sec-t'; ct.textContent='Your added items'; ch.appendChild(ct);
  cs.appendChild(ch);
  custom.forEach(function(cc){ cs.appendChild(makeItem(cc.id, cc.t, '', true)); });
  var add=document.createElement('div'); add.className='additem';
  var inp=document.createElement('input'); inp.type='text'; inp.placeholder='Add your own audit item, then press Add';
  var addBtn=document.createElement('button'); addBtn.type='button'; addBtn.textContent='Add';
  function doAdd(){ var v=(inp.value||'').trim(); if(!v)return; custom.push({id:uid(), t:v}); render(); scheduleSave(); var ni=root.querySelector('.additem input'); if(ni)ni.focus(); }
  addBtn.onclick=doAdd;
  inp.onkeydown=function(e){ if(e.key==='Enter'){ e.preventDefault(); doAdd(); } };
  add.appendChild(inp); add.appendChild(addBtn); cs.appendChild(add);
  root.appendChild(cs);
  if(hidden.length){
    var hs=document.createElement('div'); hs.className='sec sec-hidden';
    var hh=document.createElement('div'); hh.className='sec-h';
    var hic=document.createElement('span'); hic.className='ic'; hic.textContent='\\uD83D\\uDC41'; hh.appendChild(hic);
    var ht=document.createElement('span'); ht.className='sec-t'; ht.textContent='Removed from this audit ('+hidden.length+')'; hh.appendChild(ht);
    hs.appendChild(hh);
    hidden.forEach(function(hid){
      var meta=ITEM_BY_ID[hid]; if(!meta)return;
      var r=document.createElement('div'); r.className='hrow';
      var sp=document.createElement('span'); sp.textContent=meta.t;
      var tag=document.createElement('small'); tag.textContent=meta.sec; sp.appendChild(tag);
      r.appendChild(sp);
      var rb=document.createElement('button'); rb.type='button'; rb.className='restore'; rb.textContent='Restore';
      rb.onclick=function(){ restoreDefault(hid); };
      r.appendChild(rb); hs.appendChild(r);
    });
    root.appendChild(hs);
  }
  updateBar(); renderFix();
}
function hideDefault(id){ if(hidden.indexOf(id)<0)hidden.push(id); render(); scheduleSave(); }
function restoreDefault(id){ hidden=hidden.filter(function(x){ return x!==id; }); render(); scheduleSave(); }
function setStatus(id, s){
  var o=items[id]||{}; if(s){o.status=s;}else{delete o.status;}
  if(o.status||o.note){items[id]=o;}else{delete items[id];}
  render(); scheduleSave();
}
function setNote(id, v){
  var o=items[id]||{}; o.note=v;
  if(o.status||o.note){items[id]=o;}else{delete items[id];}
  renderFix(); scheduleSave();
}
function setCustomTitle(id, v){
  for(var i=0;i<custom.length;i++){ if(custom[i].id===id){ custom[i].t=v; break; } }
  renderFix(); scheduleSave();
}
function removeCustom(id){
  custom=custom.filter(function(c){ return c.id!==id; });
  delete items[id];
  render(); scheduleSave();
}
function eachItem(cb){
  SECTIONS.forEach(function(sec){ sec.items.forEach(function(it){ if(hidden.indexOf(it.id)<0) cb(sec.name, it.id, it.t); }); });
  custom.forEach(function(cc){ cb('Additional', cc.id, cc.t); });
}
function counts(){
  var total=0,rev=0,fix=0,pass=0;
  eachItem(function(sn,id,t){ total++; var s=(items[id]||{}).status; if(s)rev++; if(s==='fix')fix++; if(s==='pass')pass++; });
  return {total:total,rev:rev,fix:fix,pass:pass};
}
function updateBar(){
  var c=counts(); var pct=c.total?Math.round(100*c.rev/c.total):0;
  fillEl.style.width=pct+'%';
  statEl.textContent=c.rev+' of '+c.total+' reviewed \\u00B7 '+c.pass+' good \\u00B7 '+c.fix+' to fix';
}
function renderFix(){
  updateBar();
  fixOl.innerHTML=''; var any=false;
  eachItem(function(sn,id,t){
    var cur=items[id]||{};
    if(cur.status==='fix'){
      any=true;
      var li=document.createElement('li');
      li.appendChild(document.createTextNode(sn+': '+(t||'(untitled)')));
      if(cur.note){var n=document.createElement('span'); n.className='n'; n.textContent='\\u21B3 '+cur.note; li.appendChild(n);}
      fixOl.appendChild(li);
    }
  });
  fixNone.style.display=any?'none':'block';
}
var dirty=false, retryT=null;
function payload(){ return JSON.stringify({client:cli.value, items:items, custom:custom, hidden:hidden}); }
function setSaveUI(state){
  var m={ok:'Saved \\u2713',dirty:'Unsaved changes\\u2026',saving:'Saving\\u2026',err:'\\u26A0 Not saved \\u2014 retrying'};
  savedInd.className='saved saved-'+state; savedInd.textContent=m[state]||'';
}
function scheduleSave(){ dirty=true; setSaveUI('dirty'); if(timer)clearTimeout(timer); timer=setTimeout(save,650); }
function doSave(cb){
  if(timer){clearTimeout(timer);timer=null;}
  if(retryT){clearTimeout(retryT);retryT=null;}
  setSaveUI('saving');
  return fetch('/admin/audits/'+AID,{method:'POST',headers:{'Content-Type':'application/json'},body:payload()})
   .then(function(r){ if(!r.ok)throw 0; return r.json(); })
   .then(function(j){ if(j&&j.ok){ dirty=false; setSaveUI('ok'); if(cb)cb(j); } else { throw 0; } })
   .catch(function(){ setSaveUI('err'); retryT=setTimeout(save,3000); if(cb)cb(null); });
}
function save(){ doSave(); }
cli.oninput=function(){ scheduleSave(); };
document.getElementById('savebtn').onclick=function(){
  var btn=this; btn.disabled=true; btn.textContent='Saving\\u2026';
  doSave(function(j){ btn.textContent=(j&&j.ok)?'Saved \\u2713':'Try again'; setTimeout(function(){btn.disabled=false; btn.textContent='Save';},1300); });
};
window.addEventListener('beforeunload', function(e){
  if(dirty){
    if(navigator.sendBeacon){ try{ navigator.sendBeacon('/admin/audits/'+AID, new Blob([payload()],{type:'application/json'})); }catch(_){ } }
    e.preventDefault(); e.returnValue=''; return '';
  }
});
setSaveUI('ok');
document.getElementById('copy').onclick=function(){
  var lines=['Facebook profile audit \\u2014 '+(cli.value||'client'),''];
  var any=false;
  eachItem(function(sn,id,t){
    var cur=items[id]||{};
    if(cur.status==='fix'){any=true; lines.push('\\u2022 '+sn+': '+(t||'(untitled)')+(cur.note?' ('+cur.note+')':''));}
  });
  if(!any)lines.push('Everything looks good \\u2014 no changes needed!');
  var txt=lines.join('\\n');
  var btn=this;
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(function(){btn.textContent='Copied \\u2713';setTimeout(function(){btn.textContent='Copy fix list';},1400);});
  }else{
    var ta=document.createElement('textarea');ta.value=txt;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);
    btn.textContent='Copied \\u2713';setTimeout(function(){btn.textContent='Copy fix list';},1400);
  }
};
document.getElementById('scopy').onclick=function(){
  var url=document.getElementById('surl').href, btn=this;
  function done(){btn.textContent='Copied \\u2713';setTimeout(function(){btn.textContent='Copy link';},1400);}
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(url).then(done);}
  else{var ta=document.createElement('textarea');ta.value=url;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);done();}
};
document.getElementById('aiopt').onclick=function(){
  var payload=[];
  eachItem(function(sn,id,t){ var cur=items[id]||{}; if(cur.note){ payload.push({id:id, note:cur.note, section:sn, title:t}); } });
  var btn=this, msg=document.getElementById('aimsg');
  if(!payload.length){ msg.textContent='Add a few notes first, then I can polish them.'; return; }
  var backup={}; payload.forEach(function(p){ backup[p.id]=(items[p.id]||{}).note||''; });
  btn.disabled=true; btn.textContent='\\u2728 Polishing '+payload.length+' note'+(payload.length>1?'s':'')+'\\u2026'; msg.textContent='';
  fetch('/admin/audits/'+AID+'/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({notes:payload})})
    .then(function(r){return r.json();}).then(function(j){
      btn.disabled=false; btn.textContent='\\u2728 Optimize all notes with AI';
      if(j&&j.ok&&j.notes){
        var n=0; for(var id in j.notes){ if(items[id]){ items[id].note=j.notes[id]; n++; } }
        render(); scheduleSave();
        msg.innerHTML='Polished '+n+' note'+(n===1?'':'s')+'. <a id="aiundo">Undo</a>';
        var u=document.getElementById('aiundo');
        if(u)u.onclick=function(){ for(var bid in backup){ if(items[bid]){ items[bid].note=backup[bid]; } } render(); scheduleSave(); msg.textContent='Reverted to your original notes.'; };
      } else { msg.textContent=(j&&j.error)||'Could not optimize right now.'; }
    }).catch(function(){ btn.disabled=false; btn.textContent='\\u2728 Optimize all notes with AI'; msg.textContent='Network error, please try again.'; });
};
render();
</script>
</body></html>"""


AUDIT_NOTFOUND_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Audit not found</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;display:grid;place-items:center;min-height:100vh;padding:24px;text-align:center}
.c{max-width:420px}h1{font-size:22px;color:#234F3D;margin-bottom:8px}p{color:#5c635e}a{color:#a97f2a;font-weight:700}</style></head>
<body><div class="c"><h1>This audit link isn't available</h1><p>It may have been removed. Visit <a href="/">MacRandle Acres</a>.</p></div></body></html>"""


AUDIT_REPORT_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Your Facebook Profile Audit - MacRandle Acres</title>
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.6;padding:0 0 60px;
  background-image:radial-gradient(900px 500px at 50% -10%,rgba(199,154,59,.1),transparent 60%)}
.wrap{max-width:720px;margin:0 auto;padding:0 18px}
.head{position:relative;background:linear-gradient(160deg,#26543f,#1a3b2d);color:#f6f4ec;border-radius:0 0 26px 26px;padding:30px 24px 34px;text-align:center}
.mark{width:44px;height:44px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);display:grid;place-items:center;font-weight:800;color:#234F3D;margin:0 auto 12px}
.brand{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:#e0b862;font-weight:700}
.head h1{font-size:23px;font-weight:800;margin:6px 0 2px}
.head .who{opacity:.85;font-size:14px}
.head .adv{font-size:12.5px;color:#e0b862;font-weight:700;margin-top:3px;letter-spacing:.02em}
.printbtn{position:absolute;top:14px;right:14px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.12);color:#f6f4ec;font-weight:700;font-size:12px;padding:7px 12px;border-radius:9px;cursor:pointer}
.printbtn:hover{background:rgba(255,255,255,.2)}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
@media print{body{padding:0;background:#fff}.noprint{display:none!important}.head{border-radius:0}.cta{break-inside:avoid}.fix{break-inside:avoid}}
.ring{width:132px;height:132px;border-radius:50%;background:__RING__;display:grid;place-items:center;margin:20px auto 8px}
.ring .in{width:104px;height:104px;border-radius:50%;background:#1e4535;display:grid;place-items:center;flex-direction:column}
.ring .in b{font-size:30px;color:#fff;line-height:1}.ring .in span{font-size:10.5px;color:#cfe0d6;text-transform:uppercase;letter-spacing:.08em;margin-top:3px}
.grade{font-weight:800;font-size:16px;color:#fff}
.summary{max-width:560px;margin:16px auto 0;font-size:15px;color:#eaf1ec;opacity:.92}
.sec{margin-top:26px}
.sec h2{font-size:16px;color:#234F3D;margin:0 2px 12px;display:flex;align-items:center;gap:8px}
.card{background:#fff;border:1px solid rgba(35,79,61,.1);border-radius:16px;box-shadow:0 6px 18px rgba(35,49,40,.05);overflow:hidden}
ul.str{list-style:none;padding:6px 4px}
ul.str li{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid rgba(35,79,61,.07);font-size:14.5px}
ul.str li:last-child{border-bottom:none}
ul.str .ok{width:22px;height:22px;border-radius:50%;background:#e8f3ec;color:#1c6b40;display:grid;place-items:center;font-size:13px;font-weight:800;flex:0 0 auto}
ul.str .st{flex:1;font-weight:600;color:#2D2D2D}
ul.str .tag{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:#8a918b;font-weight:700}
ul.str .none{opacity:.6;font-weight:500}
.fix{background:#fff;border:1px solid rgba(35,79,61,.1);border-left:4px solid #c79a3b;border-radius:14px;padding:16px 18px;margin-bottom:12px;box-shadow:0 6px 18px rgba(35,49,40,.05)}
.fix .fh{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.fix .num{width:24px;height:24px;border-radius:50%;background:linear-gradient(135deg,#e0b862,#a97f2a);color:#2a2005;font-weight:800;font-size:13px;display:grid;place-items:center;flex:0 0 auto}
.fix .ftag{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#a97f2a;font-weight:800}
.fix .ft{font-size:16px;font-weight:800;color:#234F3D}
.fix .frec{font-size:14.5px;color:#2D2D2D;margin-top:5px;white-space:pre-wrap}
.fix .fhint{font-size:13px;color:#6a716b;margin-top:8px;background:#f6f4ec;border-radius:9px;padding:8px 11px}
.allgood{background:#e8f3ec;border-radius:14px;padding:20px;text-align:center;font-size:15px;color:#1c6b40;font-weight:600}
.cta{margin-top:30px;background:linear-gradient(160deg,#2a5c47,#1a3b2d);border-radius:20px;padding:28px 24px;text-align:center;color:#f6f4ec}
.cta h3{font-size:19px;margin-bottom:6px}.cta p{opacity:.88;font-size:14.5px;max-width:440px;margin:0 auto 16px}
.cta a{display:inline-block;background:linear-gradient(135deg,#e0b862,#a97f2a);color:#2a2005;font-weight:800;font-size:15px;padding:14px 26px;border-radius:12px;text-decoration:none}
.foot{text-align:center;color:#8a918b;font-size:12.5px;margin-top:26px}
</style></head><body>
<div class="head">
  <button class="printbtn noprint" type="button" onclick="window.print()">&#128424; Save as PDF</button>
  <div class="mark">M</div><div class="brand">MacRandle Acres</div>
  <h1>Facebook Profile Audit</h1>
  <div class="who">Prepared for __CLIENT__ &middot; __DATE__</div>
  <div class="adv">Reviewed by Jeff Randle &middot; MacRandle Acres</div>
  <div class="ring"><div class="in"><b>__SCORE__</b><span>Profile score</span></div></div>
  <div class="grade">__GRADE__</div>
  <div class="summary">__SUMMARY__</div>
</div>
<div class="wrap">
  <div class="sec"><h2>&#9989; What's working (__PASSN__)</h2>
    <div class="card"><ul class="str">__STRENGTHS__</ul></div></div>
  <div class="sec"><h2>&#128295; Priority improvements (__FIXN__)</h2>
    __FIXES__</div>
  <div class="cta"><h3>Want to walk through this together?</h3>
    <p>Happy to hop on a quick call, go through these with you, and answer anything that comes up. No pressure &mdash; just a friendly working session.</p>
    <a href="/book">Grab a time to chat &rarr;</a></div>
  <div class="foot">MacRandle Acres &middot; Growth advisory for small businesses</div>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Website & app design service page + "dream site" intake form
# ---------------------------------------------------------------------------
PROJECTS_PATH = os.path.join(DATA, "projects.json")


def load_projects():
    try:
        with open(PROJECTS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def save_projects(v):
    try:
        tmp = PROJECTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PROJECTS_PATH)
        return True
    except Exception:
        try:
            with open(PROJECTS_PATH, "w", encoding="utf-8") as f:
                json.dump(v, f, ensure_ascii=False)
            return True
        except Exception:
            return False


def send_project_email(rec):
    from email.message import EmailMessage
    sender = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    # 1) Notify Jeff
    m = EmailMessage()
    m["From"] = sender
    m["To"] = ORG_EMAIL
    m["Reply-To"] = rec.get("email", ORG_EMAIL)
    m["Subject"] = "New design project: %s (%s)" % (rec.get("name", ""), rec.get("type", "") or "—")
    m.set_content(
        "New website/app design inquiry.\n\n"
        "Name:     %s\nEmail:    %s\nBuilding: %s\nTimeline: %s\nBudget:   %s\nLikes:    %s\n\n"
        "What it's for / goal:\n%s\n\nTheir dream site/app:\n%s\n"
        % (rec.get("name", ""), rec.get("email", ""), rec.get("type", "") or "-",
           rec.get("timeline", "") or "-", rec.get("budget", "") or "-", rec.get("inspo", "") or "-",
           rec.get("goal", "") or "-", rec.get("dream", "") or "-"))
    _smtp_send(m)
    # 2) Auto-reply to the person who submitted
    if rec.get("email"):
        first = (rec.get("name", "").split(" ") or [""])[0] or "there"
        ack = EmailMessage()
        ack["From"] = sender
        ack["To"] = rec["email"]
        ack["Reply-To"] = ORG_EMAIL
        ack["Subject"] = "Thanks — I got your project"
        ack.set_content(
            "Hi %s,\n\nThank you for sharing your project with MacRandle Acres. I've got the details, "
            "and I'll personally review them and reply within one business day to talk through your "
            "vision and the next steps.\n\nIf anything else comes to mind in the meantime, just reply "
            "to this email.\n\nTalk soon,\nJeff Randle\nMacRandle Acres" % first)
        _smtp_send(ack)
    return True


@app.route("/design")
def design_page():
    head = "" if session.get("admin") else tracking_head()
    resp = Response(DESIGN_HTML.replace("__TRACK__", head), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/project", methods=["POST"])
def api_project():
    if not rate_ok("project", client_ip(), 8, 900):
        return jsonify(ok=False, error="Too many submissions, please try again shortly."), 429
    data = request.get_json(silent=True) or {}
    if str(data.get("website", "")).strip():  # honeypot
        return jsonify(ok=True)
    name = _clean_line(data.get("name", ""))[:120]
    email = _clean_line(data.get("email", ""))[:160]
    dream = _clean_note(data.get("dream", ""))[:2000]
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not dream:
        return jsonify(ok=False, error="Please add your name, a valid email, and a little about your project."), 400
    rec = {"t": datetime.datetime.utcnow().isoformat(timespec="seconds"),
           "name": name, "email": email,
           "type": _clean_line(data.get("type", ""))[:60],
           "goal": _clean_note(data.get("goal", ""))[:800],
           "timeline": _clean_line(data.get("timeline", ""))[:60],
           "budget": _clean_line(data.get("budget", ""))[:60],
           "inspo": _clean_line(data.get("inspo", ""))[:300],
           "dream": dream}
    with _leads_lock:
        v = load_projects()
        v.append(rec)
        save_projects(v)
    threading.Thread(target=send_project_email, args=(rec,), daemon=True).start()
    return jsonify(ok=True)


@app.route("/admin/projects")
def admin_projects():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/projects")
    rows = ""
    for r in sorted(load_projects(), key=lambda x: x.get("t", ""), reverse=True):
        when = _esc((r.get("t", "") or "").replace("T", " ")[:16])
        rows += ("<div class='proj'><div class='ph'><b>%s</b> <span class='tp'>%s</span>"
                 "<span class='dt'>%s</span></div>"
                 "<div class='pmeta'><a href='mailto:%s'>%s</a> &middot; Timeline: %s &middot; Budget: %s &middot; Likes: %s</div>"
                 "<div class='pl'><span class='k'>Goal:</span> %s</div>"
                 "<div class='pl'><span class='k'>Dream:</span> %s</div></div>") % (
                 _esc(r.get("name", "")), _esc(r.get("type", "") or "—"), when,
                 _esc(r.get("email", "")), _esc(r.get("email", "")),
                 _esc(r.get("timeline", "") or "—"), _esc(r.get("budget", "") or "—"),
                 _esc(r.get("inspo", "") or "—"),
                 _esc(r.get("goal", "") or "—").replace("\n", "<br>"),
                 _esc(r.get("dream", "") or "—").replace("\n", "<br>"))
    if not rows:
        rows = "<p class='empty'>No design inquiries yet.</p>"
    return Response(PROJECTS_HTML.replace("__ROWS__", rows), mimetype="text/html")


DESIGN_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Website and App Design | Lucid Marketing, by MacRandle Acres</title>
<meta name="description" content="Custom websites and apps for small businesses. Lucid Marketing, by MacRandle Acres.">
<link rel="canonical" href="https://www.macrandleacres.com/design">
<meta property="og:title" content="Website and App Design | Lucid Marketing, by MacRandle Acres">
<meta property="og:description" content="Custom websites and apps for small businesses. Lucid Marketing, by MacRandle Acres.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.macrandleacres.com/design">
<meta property="og:image" content="https://www.macrandleacres.com/og-v5.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Website and App Design | Lucid Marketing, by MacRandle Acres">
<meta name="twitter:description" content="Custom websites and apps for small businesses. Lucid Marketing, by MacRandle Acres.">
<meta name="twitter:image" content="https://www.macrandleacres.com/og-v5.jpg">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;0,9..144,700;1,9..144,400&family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<script>document.documentElement.className+=' js'</script>
__TRACK__
<style>*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.6;padding:0 0 60px;overflow-x:hidden;
  background-image:radial-gradient(900px 480px at 50% -10%,rgba(199,154,59,.12),transparent 60%)}
.head{background:linear-gradient(160deg,#26543f,#1a3b2d);color:#f6f4ec;padding:44px 22px 40px;text-align:center;border-radius:0 0 26px 26px}
.mark{width:44px;height:44px;border-radius:50%;background:radial-gradient(circle at 38% 34%,#fbf7ea,#d8cfb0);display:grid;place-items:center;font-weight:800;color:#234F3D;margin:0 auto 14px;overflow:hidden}
.mark img{width:100%;height:100%;object-fit:cover}
.eyebrow{font-size:11.5px;letter-spacing:.18em;text-transform:uppercase;color:#e0b862;font-weight:700}
.head h1{font-size:clamp(26px,5vw,38px);font-weight:800;margin:8px 0 8px;color:#fff;line-height:1.12}
.head p{opacity:.9;font-size:16px;max-width:540px;margin:0 auto}
.wins{max-width:720px;margin:22px auto 0;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:0 18px}
.win{background:rgba(255,255,255,.06);border:1px solid rgba(224,184,98,.22);border-radius:14px;padding:14px 12px}
.win .wi{font-size:22px}.win .wt{font-size:13px;font-weight:700;margin-top:5px;color:#fff}
.card{max-width:640px;margin:26px auto 0;background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:22px;padding:30px 28px;box-shadow:0 22px 55px rgba(35,49,40,.12)}
.card h2{font-size:22px;color:#234F3D;margin-bottom:4px}.card .sub{color:#5c635e;font-size:14.5px;margin-bottom:20px}
.fg{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.fld{display:flex;flex-direction:column;gap:6px}.fld.full{grid-column:1/-1}
.fld span{font-size:13px;font-weight:700;color:#234F3D}
.fld input,.fld select,.fld textarea{font-family:inherit;font-size:14.5px;padding:11px 13px;border:1px solid rgba(35,79,61,.2);border-radius:10px;color:#2D2D2D;background:#fff;outline:none}
.fld textarea{min-height:110px;resize:vertical;line-height:1.55}
.fld input:focus,.fld select:focus,.fld textarea:focus{outline:2px solid rgba(199,154,59,.4)}
.hp{position:absolute;left:-9999px}
.send{margin-top:18px;width:100%;padding:15px;border:none;border-radius:12px;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:800;font-size:16px;cursor:pointer}
.send:disabled{opacity:.7}
.msg{font-size:13.5px;color:#b23;margin-top:10px;text-align:center}
.done{text-align:center;padding:20px 6px}.done .ic{font-size:46px;margin-bottom:12px}.done h2{font-size:25px;color:#234F3D;margin-bottom:8px}.done p{color:#5c635e;font-size:16px;max-width:420px;margin:0 auto}
.foot{text-align:center;color:#8a918b;font-size:13px;margin-top:22px}.foot a{color:#a97f2a;font-weight:700;text-decoration:none}
@media(max-width:560px){.fg{grid-template-columns:1fr}.wins{grid-template-columns:1fr 1fr}}
.hbtn,.scbtn{display:inline-block;text-decoration:none;font-weight:800;font-size:15px;padding:13px 26px;border-radius:12px;background:linear-gradient(135deg,#e0b862,#a97f2a);color:#2a2005;box-shadow:0 8px 22px rgba(169,127,42,.3)}
.hbtn{margin-top:20px}.scbtn{margin-top:22px}
.how{max-width:820px;margin:34px auto 0;padding:0 18px;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.how-step{background:#fff;border:1px solid rgba(35,79,61,.1);border-radius:14px;padding:20px 16px;text-align:center;box-shadow:0 6px 18px rgba(35,49,40,.05)}
.how-step .hn{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec;font-weight:800;display:grid;place-items:center;margin:0 auto 10px}
.how-step .ht{font-weight:800;color:#234F3D;font-size:15px}
.how-step .hd{font-size:13px;color:#5c635e;margin-top:4px;line-height:1.5}
.reply-note{text-align:center;font-size:12.5px;color:#8a918b;margin-top:10px}
.faq{max-width:640px;margin:26px auto 0;padding:0 18px}
.faq h3{font-size:17px;color:#234F3D;text-align:center;margin-bottom:14px}
.faq details{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:12px;padding:2px 16px;margin-bottom:10px}
.faq summary{cursor:pointer;font-weight:700;color:#2D2D2D;font-size:14.5px;padding:12px 0;list-style:none}
.faq summary::-webkit-details-marker{display:none}
.faq summary:after{content:'+';float:right;color:#a97f2a;font-weight:800}
.faq details[open] summary:after{content:'\\2013'}
.faq details p{font-size:14px;color:#5c635e;padding:0 0 14px;line-height:1.55;margin:0}
@media(max-width:640px){.how{grid-template-columns:1fr;max-width:420px}}
/* ---------- page motion ---------- */
#prog{position:fixed;top:0;left:0;height:3px;width:0;z-index:50;background:linear-gradient(90deg,#e0b862,#f6dc9c,#a97f2a);box-shadow:0 0 12px rgba(224,184,98,.6)}
.head{position:relative;overflow:hidden;padding:56px 22px 48px}
.head>*{position:relative;z-index:1}
.head .aur{position:absolute;z-index:0;border-radius:50%;filter:blur(60px);pointer-events:none;animation:drift 14s ease-in-out infinite alternate}
.aur.a1{width:440px;height:440px;background:#e0b862;top:-240px;left:-140px;opacity:.26}
.aur.a2{width:380px;height:380px;background:#5fa37f;bottom:-210px;right:-90px;opacity:.38;animation-delay:-7s}
.aur.a3{width:240px;height:240px;background:#f6dc9c;top:30%;left:62%;opacity:.12;animation-delay:-3s}
@keyframes drift{to{transform:translate(80px,46px) scale(1.2)}}
.head h1{font-family:'Fraunces',Georgia,serif;font-weight:700;font-size:clamp(32px,6vw,54px);letter-spacing:-.01em;line-height:1.04;max-width:760px;margin:10px auto 14px}
.head h1 .w{display:inline-block;opacity:0;transform:translateY(26px) rotate(2deg);animation:wup .8s cubic-bezier(.2,.8,.2,1) forwards}
@keyframes wup{to{opacity:1;transform:none}}
.head h1 em{font-style:italic;font-weight:400;background:linear-gradient(90deg,#e0b862,#fff3cf,#e0b862);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shim 4s linear infinite}
@keyframes shim{to{background-position:-200% 0}}
.head p{font-size:17px;max-width:560px;opacity:0;animation:wup .8s .75s cubic-bezier(.2,.8,.2,1) forwards}
.head .hbtn{opacity:0;animation:wup .8s .95s cubic-bezier(.2,.8,.2,1) forwards}
.win{opacity:0;animation:wup .8s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .3s,background .3s}
.win:nth-child(1){animation-delay:1.1s}.win:nth-child(2){animation-delay:1.22s}.win:nth-child(3){animation-delay:1.34s}
.win:hover{transform:translateY(-4px);background:rgba(255,255,255,.11)}
.hbtn,.scbtn,.send{position:relative;overflow:hidden;transition:transform .25s,box-shadow .25s}
.hbtn:hover,.scbtn:hover{transform:translateY(-2px);box-shadow:0 14px 30px rgba(169,127,42,.42)}
.hbtn:after,.scbtn:after,.send:after{content:'';position:absolute;top:0;left:-60%;width:40%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.55),transparent);transform:skewX(-20deg);animation:sheen 3.6s ease-in-out infinite}
@keyframes sheen{0%,60%{left:-60%}100%{left:130%}}
.js .rv{opacity:0;transform:translateY(36px);transition:opacity .9s cubic-bezier(.2,.8,.2,1),transform .9s cubic-bezier(.2,.8,.2,1)}
.js .rv.in{opacity:1;transform:none}
/* what's included marquee */
.inc{overflow:hidden;margin:30px 0 0;padding:14px 0;border-top:1px solid rgba(35,79,61,.1);border-bottom:1px solid rgba(35,79,61,.1);background:#fff;white-space:nowrap}
.inc div{display:inline-block;animation:marquee 34s linear infinite}
.inc span{display:inline-flex;align-items:center;gap:8px;margin:0 22px;font-weight:700;font-size:14px;color:#234F3D}
.inc span:before{content:'';width:7px;height:7px;border-radius:50%;background:#c79a3b;box-shadow:0 0 0 4px rgba(199,154,59,.18)}
@keyframes marquee{to{transform:translateX(-50%)}}
/* how it works */
.how{position:relative}
.how:before{content:'';position:absolute;top:36px;left:18%;right:18%;height:2px;background:linear-gradient(90deg,#e0b862,#234F3D);transform:scaleX(0);transform-origin:left;transition:transform 1.4s .35s cubic-bezier(.2,.8,.2,1)}
.how.in:before{transform:scaleX(1)}
.how-step{position:relative;z-index:1;transition:transform .3s,box-shadow .3s}
.how-step:hover{transform:translateY(-4px);box-shadow:0 16px 34px rgba(35,49,40,.12)}
.how-step .hn{box-shadow:0 0 0 6px #F8F7F3}
.how.in .how-step .hn{animation:pop .6s cubic-bezier(.3,1.6,.5,1) both}
.how.in .how-step:nth-child(2) .hn{animation-delay:.5s}.how.in .how-step:nth-child(3) .hn{animation-delay:1s}
@keyframes pop{from{transform:scale(.3)}}
.fld input,.fld select,.fld textarea{transition:border-color .2s,box-shadow .2s}
.fld input:focus,.fld select:focus,.fld textarea:focus{outline:none;border-color:#c79a3b;box-shadow:0 0 0 4px rgba(199,154,59,.18)}
.faq details{transition:box-shadow .25s}.faq details[open]{box-shadow:0 10px 26px rgba(35,49,40,.08)}

/* ---------- showcase ---------- */
.showcase{max-width:1080px;margin:64px auto 0;padding:0 18px;text-align:center}
.sc-eyebrow{font-size:11.5px;letter-spacing:.18em;text-transform:uppercase;color:#a97f2a;font-weight:700}
.showcase h2{font-family:'Fraunces',Georgia,serif;font-size:clamp(28px,4.4vw,42px);color:#234F3D;margin:6px 0 8px;line-height:1.08;font-weight:700}
.sc-sub{color:#5c635e;font-size:15.5px;max-width:580px;margin:0 auto 22px}
.sc-tabs{display:inline-flex;gap:6px;padding:6px;background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:100px;box-shadow:0 8px 24px rgba(35,49,40,.08);margin-bottom:28px;flex-wrap:wrap;justify-content:center}
.sc-tab{font:inherit;font-size:13.5px;font-weight:700;border:0;background:transparent;color:#5c635e;padding:9px 16px;border-radius:100px;cursor:pointer;position:relative;overflow:hidden;transition:color .25s,background .25s}
.sc-tab:hover{color:#234F3D}
.sc-tab.on{background:linear-gradient(135deg,#2a5c47,#1a3b2d);color:#f6f4ec}
.sc-tab .tb{position:absolute;left:0;bottom:0;height:3px;background:#e0b862;width:0}
.sc-tab.on.auto .tb{animation:tabfill 12s linear forwards}
@keyframes tabfill{to{width:100%}}
.stage{position:relative;padding:0 130px 40px 0;max-width:1000px;margin:0 auto}
.browser{position:relative;border-radius:16px;overflow:hidden;background:#fff;box-shadow:0 44px 90px rgba(20,40,30,.28),0 0 0 1px rgba(35,79,61,.1);transform:perspective(1800px) rotateX(3deg);transition:transform .6s cubic-bezier(.2,.8,.2,1)}
.stage:hover .browser{transform:perspective(1800px) rotateX(0)}
.bbar{display:flex;align-items:center;gap:7px;padding:11px 14px;background:linear-gradient(#f3efe6,#e9e3d7);border-bottom:1px solid rgba(0,0,0,.06)}
.bbar i{width:11px;height:11px;border-radius:50%;background:#ff5f57;display:inline-block;flex:none}.bbar i:nth-child(2){background:#febc2e}.bbar i:nth-child(3){background:#28c840}
.url{flex:1;max-width:340px;margin:0 auto;background:#fff;border-radius:8px;padding:5px 12px;font-size:12px;color:#6b6457;font-family:ui-monospace,Menlo,monospace;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vp{position:relative;height:480px;overflow:hidden;text-align:left;background:#0b1820}
.site{position:absolute;inset:0;opacity:0;visibility:hidden;transform:scale(1.03);transition:opacity .7s,transform .9s cubic-bezier(.2,.8,.2,1),visibility .7s}
.site.on{opacity:1;visibility:visible;transform:none}
.sz{transform-origin:top left;width:760px}
.site.on .sc{animation:autoscroll 12s ease-in-out .6s infinite}
.vp:hover .sc{animation-play-state:paused}
@keyframes autoscroll{0%,12%{transform:translateY(0)}48%,62%{transform:translateY(calc(-1*var(--d,0px)))}96%,100%{transform:translateY(0)}}
.sc-note{font-size:12.5px;color:#8a918b;margin-top:8px}

/* Oakline - real estate */
.s-oak .sc{background:#0b1820;color:#f3ede2}
.o-hero{position:relative;height:410px;overflow:hidden}
.o-scene{position:absolute;inset:0;width:100%;height:100%}
.o-sun{animation:sunrise 9s ease-in-out infinite alternate}
@keyframes sunrise{from{transform:translateY(18px)}to{transform:translateY(-8px)}}
.o-waves line{animation:wave 4s ease-in-out infinite alternate}
.o-waves line:nth-child(2n){animation-delay:-2s}
@keyframes wave{to{transform:translateX(-30px);opacity:.2}}
.o-win{animation:winglow 3s ease-in-out infinite alternate}.o-win:nth-child(2n){animation-delay:-1.5s}
@keyframes winglow{to{opacity:.45}}
.o-bird{animation:fly 14s linear infinite}
@keyframes fly{from{transform:translate(-80px,0)}to{transform:translate(900px,-40px)}}
.o-hero:after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,rgba(8,20,28,.86),rgba(8,20,28,.25) 62%,transparent)}
.o-nav{position:absolute;z-index:2;top:0;left:0;right:0;display:flex;align-items:center;gap:22px;padding:18px 28px;font-size:11.5px;letter-spacing:.06em}
.o-nav b{font-family:'Fraunces',serif;font-size:17px;letter-spacing:.3em;color:#f2c98a}
.o-nav span:nth-child(2){opacity:.78;margin-left:auto}
.o-pill{border:1px solid rgba(242,201,138,.6);padding:7px 14px;border-radius:100px;color:#f2c98a}
.o-copy{position:absolute;z-index:2;left:28px;top:100px;max-width:470px}
.o-kick{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:#f2c98a;margin-bottom:10px}
.s-oak h3{font-family:'Fraunces',serif;font-weight:600;font-size:50px;line-height:1;margin:0 0 24px;color:#fff}
.s-oak h3 i{color:#f2c98a;font-weight:400}
.o-search{display:flex;align-items:stretch;background:rgba(255,255,255,.97);border-radius:14px;padding:6px;box-shadow:0 18px 40px rgba(0,0,0,.35);color:#1d2a30;width:max-content}
.o-search span{padding:6px 14px;font-size:12.5px;font-weight:600;border-right:1px solid #e6e1d6;white-space:nowrap}
.o-search small{display:block;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#8b8577;font-weight:700}
.o-search b{display:grid;place-items:center;background:linear-gradient(135deg,#d9a35a,#b07a32);color:#fff;font-size:12.5px;padding:0 20px;border-radius:10px;margin-left:6px}
.o-sec{padding:32px 28px 8px}
.o-h{display:flex;justify-content:space-between;align-items:baseline;font-family:'Fraunces',serif;font-size:24px;color:#fff;margin-bottom:16px}
.o-h span{font-family:'Inter',sans-serif;font-size:12px;color:#f2c98a}
.o-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.o-card{background:#12232c;border-radius:12px;overflow:hidden;border:1px solid rgba(242,201,138,.12)}
.o-img{height:124px;position:relative;overflow:hidden}
.o-img:after{content:'';position:absolute;left:20%;right:20%;bottom:0;height:58%;background:#0e1c22;clip-path:polygon(0 40%,45% 0,100% 32%,100% 100%,0 100%)}
.o-img:before{content:'';position:absolute;z-index:1;left:38%;bottom:12%;width:9%;height:16%;background:#ffcf7a;box-shadow:34px 0 0 #ffcf7a,17px -26px 0 #ffcf7a;opacity:.9}
.o-i1{background:linear-gradient(#f6c387,#e0855e 60%,#3c5560 61%)}
.o-i2{background:linear-gradient(#9cc7d8,#d7e7ea 60%,#4d6b5a 61%)}
.o-i3{background:linear-gradient(#2a3b66,#b7698a 60%,#2b3b44 61%)}
.o-img em{position:absolute;z-index:2;top:10px;left:10px;font-style:normal;font-size:9.5px;font-weight:800;letter-spacing:.1em;background:#f2c98a;color:#1d2a30;padding:3px 8px;border-radius:100px}
.o-p{font-family:'Fraunces',serif;font-size:19px;color:#fff;padding:10px 12px 0}
.o-a{font-size:11.5px;padding:2px 12px;opacity:.8}.o-m{font-size:10.5px;padding:4px 12px 12px;color:#f2c98a}
.o-stats{display:grid;grid-template-columns:repeat(3,1fr);margin:24px 28px;border-top:1px solid rgba(242,201,138,.18);border-bottom:1px solid rgba(242,201,138,.18);padding:18px 0;text-align:center;font-size:11px;opacity:.92}
.o-stats b{display:block;font-family:'Fraunces',serif;font-size:28px;color:#f2c98a;font-weight:600}
.o-quote{padding:6px 28px 40px;font-family:'Fraunces',serif;font-style:italic;font-size:22px;color:#fff;max-width:600px}
.o-quote span{display:block;font-family:'Inter',sans-serif;font-style:normal;font-size:11px;opacity:.65;margin-top:8px}

/* Maison - restaurant */
.s-mai .sc{background:#f3ece1;color:#2b2119}
.m-nav{display:flex;align-items:center;justify-content:center;gap:26px;padding:18px 26px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;border-bottom:1px solid rgba(43,33,25,.12)}
.m-nav b{font-family:'Fraunces',serif;font-style:italic;font-weight:400;font-size:26px;letter-spacing:0;text-transform:none;color:#8a3324;margin:0 12px}
.m-res{background:#8a3324;color:#f3ece1;padding:7px 14px}
.m-hero{display:grid;grid-template-columns:1.1fr .9fr;align-items:center;gap:10px;padding:34px 32px 30px}
.m-kick{font-size:10.5px;letter-spacing:.26em;text-transform:uppercase;color:#8a3324;margin-bottom:12px}
.s-mai h3{font-family:'Fraunces',serif;font-size:52px;line-height:.96;font-weight:600;margin:0 0 16px}
.s-mai h3 i{color:#8a3324;font-weight:400}
.m-copy p{font-size:13px;max-width:300px;opacity:.8;margin-bottom:20px}
.m-btn{display:inline-block;background:#2b2119;color:#f3ece1;font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;padding:12px 22px}
.m-plate{position:relative;justify-self:center;width:250px;height:250px}
.m-plate svg{width:100%;height:100%;animation:spin 46s linear infinite;filter:drop-shadow(0 24px 30px rgba(60,30,10,.28))}
@keyframes spin{to{transform:rotate(360deg)}}
.m-steam{position:absolute;left:50%;top:-6px;width:60px;margin-left:-30px;height:60px}
.m-steam i{position:absolute;bottom:0;width:8px;height:40px;border-radius:50%;background:linear-gradient(transparent,rgba(255,255,255,.9));filter:blur(4px);animation:steam 3.2s ease-out infinite}
.m-steam i:nth-child(1){left:10px}.m-steam i:nth-child(2){left:26px;animation-delay:-1s}.m-steam i:nth-child(3){left:42px;animation-delay:-2s}
@keyframes steam{from{transform:translateY(20px) scaleX(1);opacity:0}40%{opacity:.8}to{transform:translateY(-34px) scaleX(2.2);opacity:0}}
.m-strip{overflow:hidden;background:#2b2119;color:#e9c78f;font-family:'Fraunces',serif;font-style:italic;font-size:18px;white-space:nowrap;padding:12px 0}
.m-strip div{display:inline-block;animation:marquee 20s linear infinite}
.m-low{display:grid;grid-template-columns:1.2fr 1fr;gap:26px;padding:30px 32px 40px}
.s-mai h4{font-family:'Fraunces',serif;font-size:24px;margin-bottom:12px;font-weight:600}
.m-row{display:flex;align-items:baseline;gap:8px;font-size:13px;padding:7px 0}
.m-row i{flex:1;border-bottom:1px dotted rgba(43,33,25,.35)}
.m-row b{font-family:'Fraunces',serif;color:#8a3324;font-size:15px}
.m-row small{display:block;font-size:10.5px;opacity:.6}
.m-book{background:#fffaf2;border:1px solid rgba(43,33,25,.1);padding:18px;box-shadow:0 14px 30px rgba(60,30,10,.08)}
.m-days,.m-times{display:flex;gap:6px;margin-bottom:10px}
.m-days span,.m-times span{flex:1;text-align:center;font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;border:1px solid rgba(43,33,25,.15);padding:6px 0}
.m-days b{display:block;font-family:'Fraunces',serif;font-size:18px;letter-spacing:0}
.m-times span{font-size:12px;letter-spacing:0;padding:8px 0}
.m-days .on,.m-times .on{background:#8a3324;color:#f3ece1;border-color:#8a3324}
.m-times .on{animation:pick 2.4s ease-in-out infinite}
@keyframes pick{50%{box-shadow:0 0 0 5px rgba(138,51,36,.2)}}
.m-go{background:#2b2119;color:#f3ece1;text-align:center;font-size:11px;letter-spacing:.18em;text-transform:uppercase;padding:12px;margin-top:4px}

/* Lumen - coaching */
.s-lum .sc{background:#f6f3fb;color:#2c2a3f}
.l-hero{position:relative;overflow:hidden;padding:0 0 46px;background:linear-gradient(160deg,#efe9fb,#e3f0f2)}
.l-blob{position:absolute;border-radius:50%;filter:blur(28px);opacity:.8;animation:blob 11s ease-in-out infinite alternate}
.b1{width:280px;height:280px;background:#c9b6f2;top:-70px;right:60px}
.b2{width:210px;height:210px;background:#a7e0d6;top:130px;right:230px;animation-delay:-4s}
.b3{width:190px;height:190px;background:#ffd2bd;top:50px;right:-30px;animation-delay:-8s}
@keyframes blob{to{transform:translate(-46px,34px) scale(1.18)}}
.l-nav{position:relative;display:flex;align-items:center;gap:20px;padding:18px 28px;font-size:12px}
.l-nav b{font-size:22px;font-weight:800;letter-spacing:-.03em;color:#5b45a8}
.l-nav span:nth-child(2){margin-left:auto;opacity:.7}
.l-pill{background:#2c2a3f;color:#fff;padding:7px 14px;border-radius:100px}
.l-copy{position:relative;padding:48px 28px 0;max-width:480px}
.l-kick{display:inline-block;font-size:11px;font-weight:700;background:rgba(255,255,255,.75);padding:5px 12px;border-radius:100px;color:#5b45a8;margin-bottom:14px}
.s-lum h3{font-size:52px;line-height:1;letter-spacing:-.035em;font-weight:800;margin:0 0 14px}
.s-lum h3 span{background:linear-gradient(90deg,#5b45a8,#2fa59a,#5b45a8);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shim 5s linear infinite}
.l-copy p{font-size:14px;opacity:.75;margin-bottom:22px}
.l-ctas{display:flex;gap:10px}
.l-btn{background:linear-gradient(135deg,#6b52c8,#4a37a0);color:#fff;font-weight:700;font-size:12.5px;padding:12px 20px;border-radius:100px;box-shadow:0 10px 24px rgba(91,69,168,.35)}
.l-ghost{font-weight:700;font-size:12.5px;padding:12px 18px;border-radius:100px;background:rgba(255,255,255,.8)}
.l-sec{padding:32px 28px 8px}
.l-sec h4{font-size:24px;font-weight:800;letter-spacing:-.02em;margin-bottom:14px}
.l-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.l-card{background:#fff;border-radius:16px;padding:16px;box-shadow:0 10px 26px rgba(44,42,63,.08)}
.l-ic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;font-size:17px;margin-bottom:10px}
.l-card b{display:block;font-size:14.5px}.l-card small{font-size:11px;opacity:.65}
.l-card .pr{margin-top:10px;font-weight:800;color:#5b45a8;font-size:13px}
.l-cal{display:grid;grid-template-columns:1fr 1.15fr;gap:20px;align-items:center;background:#fff;border-radius:18px;padding:20px;margin:18px 28px 40px;box-shadow:0 10px 26px rgba(44,42,63,.08)}
.l-cal b{font-size:20px;letter-spacing:-.02em}.l-cal p{font-size:12.5px;opacity:.7;margin-top:4px}
.l-days{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;font-size:10.5px;text-align:center}
.l-days span{padding:6px 0;border-radius:8px;opacity:.45}
.l-days .av{opacity:1;background:#efeafc;color:#5b45a8;font-weight:700}
.l-days .pk{opacity:1;background:#5b45a8;color:#fff;font-weight:700;animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(91,69,168,.5)}70%{box-shadow:0 0 0 9px rgba(91,69,168,0)}100%{box-shadow:0 0 0 0 rgba(91,69,168,0)}}

/* Pulse - phone app */
.phone{position:absolute;right:0;bottom:0;width:224px;height:456px;border-radius:40px;background:#0d0f14;padding:10px;box-shadow:0 44px 80px rgba(10,20,15,.45),inset 0 0 0 2px #2a2e38;animation:floaty 6s ease-in-out infinite;z-index:3;text-align:left}
@keyframes floaty{50%{transform:translateY(-14px) rotate(-1.2deg)}}
.notch{position:absolute;top:18px;left:50%;width:72px;height:20px;margin-left:-36px;background:#000;border-radius:20px;z-index:2}
.pscreen{height:100%;border-radius:31px;overflow:hidden;background:radial-gradient(260px 200px at 80% 0%,rgba(80,227,194,.2),transparent),linear-gradient(#141821,#0c0e13);color:#e9edf5;padding:44px 16px 0;position:relative;font-size:11px}
.p-top{position:absolute;top:17px;left:24px;right:24px;display:flex;justify-content:space-between;font-size:10px;font-weight:700}
.p-hi{font-size:16px;font-weight:800}
.p-sub{opacity:.6;font-size:10.5px;margin-bottom:8px}
.p-ring{position:relative;width:118px;height:118px;margin:2px auto 10px}
.p-ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.p-ring circle{fill:none;stroke-width:10;stroke-linecap:round}
.rb{stroke:rgba(255,255,255,.08)}
.rf{stroke:url(#pg);stroke-dasharray:314;stroke-dashoffset:314;animation:ring1 3.4s cubic-bezier(.3,.8,.2,1) .4s infinite alternate}
.rf2{stroke:#ff8a65;stroke-width:8;stroke-dasharray:226;stroke-dashoffset:226;animation:ring2 3.4s cubic-bezier(.3,.8,.2,1) .8s infinite alternate}
@keyframes ring1{to{stroke-dashoffset:69}}@keyframes ring2{to{stroke-dashoffset:90}}
.p-pct{position:absolute;inset:0;display:grid;place-content:center;text-align:center;line-height:1.1}
.p-pct b{font-size:22px}.p-pct small{font-size:9px;opacity:.6}
.p-bars{display:flex;align-items:flex-end;gap:6px;height:54px;padding:0 4px}
.p-bars span{flex:1;background:linear-gradient(#50e3c2,#2b8f7a);border-radius:4px 4px 2px 2px;transform-origin:bottom;animation:grow 2.8s ease-in-out infinite alternate}
.p-bars span:nth-child(odd){animation-delay:-1s}
@keyframes grow{from{transform:scaleY(.3)}}
.p-days{display:flex;gap:6px;padding:4px 4px 10px;font-size:8.5px;opacity:.5}.p-days i{flex:1;text-align:center;font-style:normal}
.p-item{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.05);border-radius:12px;padding:8px 10px;margin-bottom:6px}
.p-item b{width:17px;height:17px;border-radius:6px;border:2px solid #50e3c2;display:grid;place-items:center;font-size:10px;color:#0c0e13;flex:none}
.p-item.ck b{background:#50e3c2}
.p-item.ck2 b{animation:check 5s ease-in-out infinite}
@keyframes check{0%,25%{background:transparent;color:transparent}35%,100%{background:#50e3c2;color:#0c0e13}}
.p-item span{flex:1}.p-item small{opacity:.5}
.p-label{position:absolute;bottom:-28px;left:0;right:0;text-align:center;font-size:12px;font-weight:700;color:#5c635e}
@media(max-width:860px){.stage{padding:0 0 10px}.phone{position:relative;right:auto;bottom:auto;margin:44px auto 44px}.vp{height:420px}}
@media(max-width:560px){.vp{height:360px}.sc-tab{padding:8px 12px;font-size:12.5px}.how:before{display:none}.inc span{font-size:13px;margin:0 14px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}.js .rv,.head h1 .w,.head p,.head .hbtn,.win{opacity:1!important;transform:none!important}.vp{overflow-y:auto}.how:before{transform:none}}
</style></head><body>
<div id="prog"></div>
<div class="head">
  <span class="aur a1"></span><span class="aur a2"></span><span class="aur a3"></span>
  <div class="mark"><img src="/logo-v5.jpg" alt="MacRandle Acres logo, Remove Friction. Accelerate Growth." onerror="this.parentNode.textContent='M'"></div>
  <div class="eyebrow">Design &amp; Build</div>
  <h1><span class="w" style="animation-delay:0.10s">Websites</span> <span class="w" style="animation-delay:0.18s">&amp;</span> <span class="w" style="animation-delay:0.26s">apps</span> <span class="w" style="animation-delay:0.34s">that</span> <em><span class="w" style="animation-delay:0.42s">stop</span> <span class="w" style="animation-delay:0.50s">the</span> <span class="w" style="animation-delay:0.58s">scroll.</span></em></h1>
  <p>Custom-designed, beautifully animated, and built to turn visitors into clients. Tell me what you're dreaming up and I'll handle everything from first sketch to launch day.</p>
  <a class="hbtn" href="#pbody">Start my project &darr;</a>
  <div class="wins">
    <div class="win"><div class="wi">&#127912;</div><div class="wt">Custom design</div></div>
    <div class="win"><div class="wi">&#9889;</div><div class="wt">Built to convert</div></div>
    <div class="win"><div class="wi">&#129309;</div><div class="wt">Done for you</div></div>
  </div>
</div>
<div class="inc"><div>
  <span>Custom design, never a template</span><span>Mobile-first</span><span>Animations that captivate</span><span>Lightning fast</span><span>Built-in booking &amp; forms</span><span>SEO-ready</span><span>Analytics set up</span><span>Hosting &amp; launch handled</span>
  <span>Custom design, never a template</span><span>Mobile-first</span><span>Animations that captivate</span><span>Lightning fast</span><span>Built-in booking &amp; forms</span><span>SEO-ready</span><span>Analytics set up</span><span>Hosting &amp; launch handled</span>
</div></div>
<section class="showcase rv" id="work">
  <div class="sc-eyebrow">Concept work</div>
  <h2>Sites people remember</h2>
  <p class="sc-sub">Three concept builds, each designed from scratch for a different kind of business, plus an app. Click through them. Your project gets the same craft, built around your brand.</p>
  <div class="sc-tabs" role="tablist">
    <button class="sc-tab on auto" data-i="0">Restaurant<span class="tb"></span></button>
    <button class="sc-tab" data-i="1">Coaching &amp; wellness<span class="tb"></span></button>
    <button class="sc-tab" data-i="2">Luxury real estate<span class="tb"></span></button>
  </div>
  <div class="stage">
    <div class="browser">
      <div class="bbar"><i></i><i></i><i></i><span class="url" id="sc_url">maisontable.com</span></div>
      <div class="vp" id="sc_vp">

        <div class="site s-oak" data-order="2" data-url="oaklinecoastal.com"><div class="sz"><div class="sc">
          <div class="o-hero">
            <svg class="o-scene" viewBox="0 0 760 410" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
              <defs>
                <linearGradient id="oSky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0f2a3a"/><stop offset=".55" stop-color="#c46b4a"/><stop offset="1" stop-color="#f2b46a"/></linearGradient>
                <linearGradient id="oSea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#28485a"/><stop offset="1" stop-color="#0b1d27"/></linearGradient>
                <radialGradient id="oGlow"><stop offset="0" stop-color="#ffe2b0" stop-opacity=".8"/><stop offset="1" stop-color="#ffe2b0" stop-opacity="0"/></radialGradient>
              </defs>
              <rect width="760" height="410" fill="url(#oSky)"/>
              <g class="o-sun"><circle cx="560" cy="262" r="110" fill="url(#oGlow)"/><circle cx="560" cy="262" r="44" fill="#ffd9a0"/></g>
              <path class="o-bird" d="M0 120 q8 -8 16 0 q8 -8 16 0" stroke="#1a2a33" stroke-width="2" fill="none"/>
              <rect y="280" width="760" height="130" fill="url(#oSea)"/>
              <g class="o-waves" stroke="#ffd9a0" stroke-opacity=".55" stroke-width="2" stroke-linecap="round">
                <line x1="500" y1="296" x2="620" y2="296"/><line x1="470" y1="312" x2="650" y2="312"/><line x1="520" y1="330" x2="600" y2="330"/><line x1="440" y1="350" x2="690" y2="350"/><line x1="540" y1="372" x2="590" y2="372"/>
              </g>
              <path d="M0 300 Q200 262 420 300 L420 410 L0 410 Z" fill="#0a161c"/>
              <path d="M150 284 L150 214 L262 214 L262 186 L396 186 L396 284 Z" fill="#0e1e26"/>
              <rect x="140" y="208" width="132" height="8" fill="#1a2f38"/><rect x="252" y="180" width="154" height="8" fill="#1a2f38"/>
              <rect class="o-win" x="166" y="232" width="34" height="38" fill="#ffcf7a"/><rect class="o-win" x="212" y="232" width="34" height="38" fill="#ffcf7a"/>
              <rect class="o-win" x="278" y="204" width="48" height="66" fill="#ffcf7a"/><rect class="o-win" x="336" y="204" width="44" height="30" fill="#ffcf7a"/>
            </svg>
            <div class="o-nav"><b>OAKLINE</b><span>Buy &middot; Sell &middot; Neighborhoods &middot; About</span><span class="o-pill">Let's talk</span></div>
            <div class="o-copy">
              <div class="o-kick">Coastal Maryland &middot; Since 2009</div>
              <h3>Find your place<br><i>on the coast.</i></h3>
              <div class="o-search"><span><small>Location</small>Ocean City, MD</span><span><small>Price</small>$600k &ndash; $1.5M</span><span><small>Beds</small>3+</span><b>Search</b></div>
            </div>
          </div>
          <div class="o-sec">
            <div class="o-h">Featured listings <span>View all 48 &rarr;</span></div>
            <div class="o-grid">
              <div class="o-card"><div class="o-img o-i1"><em>NEW</em></div><div class="o-p">$1,295,000</div><div class="o-a">12 Bayview Ln &middot; Ocean City</div><div class="o-m">4 bd &middot; 3 ba &middot; 2,840 sqft</div></div>
              <div class="o-card"><div class="o-img o-i2"><em>WATERFRONT</em></div><div class="o-p">$879,000</div><div class="o-a">7 Heron Point &middot; Berlin</div><div class="o-m">3 bd &middot; 2 ba &middot; 1,960 sqft</div></div>
              <div class="o-card"><div class="o-img o-i3"><em>OPEN SUN</em></div><div class="o-p">$1,540,000</div><div class="o-a">88 Dune Rd &middot; Fenwick</div><div class="o-m">5 bd &middot; 4 ba &middot; 3,400 sqft</div></div>
            </div>
          </div>
          <div class="o-stats"><div><b>$480M</b>in homes sold</div><div><b>1,200+</b>families moved</div><div><b>4.9&#9733;</b>from 300+ reviews</div></div>
          <div class="o-quote">&ldquo;They sold our home in six days, over asking. It felt effortless.&rdquo;<span>THE CARTERS &middot; BERLIN, MD</span></div>
        </div></div></div>

        <div class="site s-mai on" data-order="0" data-url="maisontable.com"><div class="sz"><div class="sc">
          <div class="m-nav"><span>Menu</span><span>Story</span><b>Maison</b><span>Events</span><span class="m-res">Reserve</span></div>
          <div class="m-hero">
            <div class="m-copy">
              <div class="m-kick">Est. 2019 &middot; Downtown</div>
              <h3>Seasonal.<br>Local.<br><i>Unforgettable.</i></h3>
              <p>A twelve-seat chef's counter and a menu that changes with the farms.</p>
              <span class="m-btn">Reserve a table</span>
            </div>
            <div class="m-plate">
              <div class="m-steam"><i></i><i></i><i></i></div>
              <svg viewBox="0 0 200 200" aria-hidden="true">
                <circle cx="100" cy="100" r="96" fill="#fbf7f0"/><circle cx="100" cy="100" r="95" fill="none" stroke="#e2d6c3" stroke-width="2"/><circle cx="100" cy="100" r="72" fill="none" stroke="#efe5d6" stroke-width="1.5"/>
                <path d="M58 118 Q60 74 104 70 Q146 70 142 112 Q138 140 98 140 Q60 140 58 118 Z" fill="#b8542b"/>
                <path d="M70 112 Q74 84 104 82 Q132 84 130 108" fill="none" stroke="#e4935a" stroke-width="7" stroke-linecap="round" opacity=".85"/>
                <path d="M78 122 Q100 112 124 124" fill="none" stroke="#7a2e14" stroke-width="3" opacity=".45"/>
                <path d="M52 76 q16 -22 36 -6 q-16 22 -36 6z" fill="#4f7a2a"/><path d="M60 72 q12 -6 26 -2" stroke="#2f5216" stroke-width="1.5" fill="none"/>
                <circle cx="146" cy="78" r="9" fill="#6b8f3a"/><circle cx="156" cy="92" r="7" fill="#7fa548"/><circle cx="140" cy="94" r="6" fill="#5d7f2f"/>
                <circle cx="150" cy="134" r="5" fill="#d9b13b"/><circle cx="62" cy="146" r="3.5" fill="#8a3324"/><circle cx="74" cy="154" r="2.5" fill="#8a3324"/><circle cx="52" cy="134" r="2.5" fill="#8a3324"/>
              </svg>
            </div>
          </div>
          <div class="m-strip"><div>Tonight &middot; Wood-fired &middot; Natural wine &middot; Open till eleven &middot; Chef's counter &middot; Farm to table &middot;&nbsp;Tonight &middot; Wood-fired &middot; Natural wine &middot; Open till eleven &middot; Chef's counter &middot; Farm to table &middot;&nbsp;</div></div>
          <div class="m-low">
            <div><h4>Tonight's menu</h4>
              <div class="m-row"><span>Charred Chesapeake oysters<small>brown butter, chili, lemon</small></span><i></i><b>18</b></div>
              <div class="m-row"><span>Heirloom tomato &amp; peach<small>burrata, basil oil</small></span><i></i><b>16</b></div>
              <div class="m-row"><span>Wood-fired rockfish<small>sweet corn, chanterelles</small></span><i></i><b>34</b></div>
              <div class="m-row"><span>Dry-aged ribeye<small>bone marrow jus, embers</small></span><i></i><b>52</b></div>
              <div class="m-row"><span>Honey lavender tart<small>cr&egrave;me fra&icirc;che</small></span><i></i><b>12</b></div>
            </div>
            <div class="m-book"><h4>Book your evening</h4>
              <div class="m-days"><span>Thu<b>14</b></span><span class="on">Fri<b>15</b></span><span>Sat<b>16</b></span><span>Sun<b>17</b></span></div>
              <div class="m-times"><span>6:00</span><span class="on">7:30</span><span>9:00</span></div>
              <div class="m-go">Confirm &middot; 2 guests</div>
            </div>
          </div>
        </div></div></div>

        <div class="site s-lum" data-order="1" data-url="lumenwellness.co"><div class="sz"><div class="sc">
          <div class="l-hero"><div class="l-blob b1"></div><div class="l-blob b2"></div><div class="l-blob b3"></div>
            <div class="l-nav"><b>lumen</b><span>Programs &middot; Journal &middot; About</span><span class="l-pill">Book a call</span></div>
            <div class="l-copy">
              <div class="l-kick">&#10022; 1:1 coaching &middot; online</div>
              <h3>Feel like<br><span>yourself again.</span></h3>
              <p>Calm, clarity, and a plan that fits real life. No 5am routines required.</p>
              <div class="l-ctas"><span class="l-btn">Start a free session</span><span class="l-ghost">How it works</span></div>
            </div>
          </div>
          <div class="l-sec"><h4>Find your program</h4>
            <div class="l-cards">
              <div class="l-card"><div class="l-ic" style="background:#efeafc">&#127769;</div><b>Sleep Reset</b><small>4 weeks &middot; guided</small><div class="pr">$249</div></div>
              <div class="l-card"><div class="l-ic" style="background:#e0f4f0">&#127807;</div><b>Stress to Calm</b><small>6 weeks &middot; 1:1</small><div class="pr">$480</div></div>
              <div class="l-card"><div class="l-ic" style="background:#ffeee5">&#9728;&#65039;</div><b>Energy Rebuild</b><small>8 weeks &middot; 1:1</small><div class="pr">$720</div></div>
            </div>
          </div>
          <div class="l-cal"><div><b>Pick a time that works</b><p>Free 20-minute intro call. No pressure, just a conversation.</p></div>
            <div class="l-days"><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span>
              <span>3</span><span class="av">4</span><span>5</span><span class="av">6</span><span class="pk">7</span><span>8</span><span>9</span>
              <span class="av">10</span><span>11</span><span class="av">12</span><span class="av">13</span><span>14</span><span>15</span><span>16</span></div>
          </div>
        </div></div></div>

      </div>
    </div>
    <div class="phone" aria-hidden="true"><div class="notch"></div><div class="pscreen">
      <div class="p-top"><span>9:41</span><span>&#9679;&#9679;&#9679; &#9646;</span></div>
      <div class="p-hi">Good morning, Sam</div>
      <div class="p-sub">12-day streak. Keep it going &#128293;</div>
      <div class="p-ring"><svg viewBox="0 0 120 120"><defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#50e3c2"/><stop offset="1" stop-color="#7c8cff"/></linearGradient></defs>
        <circle class="rb" cx="60" cy="60" r="50"/><circle class="rf" cx="60" cy="60" r="50"/><circle class="rb" cx="60" cy="60" r="36" style="stroke-width:8"/><circle class="rf2" cx="60" cy="60" r="36"/></svg>
        <div class="p-pct"><b>78%</b><small>daily goal</small></div></div>
      <div class="p-bars"><span style="height:40%"></span><span style="height:65%"></span><span style="height:52%"></span><span style="height:88%"></span><span style="height:70%"></span><span style="height:96%"></span><span style="height:60%"></span></div>
      <div class="p-days"><i>M</i><i>T</i><i>W</i><i>T</i><i>F</i><i>S</i><i>S</i></div>
      <div class="p-item ck"><b>&#10003;</b><span>Morning walk</span><small>20m</small></div>
      <div class="p-item ck ck2"><b>&#10003;</b><span>Drink water</span><small>6/8</small></div>
      <div class="p-item"><b></b><span>Read 10 pages</span><small>9pm</small></div>
    </div><div class="p-label">App concept &middot; Pulse</div></div>
  </div>
  <div class="sc-note">Hover a site to pause it &middot; Every build is mobile-first</div>
  <a class="scbtn" href="#pbody">Start your project &darr;</a>
</section>
<div class="how rv">
  <div class="how-step"><div class="hn">1</div><div class="ht">Share your vision</div><div class="hd">Fill out the quick form below. It takes about two minutes.</div></div>
  <div class="how-step"><div class="hn">2</div><div class="ht">I design &amp; build</div><div class="hd">Custom-crafted to your brand, handled end to end.</div></div>
  <div class="how-step"><div class="hn">3</div><div class="ht">You launch</div><div class="hd">Go live and start bringing in business.</div></div>
</div>
<div class="card rv" id="pbody">
  <h2>Tell me about your dream site</h2>
  <div class="sub">A few quick questions. No cost to reach out, and there are options for every budget, from a simple site to a full custom build.</div>
  <div class="fg">
    <label class="fld"><span>Your name *</span><input id="pd_name" autocomplete="name"></label>
    <label class="fld"><span>Email *</span><input id="pd_email" type="email" autocomplete="email"></label>
    <label class="fld"><span>What do you want built?</span><select id="pd_type">
      <option value="">Choose one…</option><option>Website</option><option>Web app</option>
      <option>Mobile app</option><option>Not sure yet</option></select></label>
    <label class="fld"><span>Timeline</span><select id="pd_timeline">
      <option value="">Choose one…</option><option>ASAP</option><option>Within a month</option>
      <option>1–3 months</option><option>Flexible</option></select></label>
    <label class="fld full"><span>Describe your dream site or app *</span>
      <textarea id="pd_dream" placeholder="What should it do? What's the vibe or feeling? Any pages or features you already have in mind?"></textarea></label>
    <label class="fld full"><span>What's it for? (your business, goal, or who it's for)</span>
      <input id="pd_goal" placeholder="e.g. book more real estate clients, sell a product, launch a community…"></label>
    <label class="fld full"><span>Budget range (optional)</span><select id="pd_budget">
      <option value="">Prefer not to say</option><option>Under $2k</option><option>$2k–$5k</option>
      <option>$5k–$10k</option><option>$10k+</option></select></label>
    <label class="fld full"><span>Sites or apps you love (optional)</span>
      <input id="pd_inspo" placeholder="Paste any links or names for inspiration"></label>
  </div>
  <input type="text" id="pd_hp" class="hp" tabindex="-1" autocomplete="off">
  <button class="send" id="pd_submit">Bring my vision to life &rarr;</button>
  <div class="reply-note">I personally reply within 1 business day.</div>
  <div class="msg" id="pd_msg"></div>
  <div class="foot">Prefer to talk first? <a href="/book">Book a free Growth Audit call</a></div>
</div>
<div class="faq rv">
  <h3>Common questions</h3>
  <details><summary>Do I own the finished site?</summary><p>Yes. Once it's done, it's yours to keep.</p></details>
  <details><summary>Can you handle hosting, domain, and launch?</summary><p>Yes. I can take care of hosting, your domain, and getting everything live, so you don't have to.</p></details>
  <details><summary>How long does it take?</summary><p>It depends on the scope. We'll agree on a clear timeline together before any work starts.</p></details>
  <details><summary>What about changes and updates later?</summary><p>Absolutely. We'll set up an easy way to keep your site fresh so you're never stuck.</p></details>
</div>
<footer style="text-align:center;padding:40px 18px 8px;color:#5c635e;font-size:14px">
  <p style="font-style:italic;color:#a97f2a;margin-bottom:6px">Lucid Marketing, by MacRandle Acres.</p>
  <p>Remove Friction. Accelerate Growth.</p>
  <p style="margin-top:14px"><a href="/" style="color:#a97f2a;font-weight:700;text-decoration:none">Home</a> &middot; <a href="/privacy" style="color:#a97f2a;font-weight:700;text-decoration:none">Privacy</a></p>
</footer>
<script>
var b=document.getElementById('pbody');
document.getElementById('pd_submit').onclick=function(){
  var g=function(id){var e=document.getElementById(id);return e?(e.value||'').trim():'';};
  var name=g('pd_name'), email=g('pd_email'), dream=g('pd_dream'), msg=document.getElementById('pd_msg');
  if(!name||email.indexOf('@')<1||!dream){ msg.textContent='Please add your name, a valid email, and a little about your project.'; return; }
  var btn=this; btn.disabled=true; btn.textContent='Sending\\u2026'; msg.textContent='';
  fetch('/api/project',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    name:name,email:email,dream:dream,goal:g('pd_goal'),type:g('pd_type'),timeline:g('pd_timeline'),budget:g('pd_budget'),inspo:g('pd_inspo'),website:g('pd_hp')})})
   .then(function(r){return r.json();}).then(function(j){
     if(j&&j.ok){ if(window.fbq)fbq('track','Lead'); if(window.gtag)gtag('event','design_inquiry');
       b.innerHTML='<div class="done"><div class="ic">\\uD83C\\uDFA8</div><h2>Got it. Thank you!</h2><p>Your project just landed in my inbox. I\\'ll personally reply within one business day. Check your inbox for a confirmation. Talk soon!</p></div>';
       window.scrollTo({top:0,behavior:'smooth'});
     } else { btn.disabled=false; btn.textContent='Bring my vision to life \\u2192'; msg.textContent=(j&&j.error)||'Something went wrong, please try again.'; }
   }).catch(function(){ btn.disabled=false; btn.textContent='Bring my vision to life \\u2192'; msg.textContent='Network error, please try again.'; });
};
(function(){
  var pr=document.getElementById('prog');
  window.addEventListener('scroll',function(){var h=document.documentElement,m=h.scrollHeight-h.clientHeight;pr.style.width=(m>0?h.scrollTop/m*100:0)+'%';},{passive:true});
  var els=[].slice.call(document.querySelectorAll('.rv'));
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}});},{threshold:.12});
    els.forEach(function(el){io.observe(el);});
  } else els.forEach(function(el){el.classList.add('in');});
  // Showcase: tabbed concept sites that auto-rotate and auto-scroll inside the browser frame
  var vp=document.getElementById('sc_vp'); if(!vp)return;
  var sites=[].slice.call(vp.querySelectorAll('.site')), tabs=[].slice.call(document.querySelectorAll('.sc-tab')), url=document.getElementById('sc_url'), cur=0, auto=true;
  sites.sort(function(a,b){return (+a.getAttribute('data-order')||0)-(+b.getAttribute('data-order')||0);});
  function fit(){
    var w=vp.clientWidth, W=Math.max(760,w), s=w/W;
    sites.forEach(function(st){var sz=st.firstElementChild, sc=sz.firstElementChild;
      sz.style.width=W+'px'; sz.style.transform='scale('+s+')';
      sc.style.setProperty('--d',Math.max(0,sc.scrollHeight-vp.clientHeight/s)+'px');});
  }
  function show(i){
    cur=i;
    sites.forEach(function(st,k){st.classList.toggle('on',k===i);});
    tabs.forEach(function(t,k){t.classList.remove('auto');t.classList.toggle('on',k===i);});
    if(auto){void tabs[i].offsetWidth; tabs[i].classList.add('auto');}
    url.textContent=sites[i].getAttribute('data-url');
    var sc=sites[i].firstElementChild.firstElementChild; sc.style.animation='none'; void sc.offsetWidth; sc.style.animation='';
  }
  tabs.forEach(function(t,k){t.onclick=function(){auto=false;show(k);};});
  setInterval(function(){if(auto&&!vp.matches(':hover'))show((cur+1)%sites.length);},12000);
  window.addEventListener('resize',fit); fit();
  if(document.fonts&&document.fonts.ready)document.fonts.ready.then(fit);
})();
</script>
</body></html>"""


PROJECTS_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Design inquiries</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:720px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.proj{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 8px 22px rgba(35,49,40,.06)}
.ph{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.ph b{font-size:16px;color:#234F3D}
.tp{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;background:rgba(35,79,61,.1);color:#234F3D;padding:2px 9px;border-radius:100px}
.dt{margin-left:auto;font-size:12px;color:#8a918b}
.pmeta{font-size:13.5px;color:#5c635e;margin:6px 0 10px}.pmeta a{color:#a97f2a;font-weight:600;text-decoration:none}
.pl{font-size:14px;margin-top:6px;line-height:1.55}.pl .k{font-weight:700;color:#234F3D}
.empty{color:#5c635e;padding:20px 0}
a{color:#a97f2a}
</style></head><body><div class="wrap">
<div class="top"><h1>Design inquiries</h1><a class="back" href="/">&larr; Site</a></div>
__ROWS__
</div></body></html>"""


# ---------------------------------------------------------------------------
# AI Virtual Employee setup intake
# Stored like the other lead files (JSON in DATA, which is /var/data on Render
# when that disk is mounted). Owner mail reuses SMTP_* + LEAD_EMAIL.
# ---------------------------------------------------------------------------
AI_EMPLOYEES_PATH = os.path.join(DATA, "ai_employees.json")
AI_INDUSTRIES = [
    "Real estate",
    "Home services",
    "Professional services",
    "Retail",
    "Health and wellness",
    "Restaurant and hospitality",
    "Online business",
    "Other",
]
AI_TEAM_SIZES = ["Just me", "2-5", "6-15", "16-50", "51+"]
AI_AUTOMATIONS = [
    ("content", "Content creation"),
    ("followups", "Follow-ups"),
    ("research", "Lead research"),
    ("inbox", "Inbox and email help"),
    ("scheduling", "Scheduling"),
    ("inquiries", "Customer inquiries"),
    ("admin", "Admin tasks"),
    ("other", "Other"),
]
AI_AUTO_KEYS = {key for key, _label in AI_AUTOMATIONS}
AI_AUTO_LABELS = dict(AI_AUTOMATIONS)
AI_PACKAGES = ["Starter", "Growth", "Business", "Not sure"]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[+()0-9][0-9\s().+-]{6,}$")


def valid_phone(value):
    """Common US formats, plus international numbers that start with +."""
    raw = value or ""
    if not raw or len(raw) > 40 or not _PHONE_RE.match(raw):
        return False
    digits = re.sub(r"[^0-9]", "", raw)
    if raw.startswith("+"):
        return 8 <= len(digits) <= 15 and digits[0] != "0"
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False
    return digits[0] not in "01" and digits[3] not in "01"


def _ai_csrf_ok(token):
    expected = session.get("ai_csrf") or ""
    got = "" if token is None else str(token)
    if not expected or len(expected) != len(got):
        return False
    return hmac.compare_digest(expected, got)


def validate_ai_employee(data):
    """Return (record, errors). errors is empty only when the record is safe to store.

    The same rules are enforced in the browser on /ai-employee/start.
    """
    if not isinstance(data, dict):
        return None, {"form": "Please check the form and try again."}
    errors = {}

    def line(key):
        return _clean_line(data.get(key, ""))

    def note(key):
        return _clean_note(data.get(key, ""))

    name = line("name")
    email = line("email")
    phone = line("phone")
    business = line("business")
    industry = line("industry")
    industry_other = line("industry_other")
    team = line("team")
    daily = note("daily_task")
    teammate = note("teammate")
    tools = note("tools")
    package = line("package")
    automation_other = note("automation_other")

    if len(name) > 120:
        errors["name"] = "Please keep your name to 120 characters or fewer."
    elif len(name) < 2:
        errors["name"] = "Please enter your full name."
    if len(email) > 160 or not _EMAIL_RE.match(email or ""):
        errors["email"] = "Please enter a valid email."
    if len(phone) > 40:
        errors["phone"] = "Please keep your phone number to 40 characters or fewer."
    elif not valid_phone(phone):
        errors["phone"] = "Please enter a valid phone number."
    if len(business) > 160:
        errors["business"] = "Please keep the business name to 160 characters or fewer."
    elif len(business) < 2:
        errors["business"] = "Please enter your business name."
    if industry not in AI_INDUSTRIES:
        errors["industry"] = "Please choose an industry."
        industry_other = ""
    elif industry == "Other":
        if len(industry_other) > 120:
            errors["industry_other"] = "Please keep your industry to 120 characters or fewer."
        elif len(industry_other) < 2:
            errors["industry_other"] = "Please tell us your industry."
    else:
        industry_other = ""
    if team not in AI_TEAM_SIZES:
        errors["team"] = "Please choose a team size."
    if len(daily) > 1000:
        errors["daily_task"] = "Please keep that task to 1000 characters or fewer."
    elif len(daily) < 2:
        errors["daily_task"] = "Please share one task you wish you could take off your plate."
    if len(teammate) > 2000:
        errors["teammate"] = "Please keep the description to 2000 characters or fewer."
    elif len(teammate) < 2:
        errors["teammate"] = "Please describe the AI teammate you want."

    raw_autos = data.get("automations", [])
    if raw_autos in (None, ""):
        raw_autos = []
    autos = []
    if not isinstance(raw_autos, list) or len(raw_autos) > len(AI_AUTOMATIONS):
        errors["automations"] = "Please choose an automation from the list."
    else:
        for item in raw_autos:
            key = _clean_line(item)[:40]
            if not key:
                continue
            if key not in AI_AUTO_KEYS:
                errors["automations"] = "Please choose an automation from the list."
                break
            if key not in autos:
                autos.append(key)
    if "other" in autos:
        if len(automation_other) > 300:
            errors["automation_other"] = "Please keep the other automation to 300 characters or fewer."
        elif len(automation_other) < 2:
            errors["automation_other"] = "Please describe the other automation."
    else:
        automation_other = ""
    if len(tools) > 400:
        errors["tools"] = "Please keep tools to 400 characters or fewer."
    if package and package not in AI_PACKAGES:
        errors["package"] = "Please choose a package, or Not sure."
    if errors:
        return None, errors
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "business": business,
        "industry": industry,
        "industry_other": industry_other,
        "team": team,
        "daily_task": daily,
        "teammate": teammate,
        "automations": autos,
        "automation_other": automation_other,
        "tools": tools,
        "package": package,
    }, {}


def load_ai_employees():
    try:
        with open(AI_EMPLOYEES_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_ai_employees(rows):
    try:
        tmp = AI_EMPLOYEES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, AI_EMPLOYEES_PATH)
        return True
    except Exception:
        app.logger.exception("Could not save AI employee intake")
        return False


def ai_employee_book_path(rec):
    from urllib.parse import urlencode
    return "/book?" + urlencode({
        "service": "ai-employee",
        "type": "agent",
        "name": rec.get("name", ""),
        "email": rec.get("email", ""),
        "phone": rec.get("phone", ""),
    })


def send_ai_employee_email(rec):
    """Email Jeff only. Reuses the booking SMTP settings.

    Env: SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS,
    optional SMTP_FROM and SMTP_SSL. Recipient is LEAD_EMAIL when set,
    otherwise Macrandleacres@gmail.com (ORG_EMAIL). Never emails the lead.
    Failure is logged and swallowed so a saved intake still counts.
    """
    try:
        from email.message import EmailMessage
        sender = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
        if not sender:
            app.logger.warning(
                "AI employee intake saved but owner email was not sent (SMTP sender unset)")
            return False

        def esc_text(value):
            text = value if value else "-"
            return _esc(text, quote=False)

        labels = [esc_text(AI_AUTO_LABELS.get(key, key)) for key in (rec.get("automations") or [])]
        if rec.get("automation_other"):
            labels.append(esc_text(rec.get("automation_other")))
        industry = rec.get("industry") or "-"
        if rec.get("industry") == "Other" and rec.get("industry_other"):
            industry = "Other: " + rec.get("industry_other")
        book = SITE + ai_employee_book_path(rec)
        lines = [
            "New AI Virtual Employee setup request.",
            "",
            "Name: %s" % esc_text(rec.get("name")),
            "Email: %s" % esc_text(rec.get("email")),
            "Phone: %s" % esc_text(rec.get("phone")),
            "Business: %s" % esc_text(rec.get("business")),
            "Industry: %s" % esc_text(industry),
            "Team size: %s" % esc_text(rec.get("team")),
            "Package: %s" % esc_text(rec.get("package")),
            "",
            "Task they want off their plate:",
            esc_text(rec.get("daily_task")),
            "",
            "AI teammate they want:",
            esc_text(rec.get("teammate")),
            "",
            "Automations: %s" % (", ".join(labels) or "-"),
            "Tools: %s" % esc_text(rec.get("tools") or "-"),
            "",
            "Book their setup consultation:",
            book,
            "",
            "All intakes: %s/admin/ai-employees" % SITE,
        ]
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ORG_EMAIL
        reply = rec.get("email") or ""
        if _EMAIL_RE.match(reply):
            msg["Reply-To"] = reply
        msg["Subject"] = "New AI Virtual Employee intake: %s" % esc_text(
            _clean_line(rec.get("name", ""))[:80])
        msg.set_content("\n".join(lines))
        if _smtp_send(msg):
            return True
        app.logger.warning(
            "AI employee intake saved but owner email was not sent (SMTP unset or send failed)")
        return False
    except Exception:
        app.logger.exception("AI employee intake saved but owner notification failed")
        return False


def _notify_ai_employee(rec):
    try:
        send_ai_employee_email(rec)
    except Exception:
        app.logger.exception("AI employee intake saved but owner notification failed")


def _select_options(items):
    parts = ['<option value="">Choose one</option>']
    for item in items:
        parts.append("<option>%s</option>" % _esc(item))
    return "".join(parts)


def _auto_checks():
    parts = []
    for key, label in AI_AUTOMATIONS:
        parts.append(
            '<label class="check"><input type="checkbox" name="auto" value="%s"> %s</label>'
            % (_esc(key), _esc(label)))
    return "".join(parts)


def _package_radios():
    parts = []
    for name in AI_PACKAGES:
        parts.append(
            '<label class="check"><input type="radio" name="package" value="%s"> %s</label>'
            % (_esc(name), _esc(name)))
    return "".join(parts)


def _ai_employee_page():
    with open(os.path.join(BASE, "ai_employee_page.html"), encoding="utf-8") as f:
        html = f.read()
    head = "" if session.get("admin") else tracking_head()
    return (html.replace("__TRACK__", head)
            .replace("__CSRF__", _esc(session.get("ai_csrf", "")))
            .replace("__INDUSTRY_OPTIONS__", _select_options(AI_INDUSTRIES))
            .replace("__TEAM_OPTIONS__", _select_options(AI_TEAM_SIZES))
            .replace("__AUTO_CHECKS__", _auto_checks())
            .replace("__PACKAGE_RADIOS__", _package_radios()))


@app.route("/ai-employee/start")
def ai_employee_start():
    if not session.get("ai_csrf"):
        session["ai_csrf"] = secrets.token_urlsafe(32)
    resp = Response(_ai_employee_page(), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/ai-employee", methods=["POST"])
def api_ai_employee():
    if not rate_ok("aiemp", client_ip(), 8, 900):
        return jsonify(ok=False, error="Too many submissions, please try again shortly."), 429
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    if _clean_line(data.get("website") or ""):
        return jsonify(ok=True)
    if not _ai_csrf_ok(data.get("csrf")):
        return jsonify(ok=False,
                       error="Your session expired. Please refresh the page and try again.",
                       fields={"csrf": "Your session expired. Please refresh the page and try again."}), 400
    rec, errors = validate_ai_employee(data)
    if errors:
        return jsonify(ok=False, error="Please fix the highlighted fields.", fields=errors), 400
    rec["t"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    with _leads_lock:
        rows = load_ai_employees()
        rows.append(rec)
        if len(rows) > 2000:
            rows = rows[-2000:]
        if not save_ai_employees(rows):
            return jsonify(ok=False,
                           error="We could not save that just now. Your answers are still here. Please try again."), 500
    threading.Thread(target=_notify_ai_employee, args=(rec,), daemon=True).start()
    return jsonify(ok=True, book=ai_employee_book_path(rec))


@app.route("/admin/ai-employees")
def admin_ai_employees():
    if not session.get("admin"):
        return redirect("/admin/login?next=/admin/ai-employees")
    rows_html = ""
    for rec in sorted(load_ai_employees(), key=lambda r: r.get("t", ""), reverse=True):
        when = _esc((rec.get("t") or "").replace("T", " ")[:16])
        autos = ", ".join(AI_AUTO_LABELS.get(k, k) for k in (rec.get("automations") or []))
        if rec.get("automation_other"):
            autos = (autos + ", " if autos else "") + rec.get("automation_other")
        industry = rec.get("industry") or ""
        if industry == "Other" and rec.get("industry_other"):
            industry = "Other: " + rec.get("industry_other")
        book = ai_employee_book_path(rec)
        rows_html += (
            "<div class='proj'><div class='ph'><b>%s</b> <span class='tp'>%s</span>"
            "<span class='dt'>%s</span></div>"
            "<div class='pmeta'><a href='mailto:%s'>%s</a> &middot; %s &middot; %s</div>"
            "<div class='pl'><span class='k'>Business:</span> %s &middot; Team: %s</div>"
            "<div class='pl'><span class='k'>Daily task:</span> %s</div>"
            "<div class='pl'><span class='k'>Teammate:</span> %s</div>"
            "<div class='pl'><span class='k'>Automations:</span> %s</div>"
            "<div class='pl'><span class='k'>Tools:</span> %s</div>"
            "<div class='pl'><a href='%s'>Book their consultation</a></div></div>"
        ) % (
            _esc(rec.get("name", "")), _esc(rec.get("package", "") or "-"), when,
            _esc(rec.get("email", "")), _esc(rec.get("email", "")),
            _esc(rec.get("phone", "") or "-"), _esc(industry or "-"),
            _esc(rec.get("business", "") or "-"), _esc(rec.get("team", "") or "-"),
            _esc(rec.get("daily_task", "") or "-").replace("\n", "<br>"),
            _esc(rec.get("teammate", "") or "-").replace("\n", "<br>"),
            _esc(autos or "-"),
            _esc(rec.get("tools", "") or "-").replace("\n", "<br>"),
            _esc(book),
        )
    if not rows_html:
        rows_html = "<p class='empty'>No AI employee intakes yet.</p>"
    return Response(AI_EMPLOYEES_HTML.replace("__ROWS__", rows_html), mimetype="text/html")


AI_EMPLOYEES_HTML = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>AI employee intakes</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;padding:26px 16px}
.wrap{max-width:720px;margin:0 auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
h1{font-size:23px;color:#234F3D}a.back{font-size:13px;color:#5c635e;text-decoration:none}
.proj{background:#fff;border:1px solid rgba(35,79,61,.12);border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 8px 22px rgba(35,49,40,.06)}
.ph{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.ph b{font-size:16px;color:#234F3D}
.tp{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;background:rgba(35,79,61,.1);color:#234F3D;padding:2px 9px;border-radius:100px}
.dt{margin-left:auto;font-size:12px;color:#8a918b}
.pmeta{font-size:13.5px;color:#5c635e;margin:6px 0 10px}.pmeta a{color:#a97f2a;font-weight:600;text-decoration:none}
.pl{font-size:14px;margin-top:6px;line-height:1.55}.pl .k{font-weight:700;color:#234F3D}
.empty{color:#5c635e;padding:20px 0}
a{color:#a97f2a}
</style></head><body><div class="wrap">
<div class="top"><h1>AI employee intakes</h1><a class="back" href="/">&larr; Site</a></div>
__ROWS__
</div></body></html>"""


# Background reminder scheduler (single gunicorn worker -> one thread, no dupes).
# Set RUN_REMINDERS=0 to disable (used by tests).
if os.getenv("RUN_REMINDERS", "1") != "0":
    threading.Thread(target=_reminder_loop, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
