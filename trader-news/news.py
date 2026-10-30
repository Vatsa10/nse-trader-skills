#!/usr/bin/env python3
"""News monitor for trader-news (NSE). Standard library only.

Watches headlines for the NSE stocks you watch or hold and prints only what
is new since the last check, newest first, with how far each stock has moved
today and a flag on anything that touches your symbols.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE HDFCBANK TCS
    python3 news.py --tickers HINDCOPPER --all --hours 72
    python3 news.py --no-google                      # Yahoo headlines only
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources, one request each per symbol, no keys:
  - Yahoo Finance headline RSS, India edition:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - Google News RSS, India edition, searching "<COMPANY or SYMBOL> NSE":
    https://news.google.com/rss/search?q=<COMPANY+or+SYMBOL>+NSE&hl=en-IN&gl=IN&ceid=IN:en
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close, query1 first, then query2 (one retry
    after a pause on a 429, browser user agent):
    https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m
    When Yahoo fails (or with --cache) the move comes from the trader-data
    cache: trader/data/intraday/<SYMBOL>-5m.json, then
    trader/data/daily/<SYMBOL>.csv, with "using cached data from <time>, run
    trader-data intraday SYMBOL to refresh".

When both headline feeds fail for a symbol (blocked, rate limited), or with
--nse, it falls back to NSE's corporate announcements (results, board
meetings, the primary source): a cookie-primed session, GET
https://www.nseindia.com/ first, then
https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>.
If NSE refuses too, it prints the pages to open by hand. Open an
announcement before acting.

Bulk, block and short-selling deals (NSE, one fetch per type for the whole
date range, then filtered to your symbols): shown as items tagged "deal", new
since the last check like any other, with the quantity as a percent of the
20-day average volume from trader/data/daily/<SYMBOL>.adj.csv (or .csv) and a
LARGE flag at 1 % or more, plus the net bought minus sold per named client over
the window (--deal-days, default 30):
https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=DD-MM-YYYY&to=DD-MM-YYYY
(also block_deals, short_selling). An empty list is normal. --no-deals skips it.

Results calendar: NSE's board meetings per symbol,
https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=<SYMBOL>
A purpose with "financial results" is a results date, with "dividend" a dividend
board date. A results date within 2 NSE sessions (weekends and the NSE holidays
excluded: the built-in 2024 to 2026 tables, with trader/data/calendar.json laid
on top when it exists) prints RESULTS SOON at the top: an overnight gap can jump
past an intraday stop. --no-calendar skips it.

FII/DII flows: when trader/data/flows.csv exists (trader-data writes it; header
date,category,buy_cr,sell_cr,net_cr, rupees crore, category FII/FPI or DII) the
briefing prints one line after the results dates: the latest day's FII/FPI and
DII net, and the 5-session net when there are 5 sessions on file. It is market
context, not a signal. A missing or unreadable file prints nothing. --flows PATH
reads another file; --json has it as "flows".

Both ride the NSE session above (one re-prime on a 401/403 or a non-JSON block
page, then a one-line note; the other sources still print).

Times print in IST (UTC+05:30). State: trader/state/news.json remembers what
you have seen (30 days), so an item shows once. Offline: --offline DIR reads
yahoo-<SYMBOL>.xml, google-<SYMBOL>.xml, nse-<SYMBOL>.json, chart-<SYMBOL>.json,
meetings-<SYMBOL>.json and deals-<bulk_deals|block_deals|short_selling>.json
from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import csv
import datetime
import datetime as _dt
import http.cookiejar
import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# --- ipv4 first: keep byte-identical in every script that goes online (tests/test_repo.py checks) ---
# Some networks hand out IPv6 addresses for NSE and Yahoo but drop IPv6 traffic. urllib then waits out the
# full timeout on each IPv6 address before it tries IPv4 (a live run took 40 to 80 s a request). Trying the
# IPv4 addresses first fixes that; IPv6 stays in the list, so an IPv6-only network still connects.
import socket as _socket

if not getattr(_socket.getaddrinfo, "ipv4_first", False):
    _plain_getaddrinfo = _socket.getaddrinfo

    def _getaddrinfo_ipv4_first(*args, **kwargs):
        return sorted(_plain_getaddrinfo(*args, **kwargs), key=lambda info: info[0] != _socket.AF_INET)

    _getaddrinfo_ipv4_first.ipv4_first = True
    _socket.getaddrinfo = _getaddrinfo_ipv4_first
# --- end ipv4 first ---

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
GOOGLE = "https://news.google.com/rss/search?q=%s+NSE&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
CHART_HOSTS = ("query1", "query2")
BACKOFF = 5.0
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
NSE_QUOTE = "https://www.nseindia.com/get-quotes/equity?symbol=%s"
NSE_DEALS = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=%s&from=%s&to=%s"
NSE_MEETINGS = "https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=%s"
DEAL_KINDS = (("bulk_deals", "Bulk deal"), ("block_deals", "Block deal"), ("short_selling", "Short selling"))
DEAL_DAYS = 30      # the window the deals are fetched and netted over (NSE allows up to 365)
DEAL_PCT = 1.0      # a deal this big, as a percent of the 20-day average volume, is flagged LARGE
DEAL_CLOSE = (15, 30)   # deals carry a date only; they are stamped at the close, IST
RESULTS_SOON = 2    # results this many NSE sessions away (or fewer) are a gap risk
FLOWS_HEADER = ["date", "category", "buy_cr", "sell_cr", "net_cr"]
FLOW_CATEGORIES = ("FII/FPI", "DII")
FLOW_SESSIONS = 5   # the longer net window on the flows line
CALENDAR_DAYS = 30  # board-meeting dates further out than this are left off the briefing
DATA_DIR = os.path.join("trader", "data")
DATA_HINT = ("run `python3 trader-data/data.py doctor`; or call Kite MCP get_historical_data and "
             "`python3 trader-data/data.py import-kite`")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
IST = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b|\bQ[1-4]\b|\bnet profit\b|\bPAT\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bantitrust\b|\bCCI\b|\bjudge\b|\bcourt\b|\bNCLT\b|\bED raid|\bincome tax\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus\b|\bstock split\b|\boffering\b|\bQIP\b|\brights issue\b|\bOFS\b|\bIPO\b|\bblock deal\b|\bbulk deal\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint|\bpromoter\b"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b|\bsuspension\b|\bASM\b|\bGSM\b|\bF&O ban\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bclinical\b|\bministry\b|\bPLI\b"),
    ("macro", r"\bRBI policy\b|\brepo rate\b|\bMPC\b|\bCPI\b|\binflation\b|\bGDP\b|\bFII|\bFPI|\bDII|\bbudget\b|\btariffs?\b|\bcrude\b|\brupee\b|\bGIFT Nifty\b|\bFed\b"),
]

# --- nse calendar: keep byte-identical in every script that has it (tests/test_repo.py checks) ---
# The NSE equity-segment trading calendar. Built-in tables for 2024 to 2026 (NSE circulars), then
# trader/data/calendar.json (trader-data: data.py calendar, from NSE's holiday master and its trading
# circulars) laid on top: for a year the file lists, its holidays replace the built-in ones. A missing or
# unreadable file means the built-in tables; nothing here raises.
BUILTIN_HOLIDAYS = {   # weekday closures of the regular 09:15-15:30 market
    "2024-01-22": "Special holiday (Ram Mandir)", "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri", "2024-03-25": "Holi", "2024-03-29": "Good Friday",
    "2024-04-11": "Id-Ul-Fitr (Ramadan Eid)", "2024-04-17": "Shri Ram Navmi",
    "2024-05-01": "Maharashtra Day", "2024-05-20": "Lok Sabha election (Mumbai)",
    "2024-06-17": "Bakri Id", "2024-07-17": "Moharram", "2024-08-15": "Independence Day",
    "2024-10-02": "Mahatma Gandhi Jayanti", "2024-11-01": "Diwali Laxmi Pujan",
    "2024-11-15": "Gurunanak Jayanti", "2024-11-20": "Maharashtra assembly election",
    "2024-12-25": "Christmas",
    "2025-02-26": "Mahashivratri", "2025-03-14": "Holi", "2025-03-31": "Id-Ul-Fitr (Ramadan Eid)",
    "2025-04-10": "Shri Mahavir Jayanti", "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday", "2025-05-01": "Maharashtra Day", "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi", "2025-10-02": "Mahatma Gandhi Jayanti/Dussehra",
    "2025-10-21": "Diwali Laxmi Pujan", "2025-10-22": "Diwali-Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev", "2025-12-25": "Christmas",
    "2026-01-15": "Maharashtra municipal elections (special holiday)", "2026-01-26": "Republic Day",
    "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday", "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id", "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada", "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
BUILTIN_YEARS = (2024, 2025, 2026)
BUILTIN_SPECIAL = {    # normal-hours sessions on a weekend or a holiday
    "2024-01-20": "Saturday special session", "2024-03-02": "Saturday DR session",
    "2024-05-18": "Saturday DR session", "2025-02-01": "Union Budget (Saturday)",
    "2026-02-01": "Union Budget (Sunday)",
}
BUILTIN_MUHURAT = {    # one evening hour at times set by circular: the regular market is closed that day
    "2023-11-12": "Diwali Laxmi Pujan (Muhurat), Sunday", "2024-11-01": "Diwali Laxmi Pujan (Muhurat)",
    "2025-10-21": "Diwali Laxmi Pujan (Muhurat)", "2026-11-08": "Diwali Laxmi Pujan (Muhurat), Sunday",
}
NSE_HOLIDAYS = dict(BUILTIN_HOLIDAYS)    # these four hold the built-in tables plus calendar.json
SPECIAL_SESSIONS = dict(BUILTIN_SPECIAL)
MUHURAT_SESSIONS = dict(BUILTIN_MUHURAT)
CALENDAR_YEARS = BUILTIN_YEARS
CALENDAR_PATH = os.path.join("trader", "data", "calendar.json")


def _cal_dates(m):
    """{"2027-01-26": "Republic Day"} with bad keys dropped; anything but an object is corrupt."""
    if m is None:
        return {}
    if not isinstance(m, dict):
        raise ValueError("not an object")
    out = {}
    for k, v in m.items():
        try:
            datetime.date.fromisoformat(k)
        except (TypeError, ValueError):
            continue
        out[k] = str(v)
    return out


def load_calendar(path=None):
    """trader/data/calendar.json -> {"years", "holidays", "special_sessions", "fetched_at",
    "last_circular_check", "warnings"}, or None when it is missing or unreadable."""
    try:
        with open(path or CALENDAR_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return None
        warns = doc.get("warnings")
        return {"years": sorted(set(int(y) for y in (doc.get("years") or []))),
                "holidays": _cal_dates(doc.get("holidays")),
                "special_sessions": _cal_dates(doc.get("special_sessions")),
                "fetched_at": doc.get("fetched_at"), "last_circular_check": doc.get("last_circular_check"),
                "warnings": [str(w) for w in warns] if isinstance(warns, list) else []}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def is_muhurat(name):
    low = str(name).lower()
    return "muhurat" in low or "laxmi pujan" in low


def apply_calendar(path=None):
    """Rebuild NSE_HOLIDAYS, SPECIAL_SESSIONS, MUHURAT_SESSIONS and CALENDAR_YEARS from the built-in
    tables plus calendar.json (path None = CALENDAR_PATH). Returns the file's contents, or None."""
    global CALENDAR_YEARS
    cal = load_calendar(path)
    for table, base in ((NSE_HOLIDAYS, BUILTIN_HOLIDAYS), (SPECIAL_SESSIONS, BUILTIN_SPECIAL),
                        (MUHURAT_SESSIONS, BUILTIN_MUHURAT)):
        table.clear()
        table.update(base)
    CALENDAR_YEARS = BUILTIN_YEARS
    if not cal:
        return None
    for y in cal["years"]:
        for iso in [k for k in NSE_HOLIDAYS if k.startswith("%04d-" % y)]:
            del NSE_HOLIDAYS[iso]
    NSE_HOLIDAYS.update(cal["holidays"])
    for iso, name in cal["special_sessions"].items():
        SPECIAL_SESSIONS.pop(iso, None)
        MUHURAT_SESSIONS.pop(iso, None)
        (MUHURAT_SESSIONS if is_muhurat(name) else SPECIAL_SESSIONS)[iso] = name
    CALENDAR_YEARS = tuple(sorted(set(BUILTIN_YEARS) | set(cal["years"])))
    return cal


def years_text():
    return ", ".join(str(y) for y in CALENDAR_YEARS)


def trading_day(d):
    """(open, reason) for the regular 09:15-15:30 market on date d. A normal-hours special session is
    open; a weekend, a listed holiday and a Muhurat day are closed. Outside CALENDAR_YEARS the weekday
    rule is a guess, which calendar_known says."""
    iso = d.isoformat()
    if iso in MUHURAT_SESSIONS:
        return False, ("special session: %s, about an hour in the evening at times set by NSE; "
                       "no normal 09:15-15:30 market" % MUHURAT_SESSIONS[iso])
    if iso in SPECIAL_SESSIONS:
        return True, ""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def calendar_known(d):
    return d.year in CALENDAR_YEARS


def bars_expected(d):
    """Does NSE publish a daily bar for d? Every regular trading day, and a Muhurat session too."""
    return trading_day(d)[0] or d.isoformat() in MUHURAT_SESSIONS


apply_calendar()   # at import, from trader/data/calendar.json in the working directory when it exists
# --- end nse calendar ---


class NewsError(Exception):
    pass


def show_path(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


class NseWall(NewsError):
    """NSE's bot wall: HTTP 401/403. One fresh session is worth a try."""


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive IST wall-clock time (fixed +05:30, no DST)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[norm_symbol(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(norm_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            sym = norm_symbol(sym)
            out[sym] = p.get("name") or out.get(sym)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._nse = None

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % show_path(path))
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))
        except OSError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc))

    def get_nse(self, ticker):
        """NSE corporate announcements for one symbol (see nse_json)."""
        return self.nse_json(NSE_API % urllib.parse.quote(ticker), "nse-%s.json" % ticker, ticker)

    def nse_json(self, url, name, ticker):
        """One NSE API call on the cookie-primed session (the home page first, as a
        browser does). A 401/403 or a body that is not JSON is NSE's bot wall: the
        session is dropped and primed again, once; then it raises."""
        if self.offline:
            return self.get(None, name)
        for attempt in (0, 1):
            try:
                data = self._nse_get(url, ticker)
                json.loads(data.decode("utf-8"))   # a block page is HTML
                return data
            except (ValueError, NseWall):
                self._nse = None
                if attempt:
                    raise NewsError("NSE keeps refusing (a block page or HTTP 401/403 even after a fresh session)")

    def _nse_get(self, url, ticker):
        gap = time.time() - self._last   # polite: spaced from whatever call came before
        if gap < self.pause:
            time.sleep(self.pause - gap)
        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*",
                   "Accept-Language": "en-IN,en;q=0.9", "Referer": NSE_QUOTE % urllib.parse.quote(ticker)}
        try:
            if self._nse is None:
                self._nse = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                self._nse.open(urllib.request.Request(NSE_HOME, headers=dict(headers, Accept="text/html")),
                               timeout=30).read()
                time.sleep(self.pause)
            self._last = time.time()
            with self._nse.open(urllib.request.Request(url, headers=headers), timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            self._nse = None
            if exc.code in (401, 403):
                raise NseWall("NSE answered HTTP %s" % exc.code)
            raise NewsError("NSE answered HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            self._nse = None
            raise NewsError("could not reach nseindia.com: %s" % (getattr(exc, "reason", exc),))


def norm_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if not SYMBOL.match(s):
        raise NewsError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS; M&M -> M%26M.NS (URL-quoted)."""
    return norm_symbol(sym).replace("&", "%26") + ".NS"


def google_query(sym, name=None):
    """The Google News search words: the company name if known, else the symbol."""
    words = (name or norm_symbol(sym)).strip()
    words = re.sub(r"\s+(?:Limited|Ltd\.?)$", "", words, flags=re.I)
    return urllib.parse.quote_plus(words)


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_now(value):
    """--now: an ISO time; without an offset it is IST. -> aware UTC, or None."""
    value = (value or "").strip()
    try:
        d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = (d - IST).replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 (Atom) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker, source="Yahoo Finance"):
    """Headline RSS (Yahoo or Google News) -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        if source == "Google News":
            src = _text(it, "source")
            if src and title.endswith(" - " + src):
                title = title[:-len(src) - 3]
        out.append({"id": "rss:" + guid, "source": source, "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def parse_nse(data, ticker):
    """NSE corporate-announcements JSON (a list, or {"data": [...]}) -> items. NSE times are IST."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE sent something that is not JSON for %s (maybe a block page)" % ticker)
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise NewsError("NSE's announcements for %s are not the shape this tool knows" % ticker)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        when = None
        for key, fmt in (("sort_date", "%Y-%m-%d %H:%M:%S"), ("an_dt", "%d-%b-%Y %H:%M:%S")):
            try:
                when = (_dt.datetime.strptime(str(r.get(key) or "").strip(), fmt) - IST).replace(
                    tzinfo=_dt.timezone.utc)
                break
            except ValueError:
                continue
        title = " ".join(str(r.get("desc") or "Announcement").split())
        detail = " ".join(str(r.get("attchmntText") or "").split())
        link = r.get("attchmntFile") or NSE_QUOTE % urllib.parse.quote(ticker)
        key = r.get("seq_id") or link or (title + str(r.get("an_dt")))
        out.append({"id": "nse:%s" % key, "source": "NSE", "kind": "announcement", "ticker": ticker,
                    "title": (title + (": " + detail[:160] if detail else "")), "summary": detail,
                    "link": link, "time": when})
    return out


def _rows(data, what):
    """An NSE JSON body (a list, or {"data": [...]}) -> its rows; an empty list is fine."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE sent something that is not JSON for %s (maybe a block page)" % what)
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise NewsError("NSE's %s are not the shape this tool knows" % what)
    return [r for r in rows if isinstance(r, dict)]


def parse_date(value):
    """NSE date text (07-Oct-2026, 07-10-2026, 2026-10-07, any with a time after) -> date or None."""
    value = str(value or "").strip()
    for text in (value, value.split(" ")[0], value.split("T")[0]):
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _qty(value):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def parse_deals(data, kind):
    """NSE bulk/block/short-selling JSON -> [{"kind", "symbol", "date", "client", "side", "qty", "price",
    "remarks"}]. Bulk and block rows are BD_*; short selling rows are SS_* and name no client."""
    out = []
    for r in _rows(data, "%s deals" % kind):
        sym = str(r.get("BD_SYMBOL") or r.get("SS_SYMBOL") or "").strip().upper()
        qty = _qty(r.get("BD_QTY_TRD", r.get("SS_QTY")))
        if not sym or not qty:
            continue
        side = str(r.get("BD_BUY_SELL") or "").strip().upper()[:1]
        try:
            price = float(str(r.get("BD_TP_WATP")).replace(",", ""))
        except ValueError:
            price = None
        remarks = " ".join(str(r.get("BD_REMARKS") or "").split())
        out.append({"kind": kind, "symbol": sym, "date": parse_date(r.get("BD_DT_DATE") or r.get("SS_DATE")),
                    "client": " ".join(str(r.get("BD_CLIENT_NAME") or "").split()) or None,
                    "side": {"B": "BUY", "S": "SELL"}.get(side), "qty": qty, "price": price,
                    "remarks": "" if remarks == "-" else remarks})
    return out


def avg_volume(sym, before=None, days=20, cache=None):
    """The 20-day average volume from the trader-data cache (.adj.csv first, then .csv), over
    the sessions before the date given (else the latest ones); None when there is no cache."""
    cache = {} if cache is None else cache
    if sym not in cache:
        cache[sym] = None
        for name in ("%s.adj.csv", "%s.csv"):
            path = os.path.join(DATA_DIR, "daily", name % sym)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    cache[sym] = [(r["date"], float(r["volume"])) for r in csv.DictReader(fh)
                                  if (r.get("volume") or "").strip()]
                break
            except (OSError, ValueError, KeyError):
                continue
    rows = cache[sym]
    if not rows:
        return None
    vols = [v for d, v in rows if before is None or d < before.isoformat()] or [v for d, v in rows]
    vols = vols[-days:]
    avg = sum(vols) / len(vols)
    return avg if avg > 0 else None


def deal_items(rows, watch):
    """Deal rows on your symbols -> items tagged "deal" (the same shape the feeds give), each with
    its quantity as a percent of the 20-day average volume when the cache has one."""
    cache, out = {}, []
    for d in rows:
        if d["symbol"] not in watch:
            continue
        avg = avg_volume(d["symbol"], d["date"], cache=cache)
        pct = round(d["qty"] / avg * 100, 2) if avg else None
        large = pct is not None and pct >= DEAL_PCT
        if d["side"]:
            what = "%s %s shares" % ({"BUY": "bought", "SELL": "sold"}[d["side"]], format(d["qty"], ","))
            if d["client"]:
                what = "%s %s" % (d["client"], what)
        else:
            what = "%s shares disclosed short" % format(d["qty"], ",")
        title = "%s: %s%s%s%s" % (d["kind"], what, (" at " + inr(d["price"])) if d["price"] else "",
                                  (" (%s)" % d["remarks"]) if d["remarks"] else "",
                                  (" [%.2f%% of 20-day avg volume%s]" % (pct, ", LARGE" if large else ""))
                                  if pct is not None else "")
        when = None
        if d["date"]:
            when = (_dt.datetime.combine(d["date"], _dt.time(*DEAL_CLOSE)) - IST).replace(tzinfo=_dt.timezone.utc)
        key = "|".join(str(x) for x in (d["kind"], d["date"], d["symbol"], d["client"], d["side"], d["qty"], d["price"]))
        out.append({"id": "deal:" + key, "source": "NSE", "kind": "deal", "ticker": d["symbol"], "title": title,
                    "summary": "", "link": "", "time": when,
                    "deal": {"kind": d["kind"], "client": d["client"], "side": d["side"], "qty": d["qty"],
                             "price": d["price"], "date": d["date"].isoformat() if d["date"] else None,
                             "pct_of_avg_volume": pct, "large": large}})
    return out


def deal_flow(rows, watch):
    """Net bought minus sold per named client per symbol over the fetched window (bulk and block
    deals; short selling names no one). Biggest absolute net first."""
    net, cache, out = {}, {}, []
    for d in rows:
        if d["symbol"] in watch and d["client"] and d["side"]:
            n = net.setdefault((d["symbol"], d["client"]), {"bought": 0, "sold": 0})
            n["bought" if d["side"] == "BUY" else "sold"] += d["qty"]
    for (sym, client), n in net.items():
        avg = avg_volume(sym, cache=cache)
        total = n["bought"] - n["sold"]
        out.append({"symbol": sym, "client": client, "bought": n["bought"], "sold": n["sold"], "net": total,
                    "pct_of_avg_volume": round(abs(total) / avg * 100, 2) if avg else None})
    out.sort(key=lambda f: (-abs(f["net"]), f["symbol"], f["client"]))
    return out


def fetch_deals(fetcher, watch, now, days=DEAL_DAYS, errors=None):
    """One fetch per deal type for the whole date range (not per symbol), kept to your symbols.
    An empty list is normal. If NSE blocks, one note and the rest are skipped."""
    errors = [] if errors is None else errors
    today = to_ist(now).date()
    start = today - _dt.timedelta(days=max(1, min(int(days), 365)))
    rows = []
    for option, kind in DEAL_KINDS:
        url = NSE_DEALS % (option, start.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y"))
        try:
            rows += [d for d in parse_deals(fetcher.nse_json(url, "deals-%s.json" % option, next(iter(sorted(watch)))),
                                            kind) if d["symbol"] in watch]
        except NewsError as exc:
            if fetcher.offline and "no file" in str(exc):
                continue
            errors.append("NSE bulk/block/short-selling deals: %s; deals not checked, the rest is unaffected" % exc)
            break
    return rows


def sessions_between(start, end):
    """NSE sessions after start up to and including end: 0 for the same day, 1 for the next
    session. Negative when end is before start."""
    if end < start:
        return -sessions_between(end, start)
    return sum(1 for i in range(1, (end - start).days + 1) if trading_day(start + _dt.timedelta(days=i))[0])


def parse_meetings(data, ticker):
    """NSE corporate-board-meetings JSON -> [{"ticker", "date", "purpose", "results", "dividend"}].
    A purpose with "financial results" is a results date, one with "dividend" a dividend board date."""
    out = []
    for r in _rows(data, "board meetings for %s" % ticker):
        sym = str(r.get("bm_symbol") or ticker).strip().upper()
        when = parse_date(r.get("bm_date"))
        if sym != ticker or when is None:
            continue
        purpose = " ".join(str(r.get("bm_purpose") or r.get("bm_desc") or "").split())
        low = purpose.lower()
        out.append({"ticker": ticker, "date": when, "purpose": purpose,
                    "results": "financial results" in low, "dividend": "dividend" in low})
    return out


def collect_calendar(fetcher, watch, now, errors=None):
    """Results and dividend board dates from today on, for your symbols, soonest first. A results
    date within RESULTS_SOON sessions is "soon". If NSE blocks: one note, the rest is skipped."""
    errors = [] if errors is None else errors
    today = to_ist(now).date()
    if not calendar_known(today):
        errors.append("holiday calendar does not cover %s; counting weekends only for results dates" % today.year)
    out = []
    for t in sorted(watch):
        try:
            meetings = parse_meetings(fetcher.nse_json(NSE_MEETINGS % urllib.parse.quote(t), "meetings-%s.json" % t, t), t)
        except NewsError as exc:
            if fetcher.offline and "no file" in str(exc):
                continue
            errors.append("NSE board meetings: %s; results dates not checked, the rest is unaffected" % exc)
            break
        for m in meetings:
            if m["date"] < today or not (m["results"] or m["dividend"]):
                continue
            away = sessions_between(today, m["date"])
            m = dict(m, sessions_away=away, soon=m["results"] and away <= RESULTS_SOON)
            if m["soon"] or (m["date"] - today).days <= CALENDAR_DAYS:
                out.append(m)
    out.sort(key=lambda m: (m["date"], m["ticker"]))
    return out


def render_calendar(events):
    """RESULTS SOON lines first (gap risk), then the other dates coming up."""
    out = []
    for m in sorted(events, key=lambda m: (not m["soon"], m["date"], m["ticker"])):
        what = " and ".join((["results"] if m["results"] else []) + (["dividend board date"] if m["dividend"] else []))
        away = m["sessions_away"]
        when = "%s (%s), %s" % (m["date"].isoformat(), m["date"].strftime("%a"),
                                "today" if away == 0 else "%d session%s away" % (away, "" if away == 1 else "s"))
        if m["soon"]:
            out.append("RESULTS SOON  %-11s %s %s: gap risk, an overnight gap can jump past an intraday stop" % (
                m["ticker"], what, when))
        else:
            out.append("coming up     %-11s %s %s" % (m["ticker"], what, when))
    return out


def load_flows(path):
    """trader/data/flows.csv (date,category,buy_cr,sell_cr,net_cr; rupees crore) -> {"date",
    "fii_net_cr", "dii_net_cr", "sessions", "fii_net_cr_5d", "dii_net_cr_5d"} for the latest day, or
    None when the file is missing, unreadable or has no usable row. The 5-session nets are None until
    each category has 5 days on file. Never raises: this line is context, not a signal."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        if not rows or [c.strip() for c in rows[0]] != FLOWS_HEADER:
            return None
        net = {c: {} for c in FLOW_CATEGORIES}
        for r in rows[1:]:
            if not r:
                continue
            day, cat = _dt.date.fromisoformat(r[0].strip()).isoformat(), r[1].strip()
            if cat in net:
                value = float(r[4])
                if value != value or value in (float("inf"), float("-inf")):
                    return None
                net[cat][day] = value
    except (OSError, ValueError, IndexError, UnicodeError, csv.Error):
        return None
    days = sorted(set(net["FII/FPI"]) | set(net["DII"]))
    if not days:
        return None
    last = days[-1]
    out = {"date": last, "fii_net_cr": net["FII/FPI"].get(last), "dii_net_cr": net["DII"].get(last),
           "sessions": FLOW_SESSIONS}
    for key, cat in (("fii_net_cr_5d", "FII/FPI"), ("dii_net_cr_5d", "DII")):
        recent = sorted(net[cat])[-FLOW_SESSIONS:]
        out[key] = round(sum(net[cat][d] for d in recent), 2) if len(recent) == FLOW_SESSIONS else None
    return out


def render_flows(flows):
    """One context line: the latest day's FII/FPI and DII net, then the 5-session net."""
    if not flows:
        return []

    def cr(v):
        return "n/a" if v is None else format(v, "+,.2f")
    line = "FII/DII %s (Rs crore): FII/FPI net %s, DII net %s" % (
        flows["date"], cr(flows["fii_net_cr"]), cr(flows["dii_net_cr"]))
    if flows["fii_net_cr_5d"] is not None or flows["dii_net_cr_5d"] is not None:
        line += "; %d sessions: FII/FPI %s, DII %s" % (
            flows["sessions"], cr(flows["fii_net_cr_5d"]), cr(flows["dii_net_cr_5d"]))
    return [line + ". Market context, not a signal."]


def manual_links(ticker):
    q = urllib.parse.quote(ticker)
    return ("open by hand: %s (NSE announcements), https://finance.yahoo.com/quote/%s.NS/news, "
            "https://news.google.com/search?q=%s%%20NSE&hl=en-IN&gl=IN&ceid=IN:en" % (NSE_QUOTE % q, q, q))


def cached_move(ticker, why=None):
    """The move from the trader-data cache: intraday JSON first, then the daily CSV."""
    fail = ("live fetch failed (%s); " % why) if why else ""
    ipath = os.path.join(DATA_DIR, "intraday", "%s-5m.json" % ticker)
    dpath = os.path.join(DATA_DIR, "daily", "%s.csv" % ticker)
    if os.path.isfile(ipath):
        try:
            with open(ipath, encoding="utf-8") as fh:
                res = json.load(fh)["chart"]["result"][0]
            meta = res.get("meta") or {}
            pairs = [(t, c) for t, c in zip(res.get("timestamp") or [],
                                            ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
                     if t is not None and c is not None]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if pairs and not prev and os.path.isfile(dpath):   # a Kite import has no previous close
                day = to_ist(_dt.datetime.fromtimestamp(pairs[-1][0], _dt.timezone.utc)).date().isoformat()
                with open(dpath, encoding="utf-8") as fh:
                    before = [r for r in csv.DictReader(fh) if (r.get("close") or "").strip() and r["date"] < day]
                prev = float(before[-1]["close"]) if before else None
            if pairs and prev:
                t, price = pairs[-1]
                when = _dt.datetime.fromtimestamp(t, _dt.timezone.utc)
                return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
                        "pct": round((float(price) / float(prev) - 1) * 100, 2), "time": when,
                        "note": "%susing cached data from %s, run `python3 trader-data/data.py intraday %s` to "
                                "refresh" % (fail, to_ist(when).strftime("%Y-%m-%d %H:%M IST"), ticker)}
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            pass
    if os.path.isfile(dpath):
        with open(dpath, encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get("close") or "").strip()]
        if len(rows) >= 2:
            price, prev = float(rows[-1]["close"]), float(rows[-2]["close"])
            return {"ticker": ticker, "price": round(price, 2), "prev_close": round(prev, 2),
                    "pct": round((price / prev - 1) * 100, 2), "time": None,
                    "note": "%susing cached data from the %s daily close, run `python3 trader-data/data.py daily "
                            "%s` to refresh" % (fail, rows[-1]["date"], ticker)}
    raise NewsError("%sNo cache at %s or %s either. Next: %s" % (("%s. " % why) if why else "", show_path(ipath),
                                                                 show_path(dpath), DATA_HINT))


def fetch_move(fetcher, t):
    """query1, then query2; a 429 gets one retry after BACKOFF seconds."""
    name = "chart-%s.json" % t
    if fetcher.offline:
        return parse_chart(fetcher.get(None, name), t)
    last = None
    for host in CHART_HOSTS:
        for attempt in (0, 1):
            try:
                return parse_chart(fetcher.get(CHART % (host, yahoo_symbol(t)), name), t)
            except NewsError as exc:
                last = exc
                if "HTTP 429" in str(exc) and attempt == 0:
                    time.sleep(BACKOFF)
                    continue
                break
        if last is not None and re.search(r"HTTP 4(?!29)\d\d", str(last)):
            break
    raise last


def collect_moves(fetcher, watch, errors=None, cache=False):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        try:
            if cache and not fetcher.offline:
                m = cached_move(t)
            else:
                try:
                    m = fetch_move(fetcher, t)
                except NewsError as exc:
                    if fetcher.offline:
                        raise
                    m = cached_move(t, str(exc))
            if m.get("note"):
                errors.append("price for %s: %s" % (t, m.pop("note")))
            out.append(m)
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(t),
                r"\b%s\.NS\b" % re.escape(t)]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "deal" and "deal" not in it["tags"]:   # a short-selling row never says "deal"
            it["tags"].insert(0, "deal")
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, state, google=True, errors=None, nse=False):
    """Headlines per symbol. When every headline feed fails for a symbol (or with nse=True),
    NSE's corporate announcements; when NSE fails too, the pages to open by hand."""
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        ok = False
        try:
            items += parse_rss(fetcher.get(YAHOO % yahoo_symbol(t), "yahoo-%s.xml" % t), t)
            ok = True
        except NewsError as exc:
            errors.append("Yahoo headlines for %s: %s" % (t, exc))
        if google:
            try:
                items += parse_rss(fetcher.get(GOOGLE % google_query(t, watch.get(t)), "google-%s.xml" % t),
                                   t, "Google News")
                ok = True
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("Google News for %s: %s" % (t, exc))
        if ok and not nse:
            continue
        try:
            items += parse_nse(fetcher.get_nse(t), t)
        except NewsError as exc:
            if fetcher.offline and "no file" in str(exc):
                continue
            errors.append("NSE announcements for %s: %s; %s" % (t, exc, manual_links(t)))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def inr(x):
    """1234567.891 -> "₹12,34,567.89" (Indian digit grouping)."""
    whole, frac = divmod(int(round(abs(float(x)) * 100)), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail, groups = s[:-3], s[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if float(x) < 0 else "") + "₹" + s + ".%02d" % frac


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["ticker"], inr(m["price"]), m["pct"], inr(m["prev_close"]),
            when, flag))
    return out


def render_flow(flow):
    """Net bought minus sold per client: one line each."""
    out = []
    for f in flow:
        pct = ("; %.2f%% of 20-day avg volume" % f["pct_of_avg_volume"]) if f["pct_of_avg_volume"] is not None else ""
        out.append("  %-11s %-30s net %s shares (bought %s, sold %s%s)" % (
            f["symbol"], f["client"][:30], format(f["net"], "+,"), format(f["bought"], ","), format(f["sold"], ","), pct))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, flow=None, calendar=None, flows=None):
    out = render_calendar(calendar or []) + render_flows(flows)
    if out:
        out.append("")
    moved = render_moves(moves or [], move_pct)
    out += moved
    if moved:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("  %s  %-11s %-13s %s" % (when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        if it["link"]:
            extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    if flow:
        out.append("Net deals by client (bought minus sold, bulk and block, over the window):")
        out += render_flow(flow)
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-google", action="store_true", help="Yahoo headlines only")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--nse", action="store_true", help="also read NSE corporate announcements for every symbol")
    ap.add_argument("--no-deals", action="store_true", help="skip NSE bulk, block and short-selling deals")
    ap.add_argument("--deal-days", type=int, default=DEAL_DAYS,
                    help="deals window in days, also the net buy/sell window (max 365)")
    ap.add_argument("--no-calendar", action="store_true", help="skip NSE board meetings (results dates)")
    ap.add_argument("--calendar", default=os.path.join(DATA_DIR, "calendar.json"),
                    help="trader-data holiday calendar, laid over the built-in tables to count sessions")
    ap.add_argument("--flows", default=os.path.join(DATA_DIR, "flows.csv"),
                    help="trader-data FII/DII flows (rupees crore), printed as one context line when it exists")
    ap.add_argument("--cache", action="store_true",
                    help="moves from the trader-data cache (trader/data/intraday, then daily), not Yahoo")
    ap.add_argument("--now", help="ISO time, for reproducible runs (no offset = IST)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.tickers:
            watch = {norm_symbol(t): None for t in args.tickers}
            known = watch_from_files(args.portfolio, args.watchlist, args.plan)
            watch.update({t: known[t] for t in watch if known.get(t)})
        else:
            watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    if not watch:
        print("news: no symbols. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_now(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    if now is None:
        print("news: --now wants a time like 2026-10-19T10:15 (IST)", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, state, google=not args.no_google, errors=errors, nse=args.nse)
    deals = [] if args.no_deals else fetch_deals(fetcher, watch, now, args.deal_days, errors)
    items = dedupe(tag(items + deal_items(deals, watch), watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    flow = deal_flow(deals, watch)
    # the net view goes with the deals you are being shown (all of it with --all)
    flow_shown = [f for f in flow if args.all or any(
        i["kind"] == "deal" and i["ticker"] == f["symbol"] for i in shown)]
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors, cache=args.cache)
    events = []
    if not args.no_calendar:
        if apply_calendar(args.calendar) is None and os.path.exists(args.calendar):
            errors.append("%s is not valid JSON; using the built-in holiday tables" % show_path(args.calendar))
        events = collect_calendar(fetcher, watch, now, errors)
    flows = load_flows(args.flows)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        ev = [dict(e, date=e["date"].isoformat()) for e in events]
        print(json.dumps({"checked": now.isoformat(), "results_soon": [e["ticker"] for e in events if e["soon"]],
                          "calendar": ev, "moves": mv, "items": _json_ready(shown), "deal_flow": flow,
                          "flows": flows, "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, flow_shown, events, flows))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
