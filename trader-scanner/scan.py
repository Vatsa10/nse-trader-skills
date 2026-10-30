#!/usr/bin/env python3
"""Chart Scanner for trader-scanner: scan hundreds of NSE charts for setups. Standard library only.

    python3 scan.py universe                          # Nifty 500 list -> trader/universe.txt
    python3 scan.py universe --source equity          # every NSE EQ-series stock (EQUITY_L.csv)
    python3 scan.py daily                             # before the open: daily setups -> trader/watchlist.json
    python3 scan.py daily --symbols RELIANCE,HDFCBANK,TCS   # or --watchlist FILE, or --limit 200
    python3 scan.py orb                               # after 9:35 AM IST: opening-range breakouts so far
    python3 scan.py orb --at 10:00 --or-minutes 15    # cut the clock at 10:00 AM IST
    python3 scan.py orb --offline wall5m.json.gz --now "2026-10-19 10:00" --min-price 0
    python3 scan.py orb --trend-filter                # drop names whose daily swing trend is down
    python3 scan.py daily --cache                     # daily closes from trader/data/daily (trader-data)
    python3 scan.py longterm                          # long-term screen of the daily cache
    python3 scan.py longterm --screen near_52w_low --near-low 10
    python3 scan.py patterns                          # VCP, double bottom, flag, triangle, inverse H&S, support
    python3 scan.py patterns --pattern vcp --state coiled
    python3 scan.py patterns --intraday               # during the session: double bottom and flag on 5-minute bars
    python3 scan.py patterns --intraday --offline day.json --at 10:45
    python3 scan.py breadth                           # market breadth -> trader/data/breadth.csv

universe  Downloads an NSE symbol list and writes one SYMBOL|Name a line.
          Default (--source nifty500): niftyindices.com's Nifty 500 constituents
          (ind_nifty500list.csv, column Symbol). --source equity: NSE's
          EQUITY_L.csv (every listed equity), keeping SERIES == EQ only; it
          falls back to the Nifty 500 list when NSE refuses the download.
          When niftyindices.com refuses too (or with --cache) it reads
          trader/data/universe.csv (trader-data; a Symbol, SYMBOL or symbol
          column), then the bundled offline snapshot nifty500-snapshot.csv
          next to this script (its date and source are in
          nifty500-snapshot.json), and says which one it used.
          A symbol must look like an NSE symbol: A-Z, 0-9, & and -, up to 20
          characters (RELIANCE, M&M, BAJAJ-AUTO).

daily     Pre-market scan of daily CLOSES (Yahoo spark: 20 symbols a request,
          6 months of 1-day bars, closes only). Liquidity from the spark meta:
          regularMarketPrice >= --min-price (50) and regularMarketVolume >=
          --min-volume (500,000). Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
            swing_pullback   the swing engine (see longterm) says up and stable, a swing
                             high is pending, the close is over the change-of-character
                             level and the swing high formed 3 to 15 bars ago
            swing_reversal_up  the swing trend turned up on the last bar
            delivery_spike delivery quantity and quantity per trade both over 1.2 x their
                           30-session average on the latest day (daily --cache only)
          The swing engine runs on closes alone with Yahoo spark (high = low =
          close, a closing-basis structure) and on real highs and lows with
          --cache. Each hit carries level (the pending swing high, what
          trader-entry's watchlist --setup level reads), coc (the structural
          stop reference), swing_trend and swing_basis.
          Today's daily bar is dropped until the session's close (15:30 IST).

longterm  Long-term screen of the trader-data daily cache
          (trader/data/daily/<SYMBOL>.csv, the .adj.csv when present, at
          least 200 sessions). Per stock: the 50- and 200-day averages, the
          52-week (252-session) high and low and the distance from each in
          percent, RSI 14. Screens:
            above_200dma   the last close is above the 200-day average
            near_52w_high  within --near-high (5) percent of the 52-week high
            near_52w_low   within --near-low (10) percent above the 52-week
                           low (a value screen: cheap, not necessarily good)
            oversold       RSI 14 under 30
            swing_up       market structure is up (a SwingTrend-style engine on the
                           last 252 sessions, 5 % retrace filter, stable after 40
                           bars): the change-of-character level (swing_coc) has held
            swing_reversal_up  the engine's trend turned up on the last bar
            rs_positive    Mansfield relative strength over the Nifty is above zero and
                           above its value 5 sessions ago (needs a benchmark: below)
          mansfield_rs = (RS / its 22-day average - 1) x 100 with RS = stock close /
          index close x 100 on the common dates. Benchmark: trader/data/index/
          nifty-50.csv, else trader/data/daily/NIFTYBEES.adj.csv, else NIFTYBEES.csv;
          none = mansfield_rs null and a note.
            delivery_spike the daily scan's delivery reading, from
                           trader/data/delivery/<SYMBOL>.csv (trader-data; absent = no
                           tag, dq null)
          Each hit carries dq (the latest delivery quantity over its 30-session
          mean), swing_trend, swing_coc (a closing-basis structural stop),
          swing_event and swing_bars_since.
          --screen NAME keeps only that screen (default: any). Liquidity:
          --min-price (20) and 20-day average volume >= --min-volume (1,00,000).
          Writes trader/longterm.json. No network.

patterns  Chart patterns in the trader-data daily cache (real highs and lows, the
          last 260 sessions, at least 50). Pivots are bars higher (lower) than
          the 5 before and not lower than the 5 after, so a pattern is seen
          5 sessions late and no future bar is used. Patterns: vcp (shrinking
          pullbacks under one base high, in a Minervini-style uptrend, 200+
          sessions), double_bottom, flag (pole and flag), ascending_triangle,
          inverse_head_shoulders, rising_support (a retest of a line through 3
          pivot lows). State: coiled (formed, no close over its trigger yet)
          or breakout (the first close over it, at most 5 % over). Each hit has
          level (trigger + one tick, what trader-entry reads), stop, risk_pct,
          target (a measured move, null for vcp and rising_support), anchor_date,
          pivots, vol_x, vol_dry and first_seen. Writes trader/watchlist.json
          (mode patterns). No network.
          patterns --intraday: the double bottom (pivots 3 + 3, lows 8 to 60 bars
          apart, prior decline 1.5 %, rebound 0.8 %) and the pole and flag (top in
          the last 12 bars, 4+ old, the session high, gain 2 %, base to top within
          24 bars, half the pole held) on the current session's 5-minute bars,
          through orb's fetch path (--cache, --offline with --at / --now, --at,
          --until). Hits only when the cut-off is not after no_entries_after
          (default 15:00). Writes trader/watchlist.json, mode patterns-intraday,
          hits with level, stop, target, pattern, state, setups.

breadth   Market breadth from the adjusted daily cache (<SYMBOL>.adj.csv), the
          last 500 dates: percent of stocks above their 50- and 200-day
          averages, net new highs (over the 252 sessions before), the A/D line
          (a ratio line) and the McClellan oscillator (19 and 39 EMAs of the net
          advance ratio, each seeded with the plain average of its first n
          values). Writes trader/data/breadth.csv; when that file exists the
          other scans print a one-line regime note. Current constituents only,
          so the history is survivorship-biased (said every time).

orb       Opening-range breakouts, 5-minute bars. The session is 09:15 to
          15:30 IST (75 five-minute bars). The range is the first
          --or-minutes (15) of the session, from 09:15. Stage 1: spark closes for
          every symbol (1 day of 5-minute bars, 20 a request); a chart goes on
          only if a completed bar after the range closed at or above the highest
          close of the range (a necessary condition). Stage 2: each candidate's
          v8 chart (real highs and lows): range high = the highest high of the
          range bars, a hit = the first completed bar to CLOSE above it, at that
          bar's close time. Stage 2 fetches at most --max-confirm (200) charts,
          the most traded candidates first (price x volume so far), and says
          so when it stops short. A chart is usable only when every 5-minute
          bar from 09:15 to the cutoff is there (a missing bar is a halt, a
          circuit lock or no trades). Floors: --min-price 50 (last close),
          --min-volume 0. --trend-filter drops hits whose daily swing trend
          (the engine on the trader-data daily cache, bars before the session
          day) is down; a name with no cache or no stable trend stays, flagged
          "trend unknown" in daily_trend and trend_note.

Bars: null closes are dropped, a bar off the 5-minute grid (the live tick,
like 10:49:45) is dropped, and a bar counts only once it is complete
(start + 5 minutes <= the clock). Times are India time (IST, UTC+05:30, no
daylight saving). --now pins the clock ("2026-10-19 10:00", IST; ...Z for UTC).
Market days: Monday to Friday minus the NSE holiday list. trader/data/calendar.json
(trader-data's shared calendar: "years", "holidays", "special_sessions") wins
when it exists and parses: a special session is open, a listed holiday is
closed, a year it covers is open. Otherwise the built-in list (2026 only;
other years get a warning). Without a special-session entry the Muhurat
session is treated as closed.

Symbols are NSE symbols (RELIANCE, HDFCBANK); Yahoo is asked for SYMBOL.NS.
A trailing .NS typed by the user is dropped. Option-looking symbols are refused.

Yahoo's endpoints are free, unofficial and can lag. query1 first, query2 on
a 429, a 5xx or a network error, one retry after a pause on a 429, a browser
user agent. A polite pause between requests (--pause). Partial results are
reported as partial.

Cache (trader-data writes it): daily --cache reads trader/data/daily/<SYMBOL>.csv
and orb --cache reads trader/data/intraday/<SYMBOL>-5m.json instead of Yahoo.
Without --cache, a symbol Yahoo fails on is read from the same cache. Either
way the scan prints "using cached data from <time>, run trader-data ... to
refresh". With no cache either: run python3 trader-data/data.py doctor; or call
Kite MCP get_historical_data and data.py import-kite.

--offline PATH reads files instead of the network: universe wants a folder
with ind_nifty500list.csv (and EQUITY_L.csv for --source equity); daily and
orb take a folder of <SYMBOL>.json v8 chart files or spark responses, or one
file that is a spark response, a v8 chart, or a bundle {"RELIANCE": <v8 json>, ...}.
.json.gz is fine.

Market context (trader-data files; missing or corrupt = no line, no crash): daily, orb and patterns print one
line from trader/data/flows.csv ("FII/FPI net -943.60 cr, DII net +1,924.41 cr on 2026-10-29 (5-session ...)")
and put a `flows` object in the watchlist. orb and patterns --intraday read trader/data/preopen.json: when its
date is the session day they print the pre-open advances and declines and add preopen_gap_pct (change_pct) and
preopen_iep to each matching hit.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
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
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")  # NSE and niftyindices refuse non-browser agents
YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_FILE = os.path.join(HERE, "nifty500-snapshot.csv")
SNAPSHOT_META = os.path.join(HERE, "nifty500-snapshot.json")
DATA_DIR = os.path.join("trader", "data")
DATA_HINT = ("run `python3 trader-data/data.py doctor`; or call Kite MCP get_historical_data and "
             "`python3 trader-data/data.py import-kite`")
LT_BARS = 200       # the 200-day average needs 200 sessions
YEAR_BARS = 252     # about a year of NSE sessions
SWING_RETRACE = 5.0   # daily swing engine: pullbacks under this percent of the swing high do not move the stop (ours)
SWING_MIN_BARS = 40   # the engine's trend counts as stable only after this many bars (SwingTrend docs: feed 60+)
SWING_FEED = YEAR_BARS   # bars fed to the engine for the long-term tags: a fixed warm-up window
SWING_PULLBACK_MIN, SWING_PULLBACK_MAX = 3, 15   # swing_pullback: bars since the swing high formed (ours)
MANSFIELD_DAILY = 22     # Mansfield relative strength: the average of the ratio over this many daily bars (eod2 notes)
MANSFIELD_RISE_BARS = 5  # rs_positive wants the Mansfield value above what it was this many sessions ago (ours)
BREADTH_FAST, BREADTH_SLOW = 19, 39   # McClellan EMA lengths
BREADTH_SMA = (50, 200)  # the moving averages "percent above" counts
BREADTH_HIGH_BARS = YEAR_BARS   # a new high beats the highest high of the 252 sessions before it
BREADTH_DAYS = 500       # dates written to breadth.csv (about two years); 0 = every date in the cache (ours)
BREADTH_STRONG, BREADTH_WEAK = 60.0, 40.0   # percent above the 200-day: broad strength / weak (ours)
BREADTH_COLUMNS = ["date", "pct_50", "pct_200", "net_new_highs", "ad_line", "mcclellan_osc", "net_adv_ratio",
                   "fast_ema", "slow_ema"]
BREADTH_CAVEAT = "current constituents only, so the history is survivorship-biased"
DELIVERY_BARS = 30       # sessions in the delivery average, the latest one included
DELIVERY_MIN = 5         # fewer delivery values than this in the window: no reading, no tag
DELIVERY_RATIO = 1.2     # delivery_spike: delivery quantity and quantity per trade both over 1.2 x their average
DELIVERY_T2T = ("BE", "BZ")   # trade-to-trade series: delivery is the whole volume, so it says nothing
NIFTY500_FILE = "ind_nifty500list.csv"
NIFTY500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
EQUITY_FILE = "EQUITY_L.csv"
EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
INDEX_YAHOO = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK"}
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MIN_DAILY_BARS = 30
DEFAULT_UNIVERSE = "trader/universe.txt"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."

NSE_HOLIDAYS = {  # NSE equity segment, weekday holidays
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
NSE_EARLY_CLOSE = {}  # NSE has no early closes (the Muhurat session is treated as closed)
CALENDAR_YEARS = (2026,)
CALENDAR_FILE = os.path.join(DATA_DIR, "calendar.json")   # trader-data's shared trading calendar, when it exists
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)  # fixed, India has no DST
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
OPEN_MIN, CLOSE_MIN = 555, 930
SESSION_BARS = (CLOSE_MIN - OPEN_MIN) // 5   # 75 five-minute bars, 09:15 .. 15:25
DEFAULT_UNTIL = "15:00"   # no new entries after (MIS square-off is about 15:20; flat by 15:15)

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
    ("delivery_spike", "delivery spike (institutional-size trades, high delivery)"),
    ("swing_pullback", "swing pullback (uptrend, pending swing high)"),
    ("swing_reversal_up", "swing trend just reversed up"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time

def ist_offset(utc=None):
    """Hours from UTC for India: always +5:30 (no daylight saving)."""
    return 5.5


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def ts_to_ist(ts):
    """Unix seconds -> naive India time (IST). Never uses the machine's time zone."""
    return to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-10-19 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an IST time like 10:00" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _calendar_dates(doc):
    """{"YYYY-MM-DD": name} from a calendar.json section; bad keys are skipped."""
    out = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            try:
                out[datetime.date.fromisoformat(str(k)).isoformat()] = str(v or "")
            except ValueError:
                continue
    return out


def load_calendar(path=None):
    """trader/data/calendar.json -> {"years": set, "holidays": {...}, "special": {...}}, or None when the
    file is missing or not the shape this tool knows (the built-in table then answers)."""
    try:
        with open(path or CALENDAR_FILE, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    years = set()
    ys = doc.get("years")
    for y in (list(ys) if isinstance(ys, (list, tuple, dict)) else []):
        try:
            years.add(int(y))
        except (TypeError, ValueError):
            continue
    return {"years": years, "holidays": _calendar_dates(doc.get("holidays")),
            "special": _calendar_dates(doc.get("special_sessions"))}


def trading_day(d):
    """(is_trading_day, reason). A special session is open; then weekends, the shared calendar.json
    (its holidays, then any year it covers is open), then the built-in NSE holiday list."""
    cal = load_calendar()
    iso = d.isoformat()
    if cal and iso in cal["special"]:
        return True, ""
    if d.weekday() >= 5:
        return False, "weekend"
    if cal:
        if iso in cal["holidays"]:
            return False, "NSE holiday: " + (cal["holidays"][iso] or "closed")
        if d.year in cal["years"]:
            return True, ""
    name = NSE_HOLIDAYS.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in IST, or None if closed all day. No early closes on NSE."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def calendar_note(d):
    """A warning when neither calendar.json nor the built-in holiday list covers the date's year."""
    if d.year in CALENDAR_YEARS:
        return ""
    cal = load_calendar()
    if cal and d.year in cal["years"]:
        return ""
    return "holiday list covers 2026 only: check nseindia.com"


def clock_label(t):
    """datetime or time -> '10:15 AM'."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query1 first, query2 on a 429, a 5xx or a network error, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
OPTION_LIKE = re.compile(r"\d{2}[A-Z]{3}\d*(CE|PE|FUT)$|\d+(CE|PE)$")


def clean_symbol(sym):
    """User text -> NSE symbol: upper case, a trailing .NS dropped."""
    s = str(sym or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def check_symbol(sym):
    s = clean_symbol(sym)
    if s in INDEX_YAHOO:
        return s
    if OPTION_LIKE.search(s) and any(ch.isdigit() for ch in s):
        raise ScanError("%s looks like an F&O contract: this tool scans NSE cash equities only" % s)
    if not NSE_SYMBOL.match(s):
        raise ScanError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    return s


def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS, M&M -> M%26M.NS (URL-quoted)."""
    s = clean_symbol(sym)
    if s in INDEX_YAHOO:
        return urllib.parse.quote(INDEX_YAHOO[s], safe="")
    return urllib.parse.quote(s, safe="-") + ".NS"


def from_yahoo(ysym):
    """Yahoo symbol in an answer (RELIANCE.NS, M&M.NS) -> NSE symbol."""
    s = urllib.parse.unquote(str(ysym or "")).upper()
    for k, v in INDEX_YAHOO.items():
        if s == v:
            return k
    return s[:-3] if s.endswith(".NS") else s


to_yahoo = yahoo_symbol


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(to_yahoo(s) for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (to_yahoo(sym), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are simply missing
    from the answer. `wanted` maps Yahoo symbols back to ours (RELIANCE.NS -> RELIANCE)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        sym = (wanted or {}).get(ysym.upper(), from_yahoo(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = from_yahoo(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

def _csv_rows(text, what):
    text = text.lstrip("\ufeff")
    first = text.split("\n", 1)[0]
    if "<html" in text[:500].lower() or "," not in first:
        raise ScanError("not %s (the site sent something else, maybe a block page)" % what)
    rows = []
    for r in csv.DictReader(text.splitlines()):
        rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
    return rows


def classify(row):
    """One list row -> 'keep' or the rule that drops it."""
    sym = clean_symbol(row.get("Symbol") or row.get("SYMBOL"))
    series = (row.get("SERIES") or row.get("Series") or "EQ").strip().upper()
    if series != "EQ":
        return "not EQ series"
    if not NSE_SYMBOL.match(sym):
        return "not an NSE symbol"
    return "keep"


def parse_nifty500(text):
    rows = _csv_rows(text, "the Nifty 500 list")
    if not rows or "Symbol" not in rows[0]:
        raise ScanError("not the Nifty 500 list (no Symbol column)")
    return rows


def parse_equity_l(text):
    rows = _csv_rows(text, "NSE's EQUITY_L.csv")
    if not rows or "SYMBOL" not in rows[0]:
        raise ScanError("not NSE's EQUITY_L.csv (no SYMBOL column)")
    return rows


def build_universe(texts, source="nifty500"):
    """{filename: text} -> (kept [(SYM, name)], counts, source_used)."""
    if source == "equity" and EQUITY_FILE in texts:
        rows, used = parse_equity_l(texts[EQUITY_FILE]), "equity"
        name_key, sym_key = "NAME OF COMPANY", "SYMBOL"
    elif NIFTY500_FILE in texts:
        rows, used = parse_nifty500(texts[NIFTY500_FILE]), "nifty500"
        name_key, sym_key = "Company Name", "Symbol"
    else:
        raise ScanError("missing %s" % (EQUITY_FILE if source == "equity" else NIFTY500_FILE))
    kept, counts = {}, {"rows": len(rows)}
    for r in rows:
        why = classify(r)
        counts[why] = counts.get(why, 0) + 1
        if why == "keep":
            kept[clean_symbol(r[sym_key])] = " ".join((r.get(name_key) or "").split())
    return sorted(kept.items()), counts, used


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


def swing_info(bars, retrace=SWING_RETRACE, min_bars=SWING_MIN_BARS):
    """bars (high, low, close) oldest first -> the engine's snapshot plus `event`, the last bar's event."""
    snap, events = swing_run(bars, retrace, min_bars=min_bars)
    snap["event"] = events[-1] if events else None
    return snap


def swing_tag_list(trend, event, stable):
    """The long-term swing tags. A trend reading counts only once the engine is stable; a trend that
    just turned up is swing_reversal_up, not swing_up."""
    if not stable or trend != "up":
        return []
    return ["swing_reversal_up"] if event == "reversal" else ["swing_up"]


def swing_tags(bars, retrace=SWING_RETRACE, min_bars=SWING_MIN_BARS):
    sw = swing_info(bars, retrace, min_bars)
    return swing_tag_list(sw["trend"], sw["event"], sw["stable"])


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes (the same math as the reel's refs/scan.py)."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_hlc(series, now_utc):
    """[(date, high, low, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the session closes, anything dated after the clock dropped. A bar with no high or low
    (spark has closes only) takes its close for both."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    hs, ls = series.get("h"), series.get("l")
    out = {}
    for i, (ts, c) in enumerate(zip(series["t"], series["c"])):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        h = hs[i] if hs and i < len(hs) else None
        lo = ls[i] if ls and i < len(ls) else None
        c = float(c)
        out[d] = (float(h) if h is not None else c, float(lo) if lo is not None else c, c)  # a late duplicate wins
    return [(d,) + out[d] for d in sorted(out)]


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars (see daily_hlc)."""
    return [(b[0], b[3]) for b in daily_hlc(series, now_utc)]


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def find_swing(bars, retrace=SWING_RETRACE, min_bars=SWING_MIN_BARS):
    """bars (high, low, close) oldest first -> (setup tags, numbers). The engine runs on the last SWING_FEED
    bars and counts only once it is stable. swing_pullback: trend up, a swing high pending, the change-of-
    character level under the close, and SWING_PULLBACK_MIN to SWING_PULLBACK_MAX bars since the swing high
    formed. swing_reversal_up: the trend turned up on the last bar. `level` is the pending swing high (the
    high of the leg when none is pending), `coc` the structural stop; both None unless the trend is up."""
    sw = swing_info(bars[-SWING_FEED:], retrace, min_bars)
    up = sw["trend"] == "up" and sw["stable"]
    tags = []
    if up:
        if sw["event"] == "reversal":
            tags.append("swing_reversal_up")
        elif (sw["pivot"] is not None and sw["coc"] < bars[-1][2]
              and SWING_PULLBACK_MIN <= sw["bars_since"] <= SWING_PULLBACK_MAX):
            tags.append("swing_pullback")
    return tags, {"trend": sw["trend"] if sw["stable"] else None, "bars_since": sw["bars_since"],
                  "level": (sw["pivot"] if sw["pivot"] is not None else sw["hi"]) if up else None,
                  "coc": sw["coc"] if up else None}


def scan_daily(data, symbols, now_utc, min_price, min_volume, delivery=None):
    """Pure. data = {SYM: series}; delivery = {SYM: delivery rows} for the dq number and the delivery_spike tag.
    Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        hlc = daily_hlc(s, now_utc)
        bars = [(b[0], b[3]) for b in hlc]
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        if price is None or price < min_price or (min_volume > 0 and (vol is None or vol < min_volume)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        stags, sw = find_swing([b[1:] for b in hlc])   # highs and lows when the series has them, else closes
        tags = tags + stags
        dr = delivery_reading((delivery or {}).get(sym), d_last)
        if dr and dr["spike"]:
            tags.append("delivery_spike")
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None,
                         "level": round(sw["level"], 2) if sw["level"] is not None else None,
                         "coc": round(sw["coc"], 2) if sw["coc"] is not None else None,
                         "swing_trend": sw["trend"],
                         "swing_basis": "highs_lows" if s.get("h") and s.get("l") else "closes",
                         "dq": round(dr["dq"], 2) if dr and dr["dq"] is not None else None,
                         "dq_date": dr["date"] if dr and dr["dq"] is not None else None})
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))  # most rupees traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive IST),
    on the grid, with a close. Sorted, one per slot."""
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        et = ts_to_ist(ts)
        if et.date() != day or et.second or et.minute % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if et.time() < OPEN or et.time() >= close_t:
            continue  # pre-open, after the 15:30 close, the closing-session print
        if et + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        v = series.get("v")
        out[et] = {"start": et, "c": float(c),
                   "h": None if not hs or hs[i] is None else float(hs[i]),
                   "l": None if not ls or ls[i] is None else float(ls[i]),
                   "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    """Every slot from 09:15 to the cutoff is there, and at least the range."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    s = session(day)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds()
                             // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout (range high >= any range close)."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive IST) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --offline." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("The market closed at %s IST on %s. Replay the session with --at 10:00."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s IST: the %d-minute opening range runs 9:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:00." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). With confirm and max_confirm, only the max_confirm most
    traded candidates (price x volume so far) are confirmed; the rest are counted as unconfirmed.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


def trend_filter(hits, rows_by_sym, retrace=SWING_RETRACE, min_bars=SWING_MIN_BARS):
    """Keep opening-range hits whose daily swing trend is up. A name with no daily rows, too few bars for a
    stable reading, or no trend yet stays, flagged `trend unknown` in the hit. -> (kept, dropped symbols)."""
    kept, dropped = [], []
    for h in hits:
        rows = rows_by_sym.get(h["symbol"]) or []
        sw = swing_info([(r[2], r[3], r[4]) for r in rows[-SWING_FEED:]], retrace, min_bars) if rows else None
        if sw and sw["stable"] and sw["trend"] == "down":
            dropped.append(h["symbol"])
            continue
        if sw and sw["stable"] and sw["trend"] == "up":
            kept.append(dict(h, daily_trend="up"))
            continue
        why = ("no daily cache" if not rows else "under %d daily bars" % (min_bars + 1) if not sw["stable"]
               else "no daily trend yet")
        kept.append(dict(h, daily_trend="unknown", trend_note="trend unknown (%s)" % why))
    return kept, dropped


# ---------------------------------------------------------------- chart patterns

# Pivot-based chart patterns on the daily cache (rows with real highs and lows). A detector is a pure function of
# the rows up to the last bar. Thresholds marked (repo) follow the published stock-pattern rules, (ours) are ours.
TICK = 0.05                 # NSE price tick: level = trigger + one tick, stop = swing low - one tick
PAT_BARS = 260              # a detector reads the last 260 bars only
BREAKOUT_MAX_PCT = 5.0      # state "breakout": the last close is the first over the trigger and at most this far over (ours)
PATTERNS = [
    ("vcp", "volatility contraction (VCP)"),
    ("double_bottom", "double bottom"),
    ("flag", "pole and flag"),
    ("ascending_triangle", "ascending triangle (flat-top coil)"),
    ("inverse_head_shoulders", "inverse head and shoulders"),
    ("rising_support", "rising-support retest"),
]
PAT_LABEL = dict(PATTERNS)
PAT = {
    "min_bars": 50,              # fewer rows than this: no pattern scan (the flag needs 50, the VCP 200)
    "L": 5, "R": 5,              # pivot window: bars strictly lower on the left, not higher on the right (ours)
    # VCP
    "vcp_min_bars": 200, "vcp_trend_gate": True,    # close > 50-day > 200-day, 200-day rising, near the high (ours)
    "vcp_near_high": 0.25,       # the close is within 25 % of the 252-bar high (ours)
    "vcp_base_window": 120, "vcp_base_age": 15,     # the base high: highest pivot high of the last 120 bars, 15+ old (ours)
    "vcp_contractions": (2, 5),  # completed contractions (ours)
    "vcp_first_depth": (0.08, 0.35), "vcp_shrink": 0.85, "vcp_last_depth": 0.10,   # (ours)
    "vcp_high_floor": 0.95,      # every later high at least 95 % of the base high (ours)
    "vcp_zone": 0.95,            # coiled: close within 5 % under the trigger (ours)
    "vcp_max_risk_pct": 12.0,    # drop a hit whose (level - stop) / level is over this (ours)
    # double bottom
    "db_gap": (10, 80),          # bars between the two lows (ours)
    "db_tol_u": 0.5,             # the lows within 0.5 U (repo)
    "db_rebound_u": 3.0, "db_rebound_pct": 5.0,     # rebound at least max(3 U, 5 % of the low) (ours)
    "db_prior_bars": 60, "db_prior_drop": 1.15,     # the 60 bars before low A reached 1.15 x low A (ours)
    "db_break_u": 0.5,           # no close under the lows - 0.5 U (ours)
    # pole and flag
    "flag_min_bars": 50,         # (repo)
    "flag_recent": 7, "flag_age": (4, 6), "flag_lookback": 90,     # pole top: highest high of the last 7, 4 to 6 bars old (repo)
    "flag_gain": 0.15, "flag_base_bars": 20,        # pole gain 15 %, base to top within 20 bars (ours)
    "flag_retrace": 0.5,         # the flag low holds the midpoint of the pole (repo)
    # ascending triangle
    "tri_span": (20, 90), "tri_tol_u": 1.0, "tri_rise_u": 0.5,     # span in bars, highs within 1 U (repo), lows up 0.5 U (ours)
    # inverse head and shoulders
    "ihs_head_u": 0.6, "ihs_neck_u": 1.0,           # head 0.6 U under the shoulders, necklines within 1 U (repo)
    "ihs_prior_bars": 60, "ihs_sym": 0.5,           # head the lowest of 60 bars; shoulders within half the head depth (ours)
    # rising support
    "rs_window": 120, "rs_gap": 5, "rs_touch_u": 0.5, "rs_touches": 3,    # (ours)
    "rs_max_above": 0.03,        # the last close at most 3 % over the line (ours; the repo allows 10 %)
    "rs_stop_u": 1.0,
    "stop_u": 0.5,               # stop = swing low - 0.5 U for the triangle and the head and shoulders (ours)
}


def median(xs):
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def find_pivots(h, l, left, right):
    """-> (pivot highs, pivot lows) as [(index, price)]. Bar i is a pivot high when its high is strictly above each of
    the `left` highs before it and at least each of the `right` highs after it (the earlier of equal highs wins);
    a pivot low mirrors that. A bar that is both is ignored. A pivot needs `right` bars after it: no look-ahead."""
    ph, pl = [], []
    for i in range(left, len(h) - right):
        up = h[i] > max(h[i - left:i]) and h[i] >= max(h[i + 1:i + right + 1])
        dn = l[i] < min(l[i - left:i]) and l[i] <= min(l[i + 1:i + right + 1])
        if up and not dn:
            ph.append((i, h[i]))
        elif dn and not up:
            pl.append((i, l[i]))
    return ph, pl


def zigzag(ph, pl):
    """Pivot highs and lows merged by index into [(index, "H" or "L", price)]; neighbours of one type collapse to the
    more extreme (the earlier on a tie), so the points alternate."""
    out = []
    for pt in sorted([(i, "H", v) for i, v in ph] + [(i, "L", v) for i, v in pl]):
        if out and out[-1][1] == pt[1]:
            if pt[2] > out[-1][2] if pt[1] == "H" else pt[2] < out[-1][2]:
                out[-1] = pt
        else:
            out.append(pt)
    return out


def pat_state(c, start, trigger, inclusive=False):
    """"coiled" (the last close is under the trigger), "breakout" (the first close over it, at most BREAKOUT_MAX_PCT
    over) or None. Every close after bar `start` and before the last must be under the trigger."""
    def beyond(v):
        return v >= trigger if inclusive else v > trigger
    if any(beyond(v) for v in c[start + 1:-1]):
        return None
    last = c[-1]
    if last < trigger:
        return "coiled"
    if beyond(last) and last <= trigger * (1 + BREAKOUT_MAX_PCT / 100.0):
        return "breakout"
    return None


def pat_context(rows, params=None):
    """The last PAT_BARS rows as plain lists, with the pivots and the zigzag worked out once."""
    p = dict(PAT, **(params or {}))
    rows = rows[-PAT_BARS:]
    h, l = [r[2] for r in rows], [r[3] for r in rows]
    ph, pl = find_pivots(h, l, p["L"], p["R"])
    return {"p": p, "n": len(rows), "dates": [r[0] for r in rows], "h": h, "l": l, "c": [r[4] for r in rows],
            "v": [r[5] for r in rows], "ph": ph, "pl": pl, "zz": zigzag(ph, pl), "rng": [a - b for a, b in zip(h, l)]}


def pat_unit(x, start):
    """U: the median bar range from bar `start` to the last bar."""
    return median(x["rng"][start:]) or TICK


def vol_dry(v, a=None, b=None):
    """Advisory. With bars a and b: volume at bar b under volume at bar a. Without: the mean volume of the last 10
    bars under the mean of the 50 before them. None when there is not enough volume to say."""
    if a is not None:
        return v[b] < v[a] if v[a] and v[b] else None
    if len(v) < 60:
        return None
    base = sum(v[-60:-10]) / 50.0
    return (sum(v[-10:]) / 10.0 < base) if base > 0 else None


def detect_vcp(x):
    """Volatility contraction: shrinking pullbacks under one base high, each low above the last."""
    p, n, c, h = x["p"], x["n"], x["c"], x["h"]
    if n < p["vcp_min_bars"]:
        return None
    if p["vcp_trend_gate"]:
        if n < 220:
            return None
        sma50, sma200 = sum(c[-50:]) / 50.0, sum(c[-200:]) / 200.0
        if not (c[-1] > sma50 > sma200 and sma200 > sum(c[-220:-20]) / 200.0):
            return None
        if c[-1] < (1 - p["vcp_near_high"]) * max(h[-YEAR_BARS:]):
            return None
    cands = [t for t in x["ph"] if t[0] >= n - p["vcp_base_window"] and n - 1 - t[0] >= p["vcp_base_age"]]
    if not cands:
        return None
    i0, h0 = max(cands, key=lambda t: (t[1], -t[0]))
    zz = zigzag([t for t in x["ph"] if t[0] >= i0], [t for t in x["pl"] if t[0] >= i0])
    if not zz or zz[0][0] != i0:
        return None
    lows = zz[1::2]                                   # l(1) .. l(n); the highs are zz[0], zz[2], ..
    k = len(lows)
    if not (p["vcp_contractions"][0] <= k <= p["vcp_contractions"][1]):
        return None
    depth = [(zz[2 * j][2] - lows[j][2]) / zz[2 * j][2] for j in range(k)]
    if not (p["vcp_first_depth"][0] <= depth[0] <= p["vcp_first_depth"][1]) or depth[-1] > p["vcp_last_depth"]:
        return None
    for j in range(1, k):
        if depth[j] > p["vcp_shrink"] * depth[j - 1] or lows[j][2] <= lows[j - 1][2]:
            return None
    if any(t[2] < p["vcp_high_floor"] * h0 for t in zz[2:2 * k:2]):
        return None
    trigger, low_n = zz[2 * (k - 1)][2], lows[-1]
    state = pat_state(c, low_n[0], trigger, inclusive=True)
    if state is None or c[-1] <= low_n[2] or (state == "coiled" and c[-1] < p["vcp_zone"] * trigger):
        return None
    level, stop = trigger + TICK, low_n[2] - TICK
    if (level - stop) / level * 100 > p["vcp_max_risk_pct"]:
        return None
    return {"pattern": "vcp", "state": state, "level": level, "stop": stop, "target": None, "anchor": i0,
            "pivots": [(t[0], t[2]) for t in zz[:2 * k]], "vol_dry": vol_dry(x["v"])}


def detect_double_bottom(x):
    """Two pivot lows at about the same price with a rebound between them; coiled under the middle high."""
    p, n, c, h, l = x["p"], x["n"], x["c"], x["h"], x["l"]
    lows = x["pl"][-8:]
    for ci in range(len(lows) - 1, 0, -1):
        for ai in range(ci - 1, -1, -1):
            a, b_ = lows[ai], lows[ci]
            gap = b_[0] - a[0]
            if gap > p["db_gap"][1]:
                break
            if gap < p["db_gap"][0]:
                continue
            u = pat_unit(x, a[0])
            if abs(a[1] - b_[1]) > p["db_tol_u"] * u:
                continue
            mids = [t for t in x["ph"] if a[0] < t[0] < b_[0]]
            if not mids:
                continue
            top = max(mids, key=lambda t: (t[1], -t[0]))[1]
            low = min(a[1], b_[1])
            if top - max(a[1], b_[1]) < max(p["db_rebound_u"] * u, p["db_rebound_pct"] / 100.0 * b_[1]):
                continue
            before = h[max(0, a[0] - p["db_prior_bars"]):a[0]]
            if not before or max(before) < p["db_prior_drop"] * a[1]:
                continue
            if min(c[a[0] + 1:]) < low - p["db_break_u"] * u:
                continue
            state = pat_state(c, b_[0], top)
            if state is None or (state == "coiled" and c[-1] < (b_[1] + top) / 2.0):
                continue
            stop = low - p["db_break_u"] * u
            return {"pattern": "double_bottom", "state": state, "level": top + TICK, "stop": stop,
                    "target": top + (top - low), "anchor": a[0], "pivots": [(a[0], a[1]), (b_[0], b_[1])],
                    "vol_dry": vol_dry(x["v"], a[0], b_[0])}
    return None


def detect_flag(x):
    """Pole and flag: a sharp run up to a top 4 to 6 bars ago, then a shallow pullback that holds half the pole."""
    p, n, c, h, l = x["p"], x["n"], x["c"], x["h"], x["l"]
    if n < p["flag_min_bars"] or n < p["flag_recent"]:
        return None
    recent = h[-p["flag_recent"]:]
    top = max(recent)
    ti = n - p["flag_recent"] + recent.index(top)
    if not (p["flag_age"][0] <= n - 1 - ti <= p["flag_age"][1]) or top < max(h[-p["flag_lookback"]:]):
        return None
    bases = [t for t in x["pl"] if t[0] < ti]
    if not bases:
        return None
    bi, base = bases[-1]
    height = top - base
    if base <= 0 or height / base < p["flag_gain"] or ti - bi > p["flag_base_bars"]:
        return None
    flag_low = min(l[ti:])
    if flag_low < base + p["flag_retrace"] * height or c[-1] >= top:
        return None
    return {"pattern": "flag", "state": "coiled", "level": top + TICK, "stop": flag_low - TICK, "target": top + height,
            "anchor": bi, "pivots": [(bi, base), (ti, top)], "vol_dry": None}


def detect_ascending_triangle(x):
    """Three equal highs over two rising lows (high, low, high, low, high); coiled under the flat top."""
    p, n, c = x["p"], x["n"], x["c"]
    zz = x["zz"]
    for k in range(len(zz) - 1, max(3, len(zz) - 9), -1):
        if zz[k][1] != "H":
            continue
        a, b_, _, d, e = zz[k - 4:k + 1]
        if not (p["tri_span"][0] <= e[0] - a[0] <= p["tri_span"][1]):
            continue
        u = pat_unit(x, a[0])
        highs = [zz[k - 4][2], zz[k - 2][2], zz[k][2]]
        if max(highs) - min(highs) > p["tri_tol_u"] * u or d[2] - b_[2] < p["tri_rise_u"] * u:
            continue
        top = max(highs)
        state = pat_state(c, a[0], top)
        if state is None or (state == "coiled" and c[-1] < (d[2] + top) / 2.0):
            continue
        return {"pattern": "ascending_triangle", "state": state, "level": top + TICK, "stop": d[2] - p["stop_u"] * u,
                "target": top + (top - b_[2]), "anchor": a[0], "pivots": [(t[0], t[2]) for t in zz[k - 4:k + 1]],
                "vol_dry": None}
    return None


def detect_inverse_head_shoulders(x):
    """Low, high, low, high, low with the middle low the deepest; coiled under the neckline."""
    p, n, c, l = x["p"], x["n"], x["c"], x["l"]
    zz = x["zz"]
    for k in range(len(zz) - 1, max(3, len(zz) - 9), -1):
        if zz[k][1] != "L":
            continue
        a, b_, head, d, e = zz[k - 4:k + 1]
        u = pat_unit(x, a[0])
        shoulder = min(a[2], e[2])
        neck = max(b_[2], d[2])
        if head[2] > shoulder - p["ihs_head_u"] * u or min(b_[2], d[2]) <= max(a[2], e[2]):
            continue
        before = l[max(0, head[0] - p["ihs_prior_bars"]):head[0]]
        if (before and min(before) < head[2]) or abs(b_[2] - d[2]) > p["ihs_neck_u"] * u:
            continue
        if abs(a[2] - e[2]) > p["ihs_sym"] * (neck - head[2]):
            continue
        state = pat_state(c, e[0], neck)
        if state is None or (state == "coiled" and c[-1] < (e[2] + neck) / 2.0):
            continue
        return {"pattern": "inverse_head_shoulders", "state": state, "level": neck + TICK,
                "stop": e[2] - p["stop_u"] * u, "target": neck + (neck - head[2]), "anchor": a[0],
                "pivots": [(t[0], t[2]) for t in zz[k - 4:k + 1]], "vol_dry": None}
    return None


def detect_rising_support(x):
    """A rising line through three or more pivot lows with the last close just over it: a retest of support."""
    p, n, c, h = x["p"], x["n"], x["c"], x["h"]
    ws = max(0, n - p["rs_window"])
    lows = [t for t in x["pl"] if t[0] >= ws]
    if len(lows) < p["rs_touches"]:
        return None
    u = pat_unit(x, ws)
    best = None
    recent = lows[-8:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            a, b_ = recent[i], recent[j]
            if b_[0] - a[0] < p["rs_gap"] or b_[1] <= a[1]:
                continue
            slope = (b_[1] - a[1]) / float(b_[0] - a[0])
            touches = [t for t in lows if t[0] >= a[0] and abs(t[1] - (a[1] + slope * (t[0] - a[0]))) <= p["rs_touch_u"] * u]
            if len(touches) < p["rs_touches"]:
                continue
            if any(c[k] < a[1] + slope * (k - a[0]) for k in range(a[0], n)):
                continue
            line = a[1] + slope * (n - 1 - a[0])
            if not (line <= c[-1] <= line * (1 + p["rs_max_above"])):
                continue
            key = (len(touches), touches[-1][0])
            if best is None or key > best[0]:
                best = (key, a, line, touches)
    if best is None:
        return None
    _, a, line, touches = best
    return {"pattern": "rising_support", "state": "coiled", "level": h[-1] + TICK, "stop": line - p["rs_stop_u"] * u,
            "target": None, "anchor": a[0], "pivots": [(t[0], t[1]) for t in touches], "vol_dry": None}


DETECTORS = [("vcp", detect_vcp), ("double_bottom", detect_double_bottom), ("flag", detect_flag),
             ("ascending_triangle", detect_ascending_triangle),
             ("inverse_head_shoulders", detect_inverse_head_shoulders), ("rising_support", detect_rising_support)]


def pattern_hit(x, raw):
    """A detector's answer -> the watchlist hit shape (no symbol yet)."""
    level, stop = raw["level"], raw["stop"]
    if level <= stop:
        return None
    v = x["v"]
    mean50 = sum(v[-51:-1]) / 50.0 if len(v) >= 51 else 0
    return {"setups": [raw["pattern"]], "price": round(x["c"][-1], 2), "close_date": x["dates"][-1].isoformat(),
            "pattern": raw["pattern"], "state": raw["state"], "level": round(level, 2), "stop": round(stop, 2),
            "risk_pct": round((level - stop) / level * 100, 2),
            "target": round(raw["target"], 2) if raw["target"] is not None else None,
            "anchor_date": x["dates"][raw["anchor"]].isoformat(),
            "pivots": [[x["dates"][i].isoformat(), round(pr, 2)] for i, pr in raw["pivots"]],
            "vol_x": round(v[-1] / mean50, 2) if mean50 > 0 else None, "vol_dry": raw["vol_dry"]}


def find_patterns(rows, params=None, only=None):
    """Daily rows (ascending, (date, o, h, l, c, v)) -> pattern hits, best known first. `params` overrides PAT,
    `only` is a list of pattern names. A VCP and an ascending triangle on one chart merge into one hit."""
    x = pat_context(rows, params)
    if x["n"] < x["p"]["min_bars"]:
        return []
    found = []
    for name, fn in DETECTORS:
        if only and name not in only:
            continue
        raw = fn(x)
        hit = pattern_hit(x, raw) if raw else None
        if hit:
            found.append(hit)
    by = {h["pattern"]: h for h in found}
    if "vcp" in by and "ascending_triangle" in by:
        tri = by["ascending_triangle"]
        by["vcp"]["setups"].append("ascending_triangle")
        by["vcp"]["also"] = [{"pattern": "ascending_triangle", "level": tri["level"], "stop": tri["stop"],
                              "target": tri["target"], "anchor_date": tri["anchor_date"]}]
        found.remove(tri)
    return found


def scan_patterns(rows_by_sym, symbols, min_price=20.0, min_volume=100000, only=None, state=None, params=None):
    """Pure. rows_by_sym = {SYM: daily rows}. Returns (hits, counts, last_date). One hit per (symbol, pattern,
    anchor date)."""
    p = dict(PAT, **(params or {}))
    counts = {"requested": len(symbols), "cached": 0, "short": 0, "illiquid": 0, "scanned": 0}
    hits, dates, seen = [], {}, set()
    for sym in symbols:
        rows = rows_by_sym.get(sym)
        if not rows:
            continue
        counts["cached"] += 1
        if len(rows) < p["min_bars"]:
            counts["short"] += 1
            continue
        vol20 = sum(r[5] for r in rows[-20:]) / float(min(20, len(rows)))
        if rows[-1][4] < min_price or vol20 < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        d = rows[-1][0]
        dates[d] = dates.get(d, 0) + 1
        for h in find_patterns(rows, params, only):
            key = (sym, h["pattern"], h["anchor_date"])
            if key in seen or (state and h["state"] != state):
                continue
            seen.add(key)
            hits.append(dict({"symbol": sym}, **h))
    hits.sort(key=lambda h: (h["state"] != "coiled", (h["level"] - h["price"]) / h["level"], h["symbol"]))
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


def carry_first_seen(hits, previous):
    """Stamp each hit with first_seen: the date from the previous watchlist for the same (symbol, pattern, anchor
    date), else its own close date. `previous` is a parsed watchlist document or None."""
    old = {}
    if isinstance(previous, dict) and previous.get("mode") == "patterns":
        for h in previous.get("hits") or []:
            if isinstance(h, dict) and h.get("first_seen"):
                old[(h.get("symbol"), h.get("pattern"), h.get("anchor_date"))] = h["first_seen"]
    for h in hits:
        h["first_seen"] = old.get((h["symbol"], h["pattern"], h["anchor_date"]), h["close_date"])
    return hits


# ---------------------------------------------------------------- intraday chart patterns

# The double bottom and the pole and flag on the current session's 5-minute bars: the same detectors as the daily
# ones with the intraday parameters from the stock-pattern notes (pivots 3 + 3, bars not days). A bar's high and low
# are real (stage 2 charts), the session so far is the whole look-back.
INTRA_MIN_BARS = 12         # the flag looks at the last 12 bars; before 10:15 there is nothing to read
INTRA = {
    "L": 3, "R": 3,
    "db_gap": (8, 60), "db_rebound_u": 0.0, "db_rebound_pct": 0.8,   # lows 8 to 60 bars apart, rebound >= 0.8 % (repo)
    "db_prior_bars": SESSION_BARS, "db_prior_drop": 1.015,           # the session before low A reached 1.015 x low A
    "flag_min_bars": INTRA_MIN_BARS, "flag_recent": 12, "flag_age": (4, 11), "flag_lookback": SESSION_BARS,
    "flag_gain": 0.02, "flag_base_bars": 24, "flag_retrace": 0.5,    # top in the last 12 bars, 4+ old, session high
}
INTRA_DETECTORS = [("double_bottom", detect_double_bottom), ("flag", detect_flag)]
INTRA_PATTERNS = [k for k, _ in INTRA_DETECTORS]
INTRA_LABEL = {"double_bottom": "intraday double bottom", "flag": "intraday flag"}


def intraday_hits(bars, only=None, state=None):
    """Session bars (session_bars() output, real highs and lows) -> intraday pattern hits (no symbol yet). A pure
    function of the bars up to the last completed one."""
    if len(bars) < INTRA_MIN_BARS or any(b["h"] is None or b["l"] is None for b in bars):
        return []
    rows = [(b["start"], b["c"], b["h"], b["l"], b["c"], b["v"]) for b in bars]
    x = pat_context(rows, INTRA)
    last_close = bars[-1]["start"] + datetime.timedelta(minutes=BAR_MIN)
    out = []
    for name, fn in INTRA_DETECTORS:
        if only and name != only:
            continue
        raw = fn(x)
        if not raw or raw["level"] <= raw["stop"] or (state and raw["state"] != state):
            continue
        tag = "intraday_" + name
        out.append({"setups": [tag], "pattern": tag, "state": raw["state"], "price": round(x["c"][-1], 2),
                    "at": clock_label(last_close), "level": round(raw["level"], 2), "stop": round(raw["stop"], 2),
                    "risk_pct": round((raw["level"] - raw["stop"]) / raw["level"] * 100, 2),
                    "target": round(raw["target"], 2) if raw["target"] is not None else None,
                    "anchor_time": x["dates"][raw["anchor"]].strftime("%H:%M"),
                    "pivots": [[x["dates"][i].strftime("%H:%M"), round(pr, 2)] for i, pr in raw["pivots"]]})
    return out


def intraday_window(now_utc, at, until, explicit_day=None):
    """-> (day, cutoff naive IST) for `patterns --intraday`, or raise ScanError with the plain message. Like
    orb_window: a trading day, a cut-off not after now, not after the close, not after `until`."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run patterns --intraday on a trading day "
                        "during the session, or replay a past day with --offline." % (day_label(day), why))
    s = session(day)
    close_dt = datetime.datetime.combine(day, s[1])
    first = slot(day, INTRA_MIN_BARS)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("The market closed at %s IST on %s. Replay the session with --at 11:00."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first:
        raise ScanError("It is %s IST: an intraday pattern needs %d five-minute bars, the first complete set closes "
                        "at %s. Run patterns --intraday again after that." % (clock_label(cutoff), INTRA_MIN_BARS,
                                                                              clock_label(first)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new entries (no_entries_after): an intraday "
                        "pattern found now is expired. Run it tomorrow after %s, or replay today with --at 11:00."
                        % (clock_label(cutoff), until.strftime("%H:%M"), clock_label(first)))
    return day, cutoff


def scan_intraday_patterns(stage1, symbols, day, cutoff, min_price=0.0, min_volume=0, confirm=None,
                           max_confirm=None, only=None, state=None):
    """stage1 = {SYM: series}; confirm(sym) -> a series with real highs and lows (None = stage 1 has them). With
    max_confirm only the most traded (price x volume so far) are read. Returns (hits, counts, failed {sym: why})."""
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, INTRA_MIN_BARS):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        cands.append((sym, bars, bars[-1]["c"] * vol))
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
            if not usable(full, day, cutoff, INTRA_MIN_BARS):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        for h in intraday_hits(full, only, state):
            hits.append(dict({"symbol": sym}, **h))
    hits.sort(key=lambda h: (h["state"] != "coiled", (h["level"] - h["price"]) / h["level"], h["symbol"]))
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    """Batches of 20. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {urllib.parse.unquote(to_yahoo(s)).upper(): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)), wanted)
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 50 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)  # offline: every chart in the file, unless a list was given
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if os.path.exists(path):
            syms = symbols_from_file(path)
        elif a.universe:
            raise ScanError("no %s: run `scan.py universe --out %s` first, or pass --symbols" % (path, path))
        else:
            text, fb = fallback_list("no %s yet" % path)
            kept, _, _ = build_universe({NIFTY500_FILE: text})
            syms = [k for k, _ in kept]
            note_out(a, fb + " (run `scan.py universe` to save a list)")
    out = []
    for s in syms:
        s = check_symbol(s)
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [clean_symbol(x.get("symbol") if isinstance(x, dict) else x) for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(clean_symbol(ln.split("|")[0]))
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def inr(x):
    """12345678.9 -> '₹1,23,45,678.90' (Indian digit grouping)."""
    neg = x < 0
    whole, frac = ("%.2f" % abs(float(x))).split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return "%s₹%s.%s" % ("-" if neg else "", whole, frac)


money = inr


def indian(n):
    """An integer with Indian grouping: 1234567 -> '12,34,567'."""
    n = int(n)
    return ("-" if n < 0 else "") + inr(abs(n))[1:-3]


# ---------------------------------------------------------------- the trader-data cache

def daily_cache_path(sym, data_dir=None):
    """trader/data/daily/<SYM>.adj.csv when trader-data wrote one (split/bonus adjusted), else <SYM>.csv."""
    base = os.path.join(data_dir or DATA_DIR, "daily")
    adj = os.path.join(base, "%s.adj.csv" % sym)
    return adj if os.path.exists(adj) else os.path.join(base, "%s.csv" % sym)


def intraday_cache_path(sym, data_dir=None):
    return os.path.join(data_dir or DATA_DIR, "intraday", "%s-5m.json" % sym)


def read_daily_csv(path):
    """date,open,high,low,close,volume -> [(date, o, h, l, c, v)] ascending, bad rows skipped."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = datetime.date.fromisoformat((r.get("date") or "").strip())
                c = float(r["close"])
            except (ValueError, TypeError, KeyError):
                continue

            def f(k):
                try:
                    return float(r.get(k) or c)
                except ValueError:
                    return c
            try:
                v = float(r.get("volume") or 0)
            except ValueError:
                v = 0.0
            out[d] = (d, f("open"), f("high"), f("low"), c, v)
    return [out[d] for d in sorted(out)]


def series_from_daily(rows):
    """Daily CSV rows -> the series shape the scans read (each bar stamped 09:15 IST)."""
    t = [int((datetime.datetime.combine(r[0], OPEN) - IST_OFFSET - datetime.datetime(1970, 1, 1)).total_seconds())
         for r in rows]
    return {"t": t, "o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows],
            "c": [r[4] for r in rows], "v": [r[5] for r in rows],
            "meta": {"regularMarketPrice": rows[-1][4] if rows else None,
                     "regularMarketVolume": rows[-1][5] if rows else None}}


def load_daily_cache(symbols, data_dir=None):
    """-> ({SYM: series}, {SYM: why}, [dates of the last bars])."""
    data, missing, lasts = {}, {}, []
    for sym in symbols:
        path = daily_cache_path(sym, data_dir)
        if not os.path.exists(path):
            missing[sym] = "no daily cache at %s" % path
            continue
        try:
            rows = read_daily_csv(path)
        except OSError as e:
            missing[sym] = "could not read %s: %s" % (path, e)
            continue
        if not rows:
            missing[sym] = "%s has no rows" % path
            continue
        data[sym] = series_from_daily(rows)
        lasts.append(rows[-1][0])
    return data, missing, lasts


def load_intraday_cache(symbols, data_dir=None):
    """-> ({SYM: series}, {SYM: why}, [IST datetimes of the last bars])."""
    data, missing, lasts = {}, {}, []
    for sym in symbols:
        path = intraday_cache_path(sym, data_dir)
        if not os.path.exists(path):
            missing[sym] = "no intraday cache at %s" % path
            continue
        try:
            series = parse_chart(_read_maybe_gz(path), sym)
        except (OSError, ScanError) as e:
            missing[sym] = "%s: %s" % (path, e)
            continue
        data[sym] = series
        ts = [t for t in series["t"] if t]
        if ts:
            lasts.append(ts_to_ist(max(ts)))
    return data, missing, lasts


def cache_note(kind, n, lasts, fell_back=False):
    """'using cached data from <time>' for n symbols, with the refresh command."""
    if not n:
        return ""
    if lasts:
        lo, hi = min(lasts), max(lasts)
        fmt = (lambda d: d.strftime("%Y-%m-%d %H:%M IST")) if kind == "intraday" else (lambda d: d.isoformat())
        when = fmt(hi) if lo == hi else "%s to %s" % (fmt(lo), fmt(hi))
    else:
        when = "an unknown time"
    return "%susing cached data from %s for %s symbol%s, run `python3 trader-data/data.py %s SYMBOL` to refresh" % (
        "Yahoo failed: " if fell_back else "", when, indian(n), "" if n == 1 else "s", kind)


def note_out(a, msg):
    if msg and not getattr(a, "json", False):
        print("Note: " + msg + ".")


# ---------------------------------------------------------------- universe fallbacks

def list_from_any(text, what):
    """A symbol list CSV with a Symbol, SYMBOL or symbol column -> the Nifty 500 list's shape."""
    rows = _csv_rows(text, what)
    if not rows:
        raise ScanError("%s is empty" % what)
    key = next((k for k in ("Symbol", "SYMBOL", "symbol", "tradingsymbol") if k in rows[0]), None)
    if key is None:
        raise ScanError("%s has no Symbol column" % what)
    name = next((k for k in ("Company Name", "NAME OF COMPANY", "name", "Name", "company") if k in rows[0]), None)
    series = next((k for k in ("Series", "SERIES", " SERIES", "series") if k in rows[0]), None)
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Company Name", "Industry", "Symbol", "Series", "ISIN Code"])
    for r in rows:
        w.writerow([r.get(name, "") if name else "", "", r.get(key, ""), (r.get(series) if series else "") or "EQ", ""])
    return out.getvalue()


def snapshot_info():
    try:
        with open(SNAPSHOT_META, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def fallback_list(why=None):
    """trader/data/universe.csv, then the bundled snapshot -> (Nifty 500 shaped text, note)."""
    lead = ("%s; " % why) if why else ""
    path = os.path.join(DATA_DIR, "universe.csv")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        try:
            return list_from_any(text, path), "%sused %s (trader-data)" % (lead, path.replace(os.sep, "/"))
        except ScanError as e:
            lead += "%s: %s; " % (path, e)
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        info = snapshot_info()
        return text, ("%sused the bundled offline Nifty 500 snapshot (%s, as of %s): constituents change "
                      "twice a year, refresh with `scan.py universe` when niftyindices.com answers" % (
                          lead, info.get("short", "nifty500-snapshot.csv"), info.get("as_of", "unknown date")))
    raise ScanError("%sno trader/data/universe.csv and no bundled snapshot. Next: %s" % (lead, DATA_HINT))


# ---------------------------------------------------------------- delivery

def delivery_path(sym, data_dir=None):
    return os.path.join(data_dir or DATA_DIR, "delivery", "%s.csv" % sym)


def read_delivery_csv(path):
    """trader-data's trader/data/delivery/<SYM>.csv (date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series; an
    empty field is missing) -> [{"date", "deliv_qty", "qty_per_trade", "series"}] ascending. Rows with a bad date are
    skipped, a later row for a date wins, a field that is not a number is missing."""
    def num(v):
        try:
            return float(v) if (v or "").strip() else None
        except ValueError:
            return None
    out = {}
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = datetime.date.fromisoformat((r.get("date") or "").strip())
            except ValueError:
                continue
            out[d] = {"date": d, "deliv_qty": num(r.get("deliv_qty")), "qty_per_trade": num(r.get("qty_per_trade")),
                      "series": (r.get("series") or "").strip().upper()}
    return [out[d] for d in sorted(out)]


def load_delivery(symbols, data_dir=None):
    """-> {SYM: delivery rows} for the symbols that have a file; a file that cannot be read is left out."""
    out = {}
    for sym in symbols:
        path = delivery_path(sym, data_dir)
        if os.path.exists(path):
            try:
                rows = read_delivery_csv(path)
            except OSError:
                continue
            if rows:
                out[sym] = rows
    return out


def delivery_reading(rows, close_date=None):
    """Delivery rows (ascending) -> {"date", "dq", "tq", "series", "spike"} or None for no rows. DQ = the latest
    delivery quantity over its mean, TQ = the latest quantity per trade over its mean, both over the last
    DELIVERY_BARS sessions in the file (the latest included, a missing value left out). A ratio needs the latest
    value and at least DELIVERY_MIN values in the window, else it is None. `spike`: DQ and TQ both over
    DELIVERY_RATIO, a series that is not BE or BZ, and (when close_date is given) the reading is for that day."""
    if not rows:
        return None
    window = rows[-DELIVERY_BARS:]
    last = window[-1]

    def ratio(key):
        vals = [r[key] for r in window if r[key] is not None]
        if last[key] is None or len(vals) < DELIVERY_MIN:
            return None
        mean = sum(vals) / len(vals)
        return last[key] / mean if mean > 0 else None
    dq, tq = ratio("deliv_qty"), ratio("qty_per_trade")
    spike = (dq is not None and tq is not None and dq > DELIVERY_RATIO and tq > DELIVERY_RATIO
             and last["series"] not in DELIVERY_T2T and (close_date is None or last["date"] == close_date))
    return {"date": last["date"].isoformat(), "dq": dq, "tq": tq, "series": last["series"], "spike": spike}


# ---------------------------------------------------------------- relative strength

def mansfield_series(stock, bench, n=MANSFIELD_DAILY):
    """stock: [(date, close)] ascending, bench: {date: close}. Joined on the common dates:
    Dorsey RS = stock / index x 100, Mansfield RS = (RS / mean of the last n RS - 1) x 100.
    -> [(date, dorsey, mansfield or None)]; Mansfield starts at the n-th common date."""
    rs = [(d, c / bench[d] * 100.0) for d, c in stock if bench.get(d)]
    out = []
    for i, (d, v) in enumerate(rs):
        m = None
        if i >= n - 1:
            avg = sum(x for _, x in rs[i - n + 1:i + 1]) / n
            m = (v / avg - 1) * 100.0 if avg else None
        out.append((d, v, m))
    return out


def read_benchmark(data_dir=None):
    """The relative-strength benchmark's closes -> ({date: close}, name, note). trader/data/index/nifty-50.csv
    (trader-data), else NIFTYBEES (.adj.csv first) in trader/data/daily, else none and a note saying so."""
    base = data_dir or DATA_DIR
    short = None   # a benchmark too short for Mansfield (index --days 3 writes 3 rows): only if nothing is longer
    for name, path in (("Nifty 50 index", os.path.join(base, "index", "nifty-50.csv")),
                       ("NIFTYBEES", daily_cache_path("NIFTYBEES", base))):
        if not os.path.exists(path):
            continue
        try:
            rows = read_daily_csv(path)
        except OSError:
            continue
        if len(rows) > MANSFIELD_DAILY + MANSFIELD_RISE_BARS:
            return {r[0]: r[4] for r in rows}, name, ""
        if rows and short is None:
            short = ({r[0]: r[4] for r in rows}, name, "benchmark %s has only %d sessions" % (name, len(rows)))
    if short:
        return short
    return {}, None, ("no benchmark (neither %s nor NIFTYBEES in %s): mansfield_rs is null and rs_positive is off"
                      % (os.path.join(base, "index", "nifty-50.csv"), os.path.join(base, "daily")))


# ---------------------------------------------------------------- market breadth

def ema_series(values, n):
    """EMA with alpha 2 / (n + 1), seeded with the plain average of the first n values and first given on the n-th
    value. -> a list the length of `values`, None before the seed."""
    out, prev = [], None
    alpha = 2.0 / (n + 1)
    for i, x in enumerate(values):
        if i + 1 < n:
            out.append(None)
            continue
        prev = sum(values[:n]) / float(n) if i + 1 == n else prev + alpha * (x - prev)
        out.append(prev)
    return out


def breadth_series(rows_by_sym, sma=BREADTH_SMA, high_bars=BREADTH_HIGH_BARS, fast=BREADTH_FAST, slow=BREADTH_SLOW,
                   days=0):
    """Market breadth from daily rows ({SYM: [(date, o, h, l, c, v)]}): one dict per date with a bar.
    pct_50 / pct_200: percent of the symbols (that have the history) closing above their moving average. A new high is
    a high over the highest high of the `high_bars` sessions before it (today excluded), a new low likewise;
    net_new_highs is the running sum of highs minus lows. A symbol advances when it closes over its own previous
    close; net_adv_ratio = (advances - declines) / symbols with a previous close x 100, ad_line its running sum
    divided by 100 (a ratio line, not counts). fast and slow EMAs of net_adv_ratio (seeded with the plain average
    of the first n); mcclellan_osc = fast - slow. Dates with no symbol that has a previous close are skipped.
    `days` keeps only the last N dates (0 = all)."""
    all_dates = sorted({r[0] for rows in rows_by_sym.values() for r in rows})
    first = all_dates[-days] if days and len(all_dates) > days else None
    acc = {}
    for rows in rows_by_sym.values():
        c, hs, ls = [r[4] for r in rows], [r[2] for r in rows], [r[3] for r in rows]
        pre = [0.0]
        for x in c:
            pre.append(pre[-1] + x)
        for i, r in enumerate(rows):
            if first is not None and r[0] < first:
                continue
            a = acc.setdefault(r[0], [0, 0, 0, 0, 0, 0, 0, 0, 0])
            for k, n in enumerate(sma):
                if i + 1 >= n:
                    a[2 * k] += 1
                    if c[i] > (pre[i + 1] - pre[i + 1 - n]) / n:
                        a[2 * k + 1] += 1
            if i >= high_bars:
                if hs[i] > max(hs[i - high_bars:i]):
                    a[4] += 1
                if ls[i] < min(ls[i - high_bars:i]):
                    a[5] += 1
            if i >= 1:
                a[8] += 1
                if c[i] > c[i - 1]:
                    a[6] += 1
                elif c[i] < c[i - 1]:
                    a[7] += 1
    dates = [d for d in sorted(acc) if acc[d][8]]
    ratios = [(acc[d][6] - acc[d][7]) * 100.0 / acc[d][8] for d in dates]
    fast_ema, slow_ema = ema_series(ratios, fast), ema_series(ratios, slow)
    out, nnh, ad = [], 0, 0.0
    for i, d in enumerate(dates):
        a = acc[d]
        nnh += a[4] - a[5]
        ad += ratios[i] / 100.0
        out.append({"date": d.isoformat(), "pct_50": round(a[1] * 100.0 / a[0], 2) if a[0] else None,
                    "pct_200": round(a[3] * 100.0 / a[2], 2) if a[2] else None, "net_new_highs": nnh,
                    "ad_line": round(ad, 4),
                    "mcclellan_osc": round(fast_ema[i] - slow_ema[i], 4) if slow_ema[i] is not None else None,
                    "net_adv_ratio": round(ratios[i], 2),
                    "fast_ema": round(fast_ema[i], 4) if fast_ema[i] is not None else None,
                    "slow_ema": round(slow_ema[i], 4) if slow_ema[i] is not None else None})
    return out


def write_breadth(path, series):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(BREADTH_COLUMNS)
        for r in series:
            w.writerow(["" if r[k] is None else r[k] for k in BREADTH_COLUMNS])


def regime_note(data_dir=None):
    """One line about the market from the newest row of breadth.csv, or '' when there is no such file."""
    path = os.path.join(data_dir or DATA_DIR, "breadth.csv")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        r = rows[-1]

        def num(k):
            return float(r[k]) if (r.get(k) or "").strip() else None
        p50, p200, mc = num("pct_50"), num("pct_200"), num("mcclellan_osc")
        gauge = p200 if p200 is not None else p50
        if gauge is None:
            return ""
    except (OSError, ValueError, IndexError, KeyError):
        return ""
    word = "broad strength" if gauge >= BREADTH_STRONG else "weak" if gauge <= BREADTH_WEAK else "mixed"
    return "Market breadth %s: %s (%s above the 200-day average, %s above the 50-day%s)." % (
        r["date"], word, ("%.0f%%" % p200) if p200 is not None else "-", ("%.0f%%" % p50) if p50 is not None else "-",
        (", McClellan %+.1f" % mc) if mc is not None else "")


# ---------------------------------------------------------------- flows and pre-open (trader-data files)

FLOWS_WINDOW = 5   # sessions in the FII / DII running total
FLOWS_COLUMNS = ("date", "category", "net_cr")
FLOW_CATEGORY = {"FII/FPI": "fii", "FII": "fii", "FPI": "fii", "DII": "dii"}


def _num(v):
    """A number from a JSON or CSV field, else None (booleans and NaN are not numbers)."""
    if isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", "").strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def read_flows(data_dir=None):
    """trader/data/flows.csv (trader-data: date,category,buy_cr,sell_cr,net_cr; FII/FPI and DII, rupee crore) ->
    {"date", "fii_net_cr", "dii_net_cr", "sessions", "fii_net_cr_5d", "dii_net_cr_5d"} for the newest date and the
    running total over the last FLOWS_WINDOW dates in the file, or None when the file is missing, has no usable
    row or is not the shape this tool knows. A bad row is skipped; the later row for a date and category wins."""
    path = os.path.join(data_dir or DATA_DIR, "flows.csv")
    got = {}
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            if not rd.fieldnames or any(k not in [(f or "").strip() for f in rd.fieldnames] for k in FLOWS_COLUMNS):
                return None
            for r in rd:
                r = {(k or "").strip(): v for k, v in r.items()}
                cat = FLOW_CATEGORY.get(str(r.get("category") or "").strip().upper())
                net = _num(r.get("net_cr"))
                if net is None:
                    buy, sell = _num(r.get("buy_cr")), _num(r.get("sell_cr"))
                    net = buy - sell if buy is not None and sell is not None else None
                try:
                    d = datetime.date.fromisoformat(str(r.get("date") or "").strip())
                except ValueError:
                    continue
                if cat and net is not None:
                    got[(d, cat)] = net
    except (OSError, ValueError, csv.Error, UnicodeDecodeError):
        return None
    if not got:
        return None
    dates = sorted({d for d, _ in got})
    last, window = dates[-1], dates[-FLOWS_WINDOW:]

    def total(cat):
        vals = [got[(d, cat)] for d in window if (d, cat) in got]
        return round(sum(vals), 2) if vals else None
    out = {"date": last.isoformat(), "fii_net_cr": got.get((last, "fii")), "dii_net_cr": got.get((last, "dii")),
           "sessions": len(window)}
    out["fii_net_cr_5d"], out["dii_net_cr_5d"] = total("fii"), total("dii")
    return out


def flows_line(f):
    """'FII/FPI net -943.60 cr, DII net +1,924.41 cr on 2026-10-29 (5-session FII net ..., DII net ...).'"""
    def part(name, v):
        return ["%s net %s cr" % (name, format(v, "+,.2f"))] if v is not None else []
    head = part("FII/FPI", f["fii_net_cr"]) + part("DII", f["dii_net_cr"])
    if not head:
        return ""
    line = "%s on %s" % (", ".join(head), f["date"])
    if f["sessions"] > 1:
        tail = part("FII", f["fii_net_cr_5d"]) + part("DII", f["dii_net_cr_5d"])
        if tail:
            line += " (%d-session %s)" % (f["sessions"], ", ".join(tail))
    return line + "."


def read_preopen(data_dir=None):
    """trader/data/preopen.json (trader-data: the pre-open session's indicative prices) -> {"date", "advances",
    "declines", "unchanged", "stocks": {SYMBOL: {"iep", "change_pct"}}}, or None when the file is missing, corrupt or
    has no ISO date."""
    try:
        with open(os.path.join(data_dir or DATA_DIR, "preopen.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        d = datetime.date.fromisoformat(str(doc.get("date")))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    stocks = {}
    for s in (doc.get("stocks") if isinstance(doc.get("stocks"), list) else []):
        if isinstance(s, dict) and s.get("symbol"):
            stocks[str(s["symbol"]).strip().upper()] = {"iep": _num(s.get("iep")), "change_pct": _num(s.get("change_pct"))}
    return {"date": d, "advances": _num(doc.get("advances")), "declines": _num(doc.get("declines")),
            "unchanged": _num(doc.get("unchanged")), "stocks": stocks}


def preopen_for(day, data_dir=None):
    """The pre-open file when its date is the scan's session day, else None."""
    po = read_preopen(data_dir)
    return po if po and po["date"] == day else None


def preopen_line(po):
    parts = [("%s %s" % (indian(po[k]), word)) for k, word in (("advances", "advances"), ("declines", "declines"),
                                                               ("unchanged", "unchanged")) if po[k] is not None]
    return ("Pre-open %s: %s." % (po["date"].isoformat(), ", ".join(parts))) if parts else ""


def add_preopen(hits, po):
    """Stamp each hit whose symbol is in the pre-open file with preopen_gap_pct (the file's change_pct) and
    preopen_iep. A hit with no match, or a missing number, gets no field."""
    for h in hits:
        s = po["stocks"].get(h["symbol"]) if po else None
        if s and s["change_pct"] is not None:
            h["preopen_gap_pct"] = s["change_pct"]
        if s and s["iep"] is not None:
            h["preopen_iep"] = s["iep"]
    return hits


def add_context(doc, data_dir):
    """Put the FII / DII flows into a watchlist document and return the header lines to print."""
    f = read_flows(data_dir)
    line = flows_line(f) if f else ""
    if line:
        doc["flows"] = f
    return line


# ---------------------------------------------------------------- long-term screen

LT_SCREENS = [
    ("above_200dma", "above the 200-day average"),
    ("near_52w_high", "near the 52-week high"),
    ("near_52w_low", "near the 52-week low (value screen)"),
    ("oversold", "oversold (RSI under 30)"),
    ("swing_up", "swing structure up (change-of-character level holding)"),
    ("swing_reversal_up", "swing trend just reversed up"),
    ("rs_positive", "relative strength over the Nifty positive and rising (Mansfield)"),
    ("delivery_spike", "delivery spike (institutional-size trades, high delivery)"),
]
LT_LABEL = dict(LT_SCREENS)


def longterm_metrics(rows, bench=None, delivery=None):
    """Daily rows (ascending, >= LT_BARS) -> the numbers the long-term screen reads. `bench` is {date: close} of
    the relative-strength benchmark (None or empty = mansfield_rs is None); `delivery` this stock's delivery rows."""
    c = [r[4] for r in rows]
    yr = rows[-YEAR_BARS:]
    last = c[-1]
    dma200 = sum(c[-200:]) / 200.0
    dma50 = sum(c[-50:]) / 50.0
    hi = max(r[2] for r in yr)
    lo = min(r[3] for r in yr)
    vol20 = sum(r[5] for r in rows[-20:]) / float(min(20, len(rows)))
    r = rsi(c[-120:])
    sw = swing_info([(x[2], x[3], x[4]) for x in rows[-SWING_FEED:]])
    ms = [x[2] for x in mansfield_series([(x[0], x[4]) for x in rows[-SWING_FEED:]], bench)] if bench else []
    ms = [v for v in ms if v is not None]
    dr = delivery_reading(delivery, rows[-1][0])
    return {"close": last, "mansfield_rs": ms[-1] if ms else None,
            "dq": dr["dq"] if dr else None, "dq_date": dr["date"] if dr and dr["dq"] is not None else None,
            "delivery_spike": bool(dr and dr["spike"]),
            "mansfield_prev": ms[-1 - MANSFIELD_RISE_BARS] if len(ms) > MANSFIELD_RISE_BARS else None, "dma50": dma50, "dma200": dma200, "vs_200dma_pct": (last / dma200 - 1) * 100,
            "high52": hi, "low52": lo, "from_high_pct": (last / hi - 1) * 100,
            "above_low_pct": (last / lo - 1) * 100 if lo > 0 else None, "rsi": r, "avg_volume20": vol20,
            "ret_1y_pct": (last / yr[0][4] - 1) * 100 if yr[0][4] else None, "bars": len(rows),
            "full_year": len(rows) >= YEAR_BARS, "swing_trend": sw["trend"], "swing_coc": sw["coc"],
            "swing_event": sw["event"], "swing_bars_since": sw["bars_since"], "swing_stable": sw["stable"]}


def longterm_tags(m, near_high=5.0, near_low=10.0):
    tags = []
    if m["close"] > m["dma200"]:
        tags.append("above_200dma")
    if m["from_high_pct"] >= -near_high:
        tags.append("near_52w_high")
    if m["above_low_pct"] is not None and m["above_low_pct"] <= near_low:
        tags.append("near_52w_low")
    if m["rsi"] is not None and m["rsi"] < 30:
        tags.append("oversold")
    tags.extend(swing_tag_list(m["swing_trend"], m["swing_event"], m["swing_stable"]))
    if m["mansfield_rs"] is not None and m["mansfield_prev"] is not None and 0 < m["mansfield_rs"] and \
            m["mansfield_rs"] > m["mansfield_prev"]:
        tags.append("rs_positive")
    if m["delivery_spike"]:
        tags.append("delivery_spike")
    return tags


def scan_longterm(rows_by_sym, symbols, min_price=20.0, min_volume=100000, screen=None, near_high=5.0,
                  near_low=10.0, benchmark=None, delivery=None):
    """Pure. rows_by_sym = {SYM: daily rows}; benchmark = {date: close} for the relative-strength columns;
    delivery = {SYM: delivery rows} for dq and the delivery_spike screen. Returns (hits, counts, last_date)."""
    counts = {"requested": len(symbols), "cached": 0, "short": 0, "illiquid": 0, "scanned": 0}
    hits, dates = [], {}
    for sym in symbols:
        rows = rows_by_sym.get(sym)
        if not rows:
            continue
        counts["cached"] += 1
        if len(rows) < LT_BARS:
            counts["short"] += 1
            continue
        m = longterm_metrics(rows, benchmark, (delivery or {}).get(sym))
        if m["close"] < min_price or m["avg_volume20"] < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        d = rows[-1][0]
        dates[d] = dates.get(d, 0) + 1
        tags = longterm_tags(m, near_high, near_low)
        if screen:
            tags = [t for t in tags if t == screen]
        if not tags:
            continue
        hits.append({"symbol": sym, "setups": tags, "price": round(m["close"], 2), "close_date": d.isoformat(),
                     "dma50": round(m["dma50"], 2), "dma200": round(m["dma200"], 2),
                     "vs_200dma_pct": round(m["vs_200dma_pct"], 2), "high52": round(m["high52"], 2),
                     "low52": round(m["low52"], 2), "from_high_pct": round(m["from_high_pct"], 2),
                     "above_low_pct": round(m["above_low_pct"], 2) if m["above_low_pct"] is not None else None,
                     "rsi": round(m["rsi"], 1) if m["rsi"] is not None else None,
                     "ret_1y_pct": round(m["ret_1y_pct"], 2) if m["ret_1y_pct"] is not None else None,
                     "avg_volume20": int(m["avg_volume20"]), "full_year": m["full_year"],
                     "swing_trend": m["swing_trend"],
                     "swing_coc": round(m["swing_coc"], 2) if m["swing_coc"] is not None else None,
                     "swing_event": m["swing_event"], "swing_bars_since": m["swing_bars_since"],
                     "mansfield_rs": round(m["mansfield_rs"], 2) if m["mansfield_rs"] is not None else None,
                     "dq": round(m["dq"], 2) if m["dq"] is not None else None, "dq_date": m["dq_date"]})
    if screen == "near_52w_low":
        hits.sort(key=lambda h: (h["above_low_pct"], h["symbol"]))
    else:
        hits.sort(key=lambda h: (-h["from_high_pct"], h["symbol"]))   # closest to the 52-week high first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- commands

def _fetch_list(url, what):
    try:
        return http_get(url, timeout=60)
    except urllib.error.HTTPError as e:
        raise ScanError("%s answered HTTP %d" % (what, e.code))
    except (urllib.error.URLError, OSError) as e:
        raise ScanError("could not reach %s: %s" % (what, getattr(e, "reason", e)))


def cmd_universe(a):
    t0 = time.time()
    texts, note = {}, ""
    wanted = [EQUITY_FILE, NIFTY500_FILE] if a.source == "equity" else [NIFTY500_FILE]
    for fname in wanted:
        if a.offline:
            p = os.path.join(a.offline, fname)
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as fh:
                    texts[fname] = fh.read()
            continue
        if a.cache:
            if fname == NIFTY500_FILE:
                texts[fname], fb = fallback_list("--cache")
                note = (note + " " if note else "") + fb + "."
            continue
        url, what = (EQUITY_URL, "nseindia.com") if fname == EQUITY_FILE else (NIFTY500_URL, "niftyindices.com")
        try:
            texts[fname] = _fetch_list(url, what)
            if fname == EQUITY_FILE:
                parse_equity_l(texts[fname])
                break
            parse_nifty500(texts[fname])
        except ScanError as e:
            texts.pop(fname, None)
            if fname == EQUITY_FILE:
                note = "EQUITY_L.csv failed (%s): fell back to the Nifty 500 list." % e
                if a.pause:
                    SLEEP(a.pause)
                continue
            texts[fname], fb = fallback_list("%s failed (%s)" % (what, e))
            note = (note + " " if note else "") + fb + "."
    if not texts:
        raise ScanError(("no %s in %s" % (" or ".join(wanted), a.offline)) if a.offline else "no symbol list")
    if a.source == "equity" and EQUITY_FILE not in texts and not note:
        note = "no EQUITY_L.csv: fell back to the Nifty 500 list."
    kept, counts, used = build_universe(texts, a.source)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k not in ("keep", "rows")}
    label = "NSE EQUITY_L.csv (EQ series)" if used == "equity" else "Nifty 500 constituents"
    if a.cache and a.source == "equity" and not a.offline:
        note = note.replace("--cache", "--cache (EQUITY_L.csv is not cached)")
    if a.json:
        print(json.dumps({"count": len(kept), "rows": counts.get("rows", 0), "source": used, "note": note or None,
                          "dropped": dropped, "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2))
    else:
        if note:
            print(note)
        print("%s: %s rows." % (label, indian(counts.get("rows", 0))))
        print("Kept %s NSE equities." % indian(len(kept)))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % (indian(v), k)
                                          for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    source, cnote = "yahoo", ""
    if a.offline:
        pass
    elif a.cache:
        data, failed, lasts = load_daily_cache(symbols)
        source, cnote = "cache", cache_note("daily", len(data), lasts)
    else:
        progress(a, "Fetching daily closes for %s symbols (%d spark requests)..."
                 % (indian(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "6mo", "1d", a.pause, a)
        if failed:
            got, _, lasts = load_daily_cache(sorted(failed))
            data.update(got)
            for k in got:
                failed.pop(k, None)
            if got:
                source, cnote = ("cache" if len(got) == len(data) else "yahoo+cache"), cache_note(
                    "daily", len(got), lasts, True)
    if not data and not a.offline:
        why = next(iter(failed.values()), "no data") if failed else "no data"
        raise ScanError("no daily closes for any symbol (%s) and no trader/data/daily cache. Next: %s"
                        % (why, DATA_HINT))
    delivery = load_delivery(symbols) if a.cache else {}
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume, delivery)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed,
           "source": source, "filters": {"min_price": a.min_price, "min_volume": a.min_volume},
           "note": NOTE, "hits": hits}
    if cnote:
        doc["cache_note"] = cnote
    if delivery:
        doc["delivery_symbols"] = len(delivery)
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    regime = regime_note(DATA_DIR)
    if regime:
        doc["regime"] = regime
    flows_text = add_context(doc, DATA_DIR)
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    local = to_ist(now)
    print("Daily scan, %s IST, closes through %s (%s)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing",
        {"yahoo": "Yahoo spark, daily closes only", "cache": "trader-data cache, highs and lows",
         "yahoo+cache": "Yahoo spark + trader-data cache"}[source]))
    if regime:
        print(regime)
    if flows_text:
        print(flows_text)
    note_out(a, cnote)
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (₹%s+ and %s+ shares)%s; "
          "%s scanned." % (indian(counts["fetched"]), indian(len(symbols)),
                           (" (%s failed or had no data)" % indian(nfailed)) if nfailed else "",
                           indian(counts["fetched"] - counts["illiquid"]), _n(a.min_price),
                           indian(a.min_volume),
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           indian(counts["scanned"])))
    if calendar_note(local.date()):
        print(calendar_note(local.date()) + ".")
    if delivery:
        print("Delivery data (trader/data/delivery) for %s of the symbols: dq is today's delivery quantity over its "
              "30-session average." % indian(len(delivery)))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-10s %14s  RSI %5.1f  %s%s" % (h["symbol"], money(h["price"]), h["rsi"],
                                                ", ".join(LABEL[t] + (" (dq %.1f)" % h["dq"] if t == "delivery_spike"
                                                                      else "") for t in h["setups"]),
                                                ("  [level %s, coc %s]" % (_p(h["level"]), _p(h["coc"])))
                                                if h["level"] is not None and h["coc"] is not None
                                                and any(t.startswith("swing_") for t in h["setups"]) else ""))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL.")
    return 0 if hits else 1


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def intraday_source(a, symbols, day, data, failed):
    """The orb fetch path, shared with `patterns --intraday`: offline files, the trader-data cache, or Yahoo (spark for
    stage 1, a v8 chart per candidate for stage 2). -> (data, failed, confirm, source, cnote); confirm is None when
    the stage-1 series already carry real highs and lows (offline, cache)."""
    confirm = None
    source, cnote = ("offline" if a.offline else "yahoo"), ""
    today = to_ist(parse_now(None)).date()
    too_old = not a.offline and (today - day).days > 6
    if (a.cache or too_old) and not a.offline:
        data, failed, lasts = load_intraday_cache(symbols)
        source, cnote = "cache", cache_note("intraday", len(data), lasts)
        if not data:
            if too_old and not a.cache:
                raise ScanError("Yahoo's free 5-minute history is short: replay %s with --offline, or save it "
                                "first with `python3 trader-data/data.py intraday SYMBOL`" % day.isoformat())
            raise ScanError("no trader/data/intraday cache for these symbols. Next: run `python3 "
                            "trader-data/data.py intraday SYMBOL`, or %s" % DATA_HINT)
    elif not a.offline:
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s symbols (%d spark requests)..."
                 % (indian(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a)
        cached = set()
        if failed:
            got, _, lasts = load_intraday_cache(sorted(failed))
            data.update(got)
            cached.update(got)
            for k in got:
                failed.pop(k, None)
            if got:
                source, cnote = ("cache" if len(got) == len(data) else "yahoo+cache"), cache_note(
                    "intraday", len(got), lasts, True)
        if not data:
            why = next(iter(failed.values()), "no data") if failed else "no data"
            raise ScanError("no 5-minute bars for any symbol (%s) and no trader/data/intraday cache. Next: %s"
                            % (why, DATA_HINT))
        done = []

        def confirm(sym):
            if sym in cached:
                return data[sym]       # the cache is a v8 chart already: real highs and lows
            if a.pause:
                SLEEP(a.pause)
            done.append(sym)
            if len(done) % 50 == 0:
                progress(a, "  stage 2: %d v8 charts so far" % len(done))
            try:
                return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)
            except ScanError as e:
                got = load_intraday_cache([sym])[0]
                if sym in got:
                    return got[sym]
                raise ScanError("%s; no intraday cache either" % e)
    return data, failed, confirm, source, cnote


def load_offline_day(a, at):
    """--offline files for orb and `patterns --intraday` -> (data, failed, symbols in them, the replay day). Without
    --now the replay day is the last day in the files, and --at must say the time. Nothing offline: all None."""
    if not a.offline:
        return None, None, None, None
    data, failed = load_offline(a.offline)
    explicit_day = None
    if not a.now:
        days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
        if days:
            explicit_day = max(days)
            if at is None:
                raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    return data, failed, sorted(set(data) | set(failed)), explicit_day


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or DEFAULT_UNTIL, "--until")
    data, failed, offline_syms, explicit_day = load_offline_day(a, at)
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    data, failed, confirm, source, cnote = intraday_source(a, symbols, day, data, failed)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume, confirm,
                                     a.max_confirm)
    tfilter = None
    if a.trend_filter:
        daily_rows = {}
        for h in hits:
            path = daily_cache_path(h["symbol"], a.data_dir)
            try:
                daily_rows[h["symbol"]] = [r for r in read_daily_csv(path) if r[0] < day] if os.path.exists(path) else []
            except OSError:
                daily_rows[h["symbol"]] = []
        hits, dropped = trend_filter(hits, daily_rows)
        counts["hits"] = len(hits)
        tfilter = {"kept": len(hits), "dropped": dropped,
                   "unknown": sum(1 for h in hits if h["daily_trend"] == "unknown")}
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed,
           "unconfirmed": counts["unconfirmed"], "source": "yahoo" if source == "offline" else source,
           "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if cnote:
        doc["cache_note"] = cnote
    if tfilter:
        doc["trend_filter"] = tfilter
    regime = regime_note(a.data_dir or DATA_DIR)
    if regime:
        doc["regime"] = regime
    flows_text = add_context(doc, a.data_dir or DATA_DIR)
    po = preopen_for(day, a.data_dir or DATA_DIR)
    if po:
        add_preopen(hits, po)
        doc["preopen"] = {"date": po["date"].isoformat(), "advances": po["advances"],
                          "declines": po["declines"], "unchanged": po["unchanged"]}
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    note_out(a, cnote)
    print("Opening-range breakout scan, %s %s IST: %d-minute range (9:15 to %s), 5-minute bars, %s."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN)),
             {"cache": "trader-data cache", "yahoo+cache": "Yahoo + trader-data cache"}.get(source, "Yahoo")))
    if regime:
        print(regime)
    if flows_text:
        print(flows_text)
    if po and preopen_line(po):
        print(preopen_line(po))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % (indian(counts["fetched"]), indian(len(symbols)),
             (" (%s failed or had no data)" % indian(nfailed)) if nfailed else "",
             indian(counts["scanned"]),
             (" (%s under the price / volume floor)" % indian(counts["illiquid"])) if counts["illiquid"] else "",
             indian(counts["candidates"]), indian(counts["confirmed"])))
    if calendar_note(day):
        print(calendar_note(day) + ".")
    if tfilter:
        print("Daily trend filter: dropped %d whose daily swing trend is down%s; %d kept with the daily trend "
              "unknown." % (len(tfilter["dropped"]),
                            (" (" + ", ".join(tfilter["dropped"][:10]) + (", ..." if len(tfilter["dropped"]) > 10
                                                                          else "") + ")") if tfilter["dropped"] else "",
                            tfilter["unknown"]))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % (indian(counts["unconfirmed"]), indian(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % indian(counts["confirmed"])) if counts["unconfirmed"]
                 else indian(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %8s  %-10s %14s  range %s to %s  %+.2f%% over%s" % (
                h["at"], h["symbol"], money(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"],
                "  (%s)" % h["trend_note"] if h.get("trend_note") else ""))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.")
    return 0 if hits else 1


def _p(x):
    return "{:,.2f}".format(x)


def swing_text(h):
    """'up 1,234.50': the swing trend and its change-of-character level, or '-'."""
    if not h.get("swing_trend"):
        return "-"
    return "%s %s" % (h["swing_trend"], _p(h["swing_coc"]) if h.get("swing_coc") is not None else "-")


def cache_symbols(a, data_dir):
    """With no symbols, watchlist or universe file: every stock in the daily cache, else None."""
    daily_dir = os.path.join(data_dir, "daily")
    if not (a.symbols or a.watchlist or a.universe) and not os.path.exists(DEFAULT_UNIVERSE):
        if os.path.isdir(daily_dir):   # no universe yet: every stock in the cache
            return sorted({f[:-8] if f.endswith(".adj.csv") else f[:-4]
                           for f in os.listdir(daily_dir) if f.endswith(".csv")})
    return None


def read_cache_rows(symbols, data_dir):
    """-> ({SYM: daily rows}, {SYM: path or why}) from the trader-data daily cache."""
    rows, missing = {}, {}
    for sym in symbols:
        path = daily_cache_path(sym, data_dir)
        if not os.path.exists(path):
            missing[sym] = path
            continue
        try:
            rows[sym] = read_daily_csv(path)
        except OSError as e:
            missing[sym] = "%s (%s)" % (path, e)
    return rows, missing


def cmd_longterm(a):
    now = parse_now(a.now)
    data_dir = a.data_dir or DATA_DIR
    daily_dir = os.path.join(data_dir, "daily")
    symbols = read_symbols(a, cache_symbols(a, data_dir) or None)
    rows, missing = read_cache_rows(symbols, data_dir)
    if not rows:
        raise ScanError("no daily cache in %s for these symbols. Next: run `python3 trader-data/data.py daily "
                        "SYMBOL [SYMBOL ...]`, or %s" % (daily_dir, DATA_HINT))
    bench, bench_name, bench_note = read_benchmark(data_dir)
    delivery = load_delivery(sorted(rows), data_dir)
    hits, counts, last_date = scan_longterm(rows, symbols, a.min_price, a.min_volume, a.screen, a.near_high,
                                            a.near_low, bench, delivery)
    lasts = [r[-1][0] for r in rows.values() if r]
    cnote = cache_note("daily", len(rows), lasts)
    stale = last_date and (to_ist(now).date() - last_date).days > 5
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "longterm",
           "as_of": ("%s close" % last_date.isoformat()) if last_date else None, "universe": len(symbols),
           "scanned": counts["scanned"], "missing": len(missing), "source": "cache", "cache_note": cnote,
           "screen": a.screen or "any", "benchmark": bench_name,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume, "near_high_pct": a.near_high,
                       "near_low_pct": a.near_low},
           "note": NOTE + " Not financial advice.", "hits": hits}
    if bench_note:
        doc["benchmark_note"] = bench_note
    if delivery:
        doc["delivery_symbols"] = len(delivery)
    regime = regime_note(data_dir)
    if regime:
        doc["regime"] = regime
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2))
        return 0 if hits else 1
    print("Long-term screen, closes through %s (trader-data daily cache)." % (
        last_date.isoformat() if last_date else "nothing"))
    if regime:
        print(regime)
    note_out(a, cnote)
    note_out(a, bench_note)
    if stale:
        print("Note: the newest close is %s, more than 5 days old: refresh with `python3 trader-data/data.py "
              "daily SYMBOL`." % last_date.isoformat())
    print("%s of %s symbols cached%s; %s with %d+ sessions passed the liquidity filter (\u20b9%s+ and %s+ shares "
          "a day over 20 days); %s scanned." % (
              indian(counts["cached"]), indian(len(symbols)),
              (" (%s missing: `python3 trader-data/data.py daily SYMBOL`)" % indian(len(missing))) if missing else "",
              indian(counts["cached"] - counts["short"] - counts["illiquid"]), LT_BARS, _n(a.min_price),
              indian(a.min_volume), indian(counts["scanned"])))
    if counts["short"]:
        print("%s had under %d sessions in the cache (a 200-day average needs %d)." % (
            indian(counts["short"]), LT_BARS, LT_BARS))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in LT_SCREENS]
        print("%d matched: %s." % (len(hits), ", ".join("%d %s" % (n, LT_LABEL[k]) for k, n in per if n)))
        print("  %-10s %12s %12s %8s %12s %8s %12s %8s %6s %7s %-14s  %s" % (
            "SYMBOL", "CLOSE", "200-DMA", "VS 200", "52W HIGH", "OFF HI", "52W LOW", "OVER LO", "RSI", "RS",
            "SWING COCH", "SCREENS"))
        for h in hits[:a.show]:
            print("  %-10s %12s %12s %+7.1f%% %12s %+7.1f%% %12s %+7.1f%% %6s %7s %-14s  %s" % (
                h["symbol"], money(h["price"]), money(h["dma200"]), h["vs_200dma_pct"], money(h["high52"]),
                h["from_high_pct"], money(h["low52"]), h["above_low_pct"] or 0.0,
                ("%.1f" % h["rsi"]) if h["rsi"] is not None else "-",
                ("%+.1f" % h["mansfield_rs"]) if h["mansfield_rs"] is not None else "-", swing_text(h),
                ", ".join(LT_LABEL[t] for t in h["setups"])))
        if len(hits) > a.show:
            print("  ... and %d more in %s (--show N for more)." % (len(hits) - a.show, a.out))
    else:
        print("Nothing matched.")
    if not a.no_write:
        print("Wrote %s (mode longterm, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Not financial advice. Next: /trader-news SYMBOL, then read the company's filings.")
    return 0 if hits else 1


def cmd_patterns_intraday(a):
    """patterns --intraday: the double bottom and the flag on today's 5-minute bars, through orb's fetch path."""
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or DEFAULT_UNTIL, "--until")
    if a.pattern and a.pattern not in INTRA_PATTERNS:
        raise ScanError("--intraday reads %s only (not %s)" % (" and ".join(INTRA_PATTERNS), a.pattern))
    min_price = 50.0 if a.min_price is None else a.min_price
    min_volume = 0 if a.min_volume is None else a.min_volume
    state = None if a.state == "any" else a.state
    data, failed, offline_syms, explicit_day = load_offline_day(a, at)
    day, cutoff = intraday_window(now, at, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    data, failed, confirm, source, cnote = intraday_source(a, symbols, day, data, failed)
    hits, counts, failed2 = scan_intraday_patterns(data, symbols, day, cutoff, min_price, min_volume, confirm,
                                                   a.max_confirm, a.pattern, state)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "patterns-intraday", "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "unconfirmed": counts["unconfirmed"],
           "source": "yahoo" if source == "offline" else source, "interval": "%dm" % BAR_MIN,
           "pattern": a.pattern or "any", "state": a.state, "no_entries_after": until.strftime("%H:%M"),
           "filters": {"min_price": min_price, "min_volume": min_volume}, "note": NOTE + " Not financial advice.",
           "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if cnote:
        doc["cache_note"] = cnote
    regime = regime_note(a.data_dir or DATA_DIR)
    if regime:
        doc["regime"] = regime
    flows_text = add_context(doc, a.data_dir or DATA_DIR)
    po = preopen_for(day, a.data_dir or DATA_DIR)
    if po:
        add_preopen(hits, po)
        doc["preopen"] = {"date": po["date"].isoformat(), "advances": po["advances"],
                          "declines": po["declines"], "unchanged": po["unchanged"]}
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    note_out(a, cnote)
    print("Intraday pattern scan, %s %s IST: double bottom and flag on 5-minute bars, %s."
          % (day_label(day), clock_label(cutoff),
             {"cache": "trader-data cache", "yahoo+cache": "Yahoo + trader-data cache"}.get(source, "Yahoo")))
    if regime:
        print(regime)
    if flows_text:
        print(flows_text)
    if po and preopen_line(po):
        print(preopen_line(po))
    print("Fetched %s of %s charts%s; %s usable%s; %s read with real highs and lows."
          % (indian(counts["fetched"]), indian(len(symbols)),
             (" (%s failed or had no data)" % indian(nfailed)) if nfailed else "",
             indian(counts["scanned"]),
             (" (%s under the price / volume floor)" % indian(counts["illiquid"])) if counts["illiquid"] else "",
             indian(counts["confirmed"])))
    if calendar_note(day):
        print(calendar_note(day) + ".")
    if counts["unconfirmed"]:
        print("PARTIAL: %s more charts were not read (only the %s most traded are; --max-confirm 0 reads all)."
              % (indian(counts["unconfirmed"]), indian(a.max_confirm)))
    print("Hits are only before %s (no_entries_after); pivots need %d bars on each side, so a turn in the last %d "
          "bars is not visible yet." % (until.strftime("%H:%M"), INTRA["R"], INTRA["R"]))
    if hits:
        per = [(k, sum(1 for h in hits if h["pattern"] == "intraday_" + k)) for k in INTRA_PATTERNS]
        print("%d matched: %s." % (len(hits), ", ".join("%d %s" % (n, INTRA_LABEL[k]) for k, n in per if n)))
        print("  %-10s %-22s %-8s %9s %12s %12s %12s %6s %12s" % (
            "SYMBOL", "PATTERN", "STATE", "AT", "CLOSE", "LEVEL", "STOP", "RISK", "TARGET"))
        for h in hits[:a.show]:
            print("  %-10s %-22s %-8s %9s %12s %12s %12s %5.1f%% %12s" % (
                h["symbol"], h["pattern"], h["state"], h["at"], money(h["price"]), money(h["level"]),
                money(h["stop"]), h["risk_pct"], money(h["target"]) if h["target"] is not None else "-"))
        if len(hits) > a.show:
            print("  ... and %d more in %s (--show N for more)." % (len(hits) - a.show, a.out))
    else:
        print("Nothing matched.")
    if not a.no_write:
        print("Wrote %s (mode patterns-intraday, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Not financial advice. Next: /trader-news SYMBOL, then /trader-entry watchlist --setup level.")
    return 0 if hits else 1


def cmd_patterns(a):
    if a.intraday:
        return cmd_patterns_intraday(a)
    if a.min_price is None:
        a.min_price = 20.0
    if a.min_volume is None:
        a.min_volume = 100000
    now = parse_now(a.now)
    data_dir = a.data_dir or DATA_DIR
    daily_dir = os.path.join(data_dir, "daily")
    symbols = read_symbols(a, cache_symbols(a, data_dir) or None)
    rows, missing = read_cache_rows(symbols, data_dir)
    if not rows:
        raise ScanError("no daily cache in %s for these symbols. Next: run `python3 trader-data/data.py daily "
                        "SYMBOL [SYMBOL ...]`, or %s" % (daily_dir, DATA_HINT))
    only = [a.pattern] if a.pattern else None
    hits, counts, last_date = scan_patterns(rows, symbols, a.min_price, a.min_volume, only,
                                            None if a.state == "any" else a.state)
    previous = None
    if os.path.exists(a.out):
        try:
            with open(a.out, encoding="utf-8") as fh:
                previous = json.load(fh)
        except (OSError, ValueError):
            previous = None
    carry_first_seen(hits, previous)
    lasts = [r[-1][0] for r in rows.values() if r]
    cnote = cache_note("daily", len(rows), lasts)
    stale = last_date and (to_ist(now).date() - last_date).days > 5
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "patterns",
           "as_of": ("%s close" % last_date.isoformat()) if last_date else None, "universe": len(symbols),
           "scanned": counts["scanned"], "missing": len(missing), "source": "cache", "cache_note": cnote,
           "pattern": a.pattern or "any", "state": a.state,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume},
           "note": NOTE + " Not financial advice.", "hits": hits}
    regime = regime_note(data_dir)
    if regime:
        doc["regime"] = regime
    flows_text = add_context(doc, data_dir)
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2))
        return 0 if hits else 1
    print("Pattern scan, closes through %s (trader-data daily cache)." % (
        last_date.isoformat() if last_date else "nothing"))
    if regime:
        print(regime)
    if flows_text:
        print(flows_text)
    note_out(a, cnote)
    if stale:
        print("Note: the newest close is %s, more than 5 days old: refresh with `python3 trader-data/data.py "
              "daily SYMBOL`." % last_date.isoformat())
    print("%s of %s symbols cached%s; %s with %d+ sessions and over the liquidity filter (\u20b9%s+ and %s+ shares a "
          "day over 20 days); %s scanned." % (
              indian(counts["cached"]), indian(len(symbols)),
              (" (%s missing: `python3 trader-data/data.py daily SYMBOL`)" % indian(len(missing))) if missing else "",
              indian(counts["scanned"] + counts["illiquid"]), PAT["min_bars"], _n(a.min_price),
              indian(a.min_volume), indian(counts["scanned"])))
    if counts["short"]:
        print("%s had under %d sessions in the cache." % (indian(counts["short"]), PAT["min_bars"]))
    print("Pivots need %d bars on each side, so a turn in the last %d sessions is not visible yet. The VCP needs 200 "
          "sessions." % (PAT["L"], PAT["R"]))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in PATTERNS]
        print("%d matched: %s." % (len(hits), ", ".join("%d %s" % (n, PAT_LABEL[k]) for k, n in per if n)))
        print("  %-10s %-24s %-8s %12s %12s %12s %6s %12s  %s" % (
            "SYMBOL", "PATTERN", "STATE", "CLOSE", "LEVEL", "STOP", "RISK", "TARGET", "SEEN"))
        for h in hits[:a.show]:
            print("  %-10s %-24s %-8s %12s %12s %12s %5.1f%% %12s  %s%s" % (
                h["symbol"], h["pattern"], h["state"], money(h["price"]), money(h["level"]), money(h["stop"]),
                h["risk_pct"], money(h["target"]) if h["target"] is not None else "-", h["first_seen"],
                "  (new)" if h["first_seen"] == h["close_date"] else ""))
        if len(hits) > a.show:
            print("  ... and %d more in %s (--show N for more)." % (len(hits) - a.show, a.out))
    else:
        print("Nothing matched.")
    if not a.no_write:
        print("Wrote %s (mode patterns, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Not financial advice. Next: /trader-news SYMBOL, then /trader-entry SYMBOL for the level.")
    return 0 if hits else 1


def cmd_breadth(a):
    now = parse_now(a.now)
    data_dir = a.data_dir or DATA_DIR
    daily_dir = os.path.join(data_dir, "daily")
    symbols = read_symbols(a, cache_symbols(a, data_dir) or None)
    rows, missing, unadjusted = {}, {}, []
    for sym in symbols:
        adj = os.path.join(daily_dir, "%s.adj.csv" % sym)
        if not os.path.exists(adj) and not a.unadjusted:
            if os.path.exists(daily_cache_path(sym, data_dir)):
                unadjusted.append(sym)
            else:
                missing[sym] = adj
            continue
        path = daily_cache_path(sym, data_dir)
        if not os.path.exists(path):
            missing[sym] = path
            continue
        try:
            got = read_daily_csv(path)
        except OSError as e:
            missing[sym] = "%s (%s)" % (path, e)
            continue
        if got:
            rows[sym] = got
    if not rows:
        raise ScanError("no adjusted daily cache (<SYMBOL>.adj.csv) in %s for these symbols%s. Next: run `python3 "
                        "trader-data/data.py daily SYMBOL [SYMBOL ...]`, or %s" % (
                            daily_dir, (" (%s have only an unadjusted .csv: --unadjusted uses them, but a split "
                                        "then fakes a new low)" % indian(len(unadjusted))) if unadjusted else "",
                            DATA_HINT))
    series = breadth_series(rows, days=BREADTH_DAYS if a.days is None else a.days)
    out = a.out or os.path.join(data_dir, "breadth.csv")
    if not a.no_write:
        write_breadth(out, series)
    last = series[-1] if series else None
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "breadth",
           "symbols": len(rows), "skipped_unadjusted": len(unadjusted), "missing": len(missing),
           "rows": len(series), "out": None if a.no_write else out, "last": last, "caveat": BREADTH_CAVEAT,
           "unadjusted_included": bool(a.unadjusted)}
    if a.json:
        print(json.dumps(doc, indent=2))
        return 0 if series else 1
    print("Market breadth from %s symbols in the trader-data daily cache%s." % (
        indian(len(rows)), "" if a.unadjusted else " (adjusted .adj.csv files only)"))
    if unadjusted:
        print("Skipped %s with only an unadjusted .csv (a split would fake a new low): --unadjusted uses them."
              % indian(len(unadjusted)))
    if missing:
        print("%s not in the cache: `python3 trader-data/data.py daily SYMBOL`." % indian(len(missing)))
    if last:
        print("%d dates, %s to %s. Latest %s:" % (len(series), series[0]["date"], last["date"], last["date"]))
        print("  above the 50-day average  %s" % (("%.1f%%" % last["pct_50"]) if last["pct_50"] is not None else "-"))
        print("  above the 200-day average %s" % (("%.1f%%" % last["pct_200"]) if last["pct_200"] is not None else
                                                  "- (under 200 sessions of history)"))
        print("  net new highs (running)   %+d" % last["net_new_highs"])
        print("  A/D line (ratio, running) %+.2f" % last["ad_line"])
        print("  McClellan oscillator      %s" % (("%+.2f" % last["mcclellan_osc"]) if last["mcclellan_osc"] is not None
                                                  else "- (needs %d dates)" % BREADTH_SLOW))
    else:
        print("No dates with a previous close to compare.")
    print("Caveat: %s." % BREADTH_CAVEAT)
    if not a.no_write:
        print("Wrote %s (%d rows). The other scans print a one-line regime note from it." % (out, len(series)))
    return 0 if series else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): NSE daily setups and opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="download the Nifty 500 (or NSE EQUITY_L) list -> trader/universe.txt")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--offline", metavar="DIR", help="a folder with ind_nifty500list.csv (and EQUITY_L.csv)")
    p.add_argument("--source", choices=["nifty500", "equity"], default="nifty500",
                   help="nifty500 (default) or equity (every NSE EQ-series stock, EQUITY_L.csv)")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--cache", action="store_true",
                   help="no download: trader/data/universe.csv, else the bundled Nifty 500 snapshot")
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_volume):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe (RELIANCE,TCS)")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of NSE symbols")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price)
        p.add_argument("--min-volume", type=int, default=min_volume)
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-19 10:00" IST, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")
        p.add_argument("--cache", action="store_true",
                       help="read the trader-data cache (trader/data/daily or intraday) instead of Yahoo")

    p = sub.add_parser("daily", help="pre-market setups from daily closes")
    common(p, 50.0, 500000)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, 50.0, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this IST time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM IST (default: config no_entries_after, %s)" % DEFAULT_UNTIL)
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--trend-filter", action="store_true",
                   help="drop names whose daily swing trend (trader-data daily cache) is down; no cache = kept, flagged")
    p.add_argument("--data-dir", help="the trader-data folder for --trend-filter (default %s)" % DATA_DIR)
    p = sub.add_parser("longterm", help="long-term screen of the trader-data daily cache (no network)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
    g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of NSE symbols")
    p.add_argument("--universe", help="symbol list (default %s; without it, every stock in the cache)"
                   % DEFAULT_UNIVERSE)
    p.add_argument("--limit", type=int)
    p.add_argument("--screen", choices=[k for k, _ in LT_SCREENS], help="keep only this screen (default: any)")
    p.add_argument("--near-high", type=float, default=5.0, help="percent under the 52-week high (5)")
    p.add_argument("--near-low", type=float, default=10.0, help="percent over the 52-week low (10)")
    p.add_argument("--min-price", type=float, default=20.0)
    p.add_argument("--min-volume", type=int, default=100000, help="20-day average volume (1,00,000)")
    p.add_argument("--data-dir", help="the trader-data folder (default %s)" % DATA_DIR)
    p.add_argument("--now", help='pretend it is this time ("2026-10-29 08:30" IST)')
    p.add_argument("--out", default="trader/longterm.json")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--show", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("patterns", help="chart patterns (VCP, double bottom, flag, ...) in the trader-data daily cache")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
    g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of NSE symbols")
    p.add_argument("--universe", help="symbol list (default %s; without it, every stock in the cache)"
                   % DEFAULT_UNIVERSE)
    p.add_argument("--limit", type=int)
    p.add_argument("--pattern", choices=[k for k, _ in PATTERNS], help="look for this pattern only (default: all)")
    p.add_argument("--state", choices=["coiled", "breakout", "any"], default="any",
                   help="coiled (formed, not broken out) or breakout (first close over the trigger); default any")
    p.add_argument("--min-price", type=float, help="last close (20; 50 with --intraday)")
    p.add_argument("--min-volume", type=int,
                   help="20-day average volume (1,00,000); with --intraday the volume so far (0)")
    p.add_argument("--data-dir", help="the trader-data folder (default %s)" % DATA_DIR)
    p.add_argument("--now", help='pretend it is this time ("2026-10-29 08:30" IST)')
    p.add_argument("--out", default="trader/watchlist.json")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--show", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.add_argument("--intraday", action="store_true",
                   help="intraday double bottom and flag on today's 5-minute bars (orb's fetch path; needs the "
                        "session open, or --offline with --at / --now)")
    p.add_argument("--offline", metavar="PATH", help="--intraday: fixture folder or file instead of the network")
    p.add_argument("--at", help="--intraday: cut the clock at this IST time, HH:MM")
    p.add_argument("--until", help="--intraday: no hits after HH:MM IST (default: config no_entries_after, %s)"
                   % DEFAULT_UNTIL)
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--cache", action="store_true", help="--intraday: read trader/data/intraday instead of Yahoo")
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="--intraday: v8 charts to fetch, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--pause", type=float, default=PAUSE, help="--intraday: seconds between requests (%.1f)" % PAUSE)
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("breadth", help="market breadth from the trader-data daily cache -> trader/data/breadth.csv")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
    g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of NSE symbols")
    p.add_argument("--universe", help="symbol list (default %s; without it, every stock in the cache)"
                   % DEFAULT_UNIVERSE)
    p.add_argument("--limit", type=int)
    p.add_argument("--unadjusted", action="store_true",
                   help="also use symbols that have only an unadjusted <SYMBOL>.csv (a split fakes a new low)")
    p.add_argument("--days", type=int, help="dates to write (%d; 0 = every date in the cache)" % BREADTH_DAYS)
    p.add_argument("--data-dir", help="the trader-data folder (default %s)" % DATA_DIR)
    p.add_argument("--now", help='pretend it is this time ("2026-10-29 08:30" IST)')
    p.add_argument("--out", help="default <data-dir>/breadth.csv")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb, "longterm": cmd_longterm,
                "patterns": cmd_patterns, "breadth": cmd_breadth}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
