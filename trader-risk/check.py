#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk (NSE cash equities). Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json),
prices (trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-19 10:15"        # pin the clock (IST, India time)
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts
    python3 check.py --bands trader/data/bands.json --charges trader/charges.json

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        missing symbol or side, qty not a whole number of shares
                (no fractional shares, no rupee-notional orders), product
                not MIS or CNC, a limit order without a limit price, unknown
                type; a bracket that is not day or misses a leg
  asset         options and futures (F&O is out of scope), crypto and
                anything that is not an NSE symbol
  short         selling more than you hold, unless "allow_short": true; a
                short is MIS (intraday) only, never CNC (no overnight short in cash)
  circuit       a limit or stop price outside today's price band (from
                trader/data/bands.json; for an MIS bracket the stop-loss and
                target legs too): the exchange would reject it. A stop past the
                lower circuit is a warning: it may not fill if the stock is
                locked at the circuit. No band data: a warning when the stop is
                over 5% away ("band unknown: run trader-data bands")

What blocks an entry (on top):

  blocked       a symbol on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need MIS leverage
                ("allow_mis_leverage" is off by default: cash only)
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true; a stop on the wrong side
  loss          qty x (entry - stop) plus estimated charges (brokerage,
                STT, exchange, SEBI, GST, stamp duty, DP: costs()) over
                max_loss_per_trade_inr. The entry is the limit price for a
                limit order, else the HIGHER of the plan's entry and a fresh
                price (under 15 minutes old)
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk
  daily loss    the max losses of today's entries plus this one over max_daily_loss_inr
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (IST), or
                the market is closed (weekend, NSE holiday, outside 09:15 to 15:30)
  mis cutoff    a new MIS (intraday) entry after no_mis_after (default 15:00):
                the broker squares MIS off around 15:20

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
Charges are an estimate: Zerodha rates verified 2026-10-29 (embedded), deep-merged
with trader/charges.json if present, times its calibration factor, which
trader-broker `ticket.py reconcile` fits to your contract notes. The contract note
is the real number.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 8000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 100, "max_daily_loss_inr": 300, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "15:00", "no_mis_after": "15:00", "flat_by": "15:15",
    "default_product": "MIS", "paper": True, "blocked": [], "allow_mis_leverage": False, "mis_leverage": 5,
    "allow_short": False,
}
# the fields that identify an order (trader-broker/ticket.py has the same tuple)
ORDER_FIELDS = ("symbol", "side", "qty", "product", "type", "time_in_force", "limit_price",
                "stop_price", "order_class", "take_profit_limit_price", "stop_loss_stop_price", "tag")
PRICE_FIELDS = ("qty", "limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price")
TYPES = {"market", "limit", "stop", "stop_limit"}      # stop = SL-M, stop_limit = SL at Zerodha
TIFS = {"day", "ioc"}
PRODUCTS = {"MIS", "CNC"}
OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")                       # a US option symbol
FNO = re.compile(r"^[A-Z0-9&\-]+\d{2}([A-Z]{3}|\d{1,2}[A-Z0-9]{1,3})[A-Z0-9.]*(CE|PE|FUT)$")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "stop price": "the stop price", "stale price": "an old price"}

NSE_HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections (special holiday)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
NSE_EARLY_CLOSE = {}   # NSE has no early closes; the Muhurat session is treated as closed
BUILTIN_HOLIDAYS = dict(NSE_HOLIDAYS)   # the offline fallback; NSE_HOLIDAYS also holds calendar.json
BUILTIN_YEARS = (2026,)
# Normal-hours sessions on a weekend or holiday: the market is open 09:15-15:30.
BUILTIN_SPECIAL = {"2026-02-01": "Union Budget (Sunday)"}
# Muhurat: one evening hour, times set by circular and not tracked here; the regular market is closed.
BUILTIN_MUHURAT = {"2026-11-08": "Diwali Laxmi Pujan (Muhurat), Sunday"}
SPECIAL_SESSIONS = dict(BUILTIN_SPECIAL)
MUHURAT_SESSIONS = dict(BUILTIN_MUHURAT)
CALENDAR_YEARS = BUILTIN_YEARS
CALENDAR_PATH = "trader/data/calendar.json"
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, fixed +05:30, no DST)

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise RiskError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def _iso_map(m):
    """{"2027-01-26": "Republic Day"} -> the same with bad keys dropped; anything else is corrupt."""
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
    """trader/data/calendar.json (trader-data: data.py calendar) -> dict, or None when it is
    missing or unreadable. Never raises: the built-in table is the fallback."""
    try:
        with open(path or CALENDAR_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return None
        return {"years": sorted(set(int(y) for y in (doc.get("years") or []))),
                "holidays": _iso_map(doc.get("holidays")),
                "special_sessions": _iso_map(doc.get("special_sessions"))}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def is_muhurat(name):
    low = str(name).lower()
    return "muhurat" in low or "laxmi pujan" in low


def apply_calendar(path=None):
    """Rebuild NSE_HOLIDAYS, SPECIAL_SESSIONS, MUHURAT_SESSIONS and CALENDAR_YEARS from the built-in
    tables plus calendar.json. For a year the file lists, the file replaces the built-in holidays.
    Same rules as trader-alerts/alerts.py."""
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


apply_calendar()   # at import, from trader/data/calendar.json when it exists


def years_text():
    return ", ".join(str(y) for y in CALENDAR_YEARS)


def trading_day(d):
    """(is_trading_day, reason). Normal-hours special sessions open, weekends and the NSE holiday
    list closed. The Muhurat evening session is closed for the regular market, with its own reason."""
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


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an IST time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (IST). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if d.year not in CALENDAR_YEARS:
        warns.append("holiday list covers %s only: check nseindia.com for %s" % (years_text(), d.isoformat()))
    ok, why = trading_day(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    if t < OPEN or now_ist.time() >= CLOSE:
        return ["window: the market is closed (%s IST; the session is %s to %s)"
                % (stamp, OPEN.strftime("%H:%M"), CLOSE.strftime("%H:%M"))], warns
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- charges (a calibrated estimate)
# Zerodha equity charges, verified 2026-10-29 from zerodha.com/charges. trader/charges.json
# (optional) is deep-merged over these, and its calibration.factor (written by trader-broker
# `ticket.py reconcile` from your contract notes) scales the result. The same block is in
# trader-risk/check.py, trader-budget/size.py and trader-broker/ticket.py.

CHARGES_DEFAULTS = {
    "broker": "zerodha", "verified": "2026-10-29 zerodha.com/charges",
    "intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "stamp_buy_pct": 0.003},
    "delivery": {"brokerage_pct": 0, "brokerage_cap": 0, "stt_buy_pct": 0.1, "stt_sell_pct": 0.1,
                 "stamp_buy_pct": 0.015, "dp_per_scrip_sell": 15.34},
    "exchange_txn_pct": {"NSE": 0.00307}, "sebi_per_crore": 10, "gst_pct": 18,
    "calibration": {"factor": 1.0, "samples": 0, "updated": None},
}
CHARGES_FILE = "trader/charges.json"


def deep_merge(base, over):
    """base with over laid on top, dicts merged key by key (neither is changed)."""
    out = {}
    for k, v in base.items():
        out[k] = deep_merge(v, {}) if isinstance(v, dict) else v
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_charges(path=None):
    """The charges model: CHARGES_DEFAULTS deep-merged with trader/charges.json if it exists.
    path None = CHARGES_FILE; "" = the defaults only. A broken file falls back to the defaults."""
    path = CHARGES_FILE if path is None else path
    if not path or not os.path.exists(path):
        return deep_merge(CHARGES_DEFAULTS, {})
    # read every time: a cache keyed on the file's mtime goes stale when a rewrite lands in the same
    # timestamp tick as the read before it (reconcile writes the calibration, then --show reads it)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return deep_merge(CHARGES_DEFAULTS, {})
    return deep_merge(CHARGES_DEFAULTS, doc if isinstance(doc, dict) else {})


def charges_factor(model):
    try:
        f = float((model.get("calibration") or {}).get("factor") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return f if f > 0 else 1.0


def charges_label(model=None):
    """"estimate", or "estimate (calibrated from N contract notes)" once reconcile has run."""
    model = model if model is not None else load_charges()
    try:
        n = int((model.get("calibration") or {}).get("samples") or 0)
    except (TypeError, ValueError):
        n = 0
    return "estimate (calibrated from %d contract notes)" % n if n > 0 else "estimate"


def charge_parts(buy_value, sell_value, product="MIS", model=None, buy_orders=None, sell_orders=None,
                 dp_scrips=None, exchange="NSE"):
    """Itemised charges in rupees, BEFORE the calibration factor.

    MIS uses the "intraday" rates, CNC the "delivery" rates. Brokerage is per executed
    order: buy_orders / sell_orders are each order's value (default one order a side).
    GST is on brokerage + exchange txn + SEBI. The DP charge is per scrip per CNC sell
    day (dp_scrips, default 1 when there is a CNC sell) and already includes GST."""
    m = model if model is not None else load_charges()
    cnc = str(product or "MIS").upper() == "CNC"
    seg = m.get("delivery" if cnc else "intraday") or {}

    def pct(k):
        return float(seg.get(k) or 0) / 100.0

    buy, sell = float(buy_value or 0), float(sell_value or 0)
    orders = [float(v) for v in (buy_orders if buy_orders is not None else [buy]) if float(v) > 0]
    orders += [float(v) for v in (sell_orders if sell_orders is not None else [sell]) if float(v) > 0]
    cap, rate = float(seg.get("brokerage_cap") or 0), pct("brokerage_pct")
    brokerage = sum(min(cap, rate * v) if cap > 0 else rate * v for v in orders)
    stt = pct("stt_buy_pct") * buy + pct("stt_sell_pct") * sell
    tx = m.get("exchange_txn_pct")
    if isinstance(tx, dict):
        tx = tx.get(str(exchange or "NSE").upper(), tx.get("NSE"))
    txn = float(tx or 0) / 100.0 * (buy + sell)
    sebi = float(m.get("sebi_per_crore") or 0) * (buy + sell) / 1e7
    gst = float(m.get("gst_pct") or 0) / 100.0 * (brokerage + txn + sebi)
    stamp = pct("stamp_buy_pct") * buy
    dp = 0.0
    if cnc and sell > 0:
        dp = float(seg.get("dp_per_scrip_sell") or 0) * (1 if dp_scrips is None else dp_scrips)
    return {"brokerage": brokerage, "stt": stt, "exchange": txn, "sebi": sebi, "gst": gst, "stamp": stamp,
            "dp": dp}


def costs(qty, buy_price, sell_price, product="MIS", model=None):
    """Estimated charges for a round trip of qty shares, in rupees: charge_parts() times
    calibration.factor. Identical in trader-risk/check.py and trader-budget/size.py.
    An estimate: the contract note is the real number (trader-broker reconcile calibrates it)."""
    m = model if model is not None else load_charges()
    q = float(qty or 0)
    parts = charge_parts(q * float(buy_price or 0), q * float(sell_price or 0), product, m)
    raw = sum(parts.values())
    factor = charges_factor(m)
    out = {k: round(v, 2) for k, v in parts.items()}
    out["computed"] = round(raw, 2)
    out["factor"] = factor
    out["total"] = round(raw * factor, 2)
    out["approximate"] = True
    out["label"] = charges_label(m)
    return out


# ---------------------------------------------------------------- price bands (circuit limits)
# trader/data/bands.json, written by trader-data (bands, or import-kite from a Kite get_quotes
# dump): {"as_of": "YYYY-MM-DD", "source": "...", "bands": {"SYM": {"band_pct", "lower", "upper"}}}.
# The same block is in trader-risk/check.py and trader-broker/ticket.py.

BANDS_FILE = "trader/data/bands.json"
BAND_STALE_DAYS = 5          # older than this (calendar days) = unknown


def load_bands(path=None):
    """bands.json as a dict, or None when it is missing or broken. path None = BANDS_FILE."""
    path = BANDS_FILE if path is None else path
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("bands"), dict) else None


def band_for(doc, sym, today):
    """(band, why). band = {"band_pct", "lower", "upper", "as_of", "source", "today"} for the
    symbol from the most recent bands file, or None with why ("unknown" or a stale note)."""
    if not doc:
        return None, "unknown"
    b = (doc.get("bands") or {}).get(str(sym or "").upper())
    if not isinstance(b, dict):
        return None, "unknown"
    as_of = str(doc.get("as_of") or "")[:10]
    try:
        d = datetime.date.fromisoformat(as_of)
        t = datetime.date.fromisoformat(str(today)[:10])
    except ValueError:
        return None, "unknown"
    if d > t or (t - d).days > BAND_STALE_DAYS:
        return None, "stale (bands.json is as of %s)" % as_of

    def num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    lo, hi = num(b.get("lower")), num(b.get("upper"))
    pct = b.get("band_pct")
    try:
        pct = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct = None
    if lo is None or hi is None or lo >= hi:
        if "band_pct" in b and b["band_pct"] is None:
            return {"band_pct": None, "lower": None, "upper": None, "as_of": as_of,
                    "source": doc.get("source"), "today": d == t}, "no band"
        return None, "unknown"
    return {"band_pct": pct, "lower": lo, "upper": hi, "as_of": as_of, "source": doc.get("source"),
            "today": d == t}, "known"


def band_words(band):
    """"1306.80 to 1597.20 (10% band, as of 2026-10-19, nse)"."""
    if band.get("lower") is None:
        return "no price band (F&O stock: the exchange's dynamic price limits apply; as of %s)" % band["as_of"]
    pct = band.get("band_pct")
    pct_s = ("%s%% band" % (int(pct) if pct == int(pct) else pct)) if pct is not None else "no fixed band"
    src = (", " + str(band["source"])) if band.get("source") else ""
    return "%.2f to %.2f (%s, as of %s%s)" % (band["lower"], band["upper"], pct_s, band["as_of"], src)


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def inr(x):
    """12345678.9 -> "₹1,23,45,678.90" (Indian digit grouping; whole rupees drop the paise)."""
    x = float(x)
    neg = x < 0
    s = "%.2f" % abs(x)
    whole, paise = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    out = "₹" + whole + ("" if paise == "00" else "." + paise)
    return ("-" + out) if neg else out


money = inr


def limit_of(c, name):
    """A rupee limit from the config: name_inr."""
    return c.get(name + "_inr")


def _cent(x):
    """A price to the cent, half up (366.475 -> 366.48)."""
    return int(float(x) * 100 + 0.5 + 1e-9) / 100.0


def _p(x):
    return "%.2f" % float(x)


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None,
          bands=None, charges=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its IST date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh
    bands        the trader/data/bands.json dict (None = band unknown for every symbol)
    charges      the charges model (None = load_charges(): defaults + trader/charges.json)"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    model = charges if charges is not None else load_charges()
    label = charges_label(model)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    mis_cut = hhmm(c.get("no_mis_after"), "no_mis_after")
    late_for_mis = (mis_cut is not None and now_ist.time().replace(second=0, microsecond=0) > mis_cut)
    default_product = str(c.get("default_product") or "MIS").upper()
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append("LIVE account (\"paper\": false): these orders use real money")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        tif = str(o.get("time_in_force", "day")).strip().lower()
        qty = o.get("qty")
        product = str(o.get("product") or default_product).strip().upper()
        if sym.endswith(".NS"):
            sym = sym[:-3]
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        oc = str(o.get("order_class") or "").strip().lower()
        legs = o.get("take_profit_limit_price") is not None or o.get("stop_loss_stop_price") is not None
        bracket = oc == "bracket" or (not oc and legs)
        value = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if o.get("notional") is not None:
            reasons.append("format: NSE takes whole shares only: give qty, not a rupee notional amount")
        if qty is None:
            reasons.append("format: no qty (whole shares)")
        elif _f(qty) is not None and abs(_f(qty) - round(_f(qty))) > 1e-9:
            reasons.append("format: whole shares only (no fractional shares on NSE), got qty %r" % qty)
        if product not in PRODUCTS:
            reasons.append("format: product must be MIS (intraday) or CNC (delivery), got %r" % o.get("product"))
        elif o.get("product") is None:
            warns.append("no product given: treated as %s; put \"product\" on the order" % product)
        for k in PRICE_FIELDS:
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive number, got %r" % (k, o.get(k)))
                elif not isinstance(o.get(k), str):
                    warns.append("send %s as a string (\"%s\")" % (k, o.get(k)))
        if typ not in TYPES:
            reasons.append("format: unknown order type %r" % typ)
        if tif not in TIFS:
            reasons.append("format: unknown time_in_force %r" % tif)
        if typ in ("limit", "stop_limit") and o.get("limit_price") is None:
            reasons.append("format: a %s order needs a limit_price" % typ)
        if typ in ("stop", "stop_limit") and o.get("stop_price") is None:
            reasons.append("format: a %s order needs a stop_price" % typ)
        if o.get("extended_hours"):
            reasons.append("format: NSE has no extended-hours trading")
        if bracket:
            if tif != "day":
                reasons.append("format: a bracket (entry + SL-M stop + target) needs time_in_force day, got %r"
                               % tif)
            if o.get("take_profit_limit_price") is None or o.get("stop_loss_stop_price") is None:
                reasons.append("format: a bracket order needs both take_profit_limit_price and "
                               "stop_loss_stop_price")
        elif oc:
            warns.append("advanced order (%s): check every leg before you say yes" % oc)

        # asset
        compact = sym.replace(" ", "")
        if sym and (OCC.match(compact) or FNO.match(compact)):
            reasons.append("asset: %s looks like an option or future; F&O is out of scope, this pack "
                           "trades NSE cash equities only" % sym)
        elif sym and ("/" in sym or not TICKER.match(sym)):
            reasons.append("asset: %s does not look like an NSE symbol (e.g. RELIANCE, M&M, BAJAJ-AUTO); "
                           "this pack trades NSE stocks and ETFs only" % sym)

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("limit", "stop_limit"):
            ref, ref_from = _f(o.get("limit_price")), "limit"
        elif typ == "stop":
            ref, ref_from = _f(o.get("stop_price")), "stop price"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = _cent(ref)
                if live and ref_from == "price" and plan_entry and live > plan_entry + PAISA:
                    info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(ref), _p(plan_entry), _p(ref)))
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None and typ == "market":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        q = _f(qty)
        if q and ref:
            value = q * ref

        # circuit: today's price band from trader/data/bands.json
        band, band_state = band_for(bands, sym, today) if sym else (None, "unknown")
        if band and band.get("lower") is not None:
            lo, hi = band["lower"], band["upper"]
            today_band = "today's circuit" if band["today"] else "the latest circuit (%s)" % band["as_of"]
            own = [("limit_price", "limit price"), ("stop_price", "stop price")]
            if bracket and product == "MIS":
                # the MIS exits are placed today too, so the exchange checks them against today's band
                own += [("stop_loss_stop_price", "bracket stop-loss"), ("take_profit_limit_price", "target limit price")]
            for k, words in own:
                v = _f(o.get(k))
                if v and (v < lo - PAISA or v > hi + PAISA):
                    reasons.append("circuit: %s outside %s: %s is not within %s; the exchange rejects it"
                                   % (words, today_band, _p(v), band_words(band)))

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9:
            if q is not None:
                exiting = q <= held + 1e-9
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (sym, qty, sym))

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            mn, mx = _f(limit_of(c, "min_order")), _f(limit_of(c, "max_order"))
            if mn and value < mn - PAISA:
                later.append("size: %s is under your %s minimum order" % (money(value), money(mn)))
            if mx and value > mx + PAISA:
                later.append("size: %s is over your %s max order" % (money(value), money(mx)))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            lev = product == "MIS" and c.get("allow_mis_leverage")
            power = cash * float(c.get("mis_leverage") or 1) if lev else cash
            if value > power + PAISA:
                later.append("cash: needs %s but only %s cash; that would be MIS leverage (borrowed money), "
                             "which is off (\"allow_mis_leverage\": false)" % (money(value), money(cash))
                             if not lev else "cash: needs %s, over %s buying power at %sx MIS leverage"
                             % (money(value), money(power), _num(c.get("mis_leverage") or 1)))
            elif after_cash < -PAISA:
                warns.append("uses MIS leverage: %s on %s cash; a loss is still yours in full"
                             % (money(value), money(cash)))
            elif after_cash < buffer_inr - PAISA:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + PAISA:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            over = (q > held + 1e-9) if q is not None else held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))
            elif over and product != "MIS":
                reasons.append("short: a short sale in cash must be MIS (intraday, squared off the same day); "
                               "no overnight short with CNC")

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window, MIS cutoff
        max_loss = risk_ps = rr = stop = target = charges = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            leg_stop, plan_stop = _f(o.get("stop_loss_stop_price")), _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(o.get("take_profit_limit_price")) or _f(plan.get("target"))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append("the bracket stop %s differs from the plan's stop %s: the bracket's is the one "
                             "the broker uses" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg_stop:
                warns.append("the stop %s is in the plan only, not at the broker: set a stop alert "
                             "(trader-alerts: alerts.py add --from-plan)" % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): a bracket "
                                 "stop_loss_stop_price, from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    shares = q or 0
                    charges = costs(shares, ref if long else stop, stop if long else ref, product, model)["total"]
                    max_loss = round(shares * risk_ps + charges, 2)
                    cap = _f(limit_of(c, "max_loss_per_trade"))
                    if cap and max_loss > cap + PAISA:
                        later.append("loss: %s shares x %s risk a share + %s charges (%s) = %s at the stop, "
                                     "over your %s max loss per trade"
                                     % (_num(shares), _p(risk_ps), money(charges), label, money(max_loss),
                                        money(cap)))
                    if band and band.get("lower") is not None:
                        if long and stop < band["lower"] - PAISA:
                            warns.append("the stop %s is below the lower circuit %s: the stop may not fill if the "
                                         "stock hits lower circuit (nothing trades below %s that day)"
                                         % (_p(stop), _p(band["lower"]), _p(band["lower"])))
                        elif not long and stop > band["upper"] + PAISA:
                            warns.append("the stop %s is above the upper circuit %s: the stop may not fill if the "
                                         "stock hits upper circuit" % (_p(stop), _p(band["upper"])))
                    elif not band and risk_ps / ref > 0.05:
                        warns.append("the stop is %.1f%% away: band unknown: run trader-data bands (past a 5%% "
                                     "circuit band a stop may not fill)%s"
                                     % (100.0 * risk_ps / ref,
                                        "" if band_state == "unknown" else "; bands.json is " + band_state))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(limit_of(c, "max_daily_loss"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (money(used), money(max_loss), money(used + max_loss),
                                                       money(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if product == "MIS" and late_for_mis and not win_reasons:
                later.append("mis cutoff: no new MIS (intraday) entries after %s IST; the broker squares MIS "
                             "off around 15:20 (\"no_mis_after\")" % mis_cut.strftime("%H:%M"))
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s%s incl. ~%s charges (%s), reward:risk %s%s"
                            % (money(max_loss), (" (limit %s)" % money(limit_of(c, "max_loss_per_trade")))
                               if _f(limit_of(c, "max_loss_per_trade")) else "", money(charges), label,
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if band:
                info.append("circuit: " + band_words(band))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               money(loss_today + batch_loss), money(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind, "product": product,
            "amount": "%s shares" % qty,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "charges": charges, "max_loss": max_loss,
            "reward_risk": rr, "charges_label": label,
            "band": band, "band_state": band_state, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "charges_label": label, "charges_verified": model.get("verified"),
        "bands_as_of": (bands or {}).get("as_of"),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                   r["product"], r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    if any(r.get("charges") is not None for r in res["results"]):
        lines.append("Charges are an %s (Zerodha rates verified %s); the contract note is the real number. "
                     "Calibrate with trader-broker: ticket.py reconcile." % (res.get("charges_label", "estimate"),
                                                                           str(res.get("charges_verified") or
                                                                               "2026-10-29").split(" ")[0]))
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s (%s account)."
                 % (res["pass"], res["block"], res["now_ist"], money(res["cash_after"]),
                    "paper" if res["paper"] else "LIVE"))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--bands", help="price bands (default trader/data/bands.json, written by trader-data)")
    ap.add_argument("--charges", help="charges override and calibration (default trader/charges.json)")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-19")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        bands = load_bands(a.bands)
        model = load_charges(a.charges)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times, bands, model)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
