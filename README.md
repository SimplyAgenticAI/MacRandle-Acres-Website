# MacRandle Acres — Fractional Growth Advisor for Real Estate Teams

A hand-built site positioning Jeff Randle / MacRandle Acres as a **Monthly
Revenue Growth Advisor** for real estate teams. Botanical, premium brand system
matching the logo: warm-white background, forest-green primary, charcoal text,
antique-gold accents, plum used sparingly. Fraunces serif + Inter. Tagline
"Cultivating better businesses" and the four values (Growth · Foundation ·
Freedom · Trust).

Palette: green `#234F3D` · charcoal `#2D2D2D` · warm white `#F8F7F3` ·
gold `#c79a3b` · plum `#5B2C6F`.

## Two views: admin (editable) vs visitor (read-only)
The site runs as a small **Flask app** (`app.py`).

- **Visitors** see the public page, read-only, with the latest saved content.
- **Admin** signs in via the **🔒 Admin · Edit site** link in the footer (or
  go straight to **`/admin/login`**, or press **Ctrl/⌘+Shift+E**). After signing
  in, a floating **"✎ Edit page"** bar appears — click it and every headline,
  paragraph, card, price, and button becomes click-to-type editable. Click
  **Save changes** and the edits are written to `content.json` and go live for
  everyone. *Cancel* reverts, *Log out* leaves admin mode.

**Local preview (no server needed):** open `index.html` directly, or on a
static host, click the same **Admin** link — you'll enter a *local preview*
edit mode. Changes save to your browser and don't affect the live site until you
click **⬇ Export** to download an updated `content.json` and commit it. This
lets you try editing before deploying.

Edits are stored as content overrides in `content.json`, keyed by the
`data-edit="..."` attributes in `index.html`.

## Run locally
```
pip install -r requirements.txt
python app.py            # http://localhost:5000
```
To try editing locally, set a password first:
`ADMIN_PASSWORD=test python app.py` (PowerShell: `$env:ADMIN_PASSWORD='test'; python app.py`).

## Deploy on Render (Web Service)
1. Render → **New +** → **Blueprint** → pick this repo → **Apply**
   (reads `render.yaml`). Or **New + → Web Service** with
   Build `pip install -r requirements.txt`, Start `gunicorn app:app`.
2. In the service's **Environment** tab, set:
   - `ADMIN_PASSWORD` — your edit-mode password (keep it private).
   - `SECRET_KEY` — the Blueprint auto-generates this; otherwise add any long
     random string.
3. Deploy. Your site is the Render URL; sign in to edit at `<url>/admin/login`.

### ⚠️ Note on saved edits + the free tier
`content.json` is written to the server's disk. On Render's **free** tier the
disk resets on every redeploy/restart, so edits made through the UI can be lost
when the service restarts. To make edits durable, either:
- commit the updated `content.json` to the repo periodically, **or**
- add a small Render **persistent disk** (paid) mounted where `content.json`
  lives, **or** move the store to a database.
Tell me which you'd prefer and I'll wire it up.

## Fill in the 3 links
Search `index.html` for `[[LINK]]` and replace `-schedule` (booking),
`-email` (mailto), `-phone` (tel).

## Logo & animation
Logo ships as `logo.jpg` (hero showcase + favicon + social image). Animated
touches: pulsing moonlight glow + float on the logo, headline gold shimmer,
drifting-leaf canvas, Growth Scorecard count-up on scroll, glowing nav mark,
hover micro-interactions — all respect `prefers-reduced-motion`.
