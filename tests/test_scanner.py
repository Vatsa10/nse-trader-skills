import datetime
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

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
# All fixtures are SYNTHETIC NSE data: real US bar shapes re-timed so the open is 09:15 IST and
# renamed to NSE symbols (RELIANCE, ITC, INFY, M&M ...). Prices are not real NSE prices.
WALL = os.path.join(SDIR, "wall5m-2026-10-19.json.gz")       # 234 five-minute charts, Mon Oct 19 2026
DAILY = os.path.join(SDIR, "daily-spark-2026-10-19.json")    # 19 daily charts as one spark answer
LIVE = os.path.join(SDIR, "spark-3mo-live-2026-10-21.json")  # a 20-symbol spark answer (19 come back)
LISTS = os.path.join(SDIR, "lists")                          # trimmed ind_nifty500list.csv and EQUITY_L.csv
SPARK5 = os.path.join(SDIR, "spark-5m-live-2026-10-21-0946.json")     # as of 09:46:47 IST Wed Oct 21
REL5 = os.path.join(SDIR, "RELIANCE-5m-live-2026-10-21-0946.json")    # its v8 chart, with it
DT = datetime.datetime
D = datetime.date


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """India wall-clock text -> unix seconds (IST is a fixed UTC+05:30)."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                      else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


class Universe(unittest.TestCase):
    def texts(self):
        out = {}
        for f in (scan.NIFTY500_FILE, scan.EQUITY_FILE):
            with open(os.path.join(LISTS, f), encoding="utf-8") as fh:
                out[f] = fh.read()
        return out

    def test_nifty500_is_the_default(self):
        kept, counts, used = scan.build_universe(self.texts())
        self.assertEqual(used, "nifty500")
        self.assertEqual([s for s, _ in kept], ["BAJAJ-AUTO", "BEL", "HDFCBANK", "HINDCOPPER", "INFY", "ITC", "M&M",
                                                "RELIANCE", "TCS"])
        self.assertEqual(counts, {"rows": 10, "keep": 9, "not an NSE symbol": 1})
        self.assertEqual(dict(kept)["M&M"], "Mahindra & Mahindra Ltd.")

    def test_equity_l_keeps_eq_series_only(self):
        kept, counts, used = scan.build_universe(self.texts(), "equity")
        self.assertEqual(used, "equity")
        self.assertEqual([s for s, _ in kept], ["BAJAJ-AUTO", "HDFCBANK", "HINDCOPPER", "M&M", "NIFTYBEES",
                                                "RELIANCE"])
        self.assertEqual(counts["not EQ series"], 2)                # SM (SME) and BE rows
        # no EQUITY_L.csv: the Nifty 500 list is used instead
        t = self.texts()
        del t[scan.EQUITY_FILE]
        self.assertEqual(scan.build_universe(t, "equity")[2], "nifty500")

    def test_not_a_list(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_nifty500("<html>Access Denied</html>")
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_l("Company Name,Symbol\nX,Y\n")


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(scan.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(scan.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(scan.yahoo_symbol("NIFTY"), "%5ENSEI")
        self.assertEqual(scan.from_yahoo("M&M.NS"), "M&M")
        self.assertEqual(scan.from_yahoo("^NSEBANK"), "BANKNIFTY")

    def test_check_symbol(self):
        self.assertEqual(scan.check_symbol("hindcopper.ns"), "HINDCOPPER")
        for bad in ("NIFTY26OCT25000CE", "RELIANCE26OCTFUT", "BAD SYMBOL", "TOO$"):
            with self.assertRaises(scan.ScanError):
                scan.check_symbol(bad)

    def test_inr(self):
        self.assertEqual(scan.inr(12345678.9), "\u20b91,23,45,678.90")
        self.assertEqual(scan.inr(999.5), "\u20b9999.50")
        self.assertEqual(scan.inr(-100000), "-\u20b91,00,000.00")
        self.assertEqual(scan.indian(234), "234")
        self.assertEqual(scan.indian(1234567), "12,34,567")


class Time(unittest.TestCase):
    def test_ist_clock(self):
        self.assertEqual(scan.ist_offset(), 5.5)                   # no DST, summer or winter
        self.assertEqual(scan.ts_to_ist(1792381500), DT(2026, 10, 19, 9, 15))   # RELIANCE's first bar
        self.assertEqual(scan.ts_to_ist(ts("2026-12-01 09:15")), DT(2026, 12, 1, 9, 15))
        self.assertEqual(scan.parse_now("2026-10-19 10:00"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.parse_now("2026-12-01 10:00"), DT(2026, 12, 1, 4, 30))
        self.assertEqual(scan.parse_now("2026-10-19T04:30:00Z"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 10, 0)), "10:00 AM")
        self.assertEqual(scan.clock_label(datetime.time(13, 5)), "1:05 PM")
        self.assertEqual((scan.OPEN_MIN, scan.CLOSE_MIN, scan.SESSION_BARS), (555, 930, 75))
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_nse_calendar(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali Balipratipada"))
        self.assertEqual(scan.trading_day(D(2026, 10, 19)), (True, ""))
        self.assertEqual(scan.session(D(2026, 12, 24)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 11, 8)))            # Muhurat Sunday: treated as closed
        self.assertIn("nseindia.com", scan.calendar_note(D(2027, 1, 4)))
        self.assertEqual(scan.calendar_note(D(2026, 1, 5)), "")


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        with open(LIVE, encoding="utf-8") as fh:
            text = fh.read()
        wanted = {scan.to_yahoo(s): s for s in ["INFY", "RELIANCE", "M&M", "ZZZZQX"]}
        data = scan.parse_spark(text, wanted)
        self.assertEqual(len(data), 19)            # 20 asked; Yahoo silently drops the made-up ZZZZQX
        self.assertNotIn("ZZZZQX", data)
        self.assertIn("M&M", data)               # M&M comes back as ours
        t = data["RELIANCE"]
        self.assertEqual(len(t["t"]), 65)
        self.assertIsNone(t["h"])                  # spark is closes only
        self.assertEqual(t["c"][-1], 368.06)
        self.assertEqual(t["meta"]["regularMarketVolume"], 38651173)
        self.assertEqual(data["IDEA"]["meta"]["regularMarketPrice"], 2.98)
        closes = scan.daily_closes(t, scan.parse_now("2026-10-21 08:59"))
        self.assertEqual(closes[-1], (D(2026, 10, 19), 368.06))
        # the same answer read during Monday's session: Monday's bar is still in progress
        closes = scan.daily_closes(t, scan.parse_now("2026-10-19 11:45"))
        self.assertEqual(closes[-1][0], D(2026, 10, 16))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')

    def test_urls(self):
        self.assertEqual(scan.spark_path(["M&M", "reliance", "BAJAJ-AUTO"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,RELIANCE.NS,BAJAJ-AUTO.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("RELIANCE", "1d", "5m"), "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(LIVE, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com",
                                      "https://query1.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query2_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = ["INFY", "RELIANCE", "TCS", "M&M", "IDEA", "ZZZZQX", "HDFCBANK", "ICICIBANK", "MANAPPURAM", "APOLLOTYRE", "FORTIS", "ULTRACEMCO",
                "TRENT", "KEI", "COALINDIA", "ITC", "SUZLON", "MCDOWELL-N", "SONATSOFTW", "HUDCO"] + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-21 08:59"), 5, 1000000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    # Hand-checked from the closes (see the numbers in the comments): through Fri Oct 16 2026.
    EXPECT = {
        "BAJFINANCE": ["breakout_20d"],    # 1134.38 > 1120.78, the best close of the 20 before
        "TATACONSUM": ["breakout_20d"],     # 302.30 > 298.81
        "GMRAIRPORT": ["breakout_20d"],    # 178.98 > 177.01
        "BHARATFORG": ["breakout_20d"],     # 309.63 > 308.24
        "ITC": ["breakout_20d"],     # 93.90 > 92.00 (a closing high; its intraday high was not taken out)
        "IOC": ["bull_flag"],       # up 18.4 % over the 10 sessions, last 5 closes in a 3.2 % range
        "LTTS": ["bull_flag"],       # up 16.3 %, 2.6 % range
        "KEI": ["reclaim_ma20"],      # 12.31 under 12.41, then 12.43 over 12.37
        "DELHIVERY": ["reclaim_ma20"],    # 335.53 under 336.15, then 339.72 over 335.65
        "BOSCHLTD": ["reclaim_ma20"],     # 312.00 under 312.90, then 313.83 over 312.88
        "COALINDIA": ["oversold"],        # RSI 24.0
        "SUZLON": ["oversold"],       # RSI 21.0
        "SBICARD": ["oversold"],         # RSI 25.5
        "SONATSOFTW": ["oversold"],       # RSI 22.7
        "METROPOLIS": ["oversold_bounce"],  # RSI 27.1 on Thursday, closed up Friday (214.94 -> 217.39), RSI 34.0
    }
    # The reel's refs/scan.py tags for the same charts (it used highs and lows): all reproduced.
    REEL = {"BAJFINANCE": "breakout_20d", "TATACONSUM": "breakout_20d", "GMRAIRPORT": "breakout_20d", "BHARATFORG": "breakout_20d",
            "IOC": "bull_flag", "KEI": "reclaim_ma20", "DELHIVERY": "reclaim_ma20", "BOSCHLTD": "reclaim_ma20",
            "COALINDIA": "oversold", "SUZLON": "oversold", "SBICARD": "oversold", "SONATSOFTW": "oversold"}

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 19)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 08:45"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 10, 16))
        self.assertEqual(counts, {"requested": 19, "fetched": 19, "illiquid": 0, "short": 0, "scanned": 19,
                                  "hits": 15})
        for sym, tag in self.REEL.items():
            self.assertIn(tag, self.EXPECT[sym], sym)
        blk = [h for h in hits if h["symbol"] == "BAJFINANCE"][0]
        self.assertEqual((blk["price"], blk["rsi"], blk["close_date"]), (1134.38, 63.7, "2026-10-16"))

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 08:45"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:35"), 0, 0)
        self.assertEqual(before, during)
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-19 15:29"))
        self.assertEqual(bars[-1], (D(2026, 10, 16), 354.7200012207031))
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-19 15:30"))
        self.assertEqual(bars[-1], (D(2026, 10, 19), 365.7950134277344))

    def test_no_early_close_on_nse(self):
        # Thu Dec 24 2026 is a full NSE session: the daily bar completes at 15:30, not earlier
        s = {"t": [ts("2026-12-23 09:15"), ts("2026-12-24 09:15")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-12-24 15:29"))), 1)
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-12-24 15:30"))), 2)

    def test_liquidity_filter(self):
        # meta is Yahoo's as of 10:35 AM Monday, so the volume is the day's so far
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:35"), 5, 1000000)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["COALINDIA", "DELHIVERY", "GMRAIRPORT", "IOC", "ITC", "KEI", "SONATSOFTW"])
        self.assertEqual(counts["illiquid"], 8)     # SUZLON under ₹5; BAJFINANCE, TATACONSUM, BHARATFORG, LTTS, BOSCHLTD, SBICARD, METROPOLIS under 1M so far
        self.assertEqual(counts["scanned"], 11)

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [10.0] * 10,
             "meta": {"regularMarketPrice": 10, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-21 08:45"), 5, 1000000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_the_reel_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(15, 15))
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_scan_at_1000(self):
        hits, counts, failed = self.scan_at("2026-10-19 10:00")
        self.assertEqual(len(self.syms), 234)
        self.assertEqual(counts["scanned"], 231)          # usable charts
        self.assertEqual(len(hits), 42)
        self.assertEqual(self.errors, {"DHFL": "no data for DHFL: No data found, symbol may be delisted"})
        self.assertEqual(counts["unusable"], 2)            # ZEEL (no bars), PVRINOX (missing bars)
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual(rel, {"symbol": "RELIANCE", "setups": ["orb"], "price": 364.18, "at": "10:00 AM",
                                "bar_start": "09:55", "bar_index": 8, "or_high": 362.18, "or_low": 354.47,
                                "pct_over": 0.55})
        self.assertEqual(hits[0]["symbol"], "BLUESTARCO")   # sorted by time, then symbol
        self.assertEqual([h["at"] for h in hits], sorted([h["at"] for h in hits], key=lambda x: scan.parse_hhmm(
            x[:-3]).hour % 12 * 60 + int(x[-5:-3])))
        self.assertEqual(sum(1 for h in hits if h["at"] == "10:00 AM"), 11)
        lly = [h for h in hits if h["symbol"] == "MARUTI"][0]
        self.assertEqual((lly["price"], lly["or_high"], lly["pct_over"]), (1255.0, 1254.99, 0.0))

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:00", min_price=5)
        self.assertEqual(len(hits), 41)                    # IDEA at ₹2.97 drops out
        self.assertEqual(counts["illiquid"], 9)
        self.assertNotIn("IDEA", [h["symbol"] for h in hits])

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-19 09:59", syms=["RELIANCE"])
        self.assertEqual(hits, [])                         # the 09:55 bar is not done until 10:00
        hits, _, _ = self.scan_at("2026-10-19 10:00", syms=["RELIANCE"])
        self.assertEqual(hits[0]["at"], "10:00 AM")

    def test_the_live_tick_is_dropped(self):
        s = self.data["RELIANCE"]
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 10, 19, 10, 43, 7))   # off the grid
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 10, 44))
        self.assertEqual(len(bars), 17)                    # 9:15 to 10:35; 10:40 in progress, the tick dropped
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 10, 44), 3))
        hits, _, _ = self.scan_at("2026-10-19 10:45", syms=["RELIANCE"])
        self.assertEqual(hits[0]["price"], 364.18)         # still the first close above the range

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 10, 19), DT(2026, 10, 19, 10, 0), 15,
                                             0, 0, confirm)
        self.assertEqual(len(hits), 42)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), 231)                   # stage 1 skipped most charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE", "ITC"], D(2026, 10, 19),
                                             DT(2026, 10, 19, 10, 0), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-21", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        bars = scan.session_bars(s, D(2026, 10, 21), DT(2026, 10, 21, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 21), DT(2026, 10, 21, 10, 0), 3))

    def test_a_full_75_bar_session_and_a_30_minute_range(self):
        # Mon Dec 7 2026: IST all year (no DST), 09:15 to 15:30, 75 five-minute bars
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 80
        s = bars_series("2026-12-07", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], D(2026, 12, 7), DT(2026, 12, 7, 11, 45), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")         # range 9:15 to 9:45 (high 101.4), the 9:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 12, 7, 9, 15))
        bars = scan.session_bars(s, D(2026, 12, 7), DT(2026, 12, 7, 15, 30))
        self.assertEqual(len(bars), 75)                    # 09:15 .. 15:25; bars from 15:30 on are dropped
        self.assertEqual(bars[-1]["start"], DT(2026, 12, 7, 15, 25))
        self.assertTrue(scan.usable(bars, D(2026, 12, 7), DT(2026, 12, 7, 15, 30), 6))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-12-07 15:40"), None, 15, datetime.time(15, 50))
        self.assertIn("closed at 3:30 PM", str(cm.exception))

    def test_window_messages(self):
        until = datetime.time(15, 15)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-17 10:00"))
        self.assertIn("NSE holiday: Dussehra", msg("2026-10-20 10:00"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:25"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:32"))
        self.assertIn("expired", msg("2026-10-19 15:20"))
        self.assertIn("expired", msg("2026-10-19 11:15", until=datetime.time(10, 45)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-19 16:15"))
        self.assertIn("later than now", msg("2026-10-19 09:45", at=datetime.time(10, 0)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-19 16:45", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:15"), datetime.time(10, 0), 15, until),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, until)[1],
                         DT(2026, 10, 19, 9, 35))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:00"), None, 7, until)


class LiveFiveMinute(unittest.TestCase):
    """A spark 5-minute answer and RELIANCE's v8 chart as of 09:46:47 IST on Wed Oct 21 2026 (synthetic)."""

    def setUp(self):
        with open(SPARK5, encoding="utf-8") as fh:
            self.spark = scan.parse_spark(fh.read())
        with open(REL5, encoding="utf-8") as fh:
            self.v8 = scan.parse_chart(fh.read(), "RELIANCE")
        self.now = scan.parse_now("2026-10-21 09:46:48")

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        self.assertEqual(len(self.spark), 20)
        self.assertIn("M&M", self.spark)
        t = self.spark["RELIANCE"]
        self.assertEqual(len(t["t"]), 8)                   # 9:15 ... 09:45 (in progress) + the 09:46:47 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 10, 21, 9, 46, 47))
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 15))
        self.assertEqual(cut, DT(2026, 10, 21, 9, 46))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars], ["09:15", "09:20", "09:25", "09:30",
                                                                         "09:35", "09:40"])
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 6)

    def test_reliance_broke_its_range_at_945(self):
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 15))
        hits, counts, failed = scan.scan_orb(self.spark, ["RELIANCE", "IDEA"], day, cut, 15, 5, 0, lambda s: self.v8)
        # range 9:15 to 9:30: high 370.49 (the 9:15 bar), low 363.64; the 9:40 bar closed 370.585
        self.assertEqual(hits, [{"symbol": "RELIANCE", "setups": ["orb"], "price": 370.58, "at": "9:45 AM",
                                 "bar_start": "09:40", "bar_index": 5, "or_high": 370.49, "or_low": 363.64,
                                 "pct_over": 0.03}])
        self.assertEqual(counts["illiquid"], 1)            # IDEA under the ₹5 floor

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=10)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (10, counts["candidates"] - 10))
        self.assertIn("RELIANCE", asked)                       # the most dollars traded by 10:00
        self.assertLessEqual(len(hits), 10)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 42)
        for h in hits:
            self.assertIn(h, hits_all)


class V4Cache(unittest.TestCase):
    """v4: the trader-data caches, the universe fallbacks and the long-term screen. The daily CSVs here are
    SYNTHETIC (made up in the test); the intraday cache files are charts from the wall fixture."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.saved = (scan._fetch_list, scan.yahoo_get, scan.SLEEP)
        scan.SLEEP = lambda s: None

    def tearDown(self):
        scan._fetch_list, scan.yahoo_get, scan.SLEEP = self.saved
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def blocked(self):
        def boom(*a, **k):
            raise scan.ScanError("Yahoo answered HTTP 403 for /v7/finance/spark")
        scan.yahoo_get = boom

        def no_list(url, what):
            raise scan.ScanError("%s answered HTTP 403" % what)
        scan._fetch_list = no_list

    def daily_csv(self, sym, closes, volume=500000, start=D(2025, 9, 1), adj=False):
        os.makedirs(os.path.join("trader", "data", "daily"), exist_ok=True)
        day, rows = start, []
        for c in closes:
            while day.weekday() >= 5:
                day += datetime.timedelta(days=1)
            rows.append("%s,%.2f,%.2f,%.2f,%.2f,%d" % (day.isoformat(), c, c * 1.01, c * 0.99, c, volume))
            day += datetime.timedelta(days=1)
        name = "%s.adj.csv" % sym if adj else "%s.csv" % sym
        with open(os.path.join("trader", "data", "daily", name), "w") as fh:
            fh.write("date,open,high,low,close,volume\n" + "\n".join(rows) + "\n")
        return rows[-1].split(",")[0]

    # ---- universe

    def test_the_bundled_snapshot_is_a_real_nifty_500(self):
        with open(scan.SNAPSHOT_FILE, encoding="utf-8") as fh:
            kept, counts, used = scan.build_universe({scan.NIFTY500_FILE: fh.read()})
        syms = [k for k, _ in kept]
        self.assertGreaterEqual(len(syms), 490)
        for s_ in ("RELIANCE", "HDFCBANK", "TCS", "M&M", "BAJAJ-AUTO", "HINDCOPPER"):
            self.assertIn(s_, syms)
        info = scan.snapshot_info()
        self.assertEqual(info["as_of"], "2026-07-13")
        self.assertIn("niftyindices", info["upstream"])

    def test_universe_blocked_uses_the_snapshot(self):
        self.blocked()
        code, text, err = run(["universe", "--out", "u.txt", "--pause", "0"])
        self.assertEqual(code, 0, err)
        self.assertIn("niftyindices.com failed", text)
        self.assertIn("bundled offline Nifty 500 snapshot", text)
        self.assertIn("2026-07-13", text)
        with open("u.txt") as fh:
            self.assertGreaterEqual(len(fh.read().splitlines()), 490)

    def test_universe_prefers_trader_data_universe_csv(self):
        self.blocked()
        os.makedirs(os.path.join("trader", "data"))
        with open(os.path.join("trader", "data", "universe.csv"), "w") as fh:
            fh.write("symbol,name\nRELIANCE,Reliance Industries\nTCS,Tata Consultancy\nNOT A SYMBOL,x\n")
        code, text, _ = run(["universe", "--out", "u.txt", "--json"])
        doc = json.loads(text)
        self.assertEqual(doc["count"], 2)
        self.assertIn("trader/data/universe.csv", doc["note"])
        code, text, _ = run(["universe", "--cache", "--no-write"])
        self.assertIn("Kept 2 NSE equities", text)

    # ---- daily

    def test_daily_cache_flag(self):
        up = [100 + i * 0.1 for i in range(59)] + [110.0]
        last = self.daily_csv("ITC", up)
        self.daily_csv("TCS", [200.0] * 60)
        code, text, err = run(["daily", "--cache", "--symbols", "ITC,TCS,HDFCBANK", "--now", "2026-12-01 08:30",
                               "--no-write"])
        self.assertEqual(code, 0, err)
        self.assertIn("trader-data cache", text)
        self.assertIn("using cached data from %s for 2 symbols" % last, text)
        self.assertIn("ITC", text)
        self.assertIn("20-day closing-high breakout", text)

    def test_daily_yahoo_failing_falls_back_and_says_so(self):
        self.blocked()
        self.daily_csv("ITC", [100 + i * 0.1 for i in range(59)] + [110.0])
        code, text, err = run(["daily", "--symbols", "ITC", "--now", "2026-12-01 08:30", "--json", "--no-write"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(doc["source"], "cache")
        self.assertIn("Yahoo failed: using cached data", doc["cache_note"])
        code, _, err = run(["daily", "--symbols", "TCS", "--now", "2026-12-01 08:30", "--no-write"])
        self.assertEqual(code, 1)
        for must in ("HTTP 403", "data.py doctor", "get_historical_data", "import-kite"):
            self.assertIn(must, err)

    def test_no_universe_file_reads_the_snapshot(self):
        self.daily_csv("RELIANCE", [100.0 + (i % 7) for i in range(60)])
        code, text, err = run(["daily", "--cache", "--limit", "600", "--now", "2026-12-01 08:30", "--no-write"])
        self.assertIn("bundled offline Nifty 500 snapshot", text)
        self.assertIn("1 symbol", text)

    # ---- orb

    def test_orb_reads_the_intraday_cache(self):
        bundle = json.loads(scan._read_maybe_gz(WALL))
        syms = sorted(bundle)[:40]
        os.makedirs(os.path.join("trader", "data", "intraday"))
        for k in syms:
            with open(os.path.join("trader", "data", "intraday", "%s-5m.json" % scan.from_yahoo(k)), "w") as fh:
                json.dump(bundle[k], fh)
        names = ",".join(scan.from_yahoo(k) for k in syms)
        want = run(["orb", "--offline", WALL, "--symbols", names, "--now", "2026-10-19 10:00", "--min-price", "0",
                    "--json", "--no-write"])[1]
        code, text, err = run(["orb", "--cache", "--symbols", names, "--now", "2026-10-19 10:00", "--min-price", "0",
                               "--json", "--no-write"])
        got = json.loads(text)
        self.assertEqual(got["hits"], json.loads(want)["hits"])
        self.assertEqual(got["source"], "cache")
        self.assertIn("using cached data from 2026-10-19", got["cache_note"])
        self.assertIn("trader-data/data.py intraday SYMBOL", got["cache_note"])
        # Yahoo blocked on the day itself: stage 1 and stage 2 come from the cache
        self.blocked()
        real = scan.parse_now
        scan.parse_now = lambda text: real(text or "2026-10-19 10:00")
        try:
            code, text, err = run(["orb", "--symbols", names, "--now", "2026-10-19 10:00", "--min-price", "0",
                                   "--json", "--no-write"])
        finally:
            scan.parse_now = real
        got = json.loads(text)
        self.assertEqual(got["hits"], json.loads(want)["hits"])
        self.assertIn("Yahoo failed: using cached data", got["cache_note"])

    # ---- long-term

    def test_longterm_screens(self):
        self.daily_csv("UP", [100 + i * 0.4 for i in range(260)])                       # trend, at the high
        self.daily_csv("DOWN", [300 - i * 0.6 for i in range(250)] + [151, 150.5, 150, 149.6, 149.2])
        self.daily_csv("SHORT", [100.0] * 120)
        self.daily_csv("THIN", [100 + i * 0.4 for i in range(260)], volume=500)
        self.daily_csv("ADJ", [50.0] * 260)
        self.daily_csv("ADJ", [100 + i * 0.4 for i in range(260)], adj=True)          # the .adj.csv wins
        code, text, err = run(["longterm", "--now", "2026-12-01 08:30", "--json", "--no-write"])
        doc = json.loads(text)
        hits = {h["symbol"]: h for h in doc["hits"]}
        self.assertEqual(sorted(hits), ["ADJ", "DOWN", "UP"])
        self.assertEqual(hits["UP"]["setups"], ["above_200dma", "near_52w_high"])
        self.assertEqual(hits["ADJ"]["setups"], ["above_200dma", "near_52w_high"])
        self.assertIn("near_52w_low", hits["DOWN"]["setups"])
        self.assertIn("oversold", hits["DOWN"]["setups"])
        self.assertLess(hits["DOWN"]["above_low_pct"], 2)
        self.assertLess(hits["DOWN"]["vs_200dma_pct"], 0)
        self.assertEqual(doc["counts"]["short"], 1)
        self.assertEqual(doc["counts"]["illiquid"], 1)
        self.assertEqual(doc["source"], "cache")
        code, text, _ = run(["longterm", "--screen", "near_52w_low", "--now", "2026-12-01 08:30", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("DOWN", text)
        self.assertNotIn("  UP ", text)
        self.assertIn("near the 52-week low (value screen)", text)
        self.assertIn("using cached data from", text)
        self.assertIn("Not financial advice", text)
        self.assertNotIn("\u2014", text)
        m = scan.longterm_metrics(scan.read_daily_csv(os.path.join("trader", "data", "daily", "UP.csv")))
        self.assertAlmostEqual(m["dma200"], sum(100 + i * 0.4 for i in range(60, 260)) / 200, places=6)
        self.assertAlmostEqual(m["high52"], (100 + 259 * 0.4) * 1.01, places=1)

    def test_longterm_without_a_cache_says_what_to_run(self):
        code, _, err = run(["longterm", "--symbols", "RELIANCE", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("trader-data/data.py daily", err)
        self.assertIn("import-kite", err)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", LISTS, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("Nifty 500 constituents: 10 rows.", text)
        self.assertIn("Kept 9 NSE equities", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 9)
        self.assertIn("M&M|Mahindra & Mahindra Ltd.", lines)
        code, text, _ = run(["universe", "--offline", LISTS, "--source", "equity", "--no-write", "--json"])
        self.assertEqual((json.loads(text)["source"], json.loads(text)["count"]), ("equity", 6))

        # daily over that universe with the fixture charts: only some are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-19 08:45",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 4 of 9 charts (5 failed or had no data)", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 9, 4, 5))
        self.assertEqual(doc["hits"], [{"symbol": "ITC", "setups": ["breakout_20d"], "price": 93.9,
                                        "close_date": "2026-10-16", "rsi": 75.3, "volume": 3680307}])
        self.assertEqual(doc["as_of"], "2026-10-16 close")

        # the 10:00 IST scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--min-price", "0",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("42 of 231 charts closed above their opening-range high by 10:00 AM", text)
        self.assertIn("10:00 AM  RELIANCE          ₹364.18  range 354.47 to 362.18  +0.55% over", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:30:00Z", "mode": "orb",
                          "as_of": "2026-10-19 10:00 IST", "universe": 234, "scanned": 231, "source": "yahoo"})
        self.assertEqual(len(doc["hits"]), 42)
        self.assertIn({"symbol": "RELIANCE", "setups": ["orb"], "price": 364.18, "at": "10:00 AM", "bar_start": "09:55",
                       "bar_index": 8, "or_high": 362.18, "or_low": 354.47, "pct_over": 0.55}, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:00", "--watchlist", self.wl, "--min-price", "0",
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (42, 42, 42))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo (SYMBOL.NS paths) that answers from the Oct 19 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    r = ((bundle.get(scan.from_yahoo(y)) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": y, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[scan.from_yahoo(path.split("/chart/")[1].split("?")[0])])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:00", "--min-price", "0",
                                 "--max-confirm", "10", "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("231 usable; 95 candidates, 10 confirmed", text)
        self.assertIn("PARTIAL: 85 more candidates were not checked", text)
        self.assertIn("5 of the 10 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 12)   # 234 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-17 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:29", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "09:45"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:55", "--symbols", "RELIANCE",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:55 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        data, _ = scan.load_offline(WALL)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("RELIANCE", "INFY", "ITC", "DHFL"):
            with open(os.path.join(folder, sym + ".json"), "w") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["INFY", "RELIANCE"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nRELIANCE|Reliance Industries Ltd.\nitc.ns\n\nRELIANCE\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE", "ITC", "RELIANCE"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "reliance"}, {"symbol": "ITC"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["RELIANCE", "ITC"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-19 08:45",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-19 08:45", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 08:45", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 15)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")


# SwingTrend-style engine, hand-checkable series (high, low, close), see the spec's branch catalogue
SERIES_A = [(10, 8, 9), (11, 9, 10.5), (12, 10, 11.8), (11.5, 10.5, 11), (11.2, 10.2, 10.4), (12.6, 11, 12.4),
            (12.4, 11.6, 11.8), (11.9, 9.8, 10.0)]
SERIES_B = [(12, 10, 11), (11, 9, 9.5), (10, 8, 8.2), (9.5, 8.5, 9), (9.8, 8.8, 9.6), (9, 7.4, 7.6),
            (8.4, 7.6, 8.2), (10.2, 8.1, 10.0)]


def swing_state(trend, retrace=0, **kw):
    s = scan.swing_new(retrace, 20, 40)
    s.update(trend=trend, **kw)
    return s


class Swing(unittest.TestCase):
    def test_no_trend_yet(self):
        s = scan.swing_new(0)
        self.assertIsNone(scan.swing_step(s, 20, 18, 19))                       # P1
        self.assertEqual((s["hi"], s["lo"], s["trend"]), (20, 18, None))
        self.assertIsNone(scan.swing_step(s, 21, 19, 19.5))                     # P2: the range widens
        self.assertEqual((s["hi"], s["lo"], s["trend"]), (21, 18, None))
        s = swing_state(None, hi=20, lo=18)
        self.assertEqual(scan.swing_step(s, 19, 17, 17.5), "start_down")        # P3
        self.assertEqual((s["trend"], s["lo"], s["coc"]), ("down", 17, 20))
        s = swing_state(None, hi=20, lo=18)
        self.assertEqual(scan.swing_step(s, 22, 19, 21), "start_up")            # P4
        self.assertEqual((s["trend"], s["hi"], s["coc"]), ("up", 22, 18))
        s = swing_state(None, hi=20, lo=18)
        self.assertEqual(scan.swing_step(s, 23, 16, 22), "start_up")            # P5: an outside bar
        self.assertEqual((s["hi"], s["coc"], s["lo"]), (23, 18, 16))
        s = scan.swing_new(0)
        self.assertIsNone(scan.swing_step(s, None, 1, 1))                       # a bar with a hole does not count
        self.assertEqual(s["total"], 0)

    def test_uptrend_branches(self):
        s = swing_state("up", hi=30, lo=27, coc=20)
        self.assertIsNone(scan.swing_step(s, 32, 29, 31))                       # U1: new high, no pivot
        self.assertEqual((s["hi"], s["lo"], s["bars_since"], s["pivot"]), (32, 29, 0, None))
        s = swing_state("up", hi=30, lo=27, coc=20)
        scan.swing_step(s, 29.5, 28, 28.5)                                      # U2: a pivot is born
        self.assertEqual((s["pivot"], s["lo"], s["bars_since"], s["trend"]), (30, 28, 1, "up"))
        s = swing_state("up", hi=30, lo=27, coc=20)
        scan.swing_step(s, 30, 28, 29)                                          # U3: an equal high counts as failing
        self.assertEqual((s["pivot"], s["lo"]), (30, 28))
        s = swing_state("up", hi=30, lo=28, coc=20, pivot=30, bars_since=1)
        scan.swing_step(s, 29, 27, 27.5)                                        # U4
        self.assertEqual((s["bars_since"], s["lo"]), (2, 27))
        s = swing_state("up", hi=30, lo=28, coc=20, pivot=30, bars_since=4)
        self.assertIsNone(scan.swing_step(s, 31, 29, 29.5))                     # U5: a poke is not a break
        self.assertEqual((s["hi"], s["bars_since"], s["pivot"]), (31, 0, 30))
        s = swing_state("up", hi=30, lo=27, coc=20, pivot=30, legs=2)
        self.assertEqual(scan.swing_step(s, 32, 29, 31), "bos")                 # U6
        self.assertEqual((s["coc"], s["legs"], s["pivot"], s["hi"], s["bars_since"], s["last_bos"]),
                         (27, 3, None, 32, 0, 30))
        s = swing_state("up", 15, hi=30, lo=27, coc=20, pivot=30, legs=2)
        self.assertIsNone(scan.swing_step(s, 32, 29, 31))                       # U7: the retrace filter ignores it
        self.assertEqual((s["coc"], s["legs"], s["pivot"], s["hi"], s["bars_since"]), (20, 2, None, 32, 0))
        scan.swing_step(s, 31, 29, 30)                                          # U9: a fresh pivot at the higher high
        self.assertEqual((s["pivot"], s["lo"], s["bars_since"]), (32, 29, 1))
        s = swing_state("up", 10, hi=30, lo=27, coc=20, pivot=30)
        self.assertEqual(scan.swing_step(s, 32, 29, 31), "bos")                 # exactly the retrace confirms
        s = swing_state("up", hi=30, lo=28, coc=20, pivot=30, bars_since=1)
        self.assertIsNone(scan.swing_step(s, 31, 29, 30))                       # U8: a close equal to the pivot
        self.assertEqual(s["pivot"], 30)
        s = swing_state("up", hi=30, lo=28, coc=25, pivot=30, legs=2)
        self.assertEqual(scan.swing_step(s, 28, 24, 24.5), "reversal")          # U10
        self.assertEqual((s["trend"], s["coc"], s["lo"], s["hi"], s["pivot"], s["legs"]),
                         ("down", 30, 24, None, None, 0))
        s = swing_state("up", hi=30, lo=28, coc=25)
        self.assertEqual(scan.swing_step(s, 28, 23, 24), "reversal")            # U11: pivot and reversal in one bar
        self.assertEqual((s["trend"], s["coc"], s["lo"]), ("down", 30, 23))
        s = swing_state("up", hi=30, lo=27, coc=25)
        self.assertIsNone(scan.swing_step(s, 29, 25, 25))                       # U12: equal to coc does not reverse
        self.assertEqual(s["trend"], "up")
        s = swing_state("up", hi=30, lo=27, coc=25)
        self.assertEqual(scan.swing_step(s, 33, 26, 24), "reversal")            # a new-high bar can reverse too
        self.assertEqual(s["coc"], 33)

    def test_downtrend_mirror(self):
        s = swing_state("down", hi=11.5, lo=10, coc=14, pivot=10)
        self.assertEqual(scan.swing_step(s, 11, 8.5, 9), "bos")                 # D1
        self.assertEqual((s["coc"], s["legs"], s["lo"], s["last_bos"]), (11.5, 1, 8.5, 10))
        s = swing_state("down", hi=11.5, lo=10, coc=14, pivot=10)
        self.assertEqual(scan.swing_step(s, 15, 12, 14.5), "reversal")          # D2
        self.assertEqual((s["trend"], s["coc"], s["hi"]), ("up", 10, 15))

    def test_series_a_and_b(self):
        s = scan.swing_new(5.0)
        ev = [scan.swing_step(s, *b) for b in SERIES_A[:5]]
        self.assertEqual(ev, [None, "start_up", None, None, None])
        self.assertEqual((s["pivot"], s["lo"], s["bars_since"], s["coc"]), (12, 10.2, 2, 8))
        self.assertEqual(scan.swing_step(s, *SERIES_A[5]), "bos")
        self.assertEqual((s["last_bos"], s["coc"], s["legs"], s["hi"], s["bars_since"]), (12, 10.2, 1, 12.6, 0))
        scan.swing_step(s, *SERIES_A[6])
        self.assertEqual((s["pivot"], s["lo"]), (12.6, 11.6))
        self.assertEqual(scan.swing_step(s, *SERIES_A[7]), "reversal")
        snap, events = scan.swing_run(SERIES_A, 5.0)
        self.assertEqual(events, [None, "start_up", None, None, None, "bos", None, "reversal"])
        self.assertEqual((snap["trend"], snap["coc"], snap["lo"], snap["legs"], snap["bars_since"]),
                         ("down", 12.6, 9.8, 0, 0))
        self.assertFalse(snap["stable"])
        for r in (10, 5):
            self.assertEqual(scan.swing_run(SERIES_A, r)[1], events)
        snap, events = scan.swing_run(SERIES_A, 20)                              # 15 % is under 20: the BOS is ignored
        self.assertEqual(events, [None, "start_up"] + [None] * 6)
        self.assertEqual((snap["trend"], snap["coc"], snap["pivot"], snap["lo"], snap["legs"], snap["bars_since"]),
                         ("up", 8, 12.6, 9.8, 0, 2))
        snap, events = scan.swing_run(SERIES_B, 5.0)
        self.assertEqual(events, [None, "start_down", None, None, None, "bos", None, "reversal"])
        self.assertEqual((snap["trend"], snap["coc"], snap["hi"], snap["legs"]), ("up", 7.4, 10.2, 0))

    def test_stable_and_sideways(self):
        self.assertFalse(scan.swing_run(SERIES_A, 5.0, min_bars=8)[0]["stable"])
        self.assertTrue(scan.swing_run(SERIES_A, 5.0, min_bars=7)[0]["stable"])
        s = swing_state("up", hi=30, lo=28, coc=20, pivot=30, bars_since=21)
        self.assertTrue(scan.swing_snapshot(s)["sideways"])
        s["bars_since"] = 20
        self.assertFalse(scan.swing_snapshot(s)["sideways"])

    def test_swing_tags(self):
        info = scan.swing_info(SERIES_A[:6], 5, 0)
        self.assertEqual((info["trend"], info["event"], info["coc"]), ("up", "bos", 10.2))
        self.assertEqual(scan.swing_tags(SERIES_A[:6], 5, 0), ["swing_up"])
        self.assertEqual(scan.swing_tags(SERIES_A, 5, 0), [])                   # trend down
        self.assertEqual(scan.swing_tags(SERIES_B, 5, 0), ["swing_reversal_up"])
        self.assertEqual(scan.swing_tags(SERIES_B), [])                         # 8 bars is under the 40-bar warm-up
        self.assertEqual(scan.swing_info([], 5, 0)["event"], None)


def long_rows(closes, spread=0.5, volume=200000):
    day, out = D(2025, 1, 1), []
    for c in closes:
        out.append((day, c, c + spread, c - spread, c, volume))
        day += datetime.timedelta(days=1)
    return out


class SwingScreens(unittest.TestCase):
    """The swing tags on the long-term screen. Rows are made up in the test."""

    @staticmethod
    def waves(cycles=21):
        c, out = 100.0, []
        for _ in range(cycles):
            for _ in range(8):
                c += 2.0
                out.append(c)
            for _ in range(4):
                c -= 3.0
                out.append(c)
        return [100.0] + out

    def test_an_uptrend_with_pullbacks_is_swing_up(self):
        rows = long_rows(self.waves())
        self.assertGreaterEqual(len(rows), scan.LT_BARS)
        m = scan.longterm_metrics(rows)
        self.assertEqual(m["swing_trend"], "up")
        self.assertLess(m["swing_coc"], m["close"])
        self.assertTrue(m["swing_stable"])
        self.assertIn("swing_up", scan.longterm_tags(m))
        hits, counts, _ = scan.scan_longterm({"WAVY": rows}, ["WAVY"], screen="swing_up")
        self.assertEqual([h["symbol"] for h in hits], ["WAVY"])
        h = hits[0]
        self.assertEqual(h["setups"], ["swing_up"])
        self.assertEqual(h["swing_trend"], "up")
        self.assertEqual(h["swing_coc"], round(m["swing_coc"], 2))
        self.assertIn(h["swing_event"], (None, "bos"))
        self.assertIsInstance(h["swing_bars_since"], int)
        # a collapse through the change-of-character level ends it
        crash = long_rows(self.waves() + [60.0, 55.0, 50.0])
        m2 = scan.longterm_metrics(crash)
        self.assertEqual(m2["swing_trend"], "down")
        self.assertNotIn("swing_up", scan.longterm_tags(m2))
        self.assertNotIn("swing_reversal_up", scan.longterm_tags(m2))

    def test_a_fresh_reversal_is_its_own_tag(self):
        # 193 copies of Series B's first bar (a range the price stays inside), then the other seven bars
        bars = [SERIES_B[0]] * 193 + SERIES_B[1:]
        rows = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in long_rows([0.0] * len(bars))]
        rows = [(d, c * 10, h * 10, l * 10, c * 10, 200000) for (d, _, _, _, _, _), (h, l, c) in zip(rows, bars)]
        m = scan.longterm_metrics(rows)
        self.assertEqual((m["swing_trend"], m["swing_event"], m["swing_coc"]), ("up", "reversal", 74.0))
        self.assertEqual(scan.longterm_tags(m).count("swing_reversal_up"), 1)
        self.assertNotIn("swing_up", scan.longterm_tags(m))
        hits, _, _ = scan.scan_longterm({"REV": rows}, ["REV"], screen="swing_reversal_up")
        self.assertEqual(hits[0]["swing_event"], "reversal")
        self.assertEqual(hits[0]["swing_bars_since"], 0)

    def test_the_cli_table_and_json(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.makedirs(os.path.join("trader", "data", "daily"))
            with open(os.path.join("trader", "data", "daily", "WAVY.csv"), "w") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for r in long_rows(self.waves()):
                    fh.write("%s,%.2f,%.2f,%.2f,%.2f,%d\n" % (r[0].isoformat(), r[1], r[2], r[3], r[4], r[5]))
            code, text, err = run(["longterm", "--screen", "swing_up", "--now", "2026-12-01 08:30", "--no-write"])
            self.assertEqual(code, 0, err)
            self.assertIn("SWING COCH", text)
            self.assertIn("swing structure up", text)
            code, text, _ = run(["longterm", "--now", "2026-12-01 08:30", "--no-write", "--json"])
            doc = json.loads(text)
            self.assertEqual(doc["hits"][0]["swing_trend"], "up")
            self.assertNotIn("\u2014", text)
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


CAL = os.path.join(SDIR, "calendar-2027.json")


class Calendar(unittest.TestCase):
    """trading_day reads trader/data/calendar.json (written by trader-data) when it is there."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def put(self, text):
        os.makedirs(os.path.join("trader", "data"), exist_ok=True)
        with open(os.path.join("trader", "data", "calendar.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def put_fixture(self):
        with open(CAL, encoding="utf-8") as fh:
            self.put(fh.read())

    def test_without_the_file_the_built_in_table_answers(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2027, 1, 26)), (True, ""))          # not in the 2026 table
        self.assertIn("holiday list covers 2026 only", scan.calendar_note(D(2027, 1, 4)))

    def test_the_file_covers_2027(self):
        self.put_fixture()
        self.assertEqual(scan.trading_day(D(2027, 1, 26)), (False, "NSE holiday: Republic Day"))
        self.assertEqual(scan.trading_day(D(2027, 1, 27)), (True, ""))          # the year is covered: open
        self.assertEqual(scan.trading_day(D(2027, 1, 30)), (False, "weekend"))
        self.assertEqual(scan.calendar_note(D(2027, 1, 4)), "")                 # the warning is dropped
        self.assertIn("nseindia.com", scan.calendar_note(D(2028, 1, 4)))        # but not for a year it lacks
        self.assertEqual(scan.session(D(2027, 1, 27)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2027, 1, 26)))

    def test_a_special_session_is_open_even_on_a_sunday(self):
        self.put_fixture()
        self.assertEqual(scan.trading_day(D(2027, 10, 31)), (True, ""))         # Diwali Laxmi Pujan, a Sunday
        self.assertIsNotNone(scan.session(D(2027, 10, 31)))
        self.assertEqual(scan.trading_day(D(2027, 10, 30)), (False, "weekend"))

    def test_a_covered_year_beats_the_built_in_table(self):
        # years lists 2026 and no holidays: the built-in Dussehra holiday is not in the file, so the day is open
        self.put(json.dumps({"years": [2026], "holidays": {"2026-10-19": "Test closure"}}))
        self.assertEqual(scan.trading_day(D(2026, 10, 19)), (False, "NSE holiday: Test closure"))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (True, ""))
        # a year the file does not cover falls through to the built-in table
        self.put(json.dumps({"years": [2027], "holidays": {}}))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))

    def test_years_as_strings_or_a_dict_and_bad_dates(self):
        self.put(json.dumps({"years": {"2027": {"2027-01-26": "Republic Day"}}, "holidays": {"junk": "x",
                                                                                           "2027-03-04": "Holi"}}))
        self.assertEqual(scan.trading_day(D(2027, 3, 4)), (False, "NSE holiday: Holi"))
        self.assertEqual(scan.trading_day(D(2027, 3, 5)), (True, ""))

    def test_a_corrupt_or_wrong_shape_file_is_ignored(self):
        for text in ("{not json", "[1, 2]", '"x"', ""):
            self.put(text)
            self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"), text)
            self.assertIn("2026 only", scan.calendar_note(D(2027, 1, 4)))

    def test_the_orb_window_follows_the_calendar(self):
        self.put(json.dumps({"years": [2026], "holidays": {"2026-10-19": "Test closure"}}))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-10-19 10:00"), None, 15, datetime.time(15, 0))
        self.assertIn("Test closure", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
