"""Tap-to-call link for Alice is in the server-rendered HTML of public pages."""
import os
import re
import unittest

os.environ["RUN_REMINDERS"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin"

import app as site


LABEL = "Talk to Alice, Jeff's AI assistant: (443) 323-1268"
HREF = 'href="tel:+14433231268"'
ANCHOR = '<a class="alice-call" href="tel:+14433231268">%s</a>' % LABEL
REQUESTED = ("/", "/book", "/privacy", "/terms", "/hub", "/contact")
FOOTER_PAGES = (
    "/", "/book", "/privacy", "/terms", "/hub",
    "/design", "/starter-prompts", "/ai-employee/start",
)


def _static(html):
    return re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)


class AliceCallLinkTests(unittest.TestCase):
    def setUp(self):
        self.client = site.app.test_client()

    def test_label_is_a_plain_call_link(self):
        self.assertEqual(site.ALICE_CALL_LABEL, LABEL)
        self.assertEqual(site.ALICE_CALL_LINK, ANCHOR)
        self.assertIn("Jeff's", LABEL)
        self.assertNotIn("\u2019", LABEL)
        self.assertNotIn("\\", LABEL)
        self.assertNotRegex(LABEL, r"[\u2012\u2013\u2014\u2015]")
        lowered = LABEL.lower()
        self.assertNotIn("sms", lowered)
        self.assertNotIn("text", lowered)

    def test_requested_routes_include_the_link_in_raw_html(self):
        found = []
        for path in REQUESTED:
            res = self.client.get(path)
            if res.status_code == 404:
                self.assertEqual(path, "/contact")
                continue
            found.append(path)
            self.assertEqual(res.status_code, 200, path)
            html = res.get_data(as_text=True)
            static = _static(html)
            self.assertIn(HREF, static, path)
            self.assertIn(LABEL, static, path)
            self.assertIn(ANCHOR, static, path)
            self.assertEqual(html.count(ANCHOR), static.count(ANCHOR), path)
        for path in ("/", "/book", "/privacy", "/terms", "/hub"):
            self.assertIn(path, found)

    def test_every_public_footer_has_the_call_link(self):
        for path in FOOTER_PAGES:
            static = _static(self.client.get(path).get_data(as_text=True))
            self.assertIn("<footer", static, path)
            footer = static.split("<footer", 1)[1].split("</footer>", 1)[0]
            self.assertIn(ANCHOR, footer, path)
            self.assertNotRegex(
                footer,
                r'alice-call[^>]*>[^<]*(SMS|text message|texting)',
                path,
            )

    def test_book_link_is_under_the_heading_before_javascript(self):
        html = self.client.get("/book").get_data(as_text=True)
        static = _static(html)
        self.assertEqual(static.count(ANCHOR), 2)
        head = static.split("<h1>", 1)[1].split('id="body"', 1)[0]
        self.assertIn('<p class="alice">%s</p>' % ANCHOR, head)
        self.assertLess(static.index(ANCHOR), static.index('id="body"'))
        self.assertLess(static.index('id="body"'), static.rindex(ANCHOR))

        agent = _static(self.client.get("/book?type=agent").get_data(as_text=True))
        agent_head = agent.split("<h1>", 1)[1].split('id="body"', 1)[0]
        self.assertIn("<h1>Book a Lucid Marketing call</h1>", agent)
        self.assertIn(ANCHOR, agent_head)

    def test_no_alice_call_anchor_contains_4433890330(self):
        old = "4433890330"
        anchor_re = re.compile(
            r'<a\b[^>]*\bclass="alice-call"[^>]*>.*?</a>',
            re.I | re.S,
        )
        found = 0
        for path in FOOTER_PAGES:
            static = _static(self.client.get(path).get_data(as_text=True))
            anchors = anchor_re.findall(static)
            self.assertTrue(anchors, path)
            found += len(anchors)
            for anchor in anchors:
                digits = re.sub(r"\D", "", anchor)
                self.assertNotIn(old, digits, path)
        self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
