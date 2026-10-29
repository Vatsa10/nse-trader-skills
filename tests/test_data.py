import csv
import datetime
import email.utils
import http.cookiejar
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

data = load("trader-data", "data")
alerts = load("trader-alerts", "alerts")

DFIX = os.path.join(FIX, "trader-data")
REAL = os.path.join(FIX, "real")


def rd(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def wr(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def fx(name):
    with open(os.path.join(DFIX, name), "rb") as fh:
        return fh.read()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = data.main(argv)
    return code, out.getvalue(), err.getvalue()


class Fake(object):
    """Stands in for the network: a list of (substring, status, body) rules, first match wins."""

    def __init__(self, rules):
        self.rules, self.calls = list(rules), []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        for key, code, body in self.rules:
            if key in url:
                if callable(body):
                    return body(url)
                return code, body
        return 404, b"not found"

    def urls(self, key=""):
        return [u for u, _ in self.calls if key in u]


class Net(unittest.TestCase):
    """Base: temp cache folder, fake network, no real sleeping."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dd = os.path.join(self.tmp, "data")
        self.sleeps = []
        self._t, self._s, self._j = data._transport, data._sleep, data._jitter
        data._sleep = self.sleeps.append
        data._jitter = lambda: 1.0          # full wait, so back-off sleeps are exact

    def tearDown(self):
        data._transport, data._sleep, data._jitter = self._t, self._s, self._j
        shutil.rmtree(self.tmp, ignore_errors=True)

    def net(self, rules):
        f = Fake(rules)
        data._transport = f
        return f

    def run_d(self, *argv):
        return run(list(argv) + ["--data", self.dd])

    def daily(self, sym, adj=False):
        return data.read_daily(data.data_paths(self.dd, sym)[1 if adj else 0])

    def seed(self, sym, upto):
        """Cache flat made-up bars on every NSE trading day from 2026-09-01 up to a date."""
        px = {"RELIANCE": 1185.0, "TCS": 2520.0}[sym]   # near the bhavcopy fixtures, so no split warning
        d, rows = datetime.date(2026, 9, 1), []
        while d.isoformat() <= upto:
            if d.weekday() < 5 and d.isoformat() not in data.NSE_HOLIDAYS_2026:
                rows.append(data.bar(d.isoformat(), px, px + 5, px - 5, px, 1000000))
            d += datetime.timedelta(days=1)
        data.write_daily(data.data_paths(self.dd, sym)[0], rows)


# ---------------------------------------------------------------- calendar

class Calendar(unittest.TestCase):
    def test_2026_reuses_trader_alerts(self):
        self.assertEqual(data.NSE_HOLIDAYS_2026, {k: v for k, v in alerts.NSE_HOLIDAYS.items()
                                                  if k.startswith("2026-")})
        self.assertEqual(data.NSE_HOLIDAYS_2026_COPY, data.NSE_HOLIDAYS_2026)
        self.assertIn("2026-01-15", data.HOLIDAYS)   # BMC election closure

    def test_2025_matches_the_nse_circular(self):
        want = ["2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
                "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25"]
        self.assertEqual(sorted(data.NSE_HOLIDAYS_2025), want)
        for d in list(data.NSE_HOLIDAYS_2025) + list(data.NSE_HOLIDAYS_2024):
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)

    def test_trading_days(self):
        D = datetime.date
        self.assertFalse(data.trading_day(D(2026, 10, 20)))       # Dussehra
        self.assertFalse(data.trading_day(D(2026, 10, 24)))       # Saturday
        self.assertTrue(data.trading_day(D(2025, 2, 1)))          # budget Saturday
        self.assertTrue(data.trading_day(D(2025, 10, 21)))        # Muhurat
        self.assertEqual(data.prev_trading_day(D(2026, 10, 21)), D(2026, 10, 19))
        self.assertEqual(data.last_complete_session(datetime.datetime(2026, 10, 23, 17, 0)), D(2026, 10, 22))
        self.assertEqual(data.last_complete_session(datetime.datetime(2026, 10, 23, 18, 30)), D(2026, 10, 23))
        self.assertEqual(data.last_complete_session(datetime.datetime(2026, 10, 25, 12, 0)), D(2026, 10, 23))


# ---------------------------------------------------------------- calendar.json (self-updating calendar)

class CalendarFile(Net):
    NOW = "2027-03-08 10:00"      # a Monday, 5 days after the first circular in the fixture

    def setUp(self):
        Net.setUp(self)
        self.cal_path = os.path.join(self.dd, "calendar.json")
        self.addCleanup(setattr, data, "_CAL", None)

    def feed(self, master=None, circ=None):
        return self.net([("api/holiday-master", 200, master if master is not None else fx("nse-holiday-master.json")),
                         ("api/circulars", 200, circ if circ is not None else fx("nse-circulars-cmtr.json")),
                         ("www.nseindia.com/", 200, b"<html>home</html>")])

    def cal(self):
        return json.loads(rd(self.cal_path))

    def use(self):
        data._CAL = data.load_calendar(self.dd)
        return data._CAL

    def test_fetch_writes_the_contract(self):
        f = self.feed()
        code, out, err = self.run_d("calendar", "--now", self.NOW)
        self.assertEqual(code, 0, out + err)
        cal = self.cal()
        self.assertEqual(sorted(cal), ["fetched_at", "holidays", "last_circular_check", "special_sessions",
                                       "warnings", "years"])
        self.assertEqual(cal["fetched_at"], "2027-03-08T04:30:00Z")
        self.assertEqual(cal["years"], [2027])
        self.assertEqual(cal["last_circular_check"], "2027-03-08")
        hol = cal["holidays"]
        self.assertEqual([k for k in hol if not hol[k].startswith("Circular: ")],
                         ["2027-01-26", "2027-03-22", "2027-03-26"])    # weekend rows and the FO list left out
        self.assertEqual(hol["2027-01-26"], "Republic Day")
        self.assertTrue(hol["2027-04-08"].startswith("Circular: Trading Holiday on account of Special Holiday"))
        sp = cal["special_sessions"]
        self.assertEqual(sorted(sp), ["2027-03-06", "2027-10-29"])
        self.assertEqual(sp["2027-10-29"], "Diwali Laxmi Pujan (Muhurat)")
        self.assertNotIn("2027-10-29", hol)                              # Muhurat is a session, not a closure
        self.assertEqual(sum("no date could be read" in w for w in cal["warnings"]), 1)
        self.assertTrue(any("1 holiday-master row" in w for w in cal["warnings"]))
        self.assertIn("warning: circular mentions a trading holiday but no date could be read", out)
        self.assertIn("+ 2027-01-26 holiday: Republic Day", out)
        self.assertEqual(f.urls("holiday-master")[0].split("nseindia.com")[1], "/api/holiday-master?type=trading")
        self.assertIn("from_date=03-03-2027&to_date=08-03-2027&dept=CMTR", f.urls("api/circulars")[0])  # 5 days back

    def test_resolution_order(self):
        D = datetime.date
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        self.assertIsNotNone(self.use())
        self.assertFalse(data.trading_day(D(2027, 1, 26)))      # listed holiday
        self.assertFalse(data.trading_day(D(2027, 4, 8)))       # closure from a circular
        self.assertTrue(data.trading_day(D(2027, 3, 6)))        # Saturday with a live session
        self.assertTrue(data.trading_day(D(2027, 10, 29)))      # Muhurat: bars exist
        self.assertFalse(data.trading_day(D(2027, 2, 14)))      # Sunday
        self.assertFalse(data.trading_day(D(2027, 8, 15)))      # Sunday holiday: weekend either way
        self.assertTrue(data.trading_day(D(2027, 8, 16)))       # a covered year: unlisted weekday is open
        self.assertTrue(data.trading_day(D(2027, 12, 24)))
        self.assertFalse(data.trading_day(D(2026, 10, 20)))     # 2026 not in the file: built-in Dussehra
        self.assertTrue(data.trading_day(D(2028, 1, 26)))       # uncovered year: built-in tables say open
        self.assertTrue(data.calendar_known(2027))
        self.assertFalse(data.calendar_known(2028))
        self.assertEqual(data.prev_trading_day(D(2027, 1, 27)), D(2027, 1, 25))
        data._CAL = None
        self.assertTrue(data.trading_day(D(2027, 1, 26)))       # without the file Jan 2027 is not known

    def test_a_failed_fetch_leaves_the_builtin_answer(self):
        self.net([("", 403, b"Access Denied")])
        code, out, _ = self.run_d("calendar", "--now", self.NOW)
        self.assertEqual(code, 1)
        self.assertIn("built-in tables stay in force", out)
        self.assertFalse(os.path.exists(self.cal_path))
        self.assertIsNone(data.load_calendar(self.dd))
        self.assertTrue(data.trading_day(datetime.date(2027, 1, 26)))
        self.assertFalse(data.trading_day(datetime.date(2026, 10, 20)))

    def test_bot_wall_html_is_not_a_calendar(self):
        self.net([("api/holiday-master", 200, b"<html>Access Denied</html>"), ("api/circulars", 200, b"[]"),
                  ("www.nseindia.com/", 200, b"home")])
        code, out, _ = self.run_d("calendar", "--now", self.NOW)
        self.assertEqual(code, 1)
        self.assertIn("did not return JSON", out)
        self.assertFalse(os.path.exists(self.cal_path))

    def test_second_run_is_quiet_and_refresh_reports_changes(self):
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        f = self.net([])
        code, out, _ = self.run_d("calendar", "--now", "2027-03-08 15:00")
        self.assertEqual(code, 0)
        self.assertIn("calendar.json is current", out)
        self.assertEqual(f.calls, [])
        master = json.loads(fx("nse-holiday-master.json"))
        master["CM"] = [r for r in master["CM"] if r["description"] != "Holi"] + [
            {"tradingDate": "03-May-2027", "weekDay": "Monday", "description": "Sample added closure"}]
        f = self.feed(master=json.dumps(master).encode(), circ=b'{"data": []}')
        code, out, _ = self.run_d("calendar", "--refresh", "--now", "2027-03-09 10:00")
        self.assertEqual(code, 0, out)
        self.assertIn("- 2027-03-22 holiday: Holi", out)
        self.assertIn("+ 2027-05-03 holiday: Sample added closure", out)
        self.assertIn("from_date=08-03-2027&to_date=09-03-2027", f.urls("api/circulars")[0])   # since the last check
        cal = self.cal()
        self.assertIn("2027-04-08", cal["holidays"])        # the circular closure survives a refetch
        self.assertEqual(cal["last_circular_check"], "2027-03-09")
        self.assertEqual(sum("no date could be read" in w for w in cal["warnings"]), 1)    # not duplicated

    def test_next_day_reads_only_new_circulars(self):
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        f = self.feed(circ=b'{"data": []}')
        code, out, _ = self.run_d("calendar", "--now", "2027-03-09 10:00")
        self.assertEqual(code, 0, out)
        self.assertEqual(f.urls("holiday-master"), [])       # the year is covered: no holiday refetch
        self.assertEqual(len(f.urls("api/circulars")), 1)

    def test_new_year_triggers_a_holiday_fetch(self):
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        f = self.feed()
        self.run_d("calendar", "--now", "2028-01-03 10:00")
        self.assertEqual(len(f.urls("holiday-master")), 1)    # 2028 is not in the file

    def test_a_listed_holiday_with_an_old_list_refetches(self):
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        f = self.feed()
        self.run_d("calendar", "--now", "2027-03-22 08:00")     # Holi, list is two weeks old
        self.assertEqual(len(f.urls("holiday-master")), 1)

    def test_muhurat_on_a_sunday_is_a_special_session(self):
        master = json.loads(fx("nse-holiday-master.json"))
        master["CM"].append({"tradingDate": "31-Oct-2027", "weekDay": "Sunday", "description": "Diwali Laxmi Pujan *"})
        self.feed(master=json.dumps(master).encode())
        self.run_d("calendar", "--now", self.NOW)
        self.use()
        self.assertEqual(self.cal()["special_sessions"]["2027-10-31"], "Diwali Laxmi Pujan (Muhurat)")
        self.assertNotIn("2027-10-31", self.cal()["holidays"])
        self.assertTrue(data.trading_day(datetime.date(2027, 10, 31)))

    def test_corrupt_or_wrong_shape_file_means_builtin(self):
        os.makedirs(self.dd)
        for text in ("{not json", "[]", '{"years": [2027], "holidays": [1, 2]}', '{"years": "x"}', ""):
            wr(self.cal_path, text)
            self.assertIsNone(data.load_calendar(self.dd), text)
            code, out, _ = self.run_d("status", "--now", self.NOW)
            self.assertEqual(code, 0)
            self.assertIn("calendar.json: none", out)
            self.assertTrue(data.trading_day(datetime.date(2027, 1, 26)))
        wr(self.cal_path, "{not json")
        self.feed()
        code, out, _ = self.run_d("calendar", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("unreadable", out)
        self.assertEqual(self.cal()["years"], [2027])

    def test_trader_alerts_reads_what_data_writes(self):
        D = datetime.date
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        self.addCleanup(alerts.apply_calendar, os.path.join(self.tmp, "none.json"))
        alerts.apply_calendar(self.cal_path)
        self.use()
        for d in (D(2027, 1, 26), D(2027, 4, 8), D(2027, 3, 22), D(2027, 2, 14)):
            self.assertFalse(alerts.trading_day(d)[0], d)
            self.assertFalse(data.trading_day(d), d)
        self.assertTrue(alerts.trading_day(D(2027, 3, 6))[0])                       # live Saturday session
        self.assertTrue(alerts.trading_day(D(2027, 10, 29))[1].startswith("special session"))   # Muhurat
        self.assertTrue(data.trading_day(D(2027, 10, 29)))

    def test_bad_keys_in_the_file_are_dropped(self):
        os.makedirs(self.dd)
        wr(self.cal_path, json.dumps({"years": [2027], "holidays": {"2027-01-26": "Republic Day", "soon": "x"}}))
        cal = data.load_calendar(self.dd)
        self.assertEqual(cal["holidays"], {"2027-01-26": "Republic Day"})
        self.assertEqual(cal["special_sessions"], {})
        self.assertEqual(cal["warnings"], [])

    def test_circular_subjects(self):
        D = datetime.date
        for text in ("Live Trading Session on Saturday, March 6, 2027", "session on 6th March 2027 only",
                     "session on 06-Mar-2027", "session on 06/03/2027", "on Mar 6, 2027"):
            self.assertEqual(data.dates_in_text(text)[0], D(2027, 3, 6), text)
        self.assertEqual(data.dates_in_text("no date here, Foo 40, 2027"), [])
        doc = {"data": [
            {"sub": "Trading Holiday on account of Id on Saturday, March 6, 2027"},                     # weekend: no-op
            {"sub": "Live Trading Session and Trading Holiday on account of X on March 8, 2027"},       # ambiguous
            {"sub": "Muhurat Trading on Sunday, November 7, 2027"},
            {"subject": "Trading holiday on account of Y on 12-Apr-2027"},
            {}]}
        closures, special, warns = data.parse_circulars(doc)
        self.assertEqual(list(closures), ["2027-04-12"])
        self.assertEqual(list(special), ["2027-11-07"])
        self.assertEqual(len(warns), 1)
        self.assertIn("both", warns[0])
        with self.assertRaises(data.DataError):
            data.parse_circulars({"nothing": 1})
        with self.assertRaises(data.DataError):
            data.parse_holiday_master({"CM": []})

    def test_validation_follows_the_calendar(self):
        rows = [data.bar("2027-01-25", 1, 1, 1, 1, 1), data.bar("2027-01-27", 1, 1, 1, 1, 1)]
        errs, warns = data.validate_daily(rows)
        self.assertTrue(any("no holiday list" in w and "data.py calendar" in w for w in warns), warns)
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        self.use()
        self.assertEqual(data.validate_daily(rows), ([], []))     # 26 Jan is a listed holiday: no gap
        errs, warns = data.validate_daily(rows + [data.bar("2027-01-28", 1, 1, 1, 1, 1)])
        self.assertEqual(errs, [])
        errs, warns = data.validate_daily([data.bar("2027-01-26", 1, 1, 1, 1, 1)])
        self.assertTrue(any("NSE was closed (Republic Day)" in w for w in warns), warns)

    def test_status_shows_the_calendar(self):
        code, out, _ = self.run_d("status", "--now", self.NOW)
        self.assertIn("calendar.json: none (built-in tables cover 2024, 2025, 2026)", out)
        self.assertIn("no holiday list for 2027: run data.py calendar", out)
        self.feed()
        self.run_d("calendar", "--now", self.NOW)
        code, out, _ = self.run_d("status", "--now", self.NOW)
        self.assertIn("calendar.json: years 2027", out)
        self.assertNotIn("no holiday list", out)

    def test_builtin_disagreement_is_printed(self):
        master = json.loads(fx("nse-holiday-master.json"))
        master["CM"] = [{"tradingDate": "26-Jan-2026", "weekDay": "Monday", "description": "Republic Day"},
                        {"tradingDate": "27-Jan-2026", "weekDay": "Tuesday", "description": "Surprise closure"}]
        self.feed(master=json.dumps(master).encode(), circ=b'{"data": []}')
        code, out, _ = self.run_d("calendar", "--now", "2026-10-29 10:00")
        self.assertEqual(code, 0, out)
        self.assertIn("2026: closed in NSE's list only: 2026-01-27", out)
        self.assertIn("2026: closed in the built-in table only:", out)
        self.assertIn("2026-01-15, 2026-03-03", out)


# ---------------------------------------------------------------- validation and parsing

class Validation(unittest.TestCase):
    def rows(self):
        return [data.bar("2026-10-21", 1212, 1234.3, 1199.3, 1209, 1), data.bar("2026-10-22", 1206.2, 1208, 1184.5,
                                                                               1184.5, 1)]

    def test_clean(self):
        self.assertEqual(data.validate_daily(self.rows()), ([], []))

    def test_errors_block(self):
        r = self.rows()
        self.assertTrue(data.validate_daily(r + [r[0]])[0])                     # dupe + order
        bad = [data.bar("2026-10-21", 1212, 1200, 1199.3, 1209, 1)]             # high below open
        self.assertIn("low/high", data.validate_daily(bad)[0][0])
        self.assertTrue(data.validate_daily([data.bar("2026-10-21", 0, 1, 0, 1, 1)])[0])
        with self.assertRaises(data.DataError):
            data.write_daily(os.path.join(tempfile.mkdtemp(), "x.csv"), bad)

    def test_gap_and_closed_day_warnings(self):
        r = [data.bar("2026-10-16", 1, 1, 1, 1, 1), data.bar("2026-10-20", 1, 1, 1, 1, 1),
             data.bar("2026-10-23", 1, 1, 1, 1, 1)]
        errs, warns = data.validate_daily(r)
        self.assertEqual(errs, [])
        text = " ".join(warns)
        self.assertIn("Dussehra", text)
        self.assertIn("3 NSE trading day(s) missing: 2026-10-19, 2026-10-21, 2026-10-22", text)

    def test_split_warning(self):
        r = [data.bar("2026-10-22", 2400, 2410, 2390, 2400, 1), data.bar("2026-10-23", 1201, 1210, 1195, 1205, 1)]
        self.assertIn("split or bonus", " ".join(data.validate_daily(r)[1]))

    def test_dates(self):
        D = datetime.date(2026, 10, 23)
        for s in ("2026-10-23", "23-10-2026", "23/10/2026", "23-Oct-2026", "23-OCT-2026", "23 Oct 2026",
                  "2026-10-23 00:00:00+05:30", "2026-10-23T00:00:00+0530", "Oct 23, 2026", " 23-Oct-2026 "):
            self.assertEqual(data.parse_date(s), D, s)
        with self.assertRaises(data.DataError):
            data.parse_date("yesterday")
        self.assertEqual(data.parse_iso_ist("2026-10-23T09:15:00+0530"), datetime.datetime(2026, 10, 23, 9, 15))
        self.assertEqual(data.parse_iso_ist("2026-10-23T03:45:00Z"), datetime.datetime(2026, 10, 23, 9, 15))

    def test_symbols_and_bands(self):
        self.assertEqual(data.norm_symbol("reliance.ns"), "RELIANCE")
        self.assertEqual(data.norm_symbol("NSE:M&M"), "M&M")
        with self.assertRaises(data.DataError):
            data.norm_symbol("not a symbol")
        self.assertIsNone(data.band_value("No Band"))
        self.assertEqual(data.band_value("20"), 20)
        self.assertEqual(data.band_value("??"), "unknown")
        self.assertEqual(data.snap_band(1184.5, 1066.05, 1302.95), 10)
        self.assertEqual(data.snap_band(100, 80, 120), 20)
        self.assertIsNone(data.snap_band(100, 70, 133))
        self.assertIsNone(data.snap_band(None, 1, 2))

    def test_to_5m(self):
        t0 = datetime.datetime(2026, 10, 23, 9, 15)
        pts = [(t0 + datetime.timedelta(minutes=i), 10 + i, 11 + i, 9 + i, 10.5 + i, 100) for i in range(10)]
        bars = data.to_5m(pts + [(datetime.datetime(2026, 10, 23, 15, 30), 1, 1, 1, 1, 1)])
        self.assertEqual(len(bars), 2)
        ts, o, h, low, c, v = bars[0]
        self.assertEqual(data.ts_to_ist(ts), t0)
        self.assertEqual((o, h, low, c, v), (10, 15, 9, 14.5, 500))


# ---------------------------------------------------------------- real NSE data

def real_symbols():
    if not os.path.isdir(REAL):
        return []
    return sorted(d for d in os.listdir(REAL) if os.path.isfile(os.path.join(REAL, d, "daily.csv")))


@unittest.skipUnless(real_symbols(), "no real NSE fixtures: run data.py record SYMBOL to make your own")
class RealData(Net):
    def test_have_real_data(self):
        self.assertGreaterEqual(len(real_symbols()), 3)
        for s in ("RELIANCE", "TCS", "INFY"):
            self.assertIn(s, real_symbols())

    def test_provenance(self):
        for s in real_symbols():
            with open(os.path.join(REAL, s, "provenance.json")) as fh:
                p = json.load(fh)
            self.assertEqual(p["symbol"], s)
            for k in ("source", "fetched_at"):
                self.assertTrue(p.get(k), (s, k))
            if "url" in p:
                self.assertTrue(p["url"].startswith("https://raw.githubusercontent.com/"), s)
                self.assertIn(p["commit"], p["url"])

    def test_import_csv_and_validate(self):
        for s in real_symbols():
            adjusted = "adjust" in json.loads(rd(os.path.join(REAL, s, "provenance.json"))).get("adjusted", "")
            argv = ["import-csv", os.path.join(REAL, s, "daily.csv"), "--symbol", s] + (["--adjusted"] if adjusted
                                                                                         else [])
            code, out, err = self.run_d(*argv)
            self.assertEqual(code, 0, err)
            rows = self.daily(s, adj=adjusted)
            self.assertGreaterEqual(len(rows), 500, s)
            first, last = (datetime.date.fromisoformat(rows[i]["date"]) for i in (0, -1))
            self.assertGreater((last - first).days, 730, s)
            errs, warns = data.validate_daily(rows)
            self.assertEqual(errs, [], s)
            # every missing weekday in 2024-2026 is a listed NSE holiday: the calendar and the data agree
            self.assertEqual([w for w in warns if w.startswith("gap") or "NSE was closed" in w], [], (s, warns))
            self.assertEqual([w for w in warns if "split" in w], [], s)   # split/bonus adjusted series

    def test_plausible_prices(self):
        def closes(s, lo_d, hi_d):
            rows, _ = data.parse_ohlc_csv(rd(os.path.join(REAL, s, "daily.csv")), s)
            return [r["close"] for r in rows if lo_d <= r["date"] <= hi_d]
        rel = closes("RELIANCE", "2025-01-01", "2026-12-31")
        self.assertTrue(1100 <= min(rel) and max(rel) <= 1700, (min(rel), max(rel)))   # after the 2024 1:1 bonus
        self.assertEqual(closes("RELIANCE", "2026-10-22", "2026-10-22"), [1184.5])     # 5paisa: Rs 1,184.50
        tcs = closes("TCS", "2024-01-01", "2026-12-31")
        self.assertTrue(1900 <= min(tcs) and max(tcs) <= 4700, (min(tcs), max(tcs)))
        rows, _ = data.parse_ohlc_csv(rd(os.path.join(REAL, "RELIANCE", "daily.csv")), "RELIANCE")
        dates = [r["date"] for r in rows]
        self.assertIn("2024-11-01", dates)          # Muhurat session is a real bar
        self.assertNotIn("2026-10-20", dates)       # Dussehra
        self.assertNotIn("2026-01-15", dates)       # BMC election closure

    def test_real_data_through_the_daily_contract(self):
        s = "RELIANCE"
        self.run_d("import-csv", os.path.join(REAL, s, "daily.csv"), "--symbol", s)
        with open(data.data_paths(self.dd, s)[0]) as fh:
            head = fh.readline().strip()
            first = next(csv.reader(fh))
        self.assertEqual(head, "date,open,high,low,close,volume")
        self.assertEqual(first[0], "2023-10-03")
        self.assertRegex(first[5], r"^\d+$")


# ---------------------------------------------------------------- daily: Yahoo, NSE API, bhavcopy

class Daily(Net):
    NOW = "2026-10-23 19:00"

    def test_yahoo_unadjusts_the_bonus(self):
        f = self.net([("query1.finance.yahoo.com", 200, fx("yahoo-daily-RELIANCE.json"))])
        code, out, _ = self.run_d("daily", "RELIANCE", "--years", "3", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("from yahoo", out)
        url = f.calls[0][0]
        self.assertIn("RELIANCE.NS", url)
        self.assertIn("interval=1d", url)
        self.assertIn("events=div%2Csplit", url)
        self.assertIn("Mozilla", f.calls[0][1]["User-Agent"])
        raw = {r["date"]: r for r in self.daily("RELIANCE")}
        adj = {r["date"]: r for r in self.daily("RELIANCE", adj=True)}
        self.assertEqual(adj["2024-10-25"]["close"], 1327.85)
        self.assertEqual(raw["2024-10-25"]["close"], 2655.7)                # before the 1:1 bonus
        self.assertEqual(raw["2024-10-28"]["close"], adj["2024-10-28"]["close"])
        self.assertEqual(raw["2024-10-25"]["volume"], adj["2024-10-25"]["volume"] // 2)
        warns = data.validate_daily(sorted(raw.values(), key=lambda r: r["date"]))[1]
        self.assertTrue(any("2024-10-28" in w and "split or bonus" in w for w in warns))

    def test_yahoo_backoff_then_query2(self):
        f = self.net([("query1.finance.yahoo.com", 429, b"Too Many Requests"),
                      ("query2.finance.yahoo.com", 200, fx("yahoo-daily-RELIANCE.json"))])
        code, out, _ = self.run_d("daily", "RELIANCE", "--now", self.NOW)
        self.assertEqual(code, 0)
        self.assertEqual(len(f.urls("query1")), data.RETRIES + 1)
        self.assertEqual(len(f.urls("query2")), 1)
        self.assertIn(data.BACKOFF, self.sleeps)
        self.assertIn(data.BACKOFF * 2, self.sleeps)

    def test_nse_api_with_primed_cookies(self):
        f = self.net([("yahoo.com", 403, b"blocked"),
                      ("/api/historical/cm/equity", 200, fx("nse-historical-RELIANCE.json")),
                      ("www.nseindia.com/", 200, b"<html>home</html>")])
        self.seed("RELIANCE", "2026-09-30")
        code, out, _ = self.run_d("daily", "RELIANCE", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("from nse-api", out)
        self.assertIn("tried first: yahoo", out)
        self.assertIn("Yahoo refused", out)
        nse = [c for c in f.calls if "nseindia.com" in c[0]]
        self.assertEqual(nse[0][0], "https://www.nseindia.com/")
        api_url, hdr = nse[1]
        self.assertIn("symbol=RELIANCE", api_url)
        self.assertIn("series=%5B%22EQ%22%5D", api_url)
        self.assertIn("from=01-10-2026&to=23-10-2026", api_url)
        self.assertEqual(hdr["Accept-Language"], "en-US,en;q=0.9")
        self.assertIn("get-quotes/equity?symbol=RELIANCE", hdr["Referer"])
        rows = {r["date"]: r for r in self.daily("RELIANCE")}
        self.assertEqual(rows["2026-10-22"]["close"], 1184.5)
        self.assertEqual(rows["2026-10-23"]["volume"], 14905641)
        self.assertNotIn("2026-10-20", rows)

    def test_nse_chunks_long_ranges(self):
        f = self.net([("yahoo.com", 403, b""), ("/api/historical", 200, b'{"data": []}'),
                      ("www.nseindia.com/", 200, b"ok"), ("nsearchives", 403, b"")])
        code, out, _ = self.run_d("daily", "RELIANCE", "--years", "1", "--now", self.NOW, "--source", "nse")
        self.assertEqual(code, 1)
        self.assertGreaterEqual(len(f.urls("/api/historical")), 3)
        self.assertIn("every source failed", out)
        self.assertIn("get_historical_data", out)

    def test_bhavcopy_incremental(self):
        def bhav(url):
            name = url.rsplit("/", 1)[-1]
            p = os.path.join(DFIX, name)
            return (200, fx(name)) if os.path.exists(p) else (404, b"")
        f = self.net([("nsearchives.nseindia.com/products/content/", None, bhav), ("yahoo.com", 403, b""),
                      ("nseindia.com", 403, b"Access Denied")])
        self.seed("RELIANCE", "2026-10-21")
        self.seed("TCS", "2026-10-21")
        code, out, _ = self.run_d("daily", "RELIANCE", "TCS", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("RELIANCE: +2 bar(s) from nse-bhavcopy", out)
        self.assertIn("NSE blocks this IP", out)
        got = sorted(set(u.rsplit("_", 1)[-1] for u in f.urls("sec_bhavdata_full")))
        self.assertEqual(got, ["22102026.csv", "23102026.csv"])       # only the missing days, once each
        self.assertEqual(len(f.urls("sec_bhavdata_full")), 2)
        tcs = self.daily("TCS")
        self.assertEqual((tcs[-1]["date"], tcs[-1]["close"]), ("2026-10-23", 2530.0))
        rel = self.daily("RELIANCE")
        self.assertEqual(rel[-1]["close"], 1190.0)                    # EQ row, not the BL block deal row
        self.assertIn(1.0, self.sleeps + [data.BHAV_PAUSE])
        # second run: nothing missing, nothing fetched
        f2 = self.net([])
        code, out, _ = self.run_d("daily", "RELIANCE", "--now", self.NOW)
        self.assertIn("up to date", out)
        self.assertEqual(f2.calls, [])

    def test_bhavcopy_udiff_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv",
                       "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
                       "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
                       "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
                       "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
                       "2026-10-23,2026-10-23,CM,NSE,STK,2885,INE002A01018,RELIANCE,EQ,,,,,RELIANCE INDUSTRIES LTD,"
                       "1184.00,1201.90,1163.10,1190.00,1190.00,1184.50,,1190.00,,,14905641,0,241397,F1,1,,,,,\n")
        self.net([("sec_bhavdata_full", 404, b""), ("BhavCopy_NSE_CM_0_0_0_20261023", 200, buf.getvalue()),
                  ("nsearchives", 404, b"")])
        self.seed("RELIANCE", "2026-10-22")
        code, out, _ = self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.daily("RELIANCE")[-1], data.bar("2026-10-23", 1184, 1201.9, 1163.1, 1190, 14905641))

    def test_adj_file_follows_unadjusted_updates(self):
        self.seed("RELIANCE", "2026-10-21")
        shutil.copyfile(data.data_paths(self.dd, "RELIANCE")[0], data.data_paths(self.dd, "RELIANCE")[1])
        self.net([("nsearchives.nseindia.com/products/content/sec_bhavdata_full_22102026", 200,
                   fx("sec_bhavdata_full_22102026.csv")),
                  ("nsearchives.nseindia.com/products/content/sec_bhavdata_full_23102026", 200,
                   fx("sec_bhavdata_full_23102026.csv"))])
        code, out, _ = self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.daily("RELIANCE", adj=True)[-1]["date"], "2026-10-23")


# ---------------------------------------------------------------- NSE transport: retries, cookies, legacy bhavcopy

def email_date(dt):
    return email.utils.format_datetime(dt, usegmt=True)


def script(*replies):
    """A Fake rule body that answers each call from the list in turn; an exception in it is raised."""
    it = iter(replies)

    def body(url):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return body


ARCH = "https://nsearchives.nseindia.com/content/cm/x.csv"


class Transport(Net):
    def get(self, rules, url=ARCH, **kw):
        f = self.net([("nseindia.com", None, rules)])
        return f, data.Session().get(url, gap=0, **kw)

    def test_503_twice_with_retry_after_then_200(self):
        f, (code, body) = self.get(script((503, b"", {"Retry-After": "1"}), (503, b"", {"retry-after": "1"}),
                                          (200, b"fine", {})))
        self.assertEqual((code, body), (200, b"fine"))
        self.assertEqual(len(f.calls), 3)
        self.assertEqual(self.sleeps, [1.0, 1.0])

    def test_retry_after_is_capped_and_may_be_an_http_date(self):
        future = email_date(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1))
        past = email_date(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
        self.get(script((429, b"", {"Retry-After": "120"}), (503, b"", {"Retry-After": future}),
                        (503, b"", {"Retry-After": past}), (503, b"", {"Retry-After": "soon"}), (200, b"", {})))
        # 120 s and a date an hour away are capped at 8; a past date is 0; garbage falls back to 2^4
        self.assertEqual(self.sleeps, [8.0, 8.0, 0.0, 8.0])
        self.assertEqual(data.retry_after({"Retry-After": "3"}), 3.0)
        self.assertIsNone(data.retry_after({"Retry-After": "soon"}))
        self.assertIsNone(data.retry_after({}))

    def test_exponential_backoff_with_jitter_capped_at_8(self):
        f, _ = self.get(script(*([(429, b"", {})] * 5 + [(200, b"", {})])))
        self.assertEqual(self.sleeps, [2.0, 4.0, 8.0, 8.0, 8.0])
        self.assertEqual(len(f.calls), 6)
        data._jitter = lambda: 0.5
        del self.sleeps[:]
        self.get(script((502, b"", {}), (504, b"", {}), (200, b"", {})))
        self.assertEqual(self.sleeps, [1.0, 2.0])
        data._jitter = lambda: 0.0
        del self.sleeps[:]
        self.get(script((503, b"", {}), (200, b"", {})))
        self.assertEqual(self.sleeps, [0.0])

    def test_gives_up_after_five_retries_and_returns_the_status(self):
        f, (code, _) = self.get(script(*([(503, b"busy", {})] * 9)))
        self.assertEqual(code, 503)
        self.assertEqual(len(f.calls), 1 + data.NSE_RETRIES)
        self.assertEqual(len(self.sleeps), data.NSE_RETRIES)
        self.assertTrue(all(0 <= x <= 8 for x in self.sleeps))

    def test_a_404_on_an_archive_file_is_never_retried(self):
        f, (code, _) = self.get(script((404, b"", {}), (200, b"late", {})))
        self.assertEqual(code, 404)
        self.assertEqual(len(f.calls), 1)
        self.assertEqual(self.sleeps, [])
        for status in (401, 403, 400, 500):
            f, (code, _) = self.get(script((status, b"", {}), (200, b"", {})))
            self.assertEqual((code, len(f.calls)), (status, 1), status)

    def test_timeouts_are_retried_then_reported(self):
        boom = data.SourceError("nsearchives", "timed out", transient=True)
        f, (code, body) = self.get(script(boom, boom, (200, b"ok", {})))
        self.assertEqual((code, len(f.calls), self.sleeps), (200, 3, [2.0, 4.0]))
        del self.sleeps[:]
        self.net([("nseindia.com", None, script(*([boom] * 9)))])
        with self.assertRaises(data.SourceError) as cm:
            data.Session().get(ARCH, gap=0)
        self.assertIn("gave up after 5 retries", str(cm.exception))
        self.assertEqual(len(self.sleeps), 5)
        # an error that is not transient is raised at once
        del self.sleeps[:]
        self.net([("nseindia.com", None, script(data.SourceError("nsearchives", "proxy says no")))])
        with self.assertRaises(data.SourceError):
            data.Session().get(ARCH, gap=0)
        self.assertEqual(self.sleeps, [])

    def test_only_nse_hosts_retry_unless_told(self):
        f = self.net([("yahoo.com", None, script((503, b"", {}), (200, b"", {})))])
        self.assertEqual(data.Session().get("https://query1.finance.yahoo.com/x", gap=0)[0], 503)
        self.assertEqual(len(f.calls), 1)
        f = self.net([("nseindia.com", None, script((503, b"", {}), (200, b"", {})))])
        self.assertEqual(data.Session().get(ARCH, gap=0, retries=0)[0], 503)
        self.assertEqual(len(f.calls), 1)

    def test_a_bhavcopy_day_survives_a_503(self):
        csv_text = fx("sec_bhavdata_full_23102026.csv")
        f = self.net([("sec_bhavdata_full_23102026", None, script((503, b"", {"Retry-After": "2"}), (200, csv_text, {})))])
        table = data.Bhav(data.Session()).day(datetime.date(2026, 10, 23))
        self.assertEqual(table["RELIANCE"]["close"], 1190.0)
        self.assertEqual(len(f.calls), 2)
        self.assertIn(2.0, self.sleeps)

    def test_doctor_reports_the_first_answer_without_retrying(self):
        f = self.net([("quote-equity", 503, b""), ("", 403, b"")])
        self.run_d("doctor")
        self.assertEqual(len(f.urls("quote-equity")), 1)
        self.assertNotIn(8.0, self.sleeps)


class Cookies(Net):
    def cookie(self, domain="www.nseindia.com", expires=None, name="nsit"):
        return http.cookiejar.Cookie(0, name, "abc", None, False, domain, True, False, "/", True, False, expires,
                                     expires is None, None, None, {})

    def test_saved_and_reloaded_without_dropping_session_cookies(self):
        s1 = data.Session(self.dd)
        self.assertFalse(s1.nse_primed)
        s1.jar.set_cookie(self.cookie())
        s1.jar.set_cookie(self.cookie(expires=1, name="old"))     # long expired
        s1.save_cookies()
        path = os.path.join(self.dd, data.COOKIE_FILE)
        self.assertTrue(rd(path).startswith("# Netscape HTTP Cookie File"))
        s2 = data.Session(self.dd)
        self.assertEqual([c.name for c in s2.jar], ["nsit"])           # the session cookie stays, the expired one goes
        self.assertTrue(s2.nse_primed)                                 # fresh NSE cookies: no prime call
        old = time.time() - data.COOKIE_FRESH - 60
        os.utime(path, (old, old))
        s3 = data.Session(self.dd)
        self.assertEqual(len(s3.jar), 1)
        self.assertFalse(s3.nse_primed)                                # stale file: cookies loaded, prime again

    def test_a_garbage_cookie_file_is_a_cold_start(self):
        os.makedirs(self.dd)
        wr(os.path.join(self.dd, data.COOKIE_FILE), "this is not a cookie file")
        s = data.Session(self.dd)
        self.assertEqual(len(s.jar), 0)
        self.assertFalse(s.nse_primed)

    def test_a_fresh_cookie_file_skips_the_home_page(self):
        s1 = data.Session(self.dd)
        s1.jar.set_cookie(self.cookie())
        s1.save_cookies()
        f = self.net([("quote-equity", 200, fx("nse-quote-RELIANCE.json")), ("www.nseindia.com/", 200, b"home")])
        code, out, _ = self.run_d("bands", "RELIANCE", "--now", "2026-10-23 16:30")
        self.assertEqual(code, 0, out)
        self.assertEqual([u for u in f.urls() if u.endswith("nseindia.com/")], [])

    def test_a_401_primes_once_more(self):
        s = data.Session(self.dd)
        s.jar.set_cookie(self.cookie())
        s.save_cookies()
        f = self.net([("quote-equity", None, script((401, b"", {}), (200, fx("nse-quote-RELIANCE.json"), {}))),
                      ("www.nseindia.com/", 200, b"home")])
        code, out, _ = self.run_d("bands", "RELIANCE", "--now", "2026-10-23 16:30")
        self.assertEqual(code, 0, out)
        self.assertEqual(len([u for u in f.urls() if u.endswith("nseindia.com/")]), 1)   # the re-prime
        self.assertEqual(len(f.urls("quote-equity")), 2)

    def test_the_prime_writes_the_cookie_file(self):
        class Home(object):
            def __call__(inner, url, headers, timeout):
                self.sess.jar.set_cookie(self.cookie())
                return 200, b"home"
        self.sess = data.Session(self.dd)
        data._transport = Home()
        data.nse_prime(self.sess)
        self.assertTrue(os.path.exists(os.path.join(self.dd, data.COOKIE_FILE)))


class LegacyBhav(Net):
    def test_urls_switch_on_the_udiff_date(self):
        D = datetime.date
        new = data.bhav_urls(D(2024, 7, 8))
        self.assertTrue(new[1].endswith("/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"))
        old = data.bhav_urls(D(2024, 7, 5))
        self.assertEqual(old[0], data.ARCHIVES + "/products/content/sec_bhavdata_full_05072024.csv")
        self.assertEqual(old[1], data.ARCHIVES + "/content/historical/EQUITIES/2024/JUL/cm05JUL2024bhav.csv.zip")
        self.assertTrue(data.bhav_urls(D(2020, 1, 3))[1].endswith("/EQUITIES/2020/JAN/cm03JAN2020bhav.csv.zip"))

    def test_daily_max_style_fill_reads_the_legacy_file(self):
        def legacy(url):
            name = url.rsplit("/", 1)[-1]                       # cm05JUL2024bhav.csv.zip
            day = datetime.datetime.strptime(name[2:11], "%d%b%Y")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(name[:-4], "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
                                      "TOTALTRADES,ISIN,\nRELIANCE,EQ,2900,2950,2880,2940,2940,2890,4000000,1,"
                                      "%s,100,INE002A01018,\nRELIANCE,BE,1,1,1,1,1,1,1,1,%s,1,X,\n"
                                      % (day.strftime("%d-%b-%Y").upper(), day.strftime("%d-%b-%Y").upper()))
            return 200, buf.getvalue()
        f = self.net([("content/historical/EQUITIES/", None, legacy), ("yahoo.com", 403, b""),
                      ("nseindia.com", 404, b"")])
        code, out, _ = self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--years", "0.02",
                                  "--now", "2024-07-05 19:00")
        self.assertEqual(code, 0, out)
        rows = self.daily("RELIANCE")
        self.assertEqual([r["date"] for r in rows], ["2024-06-28", "2024-07-01", "2024-07-02", "2024-07-03",
                                                     "2024-07-04", "2024-07-05"])
        self.assertEqual(rows[-1], data.bar("2024-07-05", 2900, 2950, 2880, 2940, 4000000))   # EQ row, not BE
        self.assertEqual(len(f.urls("cm05JUL2024bhav.csv.zip")), 1)
        self.assertEqual(f.urls("BhavCopy_NSE_CM"), [])         # UDiFF is not tried before its start date


# ---------------------------------------------------------------- delivery analytics

HEAD = "date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series"


def drow(date, vol, trades, dq, series="EQ"):
    """A delivery row as read_delivery returns it (a blank is None)."""
    pct = round(dq * 100.0 / vol, 2) if dq is not None and vol else None
    return {"date": date, "volume": vol, "trades": trades, "deliv_qty": dq, "deliv_pct": pct,
            "qty_per_trade": round(vol / float(trades), 2) if vol is not None and trades else None, "series": series}


def spike_rows(n=30, last=300):
    """n sessions at delivery 100 / volume 200 / 10 trades, then a last day at 300 / 600 / 10 trades."""
    start = datetime.date(2026, 8, 3)
    days = []
    d = start
    while len(days) < n + 1:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return [drow(x, 200, 10, 100) for x in days[:n]] + [drow(days[n], 600, 10, last)]


class DeliveryParse(unittest.TestCase):
    def rows(self):
        return data.parse_delivery(rd(os.path.join(DFIX, "sec_bhavdata_full_delivery_cases.csv")))

    def test_padded_headers_dashes_and_series(self):
        t = self.rows()
        self.assertEqual(t["ALPHA"], {"date": "2026-10-23", "volume": 1000, "trades": 50, "deliv_qty": 400,
                                      "deliv_pct": 40.0, "qty_per_trade": 20.0, "series": "EQ"})   # EQ, not the BL row
        self.assertIsNone(t["BETA"]["deliv_qty"])                  # "-" is missing, never 0
        self.assertIsNone(t["BETA"]["deliv_pct"])
        self.assertEqual(t["BETA"]["qty_per_trade"], 20.0)
        self.assertEqual((t["GAMMA"]["deliv_qty"], t["GAMMA"]["deliv_pct"]), (500, 100.0))   # BE: all volume
        self.assertEqual(t["GAMMA"]["series"], "BE")
        self.assertIsNone(t["DELTA"]["qty_per_trade"])             # zero trades
        self.assertEqual(t["DELTA"]["deliv_qty"], 200)
        self.assertNotIn("ALPHA-RE", t)                            # rights
        self.assertEqual(t["OMEGA"]["deliv_pct"], 50.0)
        self.assertEqual(data.parse_delivery(rd(os.path.join(DFIX, "sec_bhavdata_full_delivery_cases.csv")),
                                             want={"GAMMA"}).keys(), {"GAMMA"})

    def test_udiff_keeps_volume_and_trades_only(self):
        text = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,"
                "TtlTradgVol,TtlNbOfTxsExctd\n2026-10-23,2026-10-23,CM,NSE,STK,1,X,RELIANCE,EQ,1,1,1,1,14905641,241397\n")
        r = data.parse_delivery(text)["RELIANCE"]
        self.assertEqual((r["volume"], r["trades"], r["qty_per_trade"]), (14905641, 241397, 61.75))
        self.assertIsNone(r["deliv_qty"])
        self.assertIsNone(r["deliv_pct"])
        with self.assertRaises(data.DataError):
            data.parse_delivery("Foo,Bar\n1,2\n")

    def test_csv_contract(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        t = self.rows()
        data.write_delivery(d, "BETA", [t["BETA"]])
        data.write_delivery(d, "GAMMA", [t["GAMMA"]])
        data.write_delivery(d, "DELTA", [t["DELTA"]])
        path = lambda s: os.path.join(d, "delivery", s + ".csv")  # noqa: E731
        self.assertEqual(rd(path("BETA")), HEAD + "\n2026-10-23,2000,100,,,20.00,EQ\n")      # blank cells, no zeros
        self.assertEqual(rd(path("GAMMA")), HEAD + "\n2026-10-23,500,20,500,100.00,25.00,BE\n")
        self.assertEqual(rd(path("DELTA")), HEAD + "\n2026-10-23,800,0,200,25.00,,EQ\n")
        back = data.read_delivery(path("BETA"))
        self.assertEqual(back, [t["BETA"]])
        self.assertIsNone(back[0]["deliv_qty"])

    def test_a_blank_never_wipes_a_value(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        data.write_delivery(d, "X", [drow("2026-10-23", 1000, 50, 400)])
        rows = data.write_delivery(d, "X", [drow("2026-10-23", 1000, 50, None), drow("2026-10-22", 900, 45, None)])
        self.assertEqual([(r["date"], r["deliv_qty"]) for r in rows], [("2026-10-22", None), ("2026-10-23", 400)])
        rows = data.write_delivery(d, "X", [drow("2026-10-22", 900, 45, 300)])       # a late file fills the blank
        self.assertEqual(rows[0]["deliv_qty"], 300)


class DeliveryMetrics(unittest.TestCase):
    def test_a_spike_day(self):
        rows = spike_rows()
        self.assertEqual(len(rows), 31)
        m = data.delivery_metrics(rows)
        self.assertEqual(m["window"], 30)
        self.assertAlmostEqual(m["dq"], 300 / ((29 * 100 + 300) / 30.0))
        self.assertAlmostEqual(m["dq"], 2.8125)
        self.assertAlmostEqual(m["tq"], 60 / ((29 * 20 + 60) / 30.0))
        self.assertAlmostEqual(m["vol"], 600 / ((29 * 200 + 600) / 30.0))
        self.assertTrue(m["institutional"])
        self.assertEqual(m["band"], "strong")
        self.assertEqual(m["status"], "ok")

    def test_bands(self):
        def band(last):
            return data.delivery_metrics(spike_rows(last=last))["band"]
        self.assertEqual(band(100), "normal")
        self.assertEqual(band(150), "above normal")      # 150 / 101.7 = 1.47
        self.assertEqual(band(170), "high")              # 1.62
        self.assertEqual(band(1000), "strong")

    def test_dq_without_big_tickets_is_not_institutional(self):
        rows = spike_rows()
        rows[-1] = drow(rows[-1]["date"], 200, 10, 300)        # delivery up, ticket size flat
        m = data.delivery_metrics(rows)
        self.assertGreater(m["dq"], 1.2)
        self.assertLessEqual(m["tq"], 1.2)
        self.assertFalse(m["institutional"])

    def test_trade_to_trade_is_never_flagged(self):
        rows = spike_rows()
        rows[-1] = drow(rows[-1]["date"], 600, 10, 600, "BE")
        m = data.delivery_metrics(rows)
        self.assertTrue(m["t2t"])
        self.assertFalse(m["institutional"])

    def test_a_missing_day_is_left_out_of_the_mean_not_counted_as_zero(self):
        rows = spike_rows()
        rows[3]["deliv_qty"] = None
        rows[3]["deliv_pct"] = None
        m = data.delivery_metrics(rows)
        self.assertEqual(m["n_dq"], 29)
        self.assertAlmostEqual(m["dq"], 300 / ((28 * 100 + 300) / 29.0))

    def test_not_enough_data(self):
        m = data.delivery_metrics(spike_rows(n=3))
        self.assertEqual(m["status"], "not enough delivery data")
        self.assertFalse(m["institutional"])
        self.assertEqual(data.delivery_metrics(spike_rows(n=4))["status"], "ok")      # 5 values

    def test_latest_delivery_still_pending(self):
        rows = spike_rows()
        rows[-1] = drow(rows[-1]["date"], 600, 10, None)
        m = data.delivery_metrics(rows)
        self.assertIsNone(m["dq"])
        self.assertIsNone(m["band"])
        self.assertFalse(m["institutional"])
        self.assertIsNotNone(m["tq"])


class DeliveryCommands(Net):
    NOW = "2026-10-23 19:00"

    def bhav_net(self, extra=()):
        def bhav(url):
            name = url.rsplit("/", 1)[-1]
            return (200, fx(name)) if os.path.exists(os.path.join(DFIX, name)) else (404, b"")
        return self.net(list(extra) + [("sec_bhavdata_full", None, bhav), ("yahoo.com", 403, b""),
                                       ("nseindia.com", 404, b"")])

    def drows(self, sym):
        return data.read_delivery(data.delivery_path(self.dd, sym))

    def pending(self):
        return json.loads(rd(os.path.join(self.dd, "delivery", "pending.json")))

    def put_csv(self, sym, rows):
        data.write_delivery(self.dd, sym, rows)

    def test_daily_through_bhavcopy_fills_the_delivery_files(self):
        self.bhav_net()
        self.seed("RELIANCE", "2026-10-21")
        self.seed("TCS", "2026-10-21")
        code, out, _ = self.run_d("daily", "RELIANCE", "TCS", "--source", "bhavcopy", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("delivery: 4 row(s) written for 2 symbol(s)", out)
        text = rd(data.delivery_path(self.dd, "RELIANCE"))
        self.assertEqual(text.splitlines()[0], HEAD)
        self.assertEqual(text.splitlines()[1:], ["2026-10-22,14957042,213072,5009411,33.49,70.20,EQ",
                                                 "2026-10-23,14905641,241397,7634419,51.22,61.75,EQ"])
        self.assertEqual(self.drows("TCS")[-1]["deliv_qty"], 2356372)
        self.assertEqual(self.pending() if os.path.exists(os.path.join(self.dd, "delivery", "pending.json"))
                         else {"pending": []}, {"pending": []})
        # the file the scanner reads also keeps the plain OHLC cache untouched
        self.assertEqual(rd(data.data_paths(self.dd, "RELIANCE")[0]).splitlines()[0], "date,open,high,low,close,volume")

    def test_late_delivery_file_becomes_pending_then_arrives(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv",
                       "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlNbOfTxsExctd\n"
                       "2026-10-23,RELIANCE,EQ,1184.00,1201.90,1163.10,1190.00,14905641,241397\n")
        self.net([("sec_bhavdata_full", 404, b""), ("BhavCopy_NSE_CM_0_0_0_20261023", 200, buf.getvalue()),
                  ("nsearchives", 404, b"")])
        self.seed("RELIANCE", "2026-10-22")
        code, out, _ = self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--now", self.NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("delivery pending", out)
        self.assertEqual(rd(data.delivery_path(self.dd, "RELIANCE")), HEAD + "\n2026-10-23,14905641,241397,,,61.75,EQ\n")
        self.assertEqual(self.pending(), {"pending": ["2026-10-23"]})
        # the file appears: the next run retries the day without being asked
        f = self.bhav_net()
        self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--now", "2026-10-26 10:00")
        self.assertEqual(self.drows("RELIANCE")[-1]["deliv_qty"], 7634419)
        self.assertEqual(self.pending(), {"pending": []})
        self.assertEqual(len(f.urls("sec_bhavdata_full_23102026")), 1)

    def test_a_pending_day_is_dropped_after_five_days(self):
        os.makedirs(os.path.join(self.dd, "delivery"))
        wr(os.path.join(self.dd, "delivery", "pending.json"), json.dumps({"pending": ["2026-10-20", "2026-10-27"]}))
        f = self.bhav_net()
        self.seed("RELIANCE", "2026-10-23")
        code, out, _ = self.run_d("daily", "RELIANCE", "--source", "bhavcopy", "--now", "2026-10-27 19:00")
        self.assertIn("warning: the delivery file for 2026-10-20 did not appear within 5 days", out)
        self.assertEqual(self.pending(), {"pending": ["2026-10-27"]})
        self.assertEqual(f.urls("sec_bhavdata_full_20102026"), [])        # too old to ask again

    def test_a_run_without_bhavcopy_touches_nothing(self):
        self.net([("query1", 200, fx("yahoo-daily-RELIANCE.json"))])
        self.run_d("daily", "RELIANCE", "--now", "2026-10-23 19:00")
        self.assertFalse(os.path.exists(os.path.join(self.dd, "delivery")))

    def test_delivery_command_from_the_cache(self):
        self.put_csv("ABC", spike_rows())
        code, out, _ = self.run_d("delivery", "ABC", "--no-fetch")
        self.assertEqual(code, 0, out)
        self.assertIn("DQ 2.81x", out)
        self.assertIn("TQ 2.81x", out)
        self.assertIn("VOL 2.81x", out)
        self.assertIn("band: strong   institutional money: YES", out)
        self.assertIn("delivered 300 of 600 shares (50.0%), 60.0 shares per trade", out)
        # no symbols named: every tracked file
        code, out, _ = self.run_d("delivery", "--no-fetch")
        self.assertIn("ABC  2026-09-14", out)

    def test_delivery_command_edge_cases(self):
        rows = spike_rows()
        rows[-1] = drow(rows[-1]["date"], 600, 10, 600, "BZ")
        self.put_csv("T2T", rows)
        self.put_csv("FEW", spike_rows(n=3))
        self.put_csv("LATE", spike_rows()[:-1] + [drow("2026-09-14", 600, 10, None)])
        code, out, _ = self.run_d("delivery", "T2T", "FEW", "LATE", "NONE", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("institutional money: no (trade-to-trade series BZ", out)
        self.assertIn("not enough delivery data: 4 value(s)", out)
        self.assertIn("delivery for 2026-09-14 is not published yet", out)
        self.assertIn("NONE: no delivery data cached", out)

    def test_delivery_command_fetches_the_missing_sessions_once(self):
        f = self.bhav_net()
        code, out, _ = self.run_d("delivery", "RELIANCE", "--window", "5", "--now", self.NOW)
        self.assertEqual(code, 1)                      # two days is not enough
        self.assertIn("not enough delivery data: 2 value(s)", out)
        rows = self.drows("RELIANCE")
        self.assertEqual([r["date"] for r in rows], ["2026-10-22", "2026-10-23"])
        self.assertEqual(len(f.urls("sec_bhavdata_full_23102026")), 1)
        self.assertEqual(len(f.urls("sec_bhavdata_full_22102026")), 1)
        self.assertEqual(f.urls("BhavCopy_NSE_CM"), [])          # delivery asks for the delivery file only
        pend = self.pending()["pending"]
        self.assertNotIn("2026-10-23", pend)
        self.assertTrue(set(pend) <= {"2026-10-19", "2026-10-21"})      # recent 404s are retried later
        before = len(f.calls)
        self.run_d("delivery", "RELIANCE", "--window", "5", "--no-fetch")
        self.assertEqual(len(f.calls), before)                                  # --no-fetch asks nobody

    def test_status_lists_the_delivery_files(self):
        self.put_csv("ABC", spike_rows())
        code, out, _ = self.run_d("status", "--now", self.NOW)
        self.assertIn("delivery/: 1 symbol file(s); pending days: none", out)


# ---------------------------------------------------------------- intraday

class Intraday(Net):
    def test_yahoo_5m(self):
        f = self.net([("query1", 200, fx("yahoo-5m-RELIANCE.json"))])
        code, out, _ = self.run_d("intraday", "RELIANCE", "--days", "60")
        self.assertEqual(code, 0, out)
        self.assertIn("range=60d", f.calls[0][0])
        self.assertIn("interval=5m", f.calls[0][0])
        path = data.data_paths(self.dd, "RELIANCE")[2]
        with open(path) as fh:
            doc = json.load(fh)
        res = doc["chart"]["result"][0]
        self.assertEqual(len(res["timestamp"]), 75)                       # 15:30 bar dropped
        self.assertEqual(set(res["indicators"]["quote"][0]), {"open", "high", "low", "close", "volume"})
        self.assertEqual(res["meta"]["symbol"], "RELIANCE.NS")
        self.assertEqual(res["meta"]["source"], "yahoo")
        # the existing trader-alerts parser reads it unchanged
        q = alerts.parse_yahoo(json.dumps(doc), "RELIANCE")
        self.assertEqual(q["price"], 1190.0)
        self.assertEqual(data.validate_intraday(data.parse_chart(doc)[1]), ([], []))

    def test_nse_chart_fallback(self):
        self.net([("yahoo.com", 429, b""), ("chart-databyindex?index=RELIANCEEQN", 200, fx("nse-chart-RELIANCE.json")),
                  ("www.nseindia.com/", 200, b"ok")])
        code, out, _ = self.run_d("intraday", "RELIANCE")
        self.assertEqual(code, 0, out)
        self.assertIn("nse-chart", out)
        meta, bars = data.read_intraday(data.data_paths(self.dd, "RELIANCE")[2])
        self.assertEqual(len(bars), 75)
        self.assertEqual(data.ts_to_ist(bars[0][0]), datetime.datetime(2026, 10, 23, 9, 15))
        self.assertEqual(bars[0][1], 1184.0)
        self.assertIsNone(bars[0][5])                                      # NSE chart has no volume
        self.assertEqual(bars[-1][4], 1190.0)

    def test_all_fail(self):
        self.net([("yahoo.com", 403, b""), ("nseindia.com", 403, b"")])
        code, out, _ = self.run_d("intraday", "RELIANCE")
        self.assertEqual(code, 1)
        self.assertIn("5minute", out)


# ---------------------------------------------------------------- bands

class Bands(Net):
    def read(self):
        with open(os.path.join(self.dd, "bands.json")) as fh:
            return json.load(fh)

    def test_nse_quote(self):
        self.net([("quote-equity?symbol=RELIANCE", 200, fx("nse-quote-RELIANCE.json")),
                  ("www.nseindia.com/", 200, b"ok")])
        code, out, _ = self.run_d("bands", "RELIANCE", "--now", "2026-10-23 16:30")
        self.assertEqual(code, 0, out)
        b = self.read()
        self.assertEqual(b["as_of"], "2026-10-23")
        self.assertEqual(b["bands"]["RELIANCE"]["band_pct"], None)
        self.assertEqual(b["bands"]["RELIANCE"]["lower"], 1066.05)
        self.assertEqual(b["bands"]["RELIANCE"]["upper"], 1302.95)
        self.assertEqual(b["unknown"], [])

    def test_sec_list_then_unknown(self):
        f = self.net([("www.nseindia.com", 403, b""), ("sec_list.csv", 200, fx("sec_list.csv"))])
        code, out, _ = self.run_d("bands", "SMALLCO", "HINDCOPPER", "NOSUCH", "--now", "2026-10-23 16:30")
        self.assertEqual(code, 1)
        b = self.read()
        self.assertEqual(b["bands"]["SMALLCO"], {"band_pct": 20, "lower": None, "upper": None,
                                                 "source": "nse-sec-list", "as_of": "2026-10-23"})
        self.assertIsNone(b["bands"]["HINDCOPPER"]["band_pct"])
        self.assertNotIn("NOSUCH", b["bands"])
        self.assertEqual(b["unknown"], ["NOSUCH"])
        self.assertIn("not guessed", out)
        self.assertEqual(len(f.urls("quote-equity")), 0)    # home page blocked: no API hammering


# ---------------------------------------------------------------- Kite MCP imports

class Kite(Net):
    def test_historical_day(self):
        code, out, _ = self.run_d("import-kite", os.path.join(DFIX, "kite-historical-RELIANCE-day.json"),
                                  "--kind", "historical", "--symbol", "RELIANCE")
        self.assertEqual(code, 0, out)
        rows = self.daily("RELIANCE")
        self.assertEqual((rows[0]["date"], rows[-1]["date"], len(rows)), ("2026-10-01", "2026-10-23", 15))
        self.assertEqual(rows[-1], data.bar("2026-10-23", 1184, 1201.9, 1163.1, 1190, 14905641))

    def test_historical_needs_symbol(self):
        code, _, err = self.run_d("import-kite", os.path.join(DFIX, "kite-historical-RELIANCE-day.json"),
                                  "--kind", "historical")
        self.assertEqual(code, 1)
        self.assertIn("--symbol", err)

    def test_historical_minute_to_5m(self):
        code, out, _ = self.run_d("import-kite", os.path.join(DFIX, "kite-historical-RELIANCE-minute.json"),
                                  "--kind", "historical", "--symbol", "RELIANCE")
        self.assertEqual(code, 0, out)
        _, bars = data.read_intraday(data.data_paths(self.dd, "RELIANCE")[2])
        self.assertEqual(len(bars), 12)
        mins = json.loads(rd(os.path.join(DFIX, "kite-historical-RELIANCE-minute.json")))[:5]
        self.assertEqual(bars[0][1:], (mins[0]["open"], max(m["high"] for m in mins), min(m["low"] for m in mins),
                                       mins[-1]["close"], sum(m["volume"] for m in mins)))

    def test_quotes_update_bands_and_daily(self):
        code, out, _ = self.run_d("import-kite", os.path.join(DFIX, "kite-quotes.json"), "--kind", "quotes",
                                  "--now", "2026-10-23 16:10")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.dd, "bands.json")) as fh:
            b = json.load(fh)
        self.assertEqual(b["bands"]["RELIANCE"]["lower"], 1066.05)
        self.assertEqual(b["bands"]["RELIANCE"]["upper"], 1302.95)
        self.assertEqual(b["bands"]["RELIANCE"]["band_pct"], 10)
        self.assertEqual(b["bands"]["HINDCOPPER"]["band_pct"], 10)
        self.assertIn("kite-mcp", b["source"])
        self.assertEqual(self.daily("RELIANCE")[-1], data.bar("2026-10-23", 1184, 1201.9, 1163.1, 1190, 14905641))

    def test_quotes_mid_session_no_bar(self):
        doc = json.loads(fx("kite-quotes.json"))
        doc["NSE:RELIANCE"]["timestamp"] = "2026-10-23 11:02:00"
        p = os.path.join(self.tmp, "q.json")
        wr(p, json.dumps(doc))
        code, out, _ = self.run_d("import-kite", p, "--kind", "quotes", "--symbol", "RELIANCE")
        self.assertEqual(code, 0)
        self.assertIn("no daily bar saved", out)
        self.assertEqual(self.daily("RELIANCE"), [])

    def test_ohlc_with_date(self):
        code, out, _ = self.run_d("import-kite", os.path.join(DFIX, "kite-ohlc.json"), "--kind", "ohlc",
                                  "--date", "2026-10-23")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.daily("TCS")[-1]["close"], 2530.0)
        code, out, err = self.run_d("import-kite", os.path.join(DFIX, "kite-ohlc.json"), "--kind", "ohlc",
                                    "--now", "2026-10-23 11:00")
        self.assertEqual(code, 1)
        self.assertIn("nothing usable", err)


# ---------------------------------------------------------------- CSV imports

class ImportCsv(Net):
    def test_nse_website_export(self):
        code, out, _ = self.run_d("import-csv", os.path.join(DFIX, "nse-website-RELIANCE.csv"), "--symbol", "RELIANCE")
        self.assertEqual(code, 0, out)
        rows = self.daily("RELIANCE")
        self.assertEqual(rows[0]["date"], "2026-10-01")                     # newest-first file, sorted
        self.assertEqual(rows[-1], data.bar("2026-10-23", 1184, 1201.9, 1163.1, 1190, 14905641))

    def test_kite_console_export(self):
        code, out, _ = self.run_d("import-csv", os.path.join(DFIX, "kite-console-RELIANCE.csv"), "--symbol", "RELIANCE")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.daily("RELIANCE")), 15)

    def test_bhavcopy_file_picks_the_symbol(self):
        code, out, _ = self.run_d("import-csv", os.path.join(DFIX, "sec_bhavdata_full_23102026.csv"), "--symbol", "IOC")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.daily("IOC"), [data.bar("2026-10-23", 130, 130.31, 126.5, 129.09, 7142377)])

    def test_bad_rows(self):
        bad = os.path.join(DFIX, "bad-ohlc.csv")
        code, _, err = self.run_d("import-csv", bad, "--symbol", "RELIANCE")
        self.assertEqual(code, 1)
        self.assertIn("--drop-bad", err)
        code, out, _ = self.run_d("import-csv", bad, "--symbol", "RELIANCE", "--drop-bad")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.daily("RELIANCE")), 1)

    def test_missing_columns(self):
        p = os.path.join(self.tmp, "x.csv")
        wr(p, "Date,Price\n2026-10-23,1\n")
        code, _, err = self.run_d("import-csv", p, "--symbol", "RELIANCE")
        self.assertEqual(code, 1)
        self.assertIn("missing column", err)


# ---------------------------------------------------------------- doctor, record, status

class Doctor(Net):
    def test_one_source_is_enough(self):
        self.net([("query1", 200, fx("yahoo-daily-RELIANCE.json")), ("query2", 429, b""),
                  ("nseindia.com", 403, b"Access Denied"), ("niftyindices", 200, b"Company Name,Industry,Symbol\n"),
                  ("news.google.com", 200, b"<rss><item></item></rss>"), ("mcp.kite.trade", 405, b"")])
        code, out, _ = self.run_d("doctor", "--now", "2026-10-23 19:00")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.dd, "sources.json")) as fh:
            s = json.load(fh)
        self.assertEqual(s["price_sources_ok"], ["yahoo-q1"])
        self.assertTrue(s["sources"]["google-news"]["ok"])
        self.assertTrue(s["sources"]["kite-mcp"]["ok"])
        self.assertFalse(s["sources"]["nse-api"]["ok"])
        self.assertIn("403: NSE blocks this IP", s["sources"]["nse-api"]["error"])
        self.assertIn("429", s["sources"]["yahoo-q2"]["error"])
        for r in s["sources"].values():
            self.assertIn("latency_ms", r)
            self.assertIn("checked_at", r)
        self.assertIn("sec_bhavdata_full_23102026", s["sources"]["nsearchives"]["url"])

    def test_nothing_works(self):
        self.net([("", 403, b"")])
        code, out, _ = self.run_d("doctor")
        self.assertEqual(code, 1)
        self.assertIn("Kite MCP", out)

    def read_sources(self):
        with open(os.path.join(self.dd, "sources.json")) as fh:
            return json.load(fh)["sources"]

    def test_probes_both_calendar_endpoints(self):
        f = self.net([("api/holiday-master", 200, fx("nse-holiday-master.json")),
                      ("api/circulars", 200, fx("nse-circulars-cmtr.json")), ("www.nseindia.com/", 200, b"home"),
                      ("", 403, b"")])
        self.run_d("doctor", "--now", "2026-10-23 19:00")
        s = self.read_sources()
        self.assertTrue(s["nse-holidays"]["ok"])
        self.assertTrue(s["nse-circulars"]["ok"])
        self.assertEqual(s["nse-holidays"]["kind"], "cal")
        self.assertIn("from_date=16-10-2026&to_date=23-10-2026&dept=CMTR", f.urls("api/circulars")[0])
        hdr = [h for u, h in f.calls if "holiday-master" in u][0]
        self.assertEqual(hdr["Accept"], "application/json")
        self.assertIn("exchange-communication-holidays", hdr["Referer"])

    def test_a_changed_calendar_shape_is_reported(self):
        self.net([("api/holiday-master", 200, b'{"FO": []}'), ("api/circulars", 200, b"<html>wall</html>"),
                  ("", 403, b"")])
        code, out, _ = self.run_d("doctor")
        s = self.read_sources()
        self.assertFalse(s["nse-holidays"]["ok"])
        self.assertIn("no CM holiday list", s["nse-holidays"]["error"])
        self.assertFalse(s["nse-circulars"]["ok"])
        self.assertIn("not JSON", s["nse-circulars"]["error"])
        self.assertEqual(code, 1)


class RecordStatus(Net):
    def test_record_from_cache(self):
        self.net([("query1", 200, fx("yahoo-5m-RELIANCE.json"))])
        self.run_d("intraday", "RELIANCE")
        self.run_d("import-csv", os.path.join(DFIX, "nse-website-RELIANCE.csv"), "--symbol", "RELIANCE")
        self.run_d("import-kite", os.path.join(DFIX, "kite-quotes.json"), "--kind", "quotes", "--now",
                   "2026-10-23 16:10")
        out_dir = os.path.join(self.tmp, "real")
        code, out, err = self.run_d("record", "RELIANCE", "--no-fetch", "--out", out_dir, "--date", "2026-10-23")
        self.assertEqual(code, 0, out + err)
        d = os.path.join(out_dir, "RELIANCE")
        self.assertEqual(sorted(os.listdir(d)), ["bands.json", "daily.csv", "intraday-5m-2026-10-23.json",
                                                 "provenance.json"])
        p = json.loads(rd(os.path.join(d, "provenance.json")))
        self.assertTrue(p["fetched_at"])
        self.assertIn("yahoo", p["source"])
        # a recorded folder passes the same checks as the downloaded real data
        rows, _ = data.parse_ohlc_csv(rd(os.path.join(d, "daily.csv")), "RELIANCE")
        self.assertEqual(data.validate_daily(rows)[0], [])

    def test_record_nothing(self):
        code, _, err = self.run_d("record", "ZZZZ", "--no-fetch", "--out", os.path.join(self.tmp, "r"))
        self.assertEqual(code, 1)
        self.assertIn("nothing cached", err)

    def test_status(self):
        self.seed("RELIANCE", "2026-10-16")
        code, out, _ = self.run_d("status", "--now", "2026-10-23 19:00")
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE.csv", out)
        self.assertIn("4 session(s) behind", out)


class Style(unittest.TestCase):
    def test_no_em_dash_and_skill_frontmatter(self):
        for f in ("data.py", "SKILL.md"):
            with open(os.path.join(ROOT, "trader-data", f), encoding="utf-8") as fh:
                self.assertNotIn("\u2014", fh.read(), f)
        text = rd(os.path.join(ROOT, "trader-data", "SKILL.md"))
        self.assertTrue(text.startswith("---\nname: trader-data\n"))
        fm = text.split("\n---\n", 1)[0]
        self.assertGreater(len(fm), 200)
        self.assertGreater(len(text.splitlines()), 60)
        for tool in ("get_historical_data", "get_quotes", "get_ohlc", "import-kite", "import-csv"):
            self.assertIn(tool, text)


if __name__ == "__main__":
    unittest.main()
