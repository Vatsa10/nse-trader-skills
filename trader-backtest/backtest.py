#!/usr/bin/env python3
"""trader-backtest: multi-year daily backtester for long-term and swing strategies on NSE.

Reads the daily bars cache written by trader-data (trader/data/daily/<SYMBOL>.csv, or the split/bonus
adjusted <SYMBOL>.adj.csv when present) and replays one long-only CNC (delivery) strategy bar by bar:

  buy_hold      buy once with all the cash, hold to the end
  sip           invest a fixed amount on the first trading day of every month (whole shares, leftover carried)
  sma_cross     long while the fast SMA is above the slow SMA, flat otherwise
  breakout_52w  buy on a close at a new 52-week high, exit on a close below the N-day low or a trailing stop
  swing_trend   buy on a market-structure break (or trend turn up), exit when the swing trend turns down
  dip_buy       buy when the close is X % below the 200-day average, exit back above it (or hold)
  rebalance     several symbols at target weights, rebalanced monthly, quarterly or on a drift threshold

Realism: fills at the next day's open by default (--fill close to fill at the signal day's close),
slippage in basis points, whole shares only, Zerodha delivery charges from the shared charges model
(trader/charges.json deep-merged over the defaults, DP charge once per scrip per sell day, calibration
factor), optional Indian capital-gains tax (STCG 20 %, LTCG 12.5 % above Rs 1.25 lakh a financial year,
FIFO lots).

Commands:
  run         run one strategy (optionally --split 0.7 for an out-of-sample check, --sweep for a grid)
  check       data health for symbols: rows, range, adjusted or not, likely split/bonus dates
  strategies  list the strategy types, their parameters, and named strategies in a config file

Standard library only. Python 3.8+. Times are IST (UTC+05:30). Education and tooling, not financial advice.
"""

import argparse
import copy
import csv
import datetime
import html
import itertools
import json
import math
import os
import sys

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
TRADING_DAYS = 252
DEFAULT_RF = 6.5
DEFAULT_CAPITAL = 100000.0
DEFAULT_SIP = 10000.0
STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_EXEMPTION = 125000.0
LT_DAYS = 365

# Shared charges model (PORT_V4_SPEC contract 4), verified 2026-10-29 from zerodha.com/charges.
CHARGES_DEFAULT = {
    "broker": "zerodha", "verified": "2026-10-29 zerodha.com/charges",
    "intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "stamp_buy_pct": 0.003},
    "delivery": {"brokerage_pct": 0, "brokerage_cap": 0, "stt_buy_pct": 0.1, "stt_sell_pct": 0.1,
                 "stamp_buy_pct": 0.015, "dp_per_scrip_sell": 15.34},
    "exchange_txn_pct": {"NSE": 0.00307}, "sebi_per_crore": 10, "gst_pct": 18,
    "calibration": {"factor": 1.0, "samples": 0, "updated": None},
}

STRATEGY_TYPES = {
    "buy_hold": {"params": {}, "multi": False,
                 "about": "buy with all the cash on the first day, hold to the end"},
    "sip": {"params": {"amount": DEFAULT_SIP}, "multi": False,
            "about": "invest `amount` on the first trading day of each month, whole shares, leftover cash carried"},
    "sma_cross": {"params": {"fast": 50, "slow": 200}, "multi": False,
                  "about": "long while SMA(fast) > SMA(slow) at the close, flat when it is below"},
    "breakout_52w": {"params": {"lookback": 252, "exit_low": 50, "trail_pct": 0}, "multi": False,
                     "about": "buy on a close above the highest close of the last `lookback` days; exit on a close "
                              "below the lowest low of the last `exit_low` days, or `trail_pct` % under the highest "
                              "close since entry (0 = off)"},
    "swing_trend": {"params": {"retrace_pct": 5, "entry": "bos", "warmup": 40}, "multi": False,
                    "about": "market structure on closes: buy on a break of structure (entry=bos) or when the trend "
                             "turns up (entry=reversal), only after `warmup` bars and ignoring pullbacks shallower "
                             "than `retrace_pct` % (null = all); sell when a close breaks the change-of-character "
                             "level and the trend turns down"},
    "dip_buy": {"params": {"ma": 200, "dip_pct": 10, "exit_above_pct": 5}, "multi": False,
                "about": "buy when the close is `dip_pct` % or more below SMA(ma); sell when the close is "
                         "`exit_above_pct` % above it (null = hold, never sell)"},
    "rebalance": {"params": {"weights": {"NIFTYBEES": 0.6, "GOLDBEES": 0.2, "ITBEES": 0.2},
                             "freq": "quarterly", "band": 5}, "multi": True,
                  "about": "hold `weights` (fractions, the rest stays cash); rebalance `freq` = monthly, quarterly "
                           "or threshold (when any weight drifts more than `band` percentage points)"},
}

SWEEP_METRICS = ("sharpe", "cagr", "sortino", "total_return", "xirr", "calmar")


class BTError(Exception):
    pass


def shown(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------- small helpers

def inr(x):
    """Rupees with Indian digit grouping: 12,34,567.89."""
    neg = x < 0
    whole, frac = ("%.2f" % abs(x)).split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return "%s₹%s.%s" % ("-" if neg else "", whole, frac)


def pct(x, digits=2):
    return "n/a" if x is None else ("%+.*f %%" % (digits, x * 100))


def num(x, digits=2):
    return "n/a" if x is None else ("%.*f" % (digits, x))


def parse_date(s):
    try:
        return datetime.datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise BTError("bad date %r (want YYYY-MM-DD)" % s)


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def fy_of(d):
    """Indian financial year (April to March) as the year it starts in."""
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start_year):
    return "FY%02d" % ((start_year + 1) % 100)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def median(xs):
    s = sorted(xs)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def parse_value(v):
    t = str(v).strip()
    if t.lower() in ("none", "null", "off", ""):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


# ---------------------------------------------------------------- charges

def load_charges(root):
    path = os.path.join(root, "trader", "charges.json")
    ch = CHARGES_DEFAULT
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                ch = deep_merge(CHARGES_DEFAULT, json.load(fh))
        except (OSError, ValueError) as e:
            raise BTError("trader/charges.json is not valid JSON: %s" % e)
    return copy.deepcopy(ch)


def charges_label(ch):
    n = int((ch.get("calibration") or {}).get("samples") or 0)
    return "estimate (calibrated from %d contract notes)" % n if n > 0 else "estimate"


def order_charges(side, value, ch, dp=False):
    """Delivery (CNC) charges for one order of `value` rupees, before the calibration factor.
    GST 18 % on brokerage + exchange txn + SEBI. DP (GST included) when dp=True (first CNC sell of that
    scrip that day). Returns a dict of parts and 'total' (calibration factor applied)."""
    d = ch["delivery"]
    brok = value * float(d.get("brokerage_pct") or 0) / 100.0
    cap = float(d.get("brokerage_cap") or 0)
    if cap > 0:
        brok = min(brok, cap)
    stt = value * float(d.get("stt_buy_pct" if side == "buy" else "stt_sell_pct") or 0) / 100.0
    stamp = value * float(d.get("stamp_buy_pct") or 0) / 100.0 if side == "buy" else 0.0
    txn = value * float((ch.get("exchange_txn_pct") or {}).get("NSE") or 0) / 100.0
    sebi = value * float(ch.get("sebi_per_crore") or 0) / 1e7
    gst = float(ch.get("gst_pct") or 0) / 100.0 * (brok + txn + sebi)
    dpc = float(d.get("dp_per_scrip_sell") or 0) if (dp and side == "sell") else 0.0
    raw = brok + stt + stamp + txn + sebi + gst + dpc
    factor = float((ch.get("calibration") or {}).get("factor") or 1.0)
    return {"brokerage": brok, "stt": stt, "stamp": stamp, "exchange": txn, "sebi": sebi, "gst": gst,
            "dp": dpc, "raw": raw, "factor": factor, "total": raw * factor}


def buy_rate(ch):
    """Charges per rupee bought (no fixed parts on a delivery buy), calibration included."""
    return order_charges("buy", 1e6, ch)["total"] / 1e6


# ---------------------------------------------------------------- data

def read_csv_bars(path):
    bars = []
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        need = {"date", "open", "high", "low", "close"}
        if not rd.fieldnames or not need.issubset(set(h.strip().lower() for h in rd.fieldnames)):
            raise BTError("%s: header must be date,open,high,low,close,volume" % shown(path))
        for row in rd:
            row = {k.strip().lower(): v for k, v in row.items() if k}
            try:
                c = float(row["close"])
                o = float(row.get("open") or c)
                h = float(row.get("high") or max(o, c))
                lo = float(row.get("low") or min(o, c))
                v = float(row.get("volume") or 0)
            except (TypeError, ValueError):
                continue
            if c <= 0 or o <= 0:
                continue
            bars.append({"date": parse_date(row["date"]), "open": o, "high": h, "low": lo, "close": c, "volume": v})
    bars.sort(key=lambda b: b["date"])
    out = []
    for b in bars:  # drop duplicate dates, keep the last
        if out and out[-1]["date"] == b["date"]:
            out[-1] = b
        else:
            out.append(b)
    return out


# split / bonus ratios a one-day drop could come from: (ratio new/old, label)
CA_RATIOS = [(1 / 2.0, "1:1 bonus or 1:2 split"), (2 / 3.0, "1:2 bonus"), (1 / 3.0, "2:1 bonus"),
             (1 / 4.0, "3:1 bonus or 1:4 split"), (1 / 5.0, "1:5 split or 4:1 bonus"),
             (1 / 10.0, "1:10 split"), (3 / 4.0, "1:3 bonus"), (2 / 5.0, "2:5 split")]


def detect_corporate_actions(bars, tol=0.025):
    """Flag days where the open (or close) gaps down to about 1/2, 2/3, 1/3, 1/4, 1/5, 1/10, 3/4 or 2/5 of
    the previous close: the fingerprint of a split or bonus in an unadjusted series. A real crash can match
    too, so these are flags to check on nseindia.com (corporate actions), not facts."""
    flags = []
    for i in range(1, len(bars)):
        prev = bars[i - 1]["close"]
        for kind in ("open", "close"):
            r = bars[i][kind] / prev
            if r > 0.8:
                continue
            best = min(CA_RATIOS, key=lambda t: abs(r - t[0]) / t[0])
            if abs(r - best[0]) / best[0] <= tol:
                flags.append({"date": bars[i]["date"].isoformat(), "ratio": round(r, 4),
                              "expected": round(best[0], 4), "likely": best[1],
                              "gap_pct": round((r - 1) * 100, 1)})
                break
    return flags


def apply_adjustments(bars, flags):
    """Back-adjust prices before each flagged date by the expected ratio (volumes divided by it)."""
    out = [dict(b) for b in bars]
    for f in flags:
        d = parse_date(f["date"])
        r = f["expected"]
        for b in out:
            if b["date"] < d:
                for k in ("open", "high", "low", "close"):
                    b[k] *= r
                b["volume"] /= r
    return out


def load_symbol(data_dir, sym, auto_adjust=False):
    """-> (bars, info). Prefers <SYM>.adj.csv. Flags likely corporate actions in an unadjusted file."""
    sym = sym.strip().upper()
    adj = os.path.join(data_dir, sym + ".adj.csv")
    raw = os.path.join(data_dir, sym + ".csv")
    if os.path.isfile(adj):
        path, adjusted = adj, True
    elif os.path.isfile(raw):
        path, adjusted = raw, False
    else:
        raise BTError("no daily bars for %s in %s. Fetch them first with trader-data (daily cache "
                      "trader/data/daily/%s.csv)." % (sym, shown(data_dir), sym))
    bars = read_csv_bars(path)
    if len(bars) < 2:
        raise BTError("%s has fewer than 2 usable rows" % shown(path))
    info = {"symbol": sym, "file": shown(path), "adjusted": adjusted, "rows": len(bars),
            "first": bars[0]["date"].isoformat(), "last": bars[-1]["date"].isoformat(),
            "corporate_action_flags": [], "auto_adjusted": False}
    if not adjusted:
        flags = detect_corporate_actions(bars)
        info["corporate_action_flags"] = flags
        if flags and auto_adjust:
            bars = apply_adjustments(bars, flags)
            info["auto_adjusted"] = True
    return bars, info


def align(series):
    """{sym: bars} -> (dates, {sym: {"open":[...], ...}}) on the dates every symbol traded."""
    common = None
    for bars in series.values():
        ds = set(b["date"] for b in bars)
        common = ds if common is None else common & ds
    dates = sorted(common or [])
    out = {}
    for sym, bars in series.items():
        by = {b["date"]: b for b in bars}
        out[sym] = {k: [by[d][k] for d in dates] for k in ("open", "high", "low", "close", "volume")}
    return dates, out


# ---------------------------------------------------------------- indicators

def sma(xs, n):
    n = int(n)
    out = [None] * len(xs)
    if n <= 0:
        return out
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


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


# ---------------------------------------------------------------- strategies

class Strategy(object):
    """schedule(i) orders fill on day i (calendar actions, no look-ahead). signal(i) orders are decided at the
    close of day i and fill at the next open (or at the close of day i with --fill close)."""

    def __init__(self, params, syms, data, dates):
        self.p = params
        self.syms = syms
        self.data = data
        self.dates = dates
        self.sym = syms[0]
        self.c = data[self.sym]["close"]
        self.start = 0
        self.setup()

    def setup(self):
        pass

    def schedule(self, i, port):
        return []

    def signal(self, i, port):
        return []


class BuyHold(Strategy):
    def schedule(self, i, port):
        return [{"op": "buy_all", "sym": self.sym, "why": "buy and hold"}] if i == self.start else []


class SIP(Strategy):
    def schedule(self, i, port):
        if i == self.start or self.dates[i].month != self.dates[i - 1].month:
            amt = float(self.p.get("amount") or DEFAULT_SIP)
            return [{"op": "deposit", "amount": amt},
                    {"op": "buy_all", "sym": self.sym, "why": "monthly SIP"}]
        return []


class SMACross(Strategy):
    def setup(self):
        f, s = int(self.p["fast"]), int(self.p["slow"])
        if f <= 0 or s <= 0 or f >= s:
            raise BTError("sma_cross wants 0 < fast < slow (got fast=%s slow=%s)" % (f, s))
        self.f, self.s = sma(self.c, f), sma(self.c, s)

    def signal(self, i, port):
        f, s = self.f[i], self.s[i]
        if f is None or s is None:
            return []
        held = port.qty(self.sym) > 0
        if f > s and not held:
            return [{"op": "buy_all", "sym": self.sym, "why": "SMA%s above SMA%s" % (self.p["fast"], self.p["slow"])}]
        if f < s and held:
            return [{"op": "sell_all", "sym": self.sym, "why": "SMA%s below SMA%s" % (self.p["fast"], self.p["slow"])}]
        return []


class Breakout52w(Strategy):
    def setup(self):
        self.L = int(self.p.get("lookback") or 252)
        self.N = int(self.p.get("exit_low") or 0)
        self.trail = float(self.p.get("trail_pct") or 0)
        self.lo = self.data[self.sym]["low"]

    def signal(self, i, port):
        c = self.c
        if port.qty(self.sym) <= 0:
            if i >= self.L and c[i] > max(c[i - self.L:i]):
                return [{"op": "buy_all", "sym": self.sym, "why": "close at a new %d-day high" % self.L}]
            return []
        if self.N > 0 and i >= self.N and c[i] < min(self.lo[i - self.N:i]):
            return [{"op": "sell_all", "sym": self.sym, "why": "close below the %d-day low" % self.N}]
        if self.trail > 0:
            e = port.entry_index.get(self.sym, i)
            peak = max(c[e:i + 1])
            if c[i] < peak * (1 - self.trail / 100.0):
                return [{"op": "sell_all", "sym": self.sym, "why": "trailing stop %.1f %% under %.2f" % (self.trail, peak)}]
        return []


class SwingTrend(Strategy):
    """Market structure from the swing engine, run once over the whole series. The engine is causal, so the
    event on bar i only knows bars up to i. Events before `warmup` bars have been fed are ignored (the first
    change-of-character level comes from an arbitrary opening range)."""

    def setup(self):
        rt = self.p.get("retrace_pct")
        rt = 0.0 if rt is None else float(rt)
        self.entry = str(self.p.get("entry") or "bos").lower()
        self.warmup = int(self.p.get("warmup") or 0)
        if rt < 0:
            raise BTError("swing_trend retrace_pct must be 0 or more (got %s)" % rt)
        if self.entry not in ("bos", "reversal"):
            raise BTError("swing_trend entry must be bos or reversal (got %r)" % self.entry)
        if self.warmup < 0:
            raise BTError("swing_trend warmup must be 0 or more (got %s)" % self.warmup)
        d = self.data[self.sym]
        s = swing_new(rt, min_bars=self.warmup)
        self.ev = []
        for h, lo, c in zip(d["high"], d["low"], d["close"]):
            old = s["coc"]
            e = swing_step(s, h, lo, c)
            self.ev.append((e, s["trend"], old if e == "reversal" else s["last_bos"], s["total"] > self.warmup))

    def signal(self, i, port):
        e, trend, level, stable = self.ev[i]
        if e is None:
            return []
        if port.qty(self.sym) <= 0:
            if not stable or trend != "up":
                return []
            if self.entry == "bos" and e == "bos":
                return [{"op": "buy_all", "sym": self.sym, "why": "swing break of structure above %.2f" % level}]
            if self.entry == "reversal" and e in ("reversal", "start_up"):
                return [{"op": "buy_all", "sym": self.sym,
                         "why": "swing trend turned up" + (" through %.2f" % level if e == "reversal" else "")}]
            return []
        if e == "reversal" and trend == "down":
            return [{"op": "sell_all", "sym": self.sym, "why": "swing trend turned down (close below %.2f)" % level}]
        return []


class DipBuy(Strategy):
    def setup(self):
        self.ma = sma(self.c, int(self.p.get("ma") or 200))
        self.dip = float(self.p.get("dip_pct") or 0)
        ex = self.p.get("exit_above_pct")
        self.exit = None if ex is None else float(ex)

    def signal(self, i, port):
        m = self.ma[i]
        if m is None:
            return []
        if port.qty(self.sym) <= 0:
            if self.c[i] <= m * (1 - self.dip / 100.0):
                return [{"op": "buy_all", "sym": self.sym, "why": "%.1f %% below the %s-DMA" % ((1 - self.c[i] / m) * 100, self.p.get("ma"))}]
        elif self.exit is not None and self.c[i] >= m * (1 + self.exit / 100.0):
            return [{"op": "sell_all", "sym": self.sym, "why": "back %.1f %% above the %s-DMA" % (self.exit, self.p.get("ma"))}]
        return []


class Rebalance(Strategy):
    def setup(self):
        w = self.p.get("weights") or {}
        if not w:
            raise BTError("rebalance needs weights, e.g. --weights NIFTYBEES=0.6,GOLDBEES=0.2,ITBEES=0.2")
        self.w = {k.upper(): float(v) for k, v in w.items()}
        tot = sum(self.w.values())
        if any(v < 0 for v in self.w.values()) or tot > 1.0 + 1e-9:
            raise BTError("rebalance weights must be >= 0 and add up to at most 1 (got %.4f)" % tot)
        self.freq = str(self.p.get("freq") or "quarterly").lower()
        if self.freq not in ("monthly", "quarterly", "threshold"):
            raise BTError("rebalance freq must be monthly, quarterly or threshold")
        self.band = float(self.p.get("band") or 5) / 100.0

    def schedule(self, i, port):
        if i == self.start:
            return [{"op": "target", "weights": self.w, "why": "initial allocation"}]
        if self.freq == "threshold":
            return []
        newm = self.dates[i].month != self.dates[i - 1].month
        if newm and (self.freq == "monthly" or self.dates[i].month in (1, 4, 7, 10)):
            return [{"op": "target", "weights": self.w, "why": "%s rebalance" % self.freq}]
        return []

    def signal(self, i, port):
        if self.freq != "threshold":
            return []
        eq = port.equity(i, "close")
        if eq <= 0:
            return []
        for s, tw in self.w.items():
            cw = port.qty(s) * self.data[s]["close"][i] / eq
            if abs(cw - tw) > self.band:
                return [{"op": "target", "weights": self.w, "why": "%s drifted to %.1f %% (target %.1f %%)" % (s, cw * 100, tw * 100)}]
        return []


STRATEGIES = {"buy_hold": BuyHold, "sip": SIP, "sma_cross": SMACross, "breakout_52w": Breakout52w,
              "swing_trend": SwingTrend, "dip_buy": DipBuy, "rebalance": Rebalance}


# ---------------------------------------------------------------- tax

def realized_tax(st_gain, lt_gain, exemption=LTCG_EXEMPTION):
    """Tax for one financial year of realized listed-equity gains (STT paid).
    STCG 20 %; LTCG 12.5 % above the exemption. A short-term net loss is set off against long-term gains;
    a long-term net loss cannot reduce short-term gains (carry-forward is not modelled)."""
    st, lt = float(st_gain), float(lt_gain)
    if st < 0:
        lt += st
        st = 0.0
    lt_taxable = max(0.0, lt - exemption)
    st_tax = STCG_RATE * st
    lt_tax = LTCG_RATE * lt_taxable
    return {"stcg": st, "ltcg": max(lt, 0.0), "ltcg_taxable": lt_taxable, "stcg_tax": st_tax,
            "ltcg_tax": lt_tax, "tax": st_tax + lt_tax}


# ---------------------------------------------------------------- portfolio + engine

class Portfolio(object):
    def __init__(self, cash, data, dates, ch, slippage_bps=0.0):
        self.cash = float(cash)
        self.data = data
        self.dates = dates
        self.ch = ch
        self.slip = float(slippage_bps) / 1e4
        self.lots = {}          # sym -> [{"qty","px","date","chg_ps"}] FIFO
        self.entry_index = {}   # sym -> index of the buy that opened the current position
        self.dp_days = set()    # (sym, date) already charged DP
        self.fills = []
        self.realized = []      # matched lots for tax
        self.closed = []        # per sell order: net P&L
        self.charges = 0.0
        self.flows = []         # (date, amount) deposits
        self.notes = []

    def qty(self, sym):
        return sum(l["qty"] for l in self.lots.get(sym, []))

    def price(self, sym, i, kind):
        return self.data[sym][kind][i]

    def equity(self, i, kind="close"):
        return self.cash + sum(self.qty(s) * self.price(s, i, kind) for s in self.lots)

    def affordable(self, cash, px):
        if px <= 0 or cash <= 0:
            return 0
        rate = buy_rate(self.ch)
        q = int(cash // (px * (1 + rate)))
        while q > 0 and q * px + order_charges("buy", q * px, self.ch)["total"] > cash + 1e-9:
            q -= 1
        return max(q, 0)

    def buy(self, sym, q, i, kind, why=""):
        if q <= 0:
            return
        px = self.price(sym, i, kind) * (1 + self.slip)
        q = min(q, self.affordable(self.cash, px))
        if q <= 0:
            return
        val = q * px
        chg = order_charges("buy", val, self.ch)["total"]
        if self.qty(sym) == 0:
            self.entry_index[sym] = i
        self.cash -= val + chg
        self.charges += chg
        self.lots.setdefault(sym, []).append({"qty": q, "px": px, "date": self.dates[i], "chg_ps": chg / q})
        self.fills.append({"date": self.dates[i].isoformat(), "symbol": sym, "side": "BUY", "qty": q,
                           "price": round(px, 4), "value": round(val, 2), "charges": round(chg, 2),
                           "realized_pnl": "", "holding_days": "", "fill": kind, "note": why})

    def sell(self, sym, q, i, kind, why=""):
        have = self.qty(sym)
        q = min(int(q), have)
        if q <= 0:
            return
        px = self.price(sym, i, kind) * (1 - self.slip)
        val = q * px
        d = self.dates[i]
        dp = (sym, d) not in self.dp_days
        self.dp_days.add((sym, d))
        chg = order_charges("sell", val, self.ch, dp=dp)["total"]
        self.cash += val - chg
        self.charges += chg
        left, cost, buy_chg, days_w = q, 0.0, 0.0, 0.0
        lots = self.lots[sym]
        while left > 0:
            lot = lots[0]
            take = min(left, lot["qty"])
            held = (d - lot["date"]).days
            self.realized.append({"date": d, "symbol": sym, "qty": take, "buy_date": lot["date"], "buy_px": lot["px"],
                                  "sell_px": px, "days": held, "gain": take * (px - lot["px"]),
                                  "term": "LT" if held >= LT_DAYS else "ST"})
            cost += take * lot["px"]
            buy_chg += take * lot["chg_ps"]
            days_w += take * held
            lot["qty"] -= take
            left -= take
            if lot["qty"] == 0:
                lots.pop(0)
        if not lots:
            del self.lots[sym]
            self.entry_index.pop(sym, None)
        pnl = val - cost - chg - buy_chg
        self.closed.append({"date": d.isoformat(), "symbol": sym, "pnl": pnl,
                            "ret": pnl / (cost + buy_chg) if cost > 0 else 0.0})
        self.fills.append({"date": d.isoformat(), "symbol": sym, "side": "SELL", "qty": q, "price": round(px, 4),
                           "value": round(val, 2), "charges": round(chg, 2), "realized_pnl": round(pnl, 2),
                           "holding_days": int(round(days_w / q)), "fill": kind, "note": why})

    def target(self, weights, i, kind, why=""):
        eq = self.equity(i, kind)
        rate = buy_rate(self.ch)
        want = {}
        for s in set(list(weights.keys()) + list(self.lots.keys())):
            w = weights.get(s, 0.0)
            px = self.price(s, i, kind)
            want[s] = int((w * eq) // (px * (1 + rate) * (1 + self.slip))) if px > 0 else 0
        for s in sorted(want):
            if self.qty(s) > want[s]:
                self.sell(s, self.qty(s) - want[s], i, kind, why)
        for s in sorted(want, key=lambda k: -weights.get(k, 0.0)):
            if self.qty(s) < want[s]:
                self.buy(s, want[s] - self.qty(s), i, kind, why)

    def execute(self, orders, i, kind):
        for o in orders:
            op = o["op"]
            if op == "deposit":
                self.cash += o["amount"]
                self.flows.append((self.dates[i], o["amount"]))
            elif op == "buy_all":
                px = self.price(o["sym"], i, kind) * (1 + self.slip)
                self.buy(o["sym"], self.affordable(self.cash, px), i, kind, o.get("why", ""))
            elif op == "sell_all":
                self.sell(o["sym"], self.qty(o["sym"]), i, kind, o.get("why", ""))
            elif op == "target":
                self.target(o["weights"], i, kind, o.get("why", ""))


def make_strategy(stype, params, syms, data, dates):
    if stype not in STRATEGIES:
        raise BTError("unknown strategy %r (one of: %s)" % (stype, ", ".join(sorted(STRATEGIES))))
    p = dict(STRATEGY_TYPES[stype]["params"])
    p.update(params or {})
    if stype == "rebalance" and params and params.get("weights"):
        p["weights"] = params["weights"]
    return STRATEGIES[stype](p, syms, data, dates), p


def simulate(stype, params, syms, data, dates, ch, start, end, capital, fill="open", slippage_bps=0.0, tax=False):
    """Run one strategy on dates[start:end]. Indicators may look back before `start` (warm-up), never forward."""
    if end - start < 2:
        raise BTError("fewer than 2 trading days in the backtest window")
    strat, p = make_strategy(stype, params, syms, data, dates)
    strat.start = start
    port = Portfolio(capital, data, dates, ch, slippage_bps)
    pending = []
    fy_gain = {}
    taxes = []
    cur_fy = fy_of(dates[start])
    rows = []
    prev_eq = float(capital)
    nav = 100.0
    n_real = 0

    def settle(fy, i, kind, final=False):
        d = dates[i]
        g = fy_gain.get(fy, {"ST": 0.0, "LT": 0.0})
        t = realized_tax(g["ST"], g["LT"])
        t.update({"fy": fy_label(fy), "paid_on": d.isoformat(), "provisional": final})
        if t["tax"] > 0 and port.cash < t["tax"] and port.lots:
            # not enough cash: sell just enough of the largest holding (FIFO) to pay it, as an investor would
            short = t["tax"] - port.cash
            s = max(port.lots, key=lambda k: port.qty(k) * port.price(k, i, kind))
            px = port.price(s, i, kind) * (1 - port.slip)
            q = int(math.ceil(short * 1.01 / px)) + 1 if px > 0 else 0
            port.sell(s, q, i, kind, "sold to pay %s tax" % t["fy"])
        if t["tax"] > 0:
            port.cash -= t["tax"]
            port.fills.append({"date": d.isoformat(), "symbol": "TAX", "side": "TAX", "qty": "", "price": "",
                               "value": round(t["tax"], 2), "charges": "", "realized_pnl": "", "holding_days": "",
                               "fill": "", "note": "%s capital-gains tax%s" % (t["fy"], " (due, not yet paid)" if final else "")})
            if port.cash < 0:
                port.notes.append("Tax for %s exceeded the cash on %s; cash went negative." % (t["fy"], d.isoformat()))
        if g["ST"] or g["LT"]:
            taxes.append(t)
        return t["tax"]

    for i in range(start, end):
        d = dates[i]
        flow_before = sum(a for _, a in port.flows)
        if tax and fy_of(d) != cur_fy:
            settle(cur_fy, i, fill)
            cur_fy = fy_of(d)
        if fill == "open":
            port.execute(pending, i, "open")
            port.execute(strat.schedule(i, port), i, "open")
        else:
            port.execute(strat.schedule(i, port), i, "close")
        sig = strat.signal(i, port)
        if fill == "open":
            pending = sig
        else:
            port.execute(sig, i, "close")
        for r in port.realized[n_real:]:
            fy_gain.setdefault(fy_of(r["date"]), {"ST": 0.0, "LT": 0.0})[r["term"]] += r["gain"]
        n_real = len(port.realized)
        if tax and i == end - 1:
            for r in port.realized[n_real:]:
                fy_gain.setdefault(fy_of(r["date"]), {"ST": 0.0, "LT": 0.0})[r["term"]] += r["gain"]
            n_real = len(port.realized)
            settle(cur_fy, i, "close", final=True)
        flow = sum(a for _, a in port.flows) - flow_before
        eq = port.equity(i, "close")
        base = prev_eq + flow
        r = eq / base - 1 if base > 0 else 0.0
        nav *= 1 + r
        hold = eq - port.cash
        rows.append({"date": d, "cash": port.cash, "holdings": hold, "equity": eq, "flow": flow, "ret": r, "nav": nav})
        prev_eq = eq
    dropped = [o for o in pending if o.get("op") != "deposit"]
    return {"rows": rows, "port": port, "params": p, "taxes": taxes, "dropped": dropped,
            "capital": float(capital)}


# ---------------------------------------------------------------- metrics

def xnpv(rate, flows):
    t0 = flows[0][0]
    return sum(a / (1 + rate) ** ((d - t0).days / 365.0) for d, a in flows)


def xirr(flows, guess=0.1):
    """Annualised internal rate of return of dated cash flows [(date, amount)], outflows negative.
    Newton first, bisection fallback. None when there is no sign change."""
    flows = sorted([(d, float(a)) for d, a in flows if a], key=lambda t: t[0])
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = flows[0][0]
    ts = [((d - t0).days / 365.0, a) for d, a in flows]

    def f(r):
        return sum(a / (1 + r) ** t for t, a in ts)

    def df(r):
        return sum(-t * a / (1 + r) ** (t + 1) for t, a in ts)

    r = guess
    for _ in range(100):
        try:
            fv, dv = f(r), df(r)
        except (OverflowError, ZeroDivisionError):
            break
        if dv == 0:
            break
        nr = r - fv / dv
        if nr <= -0.999999:
            break
        if abs(nr - r) < 1e-10:
            if abs(f(nr)) < 1e-6 * max(1.0, max(abs(a) for _, a in ts)):
                return nr
            break
        r = nr
    lo, hi = -0.9999, 10.0
    try:
        flo, fhi = f(lo), f(hi)
    except OverflowError:
        return None
    while flo * fhi > 0 and hi < 1e6:
        hi *= 10
        fhi = f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-9 or hi - lo < 1e-12:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def drawdown(values, dates=None):
    """Max drawdown of a value series: depth (negative fraction), peak/trough/recovery indexes, the
    longest underwater stretch (bars and calendar days, unrecovered counts to the end), and the series."""
    peak, peak_i = None, 0
    dd = []
    mdd, mpk, mtr = 0.0, 0, 0
    long_bars, long_from, long_to, under_from = 0, None, None, None
    for i, v in enumerate(values):
        if peak is None or v >= peak:
            if under_from is not None and i - under_from > long_bars:
                long_bars, long_from, long_to = i - under_from, under_from, i
            under_from = None
            peak, peak_i = v, i
            dd.append(0.0)
            continue
        if under_from is None:
            under_from = peak_i
        x = v / peak - 1 if peak > 0 else 0.0
        dd.append(x)
        if x < mdd:
            mdd, mpk, mtr = x, peak_i, i
    unrec = False
    if under_from is not None and len(values) - 1 - under_from > long_bars:
        long_bars, long_from, long_to, unrec = len(values) - 1 - under_from, under_from, len(values) - 1, True
    rec = None
    if mdd < 0:
        for j in range(mtr, len(values)):
            if values[j] >= values[mpk]:
                rec = j
                break
    out = {"max_drawdown": mdd, "peak": mpk, "trough": mtr, "recovery": rec,
           "longest_underwater_bars": long_bars, "longest_from": long_from, "longest_to": long_to,
           "longest_unrecovered": unrec, "series": dd}
    if dates is not None:
        def ds(k):
            return None if k is None else dates[k].isoformat()
        out.update({"peak_date": ds(mpk) if mdd < 0 else None, "trough_date": ds(mtr) if mdd < 0 else None,
                    "recovery_date": ds(rec),
                    "longest_from_date": ds(long_from), "longest_to_date": ds(long_to),
                    "longest_underwater_days": (dates[long_to] - dates[long_from]).days if long_from is not None else 0})
    return out


def risk_stats(rets, rf_annual):
    rf_d = (1 + rf_annual) ** (1.0 / TRADING_DAYS) - 1
    ex = [r - rf_d for r in rets]
    sd = stdev(rets)
    vol = sd * math.sqrt(TRADING_DAYS) if sd is not None else None
    sde = stdev(ex)
    sharpe = mean(ex) / sde * math.sqrt(TRADING_DAYS) if sde else None
    down = math.sqrt(sum(min(0.0, x) ** 2 for x in ex) / len(ex)) if ex else 0.0
    sortino = mean(ex) * TRADING_DAYS / (down * math.sqrt(TRADING_DAYS)) if down > 0 else None
    return {"volatility": vol, "sharpe": sharpe, "sortino": sortino}


def alpha_beta(s, b, rf_annual):
    n = min(len(s), len(b))
    if n < 3:
        return None, None
    s, b = s[:n], b[:n]
    ms, mb = mean(s), mean(b)
    vb = sum((x - mb) ** 2 for x in b) / (n - 1)
    if vb <= 0:
        return None, None
    beta = sum((x - ms) * (y - mb) for x, y in zip(s, b)) / (n - 1) / vb
    rf_d = (1 + rf_annual) ** (1.0 / TRADING_DAYS) - 1
    alpha = (ms - rf_d - beta * (mb - rf_d)) * TRADING_DAYS
    return alpha, beta


def metrics(res, rf=DEFAULT_RF / 100.0, bench=None):
    rows, port = res["rows"], res["port"]
    dates = [r["date"] for r in rows]
    navs = [100.0] + [r["nav"] for r in rows]
    days = (dates[-1] - dates[0]).days
    final = rows[-1]["equity"]
    deposits = sum(a for _, a in port.flows)
    invested = res["capital"] + deposits
    twr = navs[-1] / 100.0 - 1
    cagr = (navs[-1] / 100.0) ** (365.0 / days) - 1 if days > 0 and navs[-1] > 0 else None
    flows = []
    if res["capital"] > 0:
        flows.append((dates[0], -res["capital"]))
    flows += [(d, -a) for d, a in port.flows]
    flows.append((dates[-1], final))
    xr = xirr(flows)
    dd = drawdown(navs, [dates[0]] + dates)
    rets = [r["ret"] for r in rows]
    rs = risk_stats(rets, rf)
    wins = [c for c in port.closed if c["pnl"] > 0]
    losses = [c for c in port.closed if c["pnl"] <= 0]
    taxes = sum(t["tax"] for t in res["taxes"])
    m = {
        "start": dates[0].isoformat(), "end": dates[-1].isoformat(), "trading_days": len(rows), "calendar_days": days,
        "initial_capital": res["capital"], "deposits": deposits, "invested": invested, "final_equity": final,
        "profit": final - invested,
        "total_return": (final - invested) / invested if invested > 0 else None,
        "twr_total_return": twr, "cagr": cagr, "xirr": xr,
        "max_drawdown": dd["max_drawdown"], "drawdown_peak": dd.get("peak_date"),
        "drawdown_trough": dd.get("trough_date"), "drawdown_recovery": dd.get("recovery_date"),
        "longest_underwater_days": dd.get("longest_underwater_days"),
        "longest_underwater_trading_days": dd["longest_underwater_bars"],
        "longest_underwater_from": dd.get("longest_from_date"), "longest_underwater_to": dd.get("longest_to_date"),
        "longest_underwater_unrecovered": dd["longest_unrecovered"],
        "volatility": rs["volatility"], "sharpe": rs["sharpe"], "sortino": rs["sortino"], "risk_free": rf,
        "calmar": (cagr / abs(dd["max_drawdown"])) if cagr is not None and dd["max_drawdown"] < 0 else None,
        "orders": sum(1 for f in port.fills if f["side"] in ("BUY", "SELL")),
        "closed_trades": len(port.closed), "win_rate": len(wins) / len(port.closed) if port.closed else None,
        "avg_win": mean([c["pnl"] for c in wins]), "avg_loss": mean([c["pnl"] for c in losses]),
        "avg_win_pct": mean([c["ret"] for c in wins]), "avg_loss_pct": mean([c["ret"] for c in losses]),
        "exposure": sum(1 for r in rows if r["holdings"] > 0) / len(rows),
        "avg_invested": mean([r["holdings"] / r["equity"] if r["equity"] > 0 else 0.0 for r in rows]),
        "charges_paid": port.charges, "tax_paid": taxes,
        "open_positions": {s: port.qty(s) for s in sorted(port.lots)},
        "cash_end": port.cash,
    }
    if bench:
        m["benchmark"] = bench_metrics(bench, rows, port, res["capital"], rets, rf)
    return m


def bench_metrics(bench, rows, port, capital, rets, rf):
    """Benchmark = the same money in another symbol at its close (no charges, fractional units)."""
    by = {}
    for b in bench["bars"]:
        by[b["date"]] = b["close"]
    keys = sorted(by)
    closes, j, last = [], 0, None
    for r in rows:
        while j < len(keys) and keys[j] <= r["date"]:
            last = by[keys[j]]
            j += 1
        closes.append(last)
    if closes[0] is None:
        return {"symbol": bench["symbol"], "note": "benchmark has no data at the start date"}
    navs = [100.0 * c / closes[0] for c in closes]
    brets = [0.0] + [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]
    days = (rows[-1]["date"] - rows[0]["date"]).days
    units = 0.0
    flows = []
    if capital > 0:
        units += capital / closes[0]
        flows.append((rows[0]["date"], -capital))
    fmap = {}
    for d, a in port.flows:
        fmap[d] = fmap.get(d, 0.0) + a
    for k, r in enumerate(rows):
        if r["date"] in fmap:
            units += fmap[r["date"]] / closes[k]
            flows.append((r["date"], -fmap[r["date"]]))
    final = units * closes[-1]
    flows.append((rows[-1]["date"], final))
    alpha, beta = alpha_beta(rets[1:], brets[1:], rf)
    rs = risk_stats(brets[1:], rf)
    dd = drawdown(navs)
    return {"symbol": bench["symbol"], "adjusted": bench["adjusted"],
            "total_return": navs[-1] / 100.0 - 1,
            "cagr": (navs[-1] / 100.0) ** (365.0 / days) - 1 if days > 0 else None,
            "xirr": xirr(flows), "final_equity_same_flows": final,
            "max_drawdown": dd["max_drawdown"], "volatility": rs["volatility"], "sharpe": rs["sharpe"],
            "alpha": alpha, "beta": beta, "nav": navs}


# ---------------------------------------------------------------- orchestration

def split_index(start, end, frac):
    if not (0.1 <= frac <= 0.95):
        raise BTError("--split wants a fraction between 0.1 and 0.95, like 0.7")
    k = start + int(round((end - start) * frac))
    if k - start < 2 or end - k < 2:
        raise BTError("--split leaves fewer than 2 days on one side")
    return k


def window(dates, dfrom, dto):
    start, end = 0, len(dates)
    if dfrom:
        d = parse_date(dfrom)
        start = next((i for i, x in enumerate(dates) if x >= d), len(dates))
    if dto:
        d = parse_date(dto)
        end = next((i for i, x in enumerate(dates) if x > d), len(dates))
    if end - start < 2:
        raise BTError("fewer than 2 trading days between %s and %s" % (dfrom or "start", dto or "end"))
    return start, end


def parse_sweep(items):
    grid = []
    for it in items or []:
        if "=" not in it:
            raise BTError("--sweep wants name=v1,v2 (got %r)" % it)
        k, vs = it.split("=", 1)
        grid.append((k.strip(), [parse_value(v) for v in vs.split(",") if v.strip()]))
    return grid


def metric_value(m, key):
    v = m.get(key)
    return None if v is None else float(v)


def overfit_warning(best, med, n, metric):
    """A warning line when the best in-sample value beats the median by a lot: more than half the median's
    size AND more than 5 points (return metrics) or 0.3 (ratio metrics). Needs 3+ combinations."""
    if best is None or med is None or n < 3:
        return None
    gap = best - med
    floor = 0.05 if metric in ("cagr", "total_return", "xirr") else 0.3
    if gap > floor and gap > 0.5 * abs(med):
        return ("Overfitting risk: the best in-sample %s (%s) beats the median of %d combinations (%s) by a lot. "
                "Expect real results nearer the median." % (metric, num(best, 3), n, num(med, 3)))
    return None


def sweep(cfg, data, dates, ch, start, end, grid, metric="sharpe", split=None, rf=DEFAULT_RF / 100.0):
    """Grid search. With split, ranks on the in-sample part and re-runs the best on the out-of-sample part.
    Flags overfitting when the best in-sample beats the median by a lot, or falls apart out of sample."""
    if metric not in SWEEP_METRICS:
        raise BTError("--metric must be one of %s" % ", ".join(SWEEP_METRICS))
    is_end = split_index(start, end, split) if split else end
    keys = [k for k, _ in grid]
    table = []
    skipped = 0
    for combo in itertools.product(*[v for _, v in grid]):
        params = dict(cfg["params"])
        params.update(dict(zip(keys, combo)))
        try:
            res = simulate(cfg["strategy"], params, cfg["symbols"], data, dates, ch, start, is_end, cfg["capital"],
                           cfg["fill"], cfg["slippage_bps"], cfg["tax"])
        except BTError:
            skipped += 1
            continue
        m = metrics(res, rf)
        table.append({"params": dict(zip(keys, combo)), "metric": metric_value(m, metric), "cagr": m["cagr"],
                      "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"], "closed_trades": m["closed_trades"]})
    if not table:
        raise BTError("no valid parameter combination in the sweep")
    valid = [t for t in table if t["metric"] is not None]
    table.sort(key=lambda t: (t["metric"] is None, -(t["metric"] or 0)))
    best = table[0]
    med = median([t["metric"] for t in valid]) if valid else None
    warnings = []
    w = overfit_warning(best["metric"], med, len(valid), metric)
    if w:
        warnings.append(w)
    out = {"metric": metric, "combinations": len(table), "skipped_invalid": skipped, "median": med,
           "best": best, "table": table, "warnings": warnings}
    if split:
        params = dict(cfg["params"])
        params.update(best["params"])
        oos = simulate(cfg["strategy"], params, cfg["symbols"], data, dates, ch, is_end, end, cfg["capital"],
                       cfg["fill"], cfg["slippage_bps"], cfg["tax"])
        om = metrics(oos, rf)
        out["split_date"] = dates[is_end].isoformat()
        out["best_out_of_sample"] = {k: om[k] for k in ("cagr", "sharpe", "max_drawdown", "total_return", "xirr")}
        ov = metric_value(om, metric)
        out["best_out_of_sample"]["metric"] = ov
        if best["metric"] is not None and best["metric"] > 0 and (ov is None or ov < 0.5 * best["metric"]):
            warnings.append("Out of sample the best combination's %s fell from %s to %s: the in-sample edge did not "
                            "hold up." % (metric, num(best["metric"], 3), num(ov, 3)))
    warnings.append("%d combinations were tried; the more you try, the more the best one is luck." % len(table))
    return out


def caveats(infos, ch, cfg):
    out = []
    for inf in infos:
        if inf["adjusted"]:
            out.append("%s: split/bonus adjusted series (%s); the guessed split/bonus gap check is off for it." % (
                inf["symbol"], os.path.basename(inf["file"])))
        else:
            line = "%s: UNADJUSTED prices (no %s.adj.csv)." % (inf["symbol"], inf["symbol"])
            fl = inf["corporate_action_flags"]
            if fl:
                line += " Likely split/bonus on %s%s. %s" % (
                    ", ".join("%s (%+.1f %%, %s)" % (f["date"], f["gap_pct"], f["likely"]) for f in fl[:6]),
                    " and %d more" % (len(fl) - 6) if len(fl) > 6 else "",
                    "Prices before them were back-adjusted by the expected ratio (--auto-adjust)." if inf["auto_adjusted"]
                    else "Results across those dates are WRONG until you fetch adjusted data with trader-data or use --auto-adjust.")
            else:
                line += " No split/bonus-like gaps found, but check nseindia.com corporate actions."
            out.append(line)
    out += [
        "Data: trader-data daily cache (free sources such as Yahoo or NSE bhavcopy, or Kite get_historical_data). "
        "Prices exclude dividends unless the adjusted file includes them.",
        "Survivorship bias: you picked a symbol that still exists and still trades today; delisted and failed "
        "companies are not in this test.",
        "Charges are an %s of Zerodha delivery charges (%s), factor %s. Fills are daily bars plus %s bps slippage: no "
        "intraday path, no circuit-lock or liquidity check." % (
            charges_label(ch), ch.get("verified", ""), (ch.get("calibration") or {}).get("factor", 1.0), cfg["slippage_bps"]),
    ]
    if cfg["tax"]:
        out.append("Tax: today's rates (STCG 20 %, LTCG 12.5 % above Rs 1.25 lakh a year) applied to every year, on "
                   "fill-price gains (charges not deducted), FIFO lots, no loss carry-forward, paid at each FY end "
                   "from the strategy's cash. Not tax advice.")
    else:
        out.append("No tax applied (add --tax for STCG/LTCG on FIFO lots).")
    out.append("Past performance does not predict future returns. This is a historical simulation, not advice.")
    return out


def build_config(a):
    cfg = {"strategy": None, "symbols": [], "params": {}, "capital": None, "name": None}
    if a.config or a.name:
        path = a.config or default_config_path(a.root)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        named = doc.get("strategies", doc)
        if not a.name:
            raise BTError("--config needs --name (one of: %s)" % ", ".join(sorted(named)))
        if a.name not in named:
            raise BTError("no strategy %r in %s (have: %s)" % (a.name, shown(path), ", ".join(sorted(named))))
        s = named[a.name]
        cfg["strategy"] = s.get("type") or s.get("strategy")
        cfg["symbols"] = [x.upper() for x in (s.get("symbols") or ([s["symbol"]] if s.get("symbol") else []))]
        cfg["params"] = dict(s.get("params") or {})
        cfg["capital"] = s.get("capital")
        cfg["name"] = a.name
        if cfg["strategy"] == "rebalance" and not cfg["symbols"]:
            cfg["symbols"] = sorted(k.upper() for k in (cfg["params"].get("weights") or {}))
    if a.strategy:
        cfg["strategy"] = a.strategy
    if a.symbol:
        cfg["symbols"] = [x.strip().upper() for x in a.symbol.split(",") if x.strip()]
    for kv in a.param or []:
        if "=" not in kv:
            raise BTError("--param wants name=value (got %r)" % kv)
        k, v = kv.split("=", 1)
        cfg["params"][k.strip()] = parse_value(v)
    if a.weights:
        w = {}
        for kv in a.weights.split(","):
            k, v = kv.split("=")
            w[k.strip().upper()] = float(v)
        cfg["params"]["weights"] = w
        if not a.symbol:
            cfg["symbols"] = sorted(w)
    if not cfg["strategy"]:
        raise BTError("say which strategy: --strategy %s (or --config FILE --name NAME)" % "|".join(sorted(STRATEGIES)))
    if cfg["strategy"] not in STRATEGIES:
        raise BTError("unknown strategy %r (one of: %s)" % (cfg["strategy"], ", ".join(sorted(STRATEGIES))))
    if cfg["strategy"] == "rebalance":
        w = cfg["params"].get("weights") or STRATEGY_TYPES["rebalance"]["params"]["weights"]
        cfg["params"]["weights"] = {k.upper(): float(v) for k, v in w.items()}
        if not cfg["symbols"]:
            cfg["symbols"] = sorted(cfg["params"]["weights"])
        missing = set(cfg["params"]["weights"]) - set(cfg["symbols"])
        cfg["symbols"] = sorted(set(cfg["symbols"]) | missing)
    elif len(cfg["symbols"]) != 1:
        raise BTError("%s takes exactly one symbol: --symbol RELIANCE" % cfg["strategy"])
    if a.capital is not None:
        cfg["capital"] = a.capital
    if cfg["capital"] is None:
        cfg["capital"] = 0.0 if cfg["strategy"] == "sip" else DEFAULT_CAPITAL
    cfg["capital"] = float(cfg["capital"])
    if cfg["strategy"] != "sip" and cfg["capital"] <= 0:
        raise BTError("--capital must be above zero")
    cfg["fill"] = a.fill
    cfg["slippage_bps"] = float(a.slippage_bps)
    cfg["tax"] = bool(a.tax)
    cfg["name"] = cfg["name"] or "%s-%s" % (cfg["strategy"], "-".join(cfg["symbols"])[:40])
    return cfg


def default_config_path(root):
    p = os.path.join(root, "trader", "strategies.json")
    if os.path.isfile(p):
        return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies.example.json")


def run(a):
    root = os.path.abspath(a.root)
    data_dir = a.data_dir or os.path.join(root, "trader", "data", "daily")
    cfg = build_config(a)
    ch = load_charges(root)
    series, infos = {}, []
    for s in cfg["symbols"]:
        bars, inf = load_symbol(data_dir, s, a.auto_adjust)
        series[s] = bars
        infos.append(inf)
    dates, data = align(series)
    if len(dates) < 2:
        raise BTError("the symbols share fewer than 2 trading days")
    start, end = window(dates, a.date_from, a.date_to)
    rf = float(a.rf) / 100.0
    bench = None
    bsym = (a.benchmark or "").strip().upper()
    bench_note = None
    if bsym in ("", "AUTO"):
        bsym = "NIFTYBEES"
        auto = True
    else:
        auto = False
    if bsym != "NONE":
        try:
            bb, binf = load_symbol(data_dir, bsym, a.auto_adjust)
            bench = {"symbol": bsym, "bars": bb, "adjusted": binf["adjusted"]}
        except BTError:
            bench_note = None if auto else "benchmark %s not cached; run trader-data for it" % bsym
            bench = None

    result = {"name": cfg["name"], "strategy": cfg["strategy"], "symbols": cfg["symbols"],
              "fill": cfg["fill"], "slippage_bps": cfg["slippage_bps"], "tax": cfg["tax"],
              "charges_label": charges_label(ch), "charges_verified": ch.get("verified"),
              "calibration_factor": (ch.get("calibration") or {}).get("factor", 1.0),
              "data": infos, "generated": datetime.datetime.now(IST).isoformat(timespec="seconds")}
    sw = None
    if a.sweep:
        sw = sweep(cfg, data, dates, ch, start, end, parse_sweep(a.sweep), a.metric, a.split, rf)
        cfg["params"].update(sw["best"]["params"])
        result["sweep"] = sw
    res = simulate(cfg["strategy"], cfg["params"], cfg["symbols"], data, dates, ch, start, end, cfg["capital"],
                   cfg["fill"], cfg["slippage_bps"], cfg["tax"])
    m = metrics(res, rf, bench)
    result["params"] = res["params"]
    result["metrics"] = {k: v for k, v in m.items() if k != "benchmark"}
    if m.get("benchmark"):
        result["benchmark"] = {k: v for k, v in m["benchmark"].items() if k != "nav"}
    elif bench_note:
        result["benchmark"] = {"note": bench_note}
    result["taxes"] = res["taxes"]
    result["dropped_orders_at_end"] = len(res["dropped"])
    result["notes"] = res["port"].notes
    if a.split:
        k = split_index(start, end, a.split)
        ins = simulate(cfg["strategy"], cfg["params"], cfg["symbols"], data, dates, ch, start, k, cfg["capital"],
                       cfg["fill"], cfg["slippage_bps"], cfg["tax"])
        oos = simulate(cfg["strategy"], cfg["params"], cfg["symbols"], data, dates, ch, k, end, cfg["capital"],
                       cfg["fill"], cfg["slippage_bps"], cfg["tax"])
        keep = ("start", "end", "cagr", "xirr", "total_return", "max_drawdown", "volatility", "sharpe", "sortino",
                "closed_trades", "win_rate", "exposure", "charges_paid", "tax_paid")
        mi, mo = metrics(ins, rf), metrics(oos, rf)
        result["split"] = {"fraction": a.split, "split_date": dates[k].isoformat(),
                           "in_sample": {x: mi[x] for x in keep}, "out_of_sample": {x: mo[x] for x in keep}}
    result["caveats"] = caveats(infos, ch, cfg)
    result["_rows"] = res["rows"]
    result["_fills"] = res["port"].fills
    result["_bench_nav"] = m.get("benchmark", {}).get("nav") if m.get("benchmark") else None
    return result


# ---------------------------------------------------------------- output

def summary_doc(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


def write_outputs(result, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary_doc(result), fh, indent=2, default=str)
    with open(os.path.join(out_dir, "trades.csv"), "w", encoding="utf-8", newline="") as fh:
        cols = ["date", "symbol", "side", "qty", "price", "value", "charges", "realized_pnl", "holding_days", "fill", "note"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in result["_fills"]:
            w.writerow(f)
    bn = result.get("_bench_nav")
    rows = result["_rows"]
    dd = drawdown([r["nav"] for r in rows])["series"]
    with open(os.path.join(out_dir, "equity.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "cash", "holdings", "equity", "flow", "daily_return", "nav", "drawdown", "benchmark_nav"])
        for k, r in enumerate(rows):
            w.writerow([r["date"].isoformat(), "%.2f" % r["cash"], "%.2f" % r["holdings"], "%.2f" % r["equity"],
                        "%.2f" % r["flow"], "%.6f" % r["ret"], "%.4f" % r["nav"], "%.6f" % dd[k],
                        "%.4f" % bn[k] if bn and bn[k] is not None else ""])
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as fh:
        fh.write(render_html(result, dd))
    return out_dir


def _thin(n, cap=900):
    step = max(1, int(math.ceil(n / float(cap))))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def svg_chart(dates, series, title, height=260, area=False, pct_axis=False, mark=None):
    W, H, L, R, T, B = 900, height, 64, 16, 28, 28
    vals = [v for _, ys, _ in series for v in ys if v is not None]
    lo, hi = min(vals), max(vals)
    if area:
        hi = 0.0
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    n = len(dates)
    idx = _thin(n)

    def X(i):
        return L + (W - L - R) * (i / float(max(n - 1, 1)))

    def Y(v):
        return T + (H - T - B) * (1 - (v - lo) / (hi - lo))

    parts = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s" xmlns="http://www.w3.org/2000/svg">' % (W, H, html.escape(title))]
    parts.append('<text x="%d" y="18" class="t">%s</text>' % (L, html.escape(title)))
    for k in range(5):
        v = lo + (hi - lo) * k / 4.0
        y = Y(v)
        lab = ("%.0f %%" % (v * 100)) if pct_axis else ("%.0f" % v)
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="g"/>' % (L, W - R, y, y))
        parts.append('<text x="%d" y="%.1f" class="a" text-anchor="end">%s</text>' % (L - 6, y + 4, lab))
    for i in (0, n // 2, n - 1):
        parts.append('<text x="%.1f" y="%d" class="a" text-anchor="middle">%s</text>' % (X(i), H - 8, dates[i].isoformat()))
    if mark is not None and 0 <= mark < n:
        parts.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" class="m"/>' % (X(mark), X(mark), T, H - B))
        parts.append('<text x="%.1f" y="%d" class="a">split</text>' % (X(mark) + 4, T + 12))
    for name, ys, cls in series:
        pts = ["%.1f,%.1f" % (X(i), Y(ys[i])) for i in idx if ys[i] is not None]
        if not pts:
            continue
        if area:
            parts.append('<polygon class="%s-a" points="%.1f,%.1f %s %.1f,%.1f"/>' % (cls, X(idx[0]), Y(0), " ".join(pts), X(idx[-1]), Y(0)))
        parts.append('<polyline class="%s" fill="none" points="%s"/>' % (cls, " ".join(pts)))
    parts.append("</svg>")
    return "".join(parts)


def render_html(result, dd):
    m = result["metrics"]
    rows = result["_rows"]
    dates = [r["date"] for r in rows]
    navs = [r["nav"] for r in rows]
    series = [("Strategy", navs, "s1")]
    bn = result.get("_bench_nav")
    b = result.get("benchmark") or {}
    if bn:
        series.append(("Benchmark " + b.get("symbol", ""), bn, "s2"))
    mark = None
    if result.get("split"):
        sd = parse_date(result["split"]["split_date"])
        mark = next((i for i, d in enumerate(dates) if d >= sd), None)
    eq_svg = svg_chart(dates, series, "Growth of 100 (time-weighted)", mark=mark)
    dd_svg = svg_chart(dates, [("Drawdown", dd, "s3")], "Drawdown", height=180, area=True, pct_axis=True, mark=mark)

    def row(k, v):
        return "<tr><th>%s</th><td>%s</td></tr>" % (html.escape(k), html.escape(v))

    tbl = [
        ("Period", "%s to %s (%d trading days)" % (m["start"], m["end"], m["trading_days"])),
        ("Invested", inr(m["invested"])), ("Final equity", inr(m["final_equity"])),
        ("Profit", inr(m["profit"])), ("Total return (money)", pct(m["total_return"])),
        ("CAGR (time-weighted)", pct(m["cagr"])), ("XIRR", pct(m["xirr"])),
        ("Max drawdown", "%s (%s to %s, recovered %s)" % (pct(m["max_drawdown"]), m["drawdown_peak"] or "-",
                                                         m["drawdown_trough"] or "-", m["drawdown_recovery"] or "not yet")),
        ("Longest underwater", "%s calendar days%s" % (m["longest_underwater_days"], " (still under)" if m["longest_underwater_unrecovered"] else "")),
        ("Volatility", pct(m["volatility"])), ("Sharpe / Sortino", "%s / %s (rf %.2f %%)" % (num(m["sharpe"]), num(m["sortino"]), m["risk_free"] * 100)),
        ("Orders / closed trades", "%d / %d" % (m["orders"], m["closed_trades"])),
        ("Win rate", pct(m["win_rate"], 1) if m["win_rate"] is not None else "n/a"),
        ("Avg win / avg loss", "%s / %s" % (inr(m["avg_win"]) if m["avg_win"] is not None else "n/a",
                                            inr(m["avg_loss"]) if m["avg_loss"] is not None else "n/a")),
        ("Exposure (days invested)", pct(m["exposure"], 1)),
        ("Charges paid (%s)" % result["charges_label"], inr(m["charges_paid"])), ("Tax paid", inr(m["tax_paid"])),
    ]
    if b.get("symbol"):
        tbl += [("Benchmark", "%s: CAGR %s, max DD %s, same-flows XIRR %s" % (b["symbol"], pct(b.get("cagr")), pct(b.get("max_drawdown")), pct(b.get("xirr")))),
                ("Alpha / beta", "%s / %s" % (pct(b.get("alpha")), num(b.get("beta"))))]
    extra = ""
    if result.get("split"):
        s = result["split"]
        extra += "<h2>Walk-forward split at %s</h2><table><tr><th></th><th>In-sample</th><th>Out-of-sample</th></tr>" % html.escape(s["split_date"])
        for k in ("cagr", "max_drawdown", "sharpe", "closed_trades", "win_rate"):
            f = (lambda v: num(v)) if k in ("sharpe",) else ((lambda v: str(v)) if k == "closed_trades" else (lambda v: pct(v)))
            extra += "<tr><th>%s</th><td>%s</td><td>%s</td></tr>" % (k, html.escape(f(s["in_sample"][k])), html.escape(f(s["out_of_sample"][k])))
        extra += "</table>"
    if result.get("sweep"):
        sw = result["sweep"]
        extra += "<h2>Parameter sweep (%d combinations, ranked by %s)</h2><table><tr><th>params</th><th>%s</th><th>CAGR</th><th>max DD</th></tr>" % (
            sw["combinations"], html.escape(sw["metric"]), html.escape(sw["metric"]))
        for t in sw["table"][:25]:
            extra += "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                html.escape(json.dumps(t["params"])), num(t["metric"], 3), pct(t["cagr"]), pct(t["max_drawdown"]))
        extra += "</table>" + "".join("<p class=\"warn\">%s</p>" % html.escape(w) for w in sw["warnings"])
    fills = result["_fills"]
    tr = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % tuple(
        html.escape(str(f[k])) for k in ("date", "symbol", "side", "qty", "price", "charges", "realized_pnl", "note")) for f in fills[:300])
    more = "<p>%d more rows in trades.csv</p>" % (len(fills) - 300) if len(fills) > 300 else ""
    cav = "".join("<li>%s</li>" % html.escape(c) for c in result["caveats"])
    params = html.escape(json.dumps(result["params"]))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest %(name)s</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--s1:#1f6feb;--s2:#9a6b00;--s3:#c0392b;--warn:#8a4b00}
@media (prefers-color-scheme: dark){:root{--bg:#141414;--fg:#ecebe7;--mut:#a3a19b;--line:#2c2b29;--s1:#6ea8ff;--s2:#e0b04a;--s3:#ff7b6b;--warn:#f0b35a}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px 16px}
main{max-width:960px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.sub{color:var(--mut);margin:0 0 16px}table{border-collapse:collapse;width:100%%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:500}
.wrap{overflow-x:auto}svg{width:100%%;height:auto;display:block;margin:8px 0}
svg .t{fill:var(--fg);font-size:13px}svg .a{fill:var(--mut);font-size:11px}svg .g{stroke:var(--line)}
svg .m{stroke:var(--mut);stroke-dasharray:4 4}svg .s1{stroke:var(--s1);stroke-width:1.6}svg .s2{stroke:var(--s2);stroke-width:1.3}
svg .s3{stroke:var(--s3);stroke-width:1.2}svg .s3-a{fill:var(--s3);opacity:.18}
.key span{display:inline-block;margin-right:16px;color:var(--mut)}.key i{display:inline-block;width:14px;height:3px;margin-right:6px;vertical-align:middle}
.warn{color:var(--warn)}li{margin:4px 0}
</style></head><body><main>
<h1>%(name)s</h1>
<p class="sub">%(strategy)s on %(symbols)s, params %(params)s, fills at %(fill)s + %(slip)s bps, tax %(tax)s. Generated %(gen)s.</p>
<div class="key"><span><i style="background:var(--s1)"></i>Strategy</span>%(bkey)s</div>
%(eq)s
%(dd)s
<h2>Results</h2><div class="wrap"><table>%(tbl)s</table></div>
%(extra)s
<h2>Caveats</h2><ul>%(cav)s</ul>
<h2>Orders</h2><div class="wrap"><table><tr><th>date</th><th>symbol</th><th>side</th><th>qty</th><th>price</th><th>charges</th><th>P&amp;L</th><th>why</th></tr>%(tr)s</table></div>%(more)s
<p class="sub">Education and tooling, not financial advice. Past performance does not predict future returns.</p>
</main></body></html>
""" % {"name": html.escape(result["name"]), "strategy": html.escape(result["strategy"]),
       "symbols": html.escape(", ".join(result["symbols"])), "params": params,
       "fill": "next open" if result["fill"] == "open" else "signal close", "slip": result["slippage_bps"],
       "tax": "on" if result["tax"] else "off", "gen": html.escape(result["generated"]),
       "bkey": ('<span><i style="background:var(--s2)"></i>Benchmark %s</span>' % html.escape(b.get("symbol", ""))) if bn else "",
       "eq": eq_svg, "dd": dd_svg, "tbl": "".join(row(k, v) for k, v in tbl), "extra": extra, "cav": cav, "tr": tr, "more": more}


def report_text(result):
    m = result["metrics"]
    L = []
    L.append("Backtest %s: %s on %s, %s to %s (%d trading days)." % (
        result["name"], result["strategy"], ", ".join(result["symbols"]), m["start"], m["end"], m["trading_days"]))
    L.append("  params %s; fills at %s + %s bps slippage; tax %s." % (
        json.dumps(result["params"]), "next open" if result["fill"] == "open" else "signal close",
        result["slippage_bps"], "on" if result["tax"] else "off"))
    L.append("  invested %s -> final %s (profit %s, %s)." % (inr(m["invested"]), inr(m["final_equity"]), inr(m["profit"]), pct(m["total_return"])))
    L.append("  CAGR %s   XIRR %s   max drawdown %s (peak %s, trough %s, recovered %s)" % (
        pct(m["cagr"]), pct(m["xirr"]), pct(m["max_drawdown"]), m["drawdown_peak"] or "-", m["drawdown_trough"] or "-",
        m["drawdown_recovery"] or "not yet"))
    L.append("  longest underwater %s calendar days%s; volatility %s; Sharpe %s; Sortino %s (risk-free %.2f %%)" % (
        m["longest_underwater_days"], " (still under at the end)" if m["longest_underwater_unrecovered"] else "",
        pct(m["volatility"]), num(m["sharpe"]), num(m["sortino"]), m["risk_free"] * 100))
    L.append("  %d orders, %d closed trades, win rate %s, avg win %s, avg loss %s, exposure %s" % (
        m["orders"], m["closed_trades"], pct(m["win_rate"], 1) if m["win_rate"] is not None else "n/a",
        inr(m["avg_win"]) if m["avg_win"] is not None else "n/a", inr(m["avg_loss"]) if m["avg_loss"] is not None else "n/a",
        pct(m["exposure"], 1)))
    L.append("  charges %s (%s); tax %s" % (inr(m["charges_paid"]), result["charges_label"], inr(m["tax_paid"])))
    b = result.get("benchmark")
    if b and b.get("symbol"):
        L.append("Benchmark %s (buy and hold, same cash flows, no charges): CAGR %s, max drawdown %s, XIRR %s; alpha %s, beta %s." % (
            b["symbol"], pct(b.get("cagr")), pct(b.get("max_drawdown")), pct(b.get("xirr")), pct(b.get("alpha")), num(b.get("beta"))))
    elif b and b.get("note"):
        L.append("Benchmark: %s." % b["note"])
    else:
        L.append("Benchmark: none (NIFTYBEES not cached; --benchmark SYMBOL to pick one).")
    if result.get("split"):
        s = result["split"]
        L.append("Walk-forward split at %s (%.0f %% in-sample):" % (s["split_date"], s["fraction"] * 100))
        for side in ("in_sample", "out_of_sample"):
            x = s[side]
            L.append("  %-14s %s to %s  CAGR %s  max DD %s  Sharpe %s  trades %d" % (
                side.replace("_", "-"), x["start"], x["end"], pct(x["cagr"]), pct(x["max_drawdown"]), num(x["sharpe"]), x["closed_trades"]))
    if result.get("sweep"):
        sw = result["sweep"]
        L.append("Sweep: %d combinations ranked by %s%s; median %s." % (
            sw["combinations"], sw["metric"], " in-sample" if sw.get("split_date") else "", num(sw["median"], 3)))
        for t in sw["table"][:10]:
            L.append("  %-34s %s %s  CAGR %s  max DD %s" % (json.dumps(t["params"]), sw["metric"], num(t["metric"], 3), pct(t["cagr"]), pct(t["max_drawdown"])))
        if sw.get("best_out_of_sample"):
            o = sw["best_out_of_sample"]
            L.append("  best out-of-sample from %s: %s %s, CAGR %s, max DD %s" % (sw["split_date"], sw["metric"], num(o["metric"], 3), pct(o["cagr"]), pct(o["max_drawdown"])))
        for w in sw["warnings"]:
            L.append("WARNING: " + w)
    if result["dropped_orders_at_end"]:
        L.append("Note: %d order(s) signalled on the last day had no next open to fill at." % result["dropped_orders_at_end"])
    for n in result["notes"]:
        L.append("Note: " + n)
    L.append("Caveats:")
    for c in result["caveats"]:
        L.append("  - " + c)
    return "\n".join(L)


# ---------------------------------------------------------------- commands

def cmd_run(a):
    result = run(a)
    if not a.no_write:
        if a.out:
            out = a.out
        else:
            stamp = datetime.datetime.now(IST).strftime("%Y%m%d-%H%M%S")
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in result["name"])
            out = os.path.join(os.path.abspath(a.root), "trader", "backtests", "%s-%s" % (safe, stamp))
        write_outputs(result, out)
        result["output_dir"] = shown(out)
    if a.json:
        print(json.dumps(summary_doc(result), indent=2, default=str))
    else:
        print(report_text(result))
        if result.get("output_dir"):
            print("Wrote %s (summary.json, trades.csv, equity.csv, report.html)." % result["output_dir"])
    return 0


def cmd_check(a):
    root = os.path.abspath(a.root)
    data_dir = a.data_dir or os.path.join(root, "trader", "data", "daily")
    out = []
    for s in a.symbols:
        try:
            bars, inf = load_symbol(data_dir, s)
        except BTError as e:
            out.append({"symbol": s.upper(), "error": str(e)})
            continue
        gaps = 0
        for i in range(1, len(bars)):
            if (bars[i]["date"] - bars[i - 1]["date"]).days > 7:
                gaps += 1
        inf["gaps_over_7_days"] = gaps
        out.append(inf)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    for inf in out:
        if "error" in inf:
            print("%s: %s" % (inf["symbol"], inf["error"]))
            continue
        print("%s: %d rows %s to %s, %s, %d gaps over 7 days." % (
            inf["symbol"], inf["rows"], inf["first"], inf["last"], "adjusted" if inf["adjusted"] else "UNADJUSTED",
            inf["gaps_over_7_days"]))
        for f in inf["corporate_action_flags"]:
            print("  likely corporate action %s: gap %+.1f %% (%s). Check nseindia.com corporate actions." % (
                f["date"], f["gap_pct"], f["likely"]))
    return 0


def cmd_strategies(a):
    print("Strategy types (all long-only CNC):")
    for k in sorted(STRATEGY_TYPES):
        t = STRATEGY_TYPES[k]
        print("  %-13s %s" % (k, t["about"]))
        print("  %-13s defaults %s" % ("", json.dumps(t["params"])))
    path = a.config or default_config_path(os.path.abspath(a.root))
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        print("Named strategies in %s:" % shown(path))
        for k, v in sorted((doc.get("strategies") or {}).items()):
            print("  %-22s %s %s %s" % (k, v.get("type"), ",".join(v.get("symbols") or []), json.dumps(v.get("params") or {})))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backtest.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run one strategy")
    r.add_argument("--strategy", choices=sorted(STRATEGIES))
    r.add_argument("--symbol", help="NSE symbol (comma list for rebalance)")
    r.add_argument("--param", action="append", help="strategy parameter name=value (repeatable)")
    r.add_argument("--weights", help="rebalance weights SYM=0.6,SYM=0.4")
    r.add_argument("--config", help="strategies JSON (default trader/strategies.json, else strategies.example.json)")
    r.add_argument("--name", help="named strategy in the config")
    r.add_argument("--from", dest="date_from", help="first day YYYY-MM-DD (earlier bars still warm up indicators)")
    r.add_argument("--to", dest="date_to", help="last day YYYY-MM-DD")
    r.add_argument("--capital", type=float, help="starting cash (default 1,00,000; 0 for sip)")
    r.add_argument("--fill", choices=("open", "close"), default="open", help="next day's open (default) or the signal close")
    r.add_argument("--slippage-bps", type=float, default=5.0, help="slippage per fill in basis points (default 5)")
    r.add_argument("--tax", action="store_true", help="apply STCG/LTCG on FIFO lots, paid at each FY end")
    r.add_argument("--rf", type=float, default=DEFAULT_RF, help="risk-free rate %% a year (default 6.5)")
    r.add_argument("--benchmark", default="auto", help="benchmark symbol, or none (default NIFTYBEES if cached)")
    r.add_argument("--split", type=float, help="walk-forward: in-sample fraction, e.g. 0.7")
    r.add_argument("--sweep", nargs="+", help="parameter grid, e.g. fast=10,20,50 slow=100,200")
    r.add_argument("--metric", default="sharpe", choices=SWEEP_METRICS, help="sweep ranking metric")
    r.add_argument("--auto-adjust", action="store_true", help="back-adjust likely split/bonus gaps in unadjusted data")
    r.add_argument("--root", default=".", help="project folder holding trader/ (default .)")
    r.add_argument("--data-dir", help="daily bars folder (default trader/data/daily)")
    r.add_argument("--out", help="output folder (default trader/backtests/<name>-<timestamp>)")
    r.add_argument("--no-write", action="store_true", help="print only, write nothing")
    r.add_argument("--json", action="store_true", help="print summary.json instead of text")
    c = sub.add_parser("check", help="data health and likely corporate actions")
    c.add_argument("symbols", nargs="+")
    c.add_argument("--root", default=".")
    c.add_argument("--data-dir")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("strategies", help="list strategy types and named strategies")
    s.add_argument("--config")
    s.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return {"run": cmd_run, "check": cmd_check, "strategies": cmd_strategies}[a.cmd](a)
    except BTError as e:
        print("backtest: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
