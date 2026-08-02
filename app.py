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
# Data lives in DATA_DIR when a persistent disk is mounted (survives redeploys);
# falls back to the app folder otherwise.
DATA = (os.getenv("DATA_DIR") or "").strip() or BASE
try:
    os.makedirs(DATA, exist_ok=True)
except Exception:
    DATA = BASE
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
    pw = os.getenv("SMTP_PASS", "").strip()
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


@app.route("/api/slots")
def api_slots():
    return jsonify(ok=True, tz=BOOK_TZ_LABEL, days=gen_slots())


def make_ics(bk):
    start = datetime.datetime.fromisoformat(bk["slot"])
    end = start + datetime.timedelta(minutes=BOOK_SLOT_MIN)
    def z(dt):
        return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MacRandle Acres//Booking//EN", "METHOD:REQUEST",
        "BEGIN:VEVENT", "UID:%s@macrandleacres.com" % bk.get("slot", ""),
        "DTSTAMP:" + z(datetime.datetime.now(datetime.timezone.utc)),
        "DTSTART:" + z(start), "DTEND:" + z(end),
        "SUMMARY:Growth Audit Call - MacRandle Acres",
        "DESCRIPTION:Growth Audit call with Jeff Randle (MacRandle Acres).",
        "ORGANIZER;CN=Jeff Randle:mailto:" + ORG_EMAIL,
        "ATTENDEE;CN=%s;RSVP=TRUE:mailto:%s" % (bk.get("name", ""), bk.get("email", "")),
        "END:VEVENT", "END:VCALENDAR"])


def google_cal_link(bk):
    from urllib.parse import quote
    start = datetime.datetime.fromisoformat(bk["slot"])
    end = start + datetime.timedelta(minutes=BOOK_SLOT_MIN)
    def z(dt):
        return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    details = "Growth Audit call with Jeff Randle (MacRandle Acres). Guest: %s <%s>" % (
        bk.get("name", ""), bk.get("email", ""))
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            "&text=" + quote("Growth Audit Call - MacRandle Acres")
            + "&dates=" + z(start) + "/" + z(end)
            + "&details=" + quote(details))


def send_booking_emails(bk):
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_PASS", "").strip()
    if not (host and user and pw):
        return
    try:
        import smtplib
        from email.message import EmailMessage
        when = _fmt_when(datetime.datetime.fromisoformat(bk["slot"]))
        ics = make_ics(bk).encode("utf-8")
        for to, mine in ((ORG_EMAIL, True), (bk["email"], False)):
            m = EmailMessage()
            m["From"] = user
            m["To"] = to
            if mine:
                m["Subject"] = "New call booked: %s (%s)" % (bk["name"], when)
                m.set_content("New Growth Audit call booked.\n\nName:  %s\nEmail: %s\nPhone: %s\nWhen:  %s\n"
                              % (bk["name"], bk["email"], bk.get("phone", ""), when))
            else:
                m["Subject"] = "Your Growth Audit call is booked"
                m.set_content("Hi %s,\n\nYour Growth Audit call with Jeff Randle is booked for %s.\n"
                              "The calendar invite is attached. Talk soon!\n\nMacRandle Acres"
                              % ((bk["name"].split(" ") or [""])[0], when))
            m.add_attachment(ics, maintype="text", subtype="calendar", filename="invite.ics")
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(m)
    except Exception:
        pass


@app.route("/api/book", methods=["POST"])
def api_book():
    if not rate_ok("book", client_ip(), 8, 600):
        return jsonify(ok=False, error="Too many attempts, please try again shortly."), 429
    data = request.get_json(silent=True) or {}
    if str(data.get("website", "")).strip():
        return jsonify(ok=True)
    slot = str(data.get("slot", "")).strip()
    name = str(data.get("name", "")).strip()[:120]
    email = str(data.get("email", "")).strip()[:160]
    if not name or "@" not in email or "." not in email:
        return jsonify(ok=False, error="Please add your name and a valid email."), 400
    if not any(slot == s["iso"] for day in gen_slots() for s in day["slots"]):
        return jsonify(ok=False, error="That time isn't available, please pick another."), 409
    bk = {"t": datetime.datetime.utcnow().isoformat(timespec="seconds"), "slot": slot,
          "name": name, "email": email, "phone": str(data.get("phone", "")).strip()[:40]}
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
                   gcal=google_cal_link(bk), ics=make_ics(bk))


@app.route("/book")
def book_page():
    resp = Response(BOOK_HTML, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
        gl = google_cal_link(b) if b.get("slot") else "#"
        rows += ("<tr class='%s'><td>%s <span class='tg'>%s</span></td><td>%s</td>"
                 "<td><a href='mailto:%s'>%s</a></td><td>%s</td>"
                 "<td><a class='addcal' href='%s' target='_blank' rel='noopener'>Add &#8599;</a></td></tr>") % (
                 tag, _esc(when), tag, _esc(b.get("name", "")), _esc(b.get("email", "")),
                 _esc(b.get("email", "")), _esc(b.get("phone", "")), _esc(gl))
    if not rows:
        rows = "<tr><td colspan=5 style='opacity:.5'>No calls booked yet.</td></tr>"
    return Response(BOOKINGS_HTML.replace("__ROWS__", rows), mimetype="text/html")


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
          body.innerHTML='<div class="done"><div class="ic">🌱</div><h2>You\\'re booked!</h2><p>'+esc(j.when)+'. Add it to your calendar so you don\\'t miss it:</p>'+
            '<div class="cal-btns">'+
            '<a class="cbtn g" href="'+esc(j.gcal||'#')+'" target="_blank" rel="noopener">📅 Add to Google Calendar</a>'+
            '<a class="cbtn i" href="'+icsHref+'" download="growth-audit.ics">⬇ Apple / Outlook (.ics)</a>'+
            '</div><p class="cn">Talk soon!</p></div>';
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
tr.past{opacity:.5}.tg{font-size:10px;font-weight:700;text-transform:uppercase;padding:2px 7px;border-radius:100px;margin-left:6px}
tr.upcoming .tg{background:rgba(35,79,61,.1);color:#234F3D}tr.past .tg{background:rgba(0,0,0,.06);color:#888}
</style></head><body><div class="wrap">
<div class="top"><h1>Booked calls</h1><a class="back" href="/">&larr; Site</a></div>
<table><thead><tr><th>When</th><th>Name</th><th>Email</th><th>Phone</th><th>Calendar</th></tr></thead><tbody>__ROWS__</tbody></table>
</div></body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
