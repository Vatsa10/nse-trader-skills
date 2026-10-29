#!/usr/bin/env python3
"""Market data layer for the NSE trader pack (trader-data). Standard library only.

Free sources are unofficial and get blocked, so every command tries a chain of
sources, says exactly which one failed and why, and also accepts data you
saved yourself (Kite MCP JSON, Kite Console / NSE website CSV).

    python3 data.py daily RELIANCE TCS [--years 3|max] [--source auto|yahoo|nse|bhavcopy]
    python3 data.py intraday RELIANCE [--days 60]
    python3 data.py bands [RELIANCE ...]
    python3 data.py import-kite kite-hist.json --kind historical --symbol RELIANCE
    python3 data.py import-kite kite-quotes.json --kind quotes
    python3 data.py import-csv RELIANCE.csv --symbol RELIANCE [--adjusted]
    python3 data.py calendar [--refresh]
    python3 data.py actions RELIANCE TCS [--years 3] [--refresh]
    python3 data.py adjust RELIANCE TCS [--force]
    python3 data.py chain NIFTY [--expiry 29-Oct-2026]
    python3 data.py index ["NIFTY 50" "NIFTY BANK" ...] [--days 30] [--years 1] [--pe-expensive 25 --pe-cheap 20]
    python3 data.py delivery RELIANCE TCS [--window 30] [--no-fetch]
    python3 data.py doctor
    python3 data.py record RELIANCE [--date 2026-10-23]
    python3 data.py status

Writes (shared contracts, see PORT_V4_SPEC.md):
  trader/data/daily/<SYM>.csv        date,open,high,low,close,volume (unadjusted)
  trader/data/daily/<SYM>.adj.csv    same header, split and bonus adjusted
  trader/data/intraday/<SYM>-5m.json Yahoo v8 chart JSON shape, 5 minute bars
  trader/data/bands.json             {"as_of","source","bands":{SYM:{band_pct,lower,upper}},"unknown":[...]}
  trader/data/sources.json           doctor's per-source health
  trader/data/cache_meta.json        where each cached file came from and when
  trader/data/calendar.json          {"fetched_at","years","holidays","special_sessions","last_circular_check","warnings"}
  trader/data/delivery/<SYM>.csv     date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series (blank = unknown)
  trader/data/delivery/pending.json  {"pending": [dates whose delivery file is not published yet]}
  trader/data/actions.json           {"fetched_at","symbols":{SYM:{"from","fetched_through","actions":[...]}}}
  trader/data/chain-<SYMBOL>.json    option chain summary: underlying, expiry, PCR, OI walls, ATM, max pain
  trader/data/index/<slug>.csv       date,open,high,low,close,volume,pe,pb,div_yield (nifty-50, nifty-bank, india-vix)

NSE requests (www.nseindia.com, nsearchives) retry up to 5 times on a 429, 502, 503, 504
or a timeout, waiting 2^n seconds with jitter (never over 8 s, or the Retry-After the server
sends); a 404 on an archive file is final. Cookies persist in trader/data/nse_cookies.txt.

Daily chain (auto): Yahoo query1 -> Yahoo query2 -> NSE JSON API (cookie
primed) -> NSE bhavcopy archives (one file per trading day). Only dates after
the last cached session are fetched (--fill-gaps also refetches holes).
Every write is validated: OHLC sanity, no duplicate dates, ascending dates,
gaps checked against the NSE trading calendar (built-in 2024 to 2026, any other year
from trader/data/calendar.json, which the calendar command keeps up to date).
"""

import argparse
import csv
import datetime
import email.utils
import gzip
import http.client
import http.cookiejar
import importlib.util
import io
import json
import os
import random
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
BROWSER = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip",
           "Connection": "keep-alive"}
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
NSE = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"
NIFTY_LIST = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
GNEWS = "https://news.google.com/rss/search?q=RELIANCE%20NSE&hl=en-IN&gl=IN&ceid=IN:en"
KITE_MCP = "https://mcp.kite.trade/mcp"
SEC_LIST = ARCHIVES + "/content/equities/sec_list.csv"
PAUSE = 0.6          # seconds between requests to the same site
BHAV_PAUSE = 1.0     # bhavcopy archive: one file a second
RETRIES = 2          # extra tries per Yahoo host on a 429 / 5xx
BACKOFF = 2.0        # first back-off in seconds, doubles each try
NSE_RETRIES = 5      # extra tries on an NSE host after a 429 / 502 / 503 / 504 or a timeout
RETRY_STATUS = (429, 502, 503, 504)
RETRY_CAP = 8.0      # longest wait before an NSE retry, Retry-After included
COOKIE_FILE = "nse_cookies.txt"
COOKIE_FRESH = 1800  # seconds: a saved cookie file this young skips the home page prime
UDIFF_FROM = datetime.date(2024, 7, 8)   # before this day NSE published only the legacy bhavcopy
MONTH_UP = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NSE_CHUNK_DAYS = 100
TIMEOUT = 20
IST = datetime.timedelta(hours=5, minutes=30)
OPEN_T = datetime.time(9, 15)
CLOSE_T = datetime.time(15, 30)
BARS_PER_SESSION = 75
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
HEADER = ["date", "open", "high", "low", "close", "volume"]
STD_BANDS = (2, 5, 10, 20)
CALENDAR_FILE = "calendar.json"
CAL_REFERER = NSE + "/resources/exchange-communication-holidays"
CIRCULAR_FIRST_DAYS = 5     # the first circular check looks this far back
CIRCULAR_TAG = "Circular: "  # marks a calendar.json entry that came from a circular, not the holiday list
MAX_WARNINGS = 20
DELIVERY_HEADER = ["date", "volume", "trades", "deliv_qty", "deliv_pct", "qty_per_trade", "series"]
DELIVERY_SERIES = ("EQ", "BE", "BZ")
T2T_SERIES = ("BE", "BZ")     # trade-to-trade: every share is delivered
DELIVERY_WINDOW = 30          # sessions the means are taken over
DELIVERY_MIN = 5              # fewer delivery values than this is "not enough delivery data"
IM_LEVEL = 1.2                # institutional money: DQ and TQ both above this
PENDING_DAYS = 5              # a delivery file still missing after this many days is given up on
ACTIONS_FILE = "actions.json"
ACTIONS_REFERER = NSE + "/companies-listing/corporate-filings-actions"
ACTION_SERIES = ("EQ", "BE", "BZ")
ACTION_CHUNK_DAYS = 365       # one corporate-actions request covers at most this many days
ACTION_OVERLAP = 7            # a refetch starts this many days before fetched_through
ACTION_LOOKAHEAD = 30         # days past today asked for, so announced ex-dates are known early
ACTIONS_STALE_DAYS = 7        # adjust warns when actions were fetched this long before the last bar
SPLIT_WORDS = ("split", "splt", "consolidation")
BONUS_SKIP = ("deb", "pref", "ncrps", "dvr")   # debenture, preference and DVR "bonus" is not a share bonus
VERIFY_LOW, VERIFY_HIGH = 0.67, 1.5           # the adjusted close on the ex-date over the day before
INDEX_DIR = "index"
INDEX_HEADER = ["date", "open", "high", "low", "close", "volume", "pe", "pb", "div_yield"]
INDEX_DEFAULT = ("NIFTY 50", "NIFTY BANK", "NIFTY 500")   # what `index` keeps when no name is given
INDEX_DAYS = 30               # snapshot files fetched for an index with nothing cached yet
INDEX_REFERER = NSE + "/reports-indices-historical-index-data"
VIX_REFERER = NSE + "/reports-indices-historical-vix"
VIX_NAME = "India VIX"
VALUATION_INDEX = "NIFTY 50"
PE_EXPENSIVE = 25.0           # Nifty 50 P/E at or above this is flagged expensive (eod2's rule of thumb)
PE_CHEAP = 20.0               # at or below this, cheap
PE_MIN_ROWS = 250             # P/E rows needed before a percentile means anything
REPORTS_REFERER = NSE + "/all-reports"
CHAIN_REFERER = NSE + "/option-chain"
INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")   # option-chain type=Indices
PCR_HIGH, PCR_LOW = 1.2, 0.7  # PCR above / below these reads supportive / weak (a hint, not a signal)
VIX_ELEVATED, VIX_CALM = 1.15, 0.85   # India VIX against its recent average
VIX_AVG_ROWS = 20
REPORT_KEYS = {"sec_bhavdata_full": "CM-BHAVDATA-FULL", "BhavCopy_NSE_CM": "CM-UDIFF-BHAVCOPY-CSV"}

# ---------------------------------------------------------------- NSE trading calendar
# 2026 comes from trader-alerts/alerts.py (loaded below, with this copy as the fallback).
NSE_HOLIDAYS_2026_COPY = {
    "2026-01-15": "Maharashtra municipal elections (special holiday)",
    "2026-01-26": "Republic Day", "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti", "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada", "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
# Special closure announced later: NSE circular CMTR72260 (Jan 2026), Maharashtra municipal elections.
NSE_EXTRA_2026 = {"2026-01-15": "Maharashtra municipal elections (special holiday)"}
# 2025: NSE circular 170/2024, NSE/CMTR/65587, 13 Dec 2024
# (https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf). Oct 21 is a holiday
# with a Muhurat session, listed in SPECIAL_SESSIONS so its bar is accepted.
NSE_HOLIDAYS_2025 = {
    "2025-02-26": "Mahashivratri", "2025-03-14": "Holi", "2025-03-31": "Id-Ul-Fitr (Ramadan Eid)",
    "2025-04-10": "Shri Mahavir Jayanti", "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday", "2025-05-01": "Maharashtra Day", "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi", "2025-10-02": "Mahatma Gandhi Jayanti/Dussehra",
    "2025-10-21": "Diwali Laxmi Pujan (Muhurat session)", "2025-10-22": "Diwali-Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev", "2025-12-25": "Christmas",
}
# 2024: NSE circular 154/2023, NSE/CMTR/59722, 12 Dec 2023, plus three special closures
# announced during the year (Jan 22, May 20, Nov 20). Cross-checked against bhavcopy history.
NSE_HOLIDAYS_2024 = {
    "2024-01-22": "Special holiday (Ram Mandir)", "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri", "2024-03-25": "Holi", "2024-03-29": "Good Friday",
    "2024-04-11": "Id-Ul-Fitr (Ramadan Eid)", "2024-04-17": "Shri Ram Navmi",
    "2024-05-01": "Maharashtra Day", "2024-05-20": "Lok Sabha election (Mumbai)",
    "2024-06-17": "Bakri Id", "2024-07-17": "Moharram", "2024-08-15": "Independence Day",
    "2024-10-02": "Mahatma Gandhi Jayanti", "2024-11-01": "Diwali Laxmi Pujan (Muhurat session)",
    "2024-11-15": "Gurunanak Jayanti", "2024-11-20": "Maharashtra assembly election",
    "2024-12-25": "Christmas",
}
# Sessions on a weekend or a holiday: a bar on these dates is real, not an error.
SPECIAL_SESSIONS = {
    "2023-11-12": "Muhurat (Sunday)", "2024-01-20": "Saturday special session",
    "2024-03-02": "Saturday DR session", "2024-05-18": "Saturday DR session",
    "2024-11-01": "Muhurat", "2025-02-01": "Budget day (Saturday)", "2025-10-21": "Muhurat",
    "2026-02-01": "Budget day (Sunday)", "2026-11-08": "Muhurat (Sunday)",
}


def _alerts_holidays():
    path = os.path.join(os.path.dirname(HERE), "trader-alerts", "alerts.py")
    try:
        spec = importlib.util.spec_from_file_location("trader_alerts_for_data", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        got = {k: v for k, v in getattr(mod, "BUILTIN_HOLIDAYS", mod.NSE_HOLIDAYS).items()
               if k.startswith("2026-")}
        return got or dict(NSE_HOLIDAYS_2026_COPY)
    except Exception:
        return dict(NSE_HOLIDAYS_2026_COPY)


NSE_HOLIDAYS_2026 = _alerts_holidays()
HOLIDAYS = {}
for _h in (NSE_HOLIDAYS_2024, NSE_HOLIDAYS_2025, NSE_HOLIDAYS_2026, NSE_EXTRA_2026):
    HOLIDAYS.update(_h)
CALENDAR_YEARS = (2024, 2025, 2026)


class DataError(Exception):
    pass


class SourceError(DataError):
    def __init__(self, source, message, hint="", transient=False):
        DataError.__init__(self, message)
        self.source, self.hint, self.transient = source, hint, transient   # transient: worth a retry

    def __str__(self):
        msg = DataError.__str__(self)
        return msg + (" (%s)" % self.hint if self.hint else "")


_CAL = None    # calendar.json as load_calendar reads it; None means the built-in tables only


def _iso_map(m):
    """{"2027-01-26": "Republic Day"} with bad keys dropped; a non-object is corrupt."""
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


def load_calendar(data_dir):
    """<data_dir>/calendar.json -> dict, or None when it is missing or corrupt (never raises)."""
    doc = _read_json(os.path.join(data_dir, CALENDAR_FILE), None)
    if not isinstance(doc, dict):
        return None
    try:
        warns = doc.get("warnings")
        return {"years": sorted(set(int(y) for y in (doc.get("years") or []))),
                "holidays": _iso_map(doc.get("holidays")),
                "special_sessions": _iso_map(doc.get("special_sessions")),
                "fetched_at": doc.get("fetched_at"), "last_circular_check": doc.get("last_circular_check"),
                "warnings": [str(w) for w in warns] if isinstance(warns, list) else []}
    except (TypeError, ValueError, AttributeError):
        return None


def calendar_known(year):
    return year in CALENDAR_YEARS or bool(_CAL and year in _CAL["years"])


def closed_name(iso):
    return (_CAL and _CAL["holidays"].get(iso)) or HOLIDAYS.get(iso, "weekend")


def trading_day(d):
    """Is NSE open on d? A special session is open, a weekend is closed, then calendar.json (a listed
    holiday is closed, any other day of a year it covers is open), then the built-in tables."""
    iso = d.isoformat()
    cal = _CAL
    if iso in SPECIAL_SESSIONS or (cal and iso in cal["special_sessions"]):
        return True
    if d.weekday() >= 5:
        return False
    if cal:
        if iso in cal["holidays"]:
            return False
        if d.year in cal["years"]:
            return True
    return iso not in HOLIDAYS


def trading_days(start, end):
    out, d = [], start
    while d <= end:
        if trading_day(d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def prev_trading_day(d):
    d -= datetime.timedelta(days=1)
    while not trading_day(d):
        d -= datetime.timedelta(days=1)
    return d


# ---------------------------------------------------------------- time (IST, fixed +05:30)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def parse_now(text):
    """--now "2026-10-23 16:30" (IST, or with an offset) -> naive IST datetime."""
    if not text:
        return utc_now() + IST
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise DataError("--now wants a time like \"2026-10-23 16:30\" (IST)")
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None) + IST
    return dt


def ts_to_ist(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).replace(tzinfo=None) + IST


def ist_to_ts(dt):
    return int((dt - IST).replace(tzinfo=datetime.timezone.utc).timestamp())


def last_complete_session(now_ist):
    """The latest session whose EOD data should exist (bhavcopy appears in the evening)."""
    d = now_ist.date()
    if trading_day(d) and d.weekday() < 5 and now_ist.time() >= datetime.time(18, 0):
        return d
    return prev_trading_day(d)


def stamp(now_ist=None):
    n = (now_ist - IST) if now_ist else utc_now()
    return n.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- parsing helpers

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                           "sep", "oct", "nov", "dec"])}


def parse_date(text):
    """yyyy-mm-dd, dd-mm-yyyy, dd/mm/yyyy, dd-Mon-yyyy, dd Mon yyyy, ISO with time -> date."""
    s = str(text or "").strip().strip('"').strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T ].*)?$", s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})(?:[T ].*)?$", s)
    if m:   # Indian order: day first
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"^(\d{1,2})[- ]([A-Za-z]{3})[A-Za-z]*[- ,]+(\d{4})(?:[T ].*)?$", s)
    if m and m.group(2).lower() in MONTHS:
        return datetime.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.match(r"^([A-Za-z]{3})[A-Za-z]* (\d{1,2}),? (\d{4})$", s)
    if m and m.group(1).lower() in MONTHS:
        return datetime.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    raise DataError("cannot read the date %r" % text)


def parse_iso_ist(text):
    """Kite timestamps: 2026-10-23T09:15:00+0530 / +05:30 / Z / naive (IST) -> naive IST."""
    s = str(text).strip().replace(" ", "T", 1)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s).replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        raise DataError("cannot read the time %r" % text)
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None) + IST
    return dt


def num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("₹", "")
    if s in ("", "-", "--", "NA", "N/A", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(x):
    if x is None:
        return ""
    if isinstance(x, int):
        return str(x)
    s = ("%.4f" % x).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def norm_symbol(sym):
    s = str(sym or "").strip().upper()
    for suf in (".NS", "-EQ"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    if s.startswith("NSE:"):
        s = s[4:]
    if not SYMBOL.match(s):
        raise DataError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    return s


def bar(date, o, h, low, c, v):
    return {"date": date if isinstance(date, str) else date.isoformat(), "open": float(o), "high": float(h),
            "low": float(low), "close": float(c), "volume": int(round(v)) if v is not None else 0}


# ---------------------------------------------------------------- validation

def ohlc_problem(o, h, low, c):
    vals = (o, h, low, c)
    if any(v is None for v in vals):
        return "missing price"
    if min(vals) <= 0:
        return "price <= 0"
    eps = 1e-6
    if low > min(o, c) + eps or h < max(o, c) - eps:
        return "low/high do not contain open and close (o=%s h=%s l=%s c=%s)" % tuple(fmt(v) for v in vals)
    return None


def compress(dates, limit=8):
    s = [d.isoformat() if hasattr(d, "isoformat") else d for d in dates]
    return ", ".join(s[:limit]) + (" and %d more" % (len(s) - limit) if len(s) > limit else "")


def validate_daily(rows):
    """(errors, warnings). Errors block a write; warnings are reported."""
    errors, warnings = [], []
    seen, prev = set(), None
    for r in rows:
        d = r["date"]
        try:
            day = datetime.date.fromisoformat(d)
        except (TypeError, ValueError):
            errors.append("bad date %r" % d)
            continue
        if d in seen:
            errors.append("duplicate date %s" % d)
        seen.add(d)
        if prev and d <= prev:
            errors.append("dates not ascending at %s (after %s)" % (d, prev))
        prev = d
        p = ohlc_problem(r["open"], r["high"], r["low"], r["close"])
        if p:
            errors.append("%s: %s" % (d, p))
        if r.get("volume") is not None and r["volume"] < 0:
            errors.append("%s: negative volume" % d)
        if not trading_day(day) and calendar_known(day.year):
            warnings.append("%s has a bar but NSE was closed (%s)" % (d, closed_name(d)))
    if rows and not errors:
        first = datetime.date.fromisoformat(rows[0]["date"])
        last = datetime.date.fromisoformat(rows[-1]["date"])
        known = [x for x in trading_days(first, last) if calendar_known(x.year) and x.isoformat() not in seen]
        unknown = [x for x in trading_days(first, last) if not calendar_known(x.year)
                   and x.isoformat() not in seen]
        if known:
            warnings.append("gap: %d NSE trading day(s) missing: %s" % (len(known), compress(known)))
        if unknown:
            yrs = sorted(set(x.year for x in unknown))
            warnings.append("%d weekday(s) without a bar in %s (no holiday list for those years, not checked: "
                            "run data.py calendar)" % (len(unknown), ", ".join(map(str, yrs))))
        for a, b in zip(rows, rows[1:]):
            ratio = a["close"] / b["open"] if b["open"] else 0
            for k in (2, 3, 4, 5, 10):
                if abs(ratio - k) / k < 0.06:
                    warnings.append("%s opens at 1/%d of the previous close: a split or bonus? "
                                    "(.adj.csv needs a Yahoo refresh)" % (b["date"], k))
    return errors, warnings


def validate_intraday(bars):
    """bars: [(ts, o, h, l, c, v)] -> (errors, warnings)."""
    errors, warnings, prev = [], [], None
    per_day = {}
    for b in bars:
        ts = b[0]
        t = ts_to_ist(ts)
        if prev is not None and ts <= prev:
            errors.append("timestamps not ascending at %s" % t)
        prev = ts
        if t.minute % 5 or t.second:
            errors.append("%s is not on a 5 minute boundary" % t)
        if not (OPEN_T <= t.time() < CLOSE_T):
            warnings.append("%s is outside 09:15-15:30 IST" % t)
        p = ohlc_problem(b[1], b[2], b[3], b[4])
        if p:
            errors.append("%s: %s" % (t, p))
        per_day[t.date()] = per_day.get(t.date(), 0) + 1
    short = [d for d, n in sorted(per_day.items()) if n != BARS_PER_SESSION]
    if short:
        warnings.append("%d session(s) without %d bars: %s" % (
            len(short), BARS_PER_SESSION, compress(["%s (%d)" % (d, per_day[d]) for d in short])))
    return errors, warnings


# ---------------------------------------------------------------- cache files

def data_paths(data_dir, sym):
    return (os.path.join(data_dir, "daily", sym + ".csv"), os.path.join(data_dir, "daily", sym + ".adj.csv"),
            os.path.join(data_dir, "intraday", sym + "-5m.json"))


def read_daily(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append(bar(r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                                float(r["close"]), num(r.get("volume")) or 0))
            except (KeyError, TypeError, ValueError):
                raise DataError("%s has a bad row: %r" % (path, r))
    return rows


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_daily(path, rows):
    """Validate, then write. Returns the warnings; raises DataError on errors."""
    rows = sorted(rows, key=lambda r: r["date"])
    errors, warnings = validate_daily(rows)
    if errors:
        raise DataError("refusing to write %s: %s" % (path, "; ".join(errors[:5])))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r["date"], fmt(r["open"]), fmt(r["high"]), fmt(r["low"]), fmt(r["close"]),
                    str(int(r["volume"] or 0))])
    _atomic_write(path, buf.getvalue())
    return warnings


def merge_rows(old, new):
    by = {r["date"]: r for r in old}
    for r in new:
        by[r["date"]] = r
    return [by[d] for d in sorted(by)]


def split_valid(rows):
    good, bad = [], []
    for r in rows:
        p = ohlc_problem(r["open"], r["high"], r["low"], r["close"])
        (bad if p else good).append((r, p))
    return [r for r, _ in good], ["%s: %s" % (r["date"], p) for r, p in bad]


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def _write_json(path, doc):
    _atomic_write(path, json.dumps(doc, indent=1, sort_keys=False) + "\n")


def note_meta(data_dir, kind, sym, info):
    path = os.path.join(data_dir, "cache_meta.json")
    meta = _read_json(path, {})
    meta.setdefault(kind, {})[sym] = info
    _write_json(path, meta)


def read_intraday(path):
    """-> (meta, [(ts, o, h, l, c, v)]) from a Yahoo-shaped chart file."""
    doc = _read_json(path, None)
    if not doc:
        return {}, []
    return parse_chart(doc)


def parse_chart(doc):
    try:
        res = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = ((doc or {}).get("chart") or {}).get("error") or {}
        raise DataError("not a chart: %s" % (err.get("description") or "no chart.result"))
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    out = []
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    for i, ts in enumerate(res.get("timestamp") or []):
        vals = [c[i] if i < len(c) else None for c in cols]
        if any(v is None for v in vals[:4]):
            continue
        out.append((int(ts), float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]),
                    None if vals[4] is None else int(vals[4])))
    return res.get("meta", {}) or {}, out


def build_chart(sym, bars, meta=None, source=""):
    bars = sorted(bars)
    m = dict(meta or {})
    m.update({"currency": m.get("currency") or "INR", "symbol": m.get("symbol") or sym + ".NS",
              "exchangeName": m.get("exchangeName") or "NSI", "instrumentType": "EQUITY",
              "gmtoffset": 19800, "timezone": "IST", "exchangeTimezoneName": "Asia/Kolkata",
              "dataGranularity": "5m", "source": source or m.get("source", "")})
    if bars:
        m["regularMarketPrice"] = bars[-1][4]
        m["regularMarketTime"] = bars[-1][0] + 300
    return {"chart": {"result": [{"meta": m, "timestamp": [b[0] for b in bars], "indicators": {"quote": [{
        "open": [b[1] for b in bars], "high": [b[2] for b in bars], "low": [b[3] for b in bars],
        "close": [b[4] for b in bars], "volume": [b[5] for b in bars]}]}}], "error": None}}


def regular_session(bars):
    return [b for b in bars if OPEN_T <= ts_to_ist(b[0]).time() < CLOSE_T]


def write_intraday(path, sym, new_bars, meta=None, source="", keep_sessions=60):
    old_meta, old = read_intraday(path)
    by = {b[0]: b for b in old}
    for b in regular_session(new_bars):
        by[b[0]] = b
    bars = [by[k] for k in sorted(by)]
    days = sorted(set(ts_to_ist(b[0]).date() for b in bars))[-keep_sessions:]
    keep = set(days)
    bars = [b for b in bars if ts_to_ist(b[0]).date() in keep]
    errors, warnings = validate_intraday(bars)
    if errors:
        raise DataError("refusing to write %s: %s" % (path, "; ".join(errors[:5])))
    m = dict(old_meta)
    m.update(meta or {})
    _write_json(path, build_chart(sym, bars, m, source))
    return bars, warnings


def to_5m(points):
    """[(naive IST datetime, o, h, l, c, v|None)] at any finer step -> 5 minute bars (ts UTC)."""
    buckets = {}
    for dt, o, h, low, c, v in sorted(points, key=lambda p: p[0]):
        if not (OPEN_T <= dt.time() < CLOSE_T):
            continue
        start = dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)
        b = buckets.get(start)
        if b is None:
            buckets[start] = [o, h, low, c, v]
        else:
            b[1], b[2], b[3] = max(b[1], h), min(b[2], low), c
            b[4] = None if (b[4] is None or v is None) else b[4] + v
    return [(ist_to_ts(k), v[0], v[1], v[2], v[3], v[4]) for k, v in sorted(buckets.items())]


# ---------------------------------------------------------------- HTTP

_transport = None    # tests: fn(url, headers, timeout) -> (status, bytes) or (status, bytes, response headers)
_sleep = time.sleep
_clock = time.monotonic
_jitter = random.random    # tests: fn() -> a number in [0, 1)


def retry_after(headers):
    """Retry-After (seconds or an HTTP date) -> seconds >= 0, or None when absent or unreadable."""
    value = None
    for k, v in (headers or {}).items():
        if str(k).lower() == "retry-after":
            value = str(v).strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds())


def retry_wait(attempt, headers=None):
    """Seconds to wait before retry number `attempt` (1, 2, ...): the Retry-After if the server sent
    one, else 2^attempt with full random jitter. Never more than RETRY_CAP."""
    sent = retry_after(headers)
    if sent is not None:
        return min(RETRY_CAP, sent)
    return min(RETRY_CAP, 2.0 ** attempt) * _jitter()


class Session(object):
    """One cookie jar per run (saved in the data folder when one is given), browser-like headers,
    gzip handled, and retries with back-off on NSE hosts."""

    def __init__(self, data_dir=None):
        self.cookie_path = os.path.join(data_dir, COOKIE_FILE) if data_dir else None
        self.jar = (http.cookiejar.MozillaCookieJar(self.cookie_path) if self.cookie_path
                    else http.cookiejar.CookieJar())
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.nse_primed = self.load_cookies()
        self.last = {}
        self.last_headers = {}

    def load_cookies(self):
        """Reload the saved cookies (expired ones dropped, session cookies kept). True when they are
        NSE cookies young enough to skip the home page prime."""
        if not self.cookie_path or not os.path.exists(self.cookie_path):
            return False
        try:
            self.jar.load(ignore_discard=True, ignore_expires=False)
            age = time.time() - os.path.getmtime(self.cookie_path)
        except (OSError, ValueError):
            self.jar.clear()    # an unreadable file is a cold start, never a crash
            return False
        return age < COOKIE_FRESH and any("nseindia.com" in c.domain for c in self.jar)

    def save_cookies(self):
        if not self.cookie_path or not len(self.jar):
            return
        try:
            os.makedirs(os.path.dirname(self.cookie_path) or ".", exist_ok=True)
            self.jar.save(ignore_discard=True, ignore_expires=True)
        except OSError:
            pass

    def polite(self, host, gap):
        t = self.last.get(host)
        if t is not None and gap:
            wait = gap - (_clock() - t)
            if wait > 0:
                _sleep(wait)
        self.last[host] = _clock()

    def get(self, url, headers=None, timeout=TIMEOUT, gap=PAUSE, retries=None):
        """(status, body). NSE hosts retry up to NSE_RETRIES times on a 429 / 502 / 503 / 504 or a
        timeout or dropped connection; any other status (a 404 on an archive file above all) comes
        straight back. Other hosts do not retry here (Yahoo has its own loop)."""
        host = urllib.parse.urlsplit(url).netloc
        if retries is None:
            retries = NSE_RETRIES if host.endswith("nseindia.com") else 0
        h = dict(BROWSER)
        h.update(headers or {})
        attempt = 0
        while True:
            self.polite(host, gap)
            try:
                code, body, reply = self.once(url, h, timeout, host)
            except SourceError as e:
                if not (e.transient and attempt < retries):
                    if attempt:
                        raise SourceError(e.source, "%s (gave up after %d retries)" % (DataError.__str__(e), attempt),
                                          e.hint)
                    raise
                wait = retry_wait(attempt + 1)
            else:
                if code not in RETRY_STATUS or attempt >= retries:
                    self.last_headers = reply
                    return code, body
                wait = retry_wait(attempt + 1, reply)
            attempt += 1
            _sleep(wait)

    def once(self, url, h, timeout, host):
        """One request -> (status, body, response headers)."""
        if _transport is not None:
            got = _transport(url, h, timeout)
            return got[0], got[1], (got[2] if len(got) > 2 else {})
        req = urllib.request.Request(url, headers=h)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                body = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                return r.status, body, dict(r.headers.items())
        except urllib.error.HTTPError as e:
            try:
                body = e.read()
            except Exception:
                body = b""
            finally:
                try:
                    e.close()
                except Exception:
                    pass
            return e.code, body, dict(e.headers.items()) if e.headers else {}
        except socket.timeout:
            raise SourceError(host, "%s timed out after %ss" % (host, timeout), "network or firewall",
                              transient=True)
        except http.client.HTTPException as e:
            # the server dropped or garbled the connection: start from a clean opener, keep the cookies
            self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
            raise SourceError(host, "%s broke the connection: %s" % (host, e), "network or firewall",
                              transient=True)
        except (urllib.error.URLError, OSError) as e:
            why = str(getattr(e, "reason", e))
            proxy = "403" in why or "407" in why
            tip = ("a proxy or firewall on this network blocks %s; use Kite MCP or import-csv" % host
                   if proxy else "no network, DNS or firewall")
            raise SourceError(host, "could not reach %s: %s" % (host, why), tip, transient=not proxy)


def hint(source, code):
    if source.startswith("nse") or source.startswith("bhav") or source == "nsearchives":
        if code == 403:
            return "403: NSE blocks this IP (cloud, VPN and datacenter IPs usually are); use Kite MCP or import-csv"
        if code == 401:
            return "401: NSE wants fresh browser cookies; try again, or use Kite MCP"
        if code == 404:
            return "404: not published (holiday, weekend, before ~18:00 IST, or a wrong symbol)"
    if source.startswith("yahoo"):
        if code == 429:
            return "429: Yahoo rate limit; wait a few minutes"
        if code in (401, 403):
            return "%d: Yahoo refused this client (cookie/crumb wall or blocked IP); use Kite MCP or import-csv" % code
        if code == 404:
            return "404: Yahoo does not know this ticker (.NS)"
    if code == 403:
        return "403: blocked by the site or a proxy"
    if code >= 500:
        return "%d: the site is having trouble; try later" % code
    return "HTTP %d" % code


def _json(body, source, what):
    try:
        return json.loads(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
    except ValueError:
        raise SourceError(source, "%s did not return JSON (a bot wall or an error page)" % what,
                          "use Kite MCP or import-csv")


# ---------------------------------------------------------------- Yahoo

def yahoo_symbol(sym):
    return urllib.parse.quote(sym) + ".NS"


def yahoo_url(host, sym, interval, rng=None, p1=None, p2=None):
    q = "interval=%s&includePrePost=false&events=div%%2Csplit" % interval
    q += ("&range=%s" % rng) if rng else ("&period1=%d&period2=%d" % (p1, p2))
    return "https://%s/v8/finance/chart/%s?%s" % (host, yahoo_symbol(sym), q)


def fetch_yahoo(sess, sym, interval, rng=None, p1=None, p2=None, hosts=YAHOO_HOSTS):
    """query1 then query2, each with retries and back-off on a 429 / 5xx. -> chart dict."""
    errs = []
    for host in hosts:
        src = "yahoo-q%s" % host[5]
        url = yahoo_url(host, sym, interval, rng, p1, p2)
        wait = BACKOFF
        for attempt in range(RETRIES + 1):
            try:
                code, body = sess.get(url, {"Accept": "application/json"})
            except SourceError as e:
                errs.append("%s: %s" % (src, e))
                break
            if code == 200:
                doc = _json(body, src, "Yahoo")
                if not (doc.get("chart") or {}).get("result"):
                    err = (doc.get("chart") or {}).get("error") or {}
                    raise SourceError(src, "Yahoo has no data for %s.NS: %s" % (
                        sym, err.get("description", "empty result")))
                return doc
            if code == 404:
                raise SourceError(src, "Yahoo does not know %s.NS" % sym, hint(src, 404))
            if code == 429 or code >= 500:
                if attempt < RETRIES:
                    _sleep(wait)
                    wait *= 2
                    continue
            errs.append("%s: HTTP %d (%s)" % (src, code, hint(src, code)))
            break
    raise SourceError("yahoo", "; ".join(errs))


def parse_yahoo_daily(doc):
    """-> (unadjusted rows, split/bonus-adjusted rows, splits [(date, factor)]).

    Yahoo's daily OHLC is already split and bonus adjusted (a 1:1 bonus is a 2:1 split
    there). The unadjusted series multiplies back every split after each date."""
    res = doc["chart"]["result"][0]
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    splits = []
    for ev in ((res.get("events") or {}).get("splits") or {}).values():
        try:
            f = float(ev["numerator"]) / float(ev["denominator"])
            splits.append((ts_to_ist(ev["date"]).date(), f))
        except (KeyError, ValueError, ZeroDivisionError, TypeError):
            continue
    splits.sort()
    adj, raw = [], []
    for i, ts in enumerate(res.get("timestamp") or []):
        try:
            o, h, low, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        except (KeyError, IndexError):
            continue
        if None in (o, h, low, c):
            continue
        d = ts_to_ist(ts).date()
        adj.append(bar(d, round(o, 4), round(h, 4), round(low, 4), round(c, 4), v or 0))
        f = 1.0
        for sd, sf in splits:
            if sd > d:
                f *= sf
        raw.append(bar(d, round(o * f, 2), round(h * f, 2), round(low * f, 2), round(c * f, 2),
                       (v or 0) / f))
    # Yahoo can send today's partial bar twice; keep the last per date
    return merge_rows([], raw), merge_rows([], adj), splits


# ---------------------------------------------------------------- NSE website (cookie primed)

def nse_prime(sess, force=False):
    if sess.nse_primed and not force:
        return
    code, _ = sess.get(NSE + "/", {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                   "Referer": NSE + "/", "Upgrade-Insecure-Requests": "1"})
    if code != 200:
        raise SourceError("nse-home", "www.nseindia.com home page answered HTTP %d" % code, hint("nse", code))
    sess.nse_primed = True
    sess.save_cookies()


def nse_api(sess, path, referer):
    nse_prime(sess)
    hdr = {"Accept": "application/json, text/plain, */*", "Referer": referer, "X-Requested-With": "XMLHttpRequest"}
    code, body = sess.get(NSE + path, hdr)
    if code == 401:
        nse_prime(sess, force=True)
        code, body = sess.get(NSE + path, hdr)
    if code != 200:
        raise SourceError("nse-api", "NSE %s answered HTTP %d" % (path.split("?")[0], code), hint("nse", code))
    return _json(body, "nse-api", "NSE " + path.split("?")[0])


def nse_hist_path(sym, start, end):
    return "/api/historical/cm/equity?symbol=%s&series=%s&from=%s&to=%s" % (
        urllib.parse.quote(sym), urllib.parse.quote('["EQ"]'), start.strftime("%d-%m-%Y"),
        end.strftime("%d-%m-%Y"))


def parse_nse_hist(doc):
    rows = {}
    for r in (doc.get("data") if isinstance(doc, dict) else doc) or []:
        series = str(r.get("CH_SERIES", "EQ")).strip()
        try:
            d = parse_date(r.get("CH_TIMESTAMP") or r.get("mTIMESTAMP"))
        except DataError:
            continue
        vals = [num(r.get(k)) for k in ("CH_OPENING_PRICE", "CH_TRADE_HIGH_PRICE", "CH_TRADE_LOW_PRICE",
                                         "CH_CLOSING_PRICE", "CH_TOT_TRADED_QTY")]
        if None in vals[:4]:
            continue
        if d.isoformat() in rows and series != "EQ":
            continue
        rows[d.isoformat()] = bar(d, *vals)
    return [rows[k] for k in sorted(rows)]


def fetch_nse_daily(sess, sym, start, end):
    out, d = [], start
    ref = NSE + "/get-quotes/equity?symbol=" + urllib.parse.quote(sym)
    while d <= end:
        e = min(end, d + datetime.timedelta(days=NSE_CHUNK_DAYS - 1))
        out = merge_rows(out, parse_nse_hist(nse_api(sess, nse_hist_path(sym, d, e), ref)))
        d = e + datetime.timedelta(days=1)
    return out


def nse_quote(sess, sym):
    return nse_api(sess, "/api/quote-equity?symbol=" + urllib.parse.quote(sym),
                   NSE + "/get-quotes/equity?symbol=" + urllib.parse.quote(sym))


def parse_nse_chart(doc):
    """chart-databyindex -> 5m bars. Its ms timestamps are IST wall time written as UTC."""
    pts = doc.get("grapthData") or doc.get("graphData") or []
    out = []
    for p in pts:
        try:
            ms, price = p[0], float(p[1])
        except (IndexError, TypeError, ValueError):
            continue
        dt = datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc).replace(tzinfo=None)
        if not (datetime.time(8, 0) <= dt.time() <= datetime.time(16, 30)):
            dt += IST   # a true UTC stamp after all
        out.append((dt, price, price, price, price, None))
    return to_5m(out)


# ---------------------------------------------------------------- NSE bhavcopy archives

def bhav_urls(d):
    """The delivery bhavcopy first, then the plain one: UDiFF from 2024-07-08, the legacy
    cmDDMONYYYYbhav file before that."""
    full = ARCHIVES + "/products/content/sec_bhavdata_full_%s.csv" % d.strftime("%d%m%Y")
    if d >= UDIFF_FROM:
        return [full, ARCHIVES + "/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip" % d.strftime("%Y%m%d")]
    mon = MONTH_UP[d.month - 1]
    return [full, ARCHIVES + "/content/historical/EQUITIES/%d/%s/cm%02d%s%dbhav.csv.zip" % (
        d.year, mon, d.day, mon, d.year)]


def _key(h):
    return " ".join(str(h or "").replace("﻿", "").strip().lower().split())


def parse_bhav(text, want=None):
    """sec_bhavdata_full or the UDiFF CM bhavcopy -> {SYMBOL: bar}. EQ series preferred."""
    rd = csv.reader(io.StringIO(text))
    try:
        head = [_key(h) for h in next(rd)]
    except StopIteration:
        return {}
    pick = lambda *names: next((head.index(n) for n in names if n in head), None)  # noqa: E731
    ix = {"sym": pick("symbol", "tckrsymb"), "ser": pick("series", "sctysrs"),
          "date": pick("date1", "traddt", "timestamp"), "o": pick("open_price", "opnpric", "open"),
          "h": pick("high_price", "hghpric", "high"), "l": pick("low_price", "lwpric", "low"),
          "c": pick("close_price", "clspric", "close"), "v": pick("ttl_trd_qnty", "ttltradgvol", "tottrdqty")}
    if None in (ix["sym"], ix["o"], ix["h"], ix["l"], ix["c"], ix["date"]):
        raise DataError("not a bhavcopy (columns: %s)" % ", ".join(head[:8]))
    out = {}
    for r in rd:
        if len(r) < len(head):
            continue
        sym = r[ix["sym"]].strip().upper()
        if want and sym not in want:
            continue
        ser = r[ix["ser"]].strip().upper() if ix["ser"] is not None else "EQ"
        if ser not in ("EQ", "BE", "BZ", "SM", "ST"):
            continue
        if sym in out and ser != "EQ":
            continue
        vals = [num(r[ix[k]]) for k in ("o", "h", "l", "c")]
        if None in vals:
            continue
        v = num(r[ix["v"]]) if ix["v"] is not None else 0
        out[sym] = bar(parse_date(r[ix["date"]]), vals[0], vals[1], vals[2], vals[3], v)
    return out


class NotPublished(SourceError):
    """NSE says today's files are not out yet: not a failure, so nothing is hammered and nothing is retried."""


def report_key(url):
    for part, key in REPORT_KEYS.items():
        if part in url:
            return key
    return None


def daily_reports(sess, key="CM"):
    """/api/daily-reports?key=CM -> (currentDate or None, set of fileKeys in CurrentDay)."""
    doc = nse_api(sess, "/api/daily-reports?key=%s" % key, REPORTS_REFERER)
    cur = doc.get("CurrentDay") if isinstance(doc, dict) else None
    if not isinstance(cur, list):
        raise SourceError("nse-api", "daily-reports has no CurrentDay list",
                          "NSE changed the feed, or this is a bot wall; run doctor")
    try:
        day = parse_date(doc.get("currentDate"))
    except DataError:
        day = None
    return day, set(str(x.get("fileKey")) for x in cur if isinstance(x, dict) and x.get("fileKey"))


class Bhav(object):
    """Fetches each trading day once per run and serves every symbol from it. The same file also
    feeds the delivery analytics: `deliv` holds each day's delivery rows, `full` the days whose
    rows came from sec_bhavdata_full (the only file with delivery), `sec_missing` the days that
    have a bar table but no delivery file yet.

    Set `today` to the day being fetched live and the first request for it asks NSE's report list
    which files are out: the ones not listed are not requested (`unpublished` holds the days with
    nothing out yet). If that call fails, `listed` stays None and the 18:00 rule decides, as before."""

    def __init__(self, sess):
        self.sess, self.days, self.errors = sess, {}, {}
        self.deliv, self.full, self.sec_missing = {}, set(), set()
        self.tried, self.deliv_errors = set(), {}
        self.today, self.checked, self.listed = None, False, None
        self.unpublished, self.waiting, self.ready_note = set(), set(), None

    def check_ready(self):
        self.checked = True
        try:
            day, keys = daily_reports(self.sess, "CM")
        except (SourceError, DataError) as e:
            self.ready_note = "could not read NSE's report list (%s); going by the 18:00 rule" % e
            return
        if day is None:
            self.ready_note = "NSE's report list has no date; going by the 18:00 rule"
            return
        self.listed = (keys & set(REPORT_KEYS.values())) if day == self.today else set()
        if day != self.today:
            self.ready_note = "NSE's report list still shows %s, nothing for %s is out yet" % (day, self.today)
        elif not self.listed:
            self.ready_note = "NSE lists no CM-UDIFF-BHAVCOPY-CSV or CM-BHAVDATA-FULL for %s yet" % self.today

    def urls_for(self, d):
        """The bhavcopy URLs worth asking for on day d."""
        urls = bhav_urls(d)
        if d != self.today:
            return urls
        if not self.checked:
            self.check_ready()
        return urls if self.listed is None else [u for u in urls if report_key(u) in self.listed]

    def take_delivery(self, d, text, full):
        try:
            rows = parse_delivery(text)
        except DataError:
            return
        if full or d not in self.deliv:
            self.deliv[d] = rows
        if full:
            self.full.add(d)

    def day(self, d):
        if d in self.days:
            return self.days[d]
        table, last = None, None
        self.tried.add(d)
        urls = self.urls_for(d)
        if not urls:
            self.days[d], self.errors[d] = None, "not published yet"
            self.unpublished.add(d)
            return None
        for url in urls:
            try:
                code, body = self.sess.get(url, {"Accept": "*/*", "Referer": NSE + "/"}, gap=BHAV_PAUSE)
            except SourceError as e:
                last = str(e)
                break
            if code == 200:
                if url.endswith(".zip"):
                    try:
                        z = zipfile.ZipFile(io.BytesIO(body))
                        body = z.read([n for n in z.namelist() if n.lower().endswith(".csv")][0])
                    except (zipfile.BadZipFile, IndexError, KeyError):
                        last = "bad zip from %s" % url
                        continue
                try:
                    text = body.decode("utf-8", "replace")
                    table = parse_bhav(text)
                    self.take_delivery(d, text, "sec_bhavdata_full" in url)
                    break
                except DataError as e:
                    last = str(e)
                    continue
            last = "HTTP %d (%s)" % (code, hint("bhav", code))
            if code in (401, 403):
                break
        self.days[d] = table
        if table is None:
            self.errors[d] = last
        elif d not in self.full:
            self.sec_missing.add(d)
        return table

    def delivery_day(self, d):
        """Fetch only the delivery file of one day (once per run). True when its rows are in `deliv`."""
        if d in self.full:
            return True
        if d in self.tried:
            return False
        self.tried.add(d)
        url = bhav_urls(d)[0]
        try:
            if url not in self.urls_for(d):
                raise SourceError("nsearchives", "not published yet (NSE does not list CM-BHAVDATA-FULL)")
            code, body = self.sess.get(url, {"Accept": "*/*", "Referer": NSE + "/"}, gap=BHAV_PAUSE)
            if code != 200:
                raise SourceError("nsearchives", "HTTP %d (%s)" % (code, hint("bhav", code)))
            self.take_delivery(d, body.decode("utf-8", "replace"), True)
        except SourceError as e:
            self.deliv_errors[d] = str(e)
        if d not in self.full:
            self.sec_missing.add(d)
            return False
        return True


def fetch_bhav_daily(bhav, sym, days, max_days):
    if len(days) > max_days:
        days = days[-max_days:]
    rows, fails = [], []
    for d in days:
        t = bhav.day(d)
        if t is None:
            if d in bhav.unpublished:      # NSE says it is not out yet: wait, do not count it as a failure
                continue
            fails.append(d)
            if len(fails) >= 3 and not rows:   # blocked, not a few missing files
                raise SourceError("bhavcopy", "nsearchives bhavcopy: %s" % bhav.errors.get(d), "")
            continue
        if sym in t:
            rows.append(t[sym])
    if not rows and bhav.unpublished and not fails:
        raise NotPublished("bhavcopy", "today's bhavcopy is not published yet", bhav.ready_note or "")
    if not rows:
        raise SourceError("bhavcopy", "no bhavcopy rows for %s (%s)" % (
            sym, bhav.errors.get(fails[-1], "symbol not in the files") if fails else "symbol not in the files"))
    return rows


def parse_sec_list(text):
    """sec_list.csv (Symbol, Series, Security Name, Band, ...) -> {SYM: band_pct|None}."""
    rd = csv.reader(io.StringIO(text))
    head = [_key(h) for h in next(rd)]
    try:
        si, bi = head.index("symbol"), head.index("band")
    except ValueError:
        raise DataError("sec_list.csv has no Symbol/Band columns")
    ser = head.index("series") if "series" in head else None
    out = {}
    for r in rd:
        if len(r) <= max(si, bi):
            continue
        s = r[si].strip().upper()
        if ser is not None and r[ser].strip().upper() not in ("EQ", "BE", "BZ") and s in out:
            continue
        out[s] = band_value(r[bi])
    return out


def band_value(text):
    """'No Band' -> None; '20' / '20%' / 20 -> 20; anything else -> 'unknown'."""
    s = str(text if text is not None else "").strip().lower().replace("%", "")
    if s in ("no band", "nb", "none"):
        return None
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return "unknown"


def snap_band(prev_close, lower, upper):
    """Circuit limits from Kite -> nearest standard band % (2, 5, 10, 20), or None."""
    if not prev_close or not upper:
        return None
    pct = (upper / prev_close - 1) * 100
    best = min(STD_BANDS, key=lambda b: abs(b - pct))
    return best if abs(best - pct) <= 0.75 else None


# ---------------------------------------------------------------- daily command

def window(rows, years, now_ist, fill_gaps):
    end = last_complete_session(now_ist)
    if rows:
        last = datetime.date.fromisoformat(rows[-1]["date"])
        have = set(r["date"] for r in rows)
        if fill_gaps:
            first = datetime.date.fromisoformat(rows[0]["date"])
            miss = [d for d in trading_days(first, end) if d.isoformat() not in have and calendar_known(d.year)]
        else:
            miss = trading_days(last + datetime.timedelta(days=1), end)
        return miss, end
    if years == "max":
        start = datetime.date(1994, 11, 3)
    else:
        start = end - datetime.timedelta(days=int(round(365.25 * float(years))))
    return trading_days(start, end), end


def get_daily(sess, bhav, sym, miss, end, source, years, max_days):
    """Fallback chain. -> (raw rows, adj rows|None, source name, [errors])."""
    chain = {"auto": ["yahoo", "nse", "bhavcopy"], "yahoo": ["yahoo"], "nse": ["nse"],
             "bhavcopy": ["bhavcopy"]}[source]
    start = miss[0]
    errors = []
    for src in chain:
        try:
            if src == "yahoo":
                if years == "max" and start.year < 1996:
                    doc = fetch_yahoo(sess, sym, "1d", rng="max")
                else:
                    p1 = ist_to_ts(datetime.datetime.combine(start, datetime.time(0, 0)))
                    p2 = ist_to_ts(datetime.datetime.combine(end, datetime.time(23, 59)))
                    doc = fetch_yahoo(sess, sym, "1d", p1=p1, p2=p2)
                raw, adj, splits = parse_yahoo_daily(doc)
                raw = [r for r in raw if start.isoformat() <= r["date"] <= end.isoformat()]
                adj = [r for r in adj if start.isoformat() <= r["date"] <= end.isoformat()]
                if not raw:
                    raise SourceError("yahoo", "Yahoo returned no bars from %s" % start)
                return raw, adj, "yahoo", errors, splits
            if src == "nse":
                rows = fetch_nse_daily(sess, sym, start, end)
                if not rows:
                    raise SourceError("nse-api", "NSE returned no bars for %s from %s" % (sym, start),
                                      "check the symbol on nseindia.com")
                return rows, None, "nse-api", errors, []
            if src == "bhavcopy":
                return fetch_bhav_daily(bhav, sym, miss, max_days), None, "nse-bhavcopy", errors, []
        except NotPublished as e:
            bhav.waiting.add(sym)
            errors.append("%s: %s" % (src, e))
        except SourceError as e:
            errors.append("%s: %s" % (src, e))
    return None, None, None, errors, []


def cmd_daily(a):
    now = parse_now(a.now)
    sess, rc = Session(a.data), 0
    bhav = Bhav(sess)
    bhav.today = now.date()
    for s in a.symbols:
        sym = norm_symbol(s)
        raw_p, adj_p, _ = data_paths(a.data, sym)
        old = read_daily(raw_p)
        miss, end = window(old, a.years, now, a.fill_gaps)
        if not miss:
            print("%s: up to date (last session %s)." % (sym, old[-1]["date"] if old else end))
            continue
        raw, adj, src, errors, splits = get_daily(sess, bhav, sym, miss, end, a.source, a.years, a.max_days)
        if raw is None and sym in bhav.waiting:     # not a failure: NSE has not published the file yet
            print("%s: today's bhavcopy is not published yet (%s); run again later." % (sym, bhav.ready_note))
            for e in errors[:-1]:
                print("  tried first: " + e)
            continue
        if raw is None:
            rc = 1
            print("%s: every source failed:" % sym)
            for e in errors:
                print("  - " + e)
            print("  Fix: in Claude, call the Kite MCP tool get_historical_data and run import-kite, "
                  "or download a CSV (Kite Console / nseindia.com) and run import-csv.")
            continue
        raw, bad = split_valid(raw)
        merged = merge_rows(old, raw)
        try:
            warns = write_daily(raw_p, merged)
        except DataError as e:
            rc = 1
            print("%s: %s" % (sym, e))
            continue
        old_adj = read_daily(adj_p)
        if adj is not None:
            adj, bad2 = split_valid(adj)
            if old_adj and splits and any(d > datetime.date.fromisoformat(old_adj[0]["date"]) for d, _ in splits):
                # a split inside the window rescales all history: refetch the whole adjusted series
                try:
                    p1 = ist_to_ts(datetime.datetime.combine(
                        datetime.date.fromisoformat(old_adj[0]["date"]), datetime.time(0, 0)))
                    _, adj, _ = parse_yahoo_daily(fetch_yahoo(sess, sym, "1d", p1=p1, p2=ist_to_ts(now)))
                    old_adj = []
                except SourceError as e:
                    errors.append("adjusted refresh: %s" % e)
            write_daily(adj_p, merge_rows(old_adj, adj))
            note_meta(a.data, "adjusted", sym, {"source": "yahoo", "fetched_at": stamp()})
        elif old_adj:
            # unadjusted bars after the last split equal adjusted ones; flagged if a split shows up
            write_daily(adj_p, merge_rows(old_adj, raw))
        added = len(set(r["date"] for r in merged) - set(r["date"] for r in old))
        note_meta(a.data, "daily", sym, {"source": src, "fetched_at": stamp(), "rows": len(merged),
                                         "first": merged[0]["date"], "last": merged[-1]["date"],
                                         "adjusted_file": os.path.exists(adj_p)})
        print("%s: +%d bar(s) from %s, %d cached (%s to %s)%s." % (
            sym, added, src, len(merged), merged[0]["date"], merged[-1]["date"],
            ", .adj.csv updated" if os.path.exists(adj_p) else ""))
        if src == "nse-bhavcopy" and len(miss) > a.max_days:
            print("  note: bhavcopy is one file per day; fetched the latest %d of %d missing sessions "
                  "(run again, or raise --max-days)" % (a.max_days, len(miss)))
        for e in errors:
            print("  tried first: " + e)
        for b in bad:
            print("  dropped bad bar " + b)
        for w in warns:
            print("  warning: " + w)
        if src != "yahoo":    # Yahoo already sent an adjusted series; any other source needs the actions
            for line in auto_adjust(a.data, sym):
                print(line)
    if bhav.unpublished and not bhav.waiting:
        print("note: today's bhavcopy is not published yet (%s); the earlier sessions were filled, "
              "run again later for today." % bhav.ready_note)
    elif bhav.ready_note and not bhav.unpublished:
        print("note: " + bhav.ready_note)
    for line in fill_delivery(a.data, bhav, [norm_symbol(x) for x in a.symbols], now.date()):
        print(line)
    return rc


# ---------------------------------------------------------------- delivery analytics

def _int(x):
    v = num(x)
    return None if v is None else int(round(v))


def parse_delivery(text, want=None):
    """sec_bhavdata_full (or a plain bhavcopy) -> {SYMBOL: row} for the delivery file.

    A row is {date, volume, trades, deliv_qty, deliv_pct, qty_per_trade, series}; a value NSE
    leaves blank ("-") is None, never 0. In the trade-to-trade series (BE, BZ) every share is
    delivered, so delivery is the full volume. Rights symbols (-RE) are skipped; EQ wins when a
    symbol has several series. The UDiFF and legacy files carry no delivery, so those rows keep
    volume and trades and leave the delivery fields empty."""
    rd = csv.reader(io.StringIO(text))
    try:
        head = [_key(h) for h in next(rd)]
    except StopIteration:
        return {}
    pick = lambda *names: next((head.index(n) for n in names if n in head), None)  # noqa: E731
    ix = {"sym": pick("symbol", "tckrsymb"), "ser": pick("series", "sctysrs"),
          "date": pick("date1", "traddt", "timestamp"), "v": pick("ttl_trd_qnty", "ttltradgvol", "tottrdqty"),
          "n": pick("no_of_trades", "ttlnboftxsexctd", "totaltrades"), "dq": pick("deliv_qty"),
          "dp": pick("deliv_per")}
    if None in (ix["sym"], ix["date"], ix["v"]):
        raise DataError("not a bhavcopy (columns: %s)" % ", ".join(head[:8]))
    out = {}
    for r in rd:
        if len(r) < len(head):
            continue
        sym = r[ix["sym"]].strip().upper()
        if (want and sym not in want) or sym.endswith("-RE"):
            continue
        ser = r[ix["ser"]].strip().upper() if ix["ser"] is not None else "EQ"
        if ser not in DELIVERY_SERIES or (sym in out and ser != "EQ"):
            continue
        try:
            day = parse_date(r[ix["date"]])
        except DataError:
            continue
        vol = _int(r[ix["v"]])
        trades = _int(r[ix["n"]]) if ix["n"] is not None else None
        if ser in T2T_SERIES:
            dq = vol
        else:
            dq = _int(r[ix["dq"]]) if ix["dq"] is not None else None
        pct = round(dq * 100.0 / vol, 2) if dq is not None and vol else None
        if pct is None and dq is None and ix["dp"] is not None:
            given = num(r[ix["dp"]])
            pct = None if given is None else round(given, 2)
        out[sym] = {"date": day.isoformat(), "volume": vol, "trades": trades, "deliv_qty": dq, "deliv_pct": pct,
                    "qty_per_trade": round(vol / float(trades), 2) if vol is not None and trades else None,
                    "series": ser}
    return out


def delivery_dir(data_dir):
    return os.path.join(data_dir, "delivery")


def delivery_path(data_dir, sym):
    return os.path.join(delivery_dir(data_dir), sym + ".csv")


def read_delivery(path):
    """delivery/<SYM>.csv -> rows ascending by date; a blank cell is None."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                datetime.date.fromisoformat(r["date"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append({"date": r["date"], "volume": _int(r.get("volume")), "trades": _int(r.get("trades")),
                         "deliv_qty": _int(r.get("deliv_qty")), "deliv_pct": num(r.get("deliv_pct")),
                         "qty_per_trade": num(r.get("qty_per_trade")), "series": (r.get("series") or "").strip()})
    return sorted(rows, key=lambda r: r["date"])


def merge_delivery(old, new):
    """By date; a value in the new row replaces the old one, a blank never wipes a value."""
    by = dict((r["date"], dict(r)) for r in old)
    for r in new:
        cur = by.setdefault(r["date"], dict(r))
        for k, v in r.items():
            if v not in (None, ""):
                cur[k] = v
    return [by[d] for d in sorted(by)]


def write_delivery(data_dir, sym, new_rows):
    """Merge rows into delivery/<SYM>.csv (header date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series;
    a missing value is an empty cell). -> rows now in the file."""
    path = delivery_path(data_dir, sym)
    rows = merge_delivery(read_delivery(path), new_rows)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(DELIVERY_HEADER)
    cell = lambda v: "" if v is None else str(v)  # noqa: E731
    for r in rows:
        w.writerow([r["date"], cell(r["volume"]), cell(r["trades"]), cell(r["deliv_qty"]),
                    "" if r["deliv_pct"] is None else "%.2f" % r["deliv_pct"],
                    "" if r["qty_per_trade"] is None else "%.2f" % r["qty_per_trade"], r["series"] or ""])
    _atomic_write(path, buf.getvalue())
    return rows


def pending_path(data_dir):
    return os.path.join(delivery_dir(data_dir), "pending.json")


def read_pending(data_dir):
    doc = _read_json(pending_path(data_dir), {})
    out = set()
    for d in (doc.get("pending") if isinstance(doc, dict) else None) or []:
        try:
            out.add(datetime.date.fromisoformat(str(d)))
        except ValueError:
            continue
    return out


def tracked_symbols(data_dir):
    dd = delivery_dir(data_dir)
    return sorted(f[:-4] for f in (os.listdir(dd) if os.path.isdir(dd) else []) if f.endswith(".csv"))


def store_delivery(data_dir, bhav, syms):
    """Write the delivery rows the Bhav fetched for each symbol. -> {symbol: rows added or filled in}."""
    done = {}
    for sym in syms:
        rows = [bhav.deliv[d][sym] for d in sorted(bhav.deliv) if sym in bhav.deliv[d]]
        if not rows:
            continue
        before = read_delivery(delivery_path(data_dir, sym))
        after = write_delivery(data_dir, sym, rows)
        n = sum(1 for r in after if r not in before)
        if n:
            done[sym] = n
    return done


def fill_delivery(data_dir, bhav, syms, today):
    """After a run that fetched bhavcopies: retry the pending delivery days, store the rows for the
    symbols named plus every symbol already tracked, and keep pending.json (a day still missing after
    PENDING_DAYS is dropped with a warning). -> printable lines."""
    pend = read_pending(data_dir)
    if not (bhav.deliv or pend or bhav.sec_missing):
        return []
    lines = []
    for d in sorted(pend):
        if (today - d).days <= PENDING_DAYS:
            bhav.delivery_day(d)
    names = sorted(set(syms) | set(tracked_symbols(data_dir)))
    done = store_delivery(data_dir, bhav, names)
    if done:
        lines.append("delivery: %d row(s) written for %d symbol(s) in %s" % (
            sum(done.values()), len(done), delivery_dir(data_dir)))
    keep = set()
    for d in sorted(pend):
        if d in bhav.full:
            continue
        if (today - d).days > PENDING_DAYS:
            lines.append("warning: the delivery file for %s did not appear within %d days; those days stay empty"
                         % (d, PENDING_DAYS))
        else:
            keep.add(d)
    keep |= set(d for d in bhav.sec_missing if d not in bhav.full and (today - d).days <= PENDING_DAYS)
    if keep != pend or os.path.exists(pending_path(data_dir)):
        _write_json(pending_path(data_dir), {"pending": sorted(d.isoformat() for d in keep)})
    if keep:
        lines.append("delivery pending (file not published yet, retried on the next run): %s" % compress(sorted(keep)))
    return lines


def delivery_metrics(rows, window=DELIVERY_WINDOW):
    """The latest row against the mean of the last `window` rows (it included):
    DQ = delivery quantity, TQ = quantity per trade, VOL = volume, each over its mean.
    Institutional money: DQ > 1.2 and TQ > 1.2 on a series that is not trade-to-trade.
    Band on DQ: >= 2 strong, >= 1.5 high, > 1 above normal, else normal.
    Fewer than DELIVERY_MIN delivery values in the window -> status "not enough delivery data"."""
    win = rows[-window:]
    last = win[-1]
    out = {"date": last["date"], "series": last["series"], "window": len(win), "status": "ok"}
    for name, key in (("dq", "deliv_qty"), ("tq", "qty_per_trade"), ("vol", "volume")):
        vals = [r[key] for r in win if r[key] is not None]
        mean = sum(vals) / len(vals) if vals else 0
        out[name] = last[key] / mean if last[key] is not None and mean else None
        out["n_" + name] = len(vals)
    out["t2t"] = last["series"] in T2T_SERIES
    if out["n_dq"] < DELIVERY_MIN:
        out["status"] = "not enough delivery data"
    dq, tq = out["dq"], out["tq"]
    out["band"] = (None if dq is None else "strong" if dq >= 2 else "high" if dq >= 1.5
                   else "above normal" if dq > 1 else "normal")
    out["institutional"] = bool(dq is not None and tq is not None and dq > IM_LEVEL and tq > IM_LEVEL
                                and not out["t2t"] and out["status"] == "ok")
    out["last"] = last
    return out


def cmd_delivery(a):
    now = parse_now(a.now)
    today = now.date()
    syms = [norm_symbol(s) for s in a.symbols] or tracked_symbols(a.data)
    if not syms:
        raise DataError("no symbols: name some (delivery RELIANCE TCS), or fill the cache with daily first")
    window = max(DELIVERY_MIN, a.window)
    if not a.no_fetch:
        end = last_complete_session(now)
        sessions = trading_days(end - datetime.timedelta(days=int(window * 7 / 5) + 10), end)[-(window + 5):]
        have = dict((s, set(r["date"] for r in read_delivery(delivery_path(a.data, s)))) for s in syms)
        need = [d for d in sessions if any(d.isoformat() not in have[s] for s in syms)]
        need += [d for d in read_pending(a.data) if d not in need and (today - d).days <= PENDING_DAYS]
        bhav = Bhav(Session(a.data))
        bhav.today = today
        for d in sorted(need):
            bhav.delivery_day(d)
        for line in fill_delivery(a.data, bhav, syms, today):
            print(line)
        for d, why in sorted(bhav.deliv_errors.items())[:5]:
            print("  %s: %s" % (d, why))
    rc = 0
    for sym in syms:
        rows = read_delivery(delivery_path(a.data, sym))
        if not rows:
            rc = 1
            print("%s: no delivery data cached%s" % (sym, "" if a.no_fetch else " (NSE has no delivery file for it yet)"))
            continue
        m = delivery_metrics(rows, window)
        last = m["last"]
        print("%s  %s  series %s  (%d session(s) in the window of %d)" % (sym, m["date"], m["series"] or "?",
                                                                       m["window"], window))
        if m["status"] != "ok":
            rc = 1
            print("  not enough delivery data: %d value(s) in the window, %d needed%s" % (
                m["n_dq"], DELIVERY_MIN, "" if a.no_fetch else "; NSE publishes the file after the close"))
            continue
        ratio = lambda x: "n/a" if x is None else "%.2fx" % x  # noqa: E731
        if last["deliv_qty"] is not None:
            print("  delivered %s of %s shares (%s%%), %s shares per trade" % (
                format(last["deliv_qty"], ","), format(last["volume"] or 0, ","),
                "?" if last["deliv_pct"] is None else "%.1f" % last["deliv_pct"],
                "?" if last["qty_per_trade"] is None else "%.1f" % last["qty_per_trade"]))
        else:
            print("  delivery for %s is not published yet (volume and trades only)" % m["date"])
        print("  DQ %s   TQ %s   VOL %s   (each against the mean of the last %d sessions with data)" % (
            ratio(m["dq"]), ratio(m["tq"]), ratio(m["vol"]), m["window"]))
        if m["t2t"]:
            print("  band: %s   institutional money: no (trade-to-trade series %s: delivery is all volume)" % (
                m["band"] or "n/a", m["series"]))
        elif m["institutional"]:
            print("  band: %s   institutional money: YES (DQ and TQ both above %.1f: heavy delivery in big tickets)"
                  % (m["band"], IM_LEVEL))
        else:
            why = ("delivery is not published yet" if m["dq"] is None else
                   "needs DQ and TQ both above %.1f" % IM_LEVEL)
            print("  band: %s   institutional money: no (%s)" % (m["band"] or "n/a", why))
    print("A rule of thumb from delivery and ticket size, not a signal: confirm with price and your own plan.")
    return rc


# ---------------------------------------------------------------- intraday command

def cmd_intraday(a):
    sess, rc = Session(a.data), 0
    days = max(1, min(60, a.days))
    for s in a.symbols:
        sym = norm_symbol(s)
        path = data_paths(a.data, sym)[2]
        errors, bars, meta, src = [], None, {}, None
        if a.source in ("auto", "yahoo"):
            try:
                doc = fetch_yahoo(sess, sym, "5m", rng="%dd" % days)
                meta, bars = parse_chart(doc)
                meta = {k: v for k, v in meta.items() if k not in ("tradingPeriods", "currentTradingPeriod")}
                src = "yahoo"
            except (SourceError, DataError) as e:
                errors.append("yahoo: %s" % e)
        if bars is None and a.source in ("auto", "nse"):
            try:
                doc = nse_api(sess, "/api/chart-databyindex?index=%sEQN" % urllib.parse.quote(sym),
                              NSE + "/get-quotes/equity?symbol=" + urllib.parse.quote(sym))
                bars, src = parse_nse_chart(doc), "nse-chart"
                if not bars:
                    raise SourceError("nse-chart", "NSE chart had no ticks in market hours")
            except (SourceError, DataError) as e:
                bars = None
                errors.append("nse: %s" % e)
        if not bars:
            rc = 1
            print("%s: every intraday source failed:" % sym)
            for e in errors:
                print("  - " + e)
            print("  Fix: call Kite MCP get_historical_data with interval \"5minute\" (or \"minute\") and run "
                  "import-kite --kind historical.")
            continue
        try:
            allbars, warns = write_intraday(path, sym, bars, meta, src)
        except DataError as e:
            rc = 1
            print("%s: %s" % (sym, e))
            continue
        sessions = sorted(set(ts_to_ist(b[0]).date() for b in allbars))
        note_meta(a.data, "intraday", sym, {"source": src, "fetched_at": stamp(), "bars": len(allbars),
                                            "sessions": len(sessions), "last": sessions[-1].isoformat()})
        print("%s: %d 5m bars from %s, %d session(s) cached, last %s%s." % (
            sym, len(regular_session(bars)), src, len(sessions), sessions[-1],
            " (NSE chart: one session, no volume)" if src == "nse-chart" else ""))
        for e in errors:
            print("  tried first: " + e)
        for w in warns:
            print("  warning: " + w)
    return rc


# ---------------------------------------------------------------- bands command

def write_bands(data_dir, entries, unknown, as_of, source):
    path = os.path.join(data_dir, "bands.json")
    doc = _read_json(path, {})
    bands = dict(doc.get("bands") or {})
    bands.update(entries)
    unk = sorted((set(doc.get("unknown") or []) | set(unknown)) - set(bands))
    srcs = sorted(set([source] + [x for x in str(doc.get("source") or "").split(",") if x]))
    out = {"as_of": as_of, "source": ",".join(s for s in srcs if s), "bands": dict(sorted(bands.items())),
           "unknown": unk}
    _write_json(path, out)
    return out


def cmd_bands(a):
    now = parse_now(a.now)
    syms = [norm_symbol(s) for s in a.symbols]
    if not syms:
        dd = os.path.join(a.data, "daily")
        syms = sorted(f[:-4] for f in (os.listdir(dd) if os.path.isdir(dd) else [])
                      if f.endswith(".csv") and not f.endswith(".adj.csv"))
        syms += [s for s in (_read_json(os.path.join(a.data, "bands.json"), {}).get("bands") or {})
                 if s not in syms]
    if not syms:
        raise DataError("no symbols: give some, or fill the daily cache first")
    sess, entries, unknown, errors, used = Session(a.data), {}, [], [], set()
    today = now.date().isoformat()
    nse_ok = a.source in ("auto", "nse")
    for sym in syms:
        if not nse_ok:
            break
        try:
            pi = nse_quote(sess, sym).get("priceInfo") or {}
            bv = band_value(pi.get("pPriceBand"))
            if bv == "unknown":
                raise SourceError("nse-api", "NSE quote for %s has no pPriceBand" % sym)
            entries[sym] = {"band_pct": bv, "lower": num(pi.get("lowerCP")), "upper": num(pi.get("upperCP")),
                            "source": "nse-quote", "as_of": today}
            used.add("nse-quote")
        except SourceError as e:
            errors.append("nse quote %s: %s" % (sym, e))
            if e.source in ("nse-home",) or "403" in str(e):
                nse_ok = False   # blocked: do not hammer it
    rest = [s for s in syms if s not in entries]
    if rest and a.source in ("auto", "seclist"):
        try:
            code, body = sess.get(SEC_LIST, {"Referer": NSE + "/"})
            if code != 200:
                raise SourceError("nsearchives", "sec_list.csv answered HTTP %d" % code, hint("nse", code))
            table = parse_sec_list(body.decode("utf-8", "replace"))
            for sym in rest:
                bv = table.get(sym, "unknown")
                if bv != "unknown":
                    entries[sym] = {"band_pct": bv, "lower": None, "upper": None, "source": "nse-sec-list",
                                    "as_of": today}
                    used.add("nse-sec-list")
        except (SourceError, DataError) as e:
            errors.append("sec_list: %s" % e)
    unknown = [s for s in syms if s not in entries]
    doc = write_bands(a.data, entries, unknown, today, ",".join(sorted(used)))
    for sym in syms:
        e = entries.get(sym)
        if e:
            print("%-12s band %-8s lower %-10s upper %-10s (%s)" % (
                sym, "none" if e["band_pct"] is None else "%s%%" % e["band_pct"], fmt(e["lower"]) or "-",
                fmt(e["upper"]) or "-", e["source"]))
        else:
            print("%-12s band unknown (not guessed)" % sym)
    for e in errors:
        print("  " + e)
    if unknown:
        print("Unknown bands stay unknown. Kite MCP get_quotes gives lower_circuit_limit / upper_circuit_limit: "
              "save it and run import-kite --kind quotes.")
    print("Wrote %s (%d known)." % (os.path.join(a.data, "bands.json"), len(doc["bands"])))
    return 0 if not unknown else 1


# ---------------------------------------------------------------- Kite MCP imports

def _unwrap(doc):
    if isinstance(doc, dict) and "data" in doc and (doc.get("status") in (None, "success") or len(doc) <= 3):
        return doc["data"]
    return doc


def kite_candles(doc):
    """[ts, o, h, l, c, v(, oi)] lists or {date, open, ...} dicts -> [(IST dt, o, h, l, c, v)]."""
    d = _unwrap(doc)
    if isinstance(d, dict):
        d = d.get("candles") or d.get("data") or []
    out = []
    for c in d:
        if isinstance(c, dict):
            ts, vals = c.get("date") or c.get("timestamp"), [c.get(k) for k in ("open", "high", "low", "close",
                                                                                "volume")]
        else:
            ts, vals = c[0], list(c[1:6]) + [None] * (5 - len(c[1:6]))
        out.append((parse_iso_ist(ts),) + tuple(num(v) for v in vals))
    return sorted(out, key=lambda x: x[0])


def import_kite_historical(a, doc):
    if not a.symbol:
        raise DataError("--symbol is required for --kind historical (Kite candles do not name the stock)")
    sym = norm_symbol(a.symbol)
    cs = kite_candles(doc)
    if not cs:
        raise DataError("no candles in %s" % a.file)
    daily = all(c[0].time() == datetime.time(0, 0) for c in cs)
    raw_p, adj_p, intra_p = data_paths(a.data, sym)
    if daily:
        rows, bad = split_valid([bar(c[0].date(), c[1], c[2], c[3], c[4], c[5]) for c in cs])
        target = adj_p if a.adjusted else raw_p
        merged = merge_rows(read_daily(target), rows)
        warns = write_daily(target, merged)
        note_meta(a.data, "daily", sym, {"source": "kite-mcp", "fetched_at": stamp(), "rows": len(merged),
                                         "first": merged[0]["date"], "last": merged[-1]["date"],
                                         "file": os.path.basename(target)})
        if a.adjusted:
            note_meta(a.data, "adjusted", sym, {"source": "kite-mcp", "fetched_at": stamp()})
        print("%s: %d daily bar(s) from Kite into %s (%s to %s)." % (sym, len(rows), target, rows[0]["date"],
                                                                     rows[-1]["date"]))
    else:
        steps = sorted(set(int((b[0] - x[0]).total_seconds()) for x, b in zip(cs, cs[1:])
                           if b[0].date() == x[0].date()))
        step = steps[0] if steps else 300
        if step > 300 or 300 % step:
            raise DataError("these candles are %d seconds apart; need minute, 3minute or 5minute" % step)
        bars = to_5m(cs)
        allbars, warns = write_intraday(intra_p, sym, bars, {"source": "kite-mcp"}, "kite-mcp")
        sessions = sorted(set(ts_to_ist(b[0]).date() for b in allbars))
        note_meta(a.data, "intraday", sym, {"source": "kite-mcp", "fetched_at": stamp(), "bars": len(allbars),
                                            "sessions": len(sessions), "last": sessions[-1].isoformat()})
        bad = []
        print("%s: %d 5m bar(s) from Kite (%d-second candles) into %s." % (sym, len(bars), step, intra_p))
    for b in bad:
        print("  dropped bad bar " + b)
    for w in warns:
        print("  warning: " + w)
    return 0


def import_kite_quotes(a, doc, kind):
    now = parse_now(a.now)
    d = _unwrap(doc)
    if not isinstance(d, dict):
        raise DataError("expected an object keyed by \"NSE:SYMBOL\"")
    want = norm_symbol(a.symbol) if a.symbol else None
    entries, wrote = {}, 0
    for key, q in sorted(d.items()):
        if not isinstance(q, dict):
            continue
        if ":" in key and not key.upper().startswith("NSE:"):
            continue
        sym = norm_symbol(key.split(":")[-1])
        if want and sym != want:
            continue
        ohlc = q.get("ohlc") or {}
        prev = num(ohlc.get("close"))
        lo, up = num(q.get("lower_circuit_limit")), num(q.get("upper_circuit_limit"))
        if lo and up:
            entries[sym] = {"band_pct": snap_band(prev, lo, up), "lower": lo, "upper": up, "source": "kite-mcp",
                            "as_of": now.date().isoformat()}
        # a daily bar only when the session is over: ohlc.close is the PREVIOUS close in Kite
        when = parse_iso_ist(q["timestamp"]) if q.get("timestamp") else None
        if kind == "ohlc" and a.date:
            when = datetime.datetime.combine(parse_date(a.date), CLOSE_T)
        elif when is None:
            when = now
        last = num(q.get("last_price"))
        o, h, low = num(ohlc.get("open")), num(ohlc.get("high")), num(ohlc.get("low"))
        if when.time() >= CLOSE_T and trading_day(when.date()) and None not in (o, h, low, last):
            raw_p = data_paths(a.data, sym)[0]
            row = bar(when.date(), o, h, low, last, num(q.get("volume")) or num(q.get("volume_traded")) or 0)
            p = ohlc_problem(o, h, low, last)
            if p:
                print("%s: not saving the %s bar: %s" % (sym, row["date"], p))
            else:
                write_daily(raw_p, merge_rows(read_daily(raw_p), [row]))
                wrote += 1
                print("%s: saved the %s daily bar (close %s = last_price after 15:30)." % (
                    sym, row["date"], fmt(last)))
        else:
            print("%s: no daily bar saved (session not over at %s; pass --date for get_ohlc after the close)." % (
                sym, when.strftime("%Y-%m-%d %H:%M")))
    if entries:
        write_bands(a.data, entries, [], now.date().isoformat(), "kite-mcp")
        for sym, e in entries.items():
            print("%s: band %s, lower %s, upper %s -> bands.json" % (
                sym, "?" if e["band_pct"] is None else "%s%%" % e["band_pct"], fmt(e["lower"]), fmt(e["upper"])))
    if not entries and not wrote:
        raise DataError("nothing usable in %s" % a.file)
    return 0


def cmd_import_kite(a):
    with open(a.file, encoding="utf-8-sig") as fh:
        try:
            doc = json.load(fh)
        except ValueError:
            raise DataError("%s is not JSON (save the tool output as-is)" % a.file)
    if a.kind == "historical":
        return import_kite_historical(a, doc)
    return import_kite_quotes(a, doc, a.kind)


# ---------------------------------------------------------------- CSV imports

CSV_COLS = {
    "date": ["date", "timestamp", "trade date", "tradedt", "date1", "ch_timestamp", "datetime"],
    "open": ["open", "open price", "open_price", "opnpric", "opening price"],
    "high": ["high", "high price", "high_price", "hghpric"],
    "low": ["low", "low price", "low_price", "lwpric"],
    "close": ["close", "close price", "close_price", "closing price", "clspric", "last", "last price", "ltp"],
    "volume": ["volume", "total traded quantity", "ttl_trd_qnty", "ttltradgvol", "shares traded",
               "no. of shares", "tottrdqty", "qty", "vol"],
    "series": ["series", "sctysrs"],
    "symbol": ["symbol", "tckrsymb"],
}


def parse_ohlc_csv(text, symbol=None):
    """Kite Console, NSE website, Yahoo, eod2 or bhavcopy-style CSV -> (rows, notes)."""
    rd = csv.reader(io.StringIO(text))
    head = None
    for r in rd:
        if r and any(_key(c) in CSV_COLS["date"] for c in r):
            head = [_key(c) for c in r]
            break
    if not head:
        raise DataError("no header row with a Date column")
    ix = {}
    for k, names in CSV_COLS.items():
        for n in names:
            if n in head:
                ix[k] = head.index(n)
                break
    missing = [k for k in ("date", "open", "high", "low", "close") if k not in ix]
    if missing:
        raise DataError("missing column(s): %s (header: %s)" % (", ".join(missing), ", ".join(head)))
    by, notes, series_seen, skipped = {}, [], set(), 0
    for r in rd:
        if not r or len(r) <= max(ix.values()) or not "".join(r).strip():
            continue
        if "symbol" in ix and symbol and r[ix["symbol"]].strip().upper() != symbol:
            continue
        ser = r[ix["series"]].strip().upper() if "series" in ix else "EQ"
        series_seen.add(ser)
        try:
            d = parse_date(r[ix["date"]])
        except DataError:
            skipped += 1
            continue
        vals = [num(r[ix[k]]) for k in ("open", "high", "low", "close")]
        if None in vals:
            skipped += 1
            continue
        v = num(r[ix["volume"]]) if "volume" in ix else 0
        if d.isoformat() in by and ser != "EQ":
            continue
        by[d.isoformat()] = bar(d, vals[0], vals[1], vals[2], vals[3], v)
    if skipped:
        notes.append("skipped %d row(s) without a date or prices" % skipped)
    if len(series_seen) > 1:
        notes.append("series seen: %s (EQ kept where a date repeats)" % ", ".join(sorted(series_seen)))
    if "volume" not in ix:
        notes.append("no volume column: volume saved as 0")
    return [by[k] for k in sorted(by)], notes


def cmd_import_csv(a):
    sym = norm_symbol(a.symbol)
    with open(a.file, encoding="utf-8-sig", newline="") as fh:
        rows, notes = parse_ohlc_csv(fh.read(), sym)
    if not rows:
        raise DataError("no bars in %s" % a.file)
    good, bad = split_valid(rows)
    if bad and not a.drop_bad:
        raise DataError("%d bar(s) fail OHLC checks, e.g. %s; fix the file or pass --drop-bad" % (len(bad), bad[0]))
    raw_p, adj_p, _ = data_paths(a.data, sym)
    target = adj_p if a.adjusted else raw_p
    merged = good if a.replace else merge_rows(read_daily(target), good)
    warns = write_daily(target, merged)
    note_meta(a.data, "daily", sym, {"source": "import-csv:" + os.path.basename(a.file), "fetched_at": stamp(),
                                     "rows": len(merged), "first": merged[0]["date"], "last": merged[-1]["date"],
                                     "file": os.path.basename(target)})
    if a.adjusted:
        note_meta(a.data, "adjusted", sym, {"source": "import-csv:" + os.path.basename(a.file), "fetched_at": stamp()})
    print("%s: imported %d bar(s) (%s to %s) into %s; %d cached." % (
        sym, len(good), good[0]["date"], good[-1]["date"], target, len(merged)))
    for n in notes + ["dropped bad bar " + b for b in bad]:
        print("  note: " + n)
    for w in warns:
        print("  warning: " + w)
    return 0


# ---------------------------------------------------------------- calendar command

def parse_holiday_master(doc):
    """/api/holiday-master?type=trading -> (holidays, special_sessions, years, skipped) from its CM list.

    Only weekday closures are holidays. A "Laxmi Pujan" entry is the Muhurat session: a special
    session (bars exist), never a closure, weekday or not. NSE marks it with an asterisk."""
    cm = doc.get("CM") if isinstance(doc, dict) else None
    if not isinstance(cm, list) or not cm:
        raise DataError("holiday-master has no CM list (NSE changed the feed, or this is a bot wall)")
    holidays, special, years, skipped = {}, {}, set(), 0
    for r in cm:
        try:
            d = parse_date((r or {}).get("tradingDate"))
        except (DataError, AttributeError):
            skipped += 1
            continue
        name = " ".join(str(r.get("description") or "holiday").replace("*", " ").split())
        years.add(d.year)
        if "laxmi pujan" in name.lower() or "muhurat" in name.lower():
            special[d.isoformat()] = name if "muhurat" in name.lower() else name + " (Muhurat)"
        elif d.weekday() < 5:
            holidays[d.isoformat()] = name
    if not years:
        raise DataError("holiday-master CM list has no readable dates")
    return holidays, special, years, skipped


def dates_in_text(text):
    """Every date written in a circular subject: "Saturday, March 6, 2027", "6th March 2027",
    "06-Mar-2027", "06/03/2027"."""
    out = []
    pats = (r"([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})",
            r"(\d{1,2})(?:st|nd|rd|th)?[- ]([A-Za-z]{3,9})\.?[- ,]+(\d{4})",
            r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
    for i, pat in enumerate(pats):
        for m in re.finditer(pat, text):
            try:
                out.append(parse_date({0: "%s %s, %s", 1: "%s %s %s", 2: "%s-%s-%s"}[i] % m.groups()))
            except (DataError, ValueError):
                continue
    return out


def parse_circulars(doc):
    """/api/circulars?dept=CMTR -> (closures, special_sessions, warnings), the first two {iso: name}.

    "trading holiday" plus "on account of" in the subject adds a closure, "live trading session"
    (or "muhurat") a special session. A subject with no readable date is a warning, never a guess."""
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise DataError("circulars answer has no data list (NSE changed the feed, or this is a bot wall)")
    closures, special, warns = {}, {}, []
    for r in rows:
        sub = " ".join(str((r or {}).get("sub") or (r or {}).get("subject") or "").split())
        low = sub.lower()
        closed = "trading holiday" in low and "on account of" in low
        live = "live trading session" in low or "muhurat" in low
        if not (closed or live):
            continue
        if closed and live:
            warns.append("circular is both a holiday and a live session, not applied: %s" % sub[:100])
            continue
        found = dates_in_text(sub)
        if not found:
            warns.append("circular mentions a %s but no date could be read, not guessed: %s" % (
                "trading holiday" if closed else "special session", sub[:100]))
            continue
        for d in sorted(set(found)):
            if closed:
                if d.weekday() < 5:     # a weekend closure changes nothing
                    closures[d.isoformat()] = CIRCULAR_TAG + sub[:90]
            else:
                special[d.isoformat()] = CIRCULAR_TAG + sub[:90]
    return closures, special, warns


def merge_calendar(old, master, circ, now, checked_from):
    """The new calendar.json document. master = (holidays, special, years, skipped) or None;
    circ = (closures, special, warnings) or None. Entries from circulars survive a holiday-list refetch."""
    holidays = dict(old["holidays"]) if old else {}
    special = dict(old["special_sessions"]) if old else {}
    years = set(old["years"]) if old else set()
    warns = list(old["warnings"]) if old else []
    fetched = old["fetched_at"] if old else None
    if master:
        mh, ms, myears, skipped = master
        for iso in [k for k in holidays if int(k[:4]) in myears and not holidays[k].startswith(CIRCULAR_TAG)]:
            del holidays[iso]
        holidays.update(mh)
        special.update(ms)
        years |= myears
        fetched = stamp(now)
        if skipped:
            warns.append("%d holiday-master row(s) had no readable date and were skipped" % skipped)
    last = old["last_circular_check"] if old else None
    if circ:
        closures, specials, cw = circ
        holidays.update(closures)
        special.update(specials)
        warns += cw
        last = now.date().isoformat()
    elif not last:
        last = checked_from.isoformat()   # nothing read yet: the next run starts from here
    out_w = []
    for w in warns:
        if w not in out_w:
            out_w.append(w)
    return {"fetched_at": fetched, "years": sorted(years), "holidays": dict(sorted(holidays.items())),
            "special_sessions": dict(sorted(special.items())), "last_circular_check": last,
            "warnings": out_w[-MAX_WARNINGS:]}


def calendar_changes(old, new):
    """Printable lines for what a refresh changed."""
    out = []
    for key, label in (("holidays", "holiday"), ("special_sessions", "special session")):
        o, n = (old[key] if old else {}), new[key]
        for iso in sorted(set(n) - set(o)):
            out.append("  + %s %s: %s" % (iso, label, n[iso]))
        for iso in sorted(set(o) - set(n)):
            out.append("  - %s %s: %s" % (iso, label, o[iso]))
    return out


def builtin_disagreement(new, years):
    """Where NSE's list and the built-in tables differ, for the years both cover."""
    out = []
    for y in sorted(set(years) & set(CALENDAR_YEARS)):
        pre = "%04d-" % y
        built = {k for k in HOLIDAYS if k.startswith(pre) and k not in SPECIAL_SESSIONS}
        live = {k for k in new["holidays"] if k.startswith(pre)}
        if built - live:
            out.append("%d: closed in the built-in table only: %s" % (y, compress(sorted(built - live))))
        if live - built:
            out.append("%d: closed in NSE's list only: %s" % (y, compress(sorted(live - built))))
    return out


def calendar_needs(old, today, now, refresh):
    """(fetch the holiday list, read the circulars) per the refresh rules."""
    if refresh or old is None:
        return True, True
    master = today.year not in old["years"]
    if not master and today.isoformat() in old["holidays"]:
        # today is a listed closure: trust the list only if it is under a day old
        try:
            age = now - IST - datetime.datetime.strptime(old["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
            master = age > datetime.timedelta(days=1)
        except (TypeError, ValueError):
            master = True
    return master, old["last_circular_check"] != today.isoformat()


def print_calendar_summary(cal, today):
    ahead = [k for k in sorted(cal["holidays"]) if k > today.isoformat()][:3]
    print("years %s, %d holiday(s), %d special session(s)%s." % (
        ", ".join(str(y) for y in cal["years"]) or "none", len(cal["holidays"]), len(cal["special_sessions"]),
        ("; next: " + ", ".join("%s %s" % (k, cal["holidays"][k]) for k in ahead)) if ahead else ""))


def cmd_calendar(a):
    now = parse_now(a.now)
    today = now.date()
    path = os.path.join(a.data, CALENDAR_FILE)
    old = load_calendar(a.data)
    if old is None and os.path.exists(path):
        print("calendar.json is unreadable; building it again from NSE.")
    need_master, need_circ = calendar_needs(old, today, now, a.refresh)
    if not (need_master or need_circ):
        print("calendar.json is current: holiday list fetched %s, circulars checked %s." % (
            old["fetched_at"], old["last_circular_check"]))
        print_calendar_summary(old, today)
        return 0
    sess, errors, master, circ = Session(a.data), [], None, None
    if need_master:
        try:
            master = parse_holiday_master(nse_api(sess, "/api/holiday-master?type=trading", CAL_REFERER))
        except (SourceError, DataError) as e:
            errors.append("holiday list: %s" % e)
    checked_from = today - datetime.timedelta(days=CIRCULAR_FIRST_DAYS)
    if old and old["last_circular_check"]:
        try:
            checked_from = min(today, parse_date(old["last_circular_check"]))
        except DataError:
            pass
    if need_circ:
        url = "/api/circulars?from_date=%s&to_date=%s&dept=CMTR" % (
            checked_from.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y"))
        try:
            circ = parse_circulars(nse_api(sess, url, CAL_REFERER))
        except (SourceError, DataError) as e:
            errors.append("circulars: %s" % e)
    for e in errors:
        print("  " + e)
    if master is None and circ is None:
        print("calendar not updated; %s." % (
            "the built-in tables stay in force (2024 to 2026)" if old is None else "calendar.json is left as it was"))
        return 1
    new = merge_calendar(old, master, circ, now, checked_from)
    _write_json(path, new)
    bits = []
    if master:
        bits.append("holiday list %s" % ", ".join(str(y) for y in sorted(master[2])))
    if circ:
        bits.append("circulars %s to %s" % (checked_from, today))
    print("Wrote %s (%s)." % (path, "; ".join(bits)))
    changes = calendar_changes(old, new)
    for line in changes[:40]:
        print(line)
    if len(changes) > 40:
        print("  ... and %d more" % (len(changes) - 40))
    if not changes:
        print("  nothing changed.")
    if master:
        for line in builtin_disagreement(new, master[2]):
            print("  differs from the built-in table, %s" % line)
    for w in new["warnings"][len(old["warnings"]) if old else 0:]:
        print("  warning: " + w)
    print_calendar_summary(new, today)
    return 1 if errors else 0


# ---------------------------------------------------------------- corporate actions and adjustment

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def split_factor(subject):
    """"Face Value Split ... From Rs 10/- To Re 1/-" -> 10.0; a consolidation gives a factor below 1.

    From the split / splt / consolidation word on, the first two numbers (decimals allowed) with
    only letters, spaces, / - . between them are the old and the new face value; factor = old / new.
    None when that does not read cleanly, or the factor is 1."""
    low = str(subject or "").lower()
    at = [low.find(w) for w in SPLIT_WORDS if w in low]
    if not at:
        return None
    tail = low[min(at):]
    hits = list(NUMBER.finditer(tail))
    if len(hits) < 2 or not re.match(r"^[a-z /\-.]*$", tail[hits[0].end():hits[1].start()]):
        return None
    old, new = float(hits[0].group()), float(hits[1].group())
    if old <= 0 or new <= 0 or old == new:
        return None
    return old / new


def bonus_factor(subject):
    """"Bonus 2:1" -> 3.0 (a new shares for every b held: older prices divide by 1 + a/b).
    Debenture, preference, NCRPS and DVR bonus text is not a share bonus: None. So is no ratio."""
    low = str(subject or "").lower()
    if "bonus" not in low or any(w in low for w in BONUS_SKIP):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", low)
    if not m or float(m.group(1)) <= 0 or float(m.group(2)) <= 0:
        return None
    return 1 + float(m.group(1)) / float(m.group(2))


def dividend_amount(subject):
    """"Interim Dividend - Rs 30 Per Share" -> 30.0, "Re 0.50" -> 0.5 (rupees per share), else None."""
    low = str(subject or "").lower()
    m = re.search(r"\b(?:rs|re)\b\.?\s*(\d+(?:\.\d+)?)", low) if "dividend" in low else None
    return float(m.group(1)) if m and float(m.group(1)) > 0 else None


def parse_action(subject):
    """One corporate action subject -> {"kind", "factor", "dividend"[, "about"]} or None.

    kind is split, bonus, split+bonus (the factors multiply), dividend, or unparsed when a split,
    bonus or dividend is named but no number could be read: never a guess. Buy backs, rights,
    debenture or preference bonus and the rest are None."""
    low = str(subject or "").lower()
    has_split = any(w in low for w in SPLIT_WORDS)
    has_bonus = "bonus" in low and not any(w in low for w in BONUS_SKIP)
    if has_split or has_bonus:
        parts = ([("split", split_factor(subject))] if has_split else []) + (
            [("bonus", bonus_factor(subject))] if has_bonus else [])
        kind = "+".join(k for k, _ in parts)
        if any(f is None for _, f in parts):
            return {"kind": "unparsed", "about": kind, "factor": None, "dividend": None}
        factor = 1.0
        for _, f in parts:
            factor *= f
        return {"kind": kind, "factor": round(factor, 6), "dividend": None}
    if "dividend" in low:
        amount = dividend_amount(subject)
        if amount is None:
            return {"kind": "unparsed", "about": "dividend", "factor": None, "dividend": None}
        return {"kind": "dividend", "factor": None, "dividend": amount}
    return None


def parse_actions_doc(doc, sym):
    """/api/corporates-corporateActions answer -> actions [{ex_date, kind, factor, dividend, subject,
    series, isin, face_val[, about]}] for the symbol. Series other than EQ, BE, BZ, rights (-RE)
    symbols and rows without an ex-date are left out. An empty list is a valid answer."""
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise SourceError("nse-api", "corporate actions answered something that is not a list",
                          "stale cookies or a changed feed; try again, or run doctor")
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict) or sym.endswith("-RE"):
            continue
        if str(r.get("symbol") or sym).strip().upper() != sym:
            continue
        series = str(r.get("series") or "").strip().upper()
        if series not in ACTION_SERIES:
            continue
        try:
            ex = parse_date(r.get("exDate"))
        except DataError:
            continue
        subject = " ".join(str(r.get("subject") or "").split())
        got = parse_action(subject)
        if got is None or (ex.isoformat(), subject) in seen:
            continue
        seen.add((ex.isoformat(), subject))
        item = {"ex_date": ex.isoformat(), "kind": got["kind"], "factor": got["factor"],
                "dividend": got["dividend"], "subject": subject, "series": series,
                "isin": str(r.get("isin") or "").strip() or None, "face_val": num(r.get("faceVal"))}
        if got.get("about"):
            item["about"] = got["about"]
        out.append(item)
    return sorted(out, key=lambda x: (x["ex_date"], x["subject"]))


def actions_path(data_dir):
    return os.path.join(data_dir, ACTIONS_FILE)


def load_actions(data_dir):
    """actions.json -> {"fetched_at", "symbols": {SYM: {"from", "fetched_through", "actions"}}}; a missing
    or corrupt file is an empty one."""
    doc = _read_json(actions_path(data_dir), None)
    doc = doc if isinstance(doc, dict) else {}
    syms = {}
    for sym, e in (doc.get("symbols") or {}).items():
        try:
            datetime.date.fromisoformat(e["from"])
            datetime.date.fromisoformat(e["fetched_through"])
            acts = [x for x in e["actions"] if isinstance(x, dict) and x.get("ex_date")]
        except (KeyError, TypeError, ValueError):
            continue
        syms[sym] = {"from": e["from"], "fetched_through": e["fetched_through"], "actions": acts}
    return {"fetched_at": doc.get("fetched_at"), "symbols": syms}


def chunk_range(lo, hi, days=ACTION_CHUNK_DAYS):
    """[lo, hi] -> explicit (from, to) date pairs of at most `days` days each."""
    out = []
    while lo <= hi:
        e = min(hi, lo + datetime.timedelta(days=days - 1))
        out.append((lo, e))
        lo = e + datetime.timedelta(days=1)
    return out


def action_ranges(entry, start, today, refresh=False):
    """The date ranges to ask NSE for. First time (or --refresh): start to today plus the lookahead.
    Later: anything before the span already covered, and from ACTION_OVERLAP days before
    fetched_through onward. Nothing when it was already fetched today."""
    end = today + datetime.timedelta(days=ACTION_LOOKAHEAD)
    if entry is None or refresh:
        return [(start, end)]
    have = datetime.date.fromisoformat(entry["from"])
    through = datetime.date.fromisoformat(entry["fetched_through"])
    out = []
    if start < have:
        out.append((start, have - datetime.timedelta(days=1)))
    if through < today:
        out.append((min(end, max(have, through - datetime.timedelta(days=ACTION_OVERLAP))), end))
    return out


def replace_actions(old, new, ranges):
    """Actions with an ex-date inside a refetched range are replaced by what NSE sent now; the rest stay."""
    def inside(x):
        return any(lo.isoformat() <= x["ex_date"] <= hi.isoformat() for lo, hi in ranges)
    by = dict(((x["ex_date"], x["subject"]), x) for x in old if not inside(x))
    for x in new:
        by[(x["ex_date"], x["subject"])] = x
    return [by[k] for k in sorted(by)]


def fetch_actions(sess, sym, lo, hi):
    path = "/api/corporates-corporateActions?index=equities&symbol=%s&from_date=%s&to_date=%s" % (
        urllib.parse.quote(sym), lo.strftime("%d-%m-%Y"), hi.strftime("%d-%m-%Y"))
    return parse_actions_doc(nse_api(sess, path, ACTIONS_REFERER), sym)


def daily_symbols(data_dir):
    dd = os.path.join(data_dir, "daily")
    return sorted(f[:-4] for f in (os.listdir(dd) if os.path.isdir(dd) else [])
                  if f.endswith(".csv") and not f.endswith(".adj.csv"))


def describe_action(x):
    if x["kind"] == "dividend":
        return "dividend Rs %s per share" % fmt(x["dividend"])
    if x["kind"] == "unparsed":
        return "UNPARSED %s, no factor applied" % x.get("about", "action")
    return "%s x%s" % (x["kind"], fmt(x["factor"]))


def cmd_actions(a):
    now = parse_now(a.now)
    today = now.date()
    syms = [norm_symbol(s) for s in a.symbols] or daily_symbols(a.data)
    if not syms:
        raise DataError("no symbols: name some (actions RELIANCE TCS), or fill the daily cache first")
    doc, sess, rc = load_actions(a.data), Session(a.data), 0
    for sym in syms:
        if sym.endswith("-RE"):
            print("%s: rights symbol, skipped" % sym)
            continue
        cached = read_daily(data_paths(a.data, sym)[0])
        start = (datetime.date.fromisoformat(cached[0]["date"]) if cached
                 else today - datetime.timedelta(days=int(round(365.25 * a.years))))
        entry = doc["symbols"].get(sym)
        ranges = action_ranges(entry, start, today, a.refresh)
        if not ranges:
            print("%s: actions already fetched through %s (%d known)." % (
                sym, entry["fetched_through"], len(entry["actions"])))
            continue
        new, calls = [], 0
        try:
            for lo, hi in ranges:
                for c0, c1 in chunk_range(lo, hi):
                    new += fetch_actions(sess, sym, c0, c1)
                    calls += 1
        except (SourceError, DataError) as e:
            rc = 1      # this symbol's entry stays as it was
            print("%s: corporate actions not updated (%s)" % (sym, e))
            continue
        acts = replace_actions(entry["actions"] if entry else [], new, ranges)
        have = min(start, datetime.date.fromisoformat(entry["from"])) if entry else start
        doc["symbols"][sym] = {"from": have.isoformat(), "fetched_through": today.isoformat(), "actions": acts}
        doc["fetched_at"] = stamp(now)
        _write_json(actions_path(a.data), doc)
        adjusting = [x for x in acts if x["factor"]]
        print("%s: %d request(s), %d action(s) known from %s: %d split/bonus, %d dividend(s), %d unparsed." % (
            sym, calls, len(acts), have, len(adjusting), sum(1 for x in acts if x["kind"] == "dividend"),
            sum(1 for x in acts if x["kind"] == "unparsed")))
        for x in acts:
            ahead = x["ex_date"] > today.isoformat()
            if x["factor"] or x["kind"] == "unparsed" or ahead:
                print("  %s %s%s  (%s)" % (x["ex_date"], describe_action(x), "  UPCOMING" if ahead else "",
                                          x["subject"][:70]))
        for x in acts:
            if x["kind"] == "unparsed":
                print("  warning: unparsed corporate action on %s, never guessed: %s" % (
                    x["ex_date"], x["subject"][:90]))
        if cached and adjusting:
            print("  next: data.py adjust %s" % sym)
    print("Actions file: %s." % actions_path(a.data))
    return rc


def adj_source(data_dir, sym):
    """Where the existing <SYM>.adj.csv came from: yahoo, actions (our own rebuild), import-csv:FILE,
    kite-mcp, or unknown when the file predates source tracking."""
    meta = _read_json(os.path.join(data_dir, "cache_meta.json"), {})
    got = (meta.get("adjusted") or {}).get(sym)
    if got and got.get("source"):
        return str(got["source"])
    if ((meta.get("daily") or {}).get(sym) or {}).get("source") == "yahoo":
        return "yahoo"
    return "unknown"


def adjust_rows(rows, acts):
    """Raw bars + actions -> (adjusted bars, applied [(ex_date, factor, ratio|None, subjects)], warnings).

    Rows before each ex-date have O/H/L/C divided by the cumulative factor of every later action
    (2 decimals, no snapping) and volume multiplied. An action counts only once its ex-date is in the
    file's span: a future one waits for its bars. The close on the ex-date over the close before it
    (both adjusted) should be 0.67 to 1.5; outside that is "possible adjustment failure", and no bar
    on the ex-date is "unverified"."""
    dates = [r["date"] for r in rows]
    if len(set(dates)) != len(dates):
        dup = sorted(set(d for d in dates if dates.count(d) > 1))
        raise DataError("duplicate dates in the raw file (%s): fix it before adjusting" % compress(dup, 4))
    group, subjects, warns, seen = {}, {}, [], set()
    for x in acts:
        key = (x["ex_date"], x["subject"])
        if not (dates[0] < x["ex_date"] <= dates[-1]) or key in seen:
            continue
        seen.add(key)
        if x["kind"] == "unparsed" and x.get("about") != "dividend":
            warns.append("%s: no number could be read in \"%s\"; not applied, so the series before it may be "
                         "wrong" % (x["ex_date"], x["subject"][:80]))
        elif x["factor"]:
            group[x["ex_date"]] = group.get(x["ex_date"], 1.0) * x["factor"]
            subjects.setdefault(x["ex_date"], []).append(x["subject"])
    out, closes = [], []
    for r in rows:
        f = 1.0
        for ex, g in group.items():
            if ex > r["date"]:
                f *= g
        out.append({"date": r["date"], "open": round(r["open"] / f, 2), "high": round(r["high"] / f, 2),
                    "low": round(r["low"] / f, 2), "close": round(r["close"] / f, 2),
                    "volume": int(round(r["volume"] * f))})
        closes.append(r["close"] / f)
    index = dict((d, i) for i, d in enumerate(dates))
    applied = []
    for ex in sorted(group):
        i, ratio = index.get(ex), None
        if i is None:
            warns.append("%s: unverified, no bar on the ex-date (closed that day, or a gap in the file)" % ex)
        else:
            ratio = closes[i] / closes[i - 1]
            if not (VERIFY_LOW <= ratio <= VERIFY_HIGH):
                warns.append("%s: possible adjustment failure, the adjusted close is %.2fx the day before "
                             "(expected %s to %s); check the factor x%s" % (
                                 ex, ratio, VERIFY_LOW, VERIFY_HIGH, fmt(group[ex])))
        applied.append((ex, group[ex], ratio, "; ".join(subjects[ex])))
    return out, applied, warns


def verdict(ratio):
    if ratio is None:
        return "unverified"
    return ("verified" if VERIFY_LOW <= ratio <= VERIFY_HIGH else "CHECK") + ", ratio %.2f" % ratio


def adjust_symbol(data_dir, sym, force=False, auto=False):
    """Rebuild <SYM>.adj.csv from <SYM>.csv and the known actions; the raw file is never touched.
    All or nothing: any problem leaves the adj file as it was. -> (exit code, printable lines).
    A Yahoo, import or Kite adj file is kept unless force. auto (after a daily run) stays quiet when
    it has nothing to rebuild."""
    raw_p, adj_p, _ = data_paths(data_dir, sym)
    rows = read_daily(raw_p)
    entry = load_actions(data_dir)["symbols"].get(sym)
    if not rows:
        return 1, ["%s: no daily cache to adjust (run daily first)" % sym]
    if entry is None:
        return 1, ["%s: no corporate actions known: run data.py actions %s first" % (sym, sym)]
    src = adj_source(data_dir, sym) if os.path.exists(adj_p) else None
    if src not in (None, "actions") and not force:
        if auto:
            return 0, []
        return 0, ["%s: %s.adj.csv came from %s, kept as it is (--force rebuilds it from the corporate actions)"
                   % (sym, sym, src)]
    try:
        adj, applied, warns = adjust_rows(rows, entry["actions"])
        warns = warns + write_daily(adj_p, adj)
    except DataError as e:
        return 1, ["%s: not adjusted: %s" % (sym, e)]
    note_meta(data_dir, "adjusted", sym, {"source": "actions", "fetched_at": stamp(), "rows": len(adj),
                                          "applied": [ex for ex, _, _, _ in applied]})
    lines = ["%s: %s.adj.csv rebuilt from %s.csv, %d bar(s), %d action(s) applied (source: corporate actions%s)." % (
        sym, sym, sym, len(adj), len(applied), "" if src in (None, "actions") else "; replaced the %s file" % src)]
    for ex, factor, ratio, subject in applied:
        lines.append("  %s x%s  %s  (%s)" % (ex, fmt(factor), verdict(ratio), subject[:70]))
    last = datetime.date.fromisoformat(rows[-1]["date"])
    if (last - datetime.date.fromisoformat(entry["fetched_through"])).days > ACTIONS_STALE_DAYS:
        warns.append("actions were fetched through %s, the cache runs to %s: run data.py actions %s" % (
            entry["fetched_through"], last, sym))
    if entry["from"] > rows[0]["date"]:
        warns.append("actions are known only from %s, the cache starts %s: run data.py actions %s --refresh" % (
            entry["from"], rows[0]["date"], sym))
    later = sorted(x["ex_date"] for x in entry["actions"] if x["factor"] and x["ex_date"] > rows[-1]["date"])
    if later and not auto:
        lines.append("  not applied yet: ex-date %s is after the last bar %s" % (", ".join(later), rows[-1]["date"]))
    return 0, lines + ["  warning: " + w for w in warns]


def auto_adjust(data_dir, sym):
    """After a daily run on a non-Yahoo source: rebuild the adjusted file when the actions are known."""
    if sym not in load_actions(data_dir)["symbols"]:
        return []
    return adjust_symbol(data_dir, sym, auto=True)[1]


def cmd_adjust(a):
    known = load_actions(a.data)["symbols"]
    syms = [norm_symbol(s) for s in a.symbols] or [s for s in daily_symbols(a.data) if s in known]
    if not syms:
        raise DataError("no symbols: name some (adjust RELIANCE), or run actions first")
    rc = 0
    for sym in syms:
        code, lines = adjust_symbol(a.data, sym, a.force)
        rc = max(rc, code)
        for line in lines:
            print(line)
    return rc


# ---------------------------------------------------------------- index snapshot, valuation and India VIX

def index_slug(name):
    """Lowercase, every run of characters outside a-z and 0-9 becomes one "-": NIFTY 50 -> nifty-50."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower())


def index_path(data_dir, name):
    return os.path.join(data_dir, INDEX_DIR, index_slug(name) + ".csv")


def index_url(d):
    return ARCHIVES + "/content/indices/ind_close_all_%s.csv" % d.strftime("%d%m%Y")


def index_row(date, o, h, low, c, v, pe, pb, dy):
    return {"date": date if isinstance(date, str) else date.isoformat(), "open": o, "high": h, "low": low,
            "close": c, "volume": None if v is None else int(round(v)), "pe": pe, "pb": pb, "div_yield": dy}


def read_index(path):
    """index/<slug>.csv -> rows ascending by date; an empty cell is None."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                datetime.date.fromisoformat(r["date"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(index_row(r["date"], num(r.get("open")), num(r.get("high")), num(r.get("low")),
                                  num(r.get("close")), num(r.get("volume")), num(r.get("pe")), num(r.get("pb")),
                                  num(r.get("div_yield"))))
    return sorted(rows, key=lambda r: r["date"])


def merge_index(old, new):
    """By date; a value in the new row replaces the old one, a blank never wipes a value."""
    by = dict((r["date"], dict(r)) for r in old)
    for r in new:
        cur = by.setdefault(r["date"], dict(r))
        for k, v in r.items():
            if v is not None:
                cur[k] = v
    return [by[d] for d in sorted(by)]


def write_index(data_dir, name, new_rows):
    """Merge rows into index/<slug>.csv (header date,open,high,low,close,volume,pe,pb,div_yield, empty
    cell for "-" or missing). -> rows now in the file."""
    path = index_path(data_dir, name)
    rows = merge_index(read_index(path), new_rows)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(INDEX_HEADER)
    for r in rows:
        w.writerow([r["date"]] + [fmt(r[k]) for k in INDEX_HEADER[1:]])
    _atomic_write(path, buf.getvalue())
    return rows


def parse_index_csv(text, day):
    """ind_close_all_DDMMYYYY.csv -> {slug: (index name, row)}. "-" is an empty cell. A row with no
    closing value is skipped. The row's own Index Date wins; the requested day fills in when it is missing."""
    rd = csv.reader(io.StringIO(text))
    try:
        head = [_key(h) for h in next(rd)]
    except StopIteration:
        return {}
    pick = lambda *names: next((head.index(n) for n in names if n in head), None)  # noqa: E731
    ix = {"name": pick("index name"), "date": pick("index date", "date"),
          "o": pick("open index value", "open"), "h": pick("high index value", "high"),
          "l": pick("low index value", "low"), "c": pick("closing index value", "close"),
          "v": pick("volume"), "pe": pick("p/e", "pe"), "pb": pick("p/b", "pb"),
          "dy": pick("div yield", "dividend yield")}
    if None in (ix["name"], ix["c"]):
        raise DataError("not the index snapshot (columns: %s)" % ", ".join(head[:8]))
    cell = lambda r, k: num(r[ix[k]]) if ix[k] is not None and ix[k] < len(r) else None  # noqa: E731
    out = {}
    for r in rd:
        if len(r) <= max(ix["name"], ix["c"]) or not r[ix["name"]].strip():
            continue
        close = cell(r, "c")
        if close is None:
            continue
        try:
            d = parse_date(r[ix["date"]]) if ix["date"] is not None else day
        except DataError:
            d = day
        name = " ".join(r[ix["name"]].split())
        out[index_slug(name)] = (name, index_row(d, cell(r, "o"), cell(r, "h"), cell(r, "l"), close, cell(r, "v"),
                                                 cell(r, "pe"), cell(r, "pb"), cell(r, "dy")))
    return out


def fetch_index_day(sess, d):
    """One archive file -> parse_index_csv. A 404 is "not published", never retried."""
    code, body = sess.get(index_url(d), {"Accept": "*/*", "Referer": NSE + "/"}, gap=BHAV_PAUSE)
    if code != 200:
        raise SourceError("nsearchives", "ind_close_all_%s.csv answered HTTP %d" % (d.strftime("%d%m%Y"), code),
                          hint("nsearchives", code), transient=False)
    return parse_index_csv(body.decode("utf-8", "replace"), d)


def history_records(doc):
    """The first list of dicts that carries EOD_TIMESTAMP, wherever the answer nests it."""
    if isinstance(doc, list):
        if doc and isinstance(doc[0], dict) and "EOD_TIMESTAMP" in doc[0]:
            return doc
        found = [history_records(x) for x in doc if isinstance(x, (dict, list))]
    elif isinstance(doc, dict):
        found = [history_records(x) for x in doc.values() if isinstance(x, (dict, list))]
    else:
        found = []
    return next((f for f in found if f), [])


def parse_index_history(doc):
    """historicalOR/indicesHistory or vixhistory answer (newest first) -> rows ascending. NSE sends no
    volume, P/E, P/B or yield here, so those cells stay empty."""
    rows = {}
    for r in history_records(doc):
        try:
            d = parse_date(r.get("EOD_TIMESTAMP"))
        except DataError:
            continue
        close = num(r.get("EOD_CLOSE_INDEX_VAL"))
        if close is None:
            continue
        rows[d.isoformat()] = index_row(d, num(r.get("EOD_OPEN_INDEX_VAL")), num(r.get("EOD_HIGH_INDEX_VAL")),
                                        num(r.get("EOD_LOW_INDEX_VAL")), close, None, None, None, None)
    return [rows[k] for k in sorted(rows)]


def fetch_index_history(sess, name, lo, hi):
    lo_s, hi_s = lo.strftime("%d-%m-%Y"), hi.strftime("%d-%m-%Y")
    if index_slug(name) == index_slug(VIX_NAME):
        doc = nse_api(sess, "/api/historicalOR/vixhistory?from=%s&to=%s" % (lo_s, hi_s), VIX_REFERER)
    else:
        doc = nse_api(sess, "/api/historicalOR/indicesHistory?indexType=%s&from=%s&to=%s" % (
            urllib.parse.quote(str(name).upper()), lo_s, hi_s), INDEX_REFERER)
    return parse_index_history(doc)


def pe_verdict(pe, expensive=PE_EXPENSIVE, cheap=PE_CHEAP):
    """Nifty 50 P/E rule of thumb: at or above `expensive` is expensive, at or below `cheap` is cheap."""
    if pe is None:
        return None
    return "expensive" if pe >= expensive else "cheap" if pe <= cheap else "neutral"


def pe_percentile(rows, min_rows=PE_MIN_ROWS):
    """Share of the cached P/E values (the latest included) at or below the latest one, in percent.
    None until there are min_rows rows with a P/E."""
    vals = [r["pe"] for r in rows if r["pe"] is not None]
    if len(vals) < min_rows:
        return None
    return 100.0 * sum(1 for v in vals if v <= vals[-1]) / len(vals)


def index_days(end, n):
    """The last n trading sessions up to end."""
    return trading_days(end - datetime.timedelta(days=int(n * 1.5) + 14), end)[-n:]


def print_valuation(data_dir, a):
    rows = read_index(index_path(data_dir, VALUATION_INDEX))
    withpe = [r for r in rows if r["pe"] is not None]
    if not withpe:
        print("%s valuation: no P/E cached yet (the snapshot archive has it; run index --days 30)." % VALUATION_INDEX)
    else:
        last = withpe[-1]
        line = "%s on %s: P/E %s" % (VALUATION_INDEX, last["date"], fmt(last["pe"]))
        for key, label, unit in (("pb", "P/B", ""), ("div_yield", "dividend yield", "%")):
            if last[key] is not None:
                line += ", %s %s%s" % (label, fmt(last[key]), unit)
        print(line)
        print("  rule of thumb: P/E >= %s expensive, <= %s cheap, between neutral -> %s" % (
            fmt(a.pe_expensive), fmt(a.pe_cheap), pe_verdict(last["pe"], a.pe_expensive, a.pe_cheap)))
        pct = pe_percentile(rows)
        if pct is None:
            print("  P/E percentile: %d of %d rows with a P/E; needs %d (run index --days %d once, about a second "
                  "a file)" % (len(withpe), PE_MIN_ROWS, PE_MIN_ROWS, PE_MIN_ROWS + 10))
        else:
            print("  P/E percentile: %.0f%% of the %d cached sessions had a P/E at or below today's" % (
                pct, len(withpe)))
        print("  A market-wide rule of thumb, not a timing signal: valuation can stay high or low for years.")
    vix = read_index(index_path(data_dir, VIX_NAME))
    if vix:
        print("India VIX %s on %s." % (fmt(vix[-1]["close"]), vix[-1]["date"]))


def cmd_index(a):
    now = parse_now(a.now)
    end = last_complete_session(now)
    wanted = {}
    for n in (a.names or list(INDEX_DEFAULT)) + [VIX_NAME]:
        wanted.setdefault(index_slug(n), " ".join(n.split()).upper() if n != VIX_NAME else VIX_NAME)
    if a.days:
        days = index_days(end, a.days)
    else:
        days = set()
        for slug in wanted:
            rows = read_index(index_path(a.data, slug))
            days |= set(trading_days(datetime.date.fromisoformat(rows[-1]["date"]) + datetime.timedelta(days=1), end)
                        if rows else index_days(end, INDEX_DAYS))
        days = sorted(days)[-a.max_days:]
    sess, rc, got, notes, present = Session(a.data), 0, dict((s, []) for s in wanted), [], set()
    missing = []
    for d in days:
        try:
            table = fetch_index_day(sess, d)
        except SourceError as e:
            gone = "HTTP 404" in str(e)
            if gone and d < end:
                missing.append(d)
                continue
            rc = 0 if gone else 1     # today's file before the evening is not an error; a block is
            notes.append("%s: %s%s" % (d, e, " (not published yet, it appears in the evening)" if gone else ""))
            break
        except DataError as e:
            rc = 1
            notes.append("%s: %s" % (d, e))
            break
        for slug in wanted:
            if slug in table:
                got[slug].append(table[slug][1])
                present.add(slug)
    if missing:
        notes.insert(0, "no snapshot file for %d day(s), skipped (an unannounced closure, or not archived): %s" % (
            len(missing), compress(missing, 4)))
    if present:
        for slug, name in wanted.items():
            if slug not in present:
                notes.append("%s: not in the snapshot files (names look like NIFTY 50, NIFTY BANK, India VIX)" % name)
    for slug, name in wanted.items():
        if a.years:
            start = end - datetime.timedelta(days=int(round(365.25 * a.years)))
            dates = [r["date"] for r in read_index(index_path(a.data, slug)) + got[slug]]
            hi = datetime.date.fromisoformat(min(dates)) - datetime.timedelta(days=1) if dates else end
            if dates and hi <= start + datetime.timedelta(days=7):
                continue    # the cache already reaches back that far
            try:
                for c0, c1 in chunk_range(start, hi):
                    got[slug] += fetch_index_history(sess, name, c0, c1)
            except (SourceError, DataError) as e:
                rc = 1
                notes.append("%s history: %s" % (name, e))
        if got[slug]:
            before = len(read_index(index_path(a.data, slug)))
            after = write_index(a.data, name, got[slug])
            print("%s: +%d row(s), %d cached (%s to %s) -> %s" % (
                name, len(after) - before, len(after), after[0]["date"], after[-1]["date"],
                index_path(a.data, slug)))
        elif not days and not a.years:
            print("%s: up to date." % name)
    for n in notes:
        print("  " + n)
    print_valuation(a.data, a)
    return rc


# ---------------------------------------------------------------- index option chain

def expiry_text(d):
    """date -> 29-Oct-2026, the form the option-chain endpoints use."""
    return "%02d-%s-%d" % (d.day, MONTH_UP[d.month - 1].title(), d.year)


def chain_path(data_dir, sym):
    return os.path.join(data_dir, "chain-%s.json" % sym)


def expiry_dates(info):
    """option-chain-contract-info answer -> expiry dates, nearest first. None or empty is an error."""
    raw = info.get("expiryDates") if isinstance(info, dict) else None
    out = []
    for x in raw if isinstance(raw, list) else []:
        try:
            out.append(parse_date(x))
        except DataError:
            continue
    if not out:
        raise DataError("NSE listed no expiry dates (an empty contract-info answer: stale cookies, a bot wall, or "
                        "no option chain for this symbol)")
    return sorted(set(out))


def chain_records(doc):
    """The records object of an option-chain answer, {} when it is empty."""
    if not isinstance(doc, dict):
        return {}
    rec = doc.get("records")
    rec = rec if isinstance(rec, dict) else doc
    return rec if isinstance(rec.get("data"), list) and rec["data"] else {}


def row_expiry(r):
    """A strike row's expiry under either key NSE has used (expiryDate, expiryDates, expiry), or None."""
    for key in ("expiryDate", "expiryDates", "expiry"):
        v = r.get(key)
        v = v[0] if isinstance(v, list) and v else v
        if v:
            try:
                return parse_date(v)
            except DataError:
                return None
    return None


def chain_strikes(rec, expiry):
    """records.data -> {strike: {"ce_oi", "ce_chg", "pe_oi", "pe_chg"}} for one expiry. A strike with no CE or
    no PE side counts that side as zero; rows of another expiry are left out."""
    out, other = {}, set()
    for r in rec.get("data") or []:
        if not isinstance(r, dict):
            continue
        strike = num(r.get("strikePrice"))
        if strike is None:
            continue
        ex = row_expiry(r)
        if ex is not None and ex != expiry:
            other.add(ex)
            continue
        s = out.setdefault(strike, {"ce_oi": 0.0, "ce_chg": 0.0, "pe_oi": 0.0, "pe_chg": 0.0})
        for side, tag in (("CE", "ce"), ("PE", "pe")):
            leg = r.get(side)
            if isinstance(leg, dict):
                s[tag + "_oi"] = num(leg.get("openInterest")) or s[tag + "_oi"]
                s[tag + "_chg"] = num(leg.get("changeinOpenInterest")) or s[tag + "_chg"]
    if not out:
        raise DataError("no strikes for the %s expiry%s" % (
            expiry_text(expiry), " (the chain holds %s)" % ", ".join(expiry_text(x) for x in sorted(other))
            if other else ""))
    return out


def max_pain(strikes):
    """The strike where the payout to option buyers is smallest, over every strike (lowest on a tie)."""
    def payout(k):
        return sum(s["ce_oi"] * max(0.0, k - x) + s["pe_oi"] * max(0.0, x - k) for x, s in strikes.items())
    return min(sorted(strikes), key=payout)


def chain_stats(strikes, underlying):
    """PCR on total OI and on the change in OI, the largest call and put OI strikes, ATM, max pain."""
    call_oi = sum(s["ce_oi"] for s in strikes.values())
    put_oi = sum(s["pe_oi"] for s in strikes.values())
    call_chg = sum(s["ce_chg"] for s in strikes.values())
    put_chg = sum(s["pe_chg"] for s in strikes.values())
    ks = sorted(strikes)
    gaps = [b - a for a, b in zip(ks, ks[1:]) if b > a]
    gap = min(gaps) if gaps else None
    atm = None
    if underlying is not None and gap:
        atm = int(underlying / gap + 0.5) * gap
        atm = int(atm) if atm == int(atm) else atm
    top_call = max(ks, key=lambda k: strikes[k]["ce_oi"])
    top_put = max(ks, key=lambda k: strikes[k]["pe_oi"])
    ratio = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"pcr_oi": ratio(put_oi, call_oi), "pcr_change_oi": ratio(put_chg, call_chg),
            "total_call_oi": int(call_oi), "total_put_oi": int(put_oi),
            "total_call_oi_change": int(call_chg), "total_put_oi_change": int(put_chg),
            "max_call_oi_strike": top_call, "max_call_oi": int(strikes[top_call]["ce_oi"]),
            "max_put_oi_strike": top_put, "max_put_oi": int(strikes[top_put]["pe_oi"]),
            "atm": atm, "strike_gap": gap, "max_pain": max_pain(strikes), "strikes": len(ks)}


def fetch_chain(sess, sym, kind, expiry):
    """The option-chain answer for one expiry. An empty object usually means stale cookies, so it primes
    again once; still empty is a clear error."""
    path = "/api/option-chain-v3?type=%s&symbol=%s&expiry=%s" % (kind, urllib.parse.quote(sym), expiry_text(expiry))
    for attempt in (0, 1):
        doc = nse_api(sess, path, CHAIN_REFERER)
        if chain_records(doc):
            return doc
        if attempt == 0:
            nse_prime(sess, force=True)
    raise DataError("NSE returned an empty option chain for %s %s (stale cookies or a bot wall, or the chain is not "
                    "served at this hour); a fresh session was tried once. Try again in a minute." % (
                        sym, expiry_text(expiry)))


def vix_context(data_dir):
    """One line about India VIX from index/india-vix.csv, or None when the file is missing or empty."""
    rows = read_index(index_path(data_dir, VIX_NAME))
    if not rows:
        return None
    last = rows[-1]
    line = "India VIX %s on %s" % (fmt(last["close"]), last["date"])
    if len(rows) > 1 and rows[-2]["close"]:
        line += " (%+.1f%% on the day)" % ((last["close"] / rows[-2]["close"] - 1) * 100)
    recent = [r["close"] for r in rows[-VIX_AVG_ROWS:] if r["close"] is not None]
    if len(recent) >= 5:
        mean = sum(recent) / len(recent)
        line += ", %.1fx its %d-session average (%s)" % (
            last["close"] / mean, len(recent),
            "elevated" if last["close"] >= mean * VIX_ELEVATED else "calm" if last["close"] <= mean * VIX_CALM
            else "about normal")
    return line


def pcr_hint(pcr):
    if pcr is None:
        return "no call open interest"
    if pcr > PCR_HIGH:
        return "more puts than calls, leans supportive"
    if pcr < PCR_LOW:
        return "more calls than puts, leans weak"
    return "balanced"


def cmd_chain(a):
    now = parse_now(a.now)
    today = now.date()
    sym = norm_symbol(a.symbol)
    kind = "Indices" if sym in INDEX_SYMBOLS else "Equity"
    path = chain_path(a.data, sym)
    old = _read_json(path, {})
    old = old if isinstance(old, dict) else {}
    sess = Session(a.data)
    pick = "user" if a.expiry else "nearest"
    if a.expiry:
        expiry = parse_date(a.expiry)
    else:
        expiry = None
        if old.get("expiry_pick") == "nearest":     # the nearest expiry is kept until it passes
            try:
                kept = parse_date(old.get("expiry"))
                expiry = kept if kept >= today else None
            except DataError:
                pass
        if expiry is None:
            dates = expiry_dates(nse_api(sess, "/api/option-chain-contract-info?symbol=%s" % urllib.parse.quote(sym),
                                         CHAIN_REFERER))
            expiry = next((d for d in dates if d >= today), dates[0])
    rec = chain_records(fetch_chain(sess, sym, kind, expiry))
    strikes = chain_strikes(rec, expiry)
    underlying = num(rec.get("underlyingValue"))
    stats = chain_stats(strikes, underlying)
    doc = {"symbol": sym, "type": kind, "underlying": underlying, "timestamp": rec.get("timestamp"),
           "expiry": expiry.isoformat(), "expiry_pick": pick, "fetched_at": stamp(now), "source": "nse-option-chain"}
    doc.update(stats)
    vix = vix_context(a.data)
    if vix:
        doc["vix"] = vix
    _write_json(path, doc)
    print("%s option chain, expiry %s (%d strikes), underlying %s at %s" % (
        sym, expiry_text(expiry), stats["strikes"], fmt(underlying) or "?", rec.get("timestamp") or "an unknown time"))
    print("  PCR on open interest %s (%s)" % (fmt(stats["pcr_oi"]) or "n/a", pcr_hint(stats["pcr_oi"])))
    print("  PCR on today's change in OI %s (puts %+d, calls %+d)" % (
        fmt(stats["pcr_change_oi"]) or "n/a", stats["total_put_oi_change"], stats["total_call_oi_change"]))
    print("  largest call OI at %s (%s contracts), a possible resistance; largest put OI at %s (%s), a possible "
          "support" % (fmt(stats["max_call_oi_strike"]), format(stats["max_call_oi"], ","),
                       fmt(stats["max_put_oi_strike"]), format(stats["max_put_oi"], ",")))
    print("  ATM %s, max pain %s" % (fmt(stats["atm"]) or "?", fmt(stats["max_pain"])))
    if vix:
        print("  " + vix)
    print("PCR is a sentiment hint, never a signal; OI walls and max pain are levels where positions sit, not "
          "guarantees. Wrote %s." % path)
    return 0


# ---------------------------------------------------------------- doctor

def probes(now_ist):
    bd = last_complete_session(now_ist)
    return [
        ("yahoo-q1", "price", yahoo_url(YAHOO_HOSTS[0], "RELIANCE", "1d", rng="5d"), "yahoo"),
        ("yahoo-q2", "price", yahoo_url(YAHOO_HOSTS[1], "RELIANCE", "1d", rng="5d"), "yahoo"),
        ("nse-home", "gate", NSE + "/", "html"),
        ("nse-api", "price", NSE + "/api/quote-equity?symbol=RELIANCE", "nse"),
        ("nse-holidays", "cal", NSE + "/api/holiday-master?type=trading", "holidays"),
        ("nse-circulars", "cal", NSE + "/api/circulars?from_date=%s&to_date=%s&dept=CMTR" % (
            (now_ist.date() - datetime.timedelta(days=7)).strftime("%d-%m-%Y"), now_ist.strftime("%d-%m-%Y")),
         "circulars"),
        ("nse-actions", "cal", NSE + "/api/corporates-corporateActions?index=equities&symbol=RELIANCE"
         "&from_date=%s&to_date=%s" % ((now_ist.date() - datetime.timedelta(days=365)).strftime("%d-%m-%Y"),
                                       now_ist.strftime("%d-%m-%Y")), "actions"),
        ("nse-index", "index", index_url(bd), "index"),
        ("nse-chain", "cal", NSE + "/api/option-chain-contract-info?symbol=NIFTY", "chain"),
        ("nse-reports", "cal", NSE + "/api/daily-reports?key=CM", "reports"),
        ("nsearchives", "price", bhav_urls(bd)[0], "bhav"),
        ("niftyindices", "list", NIFTY_LIST, "csv"),
        ("google-news", "news", GNEWS, "rss"),
        ("kite-mcp", "info", KITE_MCP, "any"),
    ]


def check_body(kind, code, body):
    if kind == "any":
        return code < 500 and code not in (403, 407), ""
    if code not in (200, 206):
        return False, ""
    text = body[:4000].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:4000]
    if kind == "yahoo":
        try:
            return bool(json.loads(body)["chart"]["result"]), ""
        except (ValueError, KeyError, TypeError):
            return False, "not chart JSON"
    if kind == "nse":
        try:
            return "priceInfo" in json.loads(body), ""
        except ValueError:
            return False, "not JSON (bot wall)"
    if kind in ("holidays", "circulars", "actions", "reports", "chain"):
        try:
            doc = json.loads(body)
            if kind == "chain":
                return bool(doc.get("expiryDates")), "no expiryDates in the answer (stale cookies or a changed feed)"
            if kind == "reports":
                return isinstance(doc.get("CurrentDay"), list), "no CurrentDay file list in the answer"
            if kind == "actions":
                return isinstance(doc, list) or isinstance(doc.get("data"), list), "no list of actions in the answer"
            if kind == "holidays":
                return bool(isinstance(doc, dict) and doc.get("CM")), "no CM holiday list in the answer"
            return isinstance(doc.get("data"), list), "no data list in the answer"
        except (ValueError, AttributeError):
            return False, "not JSON (bot wall)"
    if kind == "bhav":
        return "SYMBOL" in text.upper(), "not a bhavcopy"
    if kind == "index":
        return "index name" in text.lower(), "not the index snapshot"
    if kind == "csv":
        return "symbol" in text.lower(), "not the index CSV"
    if kind == "rss":
        return "<rss" in text or "<item" in text, "not RSS"
    return True, ""


def cmd_doctor(a):
    now = parse_now(a.now)
    sess = Session(a.data)
    results = {}
    print("%-13s %-6s %-5s %8s  %s" % ("source", "kind", "ok", "latency", "error / hint"))
    for name, kind, url, check in probes(now):
        t0 = _clock()
        ok, err, code = False, "", None
        try:
            hdr = {"Referer": NSE + "/"} if "nse" in url else {}
            if check in ("bhav", "index"):
                hdr["Range"] = "bytes=0-2047"
            if name == "nse-api":
                hdr.update({"Accept": "application/json", "Referer": NSE + "/get-quotes/equity?symbol=RELIANCE"})
            if check in ("holidays", "circulars", "actions", "reports", "chain"):
                ref = {"actions": ACTIONS_REFERER, "reports": REPORTS_REFERER, "chain": CHAIN_REFERER}.get(
                    check, CAL_REFERER)
                hdr.update({"Accept": "application/json", "Referer": ref, "X-Requested-With": "XMLHttpRequest"})
            code, body = sess.get(url, hdr, timeout=a.timeout, gap=0, retries=0)   # a probe reports the first answer
            ok, why = check_body(check, code, body)
            if not ok:
                err = hint(name, code) if code not in (200, 206) else why
            if name == "nse-home" and ok:
                sess.nse_primed = True
        except SourceError as e:
            err = str(e)
        ms = int(round((_clock() - t0) * 1000))
        if name == "kite-mcp":
            err = ("reachable; its tools work only inside Claude with the Kite connector (no keys here)"
                   if ok else (err or "not reachable from here; Claude's connector may still work"))
        results[name] = {"ok": ok, "kind": kind, "status": code, "latency_ms": ms, "error": err,
                         "url": url.split("?")[0], "checked_at": stamp()}
        print("%-13s %-6s %-5s %6dms  %s" % (name, kind, "ok" if ok else "FAIL", ms, err))
    price_ok = [n for n, r in results.items() if r["kind"] == "price" and r["ok"]]
    doc = {"checked_at": stamp(), "price_sources_ok": price_ok, "sources": results}
    _write_json(os.path.join(a.data, "sources.json"), doc)
    if price_ok:
        print("Price data OK via: %s. Wrote %s." % (", ".join(price_ok), os.path.join(a.data, "sources.json")))
        return 0
    print("No free price source works from this machine. Use the Kite MCP connector (get_historical_data, "
          "get_quotes) + import-kite, or download CSVs and import-csv.")
    return 1


# ---------------------------------------------------------------- record

def default_fixture_dir():
    return os.path.join(os.path.dirname(HERE), "tests", "fixtures", "real")


def cmd_record(a):
    sym = norm_symbol(a.symbol)
    errors = []
    if not a.no_fetch:
        for name, fn, ns in (
                ("daily", cmd_daily, argparse.Namespace(symbols=[sym], years=a.years, source="auto", data=a.data,
                                                        fill_gaps=False, max_days=300, now=a.now)),
                ("intraday", cmd_intraday, argparse.Namespace(symbols=[sym], days=60, source="auto", data=a.data)),
                ("bands", cmd_bands, argparse.Namespace(symbols=[sym], source="auto", data=a.data, now=a.now))):
            try:
                if fn(ns):
                    errors.append("%s did not fully succeed" % name)
            except DataError as e:
                errors.append("%s: %s" % (name, e))
    out = os.path.join(a.out or default_fixture_dir(), sym)
    raw_p, adj_p, intra_p = data_paths(a.data, sym)
    meta = _read_json(os.path.join(a.data, "cache_meta.json"), {})
    prov = {"symbol": sym, "recorded_by": "trader-data record", "fetched_at": stamp(), "files": {}}
    os.makedirs(out, exist_ok=True)
    if os.path.exists(raw_p):
        rows = read_daily(raw_p)
        write_daily(os.path.join(out, "daily.csv"), rows)
        prov["files"]["daily.csv"] = dict(meta.get("daily", {}).get(sym, {}), rows=len(rows))
        if os.path.exists(adj_p):
            shutil.copyfile(adj_p, os.path.join(out, "daily.adj.csv"))
            prov["files"]["daily.adj.csv"] = {"from": "cache"}
    m, bars = read_intraday(intra_p)
    if bars:
        days = sorted(set(ts_to_ist(b[0]).date() for b in bars))
        want = parse_date(a.date) if a.date else days[-1]
        sb = [b for b in bars if ts_to_ist(b[0]).date() == want]
        if sb:
            name = "intraday-5m-%s.json" % want.isoformat()
            _write_json(os.path.join(out, name), build_chart(sym, sb, m, m.get("source", "")))
            prov["files"][name] = dict(meta.get("intraday", {}).get(sym, {}), bars=len(sb))
        else:
            errors.append("no intraday session on %s in the cache (have %s)" % (want, compress(days, 5)))
    band = (_read_json(os.path.join(a.data, "bands.json"), {}).get("bands") or {}).get(sym)
    if band:
        _write_json(os.path.join(out, "bands.json"), {"as_of": band.get("as_of"), "source": band.get("source"),
                                                      "bands": {sym: band}})
        prov["files"]["bands.json"] = {"source": band.get("source"), "as_of": band.get("as_of")}
    if not prov["files"]:
        raise DataError("nothing cached for %s to record (%s)" % (sym, "; ".join(errors) or "run daily first"))
    prov["source"] = sorted(set(str(v.get("source")) for v in prov["files"].values() if v.get("source")))
    prov["problems"] = errors
    _write_json(os.path.join(out, "provenance.json"), prov)
    print("Recorded %s into %s: %s." % (sym, out, ", ".join(sorted(prov["files"]))))
    for e in errors:
        print("  " + e)
    return 0


# ---------------------------------------------------------------- status

def cmd_status(a):
    now = parse_now(a.now)
    end = last_complete_session(now)
    dd = os.path.join(a.data, "daily")
    files = sorted(f for f in (os.listdir(dd) if os.path.isdir(dd) else []) if f.endswith(".csv"))
    print("Last complete NSE session: %s" % end)
    if not files:
        print("No daily cache in %s. Run: data.py daily RELIANCE" % dd)
    for f in files:
        rows = read_daily(os.path.join(dd, f))
        if not rows:
            continue
        last = datetime.date.fromisoformat(rows[-1]["date"])
        behind = len(trading_days(last + datetime.timedelta(days=1), end))
        errs, warns = validate_daily(rows)
        gaps = [w for w in warns if w.startswith("gap:")]
        print("%-22s %5d bars %s..%s  %s%s" % (
            f, len(rows), rows[0]["date"], rows[-1]["date"],
            "fresh" if behind == 0 else "%d session(s) behind" % behind,
            ("  " + gaps[0]) if gaps else "") + ("  ERRORS: " + errs[0] if errs else ""))
    idir = os.path.join(a.data, "intraday")
    for f in sorted(os.listdir(idir) if os.path.isdir(idir) else []):
        if f.endswith(".json"):
            _, bars = read_intraday(os.path.join(idir, f))
            days = sorted(set(ts_to_ist(b[0]).date() for b in bars))
            print("%-22s %5d bars, %d session(s), last %s" % (f, len(bars), len(days), days[-1] if days else "-"))
    tracked = tracked_symbols(a.data)
    if tracked:
        print("delivery/: %d symbol file(s); pending days: %s" % (
            len(tracked), ", ".join(d.isoformat() for d in sorted(read_pending(a.data))) or "none"))
    acts = load_actions(a.data)
    if acts["symbols"]:
        n = sum(1 for e in acts["symbols"].values() for x in e["actions"] if x["factor"])
        print("actions.json: %d symbol(s), %d split/bonus, fetched %s" % (len(acts["symbols"]), n, acts["fetched_at"]))
    idx = os.path.join(a.data, INDEX_DIR)
    for f in sorted(os.listdir(idx) if os.path.isdir(idx) else []):
        if f.endswith(".csv"):
            rows = read_index(os.path.join(idx, f))
            if rows:
                print("index/%-22s %5d rows %s..%s" % (f, len(rows), rows[0]["date"], rows[-1]["date"]))
    cal = _CAL
    if cal:
        print("calendar.json: years %s, %d holiday(s), fetched %s, circulars checked %s" % (
            ", ".join(str(y) for y in cal["years"]) or "none", len(cal["holidays"]), cal["fetched_at"],
            cal["last_circular_check"]))
    else:
        print("calendar.json: none (built-in tables cover %s)" % ", ".join(str(y) for y in CALENDAR_YEARS))
    if not calendar_known(now.year):
        print("  no holiday list for %d: run data.py calendar" % now.year)
    b = _read_json(os.path.join(a.data, "bands.json"), None)
    if b:
        print("bands.json as of %s: %d known, unknown: %s" % (b.get("as_of"), len(b.get("bands") or {}),
                                                             ", ".join(b.get("unknown") or []) or "none"))
    s = _read_json(os.path.join(a.data, "sources.json"), None)
    if s:
        print("sources.json (%s): price sources OK: %s" % (s.get("checked_at"),
                                                          ", ".join(s.get("price_sources_ok") or []) or "none"))
    return 0


# ---------------------------------------------------------------- CLI

def years_arg(x):
    if str(x).lower() == "max":
        return "max"
    try:
        v = float(x)
    except ValueError:
        raise argparse.ArgumentTypeError("--years wants a number or max")
    if v <= 0:
        raise argparse.ArgumentTypeError("--years must be positive")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE market data with source fallbacks (trader-data).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--data", default="trader/data", help="cache folder (default trader/data)")
        p.add_argument("--now", help="pretend it is this IST time")

    p = sub.add_parser("daily", help="fill the daily OHLC cache")
    common(p)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--years", type=years_arg, default=3.0)
    p.add_argument("--source", default="auto", choices=["auto", "yahoo", "nse", "bhavcopy"])
    p.add_argument("--fill-gaps", action="store_true", help="also refetch holes inside the cache")
    p.add_argument("--max-days", type=int, default=300, help="cap on bhavcopy files per run")
    p = sub.add_parser("intraday", help="fill the 5 minute cache")
    common(p)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--source", default="auto", choices=["auto", "yahoo", "nse"])
    p = sub.add_parser("bands", help="price bands into bands.json")
    common(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--source", default="auto", choices=["auto", "nse", "seclist"])
    p = sub.add_parser("import-kite", help="normalise saved Kite MCP JSON")
    common(p)
    p.add_argument("file")
    p.add_argument("--kind", required=True, choices=["historical", "quotes", "ohlc"])
    p.add_argument("--symbol")
    p.add_argument("--date", help="get_ohlc only: the session date the numbers are for (after 15:30)")
    p.add_argument("--adjusted", action="store_true", help="historical daily candles go to .adj.csv")
    p = sub.add_parser("import-csv", help="import a Kite Console / NSE website / generic OHLC CSV")
    common(p)
    p.add_argument("file")
    p.add_argument("--symbol", required=True)
    p.add_argument("--adjusted", action="store_true", help="the file is split/bonus adjusted: write .adj.csv")
    p.add_argument("--drop-bad", action="store_true", help="skip bars that fail OHLC checks")
    p.add_argument("--replace", action="store_true", help="replace the cache instead of merging")
    p = sub.add_parser("delivery", help="delivery analytics: DQ, TQ, VOL and the institutional money flag")
    common(p)
    p.add_argument("symbols", nargs="*", help="default: every symbol with a delivery file")
    p.add_argument("--window", type=int, default=DELIVERY_WINDOW, help="sessions for the means (default 30)")
    p.add_argument("--no-fetch", action="store_true", help="use the cached delivery files only")
    p = sub.add_parser("calendar", help="refresh the NSE holiday calendar (calendar.json)")
    common(p)
    p.add_argument("--refresh", action="store_true", help="refetch the holiday list and the circulars now")
    p = sub.add_parser("actions", help="corporate actions (splits, bonus, dividends) into actions.json")
    common(p)
    p.add_argument("symbols", nargs="*", help="default: every symbol in the daily cache")
    p.add_argument("--years", type=years_arg, default=3.0, help="how far back when the symbol has no daily cache")
    p.add_argument("--refresh", action="store_true", help="refetch the whole span, not only the newer ranges")
    p = sub.add_parser("adjust", help="rebuild <SYM>.adj.csv from the raw file and actions.json")
    common(p)
    p.add_argument("symbols", nargs="*", help="default: every cached symbol that has actions")
    p.add_argument("--force", action="store_true", help="also replace an adj file that came from Yahoo, Kite or a CSV")
    p = sub.add_parser("index", help="index snapshot, Nifty 50 valuation and India VIX into index/<slug>.csv")
    common(p)
    p.add_argument("names", nargs="*", help="index names, default: NIFTY 50, NIFTY BANK, NIFTY 500 (India VIX always)")
    p.add_argument("--days", type=int, default=0, help="fetch the snapshot file of the last N sessions again")
    p.add_argument("--max-days", type=int, default=300, help="cap on snapshot files per run")
    p.add_argument("--years", type=float, default=0, help="also backfill N years of index levels (no P/E)")
    p.add_argument("--pe-expensive", type=float, default=PE_EXPENSIVE, help="P/E flagged expensive (default 25)")
    p.add_argument("--pe-cheap", type=float, default=PE_CHEAP, help="P/E flagged cheap (default 20)")
    p = sub.add_parser("chain", help="option chain summary (PCR, OI walls, max pain) into chain-<SYMBOL>.json")
    common(p)
    p.add_argument("symbol", help="NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 or an F&O stock")
    p.add_argument("--expiry", help="expiry like 29-Oct-2026 (default: the nearest one)")
    p = sub.add_parser("doctor", help="probe every source")
    common(p)
    p.add_argument("--timeout", type=float, default=10)
    p = sub.add_parser("record", help="capture real data into tests/fixtures/real/<SYM>/")
    common(p)
    p.add_argument("symbol")
    p.add_argument("--date", help="intraday session to keep (default: the latest)")
    p.add_argument("--years", type=years_arg, default=3.0)
    p.add_argument("--out", help="fixtures folder (default: this pack's tests/fixtures/real)")
    p.add_argument("--no-fetch", action="store_true", help="copy what is cached, fetch nothing")
    p = sub.add_parser("status", help="what is cached, freshness, gaps")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    global _CAL
    saved, _CAL = _CAL, load_calendar(a.data)
    try:
        return {"daily": cmd_daily, "intraday": cmd_intraday, "bands": cmd_bands, "import-kite": cmd_import_kite,
                "import-csv": cmd_import_csv, "calendar": cmd_calendar, "delivery": cmd_delivery,
                "actions": cmd_actions, "adjust": cmd_adjust, "index": cmd_index, "chain": cmd_chain,
                "doctor": cmd_doctor, "record": cmd_record, "status": cmd_status}[a.cmd](a)
    except DataError as e:
        print("data.py: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("data.py: %s" % e, file=sys.stderr)
        return 1
    finally:
        _CAL = saved


if __name__ == "__main__":
    sys.exit(main())
