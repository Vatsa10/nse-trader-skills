import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-hindcopper.json")
DT = datetime.datetime
UA = alerts.UA

NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
            "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
            "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_holidays_match_the_spec(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.NSE_EARLY_CLOSE, {})
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(alerts.to_ist(DT(2026, 1, 15, 3, 45)), DT(2026, 1, 15, 9, 15))
        self.assertEqual(alerts.to_ist(DT(2026, 7, 15, 3, 45)), DT(2026, 7, 15, 9, 15))

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-13T09:15"), DT(2026, 10, 13, 3, 45))
        self.assertEqual(ist("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ist("2026-10-13T10:00:01Z"), DT(2026, 10, 13, 10, 0, 1))
        self.assertEqual(ist("2026-10-13T09:15:00+05:30"), DT(2026, 10, 13, 3, 45))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:30"))
        pre = alerts.market_status(ist("2026-10-13T09:05"))
        self.assertFalse(pre["open"])
        self.assertIn("pre-open", pre["reason"])
        self.assertEqual(pre["next_open"], "2026-10-13 09:15")
        self.assertTrue(alerts.market_status(ist("2026-10-13T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:29"))["open"])
        after = alerts.market_status(ist("2026-10-13T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-10-14 09:15")
        dussehra = alerts.market_status(ist("2026-10-20T11:00"))
        self.assertFalse(dussehra["open"])
        self.assertEqual(dussehra["reason"], "NSE holiday: Dussehra")
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-02T10:00"))["next_open"], "2026-10-05 09:15")
        self.assertFalse(alerts.market_status(ist("2026-11-08T18:30"))["open"])      # Muhurat: closed by default
        self.assertEqual(alerts.market_status(ist("2026-10-17T10:00"))["reason"], "weekend")
        self.assertFalse(alerts.market_status(ist("2027-03-01T10:00"))["calendar_known"])


CAL27 = os.path.join(QDIR, "calendar-2027.json")


class CalendarFile(unittest.TestCase):
    """trader/data/calendar.json (written by trader-data) merged over the built-in 2026 table."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.none = os.path.join(self.tmp, "none.json")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(alerts.apply_calendar, self.none)        # back to the built-in tables

    def test_the_file_extends_the_years(self):
        D = datetime.date
        self.assertIsNotNone(alerts.apply_calendar(CAL27))
        self.assertEqual(alerts.trading_day(D(2027, 1, 26)), (False, "NSE holiday: Republic Day"))
        self.assertEqual(alerts.trading_day(D(2027, 3, 22)), (False, "NSE holiday: Holi"))
        self.assertEqual(alerts.trading_day(D(2027, 4, 8))[0], False)           # closure from a circular
        self.assertEqual(alerts.trading_day(D(2027, 3, 8)), (True, ""))
        self.assertEqual(alerts.trading_day(D(2027, 2, 13)), (False, "weekend"))
        self.assertEqual(alerts.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        # 2026 is listed in the file, so the file replaces the built-in 2026 table for that year
        self.assertEqual(alerts.trading_day(D(2026, 1, 26)), (True, ""))
        self.assertEqual(alerts.CALENDAR_YEARS, (2026, 2027))
        st = alerts.market_status(ist("2027-03-08T10:00"))
        self.assertTrue(st["open"])
        self.assertTrue(st["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2028-03-08T10:00"))["calendar_known"])
        self.assertEqual(alerts.market_status(ist("2027-01-25T16:00"))["next_open"], "2027-01-27 09:15")

    def test_clock_text(self):
        alerts.apply_calendar(CAL27)
        code, text, _ = run(["clock", "--now", "2027-01-26 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Republic Day", text)
        self.assertNotIn("holiday list covers", text)
        code, text, _ = run(["clock", "--now", "2028-01-04T10:00"])
        self.assertIn("holiday list covers 2026, 2027 only: check nseindia.com", text)
        self.assertIn("data.py calendar", text)

    def test_normal_hours_special_session(self):
        alerts.apply_calendar(CAL27)
        st = alerts.market_status(ist("2027-03-06T10:00"))                      # a Saturday
        self.assertTrue(st["open"])
        self.assertIn("Live Trading Session", st["special_session"])
        self.assertFalse(st["muhurat"])
        code, text, _ = run(["clock", "--now", "2027-03-06 10:00"])
        self.assertEqual(code, 0)
        self.assertIn("Special session today: Circular: Live Trading Session", text)

    def test_muhurat_is_a_special_session_not_a_plain_holiday(self):
        D = datetime.date
        alerts.apply_calendar(CAL27)
        ok, why = alerts.trading_day(D(2027, 10, 29))                           # a Friday
        self.assertFalse(ok)
        self.assertTrue(why.startswith("special session: Diwali Laxmi Pujan (Muhurat)"), why)
        self.assertNotIn("holiday", why)
        st = alerts.market_status(ist("2027-10-29T18:30"))
        self.assertFalse(st["open"])                                            # the regular market is closed
        self.assertTrue(st["muhurat"])
        self.assertEqual(st["special_session"], "Diwali Laxmi Pujan (Muhurat)")
        self.assertEqual(st["next_open"], "2027-11-01 09:15")
        code, text, _ = run(["clock", "--now", "2027-10-29 18:30"])
        self.assertEqual(code, 1)
        self.assertIn("regular market closed, special session: Diwali Laxmi Pujan (Muhurat)", text)
        # the built-in Muhurat (Sunday 8 Nov 2026) says the same without any file
        alerts.apply_calendar(self.none)
        self.assertTrue(alerts.trading_day(D(2026, 11, 8))[1].startswith("special session: "))
        self.assertTrue(alerts.market_status(ist("2026-11-08T18:30"))["muhurat"])

    def test_a_muhurat_on_a_sunday_in_the_file(self):
        path = os.path.join(self.tmp, "cal.json")
        with open(path, "w") as fh:
            json.dump({"years": [2027], "holidays": {}, "special_sessions": {"2027-10-31": "Diwali Laxmi Pujan (Muhurat)"}},
                      fh)
        alerts.apply_calendar(path)
        self.assertIn("2027-10-31", alerts.MUHURAT_SESSIONS)
        self.assertNotIn("2027-10-31", alerts.SPECIAL_SESSIONS)
        self.assertFalse(alerts.market_status(ist("2027-10-31T18:30"))["open"])

    def test_missing_or_corrupt_file_is_the_builtin_table(self):
        self.assertIsNone(alerts.apply_calendar(self.none))
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        for text in ("", "{nope", "[]", '{"years": ["x"]}', '{"years": [2027], "holidays": ["a"]}', "null"):
            path = os.path.join(self.tmp, "bad.json")
            with open(path, "w") as fh:
                fh.write(text)
            self.assertIsNone(alerts.apply_calendar(path), text)
            self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026, text)
            self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
            self.assertEqual(alerts.trading_day(datetime.date(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(alerts.NSE_HOLIDAYS, alerts.BUILTIN_HOLIDAYS)

    def test_applying_twice_does_not_pile_up(self):
        alerts.apply_calendar(CAL27)
        once = (dict(alerts.NSE_HOLIDAYS), dict(alerts.SPECIAL_SESSIONS), dict(alerts.MUHURAT_SESSIONS))
        alerts.apply_calendar(CAL27)
        self.assertEqual((alerts.NSE_HOLIDAYS, alerts.SPECIAL_SESSIONS, alerts.MUHURAT_SESSIONS), once)
        alerts.apply_calendar(self.none)
        self.assertNotIn("2027-01-26", alerts.NSE_HOLIDAYS)
        self.assertNotIn("2027-03-06", alerts.SPECIAL_SESSIONS)

    def test_the_default_path_is_trader_data(self):
        self.assertEqual(alerts.CALENDAR_PATH, os.path.join("trader", "data", "calendar.json"))
        old = alerts.CALENDAR_PATH
        alerts.CALENDAR_PATH = CAL27
        try:
            self.assertIsNotNone(alerts.apply_calendar())
            self.assertIn("2027-01-26", alerts.NSE_HOLIDAYS)
        finally:
            alerts.CALENDAR_PATH = old


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], q["currency"]), (1452.30, 1448.10, "INR"))
        self.assertEqual(q["time"], "2026-10-13T10:00:00Z")      # 15:30 IST
        with open(os.path.join(QDIR, "yahoo-HINDCOPPER.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "HINDCOPPER")
        self.assertEqual((q["price"], q["prev_close"], q["open"]), (324.85, 314.45, 314.90))
        self.assertEqual(q["time"], "2026-10-19T05:22:00Z")      # 10:52 IST

    def test_symbols_urls_and_money(self):
        self.assertEqual(alerts.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(alerts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(alerts.yahoo_symbol("NIFTY"), "%5ENSEI")
        self.assertEqual(alerts.yahoo_symbol("BANKNIFTY"), "%5ENSEBANK")
        self.assertEqual(alerts.yahoo_url("HINDCOPPER"),
                         "https://query1.finance.yahoo.com/v8/finance/chart/HINDCOPPER.NS?range=1d&interval=1d")
        self.assertIn("//query2.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))
        with self.assertRaises(alerts.AlertError):
            alerts.norm_symbol("not a symbol")
        self.assertEqual(alerts.inr(1234567.891), "₹12,34,567.89")
        self.assertEqual(alerts.inr(308.1), "₹308.10")

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["RELIANCE", "TCS", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["RELIANCE", "TCS"])
        self.assertEqual(q["TCS"]["price"], 3088.40)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-HINDCOPPER.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-HINDCOPPER.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["HINDCOPPER"], "yahoo", d)
            self.assertEqual((q["HINDCOPPER"]["price"], errors), (324.85, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-HINDCOPPER.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query1_first_with_a_browser_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["HINDCOPPER"])
        self.assertEqual((q["HINDCOPPER"]["price"], errors), (324.85, []))
        self.assertEqual(f.calls, [("query1.finance.yahoo.com", UA)])
        self.assertIn("Mozilla/5.0", UA)

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["HINDCOPPER"])[0]["HINDCOPPER"]["price"], 324.85)
        self.assertEqual(self.hosts(f), ["query1", "query1"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query2_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("HINDCOPPER", alerts.get_quotes(["HINDCOPPER"])[0])
        self.assertEqual(self.hosts(f), ["query1", "query1", "query2"])
        f = self.net([503, 200])
        self.assertIn("HINDCOPPER", alerts.get_quotes(["HINDCOPPER"])[0])
        self.assertEqual(self.hosts(f), ["query1", "query2"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["HINDCOPPER"])
        self.assertEqual(q, {})
        self.assertIn("query2.finance.yahoo.com/v8/finance/chart/HINDCOPPER.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query1"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["HINDCOPPER"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query1"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["HINDCOPPER", "RELIANCE"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class V4Cache(unittest.TestCase):
    """v4: Yahoo failing (or --cache) reads the trader-data cache."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.saved = alerts.fetch_yahoo

        def boom(sym, timeout=20):
            raise alerts.AlertError("could not reach query2.finance.yahoo.com: Forbidden")
        alerts.fetch_yahoo = boom

    def tearDown(self):
        alerts.fetch_yahoo = self.saved
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_no_cache_names_the_way_out(self):
        q, errors = alerts.get_quotes(["HINDCOPPER"], pause=0)
        self.assertEqual(q, {})
        for must in ("Forbidden", "No cache for HINDCOPPER", "data.py doctor", "get_historical_data", "import-kite"):
            self.assertIn(must, errors[0])

    def test_intraday_cache_then_daily_csv(self):
        os.makedirs(os.path.join("trader", "data", "intraday"))
        os.makedirs(os.path.join("trader", "data", "daily"))
        shutil.copy(os.path.join(QDIR, "yahoo-HINDCOPPER.json"),
                    os.path.join("trader", "data", "intraday", "HINDCOPPER-5m.json"))
        with open(os.path.join("trader", "data", "daily", "RELIANCE.csv"), "w") as fh:
            fh.write("date,open,high,low,close,volume\n2026-10-27,1450,1462,1441,1455.5,100\n"
                     "2026-10-28,1456,1470,1450,1468.25,120\n")
        q, errors = alerts.get_quotes(["HINDCOPPER", "RELIANCE", "TCS"], pause=0)
        self.assertEqual(q["HINDCOPPER"]["source"], "cache")
        self.assertIn("using cached data from 2026-10-", q["HINDCOPPER"]["note"])
        self.assertIn("trader-data/data.py intraday HINDCOPPER", q["HINDCOPPER"]["note"])
        self.assertEqual((q["RELIANCE"]["price"], q["RELIANCE"]["prev_close"]), (1468.25, 1455.5))
        self.assertIn("2026-10-28 daily close", q["RELIANCE"]["note"])
        self.assertEqual(len(errors), 1)
        self.assertIn("No cache for TCS", errors[0])
        code, text, _ = run(["quote", "RELIANCE", "--cache", "--now", "2026-10-29 10:00"])
        self.assertEqual(code, 0)
        self.assertIn("note: using cached data from the 2026-10-28 daily close", text)


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["RELIANCE", "BEL", "TCS", "HINDCOPPER"], "yahoo", QDIR)
        self.open = alerts.market_status(ist("2026-10-13T10:00"))
        self.now = ist("2026-10-13T10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "rel-1450", "symbol": "RELIANCE", "type": "above", "price": 1450}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("RELIANCE is ₹1,452.30: at or above your alert level ₹1,450.00", rings[0]["message"])
        self.assertEqual(alerts.evaluate(rules, self.q, self.open, st, self.now)[0], [])

    def test_stop_and_rearm(self):
        rules = [{"id": "bel-stop", "symbol": "BEL", "type": "stop", "price": 402, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop ₹402.00", rings[0]["message"])
        self.q["BEL"]["price"] = 403.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("bel-stop", st["rung"])
        self.q["BEL"]["price"] = 401.5
        self.assertEqual(len(alerts.evaluate(rules, self.q, self.open, st, self.now)[0]), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "bel-3", "symbol": "BEL", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "bel-1", "symbol": "BEL", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-1.15%", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "bel-up", "symbol": "BEL", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        hc = [{"id": "hc-3", "symbol": "HINDCOPPER", "type": "pct_move", "pct": 3}]
        rings, _ = self.at("2026-10-19 10:52", hc)
        self.assertIn("HINDCOPPER moved +3.31% to ₹324.85 (from ₹314.45)", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-13T09:10"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-13T09:10"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertIn("Opening bell: NSE is open (2026-10-13 10:00 IST)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-13T11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-14T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-14T09:16"))[0]), 1)

    def test_the_plan_gives_a_stop_a_target_and_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("hindcopper-plan-stop", "stop"), ("hindcopper-plan-target", "target"),
                          ("hindcopper-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (308.10, 320.50))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:15", "flat by 15:15"))
        self.assertTrue(all(r["date"] == "2026-10-19" for r in rules))
        self.assertEqual(alerts.rules_from_plan({"symbol": "BEL", "stop": 1, "target": 2})[2]["at"], "15:15")
        q = json.loads(json.dumps(self.q))
        q["HINDCOPPER"]["price"] = 315.00
        rings, st = self.at("2026-10-19 10:52", rules, None, q)
        self.assertEqual(rings, [])
        q["HINDCOPPER"]["price"] = 307.90
        rings, st = self.at("2026-10-19 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["hindcopper-plan-stop"])
        self.assertIn("HINDCOPPER is ₹307.90: at or below your stop ₹308.10 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["HINDCOPPER"]["price"] = 320.50
        self.assertEqual([r["id"] for r in self.at("2026-10-19 12:00", rules, st, q)[0]], ["hindcopper-plan-target"])

    def test_a_cnc_plan_has_no_flat_by(self):
        with open(PLAN) as fh:
            plan = json.load(fh)
        plan.update({"product": "CNC", "flat_by": None})
        rules = alerts.rules_from_plan(plan)
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("hindcopper-plan-stop", "stop"), ("hindcopper-plan-target", "target")])
        self.assertEqual(len(alerts.rules_from_plan(dict(plan, product="cnc"))), 2)
        self.assertEqual(len(alerts.rules_from_plan(dict(plan, product="CNC"), "15:00")), 2)   # even with a config time
        self.assertEqual(len(alerts.rules_from_plan(dict(plan, product="MIS", flat_by=None))), 3)
        self.assertEqual(len(alerts.rules_from_plan({"symbol": "BEL", "stop": 1, "target": 2})), 3)

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-19 15:14", rules)[0], [])
        rings, st = self.at("2026-10-19 15:15", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by 15:15: it is 15:15 IST (set for 15:15), the market closes at 15:30", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol HINDCOPPER", rings[0]["message"])
        self.assertIn("15:20", rings[0]["message"])
        self.assertEqual(self.at("2026-10-19 15:20", rules, st)[0], [])
        rings, _ = self.at("2026-10-19 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-21 15:16", rules, st)[0], [])     # plan rules: their day only

    def test_time_alerts_skip_holidays_and_weekends(self):
        rule = [{"id": "flat", "type": "time", "at": "15:15", "label": "flat by 15:15", "before_close": True}]
        self.assertEqual(self.at("2026-10-17 15:16", rule)[0], [])         # Saturday
        self.assertEqual(self.at("2026-10-20 15:16", rule)[0], [])         # Dussehra
        rings, st = self.at("2026-12-01 15:15", rule)
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:45:00Z")

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("hindcopper-")], plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "TCS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "HINDCOPPER", "stop": 308.10})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added hindcopper-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        self.assertEqual(self.check("2026-10-19 10:52")[0], 0)          # the opening bell rings first
        code, text, _ = self.check("2026-10-19 10:53")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, HINDCOPPER ₹324.85.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"HINDCOPPER": 324.85})
        self.assertEqual(doc["detail"]["HINDCOPPER"]["fetched"], "2026-10-19T05:23:00Z")

        code, text, _ = self.check("2026-10-19 15:15")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by 15:15", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-19 15:17")[0], 1)
        code, text, _ = self.check("2026-10-21 10:00")                  # 10-20 is Dussehra
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-21 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["hindcopper-flat-by", "hindcopper-plan-stop", "hindcopper-plan-target",
                                       "opening-bell"])
        with open(self.state) as fh:
            self.assertNotIn("hindcopper-flat-by", json.load(fh)["rung"])

    def test_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "RELIANCE", "--above", "1500"], ["--symbol", "BEL", "--stop", "390"]):
            self.assertEqual(run(["add", "--rules", self.rules] + args)[0], 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--above", "1500"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-13T16:00")
        self.assertEqual(code, 1)
        self.assertIn("NSE closed", text)
        with open(self.prices) as fh:
            self.assertEqual(json.load(fh)["prices"], {"RELIANCE": 1452.30, "BEL": 401.25})

        code, text, _ = self.check("2026-10-14T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        self.assertEqual(self.check("2026-10-14T09:45")[0], 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "15:15", "--label", "flat by 15:15",
                              "--symbol", "hindcopper.ns"])[0], 0)
        self.assertEqual(run(["add", "--rules", self.rules, "--symbol", "HINDCOPPER", "--target", "320.50",
                              "--date", "2026-10-19"])[0], 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1515", "type": "time", "at": "15:15", "label": "flat by 15:15",
                                    "symbol": "HINDCOPPER"})
        self.assertEqual(rules[1], {"id": "hindcopper-target-320.50", "symbol": "HINDCOPPER", "type": "target",
                                    "price": 320.5, "date": "2026-10-19"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "TCS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-13T10:00:01Z"])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE   ₹1,452.30", text)
        self.assertIn("delayed", text)
        run(["quote", "BEL", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["BEL", "RELIANCE", "TCS"])
        self.assertEqual(doc["detail"]["TCS"]["prev_close"], 3102.75)
        self.assertEqual(doc["detail"]["TCS"]["fetched"], "2026-10-13T10:00:01Z")

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "TCS", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["HINDCOPPER", "TCS"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Christmas", text)
        code, text, _ = run(["clock", "--now", "2027-01-04T10:00"])
        self.assertIn("holiday list covers 2026 only: check nseindia.com", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
