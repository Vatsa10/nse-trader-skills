#!/usr/bin/env python3
"""One-page HTML dashboard for the NSE trader pack (trader-report). Standard library only.

    python3 report.py build                                  # trader/report.html from whatever the other skills wrote
    python3 report.py build --symbol RELIANCE --symbol "M&M" # add a per-symbol page section
    python3 report.py build --sections market,scans --title "Morning" --out morning.html
    python3 report.py build --public                         # leave out holdings, proposed orders and risk verdicts

Reads (all optional, all under the project's trader/ folder):
  trader/data/index/nifty-50.csv, india-vix.csv   date,open,high,low,close,volume,pe,pb,div_yield (trader-data index)
  trader/data/breadth.csv                         date,pct_50,pct_200,...,mcclellan_osc (trader-scanner breadth)
  trader/data/flows.csv                           date,category,buy_cr,sell_cr,net_cr (FII/FPI and DII)
  trader/data/chain-NIFTY.json                    option chain summary (trader-data chain)
  trader/data/preopen.json                        pre-open snapshot (trader-data)
  trader/watchlist.json, trader/longterm.json     trader-scanner
  trader/data/daily/<SYM>.adj.csv or .csv         trader-data daily
  trader/data/delivery/<SYM>.csv                  trader-data delivery
  trader/data/actions.json                        trader-data actions
  trader/data/fundamentals/<SYM>.json             trader-invest fetch
  trader/thesis/*.json                            trader-thesis
  trader/plan.json, trader/orders.json            trader-exit, trader-budget (or trader-portfolio)
  trader/state/risk.json                          trader-risk verdicts
  trader/holdings.json                            trader-portfolio import
  trader/backtests/*/summary.json                 trader-backtest

Writes ONE self-contained HTML file: inline CSS, inline SVG charts, a few lines of JavaScript for tabs and
sortable, filterable tables. No external script, font or network. A missing input leaves its section out and
lists the command that would fill it in the footer. Every number comes from an input file; nothing is invented.
Education and tooling, not financial advice. It never places an order.
"""

import argparse
import csv
import datetime
import glob
import html
import json
import os
import sys

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

MAX_POINTS = 240          # longer series are downsampled to this many points
PE_EXPENSIVE = 25.0       # rule of thumb for the Nifty 50 P/E
PE_CHEAP = 20.0
W, H = 640, 200
ML, MR, MT, MB = 46, 12, 16, 24

ALL_SECTIONS = ["market", "news", "scans", "study", "symbols", "thesis", "plan", "portfolio", "backtest"]
TITLES = {"market": "Market", "news": "News", "scans": "Scans", "study": "Pattern study", "symbols": "Symbols",
          "thesis": "Thesis", "plan": "Plan and risk", "portfolio": "Portfolio", "backtest": "Backtest"}
PRIVATE = ("portfolio",)   # whole sections; --public also drops orders and risk verdicts from Plan

esc = html.escape


class Missing(Exception):
    """A section with nothing to show. .cmds is the list of commands that would fill it."""

    def __init__(self, cmds):
        Exception.__init__(self, "; ".join(cmds))
        self.cmds = cmds


# ---------------------------------------------------------------- reading

class Ctx(object):
    def __init__(self, data, trader, symbols, public=False):
        self.data, self.trader, self.symbols, self.public = data, trader, symbols, public
        self.notes = []       # skipped or unreadable inputs
        self.sources = []     # (section, path, as_of)

    def src(self, section, path, as_of):
        self.sources.append((section, path.replace(os.sep, "/"), as_of or ""))

    def read_json(self, path):
        """-> parsed JSON or None (absent, or corrupt with a note)."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (ValueError, OSError) as e:
            self.notes.append("%s skipped: %s" % (path.replace(os.sep, "/"), str(e)[:120]))
            return None

    def read_csv(self, path):
        """-> list of dict rows, or None (absent, or unreadable with a note)."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                rows = [dict((k.strip().lower(), (v or "").strip()) for k, v in r.items() if k)
                        for r in csv.DictReader(fh)]
        except (csv.Error, OSError, UnicodeDecodeError) as e:
            self.notes.append("%s skipped: %s" % (path.replace(os.sep, "/"), str(e)[:120]))
            return None
        return rows or None


def num(v):
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def fmt(v, nd=2):
    if v is None or v == "":
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if not isinstance(v, float):
            return str(v)
        t = "%.*f" % (nd, v)
        return t.rstrip("0").rstrip(".") if "." in t else t
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x, nd) for x in v)
    return str(v)


def downsample(items, n=MAX_POINTS):
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / float(n - 1)
    return [items[int(round(i * step))] for i in range(n)]


def dates_of(rows):
    return [r.get("date", "") for r in rows]


# ---------------------------------------------------------------- SVG charts

def scale_points(values, lo, hi, x0=ML, x1=W - MR, y0=MT, y1=H - MB):
    """Values -> 'x,y x,y ...' for a polyline. A None is left out; x follows the index."""
    n = len(values)
    span = (hi - lo) or 1.0
    out = []
    for i, v in enumerate(values):
        if v is None:
            continue
        x = (x0 + x1) / 2.0 if n == 1 else x0 + i * (x1 - x0) / float(n - 1)
        y = y1 - (v - lo) / span * (y1 - y0)
        out.append("%.1f,%.1f" % (x, y))
    return " ".join(out)


def table_fallback(labels, series):
    head = "".join("<th>%s</th>" % esc(s["name"]) for s in series)
    body = "".join("<tr><td>%s</td>%s</tr>" % (esc(str(lab)), "".join(
        "<td>%s</td>" % esc(fmt(s["values"][i])) for s in series)) for i, lab in enumerate(labels))
    return ('<details><summary>Data table</summary><div class="wrap"><table><tr><th></th>%s</tr>%s</table></div>'
            "</details>" % (head, body))


def line_chart(title, desc, labels, series, hlines=(), pad=0.05):
    """series: [{"name","values","cls"}]; hlines: [(value, label, cls)]. -> inline SVG + table fallback."""
    labels = list(labels)
    allv = [v for s in series for v in s["values"] if v is not None] + [h[0] for h in hlines]
    if not allv:
        return ""
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi, lo = hi + 1.0, lo - 1.0
    p = (hi - lo) * pad
    lo, hi = lo - p, hi + p
    parts = ['<svg viewBox="0 0 %d %d" role="img">' % (W, H),
             "<title>%s</title><desc>%s</desc>" % (esc(title), esc(desc))]
    for frac in (0.0, 0.5, 1.0):
        y = H - MB - frac * (H - MB - MT)
        parts.append('<line class="g" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ML, W - MR, y, y))
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ML - 4, y + 4, esc(fmt(lo + frac * (hi - lo), 1))))
    for value, label, cls in hlines:
        y = H - MB - (value - lo) / (hi - lo) * (H - MB - MT)
        parts.append('<line class="m %s" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (cls, ML, W - MR, y, y))
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">%s %s</text>' % (W - MR - 2, y - 3, esc(label), esc(fmt(value, 1))))
    for s in series:
        pts = scale_points(s["values"], lo, hi)
        if pts:
            parts.append('<polyline class="ln %s" fill="none" points="%s"/>' % (s["cls"], pts))
    if labels:
        parts.append('<text class="a" x="%d" y="%d">%s</text>' % (ML, H - 6, esc(labels[0])))
        parts.append('<text class="a" x="%d" y="%d" text-anchor="end">%s</text>' % (W - MR, H - 6, esc(labels[-1])))
    parts.append("</svg>")
    key = "".join('<span><i class="%s"></i>%s</span>' % (s["cls"], esc(s["name"])) for s in series)
    return ('<figure><figcaption>%s</figcaption><div class="key">%s</div>%s%s</figure>'
            % (esc(title), key, "".join(parts), table_fallback(labels, series)))


def bar_chart(title, desc, labels, series, ref=None):
    """Grouped bars from a zero baseline; series: [{"name","values","cls"}]."""
    labels = list(labels)
    allv = [v for s in series for v in s["values"] if v is not None] + [0.0] + ([ref[0]] if ref else [])
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi = lo + 1.0
    n, k = len(labels), len(series)
    if not n or k == 0:
        return ""
    plot_w = W - ML - MR
    slot = plot_w / float(n)
    bw = max(slot * 0.8 / k, 1.0)

    def ypos(v):
        return H - MB - (v - lo) / (hi - lo) * (H - MB - MT)
    parts = ['<svg viewBox="0 0 %d %d" role="img">' % (W, H),
             "<title>%s</title><desc>%s</desc>" % (esc(title), esc(desc))]
    for v in (lo, hi):
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">%s</text>' % (ML - 4, ypos(v) + 4, esc(fmt(v, 1))))
    parts.append('<line class="g" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ML, W - MR, ypos(0), ypos(0)))
    if ref:
        parts.append('<line class="m" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (ML, W - MR, ypos(ref[0]), ypos(ref[0])))
        parts.append('<text class="a" x="%d" y="%.1f" text-anchor="end">%s</text>' % (W - MR - 2, ypos(ref[0]) - 3, esc(ref[1])))
    for j, s in enumerate(series):
        for i, v in enumerate(s["values"]):
            if v is None:
                continue
            x = ML + i * slot + slot * 0.1 + j * bw
            y0, y1 = ypos(max(v, 0.0)), ypos(min(v, 0.0))
            parts.append('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f"><title>%s %s: %s</title></rect>'
                         % (s["cls"], x, y0, bw, max(y1 - y0, 0.5), esc(labels[i]), esc(s["name"]), esc(fmt(v))))
    parts.append('<text class="a" x="%d" y="%d">%s</text>' % (ML, H - 6, esc(labels[0])))
    parts.append('<text class="a" x="%d" y="%d" text-anchor="end">%s</text>' % (W - MR, H - 6, esc(labels[-1])))
    parts.append("</svg>")
    key = "".join('<span><i class="%s"></i>%s</span>' % (s["cls"], esc(s["name"])) for s in series)
    return ('<figure><figcaption>%s</figcaption><div class="key">%s</div>%s%s</figure>'
            % (esc(title), key, "".join(parts), table_fallback(labels, series)))


# ---------------------------------------------------------------- html pieces

def kv_table(pairs):
    return '<div class="wrap"><table class="kv">%s</table></div>' % "".join(
        "<tr><th>%s</th><td>%s</td></tr>" % (esc(k), esc(fmt(v))) for k, v in pairs)


def data_table(headers, rows, sortable=True):
    """rows: lists of cells; a cell is text or (text, sort_value)."""
    def cell(c):
        if isinstance(c, tuple):
            return '<td data-v="%s">%s</td>' % (esc(str(c[1])), esc(fmt(c[0])))
        return "<td>%s</td>" % esc(fmt(c))
    head = "".join("<th>%s</th>" % esc(h) for h in headers)
    body = "".join("<tr>%s</tr>" % "".join(cell(c) for c in r) for r in rows)
    return ('<div class="wrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % ("sortable" if sortable else "", head, body))


def numcell(v, nd=2):
    x = num(v)
    return (fmt(x, nd), x) if x is not None else ("-", "")


def card(label, value, note=""):
    return '<div class="card"><span>%s</span><b>%s</b><small>%s</small></div>' % (esc(label), esc(value), esc(note))


def source_line(path, as_of):
    return '<p class="src">Source: <code>%s</code>%s</p>' % (
        esc(path.replace(os.sep, "/")), (", as of %s" % esc(as_of)) if as_of else "")


# ---------------------------------------------------------------- sections

def last_valid(rows, key):
    for r in reversed(rows):
        if num(r.get(key)) is not None:
            return r
    return None


def series_of(rows, key):
    return [num(r.get(key)) for r in rows]


def section_market(c):
    out, ran = [], False
    cards = []
    d = os.path.join(c.data, "index")
    nifty = c.read_csv(os.path.join(d, "nifty-50.csv"))
    vix = c.read_csv(os.path.join(d, "india-vix.csv"))
    if nifty:
        ran = True
        p = os.path.join(d, "nifty-50.csv")
        rows = sorted(nifty, key=lambda r: r.get("date", ""))
        last = last_valid(rows, "close")
        if last:
            prev = [r for r in rows if r["date"] < last["date"] and num(r.get("close")) is not None]
            chg = ""
            if prev:
                a, b = num(last["close"]), num(prev[-1]["close"])
                chg = "%+.2f%% on %s" % ((a / b - 1) * 100, prev[-1]["date"]) if b else ""
            cards.append(card("Nifty 50 close", fmt(num(last["close"])), "%s %s" % (last["date"], chg)))
        pe = last_valid(rows, "pe")
        if pe:
            v = num(pe["pe"])
            band = "expensive (>= %g)" % PE_EXPENSIVE if v >= PE_EXPENSIVE else (
                "cheap (<= %g)" % PE_CHEAP if v <= PE_CHEAP else "in between (%g to %g)" % (PE_CHEAP, PE_EXPENSIVE))
            cards.append(card("Nifty 50 P/E", fmt(v), "%s: %s, rule of thumb, not a signal" % (pe["date"], band)))
        c.src("market", p, last["date"] if last else "")
        shown = downsample(rows[-750:])
        out.append(line_chart("Nifty 50 close", "Nifty 50 closing level, %s to %s" % (shown[0]["date"], shown[-1]["date"]),
                              dates_of(shown), [{"name": "Close", "values": series_of(shown, "close"), "cls": "s1"}]))
    if vix:
        ran = True
        p = os.path.join(d, "india-vix.csv")
        rows = sorted(vix, key=lambda r: r.get("date", ""))
        last = last_valid(rows, "close")
        if last:
            cards.append(card("India VIX", fmt(num(last["close"])), last["date"]))
        c.src("market", p, last["date"] if last else "")
        shown = downsample(rows[-750:])
        out.append(line_chart("India VIX", "India VIX closing level, %s to %s" % (shown[0]["date"], shown[-1]["date"]),
                              dates_of(shown), [{"name": "VIX close", "values": series_of(shown, "close"), "cls": "s2"}]))
    br = c.read_csv(os.path.join(c.data, "breadth.csv"))
    if br:
        ran = True
        rows = sorted(br, key=lambda r: r.get("date", ""))
        shown = downsample(rows[-500:])
        c.src("market", os.path.join(c.data, "breadth.csv"), rows[-1].get("date"))
        out.append(line_chart("Breadth: share of stocks above their 50 and 200-day average (%)",
                              "Percent of scanned stocks closing above the 50 and 200-day moving average",
                              dates_of(shown), [{"name": "above 50-DMA", "values": series_of(shown, "pct_50"), "cls": "s1"},
                                                {"name": "above 200-DMA", "values": series_of(shown, "pct_200"), "cls": "s2"}]))
        if any(v is not None for v in series_of(shown, "mcclellan_osc")):
            out.append(line_chart("McClellan oscillator", "McClellan oscillator from the advance and decline counts",
                                  dates_of(shown), [{"name": "McClellan", "values": series_of(shown, "mcclellan_osc"), "cls": "s3"}],
                                  hlines=[(0.0, "zero", "")]))
    fl = c.read_csv(os.path.join(c.data, "flows.csv"))
    if fl:
        ran = True
        by = {}
        for r in fl:
            cat = r.get("category", "").upper()
            key = "FII" if cat.startswith("FII") else ("DII" if cat.startswith("DII") else None)
            if key and r.get("date") and num(r.get("net_cr")) is not None:
                by.setdefault(r["date"], {})[key] = num(r["net_cr"])
        days = sorted(by)[-20:]
        if days:
            c.src("market", os.path.join(c.data, "flows.csv"), days[-1])
            out.append(bar_chart("FII and DII net flows (Rs crore, last %d sessions)" % len(days),
                                 "Daily net buying (positive) or selling (negative) by FII/FPI and DII in rupees crore",
                                 days, [{"name": "FII/FPI", "values": [by[x].get("FII") for x in days], "cls": "s1"},
                                        {"name": "DII", "values": [by[x].get("DII") for x in days], "cls": "s2"}]))
    ch = os.path.join(c.data, "chain-NIFTY.json")
    chain = c.read_json(ch)
    if isinstance(chain, dict):
        ran = True
        keys = [k for k in ("underlying", "expiry", "spot", "atm", "pcr_oi", "pcr_change_oi", "max_pain",
                            "max_call_oi_strike", "max_put_oi_strike") if k in chain]
        c.src("market", ch, str(chain.get("as_of") or chain.get("fetched_at") or chain.get("timestamp") or ""))
        out.append("<h3>Option chain, Nifty</h3>" + kv_table([(k, chain[k]) for k in keys]))
    po = os.path.join(c.data, "preopen.json")
    pre = c.read_json(po)
    if isinstance(pre, dict) and isinstance(pre.get("stocks"), list):
        ran = True
        stocks = [s for s in pre["stocks"] if isinstance(s, dict) and num(s.get("change_pct")) is not None]
        stocks.sort(key=lambda s: -abs(num(s["change_pct"])))
        c.src("market", po, str(pre.get("date") or pre.get("timestamp") or ""))
        out.append("<h3>Pre-open top gaps (advances %s, declines %s)</h3>" % (esc(fmt(pre.get("advances"))), esc(fmt(pre.get("declines"))))
                   + data_table(["symbol", "indicative open", "prev close", "change %"],
                                [[s.get("symbol", ""), numcell(s.get("iep")), numcell(s.get("prev_close")), numcell(s.get("change_pct"))]
                                 for s in stocks[:10]]))
    if not ran:
        raise Missing(["python3 trader-data/data.py index", "python3 trader-scanner/scan.py breadth",
                       "python3 trader-data/data.py chain NIFTY", "python3 trader-data/data.py flows",
                       "python3 trader-data/data.py preopen"])
    return ('<div class="cards">%s</div>' % "".join(cards) if cards else "") + "".join(out)


WATCH_COLS = ["symbol", "setups", "pattern", "state", "price", "level", "stop", "target", "risk_pct",
              "swing_trend", "coc", "dq", "close_date"]
LONG_COLS = ["symbol", "screens", "price", "swing_trend", "swing_coc", "mansfield_rs", "dq", "from_high_pct",
             "vs_200dma_pct", "rsi", "ret_1y_pct"]


def hits_table(hits, cols):
    cols = [k for k in cols if any(isinstance(h, dict) and h.get(k) is not None for h in hits)]
    rows = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        row = []
        for k in cols:
            v = h.get(k)
            row.append(numcell(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else
                       (fmt(v) if v is not None else "-"))
        rows.append(row)
    return ('<input class="flt" type="search" placeholder="filter rows" aria-label="filter rows">'
            + data_table(cols, rows))


def section_scans(c):
    out, ran = [], False
    for name, cols, cmds in (("watchlist.json", WATCH_COLS, "python3 trader-scanner/scan.py daily"),
                             ("longterm.json", LONG_COLS, "python3 trader-scanner/scan.py longterm")):
        p = os.path.join(c.trader, name)
        doc = c.read_json(p)
        hits = doc.get("hits") if isinstance(doc, dict) else (doc if isinstance(doc, list) else None)
        if not isinstance(hits, list):
            if doc is not None:
                c.notes.append("%s skipped: no hits list" % p.replace(os.sep, "/"))
            continue
        ran = True
        mode = doc.get("mode", "") if isinstance(doc, dict) else ""
        as_of = (doc.get("as_of") or doc.get("created_at") or "") if isinstance(doc, dict) else ""
        c.src("scans", p, str(as_of))
        out.append("<h3>%s (%d hits)</h3>" % (esc(("Watchlist" if name.startswith("watch") else "Long-term") + (", mode " + mode if mode else "")), len(hits)))
        out.append(hits_table(hits, cols) if hits else "<p>No hits in this scan.</p>")
    if not ran:
        raise Missing(["python3 trader-scanner/scan.py daily", "python3 trader-scanner/scan.py longterm"])
    return "".join(out)


def moving_average(values, n):
    out, tot, q = [], 0.0, []
    for v in values:
        q.append(v)
        tot += v
        if len(q) > n:
            tot -= q.pop(0)
        out.append(tot / n if len(q) == n else None)
    return out


def load_daily(c, sym):
    for name in (sym + ".adj.csv", sym + ".csv"):
        p = os.path.join(c.data, "daily", name)
        rows = c.read_csv(p)
        if rows:
            rows = [r for r in rows if num(r.get("close")) is not None and r.get("date")]
            if rows:
                return p, sorted(rows, key=lambda r: r["date"])
    return None, None


def symbol_block(c, sym, watch, longterm, actions):
    parts = ["<h3>%s</h3>" % esc(sym)]
    p, rows = load_daily(c, sym)
    levels = []
    for doc, tag in ((watch, "watch"), (longterm, "long-term")):
        for h in (doc.get("hits") if isinstance(doc, dict) else None) or []:
            if isinstance(h, dict) and h.get("symbol") == sym:
                for key, label in (("level", "level"), ("stop", "stop"), ("target", "target"),
                                   ("coc", "coc"), ("swing_coc", "coc")):
                    if num(h.get(key)) is not None and (num(h[key]), label) not in [(a, b) for a, b, _ in levels]:
                        levels.append((num(h[key]), label, "s3" if label in ("stop", "coc") else "s2"))
    if rows:
        closes = [num(r["close"]) for r in rows]
        dma = moving_average(closes, 200)
        yr = rows[-252:]
        hi = max(num(r.get("high")) or num(r["close"]) for r in yr)
        lo = min(num(r.get("low")) or num(r["close"]) for r in yr)
        cut = len(rows) - min(len(rows), 260)
        idx = downsample(list(range(cut, len(rows))))
        c.src("symbols", p, rows[-1]["date"])
        parts.append(line_chart("%s close, 200-day average, 52-week band" % sym,
                                "%s closing price with its 200-day average (computed here), 52-week high and low and any scanner levels" % sym,
                                [rows[i]["date"] for i in idx],
                                [{"name": "Close", "values": [closes[i] for i in idx], "cls": "s1"},
                                 {"name": "200-DMA", "values": [dma[i] for i in idx], "cls": "s2"}],
                                hlines=[(hi, "52w high", "band"), (lo, "52w low", "band")] + levels))
    else:
        parts.append("<p>No daily bars for %s. Run <code>python3 trader-data/data.py daily %s</code>.</p>" % (esc(sym), esc(sym)))
    dp = os.path.join(c.data, "delivery", sym + ".csv")
    dr = c.read_csv(dp)
    if dr:
        dr = sorted(dr, key=lambda r: r.get("date", ""))[-30:]
        vals = [num(r.get("deliv_qty")) for r in dr]
        present = [v for v in vals if v is not None]
        if len(present) >= 5:
            mean = sum(present) / len(present)
            dq = [(v / mean if v is not None and mean > 0 else None) for v in vals]
            c.src("symbols", dp, dr[-1].get("date"))
            parts.append(bar_chart("%s delivery DQ (delivered quantity over its %d-session mean)" % (sym, len(dr)),
                                   "Each day's delivered quantity divided by the mean over the window; 1 is average",
                                   dates_of(dr), [{"name": "DQ", "values": dq, "cls": "s1"}], ref=(1.0, "1.0 = average")))
    ent = ((actions or {}).get("symbols") or {}).get(sym) if isinstance(actions, dict) else None
    if isinstance(ent, dict) and ent.get("actions"):
        acts = sorted([a for a in ent["actions"] if isinstance(a, dict)], key=lambda a: a.get("ex_date", ""), reverse=True)[:12]
        parts.append("<h4>Corporate actions</h4>" + data_table(
            ["ex-date", "kind", "factor", "dividend", "subject"],
            [[a.get("ex_date"), a.get("kind"), a.get("factor"), a.get("dividend"), a.get("subject")] for a in acts], False))
    fp = os.path.join(c.data, "fundamentals", sym + ".json")
    fund = c.read_json(fp)
    if isinstance(fund, dict):
        keys = ("price", "pe", "pb", "roe_pct", "roce_pct", "debt_to_equity", "promoter_pct", "div_yield_pct", "sector")
        c.src("symbols", fp, str(fund.get("as_of") or ""))
        parts.append("<h4>Fundamentals (trader-invest fetch)</h4>" + kv_table([(k, fund[k]) for k in keys if k in fund]))
    ap = os.path.join(c.trader, "invest", sym + ".json")
    an = c.read_json(ap)
    if isinstance(an, dict) and "score" in an:
        c.src("symbols", ap, str(an.get("as_of") or ""))
        pil = [("%s pillar" % k, (v or {}).get("score")) for k, v in sorted((an.get("pillars") or {}).items())]
        parts.append("<h4>Long-term scorecard (trader-invest analyse)</h4>" + kv_table(
            [("score", an.get("score")), ("verdict", an.get("verdict")), ("coverage %", an.get("coverage_pct"))] + pil))
    return "".join(parts)


def section_symbols(c):
    if not c.symbols:
        raise Missing(["python3 trader-report/report.py build --symbol RELIANCE   (name the symbols to chart)"])
    watch = c.read_json(os.path.join(c.trader, "watchlist.json"))
    longterm = c.read_json(os.path.join(c.trader, "longterm.json"))
    actions = c.read_json(os.path.join(c.data, "actions.json"))
    if actions is not None:
        c.src("symbols", os.path.join(c.data, "actions.json"), str((actions or {}).get("fetched_at") or "") if isinstance(actions, dict) else "")
    return "".join(symbol_block(c, s, watch, longterm, actions) for s in c.symbols)


def rich_item(x):
    """One list entry: a string as is; a thesis item ({point|idea|what, ...}) as its headline with the other
    fields in a muted line, so a case reads as sentences, not as JSON."""
    if isinstance(x, str):
        return esc(x)
    if isinstance(x, dict):
        head_key = next((k for k in ("point", "idea", "what") if isinstance(x.get(k), str)), None)
        rest = ["%s: %s" % (k.replace("_", " "), v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                for k, v in x.items() if k != head_key and v not in (None, "", [], {})]
        if head_key:
            return esc(x[head_key]) + ('<br><span class="src">%s</span>' % esc("; ".join(rest)) if rest else "")
    return esc(json.dumps(x, ensure_ascii=False))


def rich(v):
    if isinstance(v, str):
        return "<p>%s</p>" % esc(v)
    if isinstance(v, list):
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % rich_item(x) for x in v)
    if isinstance(v, dict):
        return "<ul>%s</ul>" % "".join("<li><b>%s</b>: %s</li>" % (esc(str(k)), esc(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False))) for k, x in v.items())
    return "<p>%s</p>" % esc(fmt(v))


def section_thesis(c):
    out = []
    for p in sorted(glob.glob(os.path.join(c.trader, "thesis", "*.json"))):
        if p.endswith("-brief.json"):
            continue   # trader-thesis's evidence brief, not a finished thesis
        doc = c.read_json(p)
        if not isinstance(doc, dict):
            continue
        keys = [(k, label) for k, label in (("verdict", "Verdict"), ("bull_case", "Bull case"), ("bear_case", "Bear case"),
                                              ("traps", "Traps"), ("watch", "What to watch"))
                if doc.get(k) not in (None, "", [], {})]
        if not keys:
            c.notes.append("%s skipped: no verdict, bull_case, bear_case or traps" % p.replace(os.sep, "/"))
            continue
        name = str(doc.get("symbol") or os.path.splitext(os.path.basename(p))[0])
        c.src("thesis", p, str(doc.get("as_of") or doc.get("date") or doc.get("created_at") or ""))
        out.append("<details open><summary>%s</summary>%s</details>" % (
            esc(name), "".join("<h4>%s</h4>%s" % (esc(label), rich(doc[k])) for k, label in keys)))
    if not out:
        raise Missing(["run trader-thesis for a symbol (writes trader/thesis/<SYM>.json)"])
    return "".join(out)


def section_plan(c):
    out, ran = [], False
    p = os.path.join(c.trader, "plan.json")
    plan = c.read_json(p)
    if isinstance(plan, dict):
        ran = True
        keys = ("symbol", "side", "setup", "product", "entry", "stop", "target", "risk_per_share", "reward_risk",
                "level", "flat_by", "date")
        c.src("plan", p, str(plan.get("date") or plan.get("created_at") or ""))
        pairs = [(k, plan[k]) for k in keys if k in plan]
        cost = plan.get("costs")
        if isinstance(cost, dict):
            pairs += [("qty used for costs", cost.get("qty")), ("charges at stop", cost.get("at_stop")), ("charges at target", cost.get("at_target"))]
        out.append("<h3>Trade plan</h3>" + kv_table(pairs))
    rp = os.path.join(c.trader, "state", "risk.json")
    risk = None if c.public else c.read_json(rp)
    if isinstance(risk, dict) and isinstance(risk.get("results"), list):
        ran = True
        c.src("plan", rp, str(risk.get("checked_at") or risk.get("date") or ""))
        out.append("<h3>Risk verdicts (%s passed, %s blocked%s)</h3>" % (esc(fmt(risk.get("pass"))), esc(fmt(risk.get("block"))),
                                                                       ", paper account" if risk.get("paper") else ""))
        out.append(data_table(["symbol", "side", "amount", "verdict", "max loss", "reasons"],
                              [[r.get("symbol"), r.get("side"), r.get("amount"), r.get("verdict"), numcell(r.get("max_loss")),
                                "; ".join(str(x) for x in (r.get("reasons") or []))] for r in risk["results"] if isinstance(r, dict)]))
    op = os.path.join(c.trader, "orders.json")
    orders = None if c.public else c.read_json(op)
    if c.public and (os.path.exists(rp) or os.path.exists(op)):
        out.append("<p class=\"note\">Left out on purpose (--public): proposed orders and risk verdicts.</p>")
    if isinstance(orders, dict) and isinstance(orders.get("orders"), list):
        ran = True
        c.src("plan", op, str(orders.get("created") or orders.get("created_at") or ""))
        out.append("<h3>Proposed orders (%s, nothing placed)</h3>" % esc(fmt(orders.get("status") or "proposal")))
        out.append(data_table(["symbol", "side", "qty", "product", "type", "stop", "target"],
                              [[o.get("symbol"), o.get("side"), o.get("qty"), o.get("product"), o.get("type"),
                                o.get("stop_loss_stop_price"), o.get("take_profit_limit_price")]
                               for o in orders["orders"] if isinstance(o, dict)]))
    ep = os.path.join(c.trader, "entry.json")
    entry = c.read_json(ep)
    if isinstance(entry, dict) and entry.get("symbol"):
        ran = True
        c.src("plan", ep, str(entry.get("date") or entry.get("now") or ""))
        keys = ("symbol", "setup", "status", "time", "price", "level", "or_high", "or_low", "date")
        out.insert(0, "<h3>Entry signal (trader-entry)</h3>" + kv_table([(k, entry[k]) for k in keys if k in entry]))
    tp = os.path.join(c.trader, "timemachine.json")
    tm = c.read_json(tp)
    if isinstance(tm, dict) and isinstance(tm.get("mc"), dict):
        ran = True
        mc = tm["mc"]
        c.src("plan", tp, str(tm.get("date") or tm.get("created_at") or ""))
        pairs = [("target first", mc.get("p_target")), ("stop first", mc.get("p_stop")),
                 ("neither by flat-by", mc.get("p_neither")), ("average R", mc.get("avg_R")), ("verdict", tm.get("verdict"))]
        out.append("<h3>Time machine (%s replays)</h3>" % esc(fmt(mc.get("replays") or tm.get("replays") or "")) +
                   kv_table([(k, v) for k, v in pairs if v is not None]))
    if not ran:
        raise Missing(["python3 trader-exit/exit.py plan ... --write", "python3 trader-budget/size.py --write",
                       "python3 trader-risk/check.py"])
    return "".join(out)


def section_news(c):
    """The last trader-news briefing (trader/news.json): results soon, the board calendar, deals, moves, headlines."""
    p = os.path.join(c.trader, "news.json")
    doc = c.read_json(p)
    if not isinstance(doc, dict):
        raise Missing(["python3 trader-news/news.py (saves trader/news.json)"])
    c.src("news", p, str(doc.get("checked") or ""))
    out = []
    soon = doc.get("results_soon") or []
    if soon:
        out.append('<p class="warn"><b>Results soon (gap risk):</b> %s</p>' % esc(", ".join(soon)))
    cal = [e for e in doc.get("calendar") or [] if isinstance(e, dict)]
    if cal:
        out.append("<h3>Board meetings</h3>" + data_table(
            ["symbol", "date", "purpose", "sessions away", "soon"],
            [[e.get("ticker"), e.get("date"), e.get("purpose"), e.get("sessions_away"), "yes" if e.get("soon") else ""]
             for e in cal], False))
    deals = [d for d in doc.get("deal_flow") or [] if isinstance(d, dict)]
    if deals:
        out.append("<h3>Bulk, block and short deals (net by client)</h3>" + data_table(
            ["symbol", "client", "bought", "sold", "net", "% of 20-day avg volume"],
            [[d.get("symbol"), d.get("client"), numcell(d.get("bought"), 0), numcell(d.get("sold"), 0),
              numcell(d.get("net"), 0), numcell(d.get("pct_of_avg_volume"))] for d in deals]))
    fl = doc.get("flows")
    if isinstance(fl, dict) and fl:
        out.append("<h3>FII/DII (Rs crore)</h3>" + kv_table([(k, v) for k, v in fl.items() if not isinstance(v, (dict, list))]))
    moves = [m for m in doc.get("moves") or [] if isinstance(m, dict)]
    if moves:
        out.append("<h3>Moves vs yesterday's close</h3>" + data_table(
            ["symbol", "price", "change %", "moving"],
            [[m.get("ticker"), numcell(m.get("price")), numcell(m.get("pct")), "yes" if m.get("moving") else ""]
             for m in moves]))
    items = [i for i in doc.get("items") or [] if isinstance(i, dict)]
    if items:
        rows = []
        for i in items[:60]:
            link = i.get("link") or ""
            title = esc(str(i.get("title") or ""))
            if link.startswith(("https://", "http://")):
                title = '<a href="%s" rel="noopener noreferrer" target="_blank">%s</a>' % (esc(link, quote=True), title)
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                esc(str(i.get("time") or i.get("published") or "")), esc(str(i.get("ticker") or "")),
                esc(str(i.get("source") or "")), title, esc(", ".join(i.get("tags") or []))))
        out.append('<h3>Headlines (%d)</h3><input class="flt" placeholder="filter headlines" aria-label="filter headlines">'
                   '<table class="sortable"><thead><tr><th>time</th><th>symbol</th><th>source</th><th>headline</th>'
                   '<th>tags</th></tr></thead><tbody>%s</tbody></table>' % (len(items), "".join(rows)))
    notes = [n for n in doc.get("notes") or [] if isinstance(n, str)]
    if notes:
        out.append("<h3>Notes</h3><ul>%s</ul>" % "".join("<li>%s</li>" % esc(n) for n in notes))
    return "".join(out) or "<p>Nothing new in the last briefing.</p>"


def section_study(c):
    """trader-scanner patterns --study (trader/data/pattern-study.json): how each pattern did in history."""
    p = os.path.join(c.data, "pattern-study.json")
    doc = c.read_json(p)
    if not isinstance(doc, dict) or not isinstance(doc.get("patterns"), dict):
        raise Missing(["python3 trader-scanner/scan.py patterns --study"])
    c.src("study", p, str(doc.get("split_date") or ""))
    rows = []
    for pat, segs in doc["patterns"].items():
        for seg in ("in", "out", "all"):
            s = (segs or {}).get(seg)
            if isinstance(s, dict):
                ex = s.get("exits") or {}
                rows.append([pat, {"in": "in-sample", "out": "out-of-sample", "all": "all"}[seg], s.get("trades"),
                             numcell(s.get("win_pct")), numcell(s.get("exp_r")), numcell(s.get("se_r")),
                             numcell(s.get("pf")), numcell(s.get("control_r")), numcell(s.get("lift_r")),
                             numcell(s.get("median_risk_pct")),
                             "%s/%s/%s" % (ex.get("target", 0), ex.get("stop", 0), ex.get("time", 0))])
    opts = doc.get("options") or {}
    head = ('<p class="src">%s stocks; buy the next open after a close over the level; out at the stop, the target or '
            '%s sessions; %s%% costs. Split at %s: tune on in-sample, judge on out-of-sample. Expectancy in R '
            '(1 R = entry - stop); lift is over random entries with the same risk.</p>' % (
                esc(fmt(doc.get("symbols"))), esc(fmt(opts.get("horizon"))), esc(fmt(opts.get("cost_pct"))),
                esc(str(doc.get("split_date") or "?"))))
    if doc.get("params"):
        head += '<p class="src">Overrides: %s</p>' % esc(json.dumps(doc["params"]))
    return head + data_table(["pattern", "segment", "trades", "win %", "expect R", "+/- SE", "PF", "random R", "lift R",
                              "median risk %", "target/stop/time"], rows) + '<p class="src">%s</p>' % esc(doc.get("note") or "")


def section_portfolio(c):
    p = os.path.join(c.trader, "holdings.json")
    doc = c.read_json(p)
    if not isinstance(doc, dict) or not isinstance(doc.get("holdings"), list):
        raise Missing(["python3 trader-portfolio/portfolio.py import KITE_OR_CONSOLE_FILE"])
    c.src("portfolio", p, str(doc.get("as_of") or ""))
    rows, tot_v, tot_i = [], 0.0, 0.0
    for h in doc["holdings"]:
        if not isinstance(h, dict):
            continue
        q, avg, last = num(h.get("qty")), num(h.get("avg_price")), num(h.get("last_price"))
        val = q * last if q is not None and last is not None else None
        inv = q * avg if q is not None and avg is not None else None
        if val is not None and inv is not None:
            tot_v, tot_i = tot_v + val, tot_i + inv
        rows.append([h.get("symbol"), numcell(q, 0), numcell(avg), numcell(last), numcell(inv, 0), numcell(val, 0),
                     numcell(val - inv if val is not None and inv is not None else None, 0),
                     numcell((last / avg - 1) * 100 if last is not None and avg else None, 1)])
    head = [("Source", doc.get("source")), ("Cash", doc.get("cash")),
            ("Priced holdings: invested", round(tot_i, 2)), ("Priced holdings: value", round(tot_v, 2)),
            ("Priced holdings: P&L", round(tot_v - tot_i, 2))]
    return (kv_table(head) + "<p class=\"src\">Value, invested and P&amp;L are quantity times the stored last price and average "
            "price; holdings without a last price are shown but not totalled.</p>"
            + data_table(["symbol", "qty", "avg price", "last price", "invested", "value", "P&L", "P&L %"], rows))


METRIC_ROWS = [("start", "Start", "t"), ("end", "End", "t"), ("invested", "Invested (Rs)", "n"), ("final_equity", "Final equity (Rs)", "n"),
               ("cagr", "CAGR", "p"), ("xirr", "XIRR", "p"), ("total_return", "Total return", "p"),
               ("max_drawdown", "Max drawdown", "p"), ("volatility", "Volatility", "p"), ("sharpe", "Sharpe", "n"),
               ("sortino", "Sortino", "n"), ("closed_trades", "Closed trades", "n"), ("win_rate", "Win rate", "p"),
               ("exposure", "Exposure", "p"), ("charges_paid", "Charges paid (Rs)", "n"), ("tax_paid", "Tax paid (Rs)", "n")]


def section_backtest(c):
    found = sorted(glob.glob(os.path.join(c.trader, "backtests", "*", "summary.json")), key=os.path.getmtime)
    for p in reversed(found):
        doc = c.read_json(p)
        if isinstance(doc, dict) and isinstance(doc.get("metrics"), dict):
            break
    else:
        raise Missing(["python3 trader-backtest/backtest.py run --strategy buy_hold --symbol NIFTYBEES"])
    m = doc["metrics"]
    pairs = [("Run", doc.get("name")), ("Strategy", doc.get("strategy")), ("Symbols", doc.get("symbols")),
             ("Fill, slippage bps", "%s, %s" % (doc.get("fill"), doc.get("slippage_bps"))), ("Tax", "on" if doc.get("tax") else "off")]
    for k, label, kind in METRIC_ROWS:
        v = m.get(k)
        if v is None:
            continue
        pairs.append((label, "%.2f%%" % (v * 100) if kind == "p" and num(v) is not None else (round(v, 2) if kind == "n" and isinstance(v, float) else v)))
    b = doc.get("benchmark") or {}
    if b.get("symbol"):
        pairs.append(("Benchmark " + b["symbol"], "CAGR %s, max drawdown %s" % (
            "%.2f%%" % (b["cagr"] * 100) if num(b.get("cagr")) is not None else "-",
            "%.2f%%" % (b["max_drawdown"] * 100) if num(b.get("max_drawdown")) is not None else "-")))
    c.src("backtest", p, str(doc.get("generated") or ""))
    link = os.path.join(os.path.dirname(p), "report.html")
    note = ""
    if os.path.isfile(link):
        note = '<p><a href="%s">Open the full backtest report</a> (equity curve, drawdown, orders)</p>' % esc(rel_link(link, c.out_dir))
    cav = "".join("<li>%s</li>" % esc(x) for x in (doc.get("caveats") or [])[:6])
    return kv_table(pairs) + note + ("<h4>Caveats</h4><ul>%s</ul>" % cav if cav else "")


def rel_link(target, out_dir):
    try:
        return os.path.relpath(target, out_dir).replace(os.sep, "/")
    except ValueError:      # another drive on Windows
        return target.replace(os.sep, "/")


BUILDERS = {"market": section_market, "news": section_news, "scans": section_scans, "study": section_study,
            "symbols": section_symbols, "thesis": section_thesis, "plan": section_plan, "portfolio": section_portfolio,
            "backtest": section_backtest}


# ---------------------------------------------------------------- page

CSS = """:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--card:#f2efe8;--s1:#1f6feb;--s2:#9a6b00;--s3:#c0392b;--band:#6b6a66}
@media (prefers-color-scheme: dark){:root{--bg:#141414;--fg:#ecebe7;--mut:#a3a19b;--line:#2c2b29;--card:#1d1d1c;--s1:#6ea8ff;--s2:#e0b04a;--s3:#ff7b6b;--band:#a3a19b}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;padding:24px 16px}
main{max-width:1000px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:0 0 8px}h3{font-size:16px;margin:22px 0 6px}h4{font-size:14px;margin:14px 0 4px}
.sub,.src,small{color:var(--mut)}.src{font-size:13px;margin:4px 0 12px}code{font-size:13px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0}nav button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 12px;font:inherit;cursor:pointer}
nav button[aria-pressed=true]{border-color:var(--s1);color:var(--s1)}
section{padding:12px 0 18px;border-top:1px solid var(--line)}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:150px;flex:1}
.card span,.card small{display:block;color:var(--mut);font-size:12px}.card b{font-size:20px;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500}table.sortable th{cursor:pointer}table.kv th{width:36%}.wrap{overflow-x:auto}
.warn{color:var(--s3)}.flt{width:100%;box-sizing:border-box;margin:6px 0;padding:6px 8px;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px}
figure{margin:12px 0}figcaption{font-weight:600}svg{width:100%;height:auto;display:block;margin:6px 0}
svg .a{fill:var(--mut);font-size:11px}svg .g{stroke:var(--line)}svg .m{stroke:var(--mut);stroke-dasharray:4 4}svg .m.s2{stroke:var(--s2)}svg .m.s3{stroke:var(--s3)}svg .m.band{stroke:var(--band)}
svg .ln{stroke-width:1.6;fill:none}svg .ln.s1{stroke:var(--s1)}svg .ln.s2{stroke:var(--s2)}svg .ln.s3{stroke:var(--s3)}svg rect.s1{stroke:var(--s1);fill:var(--s1)}svg rect.s2{stroke:var(--s2);fill:var(--s2)}svg rect.s3{stroke:var(--s3);fill:var(--s3)}
.key span{display:inline-block;margin-right:16px;color:var(--mut);font-size:13px}.key i{display:inline-block;width:14px;height:3px;margin-right:6px;vertical-align:middle}
.key i.s1{background:var(--s1)}.key i.s2{background:var(--s2)}.key i.s3{background:var(--s3)}
details{margin:6px 0}summary{cursor:pointer;color:var(--mut)}.note{color:var(--s2)}footer{border-top:1px solid var(--line);padding-top:12px;margin-top:20px}
.js .hide{display:none}"""

JS = """(function(){var d=document;d.body.className+=' js';
var secs=[].slice.call(d.querySelectorAll('section[data-tab]')),btns=[].slice.call(d.querySelectorAll('nav button'));
function show(t){secs.forEach(function(s){s.classList.toggle('hide',t!=='all'&&s.getAttribute('data-tab')!==t)});
btns.forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-tab')===t?'true':'false')})}
btns.forEach(function(b){b.addEventListener('click',function(){show(b.getAttribute('data-tab'))})});show('all');
function val(td){var v=td.getAttribute('data-v');if(v===null)v=td.textContent;var n=parseFloat(v);return isNaN(n)||v===''?v.toLowerCase():n}
[].forEach.call(d.querySelectorAll('table.sortable'),function(t){var dir={};
[].forEach.call(t.tHead.rows[0].cells,function(th,i){th.addEventListener('click',function(){
var rows=[].slice.call(t.tBodies[0].rows);dir[i]=!dir[i];
rows.sort(function(a,b){var x=val(a.cells[i]),y=val(b.cells[i]);if(x===y)return 0;
if(typeof x!==typeof y)return typeof x==='number'?-1:1;return (x<y?-1:1)*(dir[i]?1:-1)});
rows.forEach(function(r){t.tBodies[0].appendChild(r)})})})});
[].forEach.call(d.querySelectorAll('.flt'),function(f){f.addEventListener('input',function(){
var t=f.nextElementSibling.querySelector('tbody'),q=f.value.toLowerCase();
[].forEach.call(t.rows,function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)<0?'none':''})})})})();"""


def render(title, generated, blocks, missing, notes, sources, private_left_out):
    nav = '<nav aria-label="sections"><button type="button" data-tab="all" aria-pressed="true">All</button>%s</nav>' % "".join(
        '<button type="button" data-tab="%s" aria-pressed="false">%s</button>' % (k, esc(TITLES[k])) for k, _ in blocks)
    secs = "".join('<section data-tab="%s" id="%s"><h2>%s</h2>%s</section>' % (k, k, esc(TITLES[k]), h) for k, h in blocks)
    src_rows = "".join("<tr><td>%s</td><td><code>%s</code></td><td>%s</td></tr>" % (esc(TITLES[s]), esc(p), esc(a or "-"))
                       for s, p, a in sources)
    foot = ""
    if missing:
        foot += "<h3>Not available</h3><ul>%s</ul>" % "".join(
            "<li><b>%s</b>: run %s</li>" % (esc(TITLES[k]), " or ".join("<code>%s</code>" % esc(x) for x in cmds)) for k, cmds in missing)
    if private_left_out:
        foot += '<p class="note">Left out on purpose (--public): %s.</p>' % esc(", ".join(TITLES[k] for k in private_left_out))
    if notes:
        foot += "<h3>Skipped inputs</h3><ul>%s</ul>" % "".join("<li>%s</li>" % esc(n) for n in notes)
    if sources:
        foot += "<h3>Sources</h3><div class=\"wrap\"><table><tr><th>section</th><th>file</th><th>as of</th></tr>%s</table></div>" % src_rows
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>%s</title>\n<style>\n%s\n</style></head>'
            '<body><main>\n<h1>%s</h1>\n<p class="sub">Generated %s by trader-report from the files the other skills wrote. '
            'Education and tooling, not financial advice.</p>\n%s\n%s\n<footer>%s'
            '<p class="sub">Every figure comes from the files listed above. Past performance does not predict future returns. '
            'Nothing here places an order.</p></footer>\n</main><script>\n%s\n</script></body></html>\n'
            % (esc(title), CSS, esc(title), esc(generated), nav if blocks else "", secs, foot, JS))


def build(a):
    sections = [s.strip() for s in a.sections.split(",") if s.strip()] if a.sections else list(ALL_SECTIONS)
    bad = [s for s in sections if s not in BUILDERS]
    if bad:
        print("report.py: unknown section %s (choose from %s)" % (", ".join(bad), ", ".join(ALL_SECTIONS)), file=sys.stderr)
        return 2
    c = Ctx(a.data, a.trader, [s.strip().upper() for s in (a.symbol or [])], a.public)
    c.out_dir = os.path.dirname(os.path.abspath(a.out))
    blocks, missing, left_out = [], [], []
    for k in sections:
        if a.public and k in PRIVATE:
            left_out.append(k)
            continue
        try:
            body = BUILDERS[k](c)
        except Missing as m:
            missing.append((k, m.cmds))
            continue
        blocks.append((k, body))
        # source lines sit under the section heading
        mine = [(p, ao) for s, p, ao in c.sources if s == k]
        blocks[-1] = (k, "".join(source_line(p, ao) for p, ao in mine) + body)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = render(a.title, now, blocks, missing, c.notes, c.sources, left_out)
    d = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(d, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    print("Wrote %s: %d section(s)%s%s." % (a.out, len(blocks),
                                           ", %d not available" % len(missing) if missing else "",
                                           ", %d input(s) skipped" % len(c.notes) if c.notes else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="report.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write the HTML dashboard")
    b.add_argument("--data", default="trader/data", help="the trader-data folder (default trader/data)")
    b.add_argument("--trader", default="trader", help="the project's trader/ folder")
    b.add_argument("--out", default="trader/report.html")
    b.add_argument("--title", default="NSE trader report")
    b.add_argument("--sections", help="comma list from: " + ", ".join(ALL_SECTIONS))
    b.add_argument("--symbol", action="append", help="add a per-symbol section (repeatable)")
    b.add_argument("--public", action="store_true", help="leave out holdings, proposed orders and risk verdicts")
    a = ap.parse_args(argv)
    if a.cmd != "build":
        ap.print_help()
        return 2
    return build(a)


if __name__ == "__main__":
    sys.exit(main())
