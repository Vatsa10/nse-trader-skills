"""trader-entry tests. Fixtures in tests/fixtures/trader-entry/:

RELIANCE-2026-10-19-5m.json  RELIANCE's Oct 19 2026 five-minute bars, cut from the reel's
                         US chart re-timed to IST and renamed: SYNTHETIC.
                         It ends with the in-progress 10:30 bar and Yahoo's live tick
                         at 10:34:45. meta trimmed to one day, like a range=1d answer.
DEMO-1m-synthetic.json   SYNTHETIC one-minute bars (not market data): a wick above the
                         range high, a close AT the high, a null bar, a live tick.
"""

import copy
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `python3 -m unittest tests.test_x` works

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
RELIANCE = os.path.join(EDIR, "RELIANCE-2026-10-19-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1792381500       # 2026-10-19 09:15 IST
DT = datetime.datetime


def ny(text):
    return entry.parse_now(text)


def doc(path=RELIANCE):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ny(text), **kw)


def shift_to(d, day):
    """The same session moved to another date: 09:15 IST is always 03:45 UTC (no DST)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 362.18 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        q["close"][i] = min(q["close"][i], 362.00)
        q["high"][i] = min(q["high"][i], 362.10)
        q["open"][i] = min(q["open"][i], 362.00)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheReel(unittest.TestCase):
    def test_triggers_at_1015_on_the_1010_bar(self):
        r = at("2026-10-19 10:00")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 362.18)
        self.assertEqual(r["opening_range"]["low"], 354.47)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "10:00 AM")
        self.assertEqual(t["bar_start"], "09:55")
        self.assertEqual(t["bar_index"], 8)
        self.assertEqual(t["price"], 364.18)
        self.assertEqual(t["close"], 364.1759948730469)
        self.assertEqual(t["pct_over"], 0.55)
        self.assertEqual(r["level"], 362.18)
        self.assertEqual(r["prev_close"], 354.72)

    def test_at_1012_the_1010_bar_is_still_forming(self):
        r = at("2026-10-19 09:57")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:50")
        self.assertEqual(r["last"]["close"], 361.68)
        self.assertEqual(r["forming"]["bar_start"], "09:55")
        self.assertIn("₹0.50 under the range high ₹362.18", r["reason"])
        self.assertEqual(at("2026-10-19 09:59:59")["status"], "waiting")

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-19 09:05")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        r = at("2026-10-19 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-19 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart()
        self.assertEqual(c["bars"][-1]["t"], 1792386285)          # 10:34:45 is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))     # read as of the file's own time
        self.assertEqual(r["now"], "2026-10-19 10:34:45")
        self.assertEqual(r["_bars"][-1]["slot"], 14)               # the 10:25 bar
        self.assertEqual(r["forming"]["bar_start"], "10:30")
        self.assertEqual(r["bars_closed"], 15)
        # 10:35: closed by the clock, but Yahoo's last trade (10:34:45) is before its close
        r = at("2026-10-19 10:35")
        self.assertEqual(r["_bars"][-1]["slot"], 14)
        self.assertIn("Yahoo's last trade is 10:34:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:34:45 tick never becomes a bar
        r = at("2026-10-19 10:36")
        self.assertEqual(r["_bars"][-1]["slot"], 15)
        self.assertEqual(r["bars_closed"], 16)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-19 15:50")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:00 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-10-19 10:34", min_volume_x=1)
        self.assertEqual(r["opening_range"]["avg_volume"], 1658945)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:20 AM", 12, 365.75))
        self.assertEqual(t["volume_x"], 7.98)
        r = at("2026-10-19 10:01", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("0.60x the range's volume, under your 1.00x", r["reason"])

    def test_level_setup(self):
        r = at("2026-10-19 10:15", setup="level", level=365.5)
        self.assertEqual(r["trigger"]["time"], "10:10 AM")
        self.assertEqual(r["trigger"]["bar_index"], 10)
        self.assertEqual(r["trigger"]["price"], 365.75)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-10-19 10:15", setup="level", level=350)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:15", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at("2026-10-19 10:34", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at("2026-10-19 14:59", d)["status"], "waiting")
        r = at("2026-10-19 15:00", d)                             # default no_entries_after 15:00 IST
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 15:00", r["reason"])
        self.assertEqual(at("2026-10-19 16:15", d)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r0 = d["chart"]["result"][0]
        keep = [i for i, t in enumerate(r0["timestamp"]) if t <= OPEN_TS + 14 * 300]   # live: up to the 10:25 bar
        r0["timestamp"] = [r0["timestamp"][i] for i in keep]
        q = r0["indicators"]["quote"][0]
        for k in q:
            q[k] = [q[k][i] for i in keep]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 74 * 60 + 40                       # last trade 10:29:40
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:30"}
        r = at("2026-10-19 10:30:10", d, cfg=cfg)                           # the 10:25 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:29:40", r["lag_note"])
        self.assertEqual(at("2026-10-19 10:31", d, cfg=cfg)["status"], "expired")

    def test_config_cutoff(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:55"}
        r = at("2026-10-19 10:00", cfg=cfg)
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "10:05", "no_entries_after": "15:15"}
        r = at("2026-10-19 10:15", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")       # the 10:00 bar, first to close at or after 10:05

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:00", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:00", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-10-19 10:00"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-19T04:30:00Z"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-19 10:00 IST"), DT(2026, 10, 19, 4, 30))
        with self.assertRaises(entry.EntryError):
            entry.check_symbol("NIFTY26OCT25000CE")
        self.assertEqual(entry.check_symbol("hindcopper.ns"), "HINDCOPPER")

    def test_or_minutes_30(self):
        r = at("2026-10-19 10:34", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 362.92)
        self.assertEqual(r["trigger"]["time"], "10:00 AM")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-19 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ny("2026-10-19 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 09:51 still forming, 09:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_ist_has_no_dst(self):
        # the same session on dates either side of the US DST change: IST does not move
        for day in (datetime.date(2026, 12, 7), datetime.date(2026, 11, 2), datetime.date(2026, 10, 30),
                    datetime.date(2026, 3, 9)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ny("%s 10:00" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "10:00 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 8, day)
            self.assertEqual(entry.evaluate(chart(d), ny("%s 09:57" % day))["status"], "waiting", day)
        # 10:00 IST is 04:30 UTC, summer and winter
        self.assertEqual(ny("2026-12-07 10:00"), DT(2026, 12, 7, 4, 30))
        self.assertEqual(ny("2026-06-08 10:00"), DT(2026, 6, 8, 4, 30))

    def test_no_early_close_and_a_full_session(self):
        d = shift_to(no_breakout(), datetime.date(2026, 12, 24))   # a US half day; a full NSE session
        r = entry.evaluate(chart(d), ny("2026-12-24 14:59"))
        self.assertEqual(r["status"], "waiting")
        self.assertEqual((r["opens"], r["closes"], r["no_entries_after"], r["early_close"]),
                         ("09:15", "15:30", "15:00", None))
        r = entry.evaluate(chart(d), ny("2026-12-24 15:00"))
        self.assertEqual(r["status"], "expired")
        self.assertNotIn("early close", r["reason"])
        # a cutoff after the close is held at the close
        cfg = {"no_entries_before": "09:30", "no_entries_after": "15:45"}
        self.assertEqual(entry.evaluate(chart(d), ny("2026-12-24 10:45"), cfg=cfg)["no_entries_after"], "15:30")

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-17 10:15")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-10-19 09:15")
        r = at("2026-10-20 10:15")
        self.assertIn("NSE holiday: Dussehra", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        self.assertIn("Diwali", at("2026-11-10 10:15")["reason"])
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-10-19 10:00", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-21 10:00")                                # Yahoo sent an older session's bars only
        self.assertEqual(r["status"], "no-data")
        r = at("2027-03-01 10:15")
        self.assertFalse(r["calendar_known"])
        self.assertIn("check nseindia.com", "\n".join(entry.describe(r)))

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE")


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

    def fresh(self, content):
        """A newly loaded entry.py that reads trader/data/calendar.json from the current folder."""
        os.makedirs(os.path.join("trader", "data"), exist_ok=True)
        with open(os.path.join("trader", "data", "calendar.json"), "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return load("trader-entry", "entry")

    def run_on(self, mod, day, hhmm="10:00"):
        d = shift_to(doc(), datetime.date.fromisoformat(day))
        return mod.evaluate(mod.parse_chart(json.dumps(d), "RELIANCE"), mod.parse_now("%s %s" % (day, hhmm)))

    def test_2027_holiday_closed_and_weekday_open(self):
        e = self.fresh(CAL_2027)
        r = self.run_on(e, "2027-01-26")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("NSE holiday: Republic Day", r["reason"])
        self.assertEqual(r["next_open"], "2027-01-27 09:15")
        self.assertTrue(r["calendar_known"])
        r = self.run_on(e, "2027-01-27")
        self.assertEqual(r["status"], "triggered")
        self.assertTrue(r["calendar_known"])
        self.assertNotIn("covers", "\n".join(e.describe(r)))
        self.assertIn(2027, e.CALENDAR_YEARS)
        self.assertNotIn("2027-01-26", entry.NSE_HOLIDAYS)                  # the module loaded at import is untouched

    def test_special_saturday_opens_and_muhurat_closes_the_regular_market(self):
        e = self.fresh(CAL_2027)
        self.assertEqual(e.trading_day(datetime.date(2027, 2, 6)), (True, ""))
        self.assertEqual(self.run_on(e, "2027-02-06")["status"], "triggered")
        ok, why = e.trading_day(datetime.date(2027, 10, 29))                # a Friday
        self.assertFalse(ok)
        self.assertIn("Muhurat", why)
        self.assertIn("no normal 09:15-15:30 market", why)
        r = self.run_on(e, "2027-10-29")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2027-11-01 09:15")

    def test_builtin_2026_still_holds_and_other_years_still_warn(self):
        e = self.fresh(CAL_2027)
        self.assertIn("NSE holiday: Dussehra", e.evaluate(chart(), e.parse_now("2026-10-20 10:15"))["reason"])
        ok, why = e.trading_day(datetime.date(2026, 11, 8))                  # the 2026 Muhurat Sunday
        self.assertFalse(ok)
        r = self.run_on(e, "2028-01-03")
        self.assertFalse(r["calendar_known"])
        self.assertIn("covers 2024, 2025, 2026, 2027 only", "\n".join(e.describe(r)))

    def test_missing_or_corrupt_file_falls_back_to_the_builtin_table(self):
        e = load("trader-entry", "entry")                                    # no file at all
        self.assertEqual(e.CALENDAR_YEARS, (2024, 2025, 2026))
        for bad in ("{not json", "[1, 2]", "", '{"years": "x", "holidays": []}'):
            e = self.fresh(bad)
            self.assertEqual(e.CALENDAR_YEARS, (2024, 2025, 2026), bad)
            self.assertEqual(e.trading_day(datetime.date(2027, 1, 26)), (True, ""), bad)
            self.assertFalse(self.run_on(e, "2027-01-26")["calendar_known"], bad)
            self.assertIn("NSE holiday: Dussehra", e.trading_day(datetime.date(2026, 10, 20))[1], bad)


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(RELIANCE) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(len(c["bars"]), 17)
        self.assertEqual(self.calls, ["https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query2(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query1.finance.yahoo.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query2", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_ticker_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertIn("No data found", str(cm.exception))

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 10, 20)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 19), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 1), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 8, 31), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 1), today, "1m")
        self.assertIn("/chart/M%26M.NS?", entry.chart_path("m&m", "1d", "5m"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", entry.chart_path("BAJAJ-AUTO.NS", "1d", "5m"))
        self.assertIn("/chart/%5ENSEI?", entry.chart_path("NIFTY", "1d", "5m"))


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 363.00 (above the high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 363.00, max(b["h"], 363.00)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 363.0, "h": 363.0, "l": 363.0, "c": 363.0, "v": 0})
        return {"symbol": "RELIANCE", "meta": {}, "bars": bars, "prev_close": 354.72, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ny(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-10-19 09:52", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 364.18)
        # it looked at 09:52 (09:50 bar spiking to 363 in progress) and at 09:55:02 (that bar closed 361.68)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:52:00", "09:55:02", "10:00:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:50 bar  closed ₹361.68  ₹0.50 under ₹362.18", text)
        self.assertIn(" 9:55 bar  closed ₹364.18  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ny("2026-10-19 10:00:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-10-19 09:57", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:57:00", "10:00:02", "10:00:32"])   # 10:00:02: Yahoo still at 09:59:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-10-19 08:45", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn(" 9:25 bar  closed ₹360.39  opening range set: high ₹362.18, low ₹354.47", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 9)    # 9:15 ... 09:55, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ny("2026-10-19 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:00 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 14:45", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "15:00")
        until = DT(2026, 10, 19, 10, 45)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 10:15", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 10:45", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-17 09:45")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-19 09:45", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class V4Cache(unittest.TestCase):
    """v4: Yahoo failing (or --cache) reads trader/data/intraday/<SYM>-5m.json."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self._yahoo = entry.yahoo_get
        entry._noted.clear()

    def tearDown(self):
        entry.yahoo_get = self._yahoo
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def put_cache(self):
        os.makedirs(os.path.join("trader", "data", "intraday"))
        shutil.copy(RELIANCE, os.path.join("trader", "data", "intraday", "RELIANCE-5m.json"))

    def blocked(self):
        def boom(path):
            raise entry.EntryError("Yahoo did not answer (HTTP 403 on query1 and query2)")
        entry.yahoo_get = boom

    def test_no_cache_gives_the_way_out(self):
        self.blocked()
        code, _, err = run(["check", "RELIANCE", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        for must in ("HTTP 403", "No cache at trader/data/intraday/RELIANCE-5m.json", "data.py doctor",
                     "Kite MCP get_historical_data", "import-kite"):
            self.assertIn(must, err)

    def test_live_failure_falls_back_to_the_cache(self):
        self.blocked()
        self.put_cache()
        code, text, err = run(["check", "RELIANCE", "--now", "2026-10-19 10:00", "--write"])
        self.assertEqual(code, 0, err)
        self.assertIn("TRIGGERED 10:00 AM at \u20b9364.18", text)
        self.assertIn("using cached data from 2026-10-19", err)
        self.assertIn("run `python3 trader-data/data.py intraday RELIANCE` to refresh", err)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["source"], "cache")

    def test_cache_flag_skips_yahoo(self):
        calls = []
        entry.yahoo_get = lambda path: calls.append(path)
        self.put_cache()
        code, text, err = run(["check", "RELIANCE", "--cache", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [])
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("RELIANCE", "1m", datetime.date(2026, 10, 19), cache=True)
        self.assertIn("5-minute bars only", str(cm.exception))

    def test_browser_agent_and_query1_first(self):
        self.assertEqual(entry.HOSTS, ("query1", "query2"))
        self.assertTrue(entry.UA.startswith("Mozilla/5.0 (Windows"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 10:00", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:00 AM at ₹364.18", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-19",
                "status": "triggered", "time": "10:00 AM", "bar_start": "09:55", "bar_index": 8, "price": 364.18,
                "level": 362.18, "interval": "5m", "opening_range": {"high": 362.18, "low": 354.47, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-19T04:30:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:57", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:57", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(RELIANCE, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance.ns", "--offline", "charts", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "TCS", "--offline", RELIANCE])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE, not TCS", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("plans longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--level", "300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:55"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:35", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:55 bar  closed ₹364.18  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 8)
        code, text, _ = run(["watch", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:35", "--until", "09:45",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(RELIANCE, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 362.18, "or_low": 354.47},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE    triggered  10:00 AM  ₹364.18     ₹362.18"), rows[1])
        self.assertIn("ZZZZ        error: no offline file", rows[2])
        self.assertIn("DEMO        error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 09:57", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only", "in the background", "/loop", "nothing was placed", "10:00 am", "₹364.18", "bar 8", "09:15", "nse"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
