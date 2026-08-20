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

from flask import (Flask, request, session, redirect, jsonify,
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
                 "leads.json", "bookings.json"}

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
    return Response("User-agent: *\nAllow: /\nDisallow: /admin\nSitemap: %s/sitemap.xml\n" % SITE,
                    mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = "".join("<url><loc>%s%s</loc></url>" % (SITE, u) for u in ("/", "/hub", "/privacy"))
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
    return render_page("main")


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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
    <div class="foot">Cultivating better businesses, MacRandle Acres</div>
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
_TRACK_EXACT = {"/", "/hub"}
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
<meta name=viewport content="width=device-width,initial-scale=1"><title>Privacy Policy, MacRandle Acres</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel=stylesheet>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Inter',system-ui,sans-serif;background:#F8F7F3;color:#2D2D2D;line-height:1.7;padding:40px 18px}
.wrap{max-width:680px;margin:0 auto}h1{color:#234F3D;font-size:28px;margin-bottom:6px}.sub{color:#8a8f88;font-size:13px;margin-bottom:24px}
h2{color:#234F3D;font-size:17px;margin:22px 0 6px}p{margin-bottom:10px;font-size:15px}a{color:#a97f2a;font-weight:600}
.back{display:inline-block;margin-top:26px;font-size:14px}</style></head><body><div class="wrap">
<h1>Privacy Policy</h1><div class="sub">MacRandle Acres &middot; Jeff Randle</div>
<p>This site is operated by Jeff Randle (MacRandle Acres). We respect your privacy and keep things simple.</p>
<h2>What we collect</h2>
<p>If you submit the Growth Audit form, we collect the name, email, phone, and answers you provide. We also collect basic, cookieless visit counts (page, referring site, device type) with no personal data. If analytics or advertising pixels are enabled, those third parties (Google, Meta) may set their own cookies per their policies.</p>
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
BOOK_SLOT_MIN = int(os.getenv("BOOK_SLOT_MIN", "30"))
BOOK_DAYS_AHEAD = 14
BOOK_MIN_NOTICE_H = 3
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
    head = "" if session.get("admin") else tracking_head()
    html = BOOK_HTML.replace("</head>", head + "\n</head>", 1)
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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
var TZ='', DAYS=[], di=0, slot=null, body=document.getElementById('body');
fetch('/api/slots').then(function(r){return r.json();}).then(function(j){
  TZ=j.tz||''; DAYS=j.days||[];
  document.getElementById('sub').textContent='30 minutes with Jeff Randle. All times '+TZ+'.';
  render();
}).catch(function(){body.innerHTML='<div class="empty">Could not load times. Please email Macrandleacres@gmail.com.</div>';});
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function render(){
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
    '<label class="fld"><span>Your name *</span><input id="bn" autocomplete="name"></label>'+
    '<label class="fld"><span>Email *</span><input id="be" type="email" autocomplete="email"></label>'+
    '<label class="fld full"><span>Phone (optional)</span><input id="bp" autocomplete="tel"></label></div>'+
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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
<link rel="icon" type="image/jpeg" href="/logo.jpg">
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
  <div class="foot">MacRandle Acres &middot; Growth advisory for real estate teams</div>
</div>
</body></html>"""


# Background reminder scheduler (single gunicorn worker -> one thread, no dupes).
# Set RUN_REMINDERS=0 to disable (used by tests).
if os.getenv("RUN_REMINDERS", "1") != "0":
    threading.Thread(target=_reminder_loop, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
