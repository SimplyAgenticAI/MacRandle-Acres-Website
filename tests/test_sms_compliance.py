"""A2P 10DLC: terms page, privacy SMS section, and optional SMS consent on phone forms."""
import os
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ["RUN_REMINDERS"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin"

import app as site


TERMS_PHRASES = (
    "Program name: Lucid Marketing by MacRandle Acres.",
    "lead follow-up, appointment reminders, and occasional offers",
    "Message frequency varies.",
    "Message and data rates may apply.",
    "Reply STOP to opt out, HELP for help.",
    "Carriers are not liable for delayed or undelivered messages.",
    "not a condition of purchase",
    "macrandleacres@gmail.com",
    "(443) 323-1268",
)

PRIVACY_PHRASES = (
    "Mobile numbers and SMS opt-in consent are never shared with or sold to third parties "
    "or affiliates for marketing or promotional purposes.",
    "Text messaging originator opt-in data and consent are not shared with any third parties.",
    "Reply STOP to opt out.",
)


class SmsComplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = site.app.test_client()
        for bucket in ("book", "aiemp", "lead"):
            site._RL.pop(bucket, None)

    def _paths(self):
        root = self.tmp.name
        return {
            "bookings": os.path.join(root, "bookings.json"),
            "ai": os.path.join(root, "ai_employees.json"),
            "leads": os.path.join(root, "leads.json"),
        }

    def test_terms_page_has_sms_program_and_no_em_dash(self):
        res = self.client.get("/terms")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("<h1>Terms and Conditions</h1>", html)
        self.assertIn("Lucid Marketing, by MacRandle Acres", html)
        self.assertIn("MacRandle Acres, Salisbury, MD", html)
        self.assertIn("Effective date: November 15, 2026", html)
        self.assertIn("You must be 18 years of age or older to use this SMS service.", html)
        sms = html.split('id="sms"', 1)[1]
        self.assertIn("You must be 18 years of age or older to use this SMS service.", sms)
        self.assertEqual(sms.count("You must be 18 years of age or older to use this SMS service."), 1)
        self.assertIn("<h2>SMS program</h2>", html)
        self.assertIn('href="/privacy"', html)
        self.assertEqual(
            re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1),
            "https://www.macrandleacres.com/terms",
        )
        for phrase in TERMS_PHRASES:
            self.assertIn(phrase, html, phrase)
        for heading in (
            "Use of the site",
            "Services and booking",
            "No guarantees of results",
            "Payments",
            "Limitation of liability",
            "Maryland governing law",
            "Changes to these terms",
            "Contact",
        ):
            self.assertIn("<h2>%s</h2>" % heading, html)
        self.assertIn("Payments are handled by Stripe", html)
        self.assertIn("laws of the State of Maryland", html)
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)

    def test_privacy_sms_section_and_updated_date(self):
        html = self.client.get("/privacy").get_data(as_text=True)
        self.assertIn("Last updated: November 15, 2026", html)
        self.assertIn("<h2>Data security</h2>", html)
        self.assertIn(
            "We use reasonable administrative, technical, and physical safeguards to protect "
            "personal information, including encrypted connections (HTTPS) and access controls "
            "that limit who can view your information. No method of transmission or storage "
            "is completely secure.",
            html,
        )
        self.assertIn('id="sms"', html)
        self.assertIn("<h2>Text messages (SMS)</h2>", html)
        self.assertIn('href="/terms"', html)
        self.assertIn("join the email list", html)
        for phrase in PRIVACY_PHRASES:
            self.assertIn(phrase, html, phrase)
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)
        self.assertNotIn("Growth Audit form", html)

    def test_terms_link_is_in_every_footer_that_links_privacy(self):
        for path in ("/", "/hub", "/starter-prompts", "/design", "/privacy", "/book",
                     "/ai-employee/start", "/terms"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('href="/privacy"', html, path)
            self.assertIn('href="/terms"', html, path)
            self.assertRegex(html, r'href="/terms"[^>]*>Terms<', path)

    def test_stored_consent_sentence(self):
        self.assertEqual(
            site.SMS_CONSENT_TEXT,
            "I agree to receive text messages from Lucid Marketing by MacRandle Acres, "
            "including appointment reminders, inquiry follow-ups, and promotional offers. "
            "Message frequency varies. Msg & data rates may apply. Reply STOP to opt out, "
            "HELP for help. Consent is not a condition of purchase.",
        )
        self.assertNotIn("\u2014", site.SMS_CONSENT_TEXT)
        self.assertNotIn("\u2013", site.SMS_CONSENT_TEXT)

    def _static_html(self, path):
        html = self.client.get(path).get_data(as_text=True)
        static = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
        return html, static

    def _assert_one_visible_consent(self, path, html, static):
        block = site.sms_consent_label_html("sms_consent")
        self.assertEqual(static.count(block), 1, path)
        self.assertEqual(html.count(block), 1, path)
        inputs = re.findall(r'<input\b[^>]*name="sms_consent"[^>]*>', html, re.I)
        self.assertEqual(inputs, [
            '<input id="sms_consent" name="sms_consent" type="checkbox" value="yes">'
        ], path)
        self.assertNotIn("checked", inputs[0])
        self.assertNotIn("required", inputs[0])
        self.assertIn('<label for="sms_consent">%s</label>' % site._esc(site.SMS_CONSENT_TEXT), static)
        self.assertIn('href="/terms" target="_blank" rel="noopener">Terms</a>', block)
        self.assertIn('href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>', block)
        self.assertNotIn("\u2014", html, path)
        self.assertNotIn("\u2013", html, path)
        self.assertEqual(html.count('id="sms_consent"'), 1, path)

    def test_sms_checkbox_is_in_raw_html_and_optional(self):
        book_html, book_static = self._static_html("/book")
        start_html, start_static = self._static_html("/ai-employee/start")
        self._assert_one_visible_consent("/book", book_html, book_static)
        self._assert_one_visible_consent("/ai-employee/start", start_html, start_static)

        body_at = book_static.find('id="body"')
        slot_at = book_static.find('id="smsSlot"')
        consent_at = book_static.find('name="sms_consent"')
        self.assertGreater(slot_at, body_at)
        self.assertGreater(consent_at, slot_at)
        self.assertNotIn('name="sms_consent"', book_static[body_at:slot_at])
        slot_tag = re.search(r'<div class="sms-slot" id="smsSlot"([^>]*)>', book_static)
        self.assertIsNotNone(slot_tag)
        self.assertNotIn("hidden", slot_tag.group(1))
        self.assertNotIn("smsMarkup", book_html)
        self.assertIn("smsChecked()", book_html)
        self.assertIn("sms_consent:sms", book_html)

        step = re.search(
            r'<section class="pane" data-step="0"([^>]*)>(.*?)</section>',
            start_static,
            re.S,
        )
        self.assertIsNotNone(step)
        self.assertNotIn("hidden", step.group(1))
        self.assertIn('name="sms_consent"', step.group(2))
        for later in ("1", "2", "3"):
            pane = re.search(
                r'<section class="pane" data-step="%s"([^>]*)>(.*?)</section>' % later,
                start_static,
                re.S,
            )
            self.assertIsNotNone(pane, later)
            self.assertIn("hidden", pane.group(1), later)
            self.assertNotIn('name="sms_consent"', pane.group(2), later)

        home = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('name="sms_consent"', home)
        for path in ("/", "/design", "/hub", "/privacy", "/starter-prompts"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIsNone(re.search(r'type="tel"|autocomplete="tel"', html), path)

    def _slots(self):
        days = self.client.get("/api/slots").get_json()["days"]
        slots = [slot["iso"] for day in days for slot in day["slots"]]
        self.assertGreaterEqual(len(slots), 2, days)
        return slots

    def _assert_consent_record(self, rec, agreed):
        self.assertIs(rec["sms_consent"], agreed)
        self.assertRegex(rec["sms_consent_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertEqual(rec["sms_consent_text"], site.SMS_CONSENT_TEXT)

    def test_booking_succeeds_with_and_without_sms_consent(self):
        paths = self._paths()
        with patch.object(site, "BOOKINGS_PATH", paths["bookings"]):
            slots = self._slots()
            without = self.client.post("/api/book", json={
                "slot": slots[0], "name": "Ada Lovelace", "email": "ada@example.com",
                "phone": "(410) 555-0199",
            })
            with_yes = self.client.post("/api/book", json={
                "slot": slots[1], "name": "Grace Hopper", "email": "grace@example.com",
                "phone": "443-389-0330", "sms_consent": True,
            })
            self.assertEqual(without.status_code, 200, without.get_json())
            self.assertTrue(without.get_json()["ok"])
            self.assertEqual(with_yes.status_code, 200, with_yes.get_json())
            self.assertTrue(with_yes.get_json()["ok"])
            rows = {row["email"]: row for row in site.load_bookings()}
            self._assert_consent_record(rows["ada@example.com"], False)
            self._assert_consent_record(rows["grace@example.com"], True)
            self.client.post("/admin/login", data={"password": "test-admin", "next": "/admin/bookings"})
            admin = self.client.get("/admin/bookings").get_data(as_text=True)
            self.assertIn("SMS consent", admin)
            self.assertIn(">Yes<", admin)
            self.assertIn(">No<", admin)
            self.assertIn(site._esc(site.SMS_CONSENT_TEXT), admin)

            captured = []

            class DummySMTP(object):
                def __init__(self, *args, **kwargs):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def starttls(self):
                    pass

                def login(self, *args):
                    pass

                def send_message(self, message):
                    captured.append(message)

            with patch.dict(os.environ, {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_USER": "jeff@example.com",
                "SMTP_PASS": "secret",
                "SMTP_PORT": "587",
            }):
                with patch("smtplib.SMTP", DummySMTP):
                    site.send_booking_emails(rows["grace@example.com"])
            self.assertTrue(captured, "booking email was not sent")
            owner = next(msg for msg in captured if msg["To"] == site.ORG_EMAIL)
            plain = owner.get_body(preferencelist=("plain",))
            body = plain.get_content() if plain is not None else owner.get_content()
            self.assertIn("SMS consent: Yes", body)
            self.assertIn(rows["grace@example.com"]["sms_consent_at"], body)
            self.assertIn(site.SMS_CONSENT_TEXT, body)

    def _ai_token(self):
        html = self.client.get("/ai-employee/start").get_data(as_text=True)
        match = re.search(r'id="csrf" name="csrf" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        return match.group(1)

    def _ai_payload(self, **overrides):
        data = {
            "csrf": self._ai_token(),
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
            "automations": ["followups"],
            "automation_other": "",
            "tools": "Gmail",
            "package": "Starter",
        }
        data.update(overrides)
        return data

    def test_ai_intake_succeeds_with_and_without_sms_consent(self):
        paths = self._paths()
        sent = []

        class SyncThread(object):
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self._target = target
                self._args = args or ()

            def start(self):
                if self._target:
                    self._target(*self._args)

        def fake_send(msg):
            sent.append(msg.get_content())
            return True

        with patch.object(site, "AI_EMPLOYEES_PATH", paths["ai"]):
            with patch.object(site.threading, "Thread", SyncThread):
                with patch.object(site, "_smtp_send", side_effect=fake_send):
                    with patch.dict(os.environ, {"SMTP_USER": "jeff@example.com", "SMTP_FROM": "jeff@example.com"}):
                        skipped = self.client.post("/api/ai-employee", json=self._ai_payload(
                            email="ada@example.com", name="Ada Lovelace"))
                        opted = self.client.post("/api/ai-employee", json=self._ai_payload(
                            email="grace@example.com", name="Grace Hopper", sms_consent=True))
                    self.assertEqual(skipped.status_code, 200, skipped.get_json())
                    self.assertTrue(skipped.get_json()["ok"])
                    self.assertEqual(opted.status_code, 200, opted.get_json())
                    self.assertTrue(opted.get_json()["ok"])
                    rows = {row["email"]: row for row in site.load_ai_employees()}
                    self._assert_consent_record(rows["ada@example.com"], False)
                    self._assert_consent_record(rows["grace@example.com"], True)
                    self.assertTrue(any("SMS consent: No" in body for body in sent))
                    self.assertTrue(any("SMS consent: Yes" in body and site.SMS_CONSENT_TEXT in body for body in sent))
                    self.client.post("/admin/login", data={"password": "test-admin", "next": "/admin/ai-employees"})
                    admin = self.client.get("/admin/ai-employees").get_data(as_text=True)
                    self.assertIn("SMS consent:", admin)
                    self.assertIn(site._esc(site.SMS_CONSENT_TEXT), admin)

    def test_lead_api_stores_consent_without_blocking_old_posts(self):
        paths = self._paths()
        with patch.object(site, "LEADS_PATH", paths["leads"]):
            plain = self.client.post("/api/lead", json={
                "name": "Ada Lovelace", "email": "ada@example.com", "phone": "(410) 555-0199",
            })
            opted = self.client.post("/api/lead", json={
                "name": "Grace Hopper", "email": "grace@example.com", "phone": "443-389-0330",
                "sms_consent": "true",
            })
            self.assertEqual(plain.status_code, 200)
            self.assertTrue(plain.get_json()["ok"])
            self.assertEqual(opted.status_code, 200)
            self.assertTrue(opted.get_json()["ok"])
            rows = {row["email"]: row for row in site.load_leads()}
            self._assert_consent_record(rows["ada@example.com"], False)
            self._assert_consent_record(rows["grace@example.com"], True)
            self.client.post("/admin/login", data={"password": "test-admin", "next": "/admin/leads"})
            admin = self.client.get("/admin/leads").get_data(as_text=True)
            self.assertIn("SMS consent:", admin)
            self.assertIn(site._esc(site.SMS_CONSENT_TEXT), admin)
            csv = self.client.get("/admin/leads/export.csv").get_data(as_text=True)
            self.assertIn("sms_consent", csv)
            self.assertIn("yes", csv)
            self.assertIn("no", csv)


if __name__ == "__main__":
    unittest.main()
