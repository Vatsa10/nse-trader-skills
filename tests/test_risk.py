import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

check = load("trader-risk", "check")
check.CHARGES_FILE = ""          # the embedded defaults, whatever is in the repo's trader/
check.BANDS_FILE = ""

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
REL_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
BANDS_PATH = os.path.join(FIX, "trader-risk", "bands.json")
ETF_PATH = os.path.join(FIX, "trader-risk", "orders-etfs.json")
NOW = "2026-10-19 09:45"
TODAY = "2026-10-19"
SPLIT_TODAY = "2026-10-13"
PRICES = {"NIFTYBEES": 285.40, "GOLDBEES": 102.30, "BANKBEES": 598.20, "ITBEES": 41.75, "BEL": 410.00}
QUOTE_TIME = datetime.datetime(2026, 10, 19, 4, 22, 0)   # 09:52 IST
# long-term split rules: every day-trade check off
SPLIT = {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 5000,
         "min_order_inr": 500, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
         "no_entries_after": None, "no_mis_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0,
         "max_loss_per_trade_inr": 0, "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def rel(**over):
    with open(REL_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target, product="MIS"):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "product": product, "type": "market",
            "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop, "plan": {"entry": entry, "stop": stop, "target": target}}


def etf_orders():
    with open(ETF_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, qty, product="CNC"):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "product": product, "type": "market",
            "time_in_force": "day"}


def placed(sym, side="buy", day=TODAY, max_loss=86.11, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T09:50:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(check.inr(1234567.89), "₹12,34,567.89")
        self.assertEqual(check.inr(100), "₹100")
        self.assertEqual(check.inr(99999), "₹99,999")
        self.assertEqual(check.inr(100000), "₹1,00,000")

    def test_costs_are_approximate_and_small(self):
        c = check.costs(4, 1452.30, 1432.30, "MIS")
        self.assertTrue(c["approximate"])
        self.assertEqual(c["total"], 6.12)
        self.assertEqual(c["brokerage"], 3.46)            # 0.03% each side, under the ₹20 cap
        self.assertEqual(c["label"], "estimate")
        self.assertEqual(check.costs(1000, 1000, 1000)["brokerage"], 40.0)   # ₹20 cap each side
        cnc = check.costs(4, 1452.30, 1432.30, "CNC")
        self.assertEqual((cnc["brokerage"], cnc["dp"], cnc["total"]), (0.0, 15.34, 28.18))
        self.assertGreater(cnc["total"], c["total"])

    def test_the_verified_rates(self):
        m = check.load_charges("")
        self.assertEqual(m["exchange_txn_pct"], {"NSE": 0.00307})
        self.assertEqual((m["intraday"]["stamp_buy_pct"], m["delivery"]["stamp_buy_pct"]), (0.003, 0.015))
        self.assertEqual((m["delivery"]["stt_buy_pct"], m["delivery"]["stt_sell_pct"]), (0.1, 0.1))
        self.assertEqual((m["delivery"]["dp_per_scrip_sell"], m["gst_pct"]), (15.34, 18))
        p = check.charge_parts(1e7, 0, "MIS", m)
        self.assertAlmostEqual(p["exchange"], 307.0)
        self.assertAlmostEqual(p["sebi"], 10.0)
        self.assertAlmostEqual(p["gst"], 0.18 * (20 + 307 + 10))
        self.assertEqual(check.charge_parts(10000, 0, "CNC", m)["dp"], 0.0)   # DP only on a CNC sell

    def test_charges_file_is_deep_merged_and_calibrated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "charges.json")
            with open(path, "w") as fh:
                json.dump({"intraday": {"brokerage_cap": 10}, "calibration": {"factor": 1.5, "samples": 3}}, fh)
            m = check.load_charges(path)
            self.assertEqual(m["intraday"]["brokerage_pct"], 0.03)       # kept from the defaults
            self.assertEqual(m["intraday"]["brokerage_cap"], 10)
            c = check.costs(4, 1452.30, 1432.30, "MIS", m)
            self.assertEqual(c["factor"], 1.5)
            self.assertEqual(c["total"], round(c["computed"] * 1.5, 2))
            self.assertEqual(c["label"], "estimate (calibrated from 3 contract notes)")
            res = check.check([rel()], cfg(), now=at(NOW), charges=m)
            self.assertIn("charges (estimate (calibrated from 3 contract notes))", res["results"][0]["info"][1])


class TheReel(unittest.TestCase):
    def test_the_reliance_bracket_passes_every_check(self):
        res = check.check([rel()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["product"], r["entry_ref"], r["stop"], r["target"]),
                         ("entry", "MIS", 1452.30, 1432.30, 1472.30))
        self.assertEqual((r["risk_per_share"], r["charges"], r["max_loss"], r["reward_risk"]), (20.0, 6.12, 86.12, 1.0))
        self.assertEqual(r["value_inr"], 5809.2)
        self.assertEqual(res["cash_after"], 4190.8)
        self.assertEqual(res["date"], TODAY)
        self.assertIs(res["paper"], True)

    def test_five_shares_is_blocked_by_the_max_loss_only(self):
        res = check.check([rel(qty="5")], cfg(), now=at(NOW))
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 5 shares x 20.00 risk a share + ₹7.65 charges (estimate) = ₹107.65 at the stop, "
                          "over your ₹100 max loss per trade"])

    def test_fingerprint(self):
        o = rel()
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty="5")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, product="CNC")), check.fingerprint(o))
        self.assertIn("product", check.ORDER_FIELDS)
        self.assertNotIn("notional", check.ORDER_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", REL_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 4 shares MIS, entry", text)
            self.assertIn("entry 1452.30 (the plan's entry), stop 1432.30, target 1472.30: risk 20.00 a share", text)
            self.assertIn("max loss ₹86.12 (limit ₹100) incl. ~₹6.12 charges (estimate), reward:risk 1.00 (min 1)",
                          text)
            self.assertIn("Charges are an estimate (Zerodha rates verified 2026-10-29)", text)
            self.assertIn("ticket.py reconcile", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 09:45 IST", text)
            self.assertIn("₹4,190.80", text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(rel()))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = rel()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 1452.30}
        self.assertIn("stop: no stop", reasons(self.one(o)))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = rel()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))

    def test_stop_target_and_reward_risk(self):
        self.assertIn("must be below the entry 1452.30", reasons(self.one(rel(stop_loss_stop_price="1455.00"))))
        self.assertIn("must be above the entry", reasons(self.one(rel(take_profit_limit_price="1450.00"))))
        self.assertIn("reward:risk: 0.50", reasons(self.one(rel(take_profit_limit_price="1462.30"))))

    def test_a_fresh_higher_price_is_the_entry(self):
        res = check.check([rel()], cfg(), prices={"RELIANCE": 1458.0}, now=at("2026-10-19 09:55"),
                          price_times={"RELIANCE": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (1458.0, "price", 25.7))
        self.assertIn("4 shares x 25.70 risk a share + ₹6.13 charges (estimate) = ₹108.93", reasons(res))
        res = check.check([rel()], cfg(), prices={"RELIANCE": 1458.0}, now=at("2026-10-19 10:30"),
                          price_times={"RELIANCE": QUOTE_TIME})
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("older than 15 minutes", warnings(res))

    def test_a_limit_order_uses_its_limit_price(self):
        res = check.check([rel(type="limit", limit_price="1452.00")], cfg(), prices={"RELIANCE": 1458.0}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (1452.0, 84.92, "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        res = check.check([rel()], cfg(), state={"orders": [placed("TCS"), placed("INFY")]}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, ₹172.22 of ₹300 daily loss", res["results"][0]["info"][-1])
        three = {"orders": [placed("TCS"), placed("INFY"), placed("BEL")]}
        why = reasons(check.check([rel()], cfg(), state=three, now=at(NOW)))
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹258.33 already at risk today plus ₹86.12 for this one = ₹344.45, over your ₹300",
                      why)
        yesterday = {"orders": [placed(s, day="2026-10-16") for s in ("TCS", "INFY", "BEL")]}
        self.assertEqual(verdicts(check.check([rel()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [bracket(s, 1, 600.0, 580.0, 640.0) for s in ("TCS", "INFY", "BEL", "HDFCBANK")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", reasons(res, 3))

    def test_entry_window_ist(self):
        cases = [("2026-10-19 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-19 15:01", "no new entries after 15:00 IST (it is 15:01)"),
                 ("2026-10-19 09:10", "the market is closed (Mon 2026-10-19 09:10"),
                 ("2026-10-19 15:35", "the market is closed"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-10-20 10:15", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-10-19T03:55:00Z", "no new entries before 09:30 IST (it is 09:25)")]
        for now, want in cases:
            self.assertIn("window: " + want, reasons(self.one(rel(), now=now)), now)
        for now in ("2026-10-19 09:30", "2026-10-19 15:00", "2026-10-19T04:15:00Z"):
            self.assertEqual(verdicts(self.one(rel(), now=now)), ["PASS"], now)
        res = self.one(rel(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(rel(), no_entries_before="9.30am")

    def test_holidays_outside_2026_warn(self):
        res = self.one(rel(), now="2027-01-04 10:00")
        self.assertIn("holiday list covers 2024, 2025, 2026 only: check nseindia.com", " ".join(res["warnings"]))

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(at("2026-10-19 09:45"), datetime.datetime(2026, 10, 19, 4, 15))
        self.assertEqual(at("2026-03-09 09:45"), datetime.datetime(2026, 3, 9, 4, 15))
        self.assertEqual(check.to_ist(datetime.datetime(2026, 12, 1, 4, 15)), datetime.datetime(2026, 12, 1, 9, 45))

    def test_no_mis_entries_after_the_cutoff(self):
        res = self.one(rel(), now="2026-10-19 15:05", no_entries_after="15:20")
        self.assertIn("mis cutoff: no new MIS (intraday) entries after 15:00 IST", reasons(res))
        res = self.one(rel(product="CNC"), now="2026-10-19 15:05", no_entries_after="15:20")
        self.assertNotIn("mis cutoff", reasons(res))

    def test_product(self):
        self.assertIn("product must be MIS", reasons(self.one(rel(product="NRML"))))
        o = rel()
        o.pop("product")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["results"][0]["product"], "MIS")
        self.assertIn("treated as MIS", warnings(res))

    def test_mis_leverage_is_off_by_default(self):
        big = bracket("RELIANCE", 4, 1452.30, 1432.30, 1472.30)
        pf = {"cash": 3000, "positions": []}
        c = dict(cfg(), max_position_pct=500, cash_buffer_pct=0)
        res = check.check([big], c, portfolio=pf, now=at(NOW))
        self.assertIn("MIS leverage", reasons(res))
        res = check.check([big], dict(c, allow_mis_leverage=True), portfolio=pf, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        self.assertIn("uses MIS leverage", warnings(res))

    def test_a_short_entry_is_mis_only(self):
        short = {"symbol": "RELIANCE", "side": "sell", "qty": "4", "product": "MIS", "type": "market",
                 "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "1432.30",
                 "stop_loss_stop_price": "1472.30", "plan": {"entry": 1452.30}}
        self.assertIn("short: selling more RELIANCE than you hold", reasons(self.one(short)))
        r = self.one(short, allow_short=True)["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["reward_risk"]), ("PASS", "entry", 1.0))
        self.assertIn("no overnight short with CNC", reasons(self.one(dict(short, product="CNC"), allow_short=True)))

    def test_whole_shares_and_no_notional(self):
        self.assertIn("whole shares only", reasons(self.one(rel(qty="4.5"))))
        self.assertIn("not a rupee notional", reasons(self.one(dict(rel(qty=None), notional="5000"))))
        self.assertIn("time_in_force day", reasons(self.one(rel(time_in_force="ioc"))))
        o = rel()
        o.pop("take_profit_limit_price")
        self.assertIn("needs both take_profit_limit_price and stop_loss_stop_price", reasons(self.one(o)))
        self.assertIn("as a string", warnings(self.one(rel(stop_loss_stop_price=1432.30))))
        self.assertIn("no extended-hours", reasons(self.one(rel(extended_hours=True))))

    def test_wide_stop_warns_about_the_circuit(self):
        o = bracket("BEL", 1, 600.0, 560.0, 700.0)
        self.assertIn("circuit", warnings(check.check([o], cfg(), now=at(NOW))))
        self.assertIn("band unknown: run trader-data bands", warnings(check.check([o], cfg(), now=at(NOW))))


def bands(**over):
    with open(BANDS_PATH) as fh:
        doc = json.load(fh)
    doc.update(over)
    return doc


CAL_2027 = {"fetched_at": "2026-10-29T10:00:00Z", "years": [2027],
            "holidays": {"2027-01-26": "Republic Day", "2027-03-22": "Holi"},
            "special_sessions": {"2027-02-06": "Special Saturday session",
                                 "2027-10-29": "Diwali Laxmi Pujan (Muhurat)"},
            "last_circular_check": "2026-10-29", "warnings": []}


class CalendarFile(unittest.TestCase):
    """trader/data/calendar.json (trader-data: data.py calendar) extends the holiday list past 2026."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)
        os.makedirs(os.path.join("trader", "data"))

    def fresh(self, content):
        """A newly loaded check.py that reads trader/data/calendar.json from the current folder."""
        with open(os.path.join("trader", "data", "calendar.json"), "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return load("trader-risk", "check")

    def one(self, mod, now):
        return mod.check([rel()], cfg(), now=mod.parse_now(now))

    def test_2027_holiday_closed_and_weekday_open(self):
        c = self.fresh(CAL_2027)
        res = self.one(c, "2027-01-26 10:15")
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("window: the market is closed today (NSE holiday: Republic Day", reasons(res))
        self.assertNotIn("covers", " ".join(res["warnings"]))
        res = self.one(c, "2027-01-27 10:15")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("covers", " ".join(res["warnings"]))
        self.assertIn(2027, c.CALENDAR_YEARS)
        self.assertNotIn("2027-01-26", check.NSE_HOLIDAYS)               # the module loaded at import is untouched

    def test_special_saturday_opens_and_muhurat_closes_the_regular_market(self):
        c = self.fresh(CAL_2027)
        self.assertEqual(verdicts(self.one(c, "2027-02-06 10:15")), ["PASS"])
        res = self.one(c, "2027-10-29 10:15")                            # a Friday
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("Muhurat", reasons(res))
        self.assertIn("no normal 09:15-15:30 market", reasons(res))

    def test_builtin_2026_still_holds_and_other_years_still_warn(self):
        c = self.fresh(CAL_2027)
        self.assertIn("NSE holiday: Dussehra", reasons(self.one(c, "2026-10-20 10:15")))
        self.assertIn("holiday list covers 2024, 2025, 2026, 2027 only: check nseindia.com",
                      " ".join(self.one(c, "2028-01-03 10:15")["warnings"]))

    def test_missing_or_corrupt_file_falls_back_to_the_builtin_table(self):
        self.assertEqual(load("trader-risk", "check").CALENDAR_YEARS, (2024, 2025, 2026))   # no file at all
        for bad in ("{not json", "[1, 2]", "", '{"years": "x", "holidays": []}'):
            c = self.fresh(bad)
            self.assertEqual(c.CALENDAR_YEARS, (2024, 2025, 2026), bad)
            res = self.one(c, "2027-01-26 10:15")
            self.assertEqual(verdicts(res), ["PASS"], bad)
            self.assertIn("holiday list covers 2024, 2025, 2026 only: check nseindia.com", " ".join(res["warnings"]), bad)
            self.assertIn("NSE holiday: Dussehra", reasons(self.one(c, "2026-10-20 10:15")), bad)


class CircuitBands(unittest.TestCase):
    def one(self, order, doc=None, **over):
        return check.check([order], cfg(**over), now=at(NOW), bands=bands() if doc is None else doc)

    def test_the_band_is_shown(self):
        res = self.one(rel())
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual((r["band"]["lower"], r["band"]["upper"], r["band_state"]), (1306.8, 1597.2, "known"))
        self.assertIn("circuit: 1306.80 to 1597.20 (10% band, as of 2026-10-19", " ".join(r["info"]))
        self.assertEqual(res["bands_as_of"], "2026-10-19")

    def test_a_limit_price_outside_the_band_is_blocked(self):
        why = reasons(self.one(rel(type="limit", limit_price="1600.00", take_profit_limit_price="1640.00",
                                   stop_loss_stop_price="1580.00")))
        self.assertIn("circuit: limit price outside today's circuit: 1600.00 is not within 1306.80 to 1597.20", why)
        self.assertIn("target limit price outside today's circuit", why)
        sell = {"symbol": "BEL", "side": "sell", "qty": "1", "product": "MIS", "type": "stop", "stop_price": "380",
                "time_in_force": "day"}
        pf = {"cash": 1000, "positions": [{"ticker": "BEL", "shares": 1, "lots": [{"shares": 1, "cost": 410}]}]}
        res = check.check([sell], cfg(), portfolio=pf, now=at(NOW), bands=bands())
        self.assertEqual(res["results"][0]["kind"], "exit")
        self.assertIn("circuit: stop price outside today's circuit", reasons(res))

    def test_an_mis_bracket_stop_outside_the_band_is_blocked(self):
        o = bracket("BEL", 1, 410.0, 385.0, 460.0)
        why = reasons(self.one(o))
        self.assertIn("circuit: bracket stop-loss outside today's circuit: 385.00", why)
        self.assertIn("target limit price outside today's circuit: 460.00", why)

    def test_a_cnc_stop_past_the_lower_circuit_warns(self):
        o = bracket("BEL", 2, 410.0, 385.0, 460.0, product="CNC")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        self.assertIn("the stop 385.00 is below the lower circuit 389.50: the stop may not fill if the stock hits "
                      "lower circuit", warnings(res))
        self.assertNotIn("band unknown", warnings(res))

    def test_no_band_stock_and_unknown_and_stale(self):
        o = bracket("HINDCOPPER", 1, 314.30, 290.0, 360.0)
        r = self.one(o)["results"][0]
        self.assertEqual(r["band_state"], "no band")
        self.assertIn("no price band", " ".join(r["info"]))
        self.assertNotIn("band unknown", " ".join(r["warnings"]))
        o = bracket("TCS", 1, 600.0, 560.0, 700.0)
        self.assertIn("band unknown: run trader-data bands", warnings(self.one(o)))
        stale = bands(as_of="2026-10-01")
        o = bracket("BEL", 1, 410.0, 385.0, 460.0)
        res = self.one(o, stale)
        self.assertNotIn("circuit:", reasons(res))
        self.assertIn("band unknown: run trader-data bands", warnings(res))
        self.assertIn("stale (bands.json is as of 2026-10-01)", warnings(res))
        self.assertEqual(check.band_for(bands(as_of="2026-10-16"), "BEL", TODAY)[1], "known")   # most recent
        self.assertEqual(check.band_for(None, "BEL", TODAY), (None, "unknown"))

    def test_cli_reads_the_bands_file(self):
        with tempfile.TemporaryDirectory() as d:
            orders = os.path.join(d, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"orders": [rel(type="limit", limit_price="1600.00", take_profit_limit_price="1640.00",
                                          stop_loss_stop_price="1580.00")]}, fh)
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", orders, "--config", CFG_PATH, "--portfolio", none, "--prices", none,
                                   "--state", none, "--now", NOW, "--no-write", "--bands", BANDS_PATH])
            self.assertEqual(code, 1)
            self.assertIn("limit price outside today's circuit", buf.getvalue())


class Exits(unittest.TestCase):
    PF = {"cash": 4190.8, "positions": [{"ticker": "RELIANCE", "shares": 4,
                                          "lots": [{"date": TODAY, "shares": 4, "cost": 5809.2}]}]}
    SELL = {"symbol": "RELIANCE", "side": "sell", "qty": "4", "product": "MIS", "type": "market",
            "time_in_force": "day"}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("TCS"), placed("INFY")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:25"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, warnings(res))
        self.assertEqual(verdicts(check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-17 12:00"))),
                         ["PASS"])

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, qty="2")
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1460.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        self.assertIn("needs a limit_price",
                      reasons(check.check([dict(self.SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))))
        self.assertIn("short: selling more RELIANCE than you hold (4 shares)",
                      reasons(check.check([dict(self.SELL, qty="5")], cfg(), portfolio=self.PF, now=at(NOW))))
        self.assertIn("record the fill first", reasons(check.check([self.SELL], cfg(), now=at(NOW))))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([rel(), self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class SplitChecks(unittest.TestCase):
    """The long-term checks with the day-trade checks off."""

    def one(self, order, **over):
        return check.check([order], split(**over), prices=PRICES, today=SPLIT_TODAY)["results"][0]

    def test_the_etf_buys_pass_on_a_fresh_paper_account(self):
        res = check.check(etf_orders(), split(), prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5, [r["reasons"] for r in res["results"]])
        self.assertEqual(res["results"][0]["value_inr"], 2283.2)
        self.assertEqual(res["results"][0]["amount"], "8 shares")
        self.assertEqual(res["cash_after"], 1393.7)

    def test_one_more_buy_is_blocked(self):
        res = check.check(etf_orders() + [buy("NIFTYBEES", 4)], split(), prices=PRICES, today=SPLIT_TODAY)
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("duplicate:", why)

    def test_the_day_config_blocks_buys_without_a_stop(self):
        res = check.check(etf_orders(), cfg(), prices=PRICES, now=at(NOW))
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        self.assertIn("over your ₹5,000 max order", " ".join(self.one(buy("BANKBEES", 9), max_position_pct=100)["reasons"]))
        self.assertIn("under your ₹500 minimum", " ".join(self.one(buy("ITBEES", 2))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("BEL", 2), blocked=["BEL"])
        self.assertIn("blocked list", r["reasons"][0])

    def test_options_and_futures_are_rejected(self):
        for sym in ("NIFTY26OCT25000CE", "RELIANCE26OCTFUT", "BANKNIFTY2610349000PE", "BE270617C00100000"):
            r = self.one({"symbol": sym, "side": "buy", "qty": "1", "product": "MIS", "type": "limit",
                          "limit_price": "12.50", "time_in_force": "day"})
            self.assertEqual(r["verdict"], "BLOCK", sym)
            self.assertIn("F&O is out of scope", " ".join(r["reasons"]), sym)

    def test_nse_symbols(self):
        for sym in ("M&M", "BAJAJ-AUTO", "HINDCOPPER", "NIFTYBEES"):
            r = check.check([buy(sym, 2)], split(), prices={sym: 300.0}, today=SPLIT_TODAY)["results"][0]
            self.assertEqual(r["verdict"], "PASS", (sym, r["reasons"]))
        r = check.check([buy("RELIANCE.NS", 1)], split(max_position_pct=100), prices={"RELIANCE": 1452.3},
                        today=SPLIT_TODAY)["results"][0]
        self.assertEqual((r["symbol"], r["verdict"]), ("RELIANCE", "PASS"))
        for bad in ("BTC/INR", "reliance ltd", "-ABC"):
            self.assertIn("NSE symbol", " ".join(self.one(buy(bad, 1))["reasons"]), bad)

    def test_format_rules(self):
        self.assertIn("needs a limit_price", " ".join(self.one(dict(buy("BEL", 2), type="limit"))["reasons"]))
        self.assertIn("side must be buy or sell", " ".join(self.one(dict(buy("BEL", 2), side="hold"))["reasons"]))
        self.assertIn("no qty", " ".join(self.one({"symbol": "BEL", "side": "buy", "product": "CNC"})["reasons"]))

    def test_share_orders_need_a_price(self):
        self.assertIn("no price for ZZZZ", " ".join(self.one(buy("ZZZZ", 2))["reasons"]))

    PF = {"cash": 500.0, "positions": [{"ticker": "BEL", "shares": 5,
                                         "lots": [{"date": "2026-10-13", "shares": 5, "cost": 2050.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "BEL", "side": "sell", "qty": "10", "product": "CNC", "type": "market", "time_in_force": "day"}
        r = check.check([sell], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        r = check.check([dict(sell, qty="3")], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "BEL", "side": "sell", "qty": "3", "product": "CNC", "type": "market",
                   "time_in_force": "day"}, buy("GOLDBEES", 9)]
        res = check.check(orders, split(max_position_pct=100, cash_buffer_pct=0), portfolio=self.PF,
                          prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("GOLDBEES", 9)], split(max_position_pct=100, cash_buffer_pct=0), portfolio=self.PF,
                          prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": SPLIT_TODAY + "T10:00:00"}
                         for i in range(10)]}
        self.assertIn("daily limit", reasons(check.check([buy("BEL", 2)], split(), prices=PRICES, state=st,
                                                         today=SPLIT_TODAY)))
        bel = {"orders": [{"symbol": "BEL", "side": "buy", "status": "placed", "at": SPLIT_TODAY + "T10:00:00"}]}
        self.assertIn("already placed today", reasons(check.check([buy("BEL", 2)], split(), prices=PRICES, state=bel,
                                                                  today=SPLIT_TODAY)))

    def test_live_account_warns(self):
        res = check.check([buy("BEL", 2)], split(paper=False), prices=PRICES, today=SPLIT_TODAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-19T04:24:00Z", "prices": {"RELIANCE": 1458.0},
                           "detail": {"RELIANCE": {"time": "2026-10-19T04:22:00Z"}}}, fh)
            none = os.path.join(d, "none")
            base = ["--orders", REL_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 09:55"]), 1)
            self.assertIn("= ₹108.93 at the stop", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:30"]), 0)

    def test_printed_paths_use_forward_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "trader", "orders.json")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(check.main(["--orders", missing, "--no-write"]), 1)
            self.assertIn(missing.replace(os.sep, "/"), err.getvalue())
            self.assertNotIn("\\", err.getvalue())
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(check.main(["--orders", REL_PATH, "--config", bad, "--no-write"]), 1)
            self.assertNotIn("\\", err.getvalue())
            out = os.path.join(d, "state", "risk.json")
            with redirect_stdout(io.StringIO()):
                check.main(["--orders", REL_PATH, "--config", CFG_PATH, "--portfolio", missing, "--prices", missing,
                            "--state", missing, "--now", "2026-10-19 10:30", "--out", out])
            with open(out) as fh:
                self.assertNotIn("\\", json.load(fh)["orders_file"])

    def test_errors_are_one_line(self):
        for argv, want in ((["--orders", "/nonexistent.json"], "no orders file"),
                           (["--orders", REL_PATH, "--now", "noon"], "--now wants"),
                           (["--orders", REL_PATH, "--today", "monday"], "--today wants")):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(check.main(argv + ["--no-write"]), 1)
            self.assertIn(want, err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
