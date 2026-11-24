"""Seasonal look for the public pages.

The theme is chosen from the America/New_York calendar date:

  halloween      October 15 through October 31
  thanksgiving   November 1 through Cyber Monday
                 (the fourth Thursday of November, plus four days)
  winter         the day after Cyber Monday through January 1
                 (frost and snow only)
  default        every other day

SEASONAL_THEME selects the mode: auto (the default), off (always the
everyday look), or a theme name to force. A ?theme= query on a single
request previews one theme and does not change the mode.
"""
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

THEMES = ("halloween", "thanksgiving", "winter", "default")

STYLESHEET_TAG = '<link rel="stylesheet" href="/seasonal.css">'

# Inline size keeps the mark's layout put even before the stylesheet applies.
# Display is left to seasonal.css, which shows only the active symbol.
_SYMBOL_STYLE = "position:absolute;width:12px;height:12px;right:-3px;bottom:-2px;pointer-events:none"

_SYMBOLS = (
    '<svg class="season-symbol" data-season="halloween" viewBox="0 0 16 16" '
    'aria-hidden="true" focusable="false" style="' + _SYMBOL_STYLE + '">'
    '<ellipse cx="5.7" cy="10.15" rx="2.05" ry="3.15" fill="#d06828"/>'
    '<ellipse cx="10.3" cy="10.15" rx="2.05" ry="3.15" fill="#e88942"/>'
    '<ellipse cx="8" cy="9.95" rx="2.55" ry="3.45" fill="#e07a34"/>'
    '<path d="M8.15 6.15c.35-.95.95-1.6 1.7-1.9" fill="none" stroke="#3d6a48" '
    'stroke-width="1.15" stroke-linecap="round"/>'
    "</svg>"
    '<svg class="season-symbol" data-season="thanksgiving" viewBox="0 0 16 16" '
    'aria-hidden="true" focusable="false" style="' + _SYMBOL_STYLE + '">'
    '<path fill="#b8792f" d="M2 8.6C3.6 5 7.2 3.2 13.2 3.4c-.2 3.2-1.8 5.6-4.2 6.8L10.4 13 8.2 11.4C6.6 12.2 4.6 12 3.4 10.8 4.2 10 4.6 9.2 4.6 9.2 3.2 9.6 2.2 9.2 2 8.6z"/>'
    '<path d="M4.2 8.2C6.4 7.2 8.6 6 11.4 4.6" fill="none" stroke="#f6edd4" stroke-width=".6" stroke-linecap="round"/>'
    "</svg>"
    '<svg class="season-symbol" data-season="winter" viewBox="0 0 16 16" '
    'aria-hidden="true" focusable="false" style="' + _SYMBOL_STYLE + '">'
    '<g fill="none" stroke="#6f93a3" stroke-width="1.25" stroke-linecap="round">'
    '<path d="M8 1.7v12.6M2.6 4.7l10.8 6.6M13.4 4.7 2.6 11.3"/>'
    '<path d="M8 4.15 6.85 2.85M8 4.15l1.15-1.3M8 11.85 6.85 13.15M8 11.85l1.15 1.3"/>'
    "</g>"
    '<circle cx="8" cy="8" r="1.05" fill="#6f93a3"/>'
    "</svg>"
)

_HTML_OPEN = re.compile(r"<html\b([^>]*)>", re.I)
_DATA_THEME_ATTR = re.compile(r"""\sdata-theme\s*=\s*(?:"[^"]*"|'[^']*')""", re.I)
_NAV_MARK = re.compile(
    r"(<nav\b[^>]*>.*?<span class=\"mark\"[^>]*>\s*<span>M</span>)",
    re.I | re.S,
)


def _fourth_thursday_of_november(year):
    first = date(year, 11, 1)
    offset = (3 - first.weekday()) % 7  # Thursday
    return first + timedelta(days=offset + 21)


def cyber_monday(year):
    """Monday after Thanksgiving: the fourth Thursday of November plus four days."""
    return _fourth_thursday_of_november(year) + timedelta(days=4)


def theme_for_date(day):
    """Theme name for a calendar day. Time of day does not matter."""
    if isinstance(day, datetime):
        day = day.date()
    if day.month == 10 and 15 <= day.day <= 31:
        return "halloween"
    if day.month == 1 and day.day == 1:
        return "winter"
    if day.month in (11, 12):
        end = cyber_monday(day.year)
        if date(day.year, 11, 1) <= day <= end:
            return "thanksgiving"
        if day > end:
            return "winter"
    return "default"


def eastern_today(now=None):
    """Calendar date in America/New_York."""
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    return now.date()


def _named_theme(value):
    if value is None:
        return None
    name = str(value).strip().lower()
    if name in THEMES:
        return name
    return None


def _env_mode(env):
    if env is None:
        env = os.environ.get("SEASONAL_THEME")
    return (env or "auto").strip().lower()


def current_theme(day=None, env=None, preview=None, now=None):
    """Theme for this request.

    preview (the ?theme= value) wins when it names a theme. Otherwise
    SEASONAL_THEME is auto, off, or a forced theme name. auto uses the
    Eastern date.
    """
    chosen = _named_theme(preview)
    if chosen:
        return chosen
    mode = _env_mode(env)
    if mode == "off":
        return "default"
    if mode in THEMES:
        return mode
    if day is None:
        day = eastern_today(now)
    return theme_for_date(day)


def stamp_html(html, theme):
    """Set data-theme and add the nav symbols. No data-edit attributes."""
    if theme not in THEMES:
        theme = "default"

    def _open(match):
        attrs = _DATA_THEME_ATTR.sub("", match.group(1))
        return '<html%s data-theme="%s">' % (attrs, theme)

    html = _HTML_OPEN.sub(_open, html, count=1)

    def _mark(match):
        return match.group(1) + _SYMBOLS

    return _NAV_MARK.sub(_mark, html, count=1)
