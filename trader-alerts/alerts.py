#!/usr/bin/env python3
"""Price alerts and the NSE clock for trader-alerts. Standard library only.

    python3 alerts.py clock                          # is NSE open? next open / close (IST)
    python3 alerts.py quote HINDCOPPER --write       # quotes -> trader/prices.json
    python3 alerts.py quote --holdings --write       # every symbol in your portfolio and plan
    python3 alerts.py add --from-plan                # stop, target and flat-by from trader/plan.json
    python3 alerts.py add --symbol HINDCOPPER --stop 308.10
    python3 alerts.py add --symbol HINDCOPPER --target 320.50
    python3 alerts.py add --at 15:15 --label "flat by 15:15"
    python3 alerts.py add --symbol RELIANCE --pct 3
    python3 alerts.py add --opening-bell
    python3 alerts.py check                          # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py check --now "2026-10-19 15:15" --offline tests/fixtures/trader-alerts
    python3 alerts.py list
    python3 alerts.py reset hindcopper-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        the price moved "pct" percent or more from the previous close
                  (or from "ref" if you give one); "direction": "up" or "down"
  opening_bell    NSE opened today at 09:15 IST (rings once per trading day)
  time            the IST clock passed "at" (HH:MM) on a trading day
                  (rings once per trading day). "before_close" is accepted for
                  old rule files; NSE has no early closes, so it changes nothing.

A rule with "date" is only watched on that IST date (rules from a plan are for
the plan's day only). An above / below alert rings ONCE and then stays quiet
until you reset it, unless the rule has "rearm": true (it re-arms when the
price crosses back). A pct_move alert rings at most once per day.

Quotes (free, delayed, unofficial; for alerts only, never for timing an order):
  yahoo   https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS (falls
          back to query2 on a 429, a 5xx or a network error; one retry with a
          pause on a 429; browser user agent; one request per symbol, half a
          second apart). Symbols get ".NS"; NIFTY is ^NSEI and BANKNIFTY is
          ^NSEBANK.
  cache   with --cache, or when Yahoo fails for a symbol: the trader-data
          cache, trader/data/intraday/<SYMBOL>-5m.json (last 5-minute close),
          then trader/data/daily/<SYMBOL>.csv (last daily close). The quote
          says "using cached data from <time>, run trader-data intraday SYMBOL
          to refresh". With no cache either, the error says: run python3
          trader-data/data.py doctor; or call Kite MCP get_historical_data
          and data.py import-kite.
  --offline DIR reads DIR/yahoo-<SYMBOL>.json (or .json.gz) instead.

NSE calendar: normal market 09:15-15:30 IST (pre-open 09:00-09:08 is not
tradable here), the 2024 to 2026 NSE equity holidays, no early closes. Other
years come from trader/data/calendar.json when it exists (python3
trader-data/data.py calendar writes and refreshes it): its holidays and
special sessions are merged over the built-in tables, and a year listed
in the file is treated as fully known. A missing or unreadable file means the
built-in tables only, never an error. Without a calendar for the year the
clock warns: "holiday list covers 2024, 2025, 2026 only: check nseindia.com".

Special sessions: a session on a weekend or holiday with normal hours (Budget
day, a Saturday DR session) counts as a trading day. The Muhurat session
(Diwali Laxmi Pujan, about one hour in the evening, timings set by an NSE
circular) is reported as a special session, not as a plain holiday, but the
normal 09:15-15:30 market is closed that day, so alerts and the clock treat
the regular market as closed and say why.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PAUSE = 0.5    # seconds between requests: be polite to free endpoints
BACKOFF = 2.0  # seconds to wait before the one retry after a 429
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
DATA_DIR = os.path.join("trader", "data")
DATA_HINT = ("run `python3 trader-data/data.py doctor`; or call Kite MCP get_historical_data and "
             "`python3 trader-data/data.py import-kite`")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
INDICES = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK"}
IST = datetime.timedelta(hours=5, minutes=30)

NSE_EARLY_CLOSE = {}   # NSE has no early closes; kept as a hook
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
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
PRE_OPEN = datetime.time(9, 0)


class AlertError(Exception):
    pass


def shown(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------- time (IST, fixed +05:30, no DST)

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local - IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def session(d):
    """(open_time, close_time) in IST, or None if closed all day."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def market_status(now_utc):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in CALENDAR_YEARS,
          "open": False, "reason": reason, "early_close": None,
          "special_session": SPECIAL_SESSIONS.get(d.isoformat()) or MUHURAT_SESSIONS.get(d.isoformat()),
          "muhurat": d.isoformat() in MUHURAT_SESSIONS}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        if o <= local.time() < c:
            st["open"] = True
            st["reason"] = "normal market"
        elif PRE_OPEN <= local.time() < o:
            st["reason"] = "pre-open (09:00-09:08 order collection, not tradable for these tools)"
        elif local.time() < o:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    nd = d if (s and local.time() < s[0]) else d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            st["next_open"] = "%s 09:15" % nd.isoformat()
            break
        nd += datetime.timedelta(days=1)
    return st


# ---------------------------------------------------------------- quotes

def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS, M&M -> M%26M.NS (URL-quoted), NIFTY -> ^NSEI, BANKNIFTY -> ^NSEBANK."""
    s = norm_symbol(sym)
    if s in INDICES:
        return INDICES[s].replace("^", "%5E")
    return s.replace("&", "%26") + ".NS"


def norm_symbol(sym):
    s = str(sym or "").strip().upper()
    if s in INDICES or s.startswith("^"):
        return s
    if s.endswith(".NS"):
        s = s[:-3]
    if not SYMBOL.match(s):
        raise AlertError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    return s


def yahoo_url(sym, host=YAHOO_HOSTS[0]):
    return "https://%s/v8/finance/chart/%s?range=1d&interval=1d" % (host, yahoo_symbol(sym))


def inr(x):
    """1234567.891 -> "₹12,34,567.89" (Indian digit grouping)."""
    f = round(float(x), 2)
    neg = f < 0
    whole, frac = divmod(int(round(abs(f) * 100)), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail, groups = s[:-3], s[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if neg else "") + "₹" + s + ".%02d" % frac


def parse_yahoo(text, sym):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo has no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if price is None:
        raise AlertError("Yahoo has no price for %s" % sym)
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t else "")
    q = (res[0].get("indicators", {}).get("quote") or [{}])[0]
    opens = [x for x in (q.get("open") or []) if x is not None]
    return {"symbol": sym.upper(), "price": float(price), "open": opens[0] if opens else None,
            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
            "prev_close": m.get("previousClose") or m.get("chartPreviousClose"), "time": when,
            "currency": m.get("currency"), "source": "yahoo"}


_urlopen = urllib.request.urlopen   # tests swap these two
_sleep = time.sleep


def _get(url, timeout=20):
    """(HTTP status, body). A network failure is an AlertError."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        return e.code, body
    except urllib.error.URLError as e:
        raise AlertError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch(url, timeout=20):
    code, text = _get(url, timeout)
    if code != 200:
        raise AlertError("%s answered HTTP %d" % (url.split("?")[0], code))
    return text


def fetch_yahoo(sym, timeout=20):
    """query1 first. A 429 waits BACKOFF seconds and tries once more; a 429, a 5xx or a
    network error after that moves on to query2. A 404 comes back as Yahoo's own error JSON."""
    last = None
    for host in YAHOO_HOSTS:
        url = yahoo_url(sym, host)
        try:
            code, text = _get(url, timeout)
            if code == 429:
                _sleep(BACKOFF)
                code, text = _get(url, timeout)
        except AlertError as e:
            last = str(e)
            continue
        if code == 200:
            return text
        if code == 404 and text.strip().startswith("{"):
            return text
        last = "%s answered HTTP %d" % (url.split("?")[0], code)
        if code != 429 and code < 500:
            break
    raise AlertError(last)


def _ist_text(utc):
    return to_ist(utc).strftime("%Y-%m-%d %H:%M IST")


def cache_quote(sym, why=None):
    """A quote from the trader-data cache: the intraday JSON first, then the daily CSV."""
    lead = ("%s. " % why) if why else ""
    fail = ("live fetch failed (%s); " % why) if why else ""
    ipath = os.path.join(DATA_DIR, "intraday", "%s-5m.json" % sym)
    dpath = os.path.join(DATA_DIR, "daily", "%s.csv" % sym)
    if os.path.exists(ipath):
        with open(ipath, encoding="utf-8") as fh:
            text = fh.read()
        try:
            res = json.loads(text)["chart"]["result"][0]
            meta = res.get("meta") or {}
            ts = res.get("timestamp") or []
            closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            pairs = [(t, c) for t, c in zip(ts, closes) if t is not None and c is not None]
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise AlertError("%s%s is not a Yahoo v8 chart: refresh it with python3 trader-data/data.py "
                             "intraday %s" % (lead, shown(ipath), sym))
        if pairs:
            t, price = pairs[-1]
            utc = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(t))
            return {"symbol": sym, "price": float(price), "open": None, "high": meta.get("regularMarketDayHigh"),
                    "low": meta.get("regularMarketDayLow"),
                    "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
                    "time": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "currency": meta.get("currency") or "INR",
                    "source": "cache",
                    "note": "%susing cached data from %s (%s), run `python3 trader-data/data.py intraday %s` "
                            "to refresh" % (fail, _ist_text(utc), shown(ipath), sym)}
    if os.path.exists(dpath):
        with open(dpath, encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get("close") or "").strip()]
        if rows:
            last = rows[-1]
            prev = float(rows[-2]["close"]) if len(rows) > 1 else None
            return {"symbol": sym, "price": float(last["close"]), "open": float(last["open"] or 0) or None,
                    "high": float(last["high"] or 0) or None, "low": float(last["low"] or 0) or None,
                    "prev_close": prev, "time": last["date"] + "T10:00:00Z", "currency": "INR", "source": "cache",
                    "note": "%susing cached data from the %s daily close (%s), run `python3 trader-data/data.py "
                            "daily %s` to refresh" % (fail, last["date"], shown(dpath), sym)}
    raise AlertError("%sNo cache for %s at %s or %s either. Next: %s" % (lead, sym, shown(ipath), shown(dpath), DATA_HINT))


def _offline_text(folder, source, sym):
    base = os.path.join(folder, "%s-%s.json" % (source, sym))
    if os.path.exists(base):
        with open(base, encoding="utf-8") as fh:
            return fh.read()
    if os.path.exists(base + ".gz"):
        with gzip.open(base + ".gz", "rt", encoding="utf-8") as fh:
            return fh.read()
    raise AlertError("no offline quote file %s" % shown(base))


def get_quotes(symbols, source="yahoo", offline=None, pause=PAUSE, cache=False):
    """{SYM: quote} and [errors]. One request per ticker, with a pause between. With
    cache=True, or when Yahoo fails for a ticker, the trader-data cache answers."""
    quotes, errors = {}, []
    for i, sym in enumerate(symbols):
        try:
            sym = norm_symbol(sym)
            if offline:
                text = _offline_text(offline, source, sym)
            elif cache:
                quotes[sym] = cache_quote(sym)
                continue
            else:
                if i and pause:
                    _sleep(pause)
                try:
                    text = fetch_yahoo(sym)
                except AlertError as e:
                    quotes[sym] = cache_quote(sym, str(e))
                    continue
            quotes[sym] = parse_yahoo(text, sym)
        except AlertError as e:
            errors.append(str(e))
    return quotes, errors


def write_prices(path, quotes, now_utc, source):
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source"),
                     "fetched": stamp}
    doc = {"as_of": stamp, "source": source,
           "note": ("Delayed quotes for alerts and sizing checks. Your broker's price is the one that fills. "
                    "detail.fetched is when each price was read."),
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (shown(path), e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell", "time"}
NO_QUOTE = {"opening_bell", "time"}


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s needs \"at\" as an IST time like \"15:15\"" % rule.get("id"))
    if rule.get("date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(rule["date"])):
        raise AlertError("rule %s: date must be YYYY-MM-DD" % rule.get("id"))


def active(rule, today):
    """A rule with a "date" is watched on that IST date only."""
    return not rule.get("date") or str(rule["date"]) == today


def _rang_today(stamp, today):
    """Did a rule that rang at this UTC stamp ring on this IST date?"""
    if not stamp:
        return False
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp[:10] == today
    return to_ist(utc).date().isoformat() == today


def ring_time(rule, status):
    """The time a time rule rings today (NSE has no early closes)."""
    return _hhmm(rule.get("at"))


def evaluate(rules, quotes, status, state, now_utc):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    local = to_ist(now_utc)
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        if not active(r, today):
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": "", "message": "Opening bell: NSE is open (%s IST), the close is 15:30."
                              % status["now_ist"]})
                rung[rid] = stamp
            continue
        if typ == "time":
            at = ring_time(r, status)
            if status.get("opens") and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                msg = "%s%s: it is %s IST (set for %s)" % (label[:1].upper(), label[1:],
                                                                 local.strftime("%H:%M"), at.strftime("%H:%M"))
                msg += (", the market closes at %s." % status["closes"]) if status["open"] else \
                    ", the market has closed."
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    msg += (" If %s is still open, close it: trader-broker flatten --symbol %s builds the SELL "
                            "ticket, then trader-risk and your yes. Zerodha squares off open MIS positions "
                            "around 15:20." % (sym, sym))
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        sym = r["symbol"].upper()
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                rings.append({"id": rid, "symbol": sym, "message": "%s %s %s: %s your %s %s%s."
                              % (sym, "is" if status["open"] else "last", inr(p), word, label, inr(r["price"]),
                                 (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to %s (from %s)."
                              % (sym, move, inr(p), inr(ref))})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def rules_from_plan(plan, cfg_flat_by=None):
    """trader/plan.json -> a stop alert, a target alert and a flat-by-close time alert, for the plan's day.
    A CNC (delivery) plan is held past the close, so it gets no flat-by alert."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run trader-exit first)")
    sym = str(plan["symbol"]).upper()
    out = []
    for key, typ, word in (("stop", "stop", "plan stop"), ("target", "target", "plan target")):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s (run trader-exit)" % key)
        label = plan.get("%s_label" % key)
        out.append({"id": "%s-plan-%s" % (sym.lower(), key), "symbol": sym, "type": typ, "price": float(v),
                    "note": word + (": " + label if label else "")})
    if str(plan.get("product") or "").upper() != "CNC":
        flat = plan.get("flat_by") or cfg_flat_by or "15:15"
        if _hhmm(flat) is None:
            raise AlertError("the plan's flat_by %r is not a time like 15:15" % flat)
        out.append({"id": "%s-flat-by" % sym.lower(), "symbol": sym, "type": "time", "at": flat,
                    "label": "flat by %s" % flat})
    if plan.get("date"):
        for r in out:
            r["date"] = str(plan["date"])
    return out


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        syms.append(str(p.get("ticker") or p.get("symbol") or "").upper())
    plan = _read(a.plan, {})
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(str(plan["symbol"]).upper())
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        word = "OPEN" if st["open"] else ("regular market closed" if st["muhurat"] else "closed")
        print("IST %s (%s): NSE %s, %s." % (st["now_ist"], st["weekday"], word, st["reason"]))
        if st["special_session"] and not st["muhurat"]:
            print("Special session today: %s." % st["special_session"])
        if st.get("opens"):
            print("Today's session: %s to %s IST." % (st["opens"], st["closes"]))
        if not st["open"] and st.get("next_open"):
            print("Next open: %s IST." % st["next_open"])
        if not st["calendar_known"]:
            print("holiday list covers %s only: check nseindia.com, or run python3 trader-data/data.py calendar"
                  % ", ".join(str(y) for y in CALENDAR_YEARS))
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([norm_symbol(s) for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? alerts.py quote RELIANCE TCS, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.offline, cache=a.cache)
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%+.2f%% vs prev close %s)" % (100.0 * (q["price"] / q["prev_close"] - 1), inr(q["prev_close"]))
                print("%-10s %s%s  %s  [%s]" % (s, inr(q["price"]), chg, q.get("time", ""), q["source"]))
                if q.get("note"):
                    print("  note: " + q["note"])
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), shown(a.write)))
        print("Quotes are free, delayed and unofficial: for alerts only. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add some with alerts.py add --from-plan" % shown(a.rules))
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    watched = [r for r in rules if active(r, status["date"])]
    syms = _dedupe([str(r.get("symbol", "")).upper() for r in watched if r.get("type") not in NO_QUOTE])
    quotes, errors = (get_quotes(syms, a.source, a.offline, cache=a.cache) if syms else ({}, []))
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: q["price"] for s, q in quotes.items()}}, indent=2))
    else:
        print("NSE %s (%s IST, %s)." % ("OPEN" if status["open"] else "closed", status["now_ist"],
                                              status["reason"]))
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched today%s." % (
                len(watched), (", " + ", ".join("%s %s" % (s, inr(quotes[s]["price"])) for s in syms if s in quotes))
                if quotes else ""))
        for s_ in syms:
            if s_ in quotes and quotes[s_].get("note"):
                print("note: %s: %s" % (s_, quotes[s_]["note"]))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. A bracket's stop-loss and take-profit already sit at "
                  "the broker: check get_open_position and record any fill (ticket.py fill). To close a "
                  "position yourself: ticket.py flatten, then trader-risk and your yes.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.from_plan:
        plan = _read(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s (run trader-exit first)" % shown(a.plan))
        cfg = _read(a.config, {}) if a.config else {}
        new = rules_from_plan(plan, (cfg or {}).get("flat_by"))
        ids = {r["id"] for r in new}
        doc["rules"] = [r for r in doc["rules"] if r.get("id") not in ids] + new
        state = _read(a.state, {})
        if state.get("rung"):
            for i in ids:
                state["rung"].pop(i, None)
            _write(a.state, state)
        _write(a.rules, doc)
        for r in new:
            print("Added %s: %s" % (r["id"], json.dumps(r)))
        print("Run alerts.py check every few minutes while the trade is open (a scheduled task or /loop). "
              "Free quotes are delayed; the broker's bracket orders are what actually stop you out.")
        return 0
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    elif a.at:
        if _hhmm(a.at) is None:
            raise AlertError("--at wants an IST time like 15:15")
        rule = {"id": a.id or ("time-%s" % a.at.replace(":", "")), "type": "time", "at": a.at,
                "label": a.label or "time alert"}
        if a.symbol:
            rule["symbol"] = norm_symbol(a.symbol)
        if a.before_close:
            rule["before_close"] = True
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --from-plan, --at, --opening-bell)")
        sym = norm_symbol(a.symbol)
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.target is not None:
            rule = {"type": "target", "price": a.target}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop, --target or --pct")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    if a.date:
        rule["date"] = a.date
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-24s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}),
                              ("  (rang %s)" % rung) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price alerts and the NSE clock (trader-alerts).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--source", default="yahoo", choices=["yahoo"])
        p.add_argument("--offline", metavar="DIR", help="read yahoo-<SYMBOL>.json files, no network")
        p.add_argument("--cache", action="store_true",
                       help="read the trader-data cache (trader/data/intraday, then daily) instead of Yahoo")
        p.add_argument("--now", help="pretend it is this time (no offset = IST)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is NSE open?")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="latest delayed quotes")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add symbols from your portfolio and plan")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="save to prices.json (default trader/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--target", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--opening-bell", action="store_true")
    g.add_argument("--at", metavar="HH:MM", help="a time alert, IST")
    g.add_argument("--from-plan", action="store_true", help="stop, target and flat-by from the plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json", help="flat_by when the plan has none")
    p.add_argument("--label", help="what a time alert says, e.g. \"flat by 15:15\"")
    p.add_argument("--before-close", action="store_true",
                   help="accepted for old rule files; NSE has no early closes")
    p.add_argument("--date", help="watch this rule on this IST date only (YYYY-MM-DD)")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
