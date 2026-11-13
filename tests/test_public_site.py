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
MARKETING_PAGES = ("/", "/design", "/book", "/hub", "/ai-employee/start", "/privacy", "/starter-prompts")


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
        self.assertIn("For growing businesses", hero)
        self.assertIn("Remove Friction. Accelerate Growth.", hero)

        bio = re.search(r'data-edit="jeff_p_sb">(.*?)</p>', home, re.S).group(1)
        footer = re.search(r'data-edit="foot_copy_sb">(.*?)</span>', home, re.S).group(1)
        self.assertIn("small businesses", bio)
        self.assertNotIn("real estate", bio.lower())
        self.assertIn("Helping small businesses remove friction from growth.", footer)
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
        self.assertIn('data-edit="jeff_p_sb">I\'m the founder of MacRandle Acres', home)
        self.assertIn("Helping small businesses remove friction from growth.", home)
        self.assertNotRegex(home, r'data-edit="jeff_p"[^_]')
        self.assertNotRegex(home, r'data-edit="foot_copy"[^_]')

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
            ORIGIN + "/privacy",
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
        self.assertIn("small businesses", service["description"])
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
        self.assertIn("Free Grok Starter Prompts", html)
        self.assertIn("From Lucid Marketing, by MacRandle Acres.", html)
        self.assertIn("Remove Friction. Accelerate Growth.", html)
        self.assertIn(
            "These 10 prompts are ones we have actually used to grow a channel, "
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
        self.assertIn("Book a Lucid Marketing call", cta)
        self.assertIn('href="/book?type=agent"', cta)
        self.assertNotIn("Grok", cta)
        self.assertNotIn("$", cta)
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)
        self.assertIn('window.__PAGE__="prompts"', html)

        home = self.client.get("/").get_data(as_text=True)
        home_nav = home.split("<nav", 1)[1].split("</nav>", 1)[0]
        home_footer = home.split("<footer>", 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("/starter-prompts", home_nav)
        self.assertIn('href="/starter-prompts"', home_footer)
        self.assertIn("Free Grok Starter Prompts", home_footer)

        hub = self.client.get("/hub").get_data(as_text=True)
        hub_nav = hub.split("<nav", 1)[1].split("</nav>", 1)[0]
        hub_footer = hub.split("<footer>", 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("/starter-prompts", hub_nav)
        self.assertIn('href="/starter-prompts"', hub)
        self.assertIn("Free Grok Starter Prompts", hub)
        self.assertIn('href="/starter-prompts"', hub_footer)

        design = self.client.get("/design").get_data(as_text=True)
        self.assertIn('href="/starter-prompts"', design)


if __name__ == "__main__":
    unittest.main()
