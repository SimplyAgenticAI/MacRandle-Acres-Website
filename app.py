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
PAGES = {
    "main": {"html": "index.html", "content": "content.json"},
    "hub":  {"html": "hub.html",   "content": "hub_content.json"},
}
BLOCKED_FILES = {"app.py", "requirements.txt", ".gitignore", "render.yaml", "README.md",
                 "index.html", "content.json", "hub_content.json", "scorecards.json"}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()


# ---------- content store ----------
def _content_path(page):
    return os.path.join(BASE, PAGES[page]["content"])


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
def render_page(page):
    cfg = PAGES[page]
    with open(os.path.join(BASE, cfg["html"]), encoding="utf-8") as f:
        html = f.read()
    is_admin = bool(session.get("admin"))
    payload = json.dumps(load_content(page), ensure_ascii=False).replace("</", "<\\/")
    inject = ("<script>window.__ADMIN__=%s;window.__CONTENT__=%s;window.__PAGE__=%s;</script>"
              % ("true" if is_admin else "false", payload, json.dumps(page)))
    html = html.replace("</head>", inject + "\n</head>", 1)
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

SCORECARDS_PATH = os.path.join(BASE, "scorecards.json")
SCORE_METRICS = [
    ("reached", "People reached", "\U0001F440"),
    ("clicks", "Link clicks", "\U0001F446"),
    ("calls", "Calls from ads", "\U0001F4DE"),
    ("conversations", "Conversations started", "\U0001F4AC"),
    ("seeds", "Seeds planted", "\U0001F331"),
    ("appointments", "Appointments booked", "\U0001F4C5"),
    ("followers", "New followers", "\U0001F33F"),
]
CAL_URL = "https://calendar.google.com/calendar/u/0/r/week/2026/7/26?pli=1"


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
    for key, label, icon in SCORE_METRICS:
        fields += ('<label class="fld"><span>%s %s</span>'
                   '<input type="number" min="0" name="%s" value="0"></label>') % (icon, _esc(label), key)
    html = (BUILDER_HTML.replace("__ROWS__", rows).replace("__FIELDS__", fields)
            .replace("__METRIC_KEYS__", json.dumps([k for k, _, _ in SCORE_METRICS])))
    return Response(html, mimetype="text/html")


@app.route("/api/scorecard", methods=["POST"])
def api_scorecard():
    if not session.get("admin"):
        return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    metrics = {}
    src = data.get("metrics") or {}
    for key, _, _ in SCORE_METRICS:
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
    for key, label, icon in SCORE_METRICS:
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
