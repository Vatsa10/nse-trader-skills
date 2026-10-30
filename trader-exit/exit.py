#!/usr/bin/env python3
"""Exit Planner for trader-exit: sets the stop and the target BEFORE you
enter, so you know when to get out. Standard library only. Long side only.
It never places an order.

    python3 exit.py plan                                    # from trader/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write  # the reel: stop 358.32, target 370.04
    python3 exit.py plan --symbol RELIANCE --entry 364.18 --or-high 362.18 --or-low 354.47
    python3 exit.py plan --stop swing --offline RELIANCE.json   # the session's swing structure level
    python3 exit.py plan --symbol HDFCBANK --entry 1690.25 --setup level --level 1688 --stop swing --product CNC
    python3 exit.py plan --stop atr:1.5 --offline RELIANCE.json # ATR needs the session's bars
    python3 exit.py plan --symbol HDFCBANK --entry 1690.25 --setup level --stop pct:1 --band 10
    python3 exit.py show                                    # the current trader/plan.json

Stops (a long's stop rounds DOWN to the paisa, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on the session's bars up
             to the entry bar (--offline FILE or Yahoo). With fewer than 14
             bars by then it averages all of them and says so.
  swing      the change-of-character level of the swing structure (closing
             basis): on the session's bars up to the entry bar (no pullback
             filter), or with --product CNC on the daily cache bars before the
             plan's day (pullbacks under 5% ignored, more than 40 bars). Refused
             when the swing trend is not up, or the level is at or above the entry.
  A level setup has no opening-range default: give a stop.

Targets (round half up to the paisa):
  2R (default), 1R, 1.5R ...   entry + R x risk, where risk = entry - stop
  price:X                      your price

Refused, with the reason: a stop at or above the entry, a target at or below
it, a risk under 0.05% of the price (too tight to survive the spread), an
entry.json that has not triggered (unless you give --entry), a short (v2
plans longs only).

flat_by (default 15:15 IST, or "flat_by" in trader/config.json): out by then,
win or lose. The NSE session is 09:15 to 15:30 IST; brokers auto square off
intraday (MIS) positions at about 15:20. NSE has no early closes.

--product CNC plans a delivery (swing) hold instead: no flat-by, held overnight,
costs priced with the delivery charges (STT both sides, stamp, DP on the sell),
"product": "CNC" and "flat_by": null in plan.json. The default, MIS, is unchanged.

Costs: Zerodha intraday (MIS) charges for --qty shares (default about Rs 1
lakh worth) at the stop and at the target, with reward:risk after costs. The
charges model: brokerage min(Rs 20, 0.03%) an order, STT 0.025% on the sell,
NSE exchange txn 0.00307%, SEBI Rs 10 a crore, GST 18% on (brokerage +
exchange txn + SEBI), stamp 0.003% on the buy (verified 2026-10-29 from
zerodha.com/charges). trader/charges.json, when present, is deep-merged over
these defaults, and the total is multiplied by its calibration.factor (written
by trader-broker reconcile). Labelled "estimate", or "estimate (calibrated
from N contract notes)" once calibrated.
Circuit bands: trader/data/bands.json (written by trader-data) gives the
stock's real band and lower circuit price; the plan warns when the stop sits at
or under the lower circuit, or the move is at or over the band. Without the
file, --band, or the heuristic: a stop 5%/10%/20% or more under the entry gets
a warning that a stop past the lower circuit may not fill.
"min_reward_risk" in the config (default 1.0) only warns here; trader-risk
enforces it.

Bars for an atr stop: Yahoo (query1, then query2, browser user agent, one
retry after a pause on a 429). With --cache, or when Yahoo fails, it reads
trader/data/intraday/<SYMBOL>-5m.json (written by trader-data) and says how
old it is.

A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
Exit codes: plan 0 = a plan, 1 = refused or an error. show 0 = a plan exists.
"""

import argparse
import copy
import csv
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

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

# --- utf8 output: keep byte-identical in every script (tests/test_repo.py checks) ---
# The tools print the rupee sign. On Windows, piped output (Claude Code reads it through a pipe) defaults to
# cp1252, which has no rupee sign, and the first print of one raised UnicodeEncodeError. Switch a stream that
# cannot encode it to UTF-8; a stream that already can (UTF-8 consoles, Linux, macOS) is left alone.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        "₹".encode(_stream.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
# --- end utf8 output ---

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BACKOFF = 5.0
HOSTS = ("query1", "query2")
DATA_DIR = os.path.join("trader", "data")
CHARGES_PATH = os.path.join("trader", "charges.json")
BANDS_PATH = os.path.join("trader", "data", "bands.json")
DATA_HINT = ("run `python3 trader-data/data.py doctor`; or call Kite MCP get_historical_data and "
             "`python3 trader-data/data.py import-kite`")
CHARGES_DEFAULT = {  # verified 2026-10-29 from zerodha.com/charges; trader/charges.json overrides
    "broker": "zerodha", "verified": "2026-10-29 zerodha.com/charges",
    "intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "stamp_buy_pct": 0.003},
    "delivery": {"brokerage_pct": 0, "brokerage_cap": 0, "stt_buy_pct": 0.1, "stt_sell_pct": 0.1,
                 "stamp_buy_pct": 0.015, "dp_per_scrip_sell": 15.34},
    "exchange_txn_pct": {"NSE": 0.00307}, "sebi_per_crore": 10, "gst_pct": 18,
    "calibration": {"factor": 1.0, "samples": 0, "updated": None}}
INTERVALS = {"1m": 60, "5m": 300}
DEFAULTS = {"flat_by": "15:15", "min_reward_risk": 1.0}
ATR_BARS = 14
DAILY_DIR = os.path.join("trader", "data", "daily")
SWING_DAILY_RETRACE = 5.0          # CNC swing stop: pullbacks under 5 % of the swing high do not move the level
SWING_DAILY_MIN_BARS = 40          # ... and the structure needs more than this many daily bars to be trusted
MIN_RISK_PCT = Decimal("0.05")     # risk under 0.05% of the price is inside the spread's noise
CENT = Decimal("0.01")
EPOCH = datetime.datetime(1970, 1, 1)
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."

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
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)   # India: fixed UTC+05:30, no DST
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
MIS_SQUARE_OFF = datetime.time(15, 20)   # Zerodha auto square-off for intraday (MIS), approximate
BANDS = (2, 5, 10, 20)                   # NSE daily price bands (circuit limits), percent
DEFAULT_NOTIONAL = Decimal("100000")     # costs are shown for about Rs 1 lakh of shares unless --qty


class PlanError(Exception):
    pass


def shown(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------- time

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local - IST_OFFSET


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_now(text):
    """--now -> naive UTC. A time without an offset is India time (IST)."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise PlanError('--now wants an IST time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise PlanError("%s wants an IST time like 15:15, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def session(d):
    """(open_time, close_time) in IST, or None if closed all day. No early closes on NSE. A
    normal-hours special session opens a weekend or holiday; the Muhurat session is closed
    for the regular market."""
    iso = d.isoformat()
    if iso in MUHURAT_SESSIONS:
        return None
    if iso not in SPECIAL_SESSIONS and (d.weekday() >= 5 or iso in NSE_HOLIDAYS):
        return None
    return OPEN, CLOSE


def holiday_note(d):
    iso = d.isoformat()
    if iso in MUHURAT_SESSIONS:
        return "special session: %s, evening only; no normal 09:15-15:30 market" % MUHURAT_SESSIONS[iso]
    if iso in NSE_HOLIDAYS and iso not in SPECIAL_SESSIONS:
        return "NSE holiday: %s" % NSE_HOLIDAYS[iso]
    if d.year not in CALENDAR_YEARS:
        return "holiday list covers %s only: check nseindia.com" % years_text()
    return ""


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS (a typed .NS is stripped first)."""
    s = str(sym).strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    return s + ".NS"


def nse_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if not re.match(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$", s):
        raise PlanError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    if re.search(r"\d{2}[A-Z]{3}.*(CE|PE|FUT)$", s):
        raise PlanError("%s looks like an F&O contract: cash equities only" % s)
    return s


# ---------------------------------------------------------------- money math (Decimal, to the paisa)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip("$").replace("\u20b9", "").replace("Rs", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


def cents(x):
    """Round half up to the paisa."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def floor_cents(x):
    """A long's stop: round down, away from the entry."""
    return D(x).quantize(CENT, rounding=ROUND_FLOOR)


def inr(x):
    """Rupees with Indian digit grouping: 12,34,567.89."""
    v = D(x).quantize(CENT, rounding=ROUND_HALF_UP)
    neg = v < 0
    whole, frac = format(abs(v), "f").split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s\u20b9%s.%s" % ("-" if neg else "", whole, frac)


money = inr


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_charges(path=None):
    """The embedded charges model, deep-merged with trader/charges.json when it exists."""
    model = copy.deepcopy(CHARGES_DEFAULT)
    path = CHARGES_PATH if path is None else path
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                over = json.load(fh)
        except (OSError, ValueError) as e:
            raise PlanError("%s is not valid JSON: %s" % (shown(path), e))
        if isinstance(over, dict):
            _merge(model, over)
    return model


def charges_label(model):
    n = int(((model or {}).get("calibration") or {}).get("samples") or 0)
    return "estimate (calibrated from %d contract notes)" % n if n > 0 else "estimate"


def _pct(x):
    return D(x or 0) / Decimal(100)


def costs(buy, sell, qty, intraday=True, model=None):
    """Charges for one round trip (a buy and a sell of qty shares), from the charges model:
    brokerage pct of each order (capped), STT, NSE exchange txn, SEBI per crore, GST on
    (brokerage + txn + SEBI), stamp on the buy, a DP charge on a delivery sell, all times the
    calibration factor. Returns a dict of Decimals rounded to the paisa."""
    m = model if model is not None else load_charges()
    seg = m["intraday" if intraday else "delivery"]
    b, s, q = D(buy), D(sell), D(qty)
    tb, ts = b * q, s * q
    turn = tb + ts
    cap = D(seg.get("brokerage_cap") or 0)

    def brokerage(v):
        x = v * _pct(seg.get("brokerage_pct"))
        return min(cap, x) if cap > 0 else x
    brok = brokerage(tb) + brokerage(ts)
    stt = tb * _pct(seg.get("stt_buy_pct")) + ts * _pct(seg.get("stt_sell_pct"))
    txn = turn * _pct((m.get("exchange_txn_pct") or {}).get("NSE"))
    sebi = turn * D(m.get("sebi_per_crore") or 0) / Decimal(10000000)
    gst = (brok + txn + sebi) * _pct(m.get("gst_pct"))
    stamp = tb * _pct(seg.get("stamp_buy_pct"))
    dp = D(seg.get("dp_per_scrip_sell") or 0) if not intraday else Decimal(0)
    out = {"brokerage": brok, "stt": stt, "txn": txn, "sebi": sebi, "gst": gst, "stamp": stamp, "dp": dp}
    total = sum(out.values())
    factor = D(((m.get("calibration") or {}).get("factor")) or 1)
    out["total"] = total * factor
    out["factor"] = factor
    return {k: v.quantize(CENT, rounding=ROUND_HALF_UP) if k != "factor" else v for k, v in out.items()}


def load_bands(path=None):
    """trader/data/bands.json -> the document, or None when it is not there."""
    path = BANDS_PATH if path is None else path
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise PlanError("%s is not valid JSON: %s" % (shown(path), e))
    return doc if isinstance(doc, dict) and isinstance(doc.get("bands"), dict) else None


def default_qty(entry):
    q = int(DEFAULT_NOTIONAL / D(entry))
    return max(q, 1)


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    s = format(D(x).normalize(), "f")
    return s


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    if t in ("swing", "swing-coc", "coc"):
        return "swing", None
    m = re.match(r"^(price|pct|atr):\s*(?:\$|\u20b9|rs\.?)?\s*([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X, atr:K or swing (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*(?:\$|\u20b9|rs\.?)?\s*([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, swing_coc=None, swing_bars=None):
    """The stop, the target and the risk, in exact cents. Raises PlanError with the reason."""
    e = cents(entry)
    if e <= 0:
        raise PlanError("the entry price must be above \u20b90")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = cents(or_high), cents(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (money(oh), money(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = \u20b9%s" % (money(oh), money(ol), num(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = \u20b9%s" % (money(e), num(stop_param), num(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (num(stop_param), atr_bars)
        detail = "ATR \u20b9%s over the %d bars to the entry%s" % (
            num(a.quantize(Decimal("0.0001"))), atr_bars, " (fewer than 14 so far)" if atr_bars < ATR_BARS else "")
    elif stop_rule == "swing":
        if swing_coc is None:
            raise PlanError("a swing stop needs the swing change-of-character level (the bars)")
        raw = D(swing_coc)
        label = "swing change-of-character level"
        detail = "structure level \u20b9%s%s, on a close" % (num(raw), " over %d bars" % swing_bars if swing_bars else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = floor_cents(raw)
    if stop != D(raw):
        detail = (detail + ", rounded down" if detail else "\u20b9%s rounded down" % num(raw))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (money(stop), label, money(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above \u20b90" % money(stop))
    risk = e - stop
    floor_risk = e * MIN_RISK_PCT / 100
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than 0.05%% of the price (%s on %s): too tight to "
                        "survive the spread, normal wiggle would stop you out. Widen it."
                        % (money(risk), money(floor_risk.quantize(CENT, rounding=ROUND_HALF_UP)), money(e)))
    if target_rule == "r":
        target_param = D(target_param, "the target R")      # an int or float R works too, not only parse_target's
        target = cents(e + target_param * risk)
        t_label = r_label(target_param)
    else:
        target = cents(target_param)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (money(target), money(e)))
    rr = ((target - e) / risk).quantize(CENT, rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr}


# ---------------------------------------------------------------- bars for ATR (same reader as trader-entry)

def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    """query1 first, query2 on 429 or 5xx; one retry after a pause on 429."""
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                code, body = e.code, None
                try:
                    if code == 404:     # Yahoo says why in the body: an unknown ticker
                        body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = None
                finally:
                    e.close()
                if code == 404:
                    if body:
                        return body
                    raise PlanError("Yahoo has no chart at %s (HTTP 404)" % url.split("?")[0])
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise PlanError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise PlanError("Yahoo did not answer (%s on query1 and query2)" % last)


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def intraday_cache_path(sym):
    return shown(os.path.join(DATA_DIR, "intraday", "%s-5m.json" % sym))   # same text on every OS


def warn(msg):
    print("exit.py: " + msg, file=sys.stderr)


def cache_when(text, path):
    """The newest bar in a cached chart, in IST; else when the file was saved."""
    try:
        ts = [t for t in json.loads(text)["chart"]["result"][0]["timestamp"] if t]
        return to_ist(EPOCH + datetime.timedelta(seconds=max(ts))).strftime("%Y-%m-%d %H:%M IST")
    except (ValueError, KeyError, TypeError, IndexError):
        pass
    try:
        return to_ist(EPOCH + datetime.timedelta(seconds=os.path.getmtime(path))).strftime("%Y-%m-%d %H:%M IST")
    except OSError:
        return "an unknown time"


def read_intraday_cache(sym, interval="5m", why=None):
    """trader/data/intraday/<SYM>-5m.json (trader-data's cache), with a freshness note on stderr."""
    path = intraday_cache_path(sym)
    lead = ("%s. " % why) if why else ""
    if interval != "5m":
        raise PlanError("%sThe cache holds 5-minute bars only: use --interval 5m, or %s" % (lead, DATA_HINT))
    if not os.path.exists(path):
        raise PlanError("%sNo cache at %s either. Next: %s" % (lead, shown(path), DATA_HINT))
    try:
        text = read_text(path)
    except (OSError, ValueError) as e:
        raise PlanError("could not read %s: %s" % (shown(path), getattr(e, "strerror", None) or e))
    warn("%susing cached data from %s (%s), run `python3 trader-data/data.py intraday %s` to refresh"
         % (("live fetch failed (%s); " % why) if why else "", cache_when(text, path), shown(path), sym))
    return text


def load_chart(sym, interval, day, offline=None, cache=False):
    ysym = yahoo_symbol(sym)
    if offline:
        path = offline
        if os.path.isdir(offline):
            path = os.path.join(offline, ysym + ".json")
            if not os.path.exists(path):
                path += ".gz"
        if not os.path.exists(path):
            raise PlanError("no offline file %s" % shown(path))
        try:
            text = read_text(path)
        except (OSError, ValueError) as e:
            raise PlanError("could not read %s: %s" % (shown(path), getattr(e, "strerror", None) or e))
    elif cache:
        text = read_intraday_cache(sym, interval)
    else:
        back = (to_ist(utc_now()).date() - day).days
        rng = "1d" if back <= 0 else "5d" if back <= 6 else "1mo" if back <= 28 else "60d"
        if interval == "1m" and back > 6:
            raise PlanError("Yahoo keeps 1-minute bars for about a week")
        try:
            text = yahoo_get("/v8/finance/chart/%s?range=%s&interval=%s" % (ysym, rng, interval))
        except PlanError as e:
            text = read_intraday_cache(sym, interval, str(e))
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise PlanError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise PlanError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    if str(meta.get("symbol") or ysym).upper() != ysym:
        raise PlanError("the chart file holds %s, not %s" % (meta.get("symbol"), sym))
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        h, lo = at("high", i), at("low", i)
        bars.append({"t": int(t), "h": float(c if h is None else h), "l": float(c if lo is None else lo),
                     "c": float(c)})
    return {"bars": bars, "data_time": meta.get("regularMarketTime")}


def session_bars(chart, day, interval, upto_slot=None, now_utc=None):
    """Closed, on-grid bars of one NSE session (IST), oldest first (the live tick is dropped)."""
    step = INTERVALS[interval]
    s = session(day)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    if now_utc is None and chart.get("data_time"):
        now_utc = utc_of(chart["data_time"])
    local = to_ist(now_utc) if now_utc else None
    out = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue
        et = to_ist(utc_of(b["t"]))
        if et.date() != day or not (open_dt <= et < close_dt):
            continue
        if local and et + datetime.timedelta(seconds=step) > local:
            continue
        slot = int((et - open_dt).total_seconds()) // step
        if upto_slot is not None and slot > upto_slot:
            continue
        out[slot] = b
    return [out[k] for k in sorted(out)]


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first
    bar of the session uses its own high - low (no overnight gap)."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# --- swing engine: keep byte-identical in scan.py, backtest.py, invest.py and exit.py (tests/test_repo.py checks) ---
# Market structure, one bar at a time, closing basis. In an uptrend the first bar that fails to make a new high
# freezes that high as the pending swing high (pivot); a close above it is a break of structure (bos) and the
# lowest low of the pullback becomes the change-of-character level (coc), a structural trailing stop. A close
# beyond coc reverses the trend. Pullbacks shallower than `retrace` percent of the pivot do not move coc.
# Downtrends are the mirror image. Causal: no future bars, so it is safe to replay in a backtest.

def swing_new(retrace=5.0, sideways=20, min_bars=40):
    return {"trend": None, "hi": None, "lo": None, "pivot": None, "coc": None, "bars_since": 0, "total": 0,
            "legs": 0, "last_bos": None, "retrace": retrace or 0.0, "sideways_n": sideways, "min_bars": min_bars}


def _swing_shallow(s, depth, pivot):
    # depth / pivot < retrace %, cross-multiplied so an exact tie (3 / 30 vs 10 %) confirms
    return s["retrace"] > 0 and depth * 100 < s["retrace"] * pivot


def swing_step(s, h, l, c):
    """Feed one bar (high, low, close). Returns the bar's event: start_up, start_down, bos, reversal or None."""
    if h is None or l is None or c is None:
        return None
    s["total"] += 1
    if s["trend"] is None:
        if s["hi"] is None:
            s["hi"], s["lo"] = h, l
            return None
        ev = None
        if c > s["hi"]:
            s["trend"], s["coc"], s["hi"], ev = "up", s["lo"], h, "start_up"
        elif c < s["lo"]:
            s["trend"], s["coc"], s["lo"], ev = "down", s["hi"], l, "start_down"
        s["hi"], s["lo"] = max(s["hi"], h), min(s["lo"], l)
        return ev
    s["bars_since"] += 1
    if s["trend"] == "up":
        if s["pivot"] is not None:
            if h > s["hi"]:
                s["hi"], s["bars_since"] = h, 0
            s["lo"] = min(s["lo"], l)
        elif h > s["hi"]:
            s["hi"], s["lo"], s["bars_since"] = h, l, 0
        else:
            s["pivot"], s["lo"], s["bars_since"] = s["hi"], l, 1
        if c < s["coc"]:   # checked on every bar, a new-high bar included
            s.update(trend="down", coc=s["hi"], hi=None, pivot=None, lo=l, bars_since=0, legs=0)
            return "reversal"
        if s["pivot"] is not None and c > s["pivot"]:
            pivot, s["pivot"], s["bars_since"] = s["pivot"], None, 0
            if _swing_shallow(s, pivot - s["lo"], pivot):
                return None
            s["coc"], s["last_bos"] = s["lo"], pivot
            s["legs"] += 1
            return "bos"
        return None
    if s["pivot"] is not None:
        if l < s["lo"]:
            s["lo"], s["bars_since"] = l, 0
        s["hi"] = max(s["hi"], h)
    elif l < s["lo"]:
        s["lo"], s["hi"], s["bars_since"] = l, h, 0
    else:
        s["pivot"], s["hi"], s["bars_since"] = s["lo"], h, 1
    if c > s["coc"]:
        s.update(trend="up", coc=s["lo"], lo=None, pivot=None, hi=h, bars_since=0, legs=0)
        return "reversal"
    if s["pivot"] is not None and c < s["pivot"]:
        pivot, s["pivot"], s["bars_since"] = s["pivot"], None, 0
        if _swing_shallow(s, s["hi"] - pivot, pivot):
            return None
        s["coc"], s["last_bos"] = s["hi"], pivot
        s["legs"] += 1
        return "bos"
    return None


def swing_snapshot(s):
    out = {k: s[k] for k in ("trend", "coc", "pivot", "last_bos", "hi", "lo", "bars_since", "legs")}
    out["sideways"] = s["trend"] is not None and s["bars_since"] > s["sideways_n"]
    out["stable"] = s["total"] > s["min_bars"]
    return out


def swing_run(bars, retrace=5.0, sideways=20, min_bars=40):
    """bars: (high, low, close) oldest first. Returns (final snapshot, one event per bar)."""
    s = swing_new(retrace, sideways, min_bars)
    events = [swing_step(s, h, l, c) for h, l, c in bars]
    return swing_snapshot(s), events
# --- end swing engine ---


# ---------------------------------------------------------------- swing stop

def read_daily_bars(sym, before, daily_dir=None):
    """Daily bars of trader/data/daily/<SYM>.adj.csv (else .csv) dated before `before`, oldest first, as
    {"h","l","c"}. Returns (bars, path)."""
    base = daily_dir or DAILY_DIR
    path = None
    for name in (sym + ".adj.csv", sym + ".csv"):
        if os.path.exists(os.path.join(base, name)):
            path = os.path.join(base, name)
            break
    if path is None:
        raise PlanError("no daily bars for %s in %s: a CNC swing stop reads the daily cache. Next: %s"
                        % (sym, shown(base), DATA_HINT))
    rows = []
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                row = {str(k).strip().lower(): v for k, v in row.items() if k}
                try:
                    d = datetime.date.fromisoformat(str(row.get("date") or "")[:10])
                    h, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
                except (KeyError, TypeError, ValueError):
                    continue
                if d < before:
                    rows.append((d, {"h": h, "l": lo, "c": cl}))
    except (OSError, UnicodeDecodeError) as e:
        raise PlanError("could not read %s: %s" % (shown(path), getattr(e, "strerror", None) or e))
    rows.sort(key=lambda r: r[0])
    return [b for _, b in rows], path


def swing_stop(bars, retrace=0.0, min_bars=0, what="session"):
    """The change-of-character level of `bars` ({"h","l","c"}, oldest first) as a long's stop. Refuses, with
    the reason, when the swing trend is not up (or not started), or there are not enough bars."""
    snap, _ = swing_run([(b["h"], b["l"], b["c"]) for b in bars], retrace, 20, min_bars)
    n = len(bars)
    if snap["trend"] is None:
        raise PlanError("there is no swing trend yet in the %d %s bars (price has not closed outside its first "
                        "range): a swing stop needs an uptrend. Pick another stop." % (n, what))
    if snap["trend"] != "up":
        raise PlanError("the swing trend is down on the %d %s bars (the change-of-character level %s is above "
                        "price): a long's swing stop needs an uptrend. Pick another stop."
                        % (n, what, num(D(snap["coc"]))))
    if not snap["stable"]:
        raise PlanError("only %d %s bars: a swing stop needs more than %d so the first level is not an accident "
                        "of the opening range" % (n, what, min_bars))
    if snap["coc"] is None:
        raise PlanError("the swing trend is up but has no change-of-character level yet")
    return {"coc": snap["coc"], "pivot": snap["pivot"], "trend": snap["trend"], "bars": n,
            "retrace_pct": retrace, "bars_kind": what}


# ---------------------------------------------------------------- plan

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, shown(path)))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (shown(path), e))


def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    return cfg


def resolve_flat_by(day, explicit, cfg, entry_time):
    """(HH:MM, note). NSE has no early closes; the flat-by must be before the 15:30 close."""
    t = parse_hhmm(explicit or cfg.get("flat_by") or DEFAULTS["flat_by"], "--flat-by" if explicit else "flat_by")
    note = ""
    if t >= CLOSE:
        raise PlanError("%s %s is at or after the %s close" % ("--flat-by" if explicit else "flat_by in the config",
                                                               hm(t), ampm(CLOSE)))
    if t <= OPEN:
        raise PlanError("flat-by %s is at or before the 9:15 AM open" % hm(t))
    if t > MIS_SQUARE_OFF:
        note = "after the broker's MIS auto square-off (about 15:20): an intraday position may be closed for you first"
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (ampm(entry_time), hm(t)))
    return hm(t), note


def gather(a):
    """The entry facts, from trader/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError("v2 plans longs only: no stops above a short entry. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        info = {"symbol": nse_symbol(a.symbol), "entry": a.entry, "setup": a.setup or ("orb" if a.or_high is not None else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "source": "flags",
                "level": getattr(a, "level", None)}
    else:
        path = a.from_path or "trader/entry.json"
        doc = read_json(path, "entry (run /trader-entry first, or give --symbol and --entry)")
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s: v2 plans longs only" % (shown(path), doc.get("side")))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (shown(path), doc.get("symbol"), doc.get("status") or "unknown"))
        orr = doc.get("opening_range") or {}
        info = {"symbol": str(doc.get("symbol") or "").upper(), "entry": a.entry if a.entry is not None else doc.get("price"),
                "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
                "entry_time": a.time or (doc.get("time") if a.entry is None else None),
                "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
                "interval": a.interval or doc.get("interval") or "5m",
                "or_high": a.or_high if a.or_high is not None else orr.get("high"),
                "or_low": a.or_low if a.or_low is not None else orr.get("low"),
                "or_minutes": orr.get("minutes") or a.or_minutes, "source": shown(path),
                "level": getattr(a, "level", None) if getattr(a, "level", None) is not None else doc.get("level")}
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % shown(path))
        info["symbol"] = nse_symbol(info["symbol"])
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m or 1m")
    return info


def build_plan(a, now_utc):
    info = gather(a)
    cfg = read_config(a.config)
    product = str(getattr(a, "product", None) or "MIS").upper()
    if product not in ("MIS", "CNC"):
        raise PlanError("--product is MIS (intraday, the default) or CNC (delivery), not %r" % product)
    if product == "CNC" and a.flat_by:
        raise PlanError("--flat-by is for intraday (MIS) plans: a CNC delivery plan has no flat-by")
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X, atr:K "
                            "or swing" % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    day = None
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]))
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = to_ist(now_utc).date()
    entry_time = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None
    atr = atr_n = swing = None
    if stop_rule == "swing" and product == "CNC":
        dbars, dpath = read_daily_bars(info["symbol"], day, getattr(a, "daily_dir", None))
        swing = swing_stop(dbars, SWING_DAILY_RETRACE, SWING_DAILY_MIN_BARS, "daily")
        swing["source"] = shown(dpath)
    elif stop_rule in ("atr", "swing"):
        chart = load_chart(info["symbol"], info["interval"], day, a.offline, getattr(a, "cache", False))
        bars = session_bars(chart, day, info["interval"], info["entry_bar"], parse_now(a.now) if a.now else None)
        if len(bars) < 3:
            raise PlanError("%s %s stop needs at least 3 closed %s bars of %s on %s by the entry; found %d"
                            % ("an" if stop_rule == "atr" else "a", stop_rule, info["interval"], info["symbol"],
                               day, len(bars)))
        if stop_rule == "atr":
            atr, atr_n = atr_of(bars)
        else:
            swing = swing_stop(bars, 0.0, 0, "%s session" % info["interval"])
            swing["source"] = "%s session bars" % info["interval"]
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n, swing["coc"] if swing else None, swing["bars"] if swing else None)
    if product == "CNC":
        flat_by, flat_note = None, ""
    else:
        flat_by, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_time)
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(cents(info["or_high"])), "low": float(cents(info["or_low"])),
               "minutes": int(info["or_minutes"] or 15)}
    plan = {"created_by": "trader-exit", "symbol": info["symbol"], "side": "buy", "date": day.isoformat(),
            "setup": info["setup"], "entry": float(c["entry"]),
            "entry_time": ampm(entry_time) if entry_time else None,
            "entry_bar": info["entry_bar"], "interval": info["interval"], "opening_range": orr,
            "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": flat_by,
            "stop_detail": c["stop_detail"], "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE}
    if product == "CNC":
        plan["product"] = "CNC"      # only a delivery plan says so: trader-budget reads it, MIS stays as before
    if info.get("level") is not None and info["setup"] == "level":
        plan["level"] = float(cents(info["level"]))    # trader-timemachine replays "first close above this"
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    if swing is not None:
        plan["swing"] = {"coc": swing["coc"], "trend": swing["trend"], "bars": swing["bars"],
                         "retrace_pct": swing["retrace_pct"], "source": swing["source"]}
    qty = a.qty if a.qty else default_qty(c["entry"])
    model = load_charges(getattr(a, "charges", None))
    intraday = product == "MIS"
    at_stop = costs(c["entry"], c["stop"], qty, intraday, model)["total"]
    at_target = costs(c["entry"], c["target"], qty, intraday, model)["total"]
    cal = model.get("calibration") or {}
    plan["costs"] = {"qty": qty, "at_stop": float(at_stop), "at_target": float(at_target),
                     "per_share_at_stop": float((at_stop / qty).quantize(CENT, rounding=ROUND_HALF_UP)),
                     "label": "%s %s charges for a round trip, %s" % (
                         str(model.get("broker") or "zerodha").capitalize(),
                         "intraday (MIS)" if intraday else "delivery (CNC)", charges_label(model)),
                     "estimate": charges_label(model), "verified": model.get("verified"),
                     "calibration_factor": float(D(cal.get("factor") or 1)),
                     "calibration_samples": int(cal.get("samples") or 0)}
    net_risk = c["risk"] * qty + at_stop
    net_reward = (c["target"] - c["entry"]) * qty - at_target
    plan["net_reward_risk"] = float((net_reward / net_risk).quantize(CENT, rounding=ROUND_HALF_UP))
    warnings = []
    move = (c["risk"] / c["entry"] * 100)
    bands_doc = None if a.band is not None else load_bands(getattr(a, "bands", None))
    band = (bands_doc or {}).get("bands", {}).get(info["symbol"]) if bands_doc else None
    if band is not None and not isinstance(band, dict):
        band = None
    if band is not None:
        src = "%s, as of %s" % (bands_doc.get("source") or "bands.json", bands_doc.get("as_of") or "unknown date")
        plan["band"] = {"band_pct": band.get("band_pct"), "lower": band.get("lower"), "upper": band.get("upper"),
                        "as_of": bands_doc.get("as_of"), "source": bands_doc.get("source")}
        lower, bp = band.get("lower"), band.get("band_pct")
        if lower is not None and c["stop"] <= D(lower):
            warnings.append("The stop %s is at or under %s's lower circuit %s (%s band, %s): if the stock locks "
                            "at the lower circuit, a stop there cannot fill." % (
                                money(c["stop"]), info["symbol"], money(lower),
                                ("%s%%" % num(D(bp))) if bp is not None else "no", src))
        elif bp is not None and move >= D(bp):
            warnings.append("The stop is %s%% under the entry, at or beyond %s's %s%% circuit band (%s): if the "
                            "stock hits the lower circuit, a stop there may not fill." % (
                                move.quantize(CENT), info["symbol"], num(D(bp)), src))
        elif bp is None:
            warnings.append("%s has no price band (%s): no circuit stops it, so a fast fall can run far past "
                            "the stop." % (info["symbol"], src))
        if bands_doc.get("as_of") and bands_doc.get("as_of") != day.isoformat():
            warnings.append("bands.json is as of %s, not the plan's day %s: refresh with "
                            "`python3 trader-data/data.py bands`." % (bands_doc.get("as_of"), day.isoformat()))
    elif a.band is not None:
        if move >= D(a.band):
            warnings.append("The stop is %s%% under the entry, at or beyond the stock's %s%% circuit band: if the "
                            "stock hits the lower circuit, a stop there may not fill." % (
                                move.quantize(CENT), num(D(a.band))))
    else:
        over = [b for b in BANDS if b >= 5 and move >= b]
        if over:
            warnings.append("The stop is %s%% under the entry, beyond a %d%% circuit band: if this stock has a %d%% "
                            "band (check NSE, or pass --band), a stop past the lower circuit may not fill."
                            % (move.quantize(CENT), max(over), max(over)))
    hn = holiday_note(day)
    if hn:
        warnings.append(hn + ".")
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    if product == "CNC":
        warnings.append("Delivery (CNC): the position is held overnight, so a gap at the open can fill well past "
                        "the stop, and the stop needs a resting order or an alert of its own. This plan places nothing.")
    if swing is not None:
        warnings.append("The swing level is a closing-basis stop: the structure breaks on a close under it, a wick "
                        "through it does nothing. As a price stop it is tighter than the structure itself.")
    mrr = cfg.get("min_reward_risk")
    try:
        if mrr is not None and c["reward_risk"] < D(mrr):
            warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: trader-risk checks that."
                            % (ratio(c["reward_risk"]), num(D(mrr))))
    except PlanError:
        pass
    today = to_ist(now_utc).date()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    return plan, warnings


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s" % plan["entry_time"]
        if plan.get("entry_bar") is not None:
            when += " (bar %d)" % plan["entry_bar"]
    cnc = plan.get("product") == "CNC"
    if cnc:
        out = ["Exit plan: %s long, delivery (CNC), %s, %s" % (plan["symbol"], day.strftime("%a %Y-%m-%d"), setup)]
    else:
        out = ["Exit plan: %s long, %s, %s on %s bars" % (
            plan["symbol"], day.strftime("%a %Y-%m-%d"), setup, "5-minute" if plan["interval"] == "5m" else "1-minute")]
    out.append("  Entry         %s%s" % (money(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (money(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (money(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (money(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    cs = plan.get("costs")
    if cs:
        out.append("  Costs         about %s at the stop, %s at the target for %d shares (%s charges, %s); "
                   "reward:risk after costs %s" % (money(cs["at_stop"]), money(cs["at_target"]), cs["qty"],
                                                   "delivery" if cnc else "intraday",
                                                   cs.get("estimate") or "estimate", ratio(plan["net_reward_risk"])))
    bd = plan.get("band")
    if bd:
        out.append("  Circuit band  %s, lower circuit %s, upper %s (bands.json %s)" % (
            ("%s%%" % num(D(bd["band_pct"]))) if bd.get("band_pct") is not None else "no band",
            money(bd["lower"]) if bd.get("lower") is not None else "-",
            money(bd["upper"]) if bd.get("upper") is not None else "-", bd.get("as_of") or ""))
    if cnc:
        out.append("  Hold          delivery (CNC): no flat-by, held overnight (the sell carries the DP charge)")
    else:
        out.append("  Flat by       %s IST, win or lose: no overnight hold (MIS auto square-off about 15:20)" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


def cmd_plan(a):
    now = parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % shown(a.write))
    print("Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % shown(a.plan))
        return 1
    plan = read_json(a.plan, "plan")
    need = ("symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk")
    for k in need + (() if plan.get("product") == "CNC" else ("flat_by",)):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (shown(a.plan), k))
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    for line in describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan)):
        print(line)
    today = to_ist(parse_now(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (trader-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default trader/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today)")
    p.add_argument("--time", help="entry time, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, price:X, pct:X, atr:K, swing")
    p.add_argument("--product", type=str.upper, default="MIS", choices=["MIS", "CNC"],
                   help="MIS intraday (default, flat by 15:15) or CNC delivery (no flat-by, delivery charges)")
    p.add_argument("--level", type=float, help="the price a level setup closes above (kept in plan.json for the replay)")
    p.add_argument("--daily-dir", default=DAILY_DIR, help="daily bars for a CNC swing stop (default %s)" % shown(DAILY_DIR))
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--flat-by", help="HH:MM IST (default: config flat_by, 15:15; MIS square-off is about 15:20)")
    p.add_argument("--qty", type=int, help="shares, for the cost estimate (default: about Rs 1 lakh worth)")
    p.add_argument("--band", type=float, choices=[2, 5, 10, 20], help="the stock's NSE circuit band in percent")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON for an atr stop")
    p.add_argument("--cache", action="store_true",
                   help="read trader/data/intraday/<SYMBOL>-5m.json (trader-data) instead of Yahoo")
    p.add_argument("--bands", default=BANDS_PATH, help="price bands file (default %s)" % shown(BANDS_PATH))
    p.add_argument("--charges", default=CHARGES_PATH, help="charges override (default %s)" % shown(CHARGES_PATH))
    p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-19 10:15"')
    p.add_argument("--write", nargs="?", const="trader/plan.json", metavar="PATH",
                   help="save the plan (default trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
