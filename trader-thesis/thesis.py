#!/usr/bin/env python3
"""Evidence pack and output enforcer for trader-thesis (NSE). Standard library only.

"Should I buy X?" is answered by the SKILL.md method (isolated bull/bear/risk branches and a
critic). This script does the two mechanical parts, so the numbers and the shape are not left to prose:

    python3 thesis.py brief IOC [--mode longterm|intraday|swing] [--data trader/data] [--out FILE]
    python3 thesis.py frames [--mode longterm] [--json]
    python3 thesis.py render trader/thesis/IOC-thesis.json

brief   reads whatever the other skills already wrote (every source optional) and writes ONE
        evidence pack: every value with its source file and date, every missing source listed.
        Nothing is invented: a number that cannot be computed is null and is listed.
render  turns a finished thesis JSON (the shape SKILL.md defines) into Markdown and refuses,
        exit 1 naming the section, when a required section is missing.

Files read, all optional (trader/ is the parent of --data, default trader/data):

  trader/data/daily/<SYM>.adj.csv|.csv      date,open,high,low,close,volume
  trader/data/fundamentals/<SYM>.json       trader-invest fetch
  trader/invest/<SYM>.json                  invest.py analyse SYM (saved automatically)
  trader/longterm.json, trader/watchlist.json   trader-scanner hits
  trader/data/delivery/<SYM>.csv            date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series
  trader/data/actions.json                  {"symbols":{SYM:{"actions":[{ex_date,kind,factor,dividend}]}}}
  trader/data/index/nifty-50.csv, india-vix.csv   date,open,high,low,close,volume,pe,pb,div_yield
  trader/data/flows.csv                     date,category,buy_cr,sell_cr,net_cr
  trader/data/breadth.csv                   date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc,...
  trader/data/chain-NIFTY.json              pcr_oi, max_pain, max_call_oi_strike, max_put_oi_strike
  trader/plan.json                          entry, stop, target (intraday and swing modes)

Adapted from the ADHD skill (MIT, https://github.com/UditAkhourii/adhd): see THIRD_PARTY_NOTICES.md.
Education and tooling, not financial advice.
"""

import argparse
import csv
import datetime
import json
import os
import re
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

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
MODES = ("longterm", "intraday", "swing")
YEAR_BARS = 252
DMA = 200
DELIVERY_WINDOW = 30
FLOW_DAYS = 5

# (id, name, tags, vantage prompt). Tags: longterm, intraday, wild. Pick 5 per run.
FRAMES = [
    ("bear", "Bear analyst / short seller", ("longterm", "intraday"),
     "You are a short seller paid only if this falls. Build the strongest honest case against it from the brief: "
     "what is priced in, what is stretched, what breaks first."),
    ("risk", "Risk manager", ("longterm", "intraday"),
     "You own the downside. From the brief, size the position, place the stop, and state the worst case in "
     "rupees for a given capital. What loss is the user really signing up for?"),
    ("macro", "Macro and FII/DII flows", ("longterm", "intraday"),
     "You read the tape of money: FII and DII flows, India VIX, breadth, the Nifty P/E, rates. What does the "
     "market regime say about taking this exposure now?"),
    ("contrarian", "Contrarian / crowd positioning", ("longterm", "intraday", "wild"),
     "Ask who is already in. Delivery, put-call ratio, headlines, 52-week position: is the crowd on one side? "
     "What happens if the crowd is wrong, or if it simply leaves?"),
    ("value_trap", "Value-trap hunter", ("longterm",),
     "Cheap for a reason? Hunt cyclical peak earnings, PSU policy risk, one-off profits, dividend payouts that "
     "cannot last, a low PE on shrinking earnings."),
    ("forensic", "Forensic accountant", ("longterm",),
     "Read between the lines: promoter pledge and selling, related-party dealings, cash flow against profit, "
     "debt that grew faster than sales. What would an auditor ask?"),
    ("stopout", "3am stop-out", ("intraday", "wild"),
     "Assume this trade lost money. How did it lose fastest: a gap through the stop, a circuit, an ex-date, a "
     "result, a thin book, slippage? Name the single most likely path."),
    ("tax_costs", "Tax and costs", ("longterm", "intraday"),
     "Net of STCG or LTCG, STT, brokerage, DP charges, stamp duty and slippage, what is left? Does the edge "
     "survive the costs, and does the holding period change the tax?"),
    ("horizon", "Time-horizon inversion", ("longterm", "wild"),
     "Two forced experiments. You must hold this for five years with no exit: does it still make sense? You must "
     "exit tomorrow: what is the exit price and who buys?"),
    ("regulator", "Regulator / SEBI surveillance", ("longterm", "intraday"),
     "Think like the exchange: ASM and GSM lists, price bands and circuits, T2T segment, delivery rules, "
     "pending actions. What surveillance or rule change could trap the position?"),
    ("compounder", "Patient compounder", ("longterm",),
     "Make the best long-run bull case: durable returns on capital, reinvestment runway, pricing power, "
     "management. Which numbers in the brief support it and which do not?"),
    ("opportunity", "Opportunity cost vs a NIFTYBEES SIP", ("longterm", "wild"),
     "The benchmark is a boring monthly NIFTYBEES SIP. After risk, effort and tax, why would this beat it? "
     "What return is needed just to break even with the index?"),
]

REQUIRED = ("symbol", "brief", "bull_case", "bear_case", "traps", "verdict", "watch", "handoff")
VERDICT_CALLS = {"longterm": ("buy", "wait", "avoid"), "intraday": ("take", "skip"), "swing": ("take", "skip")}
CONVICTION = ("low", "medium", "high")


class ThesisError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def ist_today():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET).date()


def parse_day(text):
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def num(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def r2(x):
    return None if x is None else round(x, 2)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return None


def mtime(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()
    except OSError:
        return None


def clean_symbol(s):
    s = str(s or "").strip().upper()
    if not TICKER.match(s):
        raise ThesisError("%r is not an NSE symbol like IOC or M&M" % s)
    return s


def src(name, path, date, values, **extra):
    d = {"source": name, "file": path.replace("\\", "/"), "date": date, "file_modified": mtime(path)}
    d.update(extra)
    d["values"] = values
    return d


# ---------------------------------------------------------------- readers: each returns (section | None, why-missing)

def read_daily(data_dir, sym):
    for name, adj in ((sym + ".adj.csv", True), (sym + ".csv", False)):
        path = os.path.join(data_dir, "daily", name)
        rows = read_rows(path) if os.path.isfile(path) else None
        if not rows:
            continue
        bars = []
        for r in rows:
            c, d = num(r.get("close")), parse_day(r.get("date"))
            if c is not None and c > 0 and d is not None:
                bars.append((d, c))
        bars.sort()
        if not bars:
            continue
        closes = [c for _, c in bars]
        last_d, last = bars[-1]
        year = closes[-YEAR_BARS:]
        hi, lo = max(year), min(year)
        dma = sum(closes[-DMA:]) / DMA if len(closes) >= DMA else None
        ret = (last / closes[-YEAR_BARS - 1] - 1) * 100 if len(closes) > YEAR_BARS else None
        peak, dd = year[0], 0.0
        for c in year:
            peak = max(peak, c)
            dd = min(dd, (c / peak - 1) * 100)
        vals = {"last_close": r2(last), "high_52w": r2(hi), "low_52w": r2(lo),
                "pct_from_52w_high": r2((last / hi - 1) * 100), "pct_above_52w_low": r2((last / lo - 1) * 100),
                "dma_200": r2(dma), "pct_vs_200dma": r2((last / dma - 1) * 100) if dma else None,
                "return_1y_pct": r2(ret), "max_drawdown_1y_pct": r2(dd) if len(year) > 1 else None,
                "bars": len(bars), "first_bar": bars[0][0].isoformat(), "adjusted": adj}
        return src("daily", path, last_d.isoformat(), vals), None
    return None, "no daily bars (run trader-data for %s)" % sym


def read_fundamentals(data_dir, sym):
    path = os.path.join(data_dir, "fundamentals", sym + ".json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return None, "no fundamentals (run trader-invest fetch %s)" % sym
    keys = ("price", "market_cap_cr", "pe", "pb", "div_yield_pct", "roe_pct", "roce_pct", "debt_to_equity",
            "promoter_pct", "promoter_pledge_pct", "promoter_name", "sector", "industry")
    vals = dict((k, doc.get(k)) for k in keys)
    ann = [a for a in doc.get("annual") or [] if isinstance(a, dict)][-5:]
    vals["annual"] = [{"fy": a.get("fy"), "revenue_cr": a.get("revenue_cr"), "net_profit_cr": a.get("net_profit_cr")}
                      for a in ann]
    qs = [q for q in doc.get("quarterly") or [] if isinstance(q, dict)]
    vals["latest_quarter"] = ({"q": qs[-1].get("q"), "revenue_cr": qs[-1].get("revenue_cr"),
                               "net_profit_cr": qs[-1].get("net_profit_cr")} if qs else None)
    return src("fundamentals", path, doc.get("as_of"), vals), None


def read_invest(trader_dir, sym):
    for name in (sym + ".json", sym + "-analysis.json"):
        path = os.path.join(trader_dir, "invest", name)
        doc = read_json(path)
        if isinstance(doc, dict) and "score" in doc:
            pil = doc.get("pillars") or {}
            vals = {"score": doc.get("score"), "verdict": doc.get("verdict"), "coverage_pct": doc.get("coverage_pct"),
                    "pillars": dict((k, (v or {}).get("score")) for k, v in pil.items()),
                    "flags": doc.get("flags"), "caps": doc.get("caps"), "strengths": doc.get("strengths"),
                    "concerns": doc.get("concerns"), "missing_fields": doc.get("missing_fields"),
                    "data_age": doc.get("data_age"), "cyclical": doc.get("cyclical"), "psu": doc.get("psu")}
            return src("invest_analysis", path, doc.get("as_of"), vals), None
    return None, "no trader-invest analysis (run: invest.py analyse %s, which saves trader/invest/%s.json)" % (sym, sym)


HIT_KEYS = ("setups", "price", "close_date", "vs_200dma_pct", "from_high_pct", "swing_trend", "swing_coc",
            "swing_event", "mansfield_rs", "dq", "dq_date", "level", "stop", "target", "risk_pct", "state",
            "anchor_date")


def read_hits(trader_dir, sym, fname, label):
    path = os.path.join(trader_dir, fname)
    doc = read_json(path)
    if doc is None:
        return None, "no %s (run trader-scanner)" % fname
    items = doc.get("hits", []) if isinstance(doc, dict) else doc
    mine = [h for h in items if isinstance(h, dict) and str(h.get("symbol", "")).upper() == sym]
    if not mine:
        return None, "%s has no hit for %s" % (fname, sym)
    hits = [dict((k, h.get(k)) for k in HIT_KEYS if k in h) for h in mine]
    date = None
    if isinstance(doc, dict):
        date = doc.get("as_of") or doc.get("generated") or doc.get("date") or doc.get("last_date")
    date = date or max([h.get("close_date") for h in mine if h.get("close_date")] or [None])
    extra = {"mode": doc.get("mode")} if isinstance(doc, dict) and doc.get("mode") else {}
    return src(label, path, date, {"hits": hits}, **extra), None


def read_delivery(data_dir, sym):
    path = os.path.join(data_dir, "delivery", sym + ".csv")
    rows = read_rows(path) if os.path.isfile(path) else None
    if not rows:
        return None, "no delivery file (run trader-data delivery)"
    rows = [r for r in rows if parse_day(r.get("date"))]
    rows.sort(key=lambda r: r["date"])
    tail = rows[-DELIVERY_WINDOW:]
    pcts = [num(r.get("deliv_pct")) for r in tail]
    pcts = [p for p in pcts if p is not None]
    if not pcts:
        return None, "delivery file has no delivery percentages"
    last = rows[-1]
    vals = {"latest_deliv_pct": r2(num(last.get("deliv_pct"))), "mean_deliv_pct_30": r2(sum(pcts) / len(pcts)),
            "sessions_used": len(pcts), "latest_deliv_qty": num(last.get("deliv_qty")),
            "latest_series": last.get("series") or None}
    return src("delivery", path, last["date"][:10], vals), None


def read_actions(data_dir, sym, today):
    path = os.path.join(data_dir, "actions.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return None, "no corporate actions (run trader-data actions)"
    ent = (doc.get("symbols") or {}).get(sym)
    if not isinstance(ent, dict):
        return None, "actions.json has no entry for %s" % sym
    acts = []
    for a in ent.get("actions") or []:
        if isinstance(a, dict) and a.get("ex_date"):
            acts.append({"ex_date": a.get("ex_date"), "kind": a.get("kind"), "factor": a.get("factor"),
                         "dividend": a.get("dividend"), "subject": a.get("subject"),
                         "upcoming": str(a["ex_date"]) >= today.isoformat()})
    acts.sort(key=lambda a: a["ex_date"])
    vals = {"actions": acts[-12:], "next_ex_date": next((a["ex_date"] for a in acts if a["upcoming"]), None)}
    return src("actions", path, ent.get("fetched_through") or doc.get("fetched_at"), vals), None


def read_events(data_dir, sym, today):
    """Results and dividend board meetings ahead, from trader/data/events.json (trader-news keeps it)."""
    path = os.path.join(data_dir, "events.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return None, "no board-meeting dates (run trader-news for %s)" % sym
    ahead = sorted((e for e in doc.get("events") or [] if isinstance(e, dict) and e.get("ticker") == sym
                    and str(e.get("date")) >= today.isoformat()), key=lambda e: str(e.get("date")))
    if not ahead:
        return None, "events.json lists no board meeting ahead for %s" % sym
    vals = {"meetings": [{"date": e.get("date"), "purpose": e.get("purpose"), "results": e.get("results"),
                          "dividend": e.get("dividend")} for e in ahead[:6]],
            "next_results": next((e.get("date") for e in ahead if e.get("results")), None)}
    return src("events", path, doc.get("fetched_at"), vals), None


def read_index(data_dir, slug, label):
    path = os.path.join(data_dir, "index", slug + ".csv")
    rows = read_rows(path) if os.path.isfile(path) else None
    rows = [r for r in rows or [] if parse_day(r.get("date")) and num(r.get("close")) is not None]
    if not rows:
        return None, "no %s index file (run trader-data index)" % slug
    rows.sort(key=lambda r: r["date"])
    last = rows[-1]
    vals = {"close": r2(num(last.get("close")))}
    for k in ("pe", "pb", "div_yield"):
        vals[k] = r2(num(last.get(k)))
    if len(rows) > 5 and num(rows[-6].get("close")):
        vals["change_5d_pct"] = r2((num(last["close"]) / num(rows[-6]["close"]) - 1) * 100)
    return src(label, path, last["date"][:10], vals), None


def read_flows(data_dir):
    path = os.path.join(data_dir, "flows.csv")
    rows = read_rows(path) if os.path.isfile(path) else None
    rows = [r for r in rows or [] if parse_day(r.get("date")) and num(r.get("net_cr")) is not None]
    if not rows:
        return None, "no FII/DII flows file (flows.csv not written yet)"
    dates = sorted(set(r["date"][:10] for r in rows))
    recent = set(dates[-FLOW_DAYS:])
    cats = {}
    for r in rows:
        if r["date"][:10] in recent:
            cats[r.get("category") or "?"] = cats.get(r.get("category") or "?", 0.0) + num(r["net_cr"])
    vals = {"days": len(recent), "net_cr_by_category": dict((k, r2(v)) for k, v in sorted(cats.items()))}
    return src("flows", path, dates[-1], vals), None


def read_breadth(data_dir):
    path = os.path.join(data_dir, "breadth.csv")
    rows = read_rows(path) if os.path.isfile(path) else None
    rows = [r for r in rows or [] if parse_day(r.get("date"))]
    if not rows:
        return None, "no market breadth (run trader-scanner breadth)"
    rows.sort(key=lambda r: r["date"])
    last = rows[-1]
    vals = dict((k, r2(num(last.get(k)))) for k in ("pct_50", "pct_200", "net_new_highs", "ad_line", "mcclellan_osc"))
    return src("breadth", path, last["date"][:10], vals), None


def read_chain(data_dir):
    path = os.path.join(data_dir, "chain-NIFTY.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return None, "no NIFTY option chain summary (run trader-data chain)"
    vals = dict((k, doc.get(k)) for k in ("pcr_oi", "max_pain", "max_call_oi_strike", "max_put_oi_strike", "expiry"))
    return src("chain_nifty", path, doc.get("as_of") or doc.get("fetched_at") or doc.get("date"), vals), None


def read_plan(trader_dir, sym):
    path = os.path.join(trader_dir, "plan.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return None, "no trader/plan.json (run trader-exit)"
    if str(doc.get("symbol", "")).upper() != sym:
        return None, "trader/plan.json is for %s, not %s" % (doc.get("symbol"), sym)
    vals = dict((k, doc.get(k)) for k in ("entry", "stop", "target", "qty", "product", "side", "flat_by"))
    return src("plan", path, doc.get("date"), vals), None


# ---------------------------------------------------------------- brief

def build_brief(sym, mode, data_dir, today):
    data_dir = os.path.normpath(data_dir)
    trader_dir = os.path.dirname(data_dir) or "."
    readers = [("daily", lambda: read_daily(data_dir, sym)),
               ("fundamentals", lambda: read_fundamentals(data_dir, sym)),
               ("invest_analysis", lambda: read_invest(trader_dir, sym)),
               ("longterm", lambda: read_hits(trader_dir, sym, "longterm.json", "longterm")),
               ("watchlist", lambda: read_hits(trader_dir, sym, "watchlist.json", "watchlist")),
               ("delivery", lambda: read_delivery(data_dir, sym)),
               ("actions", lambda: read_actions(data_dir, sym, today)),
               ("events", lambda: read_events(data_dir, sym, today)),
               ("nifty_50", lambda: read_index(data_dir, "nifty-50", "nifty_50")),
               ("india_vix", lambda: read_index(data_dir, "india-vix", "india_vix")),
               ("flows", lambda: read_flows(data_dir)),
               ("breadth", lambda: read_breadth(data_dir)),
               ("chain_nifty", lambda: read_chain(data_dir))]
    if mode in ("intraday", "swing"):
        readers.append(("plan", lambda: read_plan(trader_dir, sym)))
    sources, missing = {}, []
    for name, fn in readers:
        got, why = fn()
        if got is None:
            missing.append({"source": name, "why": why})
        else:
            sources[name] = got
    notes = []
    d = sources.get("daily")
    if d and (parse_day(d["date"]) or today) < today - datetime.timedelta(days=7):
        notes.append("daily bars end %s, more than 7 days before %s: refresh trader-data" % (d["date"], today))
    if not d and "fundamentals" not in sources:
        notes.append("neither price history nor fundamentals found: the branches have almost nothing to argue from")
    return {"symbol": sym, "mode": mode, "generated": today.isoformat(), "data_dir": data_dir.replace("\\", "/"),
            "sources": sources, "missing": missing, "notes": notes,
            "disclaimer": "Education and tooling, not financial advice. Every value is read from the file named "
                          "beside it; a missing source is listed, never guessed."}


# ---------------------------------------------------------------- frames

def frame_rows(mode=None):
    rows = []
    for fid, name, tags, prompt in FRAMES:
        if mode and mode not in tags and "wild" not in tags:
            continue
        rows.append({"id": fid, "name": name, "tags": list(tags), "prompt": prompt})
    return rows


# ---------------------------------------------------------------- render

def check_thesis(doc):
    """Return a list of problems; empty means the thesis has every required section."""
    if not isinstance(doc, dict):
        return ["the file is not a JSON object"]
    bad = []
    for k in REQUIRED:
        v = doc.get(k)
        if v is None or v == "" or (k in ("bull_case", "bear_case", "watch") and not v):
            bad.append("missing section: %s" % k)
    if bad:
        return bad
    mode = doc.get("mode") or "longterm"
    if mode not in MODES:
        bad.append("mode %r is not one of %s" % (mode, ", ".join(MODES)))
        mode = "longterm"
    for k in ("bull_case", "bear_case", "traps", "watch"):
        if not isinstance(doc[k], list):
            bad.append("section %s must be a list" % k)
    v = doc["verdict"]
    if not isinstance(v, dict):
        bad.append("verdict must be an object with call, conviction, changes_if")
    else:
        if str(v.get("call", "")).lower() not in VERDICT_CALLS[mode]:
            bad.append("verdict.call must be one of %s for mode %s" % (", ".join(VERDICT_CALLS[mode]), mode))
        if str(v.get("conviction", "")).lower() not in CONVICTION:
            bad.append("verdict.conviction must be one of %s" % ", ".join(CONVICTION))
        if not v.get("changes_if"):
            bad.append("verdict.changes_if (the one fact that would change it) is missing")
    h = doc["handoff"]
    if not isinstance(h, dict) or not h.get("skill"):
        bad.append("handoff must be an object with skill (and command or note)")
    return bad


def _text(x):
    if isinstance(x, dict):
        main = x.get("point") or x.get("idea") or x.get("what") or x.get("text") or ""
        extra = [("evidence", x.get("evidence")), ("why a trap", x.get("reason")), ("level or date", x.get("when")),
                 ("why", x.get("why")), ("frame", x.get("frame")), ("rupees", x.get("rupee_impact"))]
        tail = "; ".join("%s: %s" % (k, v) for k, v in extra if v)
        return main + (" (" + tail + ")" if tail else "")
    return str(x)


def render_md(doc):
    mode = doc.get("mode") or "longterm"
    v = doc["verdict"]
    out = ["# Thesis: %s (%s)%s" % (doc["symbol"], mode, " - " + doc["date"] if doc.get("date") else ""), ""]
    out += ["## Brief", ""]
    b = doc["brief"]
    out += [b if isinstance(b, str) else (b.get("summary") or json.dumps(b, ensure_ascii=False)), ""]
    for title, key in (("Bull case", "bull_case"), ("Bear case", "bear_case")):
        out += ["## " + title, ""] + ["- " + _text(x) for x in doc[key]] + [""]
    out += ["## Traps (attractive but wrong)", ""]
    out += ["- " + _text(x) for x in doc["traps"]] or ["- none flagged"]
    out += ["", "## Verdict", "",
            "**%s** (conviction: %s)" % (str(v["call"]).upper(), v["conviction"]), ""]
    if v.get("rationale"):
        out += [str(v["rationale"]), ""]
    out += ["The one fact that would change it: %s" % v["changes_if"], ""]
    out += ["## What to watch", ""] + ["- " + _text(x) for x in doc["watch"]] + [""]
    deep = doc.get("deepened") or []
    if deep:
        out += ["## Top arguments, deepened", ""]
        for i, d in enumerate(deep, 1):
            out.append("%d. %s" % (i, _text(d)))
            for k, label in (("must_be_true", "what would have to be true"), ("load_bearing_risk", "load-bearing risk"),
                             ("trigger", "observable trigger"), ("first_step", "first concrete step")):
                if isinstance(d, dict) and d.get(k):
                    out.append("   - %s: %s" % (label, d[k]))
        out.append("")
    h = doc["handoff"]
    out += ["## Hand-off", "", "Next skill: %s" % h["skill"]]
    if h.get("command"):
        out.append("Command: `%s`" % h["command"])
    if h.get("note"):
        out.append(str(h["note"]))
    out += ["", "A verdict is a summary of the evidence, never an order. Nothing is bought without your explicit "
            "yes. Not financial advice."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- commands

def cmd_brief(a):
    sym = clean_symbol(a.symbol)
    today = parse_day(a.today) if a.today else ist_today()
    brief = build_brief(sym, a.mode, a.data, today)
    out = a.out or os.path.join(os.path.dirname(os.path.normpath(a.data)) or ".", "thesis", sym + "-brief.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(brief, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if a.json:
        print(json.dumps(brief, indent=2, ensure_ascii=False))
    else:
        print("%s brief (%s): %d source(s) read, %d missing -> %s" % (sym, a.mode, len(brief["sources"]),
                                                                    len(brief["missing"]), out))
        for name, s in brief["sources"].items():
            print("  ok       %-16s %s  (%s)" % (name, s["date"] or "no date", s["file"]))
        for m in brief["missing"]:
            print("  MISSING  %-16s %s" % (m["source"], m["why"]))
        for n in brief["notes"]:
            print("  NOTE: " + n)
    return 0


def cmd_frames(a):
    rows = frame_rows(a.mode)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    print("%-12s %-34s %s" % ("id", "frame", "tags"))
    for r in rows:
        print("%-12s %-34s %s" % (r["id"], r["name"], ", ".join(r["tags"])))
    print("Pick 5 per run, at least one tagged wild. Same brief for every branch.")
    return 0


def cmd_render(a):
    try:
        with open(a.file, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise ThesisError("cannot read %s: %s" % (a.file, e))
    bad = check_thesis(doc)
    if bad:
        for b in bad:
            print("REFUSED: " + b, file=sys.stderr)
        return 1
    md = render_md(doc)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("wrote %s" % a.out)
    else:
        sys.stdout.write(md)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="trader-thesis evidence pack and output renderer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief", help="assemble one evidence pack for a symbol")
    b.add_argument("symbol")
    b.add_argument("--mode", choices=MODES, default="longterm")
    b.add_argument("--data", default="trader/data", help="the trader-data folder (default trader/data)")
    b.add_argument("--out", help="default trader/thesis/<SYM>-brief.json")
    b.add_argument("--today", help="pin today's date (YYYY-MM-DD), for tests")
    b.add_argument("--json", action="store_true", help="print the brief")
    f = sub.add_parser("frames", help="print the frame table")
    f.add_argument("--mode", choices=MODES, help="only frames for this mode (wild frames always shown)")
    f.add_argument("--json", action="store_true")
    r = sub.add_parser("render", help="thesis JSON -> Markdown, refusing a missing section")
    r.add_argument("file")
    r.add_argument("--out", help="write the Markdown here instead of stdout")
    a = ap.parse_args(argv)
    try:
        return {"brief": cmd_brief, "frames": cmd_frames, "render": cmd_render}[a.cmd](a)
    except ThesisError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
