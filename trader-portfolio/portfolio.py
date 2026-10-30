#!/usr/bin/env python3
"""Long-term holdings manager for trader-portfolio (NSE delivery). Standard library only, no network.

    python3 portfolio.py import holdings-kite.json             # Kite MCP get_holdings, saved raw
    python3 portfolio.py import holdings.csv tradebook.csv     # Kite Console exports
    python3 portfolio.py show                                  # value, P&L, weights, lots
    python3 portfolio.py risk                                  # concentration and asset-class checks
    python3 portfolio.py rebalance --targets targets.json --cash 5000 --write
    python3 portfolio.py tax --fy 2026-27                      # realised STCG/LTCG, harvesting ideas
    python3 portfolio.py sip --monthly 5000 --targets targets.json --write
    add --json to any of them for machine output

Files (all under the project's trader/ folder, --dir to change):

    trader/holdings.json                 written by `import` (shared contract 6)
    trader/data/fundamentals/<SYM>.json  sector, read when present (trader-invest writes it)
    trader/data/daily/<SYM>.csv          last close, used when a price is missing (trader-data)
    trader/prices.json                   optional {"prices": {SYM: price}} (trader-alerts)
    trader/charges.json                  optional charges override (contract 4)
    trader/orders.json                   proposed CNC orders (rebalance/sip with --write)
    trader/sip.json                      SIP carry ledger (sip with --write)

Tax numbers are estimates under the Indian rules in force for FY 2025-26 onward
(listed equity: STCG 20%, LTCG 12.5% above Rs 1.25 lakh a year after more than
12 months; gold/silver ETFs: LTCG 12.5% after 12 months, short-term at your slab;
FIFO lot order for demat shares). Check with a CA. Never places orders.
"""

import argparse
import copy
import csv
import datetime
import io
import json
import math
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

HERE = os.path.dirname(os.path.abspath(__file__))
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)

# contract 4: verified 2026-10-29 from zerodha.com/charges; trader/charges.json deep-merges over it
CHARGES_DEFAULT = {
    "broker": "zerodha", "verified": "2026-10-29 zerodha.com/charges",
    "intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "stamp_buy_pct": 0.003},
    "delivery": {"brokerage_pct": 0, "brokerage_cap": 0, "stt_buy_pct": 0.1, "stt_sell_pct": 0.1,
                 "stamp_buy_pct": 0.015, "dp_per_scrip_sell": 15.34},
    "exchange_txn_pct": {"NSE": 0.00307}, "sebi_per_crore": 10, "gst_pct": 18,
    "calibration": {"factor": 1.0, "samples": 0, "updated": None},
}

# tax (estimate): Finance Act 2024 rates, unchanged for FY 2025-26 and 2026-27
LTCG_EXEMPTION = 125000.0
RATES = {"equity_st": 20.0, "equity_lt": 12.5, "other_lt": 12.5}
CESS_PCT = 4.0
DEFAULT_SLAB = 30.0
EQUITY_CLASSES = ("equity", "index_etf", "sector_etf")
SLAB_ALWAYS = ("liquid_etf",)            # specified mutual fund units: slab rate whatever the period

ASSET_LABEL = {"equity": "Stocks", "index_etf": "Broad index ETF", "sector_etf": "Sector/theme ETF",
               "gold_etf": "Gold ETF", "silver_etf": "Silver ETF", "liquid_etf": "Liquid ETF",
               "intl_etf": "International ETF"}


class PortfolioError(Exception):
    pass


def shown(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------- small helpers

def ist_today():
    return (utc_now() + IST_OFFSET).date()


def utc_now():
    """Naive UTC now (no deprecated utcnow())."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def parse_date(s):
    """'2026-10-29', '29-10-2026', '29/10/2026', '2026-10-29T09:15:00' -> date (or None)."""
    if s is None:
        return None
    if isinstance(s, datetime.date):
        return s
    s = str(s).strip()
    if not s:
        return None
    s = s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise PortfolioError("cannot read the date %r (want YYYY-MM-DD)" % s)


def num(x, default=None):
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace("₹", "").replace("%", "")
    if s in ("", "-", "NA", "N/A", "null", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def r2(x):
    return None if x is None else round(float(x) + 0.0, 2)


def inr(x):
    """12345678.9 -> "₹1,23,45,678.90" (Indian digit grouping; whole rupees drop the paise)."""
    x = float(x)
    neg = x < 0
    whole, paise = ("%.2f" % abs(x)).split(".")
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


def pct(x):
    return "-" if x is None else "%.1f%%" % x


def clean_symbol(s):
    s = str(s or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if ":" in s:                          # NSE:RELIANCE
        s = s.split(":", 1)[1]
    s = re.sub(r"-(EQ|BE|BZ|SM|ST)$", "", s)
    return s


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise PortfolioError("no %s at %s" % (what, shown(path)))
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise PortfolioError("%s is not valid JSON: %s" % (shown(path), e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def add_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:                    # 29 Feb
        return d.replace(year=d.year + 1, day=28)


def lt_date(buy_date):
    """First sell date that counts as long-term: held more than 12 months (over 365 days)."""
    return add_year(buy_date) + datetime.timedelta(days=1)


def is_long_term(buy_date, sell_date):
    return buy_date is not None and sell_date >= lt_date(buy_date)


def fy_bounds(fy):
    """'2026-27' / 'FY27' / '2026' -> (2026-04-01, 2027-03-31, '2026-27')."""
    s = str(fy).strip().upper().replace("FY", "").strip()
    m = re.match(r"^(\d{4})\s*[-/]\s*(\d{2,4})$", s)
    if m:
        start = int(m.group(1))
    elif re.match(r"^\d{4}$", s):
        start = int(s)
    elif re.match(r"^\d{2}$", s):
        start = 2000 + int(s) - 1
    else:
        raise PortfolioError("--fy wants a year like 2026-27, got %r" % fy)
    return (datetime.date(start, 4, 1), datetime.date(start + 1, 3, 31),
            "%d-%02d" % (start, (start + 1) % 100))


def fy_of(d):
    start = d.year if d.month >= 4 else d.year - 1
    return "%d-%02d" % (start, (start + 1) % 100)


# ---------------------------------------------------------------- charges (contract 4)

def load_charges(tdir):
    over = read_json(os.path.join(tdir, "charges.json"), "charges", required=False)
    return deep_merge(CHARGES_DEFAULT, over if isinstance(over, dict) else {})


def charges_label(ch):
    n = int((ch.get("calibration") or {}).get("samples") or 0)
    return "estimate (calibrated from %d contract notes)" % n if n > 0 else "estimate"


def delivery_charges(buy_value=0.0, sell_value=0.0, sell_scrips=0, ch=None, exchange="NSE"):
    """Zerodha CNC (delivery) charges in rupees for one day's orders, per contract 4."""
    ch = ch or CHARGES_DEFAULT
    d = ch["delivery"]
    buy, sell = float(buy_value or 0), float(sell_value or 0)

    def brk(v):
        if v <= 0:
            return 0.0
        b = float(d.get("brokerage_pct", 0)) / 100.0 * v
        cap = float(d.get("brokerage_cap", 0) or 0)
        return min(b, cap) if cap > 0 else b

    brokerage = brk(buy) + brk(sell)
    stt = float(d["stt_buy_pct"]) / 100.0 * buy + float(d["stt_sell_pct"]) / 100.0 * sell
    txn = float(ch["exchange_txn_pct"].get(exchange, 0.00307)) / 100.0 * (buy + sell)
    sebi = (buy + sell) * float(ch["sebi_per_crore"]) / 1e7
    gst = float(ch["gst_pct"]) / 100.0 * (brokerage + txn + sebi)
    stamp = float(d["stamp_buy_pct"]) / 100.0 * buy
    dp = float(d["dp_per_scrip_sell"]) * int(sell_scrips or 0)
    factor = float((ch.get("calibration") or {}).get("factor") or 1.0)
    parts = {"brokerage": brokerage, "stt": stt, "exchange": txn, "sebi": sebi, "gst": gst,
             "stamp": stamp, "dp": dp}
    out = {k: round(v * factor, 2) for k, v in parts.items()}
    out["total"] = round(sum(parts.values()) * factor, 2)
    out["label"] = charges_label(ch)
    return out


# ---------------------------------------------------------------- groups, asset class, sector

def load_groups(path=None):
    path = path or os.path.join(HERE, "groups.json")
    g = read_json(path, "groups file")
    for k in ("groups", "psu", "asset_class", "sectors", "themes"):
        g.setdefault(k, {} if k != "psu" else [])
    return g


def asset_class(sym, groups):
    for cls, syms in (groups.get("asset_class") or {}).items():
        if sym in syms:
            return cls
    return "equity"


def is_equity_for_tax(cls):
    return cls in EQUITY_CLASSES


def sector_of(sym, tdir, groups, holding=None):
    """fundamentals file > the import's own sector (Console CSV) > groups.json map > Unknown."""
    cls = asset_class(sym, groups)
    if cls != "equity":
        return ASSET_LABEL.get(cls, cls), "asset class"
    f = read_json(os.path.join(tdir, "data", "fundamentals", sym + ".json"), "fundamentals", required=False)
    if isinstance(f, dict) and f.get("sector"):
        return str(f["sector"]), "fundamentals"
    if holding and holding.get("sector"):
        return str(holding["sector"]), "import"
    s = (groups.get("sectors") or {}).get(sym)
    if s:
        return s, "groups.json"
    return "Unknown", None


# ---------------------------------------------------------------- reading inputs

def _norm(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").strip().lower()).strip()


def read_table(path):
    """CSV (or a CSV saved from the Console XLSX) -> (header, rows) with the preamble skipped."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    if text[:2] == "PK":
        raise PortfolioError("%s looks like an .xlsx file: open it and save as CSV, then import the CSV" % shown(path))
    rows = list(csv.reader(io.StringIO(text)))
    for i, r in enumerate(rows):
        n = [_norm(c) for c in r]
        if "symbol" in n or "tradingsymbol" in n:
            header = n
            body = [x for x in rows[i + 1:] if any(c.strip() for c in x)]
            return header, [dict(zip(header, x)) for x in body]
    raise PortfolioError("%s has no header row with a Symbol column" % shown(path))


def detect(path):
    low = path.lower()
    if low.endswith(".xlsx") or low.endswith(".xls"):
        raise PortfolioError("%s: save the Console XLSX as CSV first (File > Save As > CSV)" % shown(path))
    if low.endswith(".json"):
        return "kite"
    header, _ = read_table(path)
    if "trade type" in header and ("trade date" in header or "order execution time" in header):
        return "tradebook"
    if "quantity available" in header or "average price" in header or "avg cost" in header:
        return "console"
    raise PortfolioError("%s: not a Console holdings or tradebook CSV (header %s)" % (shown(path), ", ".join(header)))


def _unwrap_kite(data):
    """Raw get_holdings output: a list, {"data": [...]}, or an MCP {"content": [{"text": "..."}]}."""
    for _ in range(4):
        if isinstance(data, list):
            if data and isinstance(data[0], dict) and data[0].get("type") == "text" and "text" in data[0]:
                data = json.loads(data[0]["text"])
                continue
            return data
        if isinstance(data, dict):
            if "content" in data and isinstance(data["content"], list):
                data = data["content"]
                continue
            for k in ("data", "holdings", "result"):
                if k in data:
                    data = data[k]
                    break
            else:
                break
    if isinstance(data, list):
        return data
    raise PortfolioError("the Kite JSON has no holdings list (want get_holdings output)")


def parse_kite(path):
    rows = _unwrap_kite(read_json(path, "Kite holdings JSON"))
    out = []
    for r in rows:
        sym = clean_symbol(r.get("tradingsymbol") or r.get("symbol"))
        if not sym:
            continue
        q = num(r.get("quantity"), 0)
        t1 = num(r.get("t1_quantity"), 0)
        if q + t1 <= 0:
            continue
        last = num(r.get("last_price"))
        close = num(r.get("close_price"))
        out.append({"symbol": sym, "qty": int(round(q + t1)), "t1_qty": int(round(t1)),
                    "avg_price": r2(num(r.get("average_price"), 0)), "last_price": r2(last),
                    "close_price": r2(close) if close else None,
                    "pnl": r2(num(r.get("pnl"))), "buy_date": None,
                    "product": str(r.get("product") or "CNC").upper(),
                    "exchange": r.get("exchange") or "NSE", "isin": r.get("isin")})
    return out


def parse_console(path):
    header, rows = read_table(path)
    out = []
    for r in rows:
        sym = clean_symbol(r.get("symbol") or r.get("tradingsymbol"))
        if not sym or sym.lower().startswith("total"):
            continue
        q = num(r.get("quantity available"), None)
        if q is None:
            q = num(r.get("qty"), num(r.get("quantity"), 0))
        q += num(r.get("quantity discrepant"), 0) + num(r.get("quantity pledged margin"), 0) + \
            num(r.get("quantity pledged loan"), 0)
        if q <= 0:
            continue
        avg = num(r.get("average price"), num(r.get("avg cost"), 0))
        close = num(r.get("previous closing price"), num(r.get("ltp")))
        upnl = num(r.get("unrealized p l"))
        last = num(r.get("ltp"))
        if last is None and upnl is not None and q:
            last = avg + upnl / q            # Console's P&L is at the previous close
        if last is None:
            last = close
        out.append({"symbol": sym, "qty": int(round(q)), "t1_qty": 0, "avg_price": r2(avg),
                    "last_price": r2(last), "close_price": r2(close) if close else None,
                    "pnl": r2(upnl), "buy_date": None, "product": "CNC",
                    "sector": (r.get("sector") or "").strip() or None, "isin": r.get("isin") or None})
    return out


def parse_tradebook(path):
    _, rows = read_table(path)
    trades = []
    for i, r in enumerate(rows):
        sym = clean_symbol(r.get("symbol") or r.get("tradingsymbol"))
        side = str(r.get("trade type") or "").strip().lower()
        if not sym or side not in ("buy", "sell"):
            continue
        d = parse_date(r.get("trade date") or r.get("order execution time"))
        q = num(r.get("quantity"), 0)
        p = num(r.get("price"), 0)
        if q <= 0:
            continue
        trades.append({"symbol": sym, "date": d, "side": side, "qty": q, "price": p,
                       "t": str(r.get("order execution time") or ""), "id": str(r.get("trade id") or ""), "i": i})
    trades.sort(key=lambda t: (t["date"], t["t"], 0 if t["side"] == "buy" else 1, t["i"]))
    return trades


def fifo(trades, groups):
    """Tradebook -> open lots per symbol and realised matches (FIFO, as Indian tax law needs for demat)."""
    open_lots, realised, notes = {}, [], []
    for t in trades:
        sym = t["symbol"]
        q = open_lots.setdefault(sym, [])
        if t["side"] == "buy":
            if q and q[-1]["date"] == t["date"] and abs(q[-1]["price"] - t["price"]) < 1e-9:
                q[-1]["qty"] += t["qty"]
            else:
                q.append({"date": t["date"], "qty": t["qty"], "price": t["price"]})
            continue
        left = t["qty"]
        while left > 1e-9:
            if not q:
                notes.append("%s: sold %g more on %s than the tradebook bought: cost unknown, buy it into "
                             "the tradebook range" % (sym, left, t["date"]))
                realised.append(_match(sym, None, None, t["date"], t["price"], left, groups))
                break
            lot = q[0]
            take = min(lot["qty"], left)
            realised.append(_match(sym, lot["date"], lot["price"], t["date"], t["price"], take, groups))
            lot["qty"] -= take
            left -= take
            if lot["qty"] <= 1e-9:
                q.pop(0)
    return {s: l for s, l in open_lots.items() if l}, realised, notes


def _match(sym, bdate, bprice, sdate, sprice, qty, groups):
    cls = asset_class(sym, groups)
    gain = None if bprice is None else (sprice - bprice) * qty
    return {"symbol": sym, "asset_class": cls, "qty": int(round(qty)),
            "buy_date": bdate.isoformat() if bdate else None, "buy_price": r2(bprice),
            "sell_date": sdate.isoformat(), "sell_price": r2(sprice),
            "gain": r2(gain), "days": (sdate - bdate).days if bdate else None,
            "term": ("long" if is_long_term(bdate, sdate) else "short") if bdate else "unknown"}


def daily_close(tdir, sym):
    """Last close from trader/data/daily/<SYM>(.adj).csv (contract 1), or None."""
    for name in (sym + ".csv",):
        p = os.path.join(tdir, "data", "daily", name)
        if not os.path.exists(p):
            continue
        last = None
        with open(p, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                c = num(row.get("close"))
                if c:
                    last = (row.get("date"), c)
        if last:
            return last
    return None


# ---------------------------------------------------------------- import

def build_holdings(files, tdir, groups, today, existing=None):
    snaps, trades, sources, notes = [], [], [], []
    for f in files:
        kind = detect(f)
        sources.append({"file": os.path.basename(f), "kind": kind})
        if kind == "kite":
            snaps.append(("kite-mcp get_holdings", parse_kite(f)))
        elif kind == "console":
            snaps.append(("kite console holdings", parse_console(f)))
        else:
            trades.extend(parse_tradebook(f))
    if len(snaps) > 1:
        notes.append("more than one holdings snapshot given: the last one (%s) wins" % sources[-1]["file"])

    lots, realised = {}, []
    if trades:
        lots, realised, n = fifo(trades, groups)
        notes.extend(n)
    elif existing:
        realised = existing.get("realised") or []
        for h in existing.get("holdings") or []:
            if h.get("lots"):
                lots[h["symbol"]] = [{"date": parse_date(l["date"]), "qty": l["qty"], "price": l["price"]}
                                     for l in h["lots"] if l.get("date")]

    if snaps:
        source, holdings = snaps[-1]
    elif existing and existing.get("holdings") and trades:
        source, holdings = existing.get("source", "existing holdings.json"), copy.deepcopy(existing["holdings"])
    else:
        source, holdings = "console tradebook (FIFO lots)", []
        for sym, ls in sorted(lots.items()):
            q = sum(l["qty"] for l in ls)
            avg = sum(l["qty"] * l["price"] for l in ls) / q
            dc = daily_close(tdir, sym)
            holdings.append({"symbol": sym, "qty": int(round(q)), "t1_qty": 0, "avg_price": r2(avg),
                             "last_price": r2(dc[1]) if dc else None, "close_price": None, "pnl": None,
                             "buy_date": None, "product": "CNC"})
            if not dc:
                notes.append("%s: no last price (tradebook only): run trader-data or import holdings too" % sym)
    if trades and snaps:
        source += " + console tradebook (FIFO lots)"

    for h in holdings:
        h.pop("lots", None)
        ls = lots.get(h["symbol"])
        if ls:
            covered = sum(l["qty"] for l in ls)
            h["lots"] = [{"date": l["date"].isoformat(), "qty": int(round(l["qty"])), "price": r2(l["price"])}
                         for l in ls]
            h["buy_date"] = h["lots"][0]["date"]
            if abs(covered - h["qty"]) > 1e-9:
                notes.append("%s: tradebook lots cover %g of %d shares (bonus, split, transfer or a "
                             "tradebook that starts too late)" % (h["symbol"], covered, h["qty"]))
        h.setdefault("product", "CNC")
    for sym in sorted(set(lots) - {h["symbol"] for h in holdings}):
        notes.append("%s: open lots in the tradebook but not in the holdings snapshot (sold since?)" % sym)
    holdings.sort(key=lambda h: h["symbol"])
    cash = existing.get("cash") if existing else None
    return {"as_of": today.isoformat(), "source": source, "cash": cash, "holdings": holdings,
            "realised": realised, "files": sources, "notes": notes}


# ---------------------------------------------------------------- show

def load_holdings(tdir):
    doc = read_json(os.path.join(tdir, "holdings.json"), "holdings (run: portfolio.py import FILE)")
    if not isinstance(doc, dict) or not isinstance(doc.get("holdings"), list):
        raise PortfolioError("holdings.json has no holdings list")
    return doc


def value_rows(doc, tdir, groups, today):
    rows = []
    for h in doc["holdings"]:
        sym = clean_symbol(h["symbol"])
        qty = int(h.get("qty") or 0)
        avg = num(h.get("avg_price"), 0)
        last = num(h.get("last_price"))
        stale = False
        if not last:
            dc = daily_close(tdir, sym)
            last, stale = (dc[1], False) if dc else (avg, True)
        close = num(h.get("close_price"))
        cls = asset_class(sym, groups)
        sector, sector_src = sector_of(sym, tdir, groups, h)
        lots = []
        for l in h.get("lots") or []:
            bd = parse_date(l.get("date"))
            if not bd:
                continue
            ld = lt_date(bd)
            lp = (last - num(l["price"], 0)) * l["qty"]
            lots.append({"date": bd.isoformat(), "qty": l["qty"], "price": l["price"],
                         "days_held": (today - bd).days, "lt_date": ld.isoformat(),
                         "long_term": today >= ld, "days_to_lt": max(0, (ld - today).days),
                         "unrealised": r2(lp)})
        rows.append({"symbol": sym, "qty": qty, "t1_qty": int(h.get("t1_qty") or 0), "avg_price": r2(avg),
                     "last_price": r2(last), "price_missing": stale, "invested": r2(qty * avg),
                     "value": r2(qty * last), "pnl": r2(qty * (last - avg)),
                     "pnl_pct": r2((last / avg - 1) * 100) if avg else None,
                     "day_change": r2(qty * (last - close)) if close else None,
                     "day_change_pct": r2((last / close - 1) * 100) if close else None,
                     "asset_class": cls, "sector": sector, "sector_source": sector_src, "lots": lots})
    total = sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = r2(r["value"] / total * 100) if total else 0.0
    rows.sort(key=lambda r: -r["value"])
    return rows, total


def show(doc, tdir, groups, today):
    rows, total = value_rows(doc, tdir, groups, today)
    inv = sum(r["invested"] for r in rows)
    dc = [r["day_change"] for r in rows if r["day_change"] is not None]
    cash = num(doc.get("cash"))
    return {"as_of": doc.get("as_of"), "today": today.isoformat(), "source": doc.get("source"),
            "value": r2(total), "invested": r2(inv), "pnl": r2(total - inv),
            "pnl_pct": r2((total / inv - 1) * 100) if inv else None,
            "day_change": r2(sum(dc)) if dc else None, "cash": cash, "holdings": rows,
            "notes": doc.get("notes") or []}


def show_text(s):
    L = ["Portfolio as of %s (%s)" % (s["as_of"], s["source"]), ""]
    L.append("%-12s %6s %10s %10s %12s %12s %7s %7s %10s" %
             ("symbol", "qty", "avg", "last", "value", "P&L", "P&L%", "weight", "day"))
    for r in s["holdings"]:
        q = "%d" % r["qty"] + ("*" if r["t1_qty"] else "")
        L.append("%-12s %6s %10.2f %10.2f %12s %12s %7s %7s %10s" % (
            r["symbol"], q, r["avg_price"], r["last_price"], inr(r["value"]), inr(r["pnl"]),
            pct(r["pnl_pct"]), pct(r["weight"]), "-" if r["day_change"] is None else inr(r["day_change"])))
    L.append("")
    L.append("Value %s, invested %s, P&L %s (%s)%s" % (
        inr(s["value"]), inr(s["invested"]), inr(s["pnl"]), pct(s["pnl_pct"]),
        "" if s["day_change"] is None else ", today %s" % inr(s["day_change"])))
    if any(r["t1_qty"] for r in s["holdings"]):
        L.append("* includes T1 shares (bought, not yet in your demat)")
    if any(r["price_missing"] for r in s["holdings"]):
        L.append("Some prices are missing: valued at your average price. Import fresh holdings.")
    lot_rows = [(r["symbol"], l) for r in s["holdings"] for l in r["lots"]]
    if lot_rows:
        L += ["", "Lots (FIFO, oldest first; long-term = held more than 12 months)",
              "%-12s %-10s %6s %9s %6s %-10s %s" % ("symbol", "bought", "qty", "price", "days", "LT from", "now")]
        for sym, l in lot_rows:
            L.append("%-12s %-10s %6d %9.2f %6d %-10s %s" % (
                sym, l["date"], l["qty"], l["price"], l["days_held"], l["lt_date"],
                "long-term" if l["long_term"] else "short-term, %d days to go" % l["days_to_lt"]))
    else:
        L += ["", "No buy dates: import your Console tradebook to see lots and long-term dates."]
    for n in s["notes"]:
        L.append("note: " + n)
    return "\n".join(L)


# ---------------------------------------------------------------- risk

RISK_DEFAULTS = {"max_stock_pct": 15.0, "max_sector_pct": 35.0, "max_group_pct": 25.0,
                 "max_psu_pct": 35.0, "max_commodity_pct": 30.0, "min_index_pct": 0.0}


def risk(doc, tdir, groups, today, limits=None):
    lim = dict(RISK_DEFAULTS)
    lim.update({k: v for k, v in (limits or {}).items() if v is not None})
    rows, total = value_rows(doc, tdir, groups, today)
    checks = []

    def add(name, level, msg, value=None, limit=None, symbols=None):
        checks.append({"check": name, "level": level, "message": msg, "value": r2(value),
                       "limit": limit, "symbols": symbols or []})

    if not total:
        raise PortfolioError("nothing to check: the holdings have no value")
    single = [r for r in rows if r["asset_class"] in ("equity", "sector_etf")]
    for r in single:
        if r["weight"] > lim["max_stock_pct"]:
            add("single stock", "warn", "%s is %s of the portfolio (limit %s)" % (
                r["symbol"], pct(r["weight"]), pct(lim["max_stock_pct"])), r["weight"], lim["max_stock_pct"],
                [r["symbol"]])
    if not any(c["check"] == "single stock" for c in checks):
        top = max(single, key=lambda r: r["weight"]) if single else None
        add("single stock", "ok", "largest stock %s" % ("%s at %s" % (top["symbol"], pct(top["weight"]))
                                                       if top else "none"), top and top["weight"],
            lim["max_stock_pct"])

    sectors = {}
    for r in rows:
        if r["asset_class"] in ("equity", "sector_etf"):
            sectors.setdefault(r["sector"], []).append(r)
    sec_out = []
    for sec, rs in sorted(sectors.items(), key=lambda kv: -sum(x["weight"] for x in kv[1])):
        w = sum(x["weight"] for x in rs)
        sec_out.append({"sector": sec, "weight": r2(w), "symbols": [x["symbol"] for x in rs]})
        if w > lim["max_sector_pct"] and sec != "Unknown":
            add("sector", "warn", "%s is %s (limit %s): %s" % (sec, pct(w), pct(lim["max_sector_pct"]),
                                                              ", ".join(x["symbol"] for x in rs)),
                w, lim["max_sector_pct"], [x["symbol"] for x in rs])
    unknown = [x["symbol"] for x in sectors.get("Unknown", [])]
    if unknown:
        add("sector", "info", "no sector for %s: run trader-invest to fetch fundamentals, or add them to "
                              "groups.json sectors" % ", ".join(unknown), symbols=unknown)

    themes = []
    for name, syms in sorted((groups.get("themes") or {}).items()):
        rs = [r for r in rows if r["symbol"] in syms]
        if rs:
            w = sum(r["weight"] for r in rs)
            themes.append({"theme": name, "weight": r2(w), "symbols": [r["symbol"] for r in rs]})
            if w > lim["max_sector_pct"]:
                add("theme", "warn", "theme %s is %s (limit %s)" % (name, pct(w), pct(lim["max_sector_pct"])),
                    w, lim["max_sector_pct"], [r["symbol"] for r in rs])

    grp = []
    for name, syms in sorted((groups.get("groups") or {}).items()):
        rs = [r for r in rows if r["symbol"] in syms and r["asset_class"] == "equity"]
        if rs:
            w = sum(r["weight"] for r in rs)
            grp.append({"group": name, "weight": r2(w), "symbols": [r["symbol"] for r in rs]})
            level = "warn" if w > lim["max_group_pct"] else "ok"
            add("group", level, "%s group: %s in %s" % (name, pct(w), ", ".join(r["symbol"] for r in rs)) +
                (" (limit %s): one promoter's governance risk across all of them" % pct(lim["max_group_pct"])
                 if level == "warn" else ""), w, lim["max_group_pct"], [r["symbol"] for r in rs])

    psu = [r for r in rows if r["symbol"] in set(groups.get("psu") or [])]
    psu_w = sum(r["weight"] for r in psu)
    if psu:
        add("psu", "warn" if psu_w > lim["max_psu_pct"] else "ok",
            "PSU (government-owned) stocks are %s: %s. They tend to move together on policy, "
            "divestment and dividend news." % (pct(psu_w), ", ".join(r["symbol"] for r in psu)),
            psu_w, lim["max_psu_pct"], [r["symbol"] for r in psu])

    classes = {}
    for r in rows:
        classes[r["asset_class"]] = classes.get(r["asset_class"], 0.0) + r["weight"]
    commodity = classes.get("gold_etf", 0.0) + classes.get("silver_etf", 0.0)
    if commodity > lim["max_commodity_pct"]:
        add("commodity", "warn", "gold and silver ETFs are %s of the portfolio (limit %s). They earn no "
                                 "dividends and can fall for years; silver swings more than gold."
            % (pct(commodity), pct(lim["max_commodity_pct"])), commodity, lim["max_commodity_pct"])
    else:
        add("commodity", "ok", "gold and silver ETFs %s" % pct(commodity), commodity, lim["max_commodity_pct"])
    idx = classes.get("index_etf", 0.0)
    if idx <= lim["min_index_pct"]:
        add("broad index", "warn", "no broad index exposure (Nifty 50 / Next 50 / Midcap ETF): every rupee "
                                   "rides on a few picks. A broad index ETF is the usual core of a long-term "
                                   "portfolio.", idx, lim["min_index_pct"])
    else:
        add("broad index", "ok", "broad index ETFs %s" % pct(idx), idx, lim["min_index_pct"])
    stocks = [r for r in rows if r["asset_class"] == "equity"]
    if 0 < len(stocks) < 5 and classes.get("equity", 0) > 30:
        add("diversification", "info", "%d individual stocks make up %s: each one moves the whole portfolio"
            % (len(stocks), pct(classes["equity"])), classes["equity"])
    return {"today": today.isoformat(), "value": r2(total), "limits": lim, "checks": checks,
            "asset_classes": [{"asset_class": k, "label": ASSET_LABEL.get(k, k), "weight": r2(v)}
                              for k, v in sorted(classes.items(), key=lambda kv: -kv[1])],
            "commodity_pct": r2(commodity), "index_pct": r2(idx), "psu_pct": r2(psu_w),
            "sectors": sec_out, "themes": themes, "groups": grp,
            "warnings": sum(1 for c in checks if c["level"] == "warn")}


def risk_text(r):
    L = ["Concentration check, portfolio value %s" % inr(r["value"]), "", "Asset classes:"]
    for a in r["asset_classes"]:
        L.append("  %-20s %s" % (a["label"], pct(a["weight"])))
    if r["sectors"]:
        L.append("Sectors (stocks and sector ETFs):")
        for s in r["sectors"]:
            L.append("  %-28s %7s  %s" % (s["sector"], pct(s["weight"]), ", ".join(s["symbols"])))
    L.append("")
    for c in r["checks"]:
        L.append("%-5s %-16s %s" % ({"warn": "WARN", "ok": "ok", "info": "info"}[c["level"]], c["check"],
                                     c["message"]))
    L += ["", "%d warning(s). These are rules of thumb, not a verdict on any stock." % r["warnings"],
          "Education and tooling, not financial advice."]
    return "\n".join(L)


# ---------------------------------------------------------------- targets and prices

def parse_targets(path):
    data = read_json(path, "targets file")
    meta = data if isinstance(data, dict) else {}
    rows = data.get("weights", data.get("targets")) if isinstance(data, dict) else data
    out = []
    if isinstance(rows, dict):
        rows = [{"symbol": k, "weight": v} for k, v in rows.items()]
    if not isinstance(rows, list) or not rows:
        raise PortfolioError("targets file needs a weights list: [{\"symbol\", \"weight\"}]")
    for i, r in enumerate(rows):
        sym = clean_symbol(r.get("symbol") or r.get("ticker"))
        w = num(r.get("weight", r.get("weight_pct", r.get("pct"))))
        if not sym or w is None or w < 0:
            raise PortfolioError("targets weights[%d] needs a symbol and a weight >= 0" % i)
        out.append({"symbol": sym, "weight": w, "name": r.get("name", ""), "price": num(r.get("price"))})
    tot = sum(r["weight"] for r in out)
    if tot <= 0:
        raise PortfolioError("target weights add up to zero")
    for r in out:
        r["weight"] = r["weight"] / tot * 100.0
    return out, meta


def price_map(doc, tdir, prices_path=None, targets=None):
    """Price for each symbol: --prices / trader/prices.json > holdings last_price > daily close > targets."""
    out, src = {}, {}
    for t in targets or []:
        if t.get("price"):
            out[t["symbol"]], src[t["symbol"]] = t["price"], "targets file"
    syms = {t["symbol"] for t in targets or []} | {clean_symbol(h["symbol"]) for h in doc.get("holdings", [])}
    for s in syms:
        dc = daily_close(tdir, s)
        if dc:
            out[s], src[s] = dc[1], "daily close %s" % dc[0]
    for h in doc.get("holdings", []):
        if num(h.get("last_price")):
            out[clean_symbol(h["symbol"])], src[clean_symbol(h["symbol"])] = num(h["last_price"]), "holdings"
    p = prices_path or os.path.join(tdir, "prices.json")
    pj = read_json(p, "prices", required=bool(prices_path))
    if isinstance(pj, dict):
        for k, v in (pj.get("prices", pj) or {}).items():
            if num(v):
                out[clean_symbol(k)], src[clean_symbol(k)] = num(v), os.path.basename(p)
    return out, src


def tick_round(price, up, cls):
    tick = 0.01
    if cls == "equity":
        for lim, t in ((250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.0)):
            if price < lim:
                tick = t
                break
        else:
            tick = 5.0
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 2)


def make_order(sym, side, qty, price, cls, why, limit_pct=None):
    o = {"symbol": sym, "side": side, "qty": str(int(qty)), "product": "CNC", "type": "market",
         "time_in_force": "day"}
    if limit_pct is not None:
        lp = price * (1 + limit_pct / 100.0) if side == "buy" else price * (1 - limit_pct / 100.0)
        o["type"] = "limit"
        o["limit_price"] = "%.2f" % tick_round(lp, side == "buy", cls)
    o["why"] = why
    return o


def write_orders(path, orders, source, created_by="trader-portfolio"):
    write_json(path, {
        "created_by": created_by,
        "created_at": utc_now().replace(microsecond=0).isoformat() + "Z",
        "status": "proposed",
        "note": ("Proposals only. Nothing is placed until trader-risk passes each order "
                 "and you say yes to it in the chat."),
        "source": source,
        "orders": orders})


# ---------------------------------------------------------------- tax

def lots_of(doc, sym):
    for h in doc.get("holdings", []):
        if clean_symbol(h["symbol"]) == sym:
            return [{"date": parse_date(l["date"]), "qty": int(l["qty"]), "price": num(l["price"], 0)}
                    for l in (h.get("lots") or []) if l.get("date")]
    return []


def realised_in(doc, start, end):
    out = []
    for m in doc.get("realised") or []:
        d = parse_date(m.get("sell_date"))
        if d and start <= d <= end:
            out.append(m)
    return out


def bucket(cls, term):
    eq = is_equity_for_tax(cls)
    if cls in SLAB_ALWAYS:
        return "other_st"
    if term == "long":
        return "equity_lt" if eq else "other_lt"
    return "equity_st" if eq else "other_st"


def tax_estimate(buckets, slab_pct=DEFAULT_SLAB):
    """Net per-bucket gains -> set-off, Rs 1.25 lakh exemption, tax. Returns a dict (estimate)."""
    b = {k: float(buckets.get(k, 0.0)) for k in ("equity_st", "other_st", "equity_lt", "other_lt")}
    gross = dict(b)
    # short-term losses offset short-term gains (higher rate first), then long-term gains
    st_loss = -sum(v for k, v in b.items() if k.endswith("_st") and v < 0)
    lt_loss = -sum(v for k, v in b.items() if k.endswith("_lt") and v < 0)
    for k in b:
        b[k] = max(0.0, b[k])
    st_rate = {"other_st": slab_pct, "equity_st": RATES["equity_st"]}
    for k in sorted(("other_st", "equity_st"), key=lambda k: -st_rate[k]):
        use = min(st_loss, b[k])
        b[k] -= use
        st_loss -= use
    for k in ("other_lt", "equity_lt"):         # no exemption on other_lt: offset it first
        use = min(st_loss, b[k])
        b[k] -= use
        st_loss -= use
        use = min(lt_loss, b[k])
        b[k] -= use
        lt_loss -= use
    exempt = min(LTCG_EXEMPTION, b["equity_lt"])
    taxable_eq_lt = b["equity_lt"] - exempt
    tax = (b["equity_st"] * RATES["equity_st"] + b["other_st"] * slab_pct +
           taxable_eq_lt * RATES["equity_lt"] + b["other_lt"] * RATES["other_lt"]) / 100.0
    return {"gross": {k: r2(v) for k, v in gross.items()},
            "after_setoff": {k: r2(v) for k, v in b.items()},
            "exemption_used": r2(exempt), "exemption_left": r2(LTCG_EXEMPTION - exempt),
            "taxable_equity_lt": r2(taxable_eq_lt),
            "tax": r2(tax), "tax_with_cess": r2(tax * (1 + CESS_PCT / 100.0)),
            "carry_forward": {"short_term_loss": r2(st_loss), "long_term_loss": r2(lt_loss)},
            "slab_pct": slab_pct}


def sum_buckets(matches):
    out = {}
    unknown = 0
    for m in matches:
        if m.get("gain") is None:
            unknown += 1
            continue
        k = bucket(m.get("asset_class", "equity"), m.get("term"))
        out[k] = out.get(k, 0.0) + float(m["gain"])
    return out, unknown


def tax_report(doc, tdir, groups, today, fy=None, slab_pct=DEFAULT_SLAB):
    start, end, label = fy_bounds(fy or fy_of(today))
    matches = realised_in(doc, start, end)
    for m in matches:
        m.setdefault("asset_class", asset_class(m["symbol"], groups))
    b, unknown = sum_buckets(matches)
    est = tax_estimate(b, slab_pct)
    rows, _ = value_rows(doc, tdir, groups, today)
    ideas = []
    # 1. book long-term equity gains up to the exemption left (sell and buy back)
    left = est["exemption_left"]
    in_fy = start <= today <= end
    lt_gain_lots = []
    for r in rows:
        if not is_equity_for_tax(r["asset_class"]):
            continue
        for l in r["lots"]:
            if l["long_term"] and l["unrealised"] > 0:
                lt_gain_lots.append((r, l))
    if in_fy and left > 0 and lt_gain_lots:
        for r, l in lt_gain_lots:
            if left <= 0:
                break
            per = r["last_price"] - l["price"]
            q = min(l["qty"], int(left // per)) if per > 0 else 0
            if q <= 0:
                continue
            g = q * per
            left -= g
            ideas.append({"kind": "harvest ltcg", "symbol": r["symbol"], "qty": q, "gain": r2(g),
                          "message": "sell %d %s (lot of %s, long-term) and buy back: books %s of long-term gain "
                                     "inside the ₹1.25 lakh exemption, resetting your cost higher. FIFO: "
                                     "the oldest lots sell first." % (q, r["symbol"], l["date"], inr(g))})
    # 2. losses to offset taxable gains
    taxable = est["after_setoff"]["equity_st"] + est["after_setoff"]["other_st"] + \
        est["after_setoff"]["other_lt"] + est["taxable_equity_lt"]
    if in_fy and taxable > 0:
        for r in rows:
            for l in r["lots"]:
                if l["unrealised"] < 0:
                    term = "long-term" if l["long_term"] else "short-term"
                    ideas.append({"kind": "harvest loss", "symbol": r["symbol"], "qty": l["qty"],
                                  "gain": l["unrealised"],
                                  "message": "%s lot of %s: %s %s loss could offset %s of taxable gains this year%s"
                                             % (r["symbol"], l["date"], inr(-l["unrealised"]), term,
                                                inr(taxable), "" if not l["long_term"] else
                                                " (a long-term loss offsets long-term gains only)")})
    # 3. short-term lots close to long-term
    for r in rows:
        for l in r["lots"]:
            if not l["long_term"] and 0 < l["days_to_lt"] <= 60 and l["unrealised"] > 0:
                ideas.append({"kind": "wait", "symbol": r["symbol"], "qty": l["qty"], "gain": l["unrealised"],
                              "message": "%s lot of %s turns long-term on %s (%d days): selling before then is "
                                         "taxed as short-term" % (r["symbol"], l["date"], l["lt_date"],
                                                                  l["days_to_lt"])})
    notes = ["Estimate, check with a CA. Gains are price-to-price: charges, STT and bonus/split adjustments "
             "are not in it, and grandfathering (shares bought before 1 Feb 2018) is not applied.",
             "Equity STCG 20%, equity LTCG 12.5% above ₹1.25 lakh; gold/silver ETF short-term gains at your "
             "slab (" + ("%g" % slab_pct) + "% assumed, --slab), long-term 12.5% with no exemption; plus 4% "
             "cess. Surcharge not included."]
    if unknown:
        notes.append("%d sale(s) have no buy in the tradebook: their gain is unknown and not counted. "
                     "Import a tradebook from before the first buy." % unknown)
    if not doc.get("realised") and not any(r["lots"] for r in rows):
        notes.append("No tradebook imported: import the Console tradebook CSV for realised gains and lots.")
    return {"fy": label, "from": start.isoformat(), "to": end.isoformat(), "realised": matches,
            "estimate": est, "ideas": ideas, "notes": notes}


def tax_text(t):
    e = t["estimate"]
    g, a = e["gross"], e["after_setoff"]
    L = ["Capital gains FY %s (%s to %s), ESTIMATE" % (t["fy"], t["from"], t["to"]), ""]
    if t["realised"]:
        L.append("%-12s %-10s %-10s %5s %10s %10s %-6s" % ("symbol", "bought", "sold", "qty", "gain", "class", "term"))
        for m in t["realised"]:
            L.append("%-12s %-10s %-10s %5d %10s %10s %-6s" % (
                m["symbol"], m["buy_date"] or "?", m["sell_date"], m["qty"],
                "?" if m["gain"] is None else inr(m["gain"]), m["asset_class"], m["term"]))
    else:
        L.append("No sales in this year.")
    L += ["", "%-26s %12s %12s" % ("bucket", "net", "after set-off")]
    for k, name in (("equity_st", "equity short-term (20%)"), ("equity_lt", "equity long-term (12.5%)"),
                    ("other_st", "gold/silver short (slab)"), ("other_lt", "gold/silver long (12.5%)")):
        L.append("%-26s %12s %12s" % (name, inr(g[k]), inr(a[k])))
    L.append("LTCG exemption used %s of ₹1,25,000, left %s" % (inr(e["exemption_used"]), inr(e["exemption_left"])))
    L.append("Estimated tax %s (%s with 4%% cess)" % (inr(e["tax"]), inr(e["tax_with_cess"])))
    cf = e["carry_forward"]
    if cf["short_term_loss"] or cf["long_term_loss"]:
        L.append("Losses to carry forward (file your return on time): short %s, long %s" % (
            inr(cf["short_term_loss"]), inr(cf["long_term_loss"])))
    if t["ideas"]:
        L += ["", "Ideas (check with a CA before acting):"]
        for i in t["ideas"]:
            L.append("- " + i["message"])
    L.append("")
    L.extend(t["notes"])
    L.append("Education and tooling, not financial advice.")
    return "\n".join(L)


# ---------------------------------------------------------------- rebalance

def plan_sell(sym, want, lots, price, cls, today, held_qty, mode):
    """FIFO sale of up to `want` shares. Tax-aware mode stops before a short-term gain lot.

    Returns (qty, matches, held_back_reason)."""
    if not lots:
        m = [{"symbol": sym, "asset_class": cls, "qty": want, "gain": None, "term": "unknown"}]
        return want, m, None
    covered = sum(l["qty"] for l in lots)
    queue = list(lots)
    if covered < held_qty:                      # shares older than the tradebook: unknown cost, sold first
        queue.insert(0, {"date": None, "qty": held_qty - covered, "price": None})
    qty, matches, why = 0, [], None
    for l in queue:
        if qty >= want:
            break
        take = min(l["qty"], want - qty)
        if l["date"] is None:
            matches.append({"symbol": sym, "asset_class": cls, "qty": take, "gain": None, "term": "unknown"})
            qty += take
            continue
        term = "long" if is_long_term(l["date"], today) else "short"
        gain = (price - l["price"]) * take
        if mode == "aware" and term == "short" and gain > 0 and cls not in SLAB_ALWAYS:
            why = ("held back %d share(s): the next FIFO lot (%s) is a short-term gain until %s"
                   % (want - qty, l["date"].isoformat(), lt_date(l["date"]).isoformat()))
            break
        matches.append({"symbol": sym, "asset_class": cls, "qty": take, "buy_date": l["date"].isoformat(),
                        "gain": r2(gain), "term": term})
        qty += take
    return qty, matches, why


def rebalance(doc, tdir, groups, today, targets, meta, cash=None, prices_path=None, min_trade=None,
              tax_mode="aware", limit_pct=None, slab_pct=DEFAULT_SLAB, others=None):
    ch = load_charges(tdir)
    prices, psrc = price_map(doc, tdir, prices_path, targets)
    cash = num(cash, num(doc.get("cash"), None))
    notes, warnings = [], []
    if cash is None:
        cash = 0.0
        notes.append("no cash given: rebalancing within your holdings only (pass --cash N to add money)")
    min_trade = num(min_trade, num(meta.get("min_trade_inr"), 500.0))
    cash_pct = num(meta.get("cash_pct"), 0.0)
    others = (others or meta.get("others") or "hold").lower()
    tw = {t["symbol"]: t["weight"] for t in targets}
    held = {clean_symbol(h["symbol"]): h for h in doc["holdings"]}
    missing = [s for s in tw if s not in prices]
    if missing:
        raise PortfolioError("no price for %s: pass --prices FILE, run trader-data, or put \"price\" in the "
                             "targets file" % ", ".join(missing))
    universe = set(tw) | (set(held) if others == "sell" else set())
    kept = sorted(set(held) - universe)
    if kept:
        notes.append("not in targets, left alone (\"others\": \"hold\"): %s" % ", ".join(kept))
    cur = {s: int(held[s]["qty"]) * prices.get(s, num(held[s].get("avg_price"), 0)) if s in held else 0.0
           for s in universe}
    base = sum(cur.values()) + cash
    investable = base * (1 - cash_pct / 100.0)
    rows = {}
    for s in sorted(universe):
        tgt = tw.get(s, 0.0) / 100.0 * investable
        rows[s] = {"symbol": s, "price": r2(prices[s]), "price_source": psrc.get(s),
                   "asset_class": asset_class(s, groups), "qty_now": int(held[s]["qty"]) if s in held else 0,
                   "value_now": r2(cur[s]), "weight_now": r2(cur[s] / base * 100) if base else 0,
                   "target_pct": r2(tw.get(s, 0.0)), "target_value": r2(tgt), "delta": r2(tgt - cur[s]),
                   "side": None, "qty": 0, "order_value": 0.0, "charges": 0.0, "note": None}

    # realised so far this FY: the exemption left for the sells' estimate
    start, end, label = fy_bounds(fy_of(today))
    prior, _ = sum_buckets([dict(m, asset_class=m.get("asset_class") or asset_class(m["symbol"], groups))
                            for m in realised_in(doc, start, end)])

    orders, sell_matches = [], []
    proceeds = 0.0
    for s, r in sorted(rows.items(), key=lambda kv: kv[1]["delta"]):
        if r["delta"] >= -min_trade or r["qty_now"] == 0:
            continue
        h = held[s]
        sellable = r["qty_now"] - int(h.get("t1_qty") or 0)
        want = min(sellable, int(math.floor(-r["delta"] / r["price"] + 1e-9)))
        if want <= 0:
            continue
        q, matches, why = plan_sell(s, want, lots_of(doc, s), r["price"], r["asset_class"], today,
                                    r["qty_now"], tax_mode)
        if why:
            r["note"] = why
        if q <= 0 or q * r["price"] < min_trade:
            if q > 0:
                r["note"] = (r["note"] + "; " if r["note"] else "") + "sale under the minimum trade"
            continue
        c = delivery_charges(sell_value=q * r["price"], sell_scrips=1, ch=ch)
        r.update(side="sell", qty=q, order_value=r2(q * r["price"]), charges=c["total"])
        proceeds += q * r["price"] - c["total"]
        sell_matches.extend(matches)
        orders.append(make_order(s, "sell", q, r["price"], r["asset_class"],
                                 "rebalance %s: %s now, target %s" % (s, pct(r["weight_now"]), pct(r["target_pct"])),
                                 limit_pct))
        if int(h.get("t1_qty") or 0):
            warnings.append("%s: %d T1 share(s) not sold (they are not in your demat yet)" % (s, h["t1_qty"]))

    avail = cash + proceeds - base * cash_pct / 100.0
    avail = max(0.0, avail)
    buys = sorted((r for r in rows.values() if r["delta"] > min_trade), key=lambda r: -r["delta"])

    def cost(q, p):
        return q * p + delivery_charges(buy_value=q * p, ch=ch)["total"]

    bought = {}
    for r in buys:
        q = int(math.floor(r["delta"] / r["price"] + 1e-9))
        while q > 0 and cost(q, r["price"]) > avail:
            q -= 1
        if q <= 0 or q * r["price"] < min_trade:
            if r["price"] > r["delta"]:
                r["note"] = "short by %s, less than one share (%s)" % (inr(r["delta"]), inr(r["price"]))
            elif q * r["price"] < min_trade and q > 0:
                r["note"] = "buy under the minimum trade (%s)" % inr(min_trade)
            else:
                r["note"] = "not enough cash for a share after the sales and the larger buys"
            continue
        bought[r["symbol"]] = q
        avail -= cost(q, r["price"])
    # spare cash: one more share at a time where the shortfall is largest
    while True:
        best, gap = None, 0.0
        for r in buys:
            q = bought.get(r["symbol"], 0)
            short = r["delta"] - q * r["price"]
            if short > r["price"] / 2 and short > gap and cost(q + 1, r["price"]) - cost(q, r["price"]) <= avail \
                    and (q > 0 or r["price"] >= min_trade):
                best, gap = r, short
        if not best:
            break
        q = bought.get(best["symbol"], 0)
        avail -= cost(q + 1, best["price"]) - cost(q, best["price"])
        bought[best["symbol"]] = q + 1
    for r in buys:
        q = bought.get(r["symbol"], 0)
        if not q:
            continue
        r["note"] = None
        c = delivery_charges(buy_value=q * r["price"], ch=ch)
        r.update(side="buy", qty=q, order_value=r2(q * r["price"]), charges=c["total"])
        orders.append(make_order(r["symbol"], "buy", q, r["price"], r["asset_class"],
                                 "rebalance %s: %s now, target %s" % (r["symbol"], pct(r["weight_now"]),
                                                                       pct(r["target_pct"])), limit_pct))
    # tax estimate: this FY so far vs this FY with these sells
    after = dict(prior)
    for k, v in sum_buckets(sell_matches)[0].items():
        after[k] = after.get(k, 0.0) + v
    t0, t1 = tax_estimate(prior, slab_pct), tax_estimate(after, slab_pct)
    if any(m.get("gain") is None for m in sell_matches):
        notes.append("some sold shares have no buy date (import the tradebook): their tax is not estimated")
    total_ch = sum(r["charges"] for r in rows.values())
    for r in rows.values():
        r["value_after"] = r2(r["value_now"] + (r["order_value"] if r["side"] == "buy" else -r["order_value"]))
        r["weight_after"] = r2(r["value_after"] / base * 100) if base else 0
    if not orders:
        notes.append("already within the minimum trade (%s) of every target: no orders" % inr(min_trade))
    return {"today": today.isoformat(), "base": r2(base), "investable": r2(investable), "cash": r2(cash),
            "cash_left": r2(avail + base * cash_pct / 100.0), "min_trade": min_trade, "tax_mode": tax_mode,
            "rows": [rows[s] for s in sorted(rows, key=lambda s: -rows[s]["target_value"])],
            "orders": orders, "charges": r2(total_ch), "charges_label": charges_label(ch),
            "tax": {"fy": label, "sells": sell_matches, "extra_tax": r2(t1["tax_with_cess"] - t0["tax_with_cess"]),
                    "before": t0, "after": t1},
            "notes": notes, "warnings": warnings}


def rebalance_text(r):
    L = ["Rebalance toward targets: base %s (holdings in targets + cash %s), min trade %s"
         % (inr(r["base"]), inr(r["cash"]), inr(r["min_trade"])), "",
         "%-12s %9s %8s %8s %8s %6s %10s %8s" % ("symbol", "price", "now", "target", "after", "side", "qty", "value")]
    for x in r["rows"]:
        L.append("%-12s %9.2f %8s %8s %8s %6s %10s %8s" % (
            x["symbol"], x["price"], pct(x["weight_now"]), pct(x["target_pct"]), pct(x["weight_after"]),
            x["side"] or "-", x["qty"] or "-", inr(x["order_value"]) if x["qty"] else "-"))
        if x["note"]:
            L.append("             %s" % x["note"])
    L += ["", "Charges %s (%s, Zerodha delivery: STT 0.1%% both sides, stamp, exchange, GST, DP %s a scrip "
          "sold). Cash left about %s." % (inr(r["charges"]), r["charges_label"], inr(15.34), inr(r["cash_left"]))]
    t = r["tax"]
    if t["sells"]:
        L.append("Tax from these sales (FY %s, FIFO, estimate, check with a CA): about %s more"
                 % (t["fy"], inr(t["extra_tax"])))
        for m in t["sells"]:
            L.append("  %s %d from %s: %s %s" % (m["symbol"], m["qty"], m.get("buy_date") or "unknown date",
                                                 "?" if m["gain"] is None else inr(m["gain"]), m["term"]))
    for w in r["warnings"]:
        L.append("warning: " + w)
    for n in r["notes"]:
        L.append("note: " + n)
    L += ["", "Proposals only (CNC). trader-risk checks them; nothing is placed until you say yes.",
          "Education and tooling, not financial advice."]
    return "\n".join(L)


# ---------------------------------------------------------------- sip

def sip(doc, tdir, groups, today, targets, monthly, carry_doc=None, prices_path=None, limit_pct=None):
    if monthly is None or monthly <= 0:
        raise PortfolioError("--monthly wants a positive amount in rupees")
    ch = load_charges(tdir)
    prices, psrc = price_map(doc or {"holdings": []}, tdir, prices_path, targets)
    missing = [t["symbol"] for t in targets if t["symbol"] not in prices]
    if missing:
        raise PortfolioError("no price for %s: pass --prices FILE or put \"price\" in the targets file"
                             % ", ".join(missing))
    pots_in = dict((carry_doc or {}).get("carry") or {})
    rows, orders = [], []
    spent_total = 0.0
    for t in targets:
        s, p = t["symbol"], prices[t["symbol"]]
        pot = monthly * t["weight"] / 100.0 + num(pots_in.pop(s, 0), 0)
        q = int(math.floor(pot / p + 1e-9))
        while q > 0 and q * p + delivery_charges(buy_value=q * p, ch=ch)["total"] > pot:
            q -= 1
        c = delivery_charges(buy_value=q * p, ch=ch)["total"] if q else 0.0
        spent = q * p + c
        spent_total += spent
        rows.append({"symbol": s, "weight": r2(t["weight"]), "price": r2(p), "price_source": psrc.get(s),
                     "pot": r2(pot), "qty": q, "value": r2(q * p), "charges": r2(c), "carry": r2(pot - spent),
                     "note": None if q else "carried: one share costs %s, %s saved so far" % (inr(p), inr(pot))})
        if q:
            orders.append(make_order(s, "buy", q, p, asset_class(s, groups),
                                     "monthly SIP %s: %s of %s" % (s, pct(t["weight"]), inr(monthly)), limit_pct))
    dropped = {k: v for k, v in pots_in.items() if num(v)}
    carry = {r["symbol"]: r["carry"] for r in rows}
    return {"today": today.isoformat(), "month": today.strftime("%Y-%m"), "monthly": monthly,
            "carry_in": r2(sum(num(v, 0) for v in ((carry_doc or {}).get("carry") or {}).values())),
            "rows": rows, "orders": orders, "spent": r2(spent_total),
            "carry_out": r2(sum(carry.values())), "carry": carry, "charges_label": charges_label(ch),
            "dropped_carry": dropped}


def sip_text(s):
    L = ["SIP for %s: %s this month + %s carried in" % (s["month"], inr(s["monthly"]), inr(s["carry_in"])), "",
         "%-12s %7s %9s %10s %5s %10s %8s %10s" % ("symbol", "weight", "price", "pot", "qty", "value", "charges",
                                                  "carry")]
    for r in s["rows"]:
        L.append("%-12s %7s %9.2f %10s %5d %10s %8s %10s" % (
            r["symbol"], pct(r["weight"]), r["price"], inr(r["pot"]), r["qty"], inr(r["value"]),
            inr(r["charges"]), inr(r["carry"])))
        if r["note"]:
            L.append("             " + r["note"])
    if s.get("warning"):
        L.append("warning: " + s["warning"])
    L += ["", "Spend %s, carry %s to next month (charges are %s)." % (inr(s["spent"]), inr(s["carry_out"]),
                                                                       s["charges_label"])]
    if s["dropped_carry"]:
        L.append("note: carry for symbols no longer in targets dropped: %s" % ", ".join(sorted(s["dropped_carry"])))
    L += ["Proposals only (CNC). Nothing is bought until you say yes.",
          "Education and tooling, not financial advice."]
    return "\n".join(L)


# ---------------------------------------------------------------- cli

def emit(obj, text_fn, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        print(text_fn(obj))


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS, help="the trader/ folder (default trader)")
    common.add_argument("--today", default=argparse.SUPPRESS, help="YYYY-MM-DD (default: today in IST)")
    common.add_argument("--groups", default=argparse.SUPPRESS, help="groups file (default: this skill's groups.json)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print JSON")
    ap = argparse.ArgumentParser(description="Long-term holdings manager (trader-portfolio).", parents=[common])
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("import", parents=[common], help="build trader/holdings.json from Kite/Console files")
    p.add_argument("files", nargs="+")
    p.add_argument("--fresh", action="store_true", help="ignore the existing holdings.json")
    p.add_argument("--cash", type=float, help="record your free cash")
    sub.add_parser("show", parents=[common], help="value, P&L, weights, lots")
    p = sub.add_parser("risk", parents=[common], help="concentration checks")
    p.add_argument("--max-stock", type=float)
    p.add_argument("--max-sector", type=float)
    p.add_argument("--max-group", type=float)
    p.add_argument("--max-commodity", type=float)
    for name in ("rebalance", "sip"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--targets", required=True)
        p.add_argument("--prices", help="prices JSON {\"prices\": {SYM: price}} (default trader/prices.json)")
        p.add_argument("--limit-pct", type=float, help="limit orders this %% through the price instead of market")
        p.add_argument("--write", nargs="?", const="", metavar="PATH",
                       help="write trader/orders.json (sip also updates trader/sip.json)")
        if name == "rebalance":
            p.add_argument("--cash", type=float, help="new money to add (default: holdings.json cash)")
            p.add_argument("--min-trade", type=float, help="skip trades under this (default 500)")
            p.add_argument("--tax-mode", choices=("aware", "ignore"), default="aware")
            p.add_argument("--others", choices=("hold", "sell"), help="holdings not in targets")
            p.add_argument("--slab", type=float, default=DEFAULT_SLAB)
        else:
            p.add_argument("--monthly", type=float, required=True)
    p = sub.add_parser("tax", parents=[common], help="realised STCG/LTCG for a financial year")
    p.add_argument("--fy")
    p.add_argument("--slab", type=float, default=DEFAULT_SLAB, help="your income-tax slab %% (default 30)")
    a = ap.parse_args(argv)
    tdir = getattr(a, "dir", "trader")
    as_json = getattr(a, "json", False)
    try:
        today = parse_date(getattr(a, "today", None)) or ist_today()
        groups = load_groups(getattr(a, "groups", None))
        if a.cmd == "import":
            path = os.path.join(tdir, "holdings.json")
            existing = None if a.fresh else read_json(path, "holdings", required=False)
            doc = build_holdings(a.files, tdir, groups, today, existing)
            if a.cash is not None:
                doc["cash"] = a.cash
            write_json(path, doc)
            out = {"written": shown(path), "holdings": len(doc["holdings"]), "lots": sum(len(h.get("lots") or [])
                                                                                  for h in doc["holdings"]),
                   "realised": len(doc["realised"]), "source": doc["source"], "notes": doc["notes"]}
            emit(out, lambda o: "Wrote %s: %d holdings, %d lots, %d realised sales (%s)%s" % (
                o["written"], o["holdings"], o["lots"], o["realised"], o["source"],
                "".join("\nnote: " + n for n in o["notes"])), as_json)
            return 0
        if a.cmd in (None, "show"):
            emit(show(load_holdings(tdir), tdir, groups, today), show_text, as_json)
            return 0
        if a.cmd == "risk":
            lim = {"max_stock_pct": a.max_stock, "max_sector_pct": a.max_sector, "max_group_pct": a.max_group,
                   "max_commodity_pct": a.max_commodity}
            emit(risk(load_holdings(tdir), tdir, groups, today, lim), risk_text, as_json)
            return 0
        if a.cmd == "tax":
            emit(tax_report(load_holdings(tdir), tdir, groups, today, a.fy, a.slab), tax_text, as_json)
            return 0
        targets, meta = parse_targets(a.targets)
        orders_path = a.write or os.path.join(tdir, "orders.json")
        if a.cmd == "rebalance":
            r = rebalance(load_holdings(tdir), tdir, groups, today, targets, meta, a.cash, a.prices, a.min_trade,
                          a.tax_mode, a.limit_pct, a.slab, a.others)
            if a.write is not None and r["orders"]:
                write_orders(orders_path, r["orders"], "trader-portfolio rebalance to %s" % os.path.basename(a.targets))
                r["written"] = shown(orders_path)
            emit(r, rebalance_text, as_json)
            return 0
        hold = read_json(os.path.join(tdir, "holdings.json"), "holdings", required=False) or {"holdings": []}
        carry_path = os.path.join(tdir, "sip.json")
        carry_doc = read_json(carry_path, "sip carry", required=False)
        s = sip(hold, tdir, groups, today, targets, a.monthly, carry_doc, a.prices, a.limit_pct)
        if carry_doc and carry_doc.get("month") == s["month"]:
            s["warning"] = "a SIP for %s was already written: this adds a second one" % s["month"]
        if a.write is not None:
            if s["orders"]:
                write_orders(orders_path, s["orders"], "trader-portfolio sip %s" % s["month"])
            write_json(carry_path, {"month": s["month"], "monthly": s["monthly"], "carry": s["carry"],
                                    "targets": os.path.basename(a.targets)})
            s["written"] = shown(orders_path)
        emit(s, sip_text, as_json)
        return 0
    except PortfolioError as e:
        print("portfolio.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
