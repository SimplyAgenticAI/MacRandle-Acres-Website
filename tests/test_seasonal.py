"""Date windows and request overrides for the seasonal look."""
import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import seasonal


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ThemeWindowTests(unittest.TestCase):
    def test_required_windows(self):
        cases = {
            date(2026, 10, 14): "default",
            date(2026, 10, 15): "halloween",
            date(2026, 10, 31): "halloween",
            date(2026, 11, 1): "thanksgiving",
            date(2026, 11, 24): "thanksgiving",
            date(2026, 11, 30): "thanksgiving",
            date(2026, 12, 1): "winter",
            date(2026, 12, 31): "winter",
            date(2027, 1, 1): "winter",
            date(2027, 1, 2): "default",
            date(2027, 11, 29): "thanksgiving",
            date(2027, 11, 30): "winter",
        }
        for day, theme in cases.items():
            self.assertEqual(seasonal.theme_for_date(day), theme, day)

    def test_january_and_october_edges_any_year(self):
        for year in (2024, 2026, 2028):
            self.assertEqual(seasonal.theme_for_date(date(year, 1, 1)), "winter", year)
            self.assertEqual(seasonal.theme_for_date(date(year, 1, 2)), "default", year)
            self.assertEqual(seasonal.theme_for_date(date(year, 10, 14)), "default", year)
            self.assertEqual(seasonal.theme_for_date(date(year, 10, 15)), "halloween", year)

    def test_cyber_monday_can_fall_in_december(self):
        # 2024: fourth Thursday is Nov 28, Cyber Monday is Dec 2.
        # 2025: fourth Thursday is Nov 27, Cyber Monday is Dec 1.
        self.assertEqual(seasonal.cyber_monday(2024), date(2024, 12, 2))
        self.assertEqual(seasonal.theme_for_date(date(2024, 12, 2)), "thanksgiving")
        self.assertEqual(seasonal.theme_for_date(date(2024, 12, 3)), "winter")
        self.assertEqual(seasonal.cyber_monday(2025), date(2025, 12, 1))
        self.assertEqual(seasonal.theme_for_date(date(2025, 12, 1)), "thanksgiving")
        self.assertEqual(seasonal.theme_for_date(date(2025, 12, 2)), "winter")

    def test_summer_is_default(self):
        self.assertEqual(seasonal.theme_for_date(date(2026, 7, 4)), "default")
        self.assertEqual(seasonal.theme_for_date(date(2026, 2, 14)), "default")


class OverrideTests(unittest.TestCase):
    def test_env_off_is_the_kill_switch(self):
        self.assertEqual(seasonal.current_theme(day=date(2026, 11, 24), env="off"), "default")
        self.assertEqual(seasonal.current_theme(day=date(2026, 10, 31), env="OFF"), "default")

    def test_env_forces_a_theme(self):
        self.assertEqual(seasonal.current_theme(day=date(2026, 1, 2), env="halloween"), "halloween")
        self.assertEqual(seasonal.current_theme(day=date(2026, 7, 4), env="Winter"), "winter")
        self.assertEqual(seasonal.current_theme(day=date(2026, 11, 24), env="default"), "default")

    def test_auto_and_blank_follow_the_date(self):
        self.assertEqual(seasonal.current_theme(day=date(2026, 11, 24), env="auto"), "thanksgiving")
        self.assertEqual(seasonal.current_theme(day=date(2026, 12, 1), env=""), "winter")
        self.assertEqual(seasonal.current_theme(day=date(2026, 10, 14), env="christmas"), "default")

    def test_preview_wins_for_that_call_only(self):
        self.assertEqual(
            seasonal.current_theme(day=date(2026, 11, 24), env="off", preview="halloween"),
            "halloween",
        )
        self.assertEqual(
            seasonal.current_theme(day=date(2026, 1, 2), env="winter", preview="default"),
            "default",
        )
        self.assertEqual(
            seasonal.current_theme(day=date(2026, 11, 24), env="off", preview="nope"),
            "default",
        )
        self.assertEqual(
            seasonal.current_theme(day=date(2026, 11, 24), env="thanksgiving", preview="off"),
            "thanksgiving",
        )

    def test_env_var_is_read_when_env_argument_is_omitted(self):
        with patch.dict(os.environ, {"SEASONAL_THEME": "off"}):
            self.assertEqual(seasonal.current_theme(day=date(2026, 11, 30)), "default")
        with patch.dict(os.environ, {"SEASONAL_THEME": "halloween"}):
            self.assertEqual(seasonal.current_theme(day=date(2027, 1, 2)), "halloween")

    def test_eastern_date_not_utc(self):
        # 03:30 UTC on Oct 15 is still Oct 14 in New York (EDT, UTC-4).
        before = datetime(2026, 10, 15, 3, 30, tzinfo=timezone.utc)
        after = datetime(2026, 10, 15, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(seasonal.eastern_today(before), date(2026, 10, 14))
        self.assertEqual(seasonal.current_theme(now=before, env="auto"), "default")
        self.assertEqual(seasonal.eastern_today(after), date(2026, 10, 15))
        self.assertEqual(seasonal.current_theme(now=after, env="auto"), "halloween")
        # A January midnight UTC is still New Year's Day evening on the East Coast? 
        # Jan 2 04:30 UTC is Jan 1 23:30 EST.
        new_year = datetime(2027, 1, 2, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(seasonal.eastern_today(new_year), date(2027, 1, 1))
        self.assertEqual(seasonal.current_theme(now=new_year, env="auto"), "winter")


class MarkupTests(unittest.TestCase):
    def _page(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            return f.read()

    def test_source_files_have_no_theme_markup(self):
        for name in ("index.html", "hub.html", "starter_prompts.html", "content.json"):
            text = self._page(name)
            self.assertNotIn("data-theme", text, name)
            self.assertNotIn("season-symbol", text, name)
            self.assertNotIn("data-season", text, name)

    def test_symbols_land_on_the_nav_mark_only(self):
        for name in ("index.html", "starter_prompts.html"):
            html = seasonal.stamp_html(self._page(name), "thanksgiving")
            nav = html.split("<nav", 1)[1].split("</nav>", 1)[0]
            footer = html.split("<footer", 1)[1].split("</footer>", 1)[0]
            self.assertEqual(nav.count("season-symbol"), 3, name)
            self.assertNotIn("season-symbol", footer, name)
            symbols = nav[nav.find("<svg"):nav.rfind("</svg>") + 6]
            self.assertNotIn("data-edit", symbols, name)
            self.assertIn('data-theme="thanksgiving"', html, name)

    def test_hub_has_no_letter_mark_so_no_symbol(self):
        html = seasonal.stamp_html(self._page("hub.html"), "winter")
        self.assertIn('data-theme="winter"', html)
        self.assertNotIn("season-symbol", html)

    def test_bad_theme_name_is_not_written_into_the_tag(self):
        html = seasonal.stamp_html("<html lang=\"en\"><nav><span class=\"mark\"><span>M</span></span></nav>", "<script>")
        self.assertIn('data-theme="default"', html)
        self.assertNotIn("<script>", html.split(">", 1)[0])


class RenderedPageTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("RUN_REMINDERS", "0")
        os.environ.setdefault("SECRET_KEY", "test-secret-key")
        os.environ.setdefault("ADMIN_PASSWORD", "test-admin")
        import app as site
        self.site = site
        self.client = site.app.test_client()

    def test_query_preview_is_per_request(self):
        winter = self.client.get("/?theme=winter").get_data(as_text=True)
        everyday = self.client.get("/?theme=default").get_data(as_text=True)
        self.assertIn('<html lang="en" data-theme="winter">', winter)
        self.assertIn('data-season="winter"', winter)
        self.assertIn('href="/seasonal.css"', winter)
        self.assertIn('<html lang="en" data-theme="default">', everyday)
        nav = everyday.split("<nav", 1)[1].split("</nav>", 1)[0]
        self.assertNotIn("data-edit", nav[nav.find("<svg"):nav.find("</svg>") + 6])

    def test_env_off_and_force_and_query_priority(self):
        with patch.dict(os.environ, {"SEASONAL_THEME": "off"}):
            html = self.client.get("/").get_data(as_text=True)
            preview = self.client.get("/?theme=halloween").get_data(as_text=True)
        self.assertIn('data-theme="default"', html)
        self.assertIn('data-theme="halloween"', preview)
        with patch.dict(os.environ, {"SEASONAL_THEME": "winter"}):
            forced = self.client.get("/hub").get_data(as_text=True)
        self.assertIn('data-theme="winter"', forced)
        self.assertNotIn("season-symbol", forced)

    def test_invalid_query_is_not_reflected(self):
        html = self.client.get("/?theme=%3Cscript%3Ealert(1)").get_data(as_text=True)
        tag = html.split("<html", 1)[1].split(">", 1)[0]
        self.assertNotIn("script", tag.lower())
        self.assertRegex(tag, r'data-theme="(halloween|thanksgiving|winter|default)"')

    def test_stylesheet_only_lists_accent_glow_and_symbol_tokens(self):
        css = self.client.get("/seasonal.css")
        self.addCleanup(css.close)
        self.assertEqual(css.status_code, 200)
        body = css.get_data(as_text=True)
        for theme in ("halloween", "thanksgiving", "winter"):
            self.assertIn(':root[data-theme="%s"]' % theme, body)
        self.assertIn("#b8612a", body)
        self.assertIn("#b8792f", body)
        self.assertIn("#1f4a3a", body)
        self.assertIn("#d8c08a", body)
        self.assertIn("#9c7a35", body)
        self.assertNotIn("--bg", body)
        self.assertNotIn("background:", body)
        self.assertNotIn("font", body)
        self.assertNotIn("animation", body)
        self.assertNotIn("@keyframes", body)
        # Snowflake color lives on the svg, not the page background.
        home = self.client.get("/?theme=winter").get_data(as_text=True)
        self.assertIn('stroke="#6f93a3"', home)
