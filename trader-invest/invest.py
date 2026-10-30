#!/usr/bin/env python3
"""Long-term stock analyst for trader-invest (NSE cash equities). Standard library only.

"Should I buy IOC for the long term?" This script gathers the fundamentals,
scores them on a transparent, editable scorecard and, if you want, writes a
staggered delivery (CNC) buy plan in whole shares with the charges and the tax.

    python3 invest.py fetch IOC                        # screener.in, then NSE quote API
    python3 invest.py fetch IOC --from ioc.json        # your own numbers (screener blocked)
    python3 invest.py fetch IOC --kite-quotes q.json   # price from the Kite MCP get_quotes JSON
    python3 invest.py analyse IOC [--horizon 5y] [--gsec 6.5]
    python3 invest.py compare IOC BPCL TCS
    python3 invest.py plan IOC --amount 5000 [--tranches 3] [--every 30]
    add --json to any of them for machine output

Files (all under the project's trader/ folder, see PORT_V4_SPEC.md):

  trader/data/fundamentals/<SYM>.json   written by fetch, read by analyse/compare/plan
  trader/data/daily/<SYM>.adj.csv       daily bars, split/bonus adjusted (preferred)
  trader/data/daily/<SYM>.csv           daily bars, unadjusted (trader-data writes it)
  trader/scorecard.json                 your weights (deep-merged over scorecard.example.json)
  trader/charges.json                   charges override (deep-merged over the built-in model)

Sources for fetch, in order, each reporting its own error:
  1. screener.in company page (consolidated, then standalone), parsed with html.parser
  2. NSE quote API (cookie-primed session): pe, sector, industry, issued size, price
  3. --from FILE: your manual JSON in the fundamentals shape (overrides 1 and 2)
  4. --kite-quotes FILE: Kite MCP get_quotes output, for the price only

Missing data is never treated as good: every missing metric is scored at the
scorecard's missing_score (25 of 100 by default) and listed in the output.
Education and tooling, not financial advice.
"""

import argparse
import csv
import datetime
import http.cookiejar
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

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

HERE = os.path.dirname(os.path.abspath(__file__))
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
SCREENER_URLS = ("https://www.screener.in/company/%s/consolidated/", "https://www.screener.in/company/%s/")
NSE_HOME = "https://www.nseindia.com/"
NSE_QUOTE = "https://www.nseindia.com/api/quote-equity?symbol=%s"
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")

# charges model: PORT_V4_SPEC.md contract 4 (verified 2026-10-29 from zerodha.com/charges)
CHARGES = {"broker": "zerodha", "verified": "2026-10-29 zerodha.com/charges",
           "intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025, "stamp_buy_pct": 0.003},
           "delivery": {"brokerage_pct": 0, "brokerage_cap": 0, "stt_buy_pct": 0.1, "stt_sell_pct": 0.1,
                        "stamp_buy_pct": 0.015, "dp_per_scrip_sell": 15.34},
           "exchange_txn_pct": {"NSE": 0.00307}, "sebi_per_crore": 10, "gst_pct": 18,
           "calibration": {"factor": 1.0, "samples": 0, "updated": None}}

# tax on listed equity (Finance Act 2024, in force from 2024-07-23)
STCG_PCT = 20.0
LTCG_PCT = 12.5
LTCG_EXEMPT = 125000.0

CONTRACT_FIELDS = ("price", "market_cap_cr", "pe", "pb", "div_yield_pct", "roe_pct", "roce_pct",
                   "debt_to_equity", "promoter_pct", "promoter_pledge_pct", "sector")
CYCLICAL_WORDS = ("oil", "gas", "petroleum", "refiner", "metal", "mining", "minerals", "steel", "copper",
                  "alumin", "zinc", "coal", "iron", "consumable fuels")
FINANCIAL_WORDS = ("bank", "financ", "nbfc", "insurance", "lending")
PSU_WORDS = ("president of india", "government of india", "maharatna", "navratna", "miniratna",
             "public sector undertaking", "central public sector")
# well known listed central PSUs: a fallback when the promoter name is not in the data
PSU_SYMBOLS = {"IOC", "BPCL", "HINDPETRO", "ONGC", "OIL", "GAIL", "COALINDIA", "NTPC", "POWERGRID", "NHPC",
               "SJVN", "SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "INDIANB", "BANKINDIA", "BEL",
               "HAL", "BHEL", "BEML", "NMDC", "SAIL", "NATIONALUM", "HINDCOPPER", "PFC", "RECLTD", "IRFC",
               "IRCTC", "RVNL", "IRCON", "CONCOR", "LICI", "GICRE", "NIACL", "MAZDOCK", "COCHINSHIP",
               "GRSE", "MOIL", "NLCINDIA", "IREDA", "HUDCO", "NBCC", "ENGINERSIN", "MRPL", "CPCL"}
# market valuation rule of thumb (Nifty 50 P/E from trader/data/index/nifty-50.csv), a hint, never scored
NIFTY_PE_EXPENSIVE = 25.0
NIFTY_PE_CHEAP = 20.0
NIFTY_INDEX_FILE = "nifty-50.csv"
EXPENSIVE_EXTRA_TRANCHES = 2      # plan: when the market is expensive, suggest this many more tranches
EXPENSIVE_PACE_FACTOR = 1.5       # ... and this much more time between them
# delivery hint (trader/data/delivery/<SYM>.csv): "institutional money" days in the last 30 sessions
DELIVERY_WINDOW = 30
DELIVERY_MIN_VALUES = 5
DELIVERY_IM_RATIO = 1.2
T2T_SERIES = ("BE", "BZ")
# swing structure on daily bars: retrace as scan/backtest use it, and the bars it needs to be trusted
SWING_RETRACE = 5.0
SWING_MIN_BARS = 40
VERDICTS = ("Strong long-term candidate", "Reasonable, size small", "Value trap risk / wait",
            "Avoid for long term")


class InvestError(Exception):
    pass


def shown(path):
    """A path as printed to the user: forward slashes on every OS (files are opened as given)."""
    return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------- small helpers

def ist_today():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET).date()


def parse_day(text):
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
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
    return "-" + out if neg else out


def crore(x):
    """Rupee crore with Indian grouping, no rupee sign: 184154 -> "1,84,154 cr"."""
    if x is None:
        return "n/a"
    return inr(round(float(x))).replace("₹", "") + " cr"


def num(text):
    """"1,84,154" / "₹ 130" / "6.36 %" / "-2,661" -> float; anything else -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if not (isinstance(text, float) and math.isnan(text)) else None
    t = str(text).replace("−", "-").replace("₹", "").replace(",", "").replace("%", "")
    t = t.replace("Cr.", "").replace("Cr", "").strip()
    m = re.search(r"-?\d+(\.\d+)?", t)
    return float(m.group(0)) if m else None


def fmt(x, nd=1, suffix=""):
    if x is None:
        return "n/a"
    if isinstance(x, int) and not isinstance(x, bool):
        return "%d%s" % (x, suffix)
    return ("%." + str(nd) + "f%s") % (x, suffix)


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise InvestError("no %s at %s" % (what, shown(path)))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise InvestError("%s is not valid JSON: %s" % (shown(path), e))


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def clean_symbol(sym):
    s = str(sym or "").strip().upper()
    for suffix in (".NS", "-EQ"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    if s.startswith("NSE:"):
        s = s[4:]
    if not TICKER.match(s):
        raise InvestError("%r is not an NSE symbol" % sym)
    return s


def cagr(start, end, years):
    if start is None or end is None or years <= 0:
        return None
    if start <= 0 or end <= 0:
        return None
    return ((end / start) ** (1.0 / years) - 1) * 100


def fy_of_month(label):
    """"Mar 2026" -> ("FY26", 3, 2026)."""
    m = re.match(r"^\s*([A-Za-z]{3})\w*\s+(\d{4})\s*$", label or "")
    if not m:
        return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    mon = months.index(m.group(1).lower()) + 1 if m.group(1).lower() in months else None
    if mon is None:
        return None
    year = int(m.group(2))
    fy = year if mon <= 3 else year + 1
    return "FY%02d" % (fy % 100), mon, year


def quarter_label(label):
    """"Jun 2026" -> "Q1FY27" (Apr-Jun Q1, Jul-Sep Q2, Oct-Dec Q3, Jan-Mar Q4)."""
    got = fy_of_month(label)
    if not got:
        return None
    fy, mon, _ = got
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(mon)
    if q is None:
        q = (((mon - 4) % 12) // 3) + 1
    return "Q%d%s" % (q, fy)


def quarter_end(qlabel):
    """"Q1FY27" -> date(2026, 6, 30)."""
    m = re.match(r"^Q([1-4])FY(\d{2})$", qlabel or "")
    if not m:
        return None
    q, fy = int(m.group(1)), 2000 + int(m.group(2))
    return {1: datetime.date(fy - 1, 6, 30), 2: datetime.date(fy - 1, 9, 30),
            3: datetime.date(fy - 1, 12, 31), 4: datetime.date(fy, 3, 31)}[q]


def next_quarter(qlabel):
    m = re.match(r"^Q([1-4])FY(\d{2})$", qlabel or "")
    if not m:
        return None
    q, fy = int(m.group(1)), int(m.group(2))
    return "Q1FY%02d" % ((fy + 1) % 100) if q == 4 else "Q%dFY%02d" % (q + 1, fy)


def results_due(qlabel):
    """SEBI LODR: results within 45 days of a quarter end, 60 days for the year end (Q4)."""
    end = quarter_end(qlabel)
    if not end:
        return None
    return end + datetime.timedelta(days=60 if qlabel.startswith("Q4") else 45)


# ---------------------------------------------------------------- screener.in parser

class ScreenerParser(HTMLParser):
    """Collects the top ratios list, every table by section id, links titled
    Sector/Industry, the about text and the whole page text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = None
        self.section_stack = []
        self.in_top = False
        self.top_depth = 0
        self.li = None
        self.li_name = False
        self.ratios = {}
        self.tables = {}
        self.table = None
        self.row = None
        self.cell = None
        self.link_title = None
        self.link_text = []
        self.links = {}
        self.text = []
        self.about = []
        self.about_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section":
            self.section_stack.append(a.get("id"))
            self.section = a.get("id") or self.section
        if tag == "ul" and a.get("id") == "top-ratios":
            self.in_top = True
            self.top_depth = 0
        if self.in_top and tag == "ul":
            self.top_depth += 1
        if self.in_top and tag == "li":
            self.li = {"name": [], "value": []}
        if self.li is not None and tag == "span" and "name" in (a.get("class") or "").split():
            self.li_name = True
        if tag == "table":
            self.table = []
        if self.table is not None and tag == "tr":
            self.row = []
        if self.row is not None and tag in ("td", "th"):
            self.cell = []
        if tag == "a" and a.get("title") in ("Sector", "Industry", "Broad Sector", "Broad Industry"):
            self.link_title = a.get("title")
            self.link_text = []
        cls = (a.get("class") or "").split()
        if tag == "div" and ("about" in cls or "company-profile" in cls):
            self.about_depth = 1
        elif self.about_depth and tag == "div":
            self.about_depth += 1

    def handle_endtag(self, tag):
        if tag == "section" and self.section_stack:
            self.section_stack.pop()
            ids = [s for s in self.section_stack if s]
            self.section = ids[-1] if ids else None
        if self.li is not None and tag == "span" and self.li_name:
            self.li_name = False
        if self.in_top and tag == "li" and self.li is not None:
            name = " ".join("".join(self.li["name"]).split())
            value = " ".join("".join(self.li["value"]).split())
            if name:
                self.ratios[name] = value
            self.li = None
        if self.in_top and tag == "ul":
            self.top_depth -= 1
            if self.top_depth <= 0:
                self.in_top = False
        if self.cell is not None and tag in ("td", "th"):
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if self.row is not None and tag == "tr":
            self.table.append(self.row)
            self.row = None
        if self.table is not None and tag == "table":
            self.tables.setdefault(self.section or "_", []).append(self.table)
            self.table = None
        if tag == "a" and self.link_title:
            self.links[self.link_title] = " ".join("".join(self.link_text).split())
            self.link_title = None
        if self.about_depth and tag == "div":
            self.about_depth -= 1

    def handle_data(self, data):
        self.text.append(data)
        if self.li is not None:
            (self.li["name"] if self.li_name else self.li["value"]).append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.link_title:
            self.link_text.append(data)
        if self.about_depth:
            self.about.append(data)


def _row(table, *names):
    """The first row whose label starts with one of names (screener adds a "+" button)."""
    for r in table or []:
        if not r:
            continue
        label = r[0].replace("+", "").replace("\xa0", " ").strip().lower()
        for n in names:
            if label == n.lower() or label.startswith(n.lower()):
                return r
    return None


def _first_table(p, section):
    tabs = p.tables.get(section) or []
    return tabs[0] if tabs else None


def parse_screener(html_text, symbol=None):
    """screener.in company page HTML -> partial fundamentals dict. Raises if nothing usable."""
    p = ScreenerParser()
    p.feed(html_text)
    r = p.ratios
    if not r and not p.tables:
        raise InvestError("screener.in page has no #top-ratios list and no tables (layout changed or blocked)")
    out = {}

    def ratio(*names):
        for n in names:
            for k, v in r.items():
                if k.lower() == n.lower():
                    return v
        return None

    out["market_cap_cr"] = num(ratio("Market Cap"))
    out["price"] = num(ratio("Current Price"))
    out["pe"] = num(ratio("Stock P/E"))
    bv = num(ratio("Book Value"))
    out["book_value"] = bv
    out["div_yield_pct"] = num(ratio("Dividend Yield"))
    out["roce_pct"] = num(ratio("ROCE"))
    out["roe_pct"] = num(ratio("ROE"))
    hl = ratio("High / Low")
    if hl:
        parts = [num(x) for x in hl.split("/")]
        if len(parts) == 2:
            out["high_52w"], out["low_52w"] = parts
    de = ratio("Debt to equity")
    if de is not None:
        out["debt_to_equity"] = num(de)
    pledge = ratio("Pledged percentage", "Pledged")
    if pledge is not None:
        out["promoter_pledge_pct"] = num(pledge)
    if out.get("price") and bv:
        out["pb"] = round(out["price"] / bv, 2)

    # profit and loss: annual
    pl = _first_table(p, "profit-loss")
    annual, ttm = [], None
    if pl:
        head = pl[0]
        sales = _row(pl, "Sales", "Revenue")
        profit = _row(pl, "Net Profit")
        eps = _row(pl, "EPS in Rs", "EPS")
        payout = _row(pl, "Dividend Payout")
        for i, label in enumerate(head):
            if i == 0:
                continue
            cell = lambda row: num(row[i]) if row and i < len(row) else None
            if label.strip().upper() == "TTM":
                ttm = {"revenue_cr": cell(sales), "net_profit_cr": cell(profit), "eps": cell(eps)}
                continue
            got = fy_of_month(label)
            if not got:
                continue
            e, po = cell(eps), cell(payout)
            dps = round(e * po / 100.0, 2) if (e is not None and po is not None and e > 0) else (
                0.0 if po == 0 else None)
            annual.append({"fy": got[0], "revenue_cr": cell(sales), "net_profit_cr": cell(profit),
                           "eps": e, "dividend_ps": dps, "payout_pct": po})
    out["annual"] = annual
    if ttm:
        out["ttm"] = ttm

    qt = _first_table(p, "quarters")
    quarterly = []
    if qt:
        head = qt[0]
        sales = _row(qt, "Sales", "Revenue")
        profit = _row(qt, "Net Profit")
        for i, label in enumerate(head):
            if i == 0:
                continue
            q = quarter_label(label)
            if not q:
                continue
            quarterly.append({"q": q, "revenue_cr": num(sales[i]) if sales and i < len(sales) else None,
                              "net_profit_cr": num(profit[i]) if profit and i < len(profit) else None})
    out["quarterly"] = quarterly

    bs = _first_table(p, "balance-sheet")
    if bs and out.get("debt_to_equity") is None:
        eq, res, bor = _row(bs, "Equity Capital"), _row(bs, "Reserves"), _row(bs, "Borrowings")
        if eq and res and bor:
            e, rv, b = num(eq[-1]), num(res[-1]), num(bor[-1])
            if None not in (e, rv, b) and (e + rv) > 0:
                out["debt_to_equity"] = round(b / (e + rv), 2)

    sh = _first_table(p, "shareholding")
    if sh:
        prom = _row(sh, "Promoters")
        if prom:
            out["promoter_pct"] = num(prom[-1])
            vals = [num(x) for x in prom[1:]]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                out["promoter_change_pp"] = round(vals[-1] - vals[0], 2)
        gov = _row(sh, "Government")
        if gov:
            out["govt_pct"] = num(gov[-1])
    if "Sector" in p.links:
        out["sector"] = p.links["Sector"]
    if "Industry" in p.links:
        out["industry"] = p.links["Industry"]
    about = " ".join("".join(p.about).split())
    if about:
        out["about"] = about[:600]
    low = " ".join("".join(p.text).split()).lower()
    if "president of india" in low:
        out["promoter_name"] = "President of India"
    if out.get("promoter_pledge_pct") is None:
        m = re.search(r"pledged\D{0,40}?(\d+(\.\d+)?)\s*%", low)
        if m:
            out["promoter_pledge_pct"] = float(m.group(1))
    out["basis"] = "consolidated" if "consolidated figures" in low or "/consolidated/" in low else "standalone"
    useful = [k for k in ("price", "pe", "roe_pct") if out.get(k) is not None] + (["annual"] if annual else [])
    if not useful:
        raise InvestError("screener.in page parsed but no ratios or P&L rows were found (layout changed?)")
    return out


# ---------------------------------------------------------------- NSE quote API and Kite

def parse_nse_quote(data):
    if not isinstance(data, dict) or not (data.get("info") or data.get("priceInfo")):
        raise InvestError("NSE quote JSON has no info/priceInfo (blocked or changed)")
    out = {}
    info = data.get("info") or {}
    ind = data.get("industryInfo") or {}
    meta = data.get("metadata") or {}
    sec = data.get("securityInfo") or {}
    price = data.get("priceInfo") or {}
    out["company"] = info.get("companyName")
    out["sector"] = ind.get("sector") or meta.get("industry") or info.get("industry")
    out["industry"] = ind.get("basicIndustry") or ind.get("industry") or info.get("industry")
    out["pe"] = num(meta.get("pdSymbolPe"))
    out["sector_pe"] = num(meta.get("pdSectorPe"))
    issued = num(sec.get("issuedSize"))
    out["issued_shares"] = issued
    out["price"] = num(price.get("lastPrice"))
    whl = price.get("weekHighLow") or {}
    out["high_52w"] = num(whl.get("max"))
    out["low_52w"] = num(whl.get("min"))
    if issued and out["price"]:
        out["market_cap_cr"] = round(issued * out["price"] / 1e7, 0)
    return {k: v for k, v in out.items() if v is not None}


def parse_kite_quotes(data, symbol):
    """Kite MCP get_quotes / get_ltp output -> {"price", "price_time"}."""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise InvestError("Kite quotes JSON is not an object")
    q = data.get("NSE:" + symbol) or data.get(symbol)
    if not isinstance(q, dict):
        raise InvestError("Kite quotes JSON has no NSE:%s entry (keys: %s)" % (symbol, ", ".join(list(data)[:5])))
    price = num(q.get("last_price"))
    if not price:
        raise InvestError("Kite quote for %s has no last_price" % symbol)
    out = {"price": price, "price_time": q.get("timestamp") or q.get("last_trade_time")}
    return out


def http_get(url, opener, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
                                                  **(headers or {})))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_screener(symbol):
    opener = urllib.request.build_opener()
    errors = []
    for tmpl in SCREENER_URLS:
        url = tmpl % symbol
        try:
            page = http_get(url, opener, {"Accept": "text/html"})
            got = parse_screener(page, symbol)
            got["source_url"] = url
            got["basis"] = "consolidated" if "/consolidated/" in url else "standalone"
            return got, None
        except urllib.error.HTTPError as e:
            errors.append("%s: HTTP %s" % (url, e.code))
        except (urllib.error.URLError, OSError) as e:
            errors.append("%s: %s" % (url, getattr(e, "reason", e)))
        except InvestError as e:
            errors.append("%s: %s" % (url, e))
    return None, "; ".join(errors)


def fetch_nse(symbol):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        http_get(NSE_HOME, opener, {"Accept": "text/html"})
        body = http_get(NSE_QUOTE % urllib.request.quote(symbol), opener,
                        {"Accept": "application/json", "Referer": NSE_HOME + "get-quotes/equity?symbol=" + symbol})
        return parse_nse_quote(json.loads(body)), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except (urllib.error.URLError, OSError) as e:
        return None, str(getattr(e, "reason", e))
    except (ValueError, InvestError) as e:
        return None, str(e)


def build_fundamentals(symbol, screener=None, nse=None, manual=None, kite=None, today=None, sources=None):
    """Merge the sources into the contract-5 shape. screener first, NSE fills gaps,
    manual overrides, Kite sets the price (and rescales price-based ratios)."""
    f = {"symbol": symbol, "as_of": (today or ist_today()).isoformat(), "source": None}
    for k in CONTRACT_FIELDS:
        f[k] = None
    f["annual"], f["quarterly"] = [], []
    used = []
    if screener:
        f.update({k: v for k, v in screener.items() if v is not None})
        used.append("screener.in (%s)" % screener.get("basis", "?"))
    if nse:
        for k, v in nse.items():
            if f.get(k) is None:
                f[k] = v
        for k in ("price", "high_52w", "low_52w"):
            if nse.get(k) is not None:        # screener rounds the price to whole rupees
                f[k] = nse[k]
        if nse.get("sector"):
            f["sector"] = f.get("sector") or nse["sector"]
            f["nse_sector"] = nse["sector"]
        if nse.get("industry"):
            f["nse_industry"] = nse["industry"]
        used.append("nse quote api")
    if manual:
        for k, v in manual.items():
            if k == "symbol" or k.startswith("_"):
                continue
            if v is not None:
                f[k] = v
        used.append("manual json")
    if kite:
        old = f.get("price")
        new = kite["price"]
        if old and new and old > 0:
            ratio = new / old
            for k in ("pe", "pb", "market_cap_cr"):
                if f.get(k) is not None:
                    f[k] = round(f[k] * ratio, 2)
            if f.get("div_yield_pct") is not None:
                f["div_yield_pct"] = round(f["div_yield_pct"] / ratio, 2)
        f["price"] = new
        f["price_source"] = "kite get_quotes"
        if kite.get("price_time"):
            f["price_time"] = str(kite["price_time"])
        used.append("kite get_quotes (price)")
    if f.get("pb") is None and f.get("price") and f.get("book_value"):
        f["pb"] = round(f["price"] / f["book_value"], 2)
    f["source"] = " + ".join(used) if used else None
    f["sources"] = sources or []
    if manual and manual.get("as_of"):
        f["as_of"] = manual["as_of"]
    f["symbol"] = symbol
    return f


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


# ---------------------------------------------------------------- daily bars

def read_daily(trader_dir, symbol):
    """(rows, path, adjusted). rows: list of (date, close) ascending."""
    base = os.path.join(trader_dir, "data", "daily")
    for name, adj in ((symbol + ".adj.csv", True), (symbol + ".csv", False)):
        path = os.path.join(base, name)
        if os.path.exists(path):
            rows = []
            with open(path, encoding="utf-8") as fh:
                header = fh.readline().strip().split(",")
                if header[:1] != ["date"] or "close" not in header:
                    raise InvestError("%s: header must be date,open,high,low,close,volume" % shown(path))
                ci = header.index("close")
                for line in fh:
                    parts = line.strip().split(",")
                    if len(parts) <= ci:
                        continue
                    d, c = parse_day(parts[0]), num(parts[ci])
                    if d and c:
                        rows.append((d, c))
            rows.sort()
            return rows, path, adj
    return [], None, None


def read_daily_bars(trader_dir, symbol):
    """Sibling of read_daily for the swing engine: (rows, path, adjusted), rows are (date, high, low, close)
    ascending. A missing or unreadable high/low stays None and the engine skips that bar."""
    base = os.path.join(trader_dir, "data", "daily")
    for name, adj in ((symbol + ".adj.csv", True), (symbol + ".csv", False)):
        path = os.path.join(base, name)
        if os.path.exists(path):
            rows = []
            with open(path, encoding="utf-8") as fh:
                header = fh.readline().strip().split(",")
                if header[:1] != ["date"] or "close" not in header:
                    raise InvestError("%s: header must be date,open,high,low,close,volume" % shown(path))
                ci = header.index("close")
                hi = header.index("high") if "high" in header else None
                li = header.index("low") if "low" in header else None
                for line in fh:
                    parts = line.strip().split(",")
                    if len(parts) <= ci:
                        continue
                    d, c = parse_day(parts[0]), num(parts[ci])
                    if d and c:
                        h = num(parts[hi]) if hi is not None and hi < len(parts) else None
                        lo = num(parts[li]) if li is not None and li < len(parts) else None
                        rows.append((d, h, lo, c))
            rows.sort(key=lambda r: r[0])
            return rows, path, adj
    return [], None, None


def swing_numbers(bars, retrace=SWING_RETRACE, min_bars=SWING_MIN_BARS):
    """bars: (date, high, low, close). Runs the swing engine and returns the trend, the change-of-character
    level and the cushion: (last close - coc) / last close x 100, negative when the close is on the wrong side
    of it (a downtrend). Under min_bars bars the numbers are not trusted: cushion is None (scored as missing)."""
    usable = [b for b in bars if None not in (b[1], b[2], b[3])]
    if not usable:
        return None
    snap, _ = swing_run([(b[1], b[2], b[3]) for b in usable], retrace, 20, min_bars)
    last = usable[-1][3]
    cushion = None
    if snap["stable"] and snap["trend"] is not None and snap["coc"] is not None and last > 0:
        cushion = round((last - snap["coc"]) / last * 100, 2)
    return {"swing_trend": snap["trend"], "swing_coc": snap["coc"], "swing_pivot": snap["pivot"],
            "swing_cushion_pct": cushion, "swing_stable": snap["stable"], "swing_bars": len(usable)}


def read_market_valuation(trader_dir):
    """Latest Nifty 50 P/E row of trader/data/index/nifty-50.csv (written by trader-data, may not exist) with
    the rule-of-thumb label. None when there is no file or no usable P/E. A hint, never scored."""
    path = os.path.join(trader_dir, "data", "index", NIFTY_INDEX_FILE)
    if not os.path.exists(path):
        return None
    best = None
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                d, pe = parse_day(row.get("date")), num(row.get("pe"))
                if d and pe and pe > 0 and (best is None or d >= best[0]):
                    best = (d, pe, num(row.get("pb")), num(row.get("div_yield")))
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    if not best:
        return None
    d, pe, pb, dy = best
    label = "expensive" if pe >= NIFTY_PE_EXPENSIVE else "cheap" if pe <= NIFTY_PE_CHEAP else "neutral"
    return {"index": "Nifty 50", "date": d.isoformat(), "pe": pe, "pb": pb, "div_yield_pct": dy, "label": label,
            "expensive_at": NIFTY_PE_EXPENSIVE, "cheap_at": NIFTY_PE_CHEAP}


def read_next_results(trader_dir, symbol, today):
    """The next results board meeting from trader/data/events.json (trader-news keeps it from NSE's
    board-meeting list) -> {"date", "purpose", "source"}, or None. Better than the SEBI deadline guess."""
    path = os.path.join(trader_dir, "data", "events.json")
    try:
        with open(path, encoding="utf-8") as fh:
            events = json.load(fh).get("events") or []
    except (OSError, ValueError, AttributeError):
        return None
    for e in sorted((e for e in events if isinstance(e, dict)), key=lambda e: str(e.get("date"))):
        d = parse_day(e.get("date"))
        if e.get("ticker") == symbol and e.get("results") and d and d >= today:
            return {"date": d.isoformat(), "purpose": e.get("purpose"), "source": shown(path)}
    return None


def read_delivery(trader_dir, symbol):
    """Rows of trader/data/delivery/<SYM>.csv ascending, as dicts (date, deliv_qty, qty_per_trade, series); an
    empty cell is None. None when there is no file."""
    path = os.path.join(trader_dir, "data", "delivery", symbol + ".csv")
    if not os.path.exists(path):
        return None
    rows = []
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                d = parse_day(row.get("date"))
                if d:
                    rows.append({"date": d, "deliv_qty": num(row.get("deliv_qty")),
                                 "qty_per_trade": num(row.get("qty_per_trade")),
                                 "series": (row.get("series") or "").strip().upper()})
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    rows.sort(key=lambda r: r["date"])
    return rows


def delivery_im_days(rows):
    """Institutional-money days among the last 30 sessions that have delivery data. A day counts when its
    delivery quantity (DQ) and its quantity per trade (TQ) are both above 1.2 times their 30-session mean and
    the series is not trade-to-trade (BE, BZ). Fewer than 5 values: not enough data."""
    if rows is None:
        return None
    win = [r for r in rows if r["deliv_qty"] is not None][-DELIVERY_WINDOW:]
    dq = [r["deliv_qty"] for r in win]
    tq = [r["qty_per_trade"] for r in win if r["qty_per_trade"] is not None]
    if len(dq) < DELIVERY_MIN_VALUES or len(tq) < DELIVERY_MIN_VALUES:
        return {"enough_data": False, "sessions": len(win), "im_days": None, "last_im_date": None,
                "text": "not enough delivery data (%d sessions with values, need %d)" % (
                    min(len(dq), len(tq)), DELIVERY_MIN_VALUES)}
    mdq, mtq = sum(dq) / len(dq), sum(tq) / len(tq)
    hits = [r["date"].isoformat() for r in win
            if r["series"] not in T2T_SERIES and r["qty_per_trade"] is not None and mdq > 0 and mtq > 0
            and r["deliv_qty"] / mdq > DELIVERY_IM_RATIO and r["qty_per_trade"] / mtq > DELIVERY_IM_RATIO]
    return {"enough_data": True, "sessions": len(win), "im_days": len(hits), "dates": hits,
            "last_im_date": hits[-1] if hits else None,
            "text": "%d institutional-money day(s) in the last %d sessions%s" % (
                len(hits), len(win), " (latest %s)" % hits[-1] if hits else "")}


def close_on_or_before(rows, day):
    lo, hi, best = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= day:
            best = rows[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def technicals(rows, horizon_years=5):
    if not rows:
        return None
    last_d, last = rows[-1]
    out = {"last_date": last_d.isoformat(), "last_close": last, "bars": len(rows)}
    year_ago = last_d - datetime.timedelta(days=365)
    yr = [c for d, c in rows if d > year_ago]
    out["high_52w"] = max(yr)
    out["low_52w"] = min(yr)
    out["off_52w_high_pct"] = round((out["high_52w"] - last) / out["high_52w"] * 100, 2)
    if len(rows) >= 200:
        dma = sum(c for _, c in rows[-200:]) / 200.0
        out["dma200"] = round(dma, 2)
        out["dist_200dma_pct"] = round((last / dma - 1) * 100, 2)
    else:
        out["dma200"] = out["dist_200dma_pct"] = None
    first_d = rows[0][0]
    for n in (1, 3, 5):
        then = last_d.replace(year=last_d.year - n) if not (last_d.month == 2 and last_d.day == 29) \
            else last_d.replace(year=last_d.year - n, day=28)
        if first_d > then + datetime.timedelta(days=7):
            out["price_cagr_%dy" % n] = None
            continue
        ref = close_on_or_before(rows, then) or rows[0]
        out["price_cagr_%dy" % n] = round(cagr(ref[1], last, n), 2) if cagr(ref[1], last, n) is not None else None
    start = last_d.replace(year=last_d.year - horizon_years) if horizon_years else first_d
    window = [c for d, c in rows if d >= start] or [c for _, c in rows]
    peak, mdd = window[0], 0.0
    for c in window:
        peak = max(peak, c)
        mdd = max(mdd, (peak - c) / peak * 100)
    out["max_drawdown_pct"] = round(mdd, 2)
    rets = [math.log(rows[i][1] / rows[i - 1][1]) for i in range(max(1, len(rows) - 252), len(rows))
            if rows[i - 1][1] > 0 and rows[i][1] > 0]
    if len(rets) > 20:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        out["volatility_pct"] = round(math.sqrt(var) * math.sqrt(252) * 100, 2)
    else:
        out["volatility_pct"] = None
    return out


# ---------------------------------------------------------------- scorecard

def load_scorecard(trader_dir, path=None):
    card = read_json(os.path.join(HERE, "scorecard.example.json"), "scorecard example")
    user = path or os.path.join(trader_dir, "scorecard.json")
    over = read_json(user, "scorecard", required=False)
    if over:
        card = deep_merge(card, over)
        card["_override"] = shown(user)
    return card


def load_charges(trader_dir):
    over = read_json(os.path.join(trader_dir, "charges.json"), "charges", required=False)
    return deep_merge(CHARGES, over) if over else json.loads(json.dumps(CHARGES))


def score_metric(value, spec):
    """Linear 0..100 between bad and good (either direction), clamped."""
    good, bad = float(spec["good"]), float(spec["bad"])
    if good == bad:
        return 100.0 if value >= good else 0.0
    s = (value - bad) / (good - bad) * 100.0
    return max(0.0, min(100.0, s))


def is_cyclical(f, profits):
    sector = " ".join(str(f.get(k) or "") for k in ("sector", "industry", "nse_sector", "nse_industry")).lower()
    why = []
    hit = [w for w in CYCLICAL_WORDS if w in sector]
    if hit:
        why.append("sector '%s' is commodity-linked" % (f.get("sector") or f.get("industry")))
    vals = [p for p in profits if p is not None]
    cv = None
    if len(vals) >= 3:
        mu = sum(vals) / len(vals)
        if mu > 0:
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
            cv = sd / mu
            if cv > 0.45:
                why.append("annual profit swings a lot (volatility %.0f%% of the average)" % (cv * 100))
        elif mu <= 0:
            why.append("average profit over the period is not positive")
    return bool(why), why, cv


def is_psu(f, symbol):
    text = " ".join(str(f.get(k) or "") for k in ("promoter_name", "about")).lower()
    if any(w in text for w in PSU_WORDS):
        return True, "promoter is the Government of India (%s)" % (f.get("promoter_name") or "PSU per company profile")
    if (f.get("govt_pct") or 0) > 50:
        return True, "government holds %.1f%%" % f["govt_pct"]
    if symbol in PSU_SYMBOLS and (f.get("promoter_pct") or 0) > 50:
        return True, "listed central PSU (built-in list), promoter holds %.1f%%" % f["promoter_pct"]
    if symbol in PSU_SYMBOLS:
        return True, "listed central PSU (built-in list)"
    return False, None


def growth_numbers(annual):
    rows = [a for a in annual if a.get("fy")]
    rows.sort(key=lambda a: a["fy"])
    out = {}
    last = rows[-1] if rows else None
    for n in (3, 5):
        if len(rows) > n:
            a, b = rows[-1 - n], rows[-1]
            out["revenue_cagr_%dy" % n] = cagr(a.get("revenue_cr"), b.get("revenue_cr"), n)
            out["profit_cagr_%dy" % n] = cagr(a.get("net_profit_cr"), b.get("net_profit_cr"), n)
        else:
            out["revenue_cagr_%dy" % n] = out["profit_cagr_%dy" % n] = None
    window = rows[-10:]
    profits = [a.get("net_profit_cr") for a in window if a.get("net_profit_cr") is not None]
    out["loss_years"] = sum(1 for p in profits if p < 0) if profits else None
    out["years_of_data"] = len(rows)
    out["latest_fy"] = last["fy"] if last else None
    return out


def analyse(f, card, daily=None, gsec=None, today=None, horizon_years=5, daily_meta=None, swing=None,
            market=None, delivery=None, next_results=None):
    symbol = f.get("symbol")
    today = today or ist_today()
    gsec = card.get("gsec_10y_pct", 6.5) if gsec is None else gsec
    annual = sorted([a for a in (f.get("annual") or []) if a.get("fy")], key=lambda a: a["fy"])
    quarterly = list(f.get("quarterly") or [])
    g = growth_numbers(annual)
    recent_profits = [a.get("net_profit_cr") for a in annual[-5:]]
    cyc, cyc_why, cv = is_cyclical(f, recent_profits)
    psu, psu_why = is_psu(f, symbol)
    sector_l = " ".join(str(f.get(k) or "") for k in ("sector", "industry", "nse_sector")).lower()
    financial = any(w in sector_l for w in FINANCIAL_WORDS)
    tech = technicals(daily, horizon_years) if daily else None
    if tech and swing:
        tech.update(swing)

    m = {}
    m["pe"] = f.get("pe")
    m["pb"] = f.get("pb")
    m["div_yield_pct"] = f.get("div_yield_pct")
    mid = [p for p in recent_profits if p is not None]
    midcycle_pe = None
    if cyc and len(mid) >= 3 and f.get("market_cap_cr") and sum(mid) > 0:
        midcycle_pe = round(f["market_cap_cr"] / (sum(mid) / len(mid)), 2)
    m["midcycle_pe"] = midcycle_pe
    pe_for_value = midcycle_pe if (cyc and midcycle_pe) else m["pe"]
    m["earnings_yield_gap"] = round(100.0 / pe_for_value - gsec, 2) if pe_for_value and pe_for_value > 0 else None
    m["roe_pct"] = f.get("roe_pct")
    m["roce_pct"] = f.get("roce_pct")
    m["debt_to_equity"] = f.get("debt_to_equity")
    m["promoter_pct"] = f.get("promoter_pct")
    m["promoter_pledge_pct"] = f.get("promoter_pledge_pct")
    for k in ("revenue_cagr_3y", "revenue_cagr_5y", "profit_cagr_3y", "profit_cagr_5y", "loss_years"):
        m[k] = g.get(k)
    m["profit_volatility"] = round(cv * 100, 1) if cv is not None else None
    lq = quarterly[-1] if quarterly else None
    yoy = None
    if lq and len(quarterly) >= 5:
        prev = quarterly[-5]
        a, b = prev.get("net_profit_cr"), lq.get("net_profit_cr")
        if a is not None and b is not None and a > 0:
            yoy = round((b - a) / a * 100, 1)
    m["latest_quarter_profit_yoy"] = yoy
    for k in ("dist_200dma_pct", "swing_cushion_pct", "off_52w_high_pct", "price_cagr_5y", "max_drawdown_pct",
              "volatility_pct"):
        m[k] = tech.get(k) if tech else None

    not_applicable = set()
    if financial:
        not_applicable.update({"debt_to_equity", "roce_pct"})
    if not cyc:
        not_applicable.add("midcycle_pe")
    if f.get("promoter_pct") == 0 and f.get("promoter_pledge_pct") is None:
        not_applicable.add("promoter_pledge_pct")

    missing_score = float(card.get("missing_score", 25))
    pillars, missing, reasons_good, reasons_bad = {}, [], [], []
    total_w = total = 0.0
    covered = counted = 0
    for pname, pspec in card["pillars"].items():
        ws = sw = 0.0
        lines = []
        for mname, mspec in pspec["metrics"].items():
            if mname in not_applicable:
                continue
            key = mspec.get("field", mname)
            if key == "pe" and cyc and midcycle_pe:
                key = "midcycle_pe"
            val = m.get(key)
            w = float(mspec.get("weight", 1))
            counted += 1
            if val is None:
                s = missing_score
                missing.append("%s.%s" % (pname, mname))
                lines.append({"metric": mname, "value": None, "score": s, "missing": True})
            else:
                covered += 1
                s = score_metric(float(val), mspec)
                lines.append({"metric": mname, "field": key, "value": val, "score": round(s, 1)})
                label = mspec.get("label", mname)
                if s >= 75:
                    reasons_good.append("%s %s" % (label, fmt(val, 2 if abs(val) < 10 else 1)))
                elif s <= 25:
                    reasons_bad.append("%s %s" % (label, fmt(val, 2 if abs(val) < 10 else 1)))
            ws += s * w
            sw += w
        pscore = round(ws / sw, 1) if sw else None
        pillars[pname] = {"score": pscore, "weight": pspec["weight"], "metrics": lines}
        if pscore is not None:
            total += pscore * float(pspec["weight"])
            total_w += float(pspec["weight"])
    overall = round(total / total_w, 1) if total_w else 0.0
    coverage = round(covered * 100.0 / counted, 0) if counted else 0.0

    verdict = VERDICTS[-1]
    for band in card["bands"]:
        if overall >= band["min"]:
            verdict = band["verdict"]
            break
    caps = []
    cp = card.get("caps", {})

    def cap(level, why):
        nonlocal verdict
        if VERDICTS.index(verdict) < VERDICTS.index(level):
            caps.append("capped at '%s': %s" % (level, why))
            verdict = level
        else:
            caps.append("%s (verdict already at or below '%s')" % (why, level))

    if m["promoter_pledge_pct"] is not None and m["promoter_pledge_pct"] > cp.get("pledge_pct", 20):
        cap("Value trap risk / wait", "promoters have pledged %.1f%% of their shares" % m["promoter_pledge_pct"])
    if g.get("loss_years") is not None and g["loss_years"] >= cp.get("loss_years", 2):
        cap("Value trap risk / wait", "%d loss years in the last %d" % (g["loss_years"], min(10, len(annual))))
    if coverage < cp.get("min_coverage_pct", 60):
        cap("Value trap risk / wait", "only %.0f%% of the scorecard has data" % coverage)
    if lq and lq.get("net_profit_cr") is not None and lq["net_profit_cr"] < 0:
        cap("Reasonable, size small", "the latest quarter (%s) was a loss of %s" % (lq["q"], crore(abs(lq["net_profit_cr"]))))
    if cyc and cp.get("cyclical_max", True):
        cap("Reasonable, size small", "cyclical business: buy in small steps, never all at once")

    flags = []
    if cyc:
        flags.append("cyclical: judge on mid-cycle earnings, not one good or bad year (%s)" % "; ".join(cyc_why))
    if psu:
        flags.append("PSU: %s. Policy risk: government pricing, subsidy sharing, dividends and "
                     "disinvestment are set by the owner, not only by minority shareholders" % psu_why)
    if financial:
        flags.append("financial company: debt/equity and ROCE are not meaningful and are left out")
    if g.get("profit_cagr_3y") is not None and cyc and g["profit_cagr_3y"] > 30:
        flags.append("3y profit CAGR %.0f%% starts from a weak year: do not extrapolate it" % g["profit_cagr_3y"])

    ages = []
    as_of = parse_day(f.get("as_of"))
    if as_of:
        age = (today - as_of).days
        ages.append("fundamentals fetched %s (%d days ago)%s" % (as_of.isoformat(), age,
                                                                 ": refresh with fetch" if age > 7 else ""))
    else:
        ages.append("fundamentals have no as_of date")
    if g.get("latest_fy"):
        ages.append("latest annual %s" % g["latest_fy"])
    if lq and next_results:
        ages.append("latest quarter %s (next results board meeting %s, from NSE via trader-news)" % (
            lq["q"], next_results["date"]))
    elif lq:
        due = results_due(next_quarter(lq["q"]))
        ages.append("latest quarter %s (next results due by %s per SEBI LODR)" % (
            lq["q"], due.isoformat() if due else "?"))
    if tech:
        dage = (today - parse_day(tech["last_date"])).days
        ages.append("daily bars to %s (%d days old)%s" % (tech["last_date"], dage,
                                                           "" if (daily_meta or {}).get("adjusted", True)
                                                           else ", UNADJUSTED: splits/bonuses distort CAGR"))
        if swing and not swing.get("swing_stable"):
            ages.append("swing trend needs more than %d daily bars (%d here): cushion scored as missing" % (
                SWING_MIN_BARS, swing.get("swing_bars", 0)))
    else:
        ages.append("no daily bars: run trader-data for %s (technicals pillar scored as missing)" % symbol)
    missing_fields = [k for k in CONTRACT_FIELDS if f.get(k) is None and not (
        k in ("debt_to_equity", "roce_pct") and financial)]

    return {"symbol": symbol, "as_of": f.get("as_of"), "source": f.get("source"), "price": f.get("price"),
            "market_cap_cr": f.get("market_cap_cr"), "sector": f.get("sector"),
            "score": overall, "verdict": verdict, "coverage_pct": coverage, "pillars": pillars,
            "metrics": m, "growth": g, "technicals": tech, "cyclical": cyc, "psu": psu,
            "gsec_10y_pct": gsec, "strengths": reasons_good, "concerns": reasons_bad, "caps": caps,
            "flags": flags, "data_age": ages, "missing_metrics": missing, "missing_fields": missing_fields,
            "scorecard": card.get("_override") or "scorecard.example.json",
            "latest_quarter": lq, "horizon_years": horizon_years,
            "market_valuation": market, "delivery": delivery, "next_results": next_results,
            "disclaimer": "Education and tooling, not financial advice."}


# ---------------------------------------------------------------- charges and plan

def delivery_charges(side, value, ch):
    d = ch["delivery"]
    brokerage = value * float(d.get("brokerage_pct", 0)) / 100.0
    if float(d.get("brokerage_cap", 0)) > 0:
        brokerage = min(brokerage, float(d["brokerage_cap"]))
    stt = value * float(d["stt_buy_pct" if side == "buy" else "stt_sell_pct"]) / 100.0
    exch = value * float(ch["exchange_txn_pct"]["NSE"]) / 100.0
    sebi = value * float(ch["sebi_per_crore"]) / 1e7
    gst = float(ch["gst_pct"]) / 100.0 * (brokerage + exch + sebi)
    stamp = value * float(d["stamp_buy_pct"]) / 100.0 if side == "buy" else 0.0
    dp = float(d.get("dp_per_scrip_sell", 0)) if side == "sell" else 0.0
    factor = float((ch.get("calibration") or {}).get("factor") or 1.0)
    parts = {"brokerage": brokerage, "stt": stt, "exchange": exch, "sebi": sebi, "gst": gst, "stamp": stamp,
             "dp": dp}
    total = sum(parts.values()) * factor
    out = {k: round(v, 2) for k, v in parts.items()}
    out["total"] = round(total, 2)
    return out


def charges_label(ch):
    n = int((ch.get("calibration") or {}).get("samples") or 0)
    return "estimate (calibrated from %d contract notes)" % n if n > 0 else "estimate"


def plan(f, analysis, ch, amount, tranches=3, every_days=30, today=None, price=None):
    today = today or ist_today()
    price = price or f.get("price")
    if not price or price <= 0:
        raise InvestError("no price for %s: run fetch (or --kite-quotes) first" % f.get("symbol"))
    if amount <= 0:
        raise InvestError("--amount must be positive")
    tranches = max(1, int(tranches))
    asked = tranches
    rate = (float(ch["delivery"]["stt_buy_pct"]) + float(ch["delivery"]["stamp_buy_pct"]) +
            float(ch["exchange_txn_pct"]["NSE"]) * 1.2) / 100.0
    unit = price * (1 + rate)
    total_qty = int(amount // unit)
    notes = []
    if total_qty == 0:
        raise InvestError("%s does not buy one share of %s at %s" % (inr(amount), f.get("symbol"), inr(price)))
    if total_qty < tranches:
        notes.append("%s buys only %d share(s): %d tranche(s) instead of %d" % (inr(amount), total_qty,
                                                                              total_qty, tranches))
        tranches = total_qty
    base, extra = divmod(total_qty, tranches)
    legs, spent = [], 0.0
    lq = analysis.get("latest_quarter")
    due = results_due(next_quarter(lq["q"])) if lq and lq.get("q") else None
    for i in range(tranches):
        qty = base + (1 if i < extra else 0)
        value = qty * price
        c = delivery_charges("buy", value, ch)
        when = today + datetime.timedelta(days=every_days * i)
        cond = "now, at a limit near %s" % inr(price) if i == 0 else (
            "on or after %s, only if the thesis still holds (re-run analyse first)" % when.isoformat())
        if i == 1 and due and today < due <= when + datetime.timedelta(days=every_days):
            cond = "after the %s results (due by %s), only if they do not break the thesis" % (
                next_quarter(lq["q"]), due.isoformat())
        legs.append({"tranche": i + 1, "qty": qty, "approx_price": price, "value": round(value, 2),
                     "charges": c, "date": when.isoformat(), "condition": cond,
                     "order": {"symbol": f["symbol"], "side": "buy", "qty": str(qty), "product": "CNC",
                               "type": "limit", "limit_price": "%.2f" % price, "time_in_force": "day",
                               "tag": "invest-t%d" % (i + 1)}})
        spent += value + c["total"]
    sell_value = total_qty * price
    sell = delivery_charges("sell", sell_value, ch)
    lt_date = (today + datetime.timedelta(days=365 + 1)).isoformat()
    gain = round(sell_value * 0.2, 2)
    tax = {"stcg_pct": STCG_PCT, "ltcg_pct": LTCG_PCT, "ltcg_exempt_per_year": LTCG_EXEMPT,
           "long_term_from": lt_date,
           "example_gain_20pct": gain,
           "example_stcg_tax": round(gain * STCG_PCT / 100, 2),
           "example_ltcg_tax": round(max(0.0, gain - LTCG_EXEMPT) * LTCG_PCT / 100, 2),
           "text": ["Held 12 months or less: short-term capital gain, taxed at %.0f%%." % STCG_PCT,
                    "Held more than 12 months: long-term, %.1f%% on gains above %s a year "
                    "(the exemption is shared across all your listed equity and equity funds)."
                    % (LTCG_PCT, inr(LTCG_EXEMPT)),
                    "Each tranche has its own buy date, so each turns long-term on its own date (FIFO on sale).",
                    "Plus 4%% health and education cess (and surcharge if it applies). Dividends are taxed at "
                    "your slab rate; the company deducts 10%% TDS above %s a year." % inr(10000)]}
    checklist = []
    if analysis.get("next_results"):
        checklist.append("next results: board meeting %s (NSE, via trader-news); an overnight gap can move a "
                         "tranche bought the day before" % analysis["next_results"]["date"])
    elif due:
        checklist.append("next results: %s due by %s (SEBI LODR deadline; check NSE corporate announcements "
                         "for the board meeting date)" % (next_quarter(lq["q"]), due.isoformat()))
    else:
        checklist.append("next results date unknown: check NSE corporate announcements")
    checklist += ["thesis breaks if: ROE stays under 12% for two years, or debt/equity rises above 1.5",
                  "thesis breaks if: promoters cut their stake by more than 5 points, or start pledging",
                  "thesis breaks if: the dividend is cut while profits are flat (for a yield buy)",
                  "re-check (not auto-sell) if the price closes 25% below your average buy: "
                  "is it the business or the market?"]
    if analysis.get("cyclical"):
        checklist.append("cyclical: judge on mid-cycle earnings; two loss quarters in a row is a thesis check")
    if analysis.get("psu"):
        checklist.append("PSU: watch government pricing and subsidy decisions (e.g. fuel/LPG under-recovery "
                         "compensation) and any offer-for-sale")
    checklist.append("review the whole position once a quarter, after results; re-run analyse")
    mv = analysis.get("market_valuation")
    pace = None
    if mv and mv.get("label") == "expensive":
        pace = {"tranches": asked + EXPENSIVE_EXTRA_TRANCHES,
                "every_days": int(round(every_days * EXPENSIVE_PACE_FACTOR))}
        notes.append("the Nifty 50 P/E is %s on %s (%.0f or more is expensive by the rule of thumb): consider slower "
                     "pacing, for example %d tranches %d days apart instead of %d every %d. A hint, not a timing "
                     "signal" % (fmt(mv["pe"], 2), mv["date"], mv["expensive_at"], pace["tranches"],
                                 pace["every_days"], asked, every_days))
    if VERDICTS.index(analysis["verdict"]) >= 2:
        notes.append("the analyse verdict is '%s': this plan is shown because you asked, not as a "
                     "recommendation" % analysis["verdict"])
    return {"symbol": f["symbol"], "price": price, "amount": amount, "total_qty": total_qty,
            "tranches": legs, "invested_incl_charges": round(spent, 2), "unspent": round(amount - spent, 2),
            "charges_label": charges_label(ch), "charges_verified": ch.get("verified"),
            "sell_all_charges_at_today_price": sell,
            "sell_note": "on a later CNC sell: STT %.1f%%, exchange, SEBI, GST and a DP charge of %s per scrip "
                         "per day (once per sell day, whatever the qty)" % (
                             float(ch["delivery"]["stt_sell_pct"]), inr(ch["delivery"]["dp_per_scrip_sell"])),
            "tax": tax, "review_checklist": checklist, "notes": notes, "verdict": analysis["verdict"],
            "market_valuation": mv, "pace_hint": pace,
            "score": analysis["score"],
            "next_step": "show tranche 1 to the user; only on their explicit yes, run trader-risk then "
                         "trader-broker for a CNC ticket. Not financial advice."}


# ---------------------------------------------------------------- output

def pct(x, nd=1):
    return fmt(x, nd, "%")


def print_analysis(a, out=None):
    out = out or sys.stdout
    w = lambda s="": print(s, file=out)
    m = a["metrics"]
    w("%s  %s   score %.0f/100   %s" % (a["symbol"], inr(a["price"]) if a["price"] else "price n/a",
                                         a["score"], a["verdict"].upper()))
    w("  %s | mcap %s | source: %s | data coverage %.0f%%" % (a.get("sector") or "sector n/a",
                                                             crore(a.get("market_cap_cr")), a.get("source"),
                                                             a["coverage_pct"]))
    w()
    for name, p in a["pillars"].items():
        w("  %-11s %5s/100  (weight %s)" % (name, "n/a" if p["score"] is None else "%.0f" % p["score"],
                                            p["weight"]))
        for line in p["metrics"]:
            if line.get("missing"):
                w("      %-24s MISSING (scored %.0f)" % (line["metric"], line["score"]))
            else:
                extra = " (mid-cycle)" if line.get("field") == "midcycle_pe" else ""
                w("      %-24s %10s%s -> %.0f" % (line["metric"], fmt(line["value"], 2), extra, line["score"]))
    w()
    w("  Valuation: PE %s%s, PB %s, yield %s; earnings yield vs 10y G-sec %s: %s points" % (
        fmt(m["pe"], 2), " (mid-cycle %s)" % fmt(m["midcycle_pe"], 2) if m.get("midcycle_pe") else "",
        fmt(m["pb"], 2), pct(m["div_yield_pct"], 2), pct(a["gsec_10y_pct"], 2),
        fmt(m["earnings_yield_gap"], 2)))
    g = a["growth"]
    w("  Growth: revenue CAGR 3y %s / 5y %s, profit CAGR 3y %s / 5y %s, loss years %s of %s" % (
        pct(g.get("revenue_cagr_3y")), pct(g.get("revenue_cagr_5y")), pct(g.get("profit_cagr_3y")),
        pct(g.get("profit_cagr_5y")), g.get("loss_years"), g.get("years_of_data")))
    mv = a.get("market_valuation")
    if mv:
        w("  Market: %s P/E %s on %s is %s (rule of thumb: %.0f or more expensive, %.0f or less cheap; "
          "a hint, not scored)" % (mv["index"], fmt(mv["pe"], 2), mv["date"], mv["label"], mv["expensive_at"],
                                   mv["cheap_at"]))
    t = a.get("technicals")
    if t:
        w("  Price: %s on %s, 52w %s to %s, %s from the 200-DMA, CAGR 1y %s 3y %s 5y %s, max drawdown %s, "
          "volatility %s" % (fmt(t["last_close"], 2), t["last_date"], fmt(t["low_52w"], 2), fmt(t["high_52w"], 2),
                             pct(t.get("dist_200dma_pct")), pct(t.get("price_cagr_1y")),
                             pct(t.get("price_cagr_3y")), pct(t.get("price_cagr_5y")),
                             pct(t.get("max_drawdown_pct")), pct(t.get("volatility_pct"))))
        if t.get("swing_trend"):
            w("  Swing: %s trend on daily closes, change-of-character level %s, cushion %s%s" % (
                t["swing_trend"].upper(), fmt(t.get("swing_coc"), 2), pct(t.get("swing_cushion_pct")),
                "" if t.get("swing_stable") else " (too few bars to trust)"))
    dv = a.get("delivery")
    if dv:
        w("  Delivery: %s (a hint, not scored)" % dv["text"])
    lq = a.get("latest_quarter")
    if lq:
        w("  Latest quarter %s: revenue %s, net profit %s%s" % (
            lq["q"], crore(lq.get("revenue_cr")), crore(lq.get("net_profit_cr")),
            "" if m.get("latest_quarter_profit_yoy") is None else " (%s y/y)" % pct(m["latest_quarter_profit_yoy"])))
    w()
    for title, items in (("Strengths", a["strengths"]), ("Concerns", a["concerns"]), ("Flags", a["flags"]),
                         ("Verdict caps", a["caps"])):
        if items:
            w("  %s:" % title)
            for i in items:
                w("    - %s" % i)
    w("  Data age:")
    for i in a["data_age"]:
        w("    - %s" % i)
    if a["missing_fields"] or a["missing_metrics"]:
        w("  Missing (never treated as good): fields %s; metrics %s" % (
            ", ".join(a["missing_fields"]) or "none", ", ".join(a["missing_metrics"]) or "none"))
    w("  Scorecard: %s. %s" % (a["scorecard"], a["disclaimer"]))


def print_compare(rows, out=None):
    out = out or sys.stdout
    cols = [("symbol", lambda a: a["symbol"]), ("price", lambda a: fmt(a["price"], 2)),
            ("PE", lambda a: fmt(a["metrics"]["pe"], 1)), ("PB", lambda a: fmt(a["metrics"]["pb"], 2)),
            ("yield%", lambda a: fmt(a["metrics"]["div_yield_pct"], 2)),
            ("ROE%", lambda a: fmt(a["metrics"]["roe_pct"], 1)), ("ROCE%", lambda a: fmt(a["metrics"]["roce_pct"], 1)),
            ("D/E", lambda a: fmt(a["metrics"]["debt_to_equity"], 2)),
            ("rev5y%", lambda a: fmt(a["growth"].get("revenue_cagr_5y"), 1)),
            ("prof5y%", lambda a: fmt(a["growth"].get("profit_cagr_5y"), 1)),
            ("loss yrs", lambda a: str(a["growth"].get("loss_years"))),
            ("value", lambda a: fmt(a["pillars"]["valuation"]["score"], 0)),
            ("quality", lambda a: fmt(a["pillars"]["quality"]["score"], 0)),
            ("growth", lambda a: fmt(a["pillars"]["growth"]["score"], 0)),
            ("stable", lambda a: fmt(a["pillars"]["stability"]["score"], 0)),
            ("tech", lambda a: fmt(a["pillars"]["technicals"]["score"], 0)),
            ("score", lambda a: fmt(a["score"], 0)),
            ("flags", lambda a: ",".join(x for x, on in (("cyclical", a["cyclical"]), ("PSU", a["psu"])) if on) or "-")]
    table = [[c[0] for c in cols]] + [[c[1](a) for c in cols] for a in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(cols))]
    for r in table:
        print("  ".join(v.rjust(widths[i]) if i else v.ljust(widths[i]) for i, v in enumerate(r)), file=out)
    print(file=out)
    for a in rows:
        print("%-12s %s (score %.0f, coverage %.0f%%, data %s)" % (a["symbol"], a["verdict"], a["score"],
                                                                   a["coverage_pct"], a["as_of"]), file=out)
    print("Not financial advice.", file=out)


def print_plan(p, out=None):
    out = out or sys.stdout
    w = lambda s="": print(s, file=out)
    w("%s staggered CNC buy plan: %s at about %s -> %d shares in %d tranche(s)" % (
        p["symbol"], inr(p["amount"]), inr(p["price"]), p["total_qty"], len(p["tranches"])))
    w("  analyse verdict: %s (score %.0f)" % (p["verdict"], p["score"]))
    for t in p["tranches"]:
        w("  T%d  buy %d @ ~%s = %s, charges %s (%s)  %s" % (
            t["tranche"], t["qty"], fmt(t["approx_price"], 2), inr(t["value"]), inr(t["charges"]["total"]),
            p["charges_label"], t["condition"]))
    w("  total with buy charges %s, left unspent %s" % (inr(p["invested_incl_charges"]), inr(p["unspent"])))
    w("  later sell of all %d at today's price: charges about %s incl. DP %s; %s" % (
        p["total_qty"], inr(p["sell_all_charges_at_today_price"]["total"]),
        inr(p["sell_all_charges_at_today_price"]["dp"]), p["sell_note"]))
    w("  Tax:")
    for line in p["tax"]["text"]:
        w("    - %s" % line)
    w("    - tranche 1 turns long-term on %s. Example: a 20%% gain of %s is %s STCG tax if sold early, "
      "%s LTCG if held over a year (inside the exemption)" % (
          p["tax"]["long_term_from"], inr(p["tax"]["example_gain_20pct"]), inr(p["tax"]["example_stcg_tax"]),
          inr(p["tax"]["example_ltcg_tax"])))
    w("  Review checklist:")
    for line in p["review_checklist"]:
        w("    - %s" % line)
    for n in p["notes"]:
        w("  NOTE: %s" % n)
    w("  Next: %s" % p["next_step"])


# ---------------------------------------------------------------- commands

def fund_path(trader_dir, symbol):
    return os.path.join(trader_dir, "data", "fundamentals", symbol + ".json")


def cmd_fetch(args):
    symbol = clean_symbol(args.symbol)
    today = parse_day(args.today) if args.today else ist_today()
    sources, scr, nse, manual, kite = [], None, None, None, None
    if args.screener_html:
        try:
            with open(args.screener_html, encoding="utf-8") as fh:
                scr = parse_screener(fh.read(), symbol)
            sources.append({"name": "screener.in (saved html)", "ok": True, "path": shown(args.screener_html)})
        except (OSError, InvestError) as e:
            err = ("could not read %s: %s" % (shown(args.screener_html), e.strerror)) if isinstance(e, OSError) else str(e)
            sources.append({"name": "screener.in (saved html)", "ok": False, "error": err})
    elif not args.no_net:
        scr, err = fetch_screener(symbol)
        sources.append({"name": "screener.in", "ok": scr is not None, "error": err,
                        "url": (scr or {}).get("source_url")})
    if args.nse_json:
        try:
            nse = parse_nse_quote(read_json(args.nse_json, "NSE quote JSON"))
            sources.append({"name": "nse quote api (saved json)", "ok": True, "path": shown(args.nse_json)})
        except InvestError as e:
            sources.append({"name": "nse quote api (saved json)", "ok": False, "error": str(e)})
    elif not args.no_net:
        nse, err = fetch_nse(symbol)
        sources.append({"name": "nse quote api", "ok": nse is not None, "error": err})
    if args.from_file:
        try:
            manual = read_json(args.from_file, "manual fundamentals JSON")
            if not isinstance(manual, dict):
                raise InvestError("manual JSON must be an object in the fundamentals shape")
            sources.append({"name": "manual json", "ok": True, "path": shown(args.from_file)})
        except InvestError as e:
            sources.append({"name": "manual json", "ok": False, "error": str(e)})
    if args.kite_quotes:
        try:
            kite = parse_kite_quotes(read_json(args.kite_quotes, "Kite quotes JSON"), symbol)
            sources.append({"name": "kite get_quotes", "ok": True, "path": shown(args.kite_quotes)})
        except InvestError as e:
            sources.append({"name": "kite get_quotes", "ok": False, "error": str(e)})
    if not (scr or nse or manual):
        old = read_json(fund_path(args.trader_dir, symbol), "fundamentals", required=False)
        if kite and old:
            manual = {k: v for k, v in old.items() if k not in ("sources", "source", "as_of")}
            manual["as_of"] = old.get("as_of")
        else:
            if args.json:
                print(json.dumps({"symbol": symbol, "ok": False, "sources": sources}, indent=2))
            else:
                for s in sources:
                    print("  %-28s %s" % (s["name"], "ok" if s["ok"] else "FAIL: %s" % s.get("error")))
                print("No fundamentals for %s. screener.in may block or change its HTML: save your numbers in "
                      "the fundamentals shape and run: invest.py fetch %s --from FILE" % (symbol, symbol))
            return 2
    f = build_fundamentals(symbol, scr, nse, manual, kite, today, sources)
    path = args.out or fund_path(args.trader_dir, symbol)
    if not args.no_write:
        write_json(path, f)
    if args.json:
        print(json.dumps(f, indent=2, ensure_ascii=False))
    else:
        for s in sources:
            print("  %-28s %s" % (s["name"], "ok" if s["ok"] else "FAIL: %s" % s.get("error")))
        missing = [k for k in CONTRACT_FIELDS if f.get(k) is None]
        print("%s: price %s, PE %s, %d annual rows, %d quarters, source %s" % (
            symbol, fmt(f.get("price"), 2), fmt(f.get("pe"), 2), len(f["annual"]), len(f["quarterly"]),
            f["source"]))
        print("  missing: %s" % (", ".join(missing) or "none"))
        if not args.no_write:
            print("  wrote %s" % shown(path))
    return 0


def load_for_analysis(args, symbol, card):
    f = read_json(fund_path(args.trader_dir, symbol), "fundamentals for %s (run: invest.py fetch %s)"
                  % (symbol, symbol))
    f.setdefault("symbol", symbol)
    rows, path, adj = read_daily(args.trader_dir, symbol)
    today = parse_day(args.today) if args.today else ist_today()
    horizon = int(re.sub(r"[^0-9]", "", args.horizon or "5") or 5)
    bars = read_daily_bars(args.trader_dir, symbol)[0] if rows else []
    return f, analyse(f, card, rows, args.gsec, today, horizon, {"path": shown(path) if path else None, "adjusted": adj},
                      swing_numbers(bars) if bars else None, read_market_valuation(args.trader_dir),
                      delivery_im_days(read_delivery(args.trader_dir, symbol)),
                      read_next_results(args.trader_dir, symbol, today))


def analysis_path(trader_dir, symbol):
    return os.path.join(trader_dir, "invest", symbol + ".json")


def cmd_analyse(args):
    card = load_scorecard(args.trader_dir, args.scorecard)
    _, a = load_for_analysis(args, clean_symbol(args.symbol), card)
    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        print_analysis(a)
    if not args.no_save:
        # trader-thesis and trader-report read the saved analysis
        path = analysis_path(args.trader_dir, a["symbol"])
        write_json(path, a)
        print("wrote %s" % shown(path), file=sys.stderr if args.json else sys.stdout)
    return 0


def cmd_compare(args):
    card = load_scorecard(args.trader_dir, args.scorecard)
    rows = [load_for_analysis(args, clean_symbol(s), card)[1] for s in args.symbols]
    rows.sort(key=lambda a: -a["score"])
    if args.json:
        print(json.dumps({"ranked": [{"symbol": a["symbol"], "score": a["score"], "verdict": a["verdict"]}
                                     for a in rows], "analyses": rows}, indent=2, ensure_ascii=False))
    else:
        print_compare(rows)
    return 0


def cmd_plan(args):
    card = load_scorecard(args.trader_dir, args.scorecard)
    symbol = clean_symbol(args.symbol)
    f, a = load_for_analysis(args, symbol, card)
    ch = load_charges(args.trader_dir)
    today = parse_day(args.today) if args.today else ist_today()
    price = args.price or f.get("price")
    t = a.get("technicals")
    if not args.price and t and parse_day(t["last_date"]) and f.get("as_of") and \
            parse_day(t["last_date"]) > parse_day(f["as_of"]):
        price = t["last_close"]
    p = plan(f, a, ch, args.amount, args.tranches, args.every, today, price)
    if args.orders:
        write_json(args.orders, {"source": "trader-invest plan", "created": today.isoformat(),
                                 "orders": [p["tranches"][0]["order"]]})
        p["orders_written"] = shown(args.orders)
    if args.json:
        print(json.dumps(p, indent=2, ensure_ascii=False))
    else:
        print_plan(p)
        if args.orders:
            print("  wrote tranche 1 to %s for trader-risk (nothing is placed without the user's yes)" % shown(args.orders))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="Long-term NSE stock analyst (trader-invest)")
    ap.add_argument("--trader-dir", default="trader", help="the project's trader/ folder")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        p.add_argument("--trader-dir", default=argparse.SUPPRESS)
        p.add_argument("--today", default=argparse.SUPPRESS)

    f = sub.add_parser("fetch", help="build trader/data/fundamentals/<SYM>.json")
    common(f)
    f.add_argument("symbol")
    f.add_argument("--from", dest="from_file", help="manual JSON in the fundamentals shape")
    f.add_argument("--kite-quotes", help="saved Kite MCP get_quotes JSON (price)")
    f.add_argument("--screener-html", help="a saved screener.in company page instead of the network")
    f.add_argument("--nse-json", help="a saved NSE /api/quote-equity response instead of the network")
    f.add_argument("--no-net", action="store_true", help="do not touch the network")
    f.add_argument("--out", help="write here instead of trader/data/fundamentals/<SYM>.json")
    f.add_argument("--no-write", action="store_true")

    for name in ("analyse", "analyze"):
        a = sub.add_parser(name, help="score one stock for the long term")
        common(a)
        a.add_argument("symbol")
        a.add_argument("--horizon", default="5y")
        a.add_argument("--gsec", type=float, help="10y G-sec yield %% (default from the scorecard, 6.5)")
        a.add_argument("--scorecard", help="scorecard JSON (default trader/scorecard.json over the example)")
        a.add_argument("--no-save", action="store_true",
                       help="do not write trader/invest/<SYM>.json (read by trader-thesis and trader-report)")

    c = sub.add_parser("compare", help="side by side")
    common(c)
    c.add_argument("symbols", nargs="+")
    c.add_argument("--horizon", default="5y")
    c.add_argument("--gsec", type=float)
    c.add_argument("--scorecard")

    p = sub.add_parser("plan", help="staggered CNC buy plan")
    common(p)
    p.add_argument("symbol")
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--tranches", type=int, default=3)
    p.add_argument("--every", type=int, default=30, help="days between tranches")
    p.add_argument("--price", type=float, help="override the price")
    p.add_argument("--orders", help="write tranche 1 as an orders file for trader-risk")
    p.add_argument("--horizon", default="5y")
    p.add_argument("--gsec", type=float)
    p.add_argument("--scorecard")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        if args.cmd == "fetch":
            return cmd_fetch(args)
        if args.cmd in ("analyse", "analyze"):
            return cmd_analyse(args)
        if args.cmd == "compare":
            return cmd_compare(args)
        if args.cmd == "plan":
            return cmd_plan(args)
    except InvestError as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print("error: %s" % e, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
