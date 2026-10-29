import csv
import datetime
import io
import json
import os
import shutil
import tempfile
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
        self._t, self._s = data._transport, data._sleep
        data._sleep = self.sleeps.append

    def tearDown(self):
        data._transport, data._sleep = self._t, self._s
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
