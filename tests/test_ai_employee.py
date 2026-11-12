"""AI Virtual Employee intake: validation, save, notify, honeypot."""
import os

os.environ["RUN_REMINDERS"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin"

import datetime
import json
import re
import tempfile
import unittest
from unittest.mock import patch

import app as site


class SyncThread(object):
    """Run the notification inline so tests can see logs and mail before the response returns."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class AiEmployeeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        orig = site.AI_EMPLOYEES_PATH
        site.AI_EMPLOYEES_PATH = os.path.join(self.tmp.name, "ai_employees.json")
        self.addCleanup(setattr, site, "AI_EMPLOYEES_PATH", orig)
        site._RL.pop("aiemp", None)
        self.thread = patch.object(site.threading, "Thread", SyncThread)
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.client = site.app.test_client()

    def _token(self):
        html = self.client.get("/ai-employee/start").get_data(as_text=True)
        match = re.search(r'id="csrf" name="csrf" value="([^"]+)"', html)
        self.assertIsNotNone(match, html[:500])
        return match.group(1)

    def _payload(self, **overrides):
        data = {
            "csrf": self._token(),
            "website": "",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "phone": "(410) 555-0199",
            "business": "Analytical Engines",
            "industry": "Professional services",
            "industry_other": "",
            "team": "2-5",
            "daily_task": "Sorting the morning inbox",
            "teammate": "A calm assistant who drafts follow-ups",
            "automations": ["followups", "inbox"],
            "automation_other": "",
            "tools": "Gmail, Google Calendar",
            "package": "Starter",
        }
        data.update(overrides)
        return data

    def _post(self, payload):
        site._RL.pop("aiemp", None)
        return self.client.post("/api/ai-employee", json=payload)

    def test_phone_and_email_rules(self):
        valid_phones = [
            "(410) 555-0199",
            "410-555-0199",
            "410.555.0199",
            "4105550199",
            "1-410-555-0199",
            "+1 410 555 0199",
            "+1 (410) 555-0199",
            "+44 20 7946 0958",
        ]
        for phone in valid_phones:
            self.assertTrue(site.valid_phone(phone), phone)
        for phone in ["", "123", "555-0199", "000-000-0000", "11234567890", "abc-def-ghij", "+0123456789"]:
            self.assertFalse(site.valid_phone(phone), phone)
        self.assertFalse(site._EMAIL_RE.match("not-an-email"))
        self.assertTrue(site._EMAIL_RE.match("ada@example.com"))

    def test_validation_errors_match_the_form(self):
        _rec, errors = site.validate_ai_employee({
            "name": "A",
            "email": "nope",
            "phone": "123",
            "business": "",
            "industry": "Space mining",
            "team": "a crowd",
            "daily_task": "",
            "teammate": "",
            "automations": ["hack-the-planet"],
            "package": "Enterprise",
            "tools": "x" * 401,
        })
        self.assertIsNone(_rec)
        self.assertEqual(errors["name"], "Please enter your full name.")
        self.assertEqual(errors["email"], "Please enter a valid email.")
        self.assertEqual(errors["phone"], "Please enter a valid phone number.")
        self.assertEqual(errors["business"], "Please enter your business name.")
        self.assertEqual(errors["industry"], "Please choose an industry.")
        self.assertEqual(errors["team"], "Please choose a team size.")
        self.assertIn("daily_task", errors)
        self.assertIn("teammate", errors)
        self.assertEqual(errors["automations"], "Please choose an automation from the list.")
        self.assertEqual(errors["package"], "Please choose a package, or Not sure.")
        self.assertEqual(errors["tools"], "Please keep tools to 400 characters or fewer.")

        _rec, errors = site.validate_ai_employee(self._payload(
            industry="Other", industry_other="", automations=["other"], automation_other=""))
        self.assertEqual(errors["industry_other"], "Please tell us your industry.")
        self.assertEqual(errors["automation_other"], "Please describe the other automation.")

        _rec, errors = site.validate_ai_employee(self._payload(name="N" * 121))
        self.assertEqual(errors["name"], "Please keep your name to 120 characters or fewer.")

    def test_api_rejects_invalid_submission_without_saving(self):
        res = self._post(self._payload(email="not-an-email", phone="123"))
        self.assertEqual(res.status_code, 400)
        body = res.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["fields"]["email"], "Please enter a valid email.")
        self.assertEqual(body["fields"]["phone"], "Please enter a valid phone number.")
        self.assertEqual(site.load_ai_employees(), [])

    def test_csrf_required(self):
        res = self._post(self._payload(csrf="not-the-token"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("session expired", res.get_json()["error"].lower())
        self.assertEqual(site.load_ai_employees(), [])

    def test_honeypot_is_dropped(self):
        with patch.object(site, "_smtp_send", return_value=True) as send:
            res = self._post(self._payload(website="https://spam.example"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        self.assertEqual(site.load_ai_employees(), [])
        send.assert_not_called()

    def test_submit_saves_and_notifies_owner_only(self):
        sent = {}

        def fake_send(msg):
            sent["to"] = msg["To"]
            sent["subject"] = msg["Subject"]
            sent["body"] = msg.get_content()
            sent["count"] = sent.get("count", 0) + 1
            return True

        payload = self._payload(name="<b>Ada</b> Lovelace", automations=["other"],
                                automation_other="<script>alert(1)</script>")
        with patch.dict(os.environ, {"SMTP_USER": "jeff@example.com", "SMTP_FROM": "jeff@example.com"}):
            with patch.object(site, "_smtp_send", side_effect=fake_send):
                res = self._post(payload)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("service=ai-employee", body["book"])
        self.assertIn("ada%40example.com", body["book"])
        rows = site.load_ai_employees()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "ada@example.com")
        self.assertEqual(rows[0]["package"], "Starter")
        self.assertEqual(rows[0]["automations"], ["other"])
        self.assertNotIn("website", rows[0])
        self.assertEqual(sent["count"], 1)
        self.assertEqual(sent["to"], site.ORG_EMAIL)
        self.assertNotEqual(sent["to"], "ada@example.com")
        self.assertIn("&lt;b&gt;Ada&lt;/b&gt;", sent["body"])
        self.assertIn("&lt;script&gt;", sent["body"])
        self.assertNotIn("<script>", sent["body"])
        self.assertIn("/book?service=ai-employee", sent["body"])
        self.assertIn("/admin/ai-employees", sent["body"])
        self.assertIn("&lt;b&gt;", sent["subject"])

    def test_notification_failure_still_succeeds(self):
        with patch.dict(os.environ, {"SMTP_USER": "jeff@example.com", "SMTP_FROM": "jeff@example.com"}):
            with patch.object(site, "_smtp_send", side_effect=RuntimeError("smtp down")):
                with self.assertLogs(site.app.logger, level="ERROR") as logs:
                    res = self._post(self._payload())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])
        self.assertEqual(len(site.load_ai_employees()), 1)
        self.assertTrue(any("owner notification failed" in line for line in logs.output))

        with patch.dict(os.environ, {"SMTP_USER": "jeff@example.com", "SMTP_FROM": "jeff@example.com"}):
            with patch.object(site, "_smtp_send", return_value=False):
                with self.assertLogs(site.app.logger, level="WARNING") as logs:
                    ok = site.send_ai_employee_email(site.load_ai_employees()[0])
        self.assertFalse(ok)
        self.assertTrue(any("owner email was not sent" in line for line in logs.output))

    def test_pages_admin_and_booking_prefill(self):
        start = self.client.get("/ai-employee/start")
        self.assertEqual(start.status_code, 200)
        html = start.get_data(as_text=True)
        self.assertIn("Your first AI agent, a Lucid Marketing setup", html)
        self.assertNotIn("Package interest", html)
        self.assertIn("Your first AI agent, set up with you on a call. $149 one-time.", html)
        self.assertNotIn("one-hour", html)
        self.assertNotIn("$200", html)
        self.assertIn('href="/book?type=agent"', html)
        self.assertIn("Book a Lucid Marketing call", html)
        self.assertNotIn("$497", html)
        self.assertNotIn("stripe", html.lower())
        self.assertIn("Real estate", html)
        self.assertIn('name="website"', html)

        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("Lucid Marketing, by MacRandle Acres | Remove Friction. Accelerate Growth.", home)
        self.assertIn('content="summary_large_image"', home)
        hero = home.split('id="top"', 1)[1].split('id="voice"', 1)[0]
        self.assertIn("Fractional Growth Advisor · For growing businesses", hero)
        self.assertIn("Remove Friction.", hero)
        self.assertIn("Accelerate Growth.", hero)
        self.assertIn("make your business", hero)
        self.assertNotIn("real estate", hero.lower())
        self.assertNotIn("realtor", hero.lower())
        self.assertNotIn("\u2014", hero)
        nav = home.split("<nav", 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("Real estate", nav)
        self.assertNotIn("real estate", nav.lower())
        re_section = home.split('id="real-estate"', 1)[1].split("MEET JEFF", 1)[0]
        self.assertIn("Real estate", re_section)
        self.assertIn("more profitable", re_section)
        self.assertNotIn("\u2014", re_section)
        self.assertIn("That's Lucid Marketing, by MacRandle Acres.", home)
        self.assertIn("Marketing that is conscious, clear, and intentional.", home)
        section = home.split('id="ai-employee"', 1)[1].split("REAL ESTATE", 1)[0]
        self.assertIn("Your first AI agent, set up with you on a call.", section)
        self.assertIn("$149", section)
        self.assertNotIn("one-hour", section)
        self.assertNotIn("$200", section)
        self.assertIn("one-time.", section)
        self.assertIn("Book a Lucid Marketing call", section)
        self.assertIn('href="/book?type=agent"', section)
        self.assertNotIn("$497", section)
        self.assertNotIn("$97/mo", section)
        self.assertNotIn("$1,997", section)
        self.assertNotIn("\u2014", section)
        self.assertNotIn("ChatGPT", section)
        self.assertNotIn("OpenAI", section)
        self.assertIn("https://www.linkedin.com/in/jeff-randle-042a23196/", home)
        self.assertIn("https://www.facebook.com/TheLucidMage", home)
        self.assertIn("Lucid Marketing, by MacRandle Acres.", home)

        hub = self.client.get("/hub").get_data(as_text=True)
        self.assertIn("Lucid Mage in the dream world.", hub)
        self.assertIn("Lucid Marketing at MacRandle Acres.", hub)
        self.assertIn("Business: Lucid Marketing, by MacRandle Acres", hub)
        self.assertIn("Lucid Mage: dreams, music, art", hub)
        self.assertIn("$149 one-time.", hub)
        self.assertIn("Your first AI agent, set up with you on a call.", hub)
        self.assertNotIn("one-hour", hub)
        self.assertNotIn("One hour", hub)
        self.assertNotIn("$200", hub)
        self.assertIn('href="/book?utm_source=hub&utm_medium=link&utm_campaign=booking"', hub)
        self.assertIn("https://www.patreon.com/LucidMage", hub)
        self.assertIn("https://www.youtube.com/@JeffRandle89", hub)
        self.assertIn("https://www.tiktok.com/@lucidmage89", hub)
        self.assertIn("https://www.instagram.com/lucidmage89/", hub)
        self.assertIn("https://www.facebook.com/TheLucidMage", hub)
        self.assertIn("Lucid dreaming techniques and dream music.", hub)
        self.assertIn("Macrandleacres@gmail.com", hub)
        self.assertNotIn("$497", hub)
        self.assertNotIn("TheLucidMage89", hub)
        self.assertNotIn("calendar.google.com/calendar/u/0/r/week/2026/7/26", hub)
        self.assertNotIn("Live demo soon", hub)
        self.assertNotIn("Frontier AI", hub)
        self.assertNotIn("marketing wisdom", hub)
        self.assertNotIn("jeffrandle.io", hub.lower())
        self.assertNotIn("ChatGPT", hub)
        self.assertNotIn('data-edit="hero_name"', hub)
        self.assertIn('data-edit="hero_name_lm"', hub)
        self.assertIn('data-link="lk_fbprofile_lm"', hub)

        sitemap = self.client.get("/sitemap.xml").get_data(as_text=True)
        self.assertIn("/ai-employee/start", sitemap)
        self.assertIn("/design", sitemap)
        self.assertIn("/book", sitemap)
        self.assertIn("https://www.macrandleacres.com/hub", sitemap)

        self.assertEqual(self.client.get("/ai_employee_page.html").status_code, 404)
        for path in ("/", "/design", "/book", "/hub", "/privacy", "/healthz"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

        plain = self.client.get("/book").get_data(as_text=True)
        self.assertIn("Book your Growth Audit call", plain)
        pre = self.client.get("/book?service=ai-employee&name=%3CAda%3E+Lovelace&email=ada@example.com&phone=4105550199")
        page = pre.get_data(as_text=True)
        self.assertIn("Book your setup consultation", page)
        self.assertIn("plan your AI virtual employee", page)
        self.assertIn("Ada", page)
        self.assertNotIn("<Ada>", page)
        self.assertIn("ada@example.com", page)

        blocked = self.client.get("/admin/ai-employees")
        self.assertIn(blocked.status_code, (301, 302))
        self.client.post("/admin/login", data={"password": "test-admin", "next": "/admin/ai-employees"})
        self._post(self._payload(name="<img src=x onerror=alert(1)>", business="Safe Co"))
        admin = self.client.get("/admin/ai-employees")
        self.assertEqual(admin.status_code, 200)
        body = admin.get_data(as_text=True)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body)
        self.assertNotIn("<img src=x", body)
        self.assertIn("Safe Co", body)
        self.assertIn("Book their consultation", body)

    def test_disk_overrides_skip_renamed_keys(self):
        """Saved JSON replaces a data-edit or data-link only when the key still matches."""
        disk = {
            "hero_name": "Not Jeff",
            "hero_subname": "Old subname",
            "nav_cta": "Schedule a Call",
            "lk_fbprofile": "https://www.facebook.com/TheLucidMage89",
            "lk_youtube": "https://jeffrandle.io",
            "lk_patreon": "https://www.patreon.com/JeffRandle",
            "ai_price": "$497 <span>one-time setup</span>",
            "ai_opt": "Optional support: $97/mo.",
            "aiemp_p": "$497 one-time setup",
            "ai_offer_price_lm": "$200 <span>one-time.</span>",
            "ai_offer_h_lm": "Your first AI agent, set up with you on a one-hour call.",
            "aiemp_price_lm": "$200 one-time.",
            "aiemp_h3_lm": "Your first AI agent, set up with you on a one-hour call.",
            "aiemp_eye_lm": "One hour, together",
            "soc_yt": "marketing wisdom",
            "saai_tag": "Live demo soon",
            "saai_eyebrow": "Frontier AI",
        }
        with patch.object(site, "load_content", return_value=disk):
            hub = self.client.get("/hub").get_data(as_text=True)
            home = self.client.get("/").get_data(as_text=True)
        # Old values ride along inside window.__CONTENT__, but no element still uses those keys.
        self.assertIn("Not Jeff", hub)
        self.assertIn('data-edit="hero_name_lm">Jeff Randle<', hub)
        self.assertNotIn('data-edit="hero_name">', hub)
        self.assertIn('data-link="lk_youtube_lm" href="https://www.youtube.com/@JeffRandle89"', hub)
        self.assertNotIn('data-link="lk_youtube"', hub)
        self.assertNotIn('data-link="lk_fbprofile"', hub)
        self.assertIn('data-edit="ai_offer_price_149">$149', home)
        self.assertNotIn('data-edit="ai_offer_price_lm"', home)
        self.assertNotIn('data-edit="ai_price"', home)
        self.assertIn('data-edit="aiemp_price_149">$149 one-time.', hub)
        self.assertNotIn('data-edit="aiemp_price_lm"', hub)
        shown = {}
        for key, html_value in (
            ("hero_name_lm", "Jeff Randle"),
            ("lk_fbprofile_lm", "https://www.facebook.com/TheLucidMage"),
            ("ai_offer_h_149", "Your first AI agent, set up with you on a call."),
        ):
            shown[key] = disk[key] if key in disk and disk[key] is not None else html_value
        self.assertEqual(shown["hero_name_lm"], "Jeff Randle")
        self.assertEqual(shown["lk_fbprofile_lm"], "https://www.facebook.com/TheLucidMage")
        self.assertNotEqual(shown["ai_offer_h_149"], disk["ai_price"])

    def test_blackout_agent_booking_and_signup(self):
        fixed = datetime.datetime(2026, 11, 16, 9, 0, tzinfo=site.BOOK_TZ)

        class Frozen(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz is not None else fixed

        with patch.object(site.datetime, "datetime", Frozen):
            days = {d["date"] for d in site.gen_slots()}
        self.assertNotIn("2026-11-26", days)
        self.assertIn("2026-11-25", days)

        agent = self.client.get("/book?type=agent").get_data(as_text=True)
        self.assertIn("<h1>Book a Lucid Marketing call</h1>", agent)
        self.assertIn('<p id="sub">Pick a time that works.</p>', agent)
        self.assertNotIn("30 minutes with Jeff Randle. Pick a time that works.", agent)
        plain = self.client.get("/book").get_data(as_text=True)
        self.assertIn("<h1>Book your Growth Audit call</h1>", plain)
        self.assertIn("30 minutes with Jeff Randle. Pick a time that works.", plain)

        path = os.path.join(self.tmp.name, "subscribers.json")
        orig = site.SUBSCRIBERS_PATH
        site.SUBSCRIBERS_PATH = path
        self.addCleanup(setattr, site, "SUBSCRIBERS_PATH", orig)
        ok = self.client.post("/api/subscribe", json={"email": "Ada@Example.com", "website": ""})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.get_json()["ok"])
        self.assertEqual(site.load_subscribers()[0]["email"], "Ada@Example.com")
        self.client.post("/api/subscribe", json={"email": "ada@example.com"})
        self.assertEqual(len(site.load_subscribers()), 1)
        self.assertEqual(self.client.post("/api/subscribe", json={"email": "nope"}).status_code, 400)
        self.client.post("/admin/login", data={"password": "test-admin", "next": "/admin/subscribers"})
        admin = self.client.get("/admin/subscribers")
        self.assertEqual(admin.status_code, 200)
        self.assertIn("Ada@Example.com", admin.get_data(as_text=True))

        home = self.client.get("/").get_data(as_text=True)
        self.assertIn('alt="Jeff Randle, founder of MacRandle Acres"', home)
        self.assertIn('src="/jeff-randle.jpg"', home)
        self.assertIn('id="quotes" hidden', home)
        self.assertIn("Helping small businesses remove friction from growth.", home)
        self.assertNotIn("real estate teams", home.lower())
        self.assertIn("Not ready for a call? Join the free MacRandle Acres community on Skool.", home)
        self.assertIn("Most popular", home)
        nav = home.split("<nav", 1)[1].split("</nav>", 1)[0]
        self.assertIn("Book a Growth Audit", nav)
        self.assertNotIn("Lucid Marketing call", nav)
        offer = home.split('id="ai-employee"', 1)[1].split('id="free-step"', 1)[0]
        self.assertIn('href="/book?type=agent"', offer)
        self.assertIn("Book a Lucid Marketing call", offer)
        privacy = self.client.get("/privacy").get_data(as_text=True)
        self.assertNotIn("Growth Audit form", privacy)
        self.assertIn("join the email list", privacy)
        hub = self.client.get("/hub").get_data(as_text=True)
        self.assertIn('data-edit="nav_cta_ga">Book a Growth Audit', hub)
        self.assertIn('href="/book?type=agent&utm_source=hub&utm_medium=link&utm_campaign=booking"', hub)


if __name__ == "__main__":
    unittest.main()
