# MacRandle Acres — Jeff Randle, the Lucid Mage

A hand-built, single-page landing site replacing the old Gamma page. Cosmic-navy
brand system with purple/indigo gradients and gold "mage" accents, animated
starfield + aurora background, and scroll-reveal motion. No frameworks, no build
step — one self-contained `index.html`.

## Preview locally
Just open `index.html` in a browser. (Or `python app.py` and visit
http://localhost:5000).

## Deploy on Render — two options

**A) Static Site (recommended — free & fast)**
1. Push this folder to a GitHub repo.
2. Render → **New +** → **Static Site** → pick the repo.
3. Build Command: *(leave blank)*  ·  Publish Directory: `.`
4. Deploy. (Or use **Blueprint** and it reads `render.yaml` automatically.)

**B) Web Service (same pattern as SimplyAgenticAI)**
1. Render → **New +** → **Web Service** → pick the repo.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app`

## Before you go live — fill in the links
Every button/link marked `[[LINK]]-xxx` in `index.html` is a placeholder.
Search the file for `[[LINK]]` and replace each with the real URL:

| Placeholder | What it should point to |
|---|---|
| `[[LINK]]-schedule` | Your Calendly / booking link |
| `[[LINK]]-email` | your business email (used as `mailto:`) |
| `[[LINK]]-phone` | your phone number (used as `tel:`) |
| `[[LINK]]-strategy` | Facebook Strategy offer page |
| `[[LINK]]-yuna` | Yuna Pro page |
| `[[LINK]]-flowchat` | FlowChat page |
| `[[LINK]]-saai` | Simply Agentic AI site |
| `[[LINK]]-art` | Acrylic pour art gallery/shop |
| `[[LINK]]-design` | Graphic design portfolio |
| `[[LINK]]-fbdream` | Lucid dreaming Facebook group |
| `[[LINK]]-patreon` | Patreon page |
| `[[LINK]]-skool` | Skool / Facebook Growth Lab |
| `[[LINK]]-fbcommunity` | Consciousness Explorers FB group |
| `[[LINK]]-youtube` | YouTube channel |
| `[[LINK]]-instagram` | Instagram profile |
| `[[LINK]]-tiktok` | TikTok profile |

LinkedIn is already wired to your public profile.
