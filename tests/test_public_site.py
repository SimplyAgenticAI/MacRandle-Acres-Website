"""Public-site cleanup: positioning, booking links, SEO, and admin exposure."""
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

os.environ["RUN_REMINDERS"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin"

import app as site


SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ORIGIN = "https://www.macrandleacres.com"
MARKETING_PAGES = ("/", "/design", "/book", "/hub", "/ai-employee/start", "/privacy", "/terms", "/starter-prompts")


def _meta_description(html):
    match = re.search(r'<meta name="description" content="([^"]+)"', html)
    return match.group(1) if match else ""


def _canonical(html):
    match = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    return match.group(1) if match else ""


def _booking_mismatches(html):
    bad = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.I | re.S):
        attrs, inner = match.group(1), re.sub(r"<[^>]+>", " ", match.group(2))
        text = re.sub(r"\s+", " ", inner).strip()
        lowered = text.lower()
        if "schedule a call" not in lowered and not re.search(r"\bbook\b", lowered):
            continue
        href = re.search(r'href="([^"]*)"', attrs)
        url = href.group(1) if href else ""
        if not url.startswith("/book"):
            bad.append((text, url))
    return bad


class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.client = site.app.test_client()

    def test_positioning_is_not_real_estate_only_outside_that_section(self):
        home = self.client.get("/").get_data(as_text=True)
        nav = home.split("<nav", 1)[1].split("</nav>", 1)[0]
        hero = home.split('id="top"', 1)[1].split('id="voice"', 1)[0]
        self.assertNotIn("real estate", nav.lower())
        self.assertNotIn("real estate", hero.lower())
        self.assertIn("Growth Partner", hero)
        self.assertNotIn("For growing businesses", hero)
        self.assertIn("Remove Friction. Accelerate Growth.", hero)

        bio = re.search(r'data-edit="jeff_p_v7">(.*?)</p>', home, re.S).group(1)
        footer = re.search(r'data-edit="foot_copy_v7">(.*?)</span>', home, re.S).group(1)
        self.assertIn("work with businesses", bio)
        self.assertNotIn("small business", bio.lower())
        self.assertNotIn("real estate", bio.lower())
        self.assertIn("Helping businesses remove friction from growth.", footer)
        self.assertNotIn("real estate", footer.lower())
        self.assertNotIn('data-edit="jeff_p"', home)
        self.assertNotIn('data-edit="foot_copy"', home)

        hub = self.client.get("/hub").get_data(as_text=True)
        hub_nav = hub.split("<nav", 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("real estate", hub_nav.lower())
        self.assertIn("Remove Friction. Accelerate Growth.", hub)
        self.assertIn('data-edit="foot_copy">Jeff Randle. All one place, on purpose.', hub)

    def test_old_content_keys_do_not_replace_the_new_defaults(self):
        """Stored jeff_p / foot_copy overrides no longer match an element, so the defaults show."""
        disk = {
            "jeff_p": "I work alongside real estate teams to close more deals.",
            "foot_copy": "Helping ambitious real estate teams remove friction from growth.",
        }
        with patch.object(site, "load_content", return_value=disk):
            home = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-edit="jeff_p_v7">I\'m the founder of MacRandle Acres', home)
        self.assertIn("Helping businesses remove friction from growth.", home)
        self.assertNotRegex(home, r'data-edit="jeff_p"[^_]')
        self.assertNotRegex(home, r'data-edit="foot_copy"[^_]')

    def test_retired_hub_ma_desc_is_left_out_of_page_and_api(self):
        """A saved hub ma_desc never reaches /hub or GET /api/content; ma_desc_lm does."""
        old = (
            "Your Fractional Growth Advisor, every month I find hidden opportunities "
            "to make your business more profitable, then build the systems to capture them."
        )
        live = "Saved ma_desc_lm still comes through from hub content."
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "hub_content.json"), "w", encoding="utf-8") as f:
            json.dump({"ma_desc": old, "ma_desc_lm": live}, f)
        with patch.object(site, "DATA", tmp.name):
            hub = self.client.get("/hub").get_data(as_text=True)
            api = self.client.get("/api/content?page=hub")
        self.assertEqual(api.status_code, 200)
        body = api.get_json()
        self.assertNotIn(old, hub)
        self.assertNotIn('"ma_desc"', hub)
        self.assertIn(live, hub)
        self.assertIn('data-edit="ma_desc_lm"', hub)
        self.assertNotIn("ma_desc", body["content"])
        self.assertNotIn(old, json.dumps(body))
        self.assertEqual(body["content"]["ma_desc_lm"], live)

    def test_booking_buttons_point_at_book(self):
        for path in MARKETING_PAGES:
            html = self.client.get(path).get_data(as_text=True)
            self.assertEqual(_booking_mismatches(html), [], path)
        self.assertNotIn("Schedule a Call", self.client.get("/").get_data(as_text=True))
        self.assertNotIn("Schedule a Call", self.client.get("/hub").get_data(as_text=True))

    def test_admin_comment_and_footer_link_are_gone(self):
        for path in ("/", "/hub"):
            html = self.client.get(path).get_data(as_text=True)
            footer = html.split("<footer>", 1)[1].split("</footer>", 1)[0]
            self.assertNotIn("/admin", footer, path)
            self.assertNotIn("adminLink", html, path)
            self.assertNotIn("Admin · Edit site", html, path)
            self.assertNotIn("ADMIN EDITING", html, path)
            self.assertIsNone(re.search(r"<!--[\s\S]*?/admin/login[\s\S]*?-->", html), path)
        login = self.client.get("/admin/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("password", login.get_data(as_text=True).lower())

    def test_canonicals_meta_and_favicon(self):
        expected = {
            "/": ORIGIN + "/",
            "/design": ORIGIN + "/design",
            "/book": ORIGIN + "/book",
            "/hub": ORIGIN + "/hub",
            "/ai-employee/start": ORIGIN + "/ai-employee/start",
            "/privacy": ORIGIN + "/privacy",
            "/terms": ORIGIN + "/terms",
            "/starter-prompts": ORIGIN + "/starter-prompts",
        }
        for path, canonical in expected.items():
            html = self.client.get(path).get_data(as_text=True)
            description = _meta_description(html)
            self.assertGreaterEqual(len(description), 50, path)
            self.assertLessEqual(len(description), 180, path)
            self.assertEqual(_canonical(html), canonical, path)
            self.assertNotIn("https://macrandleacres.com/", html)
            self.assertIn('href="/favicon.ico"', html)
        missing = self.client.get("/no-such-page-here")
        self.assertEqual(missing.status_code, 404)
        page = missing.get_data(as_text=True)
        self.assertIn("Page not found", page)
        self.assertIn("Remove Friction. Accelerate Growth.", page)
        self.assertIn("Lucid Marketing, by MacRandle Acres", page)
        self.assertGreaterEqual(len(_meta_description(page)), 40)
        self.assertIn('href="/favicon.ico"', page)
        icon = self.client.get("/favicon.ico")
        self.assertEqual(icon.status_code, 200)
        self.assertIn("icon", icon.content_type)
        self.assertGreater(len(icon.data), 100)

    def test_share_pages_have_descriptions_and_www_canonicals(self):
        cancel = self.client.get("/book/cancel").get_data(as_text=True)
        self.assertIn("Manage or cancel a booked call", _meta_description(cancel))
        self.assertEqual(_canonical(cancel), ORIGIN + "/book/cancel")

        room = self.client.get("/m/Room_1").get_data(as_text=True)
        self.assertIn("Join your booked video call", _meta_description(room))
        self.assertEqual(_canonical(room), ORIGIN + "/m/Room_1")

        missing_audit = self.client.get("/audit/missing")
        self.assertEqual(missing_audit.status_code, 404)
        audit_html = missing_audit.get_data(as_text=True)
        self.assertIn("not available", _meta_description(audit_html))
        self.assertIn('href="/favicon.ico"', audit_html)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        score_path = os.path.join(tmp.name, "scorecards.json")
        audit_path = os.path.join(tmp.name, "audits.json")
        with patch.object(site, "SCORECARDS_PATH", score_path), patch.object(site, "AUDITS_PATH", audit_path):
            self.client.post("/admin/login", data={"password": "test-admin", "next": "/"})
            created = self.client.post("/api/scorecard", json={
                "client": "Ada Lovelace", "month": "November 2026", "note": "", "metrics": {}})
            self.assertEqual(created.status_code, 200)
            sid = created.get_json()["id"]
            card = self.client.get("/s/" + sid).get_data(as_text=True)
            self.assertIn("Ada Lovelace", _meta_description(card))
            self.assertEqual(_canonical(card), ORIGIN + "/s/" + sid)
            self.assertIn('href="/book"', card)

            new_audit = self.client.post("/admin/audits/new", data={"client": "Ada Lovelace"})
            self.assertIn(new_audit.status_code, (301, 302))
            aid = new_audit.headers["Location"].rstrip("/").split("/")[-1]
            report = self.client.get("/audit/" + aid).get_data(as_text=True)
            self.assertIn("Ada Lovelace", _meta_description(report))
            self.assertEqual(_canonical(report), ORIGIN + "/audit/" + aid)

    def test_sitemap_is_valid_and_lists_public_pages_only(self):
        xml = self.client.get("/sitemap.xml")
        self.assertEqual(xml.status_code, 200)
        self.assertIn("xml", xml.content_type)
        root = ET.fromstring(xml.get_data())
        locs = [node.text for node in root.findall("sm:url/sm:loc", SITEMAP_NS)]
        self.assertEqual(locs, [
            ORIGIN + "/",
            ORIGIN + "/design",
            ORIGIN + "/hub",
            ORIGIN + "/starter-prompts",
            ORIGIN + "/book",
            ORIGIN + "/ai-employee/start",
            ORIGIN + "/thank-you",
            ORIGIN + "/privacy",
            ORIGIN + "/terms",
        ])
        blob = " ".join(locs)
        for blocked in ("/admin", "/book/cancel", "/audit/", "/s/", "/m/"):
            self.assertNotIn(blocked, blob)
        robots = self.client.get("/robots.txt").get_data(as_text=True)
        self.assertIn("Sitemap: %s/sitemap.xml" % ORIGIN, robots)
        self.assertIn("Disallow: /admin", robots)

    def test_llms_txt_describes_the_business(self):
        res = self.client.get("/llms.txt")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/plain", res.content_type)
        text = res.get_data(as_text=True)
        for phrase in (
            "Lucid Marketing, by MacRandle Acres",
            "Jeff Randle",
            "Marketing",
            "Web design",
            "App development",
            "AI Virtual Employee Setup",
            "Real estate",
            ORIGIN + "/book",
            "$149 one-time",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("\u2014", text)
        for opening in ("Most people", "Some people", "There are certain", "A lot of people"):
            self.assertFalse(any(line.startswith(opening) for line in text.splitlines()), opening)

    def test_home_faq_and_service_schema_match_visible_copy(self):
        home = self.client.get("/").get_data(as_text=True)
        blocks = [
            json.loads(raw)
            for raw in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>', home, re.S)
        ]
        service = next(b for b in blocks if b.get("@type") == "ProfessionalService")
        faq = next(b for b in blocks if b.get("@type") == "FAQPage")
        self.assertEqual(service["url"], ORIGIN)
        self.assertIn("helps businesses remove friction", service["description"])
        self.assertNotIn("small business", service["description"].lower())
        self.assertIn("Real estate is one specialty", service["description"])
        visible = re.findall(r"<summary[^>]*>(.*?)</summary>", home, re.S)
        visible = [re.sub(r"<[^>]+>", "", q).strip() for q in visible]
        schema_questions = [q["name"] for q in faq["mainEntity"]]
        self.assertEqual(schema_questions, visible)
        self.assertEqual(len(schema_questions), 5)
        joined = " ".join(q["acceptedAnswer"]["text"] for q in faq["mainEntity"])
        self.assertIn("$149 one-time", joined)
        self.assertIn(ORIGIN + "/book", joined)
        self.assertNotIn("\u2014", joined)

    def test_starter_prompts_page(self):
        html = self.client.get("/starter-prompts").get_data(as_text=True)
        self.assertEqual(self.client.get("/starter-prompts").status_code, 200)
        self.assertIn("<h1", html)
        self.assertIn("Free AI Starter Prompts", html)
        self.assertNotIn("Grok", html)
        self.assertIn("From Lucid Marketing, by MacRandle Acres.", html)
        self.assertIn("Remove Friction. Accelerate Growth.", html)
        self.assertIn(
            "Ten prompts I've used to grow a channel, "
            "write posts, follow up with clients, and fix a website.",
            html,
        )
        prompts = [
            "Look at my website the way a new visitor would.",
            "Write 5 posts for this week in my voice",
            "Find 5 recent posts or videos in my niche",
            "Look at who I work with and who has gone quiet.",
            "What are people searching for right now in my niche?",
            "Pick the best idea for my next video.",
            "Find my old videos that didn't get many views but people liked.",
            "Pick my best long video.",
            "Find 5 popular channels in my niche.",
            "Rewrite the titles, descriptions, and tags on my videos",
            "Look at all my routines and how often each one runs.",
        ]
        for prompt in prompts:
            self.assertIn(prompt, html)
        self.assertEqual(len(re.findall(r'data-copy-for="sp_\d+_text"', html)), 11)
        self.assertIn('data-edit="sp_01_text"', html)
        self.assertIn('data-edit="sp_10_text"', html)
        self.assertIn('data-edit="sp_11_text"', html)
        self.assertIn("Find My Bot Waste", html)
        self.assertIn("Don't change anything yet.", html)
        cheaper = html.split('id="cheaper"', 1)[1].split('id="sp-cta"', 1)[0]
        self.assertIn("Make Your Bots Cheaper to Run", cheaper)
        self.assertIn("trimming waste lets you do the same work for less.", cheaper)
        self.assertIn("Cut idle wake-ups", cheaper)
        self.assertIn("every 15 or 30 minutes just to see if there's work.", cheaper)
        self.assertIn("Stop repeating checks", cheaper)
        self.assertIn("Shrink big prompts and tool calls", cheaper)
        self.assertIn("No chatter", cheaper)
        self.assertIn("Only message when something needs you.", cheaper)
        self.assertIn("Estimate your savings", cheaper)
        self.assertIn("about 140 times a week", cheaper)
        self.assertIn("dropped that to 5 wake-ups a week", cheaper)
        self.assertLess(html.index('id="cheaper"'), html.index('id="sp-cta"'))
        self.assertLess(html.index("Refresh My Titles and Thumbnails"), html.index("Make Your Bots Cheaper to Run"))
        self.assertIn(">Copy</button>", html)
        cta = html.split('id="sp-cta"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Want help setting up your first AI agent?", cta)
        self.assertIn("Pay $149 and get started", cta)
        self.assertIn("Book a Lucid Marketing call", cta)
        self.assertIn('href="/book?type=agent"', cta)
        self.assertIn('href="/book?type=audit"', cta)
        self.assertNotIn("Grok", cta)
        self.assertNotIn("$497", cta)
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)
        self.assertIn('window.__PAGE__="prompts"', html)

        home = self.client.get("/").get_data(as_text=True)
        home_nav = home.split("<nav", 1)[1].split("</nav>", 1)[0]
        home_footer = home.split("<footer>", 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("/starter-prompts", home_nav)
        self.assertIn('href="/starter-prompts"', home_footer)
        self.assertIn("Free AI Starter Prompts", home_footer)

        hub = self.client.get("/hub").get_data(as_text=True)
        hub_nav = hub.split("<nav", 1)[1].split("</nav>", 1)[0]
        hub_footer = hub.split("<footer>", 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("/starter-prompts", hub_nav)
        self.assertIn('href="/starter-prompts"', hub)
        self.assertIn("Free AI Starter Prompts", hub)
        self.assertIn('href="/starter-prompts"', hub_footer)

        design = self.client.get("/design").get_data(as_text=True)
        self.assertIn('href="/starter-prompts"', design)

    def test_homepage_and_scorecards_only_claim_meta_metrics(self):
        """Removed call, conversation, nurture, and appointment metrics stay out.
        Retired keys in a saved scorecard are ignored instead of crashing.
        """
        home = self.client.get("/").get_data(as_text=True)
        impact = home.split('id="impact"', 1)[1].split("</section>", 1)[0]
        report = home.split('id="scorecard"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-edit="mz2_n">Post engagement', impact)
        self.assertIn('data-edit="mz2_s">Comments and shares, straight from Meta', impact)
        self.assertIn('data-edit="mz3_n">Impressions', impact)
        self.assertIn(
            'data-edit="mz3_s">Straight from Meta insights, plus Ads Manager when we run ads',
            impact,
        )
        self.assertIn('data-edit="mz4_n">New followers gained', impact)
        self.assertIn('data-edit="mz4_s">Straight from Meta', impact)
        self.assertIn('data-edit="mz1_n">Reach &amp; link clicks', impact)
        for gone in (
            "Calls from your ads",
            "Tracked call reporting",
            "Conversations &amp; appointments",
            "Logged in your CRM",
            "Warm leads nurtured, then booked",
            "Counted, never guessed",
        ):
            self.assertNotIn(gone, impact)
        for label, number, width in (
            ("People reached", "48200", "92%"),
            ("Impressions", "91300", "96%"),
            ("Link clicks", "1340", "70%"),
            ("Post engagement (comments and shares)", "860", "48%"),
            ("New followers gained", "112", "32%"),
        ):
            self.assertIn(label, report)
            self.assertIn('data-to="%s"' % number, report)
            self.assertIn("--w:%s" % width, report)
        self.assertEqual(report.count('class="rp-bar"'), 5)
        self.assertIn("More metrics, like booked appointments, get added as tracking grows.", report)
        for gone in (
            "Calls from ads",
            "Conversations started",
            "Warm leads nurtured",
            "Appointments booked",
            'data-edit="rl3"',
            'data-edit="rl4"',
            'data-edit="rl5"',
            'data-edit="rl6"',
        ):
            self.assertNotIn(gone, report)
        self.assertNotIn("\u2014", impact)
        self.assertNotIn("\u2014", report)
        self.assertIn('data-edit="hero_h1a">Remove Friction.', home)
        self.assertIn('data-edit="hero_h1b">Accelerate Growth.', home)
        self.assertIn("I'm not another vendor", home)
        self.assertIn("Talk to Alice, Jeff's AI assistant: (443) 323-1268", home)
        self.assertIn(".hero{position:relative;padding:40px 0 14px;text-align:center}", home)
        self.assertIn(".hero{padding:32px 0 10px}", home)
        self.assertIn("flex-wrap:wrap;margin-top:20px;", home)
        self.assertIn("border-bottom:1px solid var(--bd-soft);padding:12px 0}", home)
        self.assertIn("section{position:relative;padding:40px 0}", home)
        self.assertIn("section{padding:28px 0}", home)
        self.assertIn("#ai-employee{padding-top:8px}", home)
        # Saved admin overrides live in content.json and replace matching data-edit
        # keys for every visitor. These keys are unset, so the new defaults show.
        content = site.load_content("main")
        for key in ("mz1_n", "mz1_s", "mz2_n", "mz2_s", "mz3_n", "mz3_s", "mz4_n", "mz4_s",
                    "rl1", "rl2", "rl_impr", "rl_eng", "rl7", "score_more"):
            self.assertNotIn(key, content)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        score_path = os.path.join(tmp.name, "scorecards.json")
        with patch.object(site, "SCORECARDS_PATH", score_path):
            with open(score_path, "w", encoding="utf-8") as f:
                json.dump({
                    "old1": {
                        "client": "Old Client",
                        "month": "October 2026",
                        "note": "Keep going",
                        "metrics": {
                            "reached": 100,
                            "clicks": "20",
                            "calls": 62,
                            "conversations": {"nested": True},
                            "seeds": None,
                            "appointments": "nope",
                            "followers": 11,
                            "engagement": "n/a",
                        },
                        "created": "2026-10-01T00:00:00",
                    },
                    "badshape": {
                        "client": "Bad Shape",
                        "month": "September 2026",
                        "note": "",
                        "metrics": ["calls", 5],
                        "created": "2026-09-01T00:00:00",
                    },
                }, f)
            card = self.client.get("/s/old1")
            self.assertEqual(card.status_code, 200)
            html = card.get_data(as_text=True)
            self.assertIn("Old Client", html)
            for label in (
                "People reached",
                "Impressions",
                "Link clicks",
                "Post engagement (comments and shares)",
                "New followers gained",
            ):
                self.assertIn(label, html)
            for gone in ("Calls from ads", "Conversations started", "Seeds planted", "Appointments booked"):
                self.assertNotIn(gone, html)
            self.assertIn('data-to="100"', html)
            self.assertIn('data-to="20"', html)
            self.assertIn('data-to="11"', html)
            self.assertNotIn('data-to="62"', html)
            self.assertEqual(html.count('data-to="0"'), 2)
            shaped = self.client.get("/s/badshape")
            self.assertEqual(shaped.status_code, 200)
            self.assertEqual(shaped.get_data(as_text=True).count('data-to="0"'), 5)

            self.client.post("/admin/login", data={"password": "test-admin", "next": "/"})
            created = self.client.post("/api/scorecard", json={
                "client": "Ada Lovelace",
                "month": "November 2026",
                "note": "",
                "metrics": {
                    "reached": 10,
                    "impressions": 30,
                    "clicks": 4,
                    "engagement": 7,
                    "followers": 2,
                    "calls": 99,
                    "appointments": 88,
                },
            })
            self.assertEqual(created.status_code, 200)
            sid = created.get_json()["id"]
            with open(score_path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["old1"]["metrics"]["calls"], 62)
            self.assertEqual(saved[sid]["metrics"], {
                "reached": 10,
                "impressions": 30,
                "clicks": 4,
                "engagement": 7,
                "followers": 2,
            })
            page = self.client.get("/s/" + sid).get_data(as_text=True)
            self.assertIn('data-to="30"', page)
            self.assertNotIn("Calls from ads", page)
            admin = self.client.get("/admin/scorecards").get_data(as_text=True)
            for label in (
                "People reached",
                "Impressions",
                "Link clicks",
                "Post engagement (comments and shares)",
                "New followers gained",
            ):
                self.assertIn(label, admin)
            for gone in ("Calls from ads", "Conversations started", "Seeds planted", "Appointments booked", "Your CRM"):
                self.assertNotIn(gone, admin)
            self.assertIn("More metrics, like booked appointments, get added as tracking grows.", admin)
            self.assertNotIn("\u2014", admin.split('class="panel guide"', 1)[1].split("</div>", 1)[0])

    def test_pricing_tiers_show_strikethrough_anchor_and_this_week_price(self):
        home = self.client.get("/").get_data(as_text=True)
        en = "\u2013"
        expected = {
            "t1_price": ("$997" + en + "1,250", "$698" + en + "875"),
            "t2_price": ("$1,997" + en + "2,500", "$1,398" + en + "1,750"),
            "t3_price": ("$3,500" + en + "5,000", "$2,450" + en + "3,500"),
        }
        for key, (anchor, sale) in expected.items():
            match = re.search(r'data-edit="%s">(.*?)</div>' % key, home, re.S)
            self.assertIsNotNone(match, key)
            block = match.group(1)
            struck = re.search(r"<s>(.*?)</s>", block, re.S)
            self.assertIsNotNone(struck, key)
            self.assertIn('class="was"', block)
            self.assertIn(anchor, struck.group(1))
            self.assertNotIn(sale, struck.group(1))
            self.assertIn(sale, block)
            self.assertIn("Sale price", block)
            self.assertNotIn("This week", block)
            self.assertEqual(block.count("/mo"), 2)
            self.assertNotIn("\u2014", block)
        self.assertNotIn("This week only, ends Sunday, November 22", home)
        self.assertNotIn("small business", home.lower())

    def test_plain_http_hosts_redirect_to_https_www(self):
        for base in (
            "http://macrandleacres.com",
            "http://www.macrandleacres.com",
            "https://macrandleacres.com",
        ):
            res = self.client.get("/book?type=audit", base_url=base)
            self.assertEqual(res.status_code, 301, base)
            self.assertEqual(res.headers["Location"], ORIGIN + "/book?type=audit", base)
        ok = self.client.get("/", base_url="https://www.macrandleacres.com")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/healthz", base_url="http://macrandleacres.com").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_copy_refresh_key_strings(self):
        home = self.client.get("/").get_data(as_text=True)
        hub = self.client.get("/hub").get_data(as_text=True)
        design = self.client.get("/design").get_data(as_text=True)
        terms = self.client.get("/terms").get_data(as_text=True)
        llms = self.client.get("/llms.txt").get_data(as_text=True)
        for phrase in (
            "I help you get seen, get conversations going, and turn them into customers on Facebook, Instagram and beyond.",
            "You tell me what's on your plate and I take it off.",
            "Every month you get a short list of what I changed and what I'd do next.",
            "Profiles that pop",
            "Posts that get seen",
            "More real conversations",
            "Local groups",
            "Ads that make sense",
            "Go High Level set up right",
            'data-edit="voice_h2hi">"marketing."',
            "You want people to find you, trust you and call.",
            "showing you what moved",
            "Straight from Meta insights, plus Ads Manager when we run ads",
            'data-edit="card1_h3">Instant replies',
            'data-edit="card2_h3">Follow-up texts',
            'data-edit="card3_h3">Ad check-ups',
            'data-edit="card5_h3">Easy booking',
            'data-edit="card6_h3">Monthly plan',
            "If you use Go High Level, I can set up instant replies so new leads hear back fast.",
            "I set up follow-up texts and emails in Go High Level so fewer leads slip through.",
            "If you run ads, I review them every month and shift budget toward what gets clicks, messages and calls.",
            "I help you set up booking links and reminders so people can book you easily.",
            "A short monthly plan: what we did, what worked, what's next.",
            "Your posts, pages and groups are the work.",
            "Example month",
            "Your real numbers, straight from Meta, yours to verify. The numbers shown here are an example.",
            "Monthly plans. Pick how much you want me to take on.",
            "Done for you, done with you, or I teach you how.",
            "Sale price",
            "Monthly review of your profiles, page and posts",
            "A short to-do list, ranked",
            "I handle the top items for you, or teach you how",
            "Weekly hands-on help with your Facebook and Instagram.",
            "Weekly work on your Facebook and Instagram: posts, graphics, groups and replies",
            "Messages answered within one business day",
            "Each month I look over your profiles, page and posts, and fix the things that matter most.",
            "Your Growth Partner, all-in on growing the business.",
            "Everything in Momentum Plus",
            "I help real estate agents get seen, get conversations going, and turn them into clients on Facebook, Instagram and beyond.",
            "Ads set up and managed",
            "YouTube and video help",
            "Go High Level and AI agents set up for you",
            "Quarterly plan",
            "Every month I take work off your plate and show you",
            "look great, load fast, and come with booking and contact forms built in",
            "On a free call, I'll look at your profiles, page and posts and tell you the first three things I'd fix.",
            "Clear marketing. You always know what I did and why.",
            "a growth partner based in Salisbury, MD",
            "Jeff Randle, Growth Partner.",
            '"telephone":"+1-443-323-1268"',
            '"jobTitle":"Growth Partner"',
        ):
            self.assertIn(phrase, home, phrase)
        t2_name = re.search(r'data-edit="t2_name">([^<]*)</div>', home)
        self.assertIsNotNone(t2_name)
        self.assertEqual(t2_name.group(1), "Momentum Plus")
        for gone in (
            "Fractional Growth Advisor",
            "fractional growth advisor",
            "Straight from Meta Ads Manager",
            "Weekly nurturing sequences",
            "Monthly CRM optimization",
            "Facebook ad audit",
            "Lead follow-up management",
            'data-edit="card4_',
            "Lightning fast",
            "CRM optimization",
            "+1-862-254-7839",
            "\u2014",
        ):
            self.assertNotIn(gone, home, gone)
        self.assertEqual(home.count(">Fast<"), 0)
        self.assertIn("Growth Partner · Lucid Marketing, by MacRandle Acres", hub)
        self.assertIn(
            "Your Growth Partner. I help you get seen, get conversations going, and turn them into customers on Facebook, Instagram and beyond.",
            hub,
        )
        self.assertIn('"jobTitle":"Growth Partner"', hub)
        self.assertNotIn("Fractional Growth Advisor", hub)
        self.assertGreaterEqual(design.count("Sample content"), 4)
        self.assertIn("Sample testimonial", design)
        self.assertNotIn("THE CARTERS", design)
        self.assertNotIn("Lightning fast", design)
        self.assertIn(">Fast<", design)
        self.assertIn('href="tel:+14433231268">(443) 323-1268</a>', terms)
        self.assertNotIn("443-389-0330", terms)
        self.assertIn("growth partner in Salisbury, Maryland", llms)
        self.assertIn(
            "He helps businesses get seen, get conversations going, and turn them into customers on Facebook, Instagram and beyond.",
            llms,
        )
        self.assertIn("Momentum Plus is $1,997 to $2,500 per month", llms)
        self.assertNotIn("Growth Partner is $1,997", llms)
        self.assertIn("Phone: +1-443-323-1268", llms)
        self.assertIn("Free AI starter prompts", llms)
        self.assertNotIn("Grok", llms)
        self.assertNotIn("+1-862-254-7839", llms)

    def test_sample_homepage_sections(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertLess(home.index('id="voice"'), home.index('id="help"'))
        self.assertLess(home.index('id="help"'), home.index('id="facebook"'))
        self.assertLess(home.index('id="facebook"'), home.index('id="how-i-work"'))
        self.assertLess(home.index('id="how-i-work"'), home.index('id="promise"'))

        help_html = home.split('id="help"', 1)[1].split('id="facebook"', 1)[0]
        fb_html = home.split('id="facebook"', 1)[1].split('id="how-i-work"', 1)[0]
        how_html = home.split('id="how-i-work"', 1)[1].split('id="promise"', 1)[0]

        help_titles = re.findall(r"<h3[^>]*>(.*?)</h3>", help_html)
        self.assertEqual(help_titles, [
            "Meta profiles and business pages",
            "Posts and graphics",
            "Local Facebook groups",
            "Ads",
            "YouTube and video",
            "Go High Level and AI",
            "Whatever else is on your plate",
        ])
        self.assertEqual(help_titles[-1], "Whatever else is on your plate")
        self.assertIn("I've spent almost a decade on social media.", help_html)
        self.assertIn("I've run 200+ real estate ads.", help_html)
        self.assertIn("I've used Go High Level for years.", help_html)
        self.assertIn("AI agents and automation", help_html)
        self.assertIn('class="reveal"', help_html)
        for key in ("help_eye", "help_h2hi", "help_lead", "help1_h", "help7_p"):
            self.assertIn('data-edit="%s"' % key, help_html)

        fb_titles = re.findall(r"<h3[^>]*>(.*?)</h3>", fb_html)
        self.assertEqual(fb_titles, [
            "Pinned post",
            "Featured comments and links",
            "Stories",
            "Weekly Meta achievements and challenges",
            "Recommended feed",
            "Profile optimization",
            "Business page optimization",
            "Organic reach in local groups",
        ])
        fb_lines = re.findall(r"<p[^>]*>(.*?)</p>", fb_html)
        self.assertEqual(len(fb_lines), 9)
        for line in fb_lines[1:]:
            self.assertEqual(line.count("."), 1, line)
        self.assertIn('data-edit="fb1_p"', fb_html)
        self.assertIn('data-edit="fb8_h"', fb_html)

        how_titles = re.findall(r"<h3[^>]*>(.*?)</h3>", how_html)
        self.assertEqual(how_titles, [
            "Look",
            "Plan",
            "Do it your way",
            "Weekly check-in",
            "Monthly recap",
            "Done for you",
            "Done with you",
            "Learn with me",
        ])
        self.assertIn("I do it for you, we do it together, or I teach you how.", how_html)
        self.assertIn("Each week we look at what got done and what's next.", how_html)
        self.assertIn("Each month you get your Meta numbers and a short plan for the month ahead.", how_html)
        self.assertIn("No two clients are the same.", how_html)
        self.assertIn("Join the free community", how_html)
        self.assertIn('data-edit="step1_h"', how_html)
        self.assertIn('data-edit="way3_p"', how_html)
        self.assertIn('class="work-steps"', how_html)

        blob = help_html + fb_html + how_html
        for banned in (
            "\u2014",
            "\u2013",
            "[Jeff to confirm]",
            "Most people",
            "Some people",
            "There are certain",
            "A lot of people",
            "Grok",
            "ChatGPT",
            "Claude",
            "testimonial",
            "<blockquote",
            "qcard",
            "&ldquo;",
            "&rdquo;",
        ):
            self.assertNotIn(banned, blob, banned)


class HomeLayoutTests(unittest.TestCase):
    """Final CTA clearance and the homepage websites section."""

    def setUp(self):
        self.client = site.app.test_client()

    def _home(self):
        return self.client.get("/").get_data(as_text=True)

    def _css(self, home):
        return home.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_final_cta_clears_the_footer_wave(self):
        """The wave is pulled up over the footer. Card margin plus section padding must leave a gap."""
        home = self._home()
        css = self._css(home)
        self.assertIn("Where are you", home)
        self.assertIn('data-edit="final_h2hi">leaking growth?', home)
        final = home.split('<section class="final">', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("buy.stripe.com", final)
        self.assertNotIn("$149", final)
        self.assertNotIn("Grok Bot subscription", final)
        self.assertIn('class="btn btn-gold final-audit" href="/book?type=audit"><span data-edit="final_audit_v10">Book a free Growth Audit', final)
        self.assertIn('data-edit="final_cta2">Email Jeff', home)
        wave = re.search(r"\.foot-wave\{[^}]*top:\s*-(\d+)px", css)
        margin = re.search(r"\.final-box\{[^}]*margin-bottom:\s*(\d+)px", css)
        desktop_pad = re.search(r"section\{position:relative;padding:(\d+)px 0\}", css)
        mobile_at = css.find("@media(max-width:620px)")
        mobile_rule = css.find("section{padding:", mobile_at)
        mobile_pad = re.match(r"section\{padding:(\d+)px 0\}", css[mobile_rule:])
        self.assertGreater(mobile_at, -1)
        self.assertGreater(mobile_rule, mobile_at)
        self.assertIsNotNone(wave)
        self.assertIsNotNone(margin)
        self.assertIsNotNone(desktop_pad)
        self.assertIsNotNone(mobile_pad)
        pull = int(wave.group(1))
        gap_desktop = int(desktop_pad.group(1)) + int(margin.group(1)) - pull
        gap_mobile = int(mobile_pad.group(1)) + int(margin.group(1)) - pull
        self.assertGreaterEqual(gap_desktop, 32)
        self.assertLessEqual(gap_desktop, 48)
        self.assertGreaterEqual(gap_mobile, 24)
        self.assertLessEqual(gap_mobile, 32)
        # The section rhythm itself stays put. Only the card gains space under it.
        self.assertNotRegex(css, r"\.final\{[^}]*padding")

    def test_hero_primary_cta_is_the_growth_audit(self):
        """The hero's big button books the free Growth Audit, and the pay note is gone from the hero."""
        home = self._home()
        hero = home.split('<header class="hero"', 1)[1].split("</header>", 1)[0]
        cta = hero.split("hero-cta", 1)[1].split("</div>", 1)[0]
        primary = cta.split("<a", 2)[1]
        self.assertIn('class="btn btn-primary"', primary)
        self.assertIn('href="/book?type=audit&utm_source=site&utm_medium=cta"', primary)
        self.assertIn('data-edit="hero_audit_v9">Book your free Growth Audit', primary)
        self.assertNotIn("hero_pay_note", hero)
        self.assertNotIn("buy.stripe.com", cta)
        self.assertIn('href="#ai-employee"', cta)
        self.assertIn("Get your first AI agent, $149", cta)
        self.assertIn("Talk to a real person, not a ticket queue.", hero)
        offer = home.split('id="ai-employee"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-edit="pay_sub_v8"', offer)
        long_note = (
            "Your agent runs on your own Grok Bot subscription (usually about $30 a month), "
            "separate from this one-time setup fee. Already have one? Great. Not sure what you need? "
            "We'll sort it out on the call."
        )
        short = "Plus your own Grok Bot subscription, usually about $30 a month."
        self.assertEqual(offer.count(long_note), 1)
        self.assertEqual(home.count(long_note), 1)
        self.assertEqual(home.count(short), 0)
        for key in ("re_pay_note_v9", "jeff_pay_note_v9", "final_pay_note_v9"):
            self.assertNotIn('data-edit="%s"' % key, home)
        # The $149 offer lives only in the nav, the hero jump link and its own section.
        self.assertEqual(home.count('href="https://buy.stripe.com'), 2)
        for sec in ('id="real-estate"', 'id="jeff"'):
            block = home.split(sec, 1)[1].split("</section>", 1)[0]
            self.assertNotIn("buy.stripe.com", block)
            self.assertIn("Book a free Growth Audit", block)

    def test_design_page_has_free_quote_not_149(self):
        """The web design form offers a free quote; the $149 agent setup lives elsewhere."""
        design = self.client.get("/design").get_data(as_text=True)
        self.assertIn('<a href="#pbody">Get your free quote</a>', design)
        self.assertNotIn("Pay $149", design)
        self.assertNotIn('href="https://buy.stripe.com', design)
        self.assertNotIn("Prefer to talk first", design)

    def test_design_heading_is_a_full_sentence(self):
        design = self.client.get("/design").get_data(as_text=True)
        self.assertIn('>Explore</span> <em><span class="w" style="animation-delay:0.18s">what&#39;s</span>', design)
        self.assertNotIn("stop the scroll", design.split("<h1>")[1].split("</h1>")[0])
        self.assertNotIn('>Websites</span> <span class="w" style="animation-delay:0.18s">&amp;</span>', design)
        # The gold ending must paint on the animated words themselves, or it renders invisible.
        self.assertIn('.head h1 em .w{background:linear-gradient', design)

    def test_dragonfly_on_home_and_design(self):
        design = self.client.get("/design").get_data(as_text=True)
        self.assertIn('src="/dragonfly.js', design)
        with open(os.path.join(os.path.dirname(__file__), "..", "index.html"), encoding="utf-8") as f:
            self.assertIn('src="/dragonfly.js', f.read())
        js = self.client.get("/dragonfly.js")
        self.assertEqual(js.status_code, 200)
        body = js.get_data(as_text=True)
        js.close()
        self.assertIn("'Networking'", body)
        self.assertNotIn("\u2014", body)

    def test_index_has_no_website_mockup(self):
        path = os.path.join(os.path.dirname(__file__), "..", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("yourbusiness.com", html)
        self.assertNotIn("wb-", html)
        section = html.split('id="websites"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-edit="ws_eyebrow">Also built here', section)
        self.assertIn('data-edit="ws_h2a">Websites &amp; apps that', section)
        self.assertIn('data-edit="ws_h2hi">stop the scroll', section)
        self.assertIn("Your website is often the first handshake.", section)
        self.assertIn('data-edit="ws_li1">Custom design, never a template', section)
        self.assertIn('data-edit="ws_li2">Booking, forms &amp; lead capture built in', section)
        self.assertIn('data-edit="ws_li3">Mobile-first, fast, and SEO-ready', section)
        self.assertIn('data-edit="ws_cta1">See the work', section)
        self.assertIn('data-edit="ws_cta2">Start a project', section)


if __name__ == "__main__":
    unittest.main()


class DesignWinsTests(unittest.TestCase):
    def setUp(self):
        import app
        self.client = app.app.test_client()

    def test_design_has_four_balanced_wins(self):
        design = self.client.get("/design").get_data(as_text=True)
        self.assertEqual(design.count('<div class="win">'), 4)
        self.assertIn('<div class="wt">Ongoing support</div>', design)
        self.assertIn('grid-template-columns:repeat(4,1fr)', design)



class RealResultsTests(unittest.TestCase):
    def test_real_results_section_uses_verified_numbers_only(self):
        html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html"), encoding="utf-8").read()
        sec = html.split('id="real-results"')[1].split("</section>")[0]
        self.assertIn("Real results", sec)
        self.assertEqual(sec.count('class="impact-stat rr-stat"'), 6)
        for banned in ("97.27", "Cresskill", "New Jersey", "NJ", "\u2014"):
            self.assertNotIn(banned, sec)


class MobileNavPaddingTests(unittest.TestCase):
    def test_mobile_nav_has_side_padding(self):
        html = open(os.path.join(os.path.dirname(__file__), '..', 'index.html'), encoding='utf-8').read()
        self.assertNotIn('padding:10px 0;flex-wrap:wrap', html)
        self.assertGreaterEqual(html.count('padding:10px 16px;flex-wrap:wrap'), 2)
