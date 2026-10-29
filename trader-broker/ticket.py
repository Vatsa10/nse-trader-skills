#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker (NSE, Zerodha Kite). Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket plus the
            exact JSON Claude will send to the Kite MCP place_order tool.
            A bracket (Zerodha has no native bracket orders) shows the entry,
            the SL-M stop exit and the LIMIT target exit as separate calls
            (or one GTT OCO via place_gtt_order for a CNC delivery trade), and
            the max loss at the stop. An order is READY only if trader-risk
            passed THIS exact order today (matched by fingerprint) and nobody
            has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened after the Allow once / Deny card:
            placed (with Kite's order id), denied, failed, or paper (paper
            mode: logged only, never sent).
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a SELL ticket that closes a symbol's open MIS position by
            flat_by (default 15:15 IST, before Zerodha's ~15:20 auto
            square-off). It still goes through trader-risk and your yes.
  log       prints today's decisions.
  reconcile compares the charges model with your contract notes: reads a
            Zerodha Console tradebook CSV plus the charges you type or export
            from the contract notes (--actual, CSV or JSON), prints estimated
            vs actual per day, and writes calibration.factor (the median of
            actual / estimated) to trader/charges.json. --show prints the model.

Charges everywhere are an estimate: Zerodha rates verified 2026-10-29, deep-merged
with trader/charges.json, times the calibration factor. Tickets show today's
circuit band from trader/data/bands.json (trader-data writes it).

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 260119000123456
    python3 ticket.py fill --symbol HINDCOPPER --side buy --qty 40 --price 312.40
    python3 ticket.py flatten --symbol HINDCOPPER --qty 40
    python3 ticket.py log
    python3 ticket.py reconcile tradebook.csv --actual contract-charges.csv
    python3 ticket.py reconcile --show

Everything is written to trader/state/broker.json (append only). Times are
IST (UTC+05:30, no daylight saving); --now "2026-10-19 10:15" pins the clock.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys

# Kite place_order arguments, plus our two bracket fields (stoploss, target) that
# become separate exit orders. "why" and "plan" are ours and are never sent.
# trader/orders.json schema (trader-budget writes it, trader-risk/check.py fingerprints it)
ORDER_FIELDS = ("symbol", "side", "qty", "product", "type", "time_in_force", "limit_price",
                "stop_price", "order_class", "take_profit_limit_price", "stop_loss_stop_price", "tag")
PLACE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product",
                "order_type", "price", "trigger_price", "validity", "tag")
KITE_FIELDS = PLACE_FIELDS + ("stoploss", "target")
PRODUCTS = {"MIS": "intraday (MIS, auto square-off ~15:20)", "CNC": "delivery (CNC)"}
TYPE_WORDS = {"MARKET": "market", "LIMIT": "limit", "SL": "stop-limit", "SL-M": "stop-market"}
VALIDITY_WORDS = {"DAY": "good for today", "IOC": "immediate or cancel"}
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
IST = datetime.timedelta(hours=5, minutes=30)
FLAT_BY = "15:15"
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, fixed +05:30)

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
        raise TicketError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-19T10:15:00+05:30"."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date", else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- money

def inr(x, paise=None):
    """12345678.9 -> "₹1,23,45,678.90" (Indian grouping). Whole rupees drop ".00"."""
    f = float(x)
    neg = f < 0
    f = round(abs(f), 2)
    whole = int(f)
    frac = int(round((f - whole) * 100))
    if frac == 100:
        whole, frac = whole + 1, 0
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    if frac or paise:
        s += ".%02d" % frac
    return ("-" if neg else "") + "₹" + s


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
_CHARGES_CACHE = {}


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
    try:
        key = (os.path.abspath(path), os.path.getmtime(path))
        if key not in _CHARGES_CACHE:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            _CHARGES_CACHE[key] = deep_merge(CHARGES_DEFAULTS, doc if isinstance(doc, dict) else {})
        return deep_merge(_CHARGES_CACHE[key], {})
    except (OSError, ValueError):
        return deep_merge(CHARGES_DEFAULTS, {})


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


def costs(buy_value, sell_value, product="MIS", model=None):
    """Estimated charges for a round trip, in rupees: charge_parts() times calibration.factor."""
    m = model if model is not None else load_charges()
    return round(sum(charge_parts(buy_value, sell_value, product, m).values()) * charges_factor(m), 2)


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


# ---------------------------------------------------------------- the order

def norm_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if not SYMBOL.match(s):
        raise TicketError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % sym)
    return s


def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order as written in
    trader/orders.json (ORDER_FIELDS schema). A translated Kite ticket carries it in "_fp"."""
    if "_fp" in order:
        return order["_fp"]
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


KITE_TYPE = {"market": "MARKET", "limit": "LIMIT", "stop": "SL-M", "stop_limit": "SL"}


def to_kite(order):
    """An orders.json order (ORDER_FIELDS schema, written by trader-budget, checked by
    trader-risk) -> the internal Kite ticket used for rendering. Raises TicketError."""
    if "tradingsymbol" in order and "symbol" not in order:
        raise TicketError("orders.json uses the trader-budget schema (symbol, side, qty, type ...), "
                          "not Kite field names")
    typ = str(order.get("type", "market")).lower()
    if typ not in KITE_TYPE:
        raise TicketError("type must be market, limit, stop (SL-M) or stop_limit (SL), got %r" % order.get("type"))
    tif = str(order.get("time_in_force", "day")).lower()
    if tif not in ("day", "ioc"):
        raise TicketError("time_in_force must be day or ioc, got %r" % order.get("time_in_force"))
    side = str(order.get("side", "")).lower()
    if side not in ("buy", "sell"):
        raise TicketError("side must be buy or sell, got %r" % order.get("side"))
    q = _f(order.get("qty"))
    k = {"variety": "regular", "exchange": "NSE", "tradingsymbol": norm_symbol(order.get("symbol")),
         "transaction_type": side.upper(), "quantity": _num(order.get("qty")) if q is not None else order.get("qty"),
         "product": str(order.get("product", "MIS")).upper(), "order_type": KITE_TYPE[typ],
         "validity": tif.upper()}
    if typ in ("limit", "stop_limit") and order.get("limit_price") is not None:
        k["price"] = _num(order["limit_price"])
    if typ in ("stop", "stop_limit") and order.get("stop_price") is not None:
        k["trigger_price"] = _num(order["stop_price"])
    if order.get("tag"):
        k["tag"] = order["tag"]
    if str(order.get("order_class") or "").lower() == "bracket" or order.get("stop_loss_stop_price") is not None \
            or order.get("take_profit_limit_price") is not None:
        if order.get("stop_loss_stop_price") is not None:
            k["stoploss"] = _num(order["stop_loss_stop_price"])
        if order.get("take_profit_limit_price") is not None:
            k["target"] = _num(order["take_profit_limit_price"])
    for extra in ("why", "plan"):
        if order.get(extra) is not None:
            k[extra] = order[extra]
    k["_fp"] = fingerprint(order)
    return k


def _num(v):
    """Numbers stay numbers in Kite calls: whole -> int, else float."""
    if isinstance(v, bool) or v is None:
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else f


def _str_num(v):
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _p(v):
    return "%.2f" % float(v)


def tool_input(order):
    """The exact place_order arguments for the entry (Kite parameter names)."""
    out = {}
    for k in PLACE_FIELDS:
        if k in order and order[k] is not None:
            out[k] = _num(order[k]) if k in ("quantity", "price", "trigger_price") else order[k]
    out.setdefault("variety", "regular")
    out.setdefault("exchange", "NSE")
    out.setdefault("validity", "DAY")
    return {k: out[k] for k in PLACE_FIELDS if k in out}


def is_bracket(order):
    return order.get("stoploss") is not None or order.get("target") is not None


def exit_side(order):
    return "SELL" if str(order.get("transaction_type", "")).upper() == "BUY" else "BUY"


def exit_calls(order):
    """The protective exits for a bracket, as [(tool, args, words)].
    MIS: an SL-M stop exit and a LIMIT target exit (two separate place_order calls,
    they do NOT cancel each other: cancel the other one when one fills).
    CNC: one GTT OCO (two-leg) via place_gtt_order, which does cancel the other leg."""
    if not is_bracket(order):
        return []
    sym = order.get("tradingsymbol")
    qty = _num(order.get("quantity"))
    side = exit_side(order)
    product = str(order.get("product", "MIS")).upper()
    stop, target = _f(order.get("stoploss")), _f(order.get("target"))
    out = []
    if product == "CNC" and stop and target:
        ltp = _f((order.get("plan") or {}).get("entry")) or _f(order.get("price")) or round((stop + target) / 2, 2)
        leg = lambda px: {"exchange": "NSE", "tradingsymbol": sym, "transaction_type": side, "quantity": qty,
                          "order_type": "LIMIT", "product": "CNC", "price": _num(px)}
        out.append(("place_gtt_order", {
            "exchange": "NSE", "tradingsymbol": sym, "trigger_type": "two-leg", "last_price": _num(ltp),
            "trigger_values": [_num(stop), _num(target)], "orders": [leg(stop), leg(target)]},
            "GTT OCO: stop %s / target %s (one cancels the other; a GTT can fill worse on a gap)"
            % (_p(stop), _p(target))))
        return out
    if stop:
        out.append(("place_order", {
            "variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": side,
            "quantity": qty, "product": product, "order_type": "SL-M", "trigger_price": _num(stop),
            "validity": "DAY"}, "stop-loss exit SL-M trigger %s" % _p(stop)))
    if target:
        out.append(("place_order", {
            "variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": side,
            "quantity": qty, "product": product, "order_type": "LIMIT", "price": _num(target),
            "validity": "DAY"}, "target exit LIMIT %s" % _p(target)))
    return out


def entry_price(order):
    plan = order.get("plan") or {}
    if str(order.get("order_type", "MARKET")).upper() in ("LIMIT", "SL") and _f(order.get("price")):
        return _f(order["price"])
    return _f(plan.get("entry"))


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, before charges, or (None, stop)."""
    plan = order.get("plan") or {}
    stop = _f(order.get("stoploss")) or _f(plan.get("stop"))
    q = _f(order.get("quantity"))
    if not stop or not q:
        return None, stop
    entry = entry_price(order)
    if entry is None and _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    if not entry:
        return None, stop
    return round(q * int(round(abs(entry - stop) * 100)) / 100.0, 2), stop


def charges_at_stop(order):
    loss, stop = max_loss(order)
    entry = entry_price(order)
    q = _f(order.get("quantity"))
    if loss is None or not entry or not q:
        return None
    buy, sell = (entry, stop) if str(order.get("transaction_type", "")).upper() == "BUY" else (stop, entry)
    return costs(q * buy, q * sell, order.get("product", "MIS"))


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("transaction_type", "")).upper()
    sym = order.get("tradingsymbol", "?")
    typ = str(order.get("order_type", "MARKET")).upper()
    product = str(order.get("product", "MIS")).upper()
    tif = VALIDITY_WORDS.get(str(order.get("validity", "DAY")).upper(), order.get("validity"))
    q = _str_num(order.get("quantity"))
    if typ == "LIMIT":
        how = "limit %s" % inr(order.get("price"), True)
    elif typ == "SL":
        how = "stop-limit trigger %s, limit %s" % (inr(order.get("trigger_price"), True), inr(order.get("price"), True))
    elif typ == "SL-M":
        how = "stop-market trigger %s" % inr(order.get("trigger_price"), True)
    else:
        how = "market"
    parts = ["%s %s %s on NSE, %s, %s, %s" % (side, q, sym, how, product, tif)]
    if is_bracket(order):
        if order.get("stoploss") is not None:
            parts.append("stop-loss %s" % _p(order["stoploss"]))
        if order.get("target") is not None:
            parts.append("target %s" % _p(order["target"]))
        loss, stop = max_loss(order)
        if loss is not None:
            ch = charges_at_stop(order)
            parts.append("max loss about %s if the stop fills at %s%s (it can fill worse in a fast market, "
                         "or not at all past the lower circuit)"
                         % (inr(loss, True), _p(stop),
                            (" plus about %s charges, %s" % (inr(ch, True), charges_label()))
                            if ch is not None else ""))
    return parts


def plain(order):
    """BUY 40 HINDCOPPER on NSE, market, MIS, good for today / stop-loss 308.10 / target 320.50 / max loss ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("quantity", "price", "trigger_price", "stoploss", "target"):
        v = _f(order.get(k))
        if v is not None:
            nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it, haan)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("tradingsymbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\-]{0,19}", raw.upper()))
    others = {str(s).upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the place_order call" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("transaction_type", "")).upper() == "BUY" else "exit"


def check_shape(order):
    """Whole shares, NSE equity, known product and type. Raises TicketError."""
    norm_symbol(order.get("tradingsymbol"))
    q = _f(order.get("quantity"))
    if q is None or q <= 0 or q != int(q):
        raise TicketError("quantity must be a whole number of shares, got %r" % order.get("quantity"))
    if str(order.get("exchange", "NSE")).upper() != "NSE":
        raise TicketError("only NSE cash equity orders are supported")
    if str(order.get("product", "MIS")).upper() not in PRODUCTS:
        raise TicketError("product must be MIS or CNC (no F&O, no NRML)")
    if str(order.get("order_type", "MARKET")).upper() not in TYPE_WORDS:
        raise TicketError("order_type must be MARKET, LIMIT, SL or SL-M")


# ---------------------------------------------------------------- rendering

def band_lines(order, bands, today):
    """The circuit band the exchange checks this order against, in words."""
    sym = order.get("tradingsymbol", "")
    band, why = band_for(bands, sym, today)
    if band is None:
        extra = "" if why == "unknown" else " (%s)" % why
        return ["circuit band unknown%s: run trader-data bands, or save Kite get_quotes and import it "
                "with trader-data import-kite" % extra]
    lines = ["circuit band%s: %s" % (" today" if band["today"] else "", band_words(band))]
    if band.get("lower") is None:
        return lines
    for k, words in (("price", "limit price"), ("trigger_price", "trigger price"), ("stoploss", "stop-loss"),
                     ("target", "target")):
        v = _f(order.get(k))
        if v and (v < band["lower"] - 0.005 or v > band["upper"] + 0.005):
            lines.append("the %s %s is outside the band: the exchange rejects it today" % (words, _p(v)))
    plan_stop = _f(order.get("stoploss")) or _f((order.get("plan") or {}).get("stop"))
    if plan_stop and str(order.get("transaction_type", "")).upper() == "BUY" and plan_stop < band["lower"] - 0.005:
        lines.append("the stop %s is below the lower circuit %s: the stop may not fill if the stock hits lower "
                     "circuit" % (_p(plan_stop), _p(band["lower"])))
    return lines


def render(order, idx, total, cfg, risk, state, today, bands=None):
    ready, label, rv = status_of(order, risk, state, today)
    paper = cfg.get("paper", True)
    acct = "PAPER mode (logged only, never sent)" if paper else "LIVE KITE ACCOUNT, REAL MONEY"
    lines = ["ORDER %d of %d, %s, Zerodha Kite" % (idx, total, acct)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    for b in band_lines(order, bands, today):
        lines.append("  " + b)
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    lines.append("  %s place_order with exactly:" % ("Paper: Claude would call" if paper else "Claude will call"))
    for l in json.dumps(tool_input(order), indent=2, ensure_ascii=False).splitlines():
        lines.append("    " + l)
    exits = exit_calls(order)
    if exits:
        lines.append("  Zerodha has no bracket orders. After the entry fills, the exits are separate calls,")
        lines.append("  each with its own Allow once card:")
        for tool, args, words in exits:
            lines.append("    %s (%s): %s" % (tool, words, json.dumps(args, ensure_ascii=False)))
        if str(order.get("product", "MIS")).upper() == "MIS":
            lines.append("  The SL-M and the LIMIT exit do NOT cancel each other: when one fills, cancel the")
            lines.append("  other with cancel_order, or it can open a new position.")
    if ready:
        lines.append("  Reply \"yes\" to %s this one order, or \"no\" to skip it."
                     % ("log (paper)" if paper else "place"))
        if not paper:
            lines.append("  Claude's own card then asks again: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today, bands=None):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    band, band_state = band_for(bands, order.get("tradingsymbol", ""), today)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": "kite", "tool": "place_order", "tool_input": tool_input(order),
            "exits": [{"tool": t, "args": a, "plain": w} for t, a, w in exit_calls(order)],
            "why": order.get("why", ""), "bracket": is_bracket(order), "max_loss": loss,
            "charges_at_stop": charges_at_stop(order), "charges_label": charges_label(), "stop": stop,
            "band": band, "band_state": band_state, "band_notes": band_lines(order, bands, today)}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and whole shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = norm_symbol(symbol)
    side = str(side).lower()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    if qty != int(qty):
        raise TicketError("NSE fills are whole shares, got %s" % qty)
    qty = int(qty)
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = int(round(float(pos.get("shares", 0)) + qty))
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s (no overnight shorts in "
                              "cash equity)" % (qty, symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 0:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = int(round(float(lot["shares"]) - take))
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 0]
        pos["shares"] = int(round(float(pos["shares"]) - qty))
        if pos["shares"] <= 0:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log, so trader-risk can see intraday round trips."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    orders = [to_kite(o) for o in orders]
    for o in orders:
        check_shape(o)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    a.bands_doc = load_bands(getattr(a, "bands", None))
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today, a.bands_doc) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today, a.bands_doc)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("tradingsymbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    paper = bool(cfg.get("paper", True))
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(), "qty": order.get("quantity"),
           "product": order.get("product", "MIS"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply, "paper": paper, "broker": "kite"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if paper:
        print("PAPER mode: do NOT call place_order. Nothing is sent to Kite. Log it with:")
        print("  ticket.py result --index %d --status paper --filled-qty Q --filled-price P" % a.index)
        print("Kite place_order would have been:")
        print(json.dumps(tool_input(order), indent=2, ensure_ascii=False))
    else:
        print("Now call place_order with exactly:")
        print(json.dumps(tool_input(order), indent=2, ensure_ascii=False))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    if a.status == "placed" and rec.get("paper", True):
        raise TicketError("paper mode: nothing was sent, record --status paper (set \"paper\": false yourself "
                          "in trader/config.json to go live)")
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    for k in ("qty", "kind", "max_loss", "stop", "product"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status in ("placed", "paper") and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    elif a.status in ("placed", "paper"):
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    if a.status == "placed" and rec.get("kind") == "entry" and is_bracket(order):
        print("Entry placed. Now the exits, each its own place_order / place_gtt_order call with Allow once:")
        for tool, args, words in exit_calls(order):
            print("  %s (%s): %s" % (tool, words, json.dumps(args, ensure_ascii=False)))
        print("When the stop or the target fills later, record that exit with ticket.py fill.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, norm_symbol(a.symbol), a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (
        a.side, a.qty, norm_symbol(a.symbol), inr(a.price, True), date, a.portfolio, inr(pf["cash"], True)))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = norm_symbol(a.symbol)
    now, today = _clock(a)
    source = "the broker, get_positions"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or qty != int(qty):
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 40")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 0:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker (get_positions) and "
                              "pass --qty if you do hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or FLAT_BY
    product = str(a.product or "MIS").upper()
    order = {"symbol": sym, "side": "sell", "qty": str(int(qty)), "product": product, "type": "market",
             "time_in_force": "day",
             "why": "flat by %s IST: closes the %s intraday position before Zerodha's ~15:20 auto square-off "
                    "(qty from %s)" % (flat_by, sym, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(to_kite(order)), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_positions before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. Cancel the open SL-M stop and LIMIT target exits first (get_orders, then cancel_order for each,")
    print("     Needs approval), or a GTT with delete_gtt_order; otherwise they can fill after you are flat and")
    print("     open a new position. Then place_order with the ticket.")
    print("  If you do nothing, Zerodha squares off open MIS positions around 15:20 IST (with a charge).")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % inr(r["max_loss"], True)) \
            if r.get("max_loss") is not None and r["status"] in ("placed", "paper") else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    paper = sum(1 for r in rows if r.get("status") == "paper")
    print("%d placed, %d paper%s." % (placed, paper, "" if a.all else " today"))
    return 0


# ---------------------------------------------------------------- reconcile: contract notes -> calibration

CHARGE_FIELDS = ("brokerage", "stt", "exchange_txn", "sebi", "stamp", "gst", "dp")
RATIO_OK = (0.5, 2.0)        # actual / estimated outside this is left out of the calibration


def parse_day(text):
    """"2026-10-19", "2026-10-19T09:45:12", "19-10-2026", "19/10/2026" -> "2026-10-19", else None."""
    s = str(text or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
        if not m:
            return None
        d, mo, y = m.groups()
    try:
        return datetime.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _rows_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(fh)]


def read_tradebook(path):
    """Zerodha Console tradebook CSV -> (trades, notes). A trade is {date, symbol, exchange,
    side, qty, price, order_id, product}; F&O, currency and commodity rows are skipped."""
    if not path or not os.path.exists(path):
        raise TicketError("no tradebook at %s (Console > Reports > Tradebook > download CSV)" % path)
    try:
        rows = _rows_csv(path)
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        raise TicketError("cannot read the tradebook %s: %s" % (path, e))
    trades, notes, skipped = [], [], 0
    for n, r in enumerate(rows, 2):
        seg = (r.get("segment") or "EQ").upper()
        if seg not in ("EQ", "EQUITY", ""):
            skipped += 1
            continue
        day = parse_day(r.get("trade_date") or r.get("date") or r.get("order_execution_time"))
        sym = str(r.get("tradingsymbol") or r.get("symbol") or "").upper()
        sym = re.sub(r"(\.NS|-EQ|-BE)$", "", sym)
        side = str(r.get("trade_type") or r.get("transaction_type") or r.get("side") or "").lower()
        qty, price = _f(r.get("quantity")), _f(r.get("price"))
        if not day or not sym or side not in ("buy", "sell") or not qty or not price:
            notes.append("row %d skipped: needs trade_date, symbol, trade_type buy/sell, quantity, price" % n)
            continue
        prod = str(r.get("product") or "").upper()
        trades.append({"date": day, "symbol": sym, "exchange": (r.get("exchange") or "NSE").upper(),
                       "side": side, "qty": abs(qty), "price": price,
                       "order_id": r.get("order_id") or "row%d" % n,
                       "product": {"MIS": "MIS", "INTRADAY": "MIS", "CNC": "CNC", "DELIVERY": "CNC"}.get(prod)})
    if skipped:
        notes.append("%d non-equity row%s skipped (F&O, currency, commodity are out of scope)"
                     % (skipped, "" if skipped == 1 else "s"))
    if not trades:
        raise TicketError("no equity trades in %s" % path)
    return trades, notes


def estimate_day_charges(trades, model):
    """{date: {"computed", "trades", "symbols", "parts"}} before calibration. Per day and symbol,
    shares bought and sold the same day are intraday (MIS rates), the rest delivery (CNC) unless
    the tradebook has a product column. Brokerage is per order_id."""
    groups = {}
    for t in trades:
        groups.setdefault((t["date"], t["symbol"], t["exchange"]), []).append(t)
    days = {}
    for (day, sym, exch), ts in sorted(groups.items()):
        by_prod = {}
        if all(t["product"] for t in ts):
            for t in ts:
                by_prod.setdefault(t["product"], []).append((t, 1.0))
        else:
            bq = sum(t["qty"] for t in ts if t["side"] == "buy")
            sq = sum(t["qty"] for t in ts if t["side"] == "sell")
            iq = min(bq, sq)
            for t in ts:
                side_q = bq if t["side"] == "buy" else sq
                frac = iq / side_q if side_q else 0.0
                if frac > 0:
                    by_prod.setdefault("MIS", []).append((t, frac))
                if frac < 1:
                    by_prod.setdefault("CNC", []).append((t, 1.0 - frac))
        d = days.setdefault(day, {"computed": 0.0, "trades": 0, "symbols": [], "parts": {}})
        d["trades"] += len(ts)
        d["symbols"].append(sym)
        for prod, items in by_prod.items():
            orders = {"buy": {}, "sell": {}}
            for t, frac in items:
                o = orders[t["side"]]
                o[t["order_id"]] = o.get(t["order_id"], 0.0) + t["qty"] * t["price"] * frac
            bv, sv = sum(orders["buy"].values()), sum(orders["sell"].values())
            parts = charge_parts(bv, sv, prod, model, list(orders["buy"].values()), list(orders["sell"].values()),
                                 dp_scrips=1 if (prod == "CNC" and sv > 0) else 0, exchange=exch)
            for k, v in parts.items():
                d["parts"][k] = d["parts"].get(k, 0.0) + v
                d["computed"] += v
    return days


def read_actual(path):
    """Contract-note charges -> {date: total}. CSV or JSON rows with date plus total or the
    parts (brokerage, stt, exchange_txn, sebi, stamp, gst, dp), or a JSON {date: total}."""
    if not path or not os.path.exists(path):
        raise TicketError("no contract-note charges at %s" % path)
    if path.lower().endswith(".json"):
        doc = read_json(path, "contract-note charges")
        if isinstance(doc, dict) and doc and all(_f(v) is not None for v in doc.values()):
            rows = [{"date": k, "total": v} for k, v in doc.items()]
        elif isinstance(doc, dict):
            rows = doc.get("days") or doc.get("charges") or doc.get("contract_notes") or []
        else:
            rows = doc
        if not isinstance(rows, list):
            raise TicketError("%s: want rows of {date, total} or {date: total}" % path)
        rows = [{str(k).strip().lower(): v for k, v in r.items()} for r in rows if isinstance(r, dict)]
    else:
        try:
            rows = _rows_csv(path)
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            raise TicketError("cannot read %s: %s" % (path, e))
    out = {}
    for r in rows:
        day = parse_day(r.get("date") or r.get("trade_date"))
        if not day:
            continue
        total = _f(r.get("total"))
        if total is None:
            vals = [_f(r.get(k)) for k in CHARGE_FIELDS + ("exchange",)]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            total = sum(vals)
        out[day] = out.get(day, 0.0) + total
    if not out:
        raise TicketError("no dated charges in %s (want date plus total, or the charge columns)" % path)
    return out


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def reconcile(trades, actual, model, today):
    """Pure: per-day estimate vs actual, and the new calibration (None if nothing to fit)."""
    days = estimate_day_charges(trades, model)
    factor = charges_factor(model)
    rows, notes = [], []
    cal = model.get("calibration") or {}
    history = dict(cal.get("history") or {})
    fitted = {}
    for day in sorted(days):
        est = days[day]["computed"]
        act = (actual or {}).get(day)
        row = {"date": day, "trades": days[day]["trades"], "symbols": days[day]["symbols"],
               "estimated": round(est, 2), "estimated_calibrated": round(est * factor, 2),
               "actual": round(act, 2) if act is not None else None, "diff": None, "ratio": None}
        if act is not None and est > 0:
            row["diff"] = round(act - est, 2)
            row["ratio"] = round(act / est, 4)
            if act > 0 and RATIO_OK[0] <= act / est <= RATIO_OK[1]:
                fitted[day] = row["ratio"]
            else:
                notes.append("%s left out of the calibration: actual/estimated %.2f is outside %.1f to %.1f; check "
                             "the contract note is for the same day and equity only" % (day, act / est, RATIO_OK[0],
                                                                                         RATIO_OK[1]))
        rows.append(row)
    for day in sorted(set(actual or {}) - set(days)):
        notes.append("%s has contract-note charges but no trades in the tradebook: skipped" % day)
    new = None
    if fitted:
        history.update(fitted)
        new = {"factor": round(_median(list(history.values())), 4), "samples": len(history), "updated": today,
               "history": {k: history[k] for k in sorted(history)}}
    return {"days": rows, "notes": notes, "calibration": new, "old_factor": factor}


def cmd_reconcile(a):
    now, today = _clock(a)
    path = CHARGES_FILE or "trader/charges.json"
    if a.show:
        model = load_charges(path)
        if a.json:
            print(json.dumps(model, indent=2, ensure_ascii=False))
            return 0
        cal = model.get("calibration") or {}
        print("Charges model (%s): %s rates, verified %s." % (charges_label(model), model.get("broker"),
                                                             model.get("verified")))
        print("  file: %s (%s)" % (path, "found, merged over the defaults" if os.path.exists(path)
                                   else "not there: the embedded defaults"))
        i, d = model.get("intraday") or {}, model.get("delivery") or {}
        print("  intraday (MIS): brokerage %s%% capped at %s an order, STT %s%% on the sell, stamp %s%% on the buy"
              % (i.get("brokerage_pct"), inr(i.get("brokerage_cap") or 0), i.get("stt_sell_pct"), i.get("stamp_buy_pct")))
        print("  delivery (CNC): brokerage %s%%, STT %s%% buy and sell, stamp %s%% on the buy, DP %s a scrip a sell day"
              % (d.get("brokerage_pct"), d.get("stt_buy_pct"), d.get("stamp_buy_pct"),
                 inr(d.get("dp_per_scrip_sell") or 0, True)))
        tx = model.get("exchange_txn_pct")
        tx = ", ".join("%s %s%%" % kv for kv in sorted(tx.items())) if isinstance(tx, dict) else "%s%%" % tx
        print("  exchange txn %s, SEBI %s a crore, GST %s%% on brokerage + txn + SEBI"
              % (tx, inr(model.get("sebi_per_crore") or 0),
                 model.get("gst_pct")))
        print("  calibration: factor %s from %s contract note%s, updated %s"
              % (cal.get("factor"), cal.get("samples") or 0, "" if cal.get("samples") == 1 else "s",
                 cal.get("updated") or "never"))
        for day, r in sorted((cal.get("history") or {}).items()):
            print("    %s  actual/estimated %.4f" % (day, float(r)))
        if not cal.get("samples"):
            print("Calibrate: ticket.py reconcile TRADEBOOK.csv --actual CHARGES.csv (or .json)")
        return 0
    if not a.tradebook:
        raise TicketError("reconcile wants a tradebook CSV (Console > Reports > Tradebook), or --show")
    model = load_charges(path)
    trades, notes = read_tradebook(a.tradebook)
    actual = read_actual(a.actual) if a.actual else None
    res = reconcile(trades, actual, model, today)
    res["notes"] = notes + res["notes"]
    if res["calibration"]:
        raw = read_json(path, "charges file", required=False)
        raw = raw if isinstance(raw, dict) else {}
        raw.setdefault("broker", CHARGES_DEFAULTS["broker"])
        raw.setdefault("verified", CHARGES_DEFAULTS["verified"])
        raw["calibration"] = res["calibration"]
        write_json(path, raw)
        res["written"] = path
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print("Charges per day: the model's estimate (uncalibrated) vs your contract note.")
    print("  %-10s  %6s  %12s  %12s  %10s  %s" % ("date", "trades", "estimated", "actual", "diff", "actual/est"))
    for r in res["days"]:
        print("  %-10s  %6d  %12s  %12s  %10s  %s" % (
            r["date"], r["trades"], inr(r["estimated"], True),
            inr(r["actual"], True) if r["actual"] is not None else "-",
            (("+" if r["diff"] >= 0 else "") + inr(r["diff"], True)) if r["diff"] is not None else "-",
            ("%.4f" % r["ratio"]) if r["ratio"] is not None else "-"))
    tot_e = sum(r["estimated"] for r in res["days"] if r["actual"] is not None)
    tot_a = sum(r["actual"] for r in res["days"] if r["actual"] is not None)
    if tot_e:
        print("  matched days: estimated %s, actual %s, diff %s" % (inr(tot_e, True), inr(tot_a, True),
                                                                   inr(tot_a - tot_e, True)))
    for n in res["notes"]:
        print("note: " + n)
    cal = res["calibration"]
    if cal:
        print("Calibration: factor %.4f (median of actual/estimated over %d contract note%s; was %.4f), "
              "written to %s. Every charge estimate in trader-budget, trader-risk and trader-broker now uses it."
              % (cal["factor"], cal["samples"], "" if cal["samples"] == 1 else "s", res["old_factor"], path))
    elif actual is None:
        print("No contract-note charges given, so nothing was calibrated. Add --actual FILE with each day's "
              "total charges from the contract note (CSV or JSON).")
    else:
        print("Nothing to calibrate: no day had both trades and a usable contract-note total.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker, NSE). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--bands", help="price bands (default trader/data/bands.json, from trader-data)")
        p.add_argument("--charges", help="charges override and calibration (default trader/charges.json)")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the Allow once / Deny card")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed", "paper"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a SELL ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from get_positions (default: trader/portfolio.json)")
    p.add_argument("--product", choices=["MIS", "CNC"], default="MIS")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("reconcile", help="compare estimated charges with your contract notes and calibrate")
    p.add_argument("tradebook", nargs="?", help="Zerodha Console tradebook CSV")
    p.add_argument("--actual", "--charges-file", dest="actual",
                   help="contract-note charges: CSV or JSON rows (date, brokerage, stt, exchange_txn, sebi, "
                        "stamp, gst, dp, total) or JSON {date: total_charges}")
    p.add_argument("--charges", default=None, help="the charges file to calibrate (default trader/charges.json)")
    p.add_argument("--show", action="store_true", help="print the charges model and its calibration")
    p.add_argument("--json", action="store_true")
    clock(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    global CHARGES_FILE
    saved = CHARGES_FILE
    if getattr(a, "charges", None):
        CHARGES_FILE = a.charges
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log, "reconcile": cmd_reconcile}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1
    finally:
        CHARGES_FILE = saved


if __name__ == "__main__":
    sys.exit(main())
