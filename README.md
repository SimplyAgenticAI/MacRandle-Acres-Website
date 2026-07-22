# MacRandle Acres — Fractional Growth Advisor for Real Estate Teams

A hand-built, single-page site positioning Jeff Randle / MacRandle Acres as a
**Monthly Revenue Growth Advisor** for real estate teams — outcome-focused, not
service-focused. Botanical, premium brand system matching the MacRandle Acres
logo: warm-white background, forest-green primary, charcoal text, antique-gold
accents, plum used sparingly; Fraunces serif display + Inter body; tagline
"Cultivating better businesses" and the four values (Growth · Foundation ·
Freedom · Trust). No frameworks, no build step — one self-contained `index.html`.

Palette: green `#234F3D` · charcoal `#2D2D2D` · warm white `#F8F7F3` ·
gold `#c79a3b` · plum `#5B2C6F`.

## Logo & animation
The MacRandle Acres logo ships as **`logo.jpg`** — shown in the hero (with a
pulsing moonlight glow + gentle float) and used as the favicon / social image.
Animated touches: headline gold shimmer, drifting-leaf ambience canvas, the
Growth Scorecard numbers counting up on scroll, a glowing nav mark, and hover
micro-interactions. All motion respects `prefers-reduced-motion`.

Positioning: sell **measurable growth** (clarity, growth, confidence, time,
profit), not tasks. Signature deliverable is the monthly **Growth Scorecard**.
Three plans: Growth Audit ($997–1,250), Growth Partner ($1,997–2,500),
Rainmaker Elite ($3,500–5,000).

## Preview locally
Open `index.html` in a browser. (Or `python app.py` → http://localhost:5000).

## Deploy on Render — two options

**A) Static Site (recommended — free & fast)**
1. Push this folder to the GitHub repo.
2. Render → **New +** → **Static Site** → pick the repo.
3. Build Command: *(blank)*  ·  Publish Directory: `.`  → Deploy.
   (Or **Blueprint** → it reads `render.yaml` automatically.)

**B) Web Service (same pattern as SimplyAgenticAI)**
1. Render → **New +** → **Web Service** → pick the repo.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app`

## Before you go live — fill in the links
Only three placeholders remain. Search `index.html` for `[[LINK]]` and replace:

| Placeholder | Points to |
|---|---|
| `[[LINK]]-schedule` | Your Calendly / Growth Audit booking link |
| `[[LINK]]-email` | Business email (used as `mailto:`) |
| `[[LINK]]-phone` | Phone number (used as `tel:`) |
