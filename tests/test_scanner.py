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
        # the swing engine on closes: up, a swing high pending 3 to 15 bars, the close over the coc level
        "RELIANCE": ["swing_pullback"],     # bos 380.68 (coc 352.84), new high 382.70, 5 bars since, close 354.72
        "TCS": ["swing_pullback"],          # level 239.24, coc 210.96, 8 bars since
        "HDFCBANK": ["swing_pullback"],     # level 543.45, coc 480.35, 3 bars since
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
                                  "hits": 18})
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["level"], rel["coc"], rel["swing_trend"], rel["swing_basis"]),
                         (382.7, 352.84, "up", "closes"))                    # spark: closes only
        itc = [h for h in hits if h["symbol"] == "ITC"][0]
        self.assertEqual((itc["level"], itc["coc"]), (93.9, 85.65))          # trend up, no tag: the numbers stay
        self.assertIsNone([h for h in hits if h["symbol"] == "COALINDIA"][0]["level"])   # trend down
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
        self.assertEqual(sorted(h["symbol"] for h in hits), ["COALINDIA", "DELHIVERY", "GMRAIRPORT", "HDFCBANK", "IOC",
                                                             "ITC", "KEI", "RELIANCE", "SONATSOFTW", "TCS"])
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
        self.assertEqual(sorted(h["symbol"] for h in doc["hits"]), ["HDFCBANK", "ITC", "RELIANCE", "TCS"])
        self.assertIn({"symbol": "ITC", "setups": ["breakout_20d"], "price": 93.9, "close_date": "2026-10-16",
                       "rsi": 75.3, "volume": 3680307, "level": 93.9, "coc": 85.65, "swing_trend": "up",
                       "swing_basis": "closes", "dq": None, "dq_date": None}, doc["hits"])
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
        self.assertEqual(len(out["hits"]), 18)
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


def swing_daily_bars(extra_bars=2):
    """41 daily (high, low, close) bars that end in Series A's pullback: a quiet 33-bar range, bars 2 to 7 of the
    series, then `extra_bars` more (12/11/11.5 then 11.8/10.9/11.2 for two). 41 is past the 40-bar warm-up."""
    return [SERIES_A[0]] * 33 + SERIES_A[1:7] + [(12, 11, 11.5), (11.8, 10.9, 11.2)][:extra_bars]


class DailySwing(unittest.TestCase):
    """swing_pullback and swing_reversal_up in the daily scan, and orb --trend-filter."""

    def test_the_pullback_numbers(self):
        a9 = SERIES_A[:7] + [(12, 11, 11.5), (11.8, 10.9, 11.2)]
        tags, nums = scan.find_swing(a9, 5, 0)
        self.assertEqual(tags, ["swing_pullback"])
        self.assertEqual((nums["level"], nums["coc"], nums["bars_since"], nums["trend"]), (12.6, 10.2, 3, "up"))
        tags, nums = scan.find_swing(SERIES_A[:7], 5, 0)                         # bars_since 1: too soon
        self.assertEqual(tags, [])
        self.assertEqual((nums["level"], nums["coc"], nums["bars_since"]), (12.6, 10.2, 1))
        # 3 to 15 bars since the swing high formed, inclusive
        flat = (12, 11, 11.5)
        for extra, want in ((1, []), (2, ["swing_pullback"]), (14, ["swing_pullback"]), (15, [])):
            tags, nums = scan.find_swing(SERIES_A[:7] + [flat] * extra, 5, 0)
            self.assertEqual(tags, want, extra)
        self.assertEqual((scan.SWING_PULLBACK_MIN, scan.SWING_PULLBACK_MAX), (3, 15))
        # a close under the coc level reverses the trend: nothing, and no level
        tags, nums = scan.find_swing(SERIES_A, 5, 0)
        self.assertEqual((tags, nums["trend"], nums["level"], nums["coc"]), ([], "down", None, None))
        # under the warm-up the trend is not reported at all
        tags, nums = scan.find_swing(a9)
        self.assertEqual((tags, nums["trend"]), ([], None))

    def test_a_reversal_up_on_the_last_bar(self):
        tags, nums = scan.find_swing(SERIES_B, 5, 0)
        self.assertEqual(tags, ["swing_reversal_up"])
        self.assertEqual((nums["level"], nums["coc"], nums["bars_since"]), (10.2, 7.4, 0))

    def rows(self, bars, end=D(2026, 10, 16)):
        day, out = end - datetime.timedelta(days=len(bars) - 1), []
        for h, l, c in bars:
            out.append((day, c, h, l, c, 100000.0))
            day += datetime.timedelta(days=1)
        return out

    def test_real_highs_and_lows_versus_closes_only(self):
        rows = self.rows(swing_daily_bars())
        full = scan.series_from_daily(rows)                                      # the cache: real highs and lows
        spark = {"t": full["t"], "c": full["c"], "meta": full["meta"]}           # Yahoo spark: closes only
        now = scan.parse_now("2026-10-19 08:45")
        hits, _, _ = scan.scan_daily({"HL": full, "CL": spark}, ["HL", "CL"], now, 0, 0)
        by = {h["symbol"]: h for h in hits}
        self.assertEqual(by["HL"]["setups"], ["swing_pullback"])
        self.assertEqual((by["HL"]["level"], by["HL"]["coc"], by["HL"]["swing_basis"]), (12.6, 10.2, "highs_lows"))
        # closes only: the swing high is the highest close (12.4), the pullback low the lowest close (10.4)
        self.assertEqual(by["CL"]["setups"], ["swing_pullback"])
        self.assertEqual((by["CL"]["level"], by["CL"]["coc"], by["CL"]["swing_basis"]), (12.4, 10.4, "closes"))

    def test_daily_cache_end_to_end_and_the_6mo_request(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        saved = scan.yahoo_get
        try:
            os.makedirs(os.path.join("trader", "data", "daily"))
            with open(os.path.join("trader", "data", "daily", "SWNG.csv"), "w") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for r in self.rows(swing_daily_bars()):
                    fh.write("%s,%s,%s,%s,%s,%d\n" % (r[0].isoformat(), r[1], r[2], r[3], r[4], r[5]))
            code, text, err = run(["daily", "--cache", "--symbols", "SWNG", "--now", "2026-10-19 08:45", "--json",
                                   "--no-write", "--min-price", "0", "--min-volume", "0"])
            self.assertEqual(code, 0, err)
            h = json.loads(text)["hits"][0]
            self.assertEqual((h["setups"], h["level"], h["coc"], h["swing_basis"]),
                             (["swing_pullback"], 12.6, 10.2, "highs_lows"))
            code, text, _ = run(["daily", "--cache", "--symbols", "SWNG", "--now", "2026-10-19 08:45", "--no-write",
                                 "--min-price", "0", "--min-volume", "0"])
            self.assertIn("swing pullback", text)
            self.assertIn("[level 12.60, coc 10.20]", text)
            self.assertIn("highs and lows", text)
            seen = []

            def boom(path):
                seen.append(path)
                raise scan.ScanError("blocked")
            scan.yahoo_get = boom
            run(["daily", "--symbols", "SWNG", "--now", "2026-10-19 08:45", "--no-write", "--pause", "0"])
            self.assertIn("range=6mo&interval=1d", seen[0])
        finally:
            scan.yahoo_get = saved
            os.chdir(cwd)
            shutil.rmtree(d)

    # ---- orb --trend-filter

    def test_trend_filter_helper(self):
        up = self.rows(SERIES_A[:6])
        down = self.rows(SERIES_A)
        short = self.rows(SERIES_A[:3])
        hits = [{"symbol": "UPS", "setups": ["orb"]}, {"symbol": "DWN", "setups": ["orb"]},
                {"symbol": "SHT", "setups": ["orb"]}, {"symbol": "NOC", "setups": ["orb"]}]
        kept, dropped = scan.trend_filter(hits, {"UPS": up, "DWN": down, "SHT": short}, 5, 5)
        self.assertEqual(dropped, ["DWN"])
        self.assertEqual([h["symbol"] for h in kept], ["UPS", "SHT", "NOC"])
        self.assertEqual(kept[0]["daily_trend"], "up")
        self.assertNotIn("trend_note", kept[0])
        self.assertEqual(kept[1]["daily_trend"], "unknown")
        self.assertIn("trend unknown", kept[1]["trend_note"])
        self.assertIn("no daily cache", kept[2]["trend_note"])
        self.assertEqual(hits[0], {"symbol": "UPS", "setups": ["orb"]})           # the input is not changed
        # the default warm-up is 40 bars: 6 bars of a downtrend is "unknown", not dropped
        kept, dropped = scan.trend_filter(hits[:2], {"UPS": up, "DWN": down})
        self.assertEqual((len(kept), dropped), (2, []))

    def test_trend_filter_on_the_orb_scan(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.makedirs(os.path.join("trader", "data", "daily"))

            def put(sym, bars, extra=()):
                with open(os.path.join("trader", "data", "daily", "%s.csv" % sym), "w") as fh:
                    fh.write("date,open,high,low,close,volume\n")
                    for r in self.rows(bars) + list(extra):
                        fh.write("%s,%s,%s,%s,%s,%d\n" % (r[0].isoformat(), r[1], r[2], r[3], r[4], r[5]))
            put("BLUESTARCO", [SERIES_A[0]] * 36 + SERIES_A[1:6],          # up: a bos on the last bar
                extra=[(D(2026, 10, 19), 1.0, 1.0, 1.0, 1.0, 1000.0)])      # a crash dated the session day: not read
            put("CHALET", [SERIES_A[0]] * 35 + SERIES_A[1:])                # down: a reversal on the last bar
            base = ["--offline", WALL, "--now", "2026-10-19 10:00", "--min-price", "0", "--json", "--no-write"]
            code, text, _ = run(["orb"] + base)
            plain = json.loads(text)
            self.assertEqual(len(plain["hits"]), 42)
            self.assertNotIn("trend_filter", plain)
            code, text, err = run(["orb"] + base + ["--trend-filter"])
            self.assertEqual(code, 0, err)
            doc = json.loads(text)
            by = {h["symbol"]: h for h in doc["hits"]}
            self.assertEqual(len(doc["hits"]), 41)
            self.assertNotIn("CHALET", by)
            self.assertEqual(by["BLUESTARCO"]["daily_trend"], "up")
            self.assertEqual(by["GLENMARK"]["daily_trend"], "unknown")
            self.assertIn("trend unknown", by["GLENMARK"]["trend_note"])
            self.assertEqual(doc["trend_filter"], {"kept": 41, "dropped": ["CHALET"], "unknown": 40})
            self.assertEqual(doc["counts"]["hits"], 41)
            code, text, _ = run(["orb"] + base[:-2] + ["--no-write", "--trend-filter"])
            self.assertIn("Daily trend filter: dropped 1 whose daily swing trend is down (CHALET); 40 kept", text)
            self.assertIn("(trend unknown (no daily cache))", text)
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


def prows(closes, volumes=None, start=D(2026, 1, 1)):
    """The spec's test convention: high = close + 0.5, low = close - 0.5 (so U = 1.0)."""
    return [(start + datetime.timedelta(days=i), c, c + 0.5, c - 0.5, c, volumes[i] if volumes else 1000.0)
            for i, c in enumerate(closes)]


def ramp(points):
    """[(index, value), ...] -> closes by straight lines between the points."""
    out = []
    for (i0, v0), (i1, v1) in zip(points, points[1:]):
        out.extend(round(v0 + (v1 - v0) * (i - i0) / float(i1 - i0), 2) for i in range(i0, i1))
    return out + [points[-1][1]]


P2 = {"min_bars": 0, "L": 2, "R": 2}      # the spec's test convention: pivots of 2 bars each side, gates relaxed
VCP_UP = [100, 110, 120, 130, 140, 150, 140, 130, 120, 130, 140, 148, 142, 136, 141, 146]
DBL = [130, 125, 120, 115, 110, 105, 100, 106, 112, 108, 104, 100, 103, 107, 110]
FLAG = [102, 101, 100, 101, 102, 102, 102, 102, 102, 102, 106, 110, 114, 118, 122, 126, 123, 121, 122, 120, 122]
TRI = [100, 106, 112, 107, 101, 107, 112, 108, 104, 108, 112, 109, 110]
IHS = [120, 110, 100, 108, 115, 105, 95, 90, 97, 105, 115, 108, 102, 106, 110]
SUPPORT = [108, 104, 100, 105, 110, 112, 110, 108, 106, 110, 114, 116, 115, 113, 112, 114, 117, 118, 116]


class Patterns(unittest.TestCase):
    """Chart patterns on hand-made series (stock-pattern.md section 5): L = R = 2, high = close + 0.5."""

    def one(self, closes, name, **kw):
        hits = scan.find_patterns(prows(closes), dict(P2, **kw), [name])
        self.assertLessEqual(len(hits), 1)
        return hits[0] if hits else None

    def test_pivots_and_zigzag(self):
        # the earlier of two equal highs is the pivot; a pivot needs `right` bars after it
        ph, pl = scan.find_pivots([1, 2, 5, 5, 2, 1, 1], [0, 0, 0, 0, 0, 0, 0], 2, 2)
        self.assertEqual(ph, [(2, 5)])
        self.assertEqual(pl, [])
        # an outside bar (a high and a low at once) is ignored
        ph, pl = scan.find_pivots([10, 10, 15, 10, 10], [9, 9, 5, 9, 9], 2, 2)
        self.assertEqual((ph, pl), ([], []))
        # no look-ahead: nothing in the last `right` bars is a pivot
        closes = VCP_UP
        ph, pl = scan.find_pivots([c + .5 for c in closes], [c - .5 for c in closes], 2, 2)
        self.assertTrue(all(i <= len(closes) - 3 for i, _ in ph + pl))
        self.assertEqual([i for i, _ in ph], [5, 11])
        self.assertEqual([i for i, _ in pl], [8, 13])
        # neighbours of one type collapse to the more extreme, the earlier on a tie
        zz = scan.zigzag([(2, 10), (5, 12), (12, 9)], [(7, 3), (9, 2), (14, 4)])
        self.assertEqual(zz, [(5, "H", 12), (9, "L", 2), (12, "H", 9), (14, "L", 4)])
        self.assertEqual(scan.zigzag([(2, 10), (5, 10)], []), [(2, "H", 10)])
        self.assertEqual(scan.median([3, 1, 2]), 2)
        self.assertEqual(scan.median([1, 2, 3, 4]), 2.5)

    def test_states(self):
        self.assertEqual(scan.pat_state([1, 2, 3, 4], 0, 5), "coiled")
        self.assertEqual(scan.pat_state([1, 2, 3, 5.1], 0, 5), "breakout")
        self.assertIsNone(scan.pat_state([1, 2, 5.1, 5.2], 0, 5))          # not the first close over
        self.assertIsNone(scan.pat_state([1, 2, 3, 5.3], 0, 5))             # more than 5 % over
        self.assertIsNone(scan.pat_state([1, 6, 3, 4], 0, 5))               # an earlier close beyond
        self.assertEqual(scan.pat_state([1, 6, 3, 4], 1, 5), "coiled")      # ... before the pattern began
        self.assertEqual(scan.pat_state([1, 2, 3, 5], 0, 5, inclusive=True), "breakout")
        self.assertIsNone(scan.pat_state([1, 2, 3, 5], 0, 5))

    def test_vcp(self):
        kw = dict(vcp_trend_gate=False, vcp_base_age=10, vcp_min_bars=0)
        h = self.one(VCP_UP, "vcp", **kw)
        self.assertEqual((h["pattern"], h["state"], h["setups"]), ("vcp", "coiled", ["vcp"]))
        self.assertEqual((h["level"], h["stop"], h["target"]), (148.55, 135.45, None))
        self.assertEqual(h["pivots"], [["2026-01-06", 150.5], ["2026-01-09", 119.5], ["2026-01-12", 148.5],
                                       ["2026-01-14", 135.5]])
        self.assertEqual((h["anchor_date"], h["price"], h["close_date"]), ("2026-01-06", 146, "2026-01-16"))
        self.assertEqual(h["risk_pct"], round((148.55 - 135.45) / 148.55 * 100, 2))
        # the second low undercuts the first: nothing fires
        neg = list(VCP_UP)
        neg[13] = 118
        self.assertIsNone(self.one(neg, "vcp", **kw))
        # a first pullback of 5 % is too shallow, a base high only 5 bars old is too young
        young = dict(kw, vcp_base_age=5)
        self.assertIsNotNone(self.one(VCP_UP, "vcp", **young))
        self.assertIsNone(self.one([100, 110, 120, 130, 140, 150, 146, 143, 146, 148, 146, 144, 146, 147], "vcp", **young))
        self.assertIsNone(self.one(VCP_UP, "vcp", vcp_trend_gate=False, vcp_min_bars=0, vcp_base_age=11))
        # the stop must be close: a risk over 12 % drops the hit
        self.assertIsNone(self.one(VCP_UP, "vcp", vcp_max_risk_pct=8.0, **kw))
        # not coiled any more: the last close is over the trigger, the first one (a breakout) ...
        up = VCP_UP[:-1] + [149]
        self.assertEqual(self.one(up, "vcp", **kw)["state"], "breakout")
        # ... but the second close over is not
        self.assertIsNone(self.one(up + [150], "vcp", **kw))

    def test_vcp_trend_gate_on_a_year_of_bars(self):
        closes = ramp([(0, 50), (199, 140), (205, 150), (218, 128), (230, 148), (240, 138), (259, 145)])
        rows = prows(closes, [200000.0] * 260, D(2025, 1, 1))
        hits = scan.find_patterns(rows, only=["vcp"])           # the defaults: 5-bar pivots, every gate on
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual((h["state"], h["level"], h["stop"], h["anchor_date"]),
                         ("coiled", 148.55, 137.45, "2025-07-25"))
        self.assertEqual(h["vol_dry"], False)
        self.assertEqual(h["vol_x"], 1.0)
        # under 200 bars, or a 200-day average that is falling, or far under the high: no VCP
        self.assertEqual(scan.find_patterns(rows[-199:], only=["vcp"]), [])
        down = ramp([(0, 300), (199, 140), (205, 150), (218, 128), (230, 148), (240, 138), (259, 145)])
        self.assertEqual(scan.find_patterns(prows(down, [200000.0] * 260, D(2025, 1, 1)), only=["vcp"]), [])

    def test_double_bottom(self):
        kw = dict(db_gap=(5, 80), db_prior_bars=5)
        h = self.one(DBL, "double_bottom", **kw)
        self.assertEqual((h["state"], h["level"], h["stop"], h["target"]), ("coiled", 112.55, 99.0, 125.5))
        self.assertEqual(h["pivots"], [["2026-01-07", 99.5], ["2026-01-12", 99.5]])
        self.assertEqual(h["anchor_date"], "2026-01-07")
        # last close 113 is over B: not coiled; it is the first close over, so a breakout
        up = DBL[:-1] + [113]
        self.assertEqual(self.one(up, "double_bottom", **kw)["state"], "breakout")
        self.assertIsNone(self.one(DBL[:-1] + [120], "double_bottom", **kw))          # more than 5 % over
        # under the midpoint of the two lows and the top: not yet
        self.assertIsNone(self.one(DBL[:-1] + [104], "double_bottom", **kw))
        # the lows 2 U apart are not a double bottom
        uneven = list(DBL)
        uneven[11] = 97
        self.assertIsNone(self.one(uneven, "double_bottom", **kw))
        # the two lows closer than the minimum gap, no prior decline
        self.assertIsNone(self.one(DBL, "double_bottom", db_gap=(6, 80), db_prior_bars=5))
        self.assertIsNone(self.one(DBL, "double_bottom", db_gap=(5, 80), db_prior_bars=5, db_prior_drop=1.5))
        # volume is advisory: the second low on less volume
        vols = [1000.0] * len(DBL)
        vols[6], vols[11] = 2000.0, 1000.0
        hits = scan.find_patterns(prows(DBL, vols), dict(P2, **kw), ["double_bottom"])
        self.assertIs(hits[0]["vol_dry"], True)
        vols[11] = 3000.0
        self.assertIs(scan.find_patterns(prows(DBL, vols), dict(P2, **kw), ["double_bottom"])[0]["vol_dry"], False)

    def test_flag(self):
        h = self.one(FLAG, "flag", flag_min_bars=0)
        self.assertEqual((h["state"], h["level"], h["stop"], h["target"]), ("coiled", 126.55, 119.45, 153.5))
        self.assertEqual(h["pivots"], [["2026-01-03", 99.5], ["2026-01-16", 126.5]])       # the base, the pole top
        self.assertEqual(h["anchor_date"], "2026-01-03")
        # the flag sags under the midpoint of the pole (113): nothing fires
        sag = FLAG[:16] + [118, 112, 114, 111, 113]
        self.assertIsNone(self.one(sag, "flag", flag_min_bars=0))
        # the pole top must be 4 to 6 bars back
        self.assertIsNotNone(self.one(FLAG[:-1], "flag", flag_min_bars=0))          # 4 bars back
        self.assertIsNone(self.one(FLAG[:-2], "flag", flag_min_bars=0))             # 3 bars back
        self.assertIsNotNone(self.one(FLAG + [121], "flag", flag_min_bars=0))       # 6 bars back
        self.assertIsNone(self.one(FLAG + [121, 121], "flag", flag_min_bars=0))     # 7 bars back
        # a gain of 11 % is no pole
        self.assertIsNone(self.one([100, 99, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 106, 105, 106, 105, 106],
                                   "flag", flag_min_bars=0))
        # the default 50-bar gate
        self.assertEqual(scan.find_patterns(prows(FLAG), {"L": 2, "R": 2}, ["flag"]), [])

    def test_ascending_triangle(self):
        h = self.one(TRI, "ascending_triangle", tri_span=(0, 999))
        self.assertEqual((h["state"], h["level"], h["stop"]), ("coiled", 112.55, 103.0))
        self.assertEqual([p[1] for p in h["pivots"]], [112.5, 100.5, 112.5, 103.5, 112.5])
        self.assertEqual(h["target"], 124.5)                  # the top plus the height from the first low
        self.assertIsNone(self.one(TRI, "ascending_triangle"))                       # span 8 bars: under the 20 minimum
        flat = TRI[:8] + [100, 108, 112, 109, 110]       # the second low is lower than the first
        self.assertIsNone(self.one(flat, "ascending_triangle", tri_span=(0, 999)))   # the lows no longer rise

    def test_inverse_head_and_shoulders(self):
        h = self.one(IHS, "inverse_head_shoulders")
        self.assertEqual((h["state"], h["level"], h["stop"], h["target"]), ("coiled", 115.55, 101.0, 141.5))
        self.assertEqual([p[1] for p in h["pivots"]], [99.5, 115.5, 89.5, 115.5, 101.5])
        self.assertIsNone(self.one(IHS[:-1] + [100], "inverse_head_shoulders"))     # under the midpoint
        self.assertEqual(self.one(IHS[:-1] + [117], "inverse_head_shoulders")["state"], "breakout")
        uneven = list(IHS)
        uneven[10] = 118                                                             # the necklines 3 U apart
        self.assertIsNone(self.one(uneven, "inverse_head_shoulders"))

    def test_rising_support(self):
        h = self.one(SUPPORT, "rising_support")
        self.assertEqual((h["state"], h["level"], h["stop"], h["target"]), ("coiled", 116.55, 114.5, None))
        self.assertEqual([p[1] for p in h["pivots"]], [99.5, 105.5, 111.5])           # three touches, 1 a bar
        self.assertEqual(h["anchor_date"], "2026-01-03")
        self.assertIsNone(self.one(SUPPORT[:-1] + [121], "rising_support"))          # more than 3 % over the line
        self.assertIsNone(self.one(SUPPORT[:-1] + [114], "rising_support"))          # under the line
        self.assertIsNone(self.one(SUPPORT[:6] + [100] + SUPPORT[7:], "rising_support"))   # a close through it before

    def test_vcp_and_triangle_merge_into_one_hit(self):
        real = scan.DETECTORS
        rows = prows(VCP_UP)

        def fake(name, level, anchor):
            return lambda x: {"pattern": name, "state": "coiled", "level": level, "stop": level - 5, "target": None,
                              "anchor": anchor, "pivots": [(anchor, 100.0)], "vol_dry": None}
        scan.DETECTORS = [("vcp", fake("vcp", 150.0, 3)), ("ascending_triangle", fake("ascending_triangle", 151.0, 1)),
                          ("flag", fake("flag", 152.0, 2))]
        try:
            hits = scan.find_patterns(rows, P2)
        finally:
            scan.DETECTORS = real
        self.assertEqual([h["pattern"] for h in hits], ["vcp", "flag"])
        self.assertEqual(hits[0]["setups"], ["vcp", "ascending_triangle"])
        self.assertEqual(hits[0]["level"], 150.0)
        self.assertEqual(hits[0]["also"][0]["pattern"], "ascending_triangle")
        self.assertEqual(hits[0]["also"][0]["level"], 151.0)

    def test_the_hit_shape_and_a_stop_over_the_level(self):
        h = self.one(DBL, "double_bottom", db_gap=(5, 80), db_prior_bars=5)
        self.assertEqual(sorted(h), sorted(["setups", "price", "close_date", "pattern", "state", "level", "stop",
                                            "risk_pct", "target", "anchor_date", "pivots", "vol_x", "vol_dry"]))
        self.assertIsNone(h["vol_x"])                      # 15 bars: no 50-bar volume mean
        rows = prows(DBL + [110] * 40, [1000.0] * 55)
        self.assertEqual(scan.pattern_hit(scan.pat_context(rows, P2), {
            "pattern": "x", "state": "coiled", "level": 10.0, "stop": 11.0, "target": None, "anchor": 0,
            "pivots": [], "vol_dry": None}), None)

    def test_scan_patterns_dedups_filters_and_carries_first_seen(self):
        rows = {"FLG": prows(FLAG, start=D(2026, 1, 1)), "DBL": prows(DBL, start=D(2026, 1, 1))}
        params = dict(P2, flag_min_bars=0, db_gap=(5, 80), db_prior_bars=5)
        hits, counts, last = scan.scan_patterns(rows, ["FLG", "DBL", "NONE"], 0, 0, params=params)
        self.assertEqual(sorted((h["symbol"], h["pattern"]) for h in hits), [("DBL", "double_bottom"), ("FLG", "flag")])
        self.assertEqual((counts["requested"], counts["cached"], counts["scanned"], counts["hits"]), (3, 2, 2, 2))
        self.assertEqual(last, D(2026, 1, 21))
        hits, _, _ = scan.scan_patterns(rows, ["FLG", "DBL"], 0, 0, only=["flag"], params=params)
        self.assertEqual([h["symbol"] for h in hits], ["FLG"])
        hits, _, _ = scan.scan_patterns(rows, ["FLG", "DBL"], 0, 0, state="breakout", params=params)
        self.assertEqual(hits, [])
        hits, counts, _ = scan.scan_patterns(rows, ["FLG", "DBL"], 500, 0, params=params)       # a price floor
        self.assertEqual((hits, counts["illiquid"]), ([], 2))
        hits, counts, _ = scan.scan_patterns(rows, ["FLG"], 0, 0)                                # 21 rows: under min_bars 50
        self.assertEqual((hits, counts["short"]), ([], 1))
        # first_seen: carried when the same (symbol, pattern, anchor date) was in the previous watchlist
        hits, _, _ = scan.scan_patterns(rows, ["FLG", "DBL"], 0, 0, params=params)
        prev = {"mode": "patterns", "hits": [{"symbol": "FLG", "pattern": "flag", "anchor_date": "2026-01-03",
                                              "first_seen": "2025-12-01"},
                                             {"symbol": "DBL", "pattern": "double_bottom", "anchor_date": "2000-01-01",
                                              "first_seen": "2025-12-02"}]}
        scan.carry_first_seen(hits, prev)
        seen = {h["symbol"]: h["first_seen"] for h in hits}
        self.assertEqual(seen, {"FLG": "2025-12-01", "DBL": "2026-01-15"})      # DBL's anchor moved: it is new
        scan.carry_first_seen(hits, {"mode": "daily", "hits": prev["hits"]})     # another mode's file: ignored
        self.assertEqual({h["symbol"]: h["first_seen"] for h in hits}, {"FLG": "2026-01-21", "DBL": "2026-01-15"})

    def test_500_symbols_by_260_bars_is_fast(self):
        import random
        import time
        rnd = random.Random(7)
        by = {}
        for s in range(500):
            c, rows = 100.0, []
            drift = rnd.uniform(-0.001, 0.002)
            for i in range(260):
                c *= 1 + rnd.gauss(drift, 0.02)
                rows.append((D(2025, 1, 1) + datetime.timedelta(days=i), c, c * (1 + abs(rnd.gauss(0, 0.01))),
                             c * (1 - abs(rnd.gauss(0, 0.01))), c, 200000.0))
            by["S%d" % s] = rows
        t0 = time.time()
        hits, counts, _ = scan.scan_patterns(by, sorted(by), 0, 0)
        self.assertLess(time.time() - t0, 10.0)
        self.assertEqual(counts["scanned"], 500)
        for h in hits:                      # every hit is well-formed
            self.assertLess(h["stop"], h["level"])
            self.assertIn(h["state"], ("coiled", "breakout"))
            self.assertIn(h["pattern"], scan.PAT_LABEL)


class PatternsCli(unittest.TestCase):
    """patterns on a made-up trader-data cache: a pole and flag, a flat line, a short history."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "data", "daily"))
        flag = ramp([(0, 100), (59, 100), (61, 97), (74, 125), (75, 124), (76, 123), (77, 122), (78, 123), (79, 122)])
        self.csv("FLG", flag)
        self.csv("FLAT", [100.0] * 80)
        self.csv("SHORT", [100.0] * 20)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def csv(self, sym, closes):
        with open(os.path.join("trader", "data", "daily", "%s.csv" % sym), "w") as fh:
            fh.write("date,open,high,low,close,volume\n")
            for r in prows(closes, start=D(2025, 1, 1)):
                fh.write("%s,%s,%s,%s,%s,200000\n" % (r[0].isoformat(), r[1], r[2], r[3], r[4]))

    def test_the_command_end_to_end(self):
        out = os.path.join("trader", "watchlist.json")
        code, text, err = run(["patterns", "--now", "2026-12-01 08:30"])
        self.assertEqual(code, 0, err)
        self.assertIn("Pattern scan, closes through 2025-03-21", text)
        self.assertIn("pole and flag", text)
        self.assertIn("(new)", text)
        self.assertIn("a turn in the last 5 sessions is not visible yet", text)
        self.assertIn("Not financial advice", text)
        self.assertNotIn("\u2014", text)
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["mode"], "patterns")
        self.assertEqual((doc["scanned"], doc["universe"], doc["created_by"]), (2, 3, "trader-scanner"))
        self.assertEqual(len(doc["hits"]), 1)
        h = doc["hits"][0]
        self.assertEqual((h["symbol"], h["pattern"], h["state"], h["level"], h["stop"], h["target"], h["first_seen"]),
                         ("FLG", "flag", "coiled", 125.55, 121.45, 154.5, "2025-03-21"))
        self.assertEqual(h["close_date"], "2025-03-21")
        # a second run keeps first_seen from the file it finds, and the entry skill's key is there
        h["first_seen"] = "2025-03-10"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, text, _ = run(["patterns", "--now", "2026-12-01 08:30", "--json"])
        again = json.loads(text)
        self.assertEqual(again["hits"][0]["first_seen"], "2025-03-10")
        self.assertIsInstance(again["hits"][0]["level"], float)
        self.assertEqual(again["counts"]["short"], 1)
        # filters
        code, text, _ = run(["patterns", "--pattern", "vcp", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("Nothing matched", text)
        code, text, _ = run(["patterns", "--state", "breakout", "--no-write"])
        self.assertEqual(code, 1)
        code, text, _ = run(["patterns", "--symbols", "flg", "--pattern", "flag", "--state", "coiled", "--no-write"])
        self.assertEqual(code, 0)
        code, _, err = run(["patterns", "--symbols", "GONE", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("trader-data/data.py daily", err)


def flat_rows(closes, start=D(2026, 1, 5)):
    """High = low = close, one calendar day apart: hand-countable breadth."""
    return [(start + datetime.timedelta(days=i), c, c, c, c, 1000.0) for i, c in enumerate(closes)]


class Breadth(unittest.TestCase):
    """eod2.md ideas 5 and 6: breadth from the cache, and Mansfield relative strength."""

    def test_ema_seed_and_update(self):
        self.assertEqual(scan.ema_series([0, 30, 0], 2), [None, 15.0, 5.0])           # seed = mean of the first 2
        self.assertEqual(scan.ema_series([0, 30, 0], 3), [None, None, 10.0])
        self.assertEqual(scan.ema_series([1.0], 2), [None])

    def test_three_symbols_four_days_by_hand(self):
        rows = {"A": flat_rows([10, 11, 12, 11]), "B": flat_rows([20, 19, 18, 19]), "C": flat_rows([30, 30, 31, 31])}
        out = scan.breadth_series(rows, sma=(2, 3), high_bars=2, fast=2, slow=3)
        # day 1 has no previous close: skipped. Day 2: A up, B down, C unchanged; 1 of 3 over its 2-day average
        self.assertEqual([r["date"] for r in out], ["2026-01-06", "2026-01-07", "2026-01-08"])
        self.assertEqual(out[0], {"date": "2026-01-06", "pct_50": 33.33, "pct_200": None, "net_new_highs": 0,
                                  "ad_line": 0.0, "mcclellan_osc": None, "net_adv_ratio": 0.0, "fast_ema": None,
                                  "slow_ema": None})
        # day 3: A and C up, B down: ratio (2 - 1) / 3; 2 of 3 over both averages; highs A and C, low B: net +1
        self.assertEqual(out[1], {"date": "2026-01-07", "pct_50": 66.67, "pct_200": 66.67, "net_new_highs": 1,
                                  "ad_line": 0.3333, "mcclellan_osc": None, "net_adv_ratio": 33.33,
                                  "fast_ema": 16.6667, "slow_ema": None})     # the fast seed: (0 + 33.33) / 2
        # day 4: A down, B up, C unchanged: ratio 0; the fast EMA moves 2/3 of the way to 0; the slow seed is the
        # mean of the three ratios; the oscillator is fast - slow
        self.assertEqual(out[2], {"date": "2026-01-08", "pct_50": 33.33, "pct_200": 66.67, "net_new_highs": 1,
                                  "ad_line": 0.3333, "mcclellan_osc": -5.5556, "net_adv_ratio": 0.0,
                                  "fast_ema": 5.5556, "slow_ema": 11.1111})
        out = scan.breadth_series(rows, sma=(2, 3), high_bars=2, fast=2, slow=3, days=2)
        self.assertEqual([r["date"] for r in out], ["2026-01-07", "2026-01-08"])      # the last two dates only
        self.assertEqual(out[0]["fast_ema"], None)                                    # the series starts again there

    def test_a_symbol_without_the_history_is_not_counted(self):
        rows = {"OLD": flat_rows([10, 11, 12, 13, 14]), "NEW": flat_rows([5, 6], start=D(2026, 1, 8))}
        out = scan.breadth_series(rows, sma=(3, 4), high_bars=2, fast=2, slow=3)
        by = {r["date"]: r for r in out}
        self.assertEqual(by["2026-01-09"]["pct_50"], 100.0)     # only OLD has 3 bars; it closes over its average
        self.assertEqual(by["2026-01-09"]["net_adv_ratio"], 100.0)    # NEW has a previous close on the 9th: 2 of 2 up

    def test_defaults_are_the_documented_ones(self):
        self.assertEqual((scan.BREADTH_FAST, scan.BREADTH_SLOW, scan.BREADTH_SMA, scan.BREADTH_HIGH_BARS),
                         (19, 39, (50, 200), 252))

    def test_regime_note(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(scan.regime_note(d), "")
            rows = [{"date": "2026-10-28", "pct_50": 62.0, "pct_200": 66.67, "net_new_highs": 3, "ad_line": 1.0,
                     "mcclellan_osc": 12.34, "net_adv_ratio": 5.0, "fast_ema": 1.0, "slow_ema": 1.0}]
            scan.write_breadth(os.path.join(d, "breadth.csv"), rows)
            with open(os.path.join(d, "breadth.csv"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text.splitlines()[0],
                             "date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc,net_adv_ratio,fast_ema,slow_ema")
            self.assertEqual(scan.regime_note(d), "Market breadth 2026-10-28: broad strength (67% above the 200-day "
                                                  "average, 62% above the 50-day, McClellan +12.3).")
            for p200, word in ((40.0, "weak"), (50.0, "mixed"), (60.0, "broad strength")):
                rows[0]["pct_200"] = p200
                scan.write_breadth(os.path.join(d, "breadth.csv"), rows)
                self.assertIn(": %s (" % word, scan.regime_note(d))
            rows[0]["pct_200"], rows[0]["mcclellan_osc"] = None, None             # a young cache: only the 50-day
            scan.write_breadth(os.path.join(d, "breadth.csv"), rows)
            self.assertEqual(scan.regime_note(d), "Market breadth 2026-10-28: broad strength (- above the 200-day "
                                                  "average, 62% above the 50-day).")
            with open(os.path.join(d, "breadth.csv"), "w") as fh:
                fh.write("not,a,breadth,file\n")
            self.assertEqual(scan.regime_note(d), "")
            with open(os.path.join(d, "breadth.csv"), "w") as fh:
                fh.write("")
            self.assertEqual(scan.regime_note(d), "")
        finally:
            shutil.rmtree(d)

    # ---- Mansfield and Dorsey

    def test_mansfield_and_dorsey(self):
        stock = [(D(2026, 1, 5), 100.0), (D(2026, 1, 6), 110.0), (D(2026, 1, 7), 121.0)]
        flat = {D(2026, 1, 5): 100.0, D(2026, 1, 6): 100.0, D(2026, 1, 7): 100.0}
        out = scan.mansfield_series(stock, flat, 2)
        self.assertEqual([round(r[1], 6) for r in out], [100.0, 110.0, 121.0])         # Dorsey
        self.assertIsNone(out[0][2])
        self.assertAlmostEqual(out[2][2], 4.76, places=2)                              # (121 / 115.5 - 1) x 100
        self.assertAlmostEqual(out[1][2], (110 / 105.0 - 1) * 100, places=6)
        del flat[D(2026, 1, 6)]                                                        # joined on the common dates
        self.assertEqual([r[0] for r in scan.mansfield_series(stock, flat, 2)], [D(2026, 1, 5), D(2026, 1, 7)])
        self.assertEqual(scan.MANSFIELD_DAILY, 22)

    def test_rs_positive_means_positive_and_rising(self):
        idx = {r[0]: 100.0 for r in long_rows([100.0] * 260)}
        convex = long_rows([100 * (1 + 0.00001 * i * i) for i in range(260)])       # outperforming, and speeding up
        linear = long_rows([100 + 0.5 * i for i in range(260)])             # outperforming, but the edge is fading
        weak = long_rows([200 - 0.2 * i for i in range(260)])               # underperforming
        no = scan.longterm_tags(scan.longterm_metrics(convex))
        self.assertNotIn("rs_positive", no)                                 # no benchmark: the tag is off
        self.assertIsNone(scan.longterm_metrics(convex)["mansfield_rs"])
        for name, rows, tagged in (("convex", convex, True), ("linear", linear, False), ("weak", weak, False)):
            m = scan.longterm_metrics(rows, idx)
            self.assertEqual("rs_positive" in scan.longterm_tags(m), tagged, name)
        self.assertGreater(scan.longterm_metrics(linear, idx)["mansfield_rs"], 0)         # positive, not rising
        self.assertLess(scan.longterm_metrics(weak, idx)["mansfield_rs"], 0)
        hits, _, _ = scan.scan_longterm({"CVX": convex, "LIN": linear}, ["CVX", "LIN"], screen="rs_positive",
                                        benchmark=idx)
        self.assertEqual([h["symbol"] for h in hits], ["CVX"])
        want = scan.mansfield_series([(r[0], r[4]) for r in convex], idx)[-1][2]
        self.assertEqual(hits[0]["mansfield_rs"], round(want, 2))
        hits, _, _ = scan.scan_longterm({"CVX": convex}, ["CVX"])
        self.assertIsNone(hits[0]["mansfield_rs"])

    def test_the_benchmark_order(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(scan.read_benchmark(d)[1:2], (None,))
            self.assertIn("no benchmark", scan.read_benchmark(d)[2])
            os.makedirs(os.path.join(d, "daily"))
            os.makedirs(os.path.join(d, "index"))

            def put(path, close):
                with open(os.path.join(d, path), "w") as fh:
                    fh.write("date,open,high,low,close,volume,pe,pb,div_yield\n2026-01-05,1,1,1,%s,0,,,\n" % close)
            put(os.path.join("daily", "NIFTYBEES.csv"), 1)
            self.assertEqual(scan.read_benchmark(d)[0], {D(2026, 1, 5): 1.0})
            self.assertEqual(scan.read_benchmark(d)[1], "NIFTYBEES")
            put(os.path.join("daily", "NIFTYBEES.adj.csv"), 2)
            self.assertEqual(scan.read_benchmark(d)[0], {D(2026, 1, 5): 2.0})        # .adj.csv wins
            put(os.path.join("index", "nifty-50.csv"), 3)
            bench, name, note = scan.read_benchmark(d)
            self.assertEqual((bench, name, note), ({D(2026, 1, 5): 3.0}, "Nifty 50 index", ""))   # the index wins
        finally:
            shutil.rmtree(d)

    # ---- the commands

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "data", "daily"))

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def put_daily(self, sym, closes, adj=True, start=D(2025, 6, 2), folder="daily"):
        path = os.path.join("trader", "data", folder)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, sym + (".adj.csv" if adj else ".csv")), "w") as fh:
            fh.write("date,open,high,low,close,volume,pe,pb,div_yield\n" if folder == "index"
                     else "date,open,high,low,close,volume\n")
            for i, c in enumerate(closes):
                day = start + datetime.timedelta(days=i)
                tail = ",,," if folder == "index" else ""
                fh.write("%s,%.2f,%.2f,%.2f,%.2f,200000%s\n" % (day.isoformat(), c, c + 0.5, c - 0.5, c, tail))

    def test_the_breadth_command(self):
        self.put_daily("UPS", [100 + i for i in range(60)])
        self.put_daily("DNS", [200 - i for i in range(60)])
        self.put_daily("ZIG", [100 + (i % 2) * 3 for i in range(60)])
        self.put_daily("RAW", [100 + i for i in range(60)], adj=False)          # only an unadjusted file
        code, text, err = run(["breadth"])
        self.assertEqual(code, 0, err)
        self.assertIn("3 symbols", text)
        self.assertIn("adjusted .adj.csv files only", text)
        self.assertIn("Skipped 1 with only an unadjusted .csv", text)
        self.assertIn("survivorship-biased", text)
        self.assertIn("above the 50-day average", text)
        self.assertIn("under 200 sessions of history", text)
        path = os.path.join("trader", "data", "breadth.csv")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc,net_adv_ratio,"
                                   "fast_ema,slow_ema")
        self.assertEqual(len(lines), 1 + 59)                                  # 60 dates, the first has no previous close
        first, last = lines[1].split(","), lines[-1].split(",")
        self.assertEqual(first[0], "2025-06-03")
        self.assertEqual(first[1], "")                                        # under 50 bars: no 50-day average yet
        self.assertEqual(first[7], "")                                        # no EMA before the 19th date
        self.assertNotEqual(last[1], "")                                      # 60 bars: the 50-day exists
        self.assertEqual(last[2], "")                                         # the 200-day does not
        self.assertNotEqual(last[5], "")                                      # 59 dates: the oscillator exists
        # UPS up, DNS down, ZIG alternates: the ratio is +/-33.33 on ZIG's days
        ratios = {float(l.split(",")[6]) for l in lines[1:]}
        self.assertEqual(ratios, {33.33, -33.33})
        code, text, err = run(["breadth", "--unadjusted", "--json", "--no-write"])
        doc = json.loads(text)
        self.assertEqual((doc["symbols"], doc["skipped_unadjusted"], doc["rows"]), (4, 0, 59))
        self.assertEqual(doc["caveat"], scan.BREADTH_CAVEAT)
        self.assertEqual(doc["last"]["date"], last[0])
        code, text, _ = run(["breadth", "--days", "10", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["rows"], 10)
        code, _, err = run(["breadth", "--symbols", "RAW", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("unadjusted", err)
        self.assertIn("trader-data/data.py daily", err)
        code, _, err = run(["breadth", "--symbols", "GONE", "--no-write"])
        self.assertEqual(code, 1)

    def test_other_scans_print_the_regime_line_once_breadth_exists(self):
        self.put_daily("UPS", [100 + i for i in range(60)])
        for argv in (["longterm", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write"],
                     ["daily", "--cache", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write",
                      "--min-price", "0", "--min-volume", "0"],
                     ["patterns", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write"]):
            code, text, _ = run(argv)
            self.assertNotIn("Market breadth", text, argv[0])
        scan.write_breadth(os.path.join("trader", "data", "breadth.csv"), [
            {"date": "2026-10-28", "pct_50": 30.0, "pct_200": 35.0, "net_new_highs": -4, "ad_line": -1.0,
             "mcclellan_osc": -7.5, "net_adv_ratio": -2.0, "fast_ema": 0.0, "slow_ema": 0.0}])
        want = "Market breadth 2026-10-28: weak (35% above the 200-day average, 30% above the 50-day, McClellan -7.5)."
        for argv in (["longterm", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write"],
                     ["daily", "--cache", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write",
                      "--min-price", "0", "--min-volume", "0"],
                     ["patterns", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write"],
                     ["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--min-price", "0", "--no-write"]):
            code, text, _ = run(argv)
            self.assertIn(want, text, argv[0])
        code, text, _ = run(["longterm", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["regime"], want)
        code, text, _ = run(["daily", "--cache", "--symbols", "UPS", "--now", "2026-12-01 08:30", "--no-write",
                             "--min-price", "0", "--min-volume", "0", "--json"])
        self.assertEqual(json.loads(text)["regime"], want)

    def test_longterm_rs_with_and_without_a_benchmark(self):
        stock = [100 * (1 + 0.00001 * i * i) for i in range(260)]
        self.put_daily("OUT", stock, adj=False)
        self.put_daily("OUT2", [100.0] * 260, adj=False)
        code, text, err = run(["longterm", "--symbols", "OUT", "--now", "2026-12-01 08:30", "--no-write"])
        self.assertIn("Note: no benchmark", text)
        code, text, _ = run(["longterm", "--symbols", "OUT", "--now", "2026-12-01 08:30", "--no-write", "--json"])
        doc = json.loads(text)
        self.assertIsNone(doc["benchmark"])
        self.assertIn("no benchmark", doc["benchmark_note"])
        self.assertIsNone(doc["hits"][0]["mansfield_rs"])
        self.put_daily("nifty-50", [100.0] * 260, adj=False, folder="index")
        code, text, _ = run(["longterm", "--symbols", "OUT,OUT2", "--screen", "rs_positive", "--now",
                             "2026-12-01 08:30", "--no-write", "--json"])
        doc = json.loads(text)
        self.assertEqual(doc["benchmark"], "Nifty 50 index")
        self.assertNotIn("benchmark_note", doc)
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["OUT"])
        self.assertGreater(doc["hits"][0]["mansfield_rs"], 0)
        code, text, _ = run(["longterm", "--symbols", "OUT", "--screen", "rs_positive", "--now", "2026-12-01 08:30",
                             "--no-write"])
        self.assertIn("relative strength over the Nifty positive and rising", text)
        self.assertNotIn("Note: no benchmark", text)


DELIV_SPIKE = os.path.join(SDIR, "delivery-SPIKE.csv")      # 30 sessions at 100 delivered, the last at 300
DELIV_BE = os.path.join(SDIR, "delivery-BE.csv")            # the same on the BE (trade-to-trade) series
DELIV_MISSING = os.path.join(SDIR, "delivery-MISSING.csv")  # every third delivery figure empty


def drows(qty, qpt, series="EQ", end=D(2026, 10, 16)):
    """Delivery rows (as read_delivery_csv returns them) from two lists, the last for `end`."""
    n = len(qty)
    return [{"date": end - datetime.timedelta(days=n - 1 - i), "deliv_qty": qty[i], "qty_per_trade": qpt[i],
             "series": series} for i in range(n)]


class Delivery(unittest.TestCase):
    """The delivery_spike tag and dq (eod2.md idea 2): fixtures are made-up numbers in trader-data's layout."""

    def test_the_spike_fixture(self):
        rows = scan.read_delivery_csv(DELIV_SPIKE)
        self.assertEqual((len(rows), rows[0]["date"], rows[-1]["date"]), (31, D(2026, 9, 16), D(2026, 10, 16)))
        r = scan.delivery_reading(rows, D(2026, 10, 16))
        # the window is the last 30 sessions: 29 at 100 and the latest at 300, mean 106.67
        self.assertAlmostEqual(r["dq"], 300 / (3200 / 30.0), places=6)
        self.assertAlmostEqual(r["dq"], 2.8125, places=4)
        self.assertAlmostEqual(r["tq"], 2.8125, places=4)         # volume 3000 over 20 trades: 150 a trade, mean 53.33
        self.assertEqual((r["spike"], r["series"], r["date"]), (True, "EQ", "2026-10-16"))
        r = scan.delivery_reading(rows, D(2026, 10, 15))           # the price data is for another day: no tag
        self.assertFalse(r["spike"])
        self.assertAlmostEqual(r["dq"], 2.8125, places=4)
        self.assertTrue(scan.delivery_reading(rows)["spike"])

    def test_trade_to_trade_series_never_tag(self):
        rows = scan.read_delivery_csv(DELIV_BE)
        r = scan.delivery_reading(rows, D(2026, 10, 16))
        self.assertEqual(r["series"], "BE")
        self.assertFalse(r["spike"])
        self.assertAlmostEqual(r["dq"], 2.8125, places=4)           # the ratio is still reported
        self.assertFalse(scan.delivery_reading(drows([100] * 29 + [300], [50] * 29 + [150], "BZ"))["spike"])
        self.assertTrue(scan.delivery_reading(drows([100] * 29 + [300], [50] * 29 + [150], ""))["spike"])

    def test_missing_figures_are_left_out_of_the_mean(self):
        rows = scan.read_delivery_csv(DELIV_MISSING)
        self.assertIsNone(rows[1]["deliv_qty"])                      # empty, not zero
        self.assertEqual(rows[1]["qty_per_trade"], 50.0)
        r = scan.delivery_reading(rows, D(2026, 10, 16))
        # the last 30 sessions have 10 empty days: 19 at 100 and the latest 300, mean 110
        self.assertAlmostEqual(r["dq"], 300 / 110.0, places=6)
        self.assertTrue(r["spike"])

    def test_not_enough_values_no_reading(self):
        self.assertIsNone(scan.delivery_reading([]))
        self.assertIsNone(scan.delivery_reading(None))
        few = drows([100, 100, 100, 300], [50, 50, 50, 150])                   # 4 values
        r = scan.delivery_reading(few)
        self.assertEqual((r["dq"], r["tq"], r["spike"]), (None, None, False))
        five = drows([100, 100, 100, 100, 300], [50, 50, 50, 50, 150])
        self.assertTrue(scan.delivery_reading(five)["spike"])                  # 5 is enough
        gaps = drows([100, None, 100, None, 100, None, 100, 300], [50] * 7 + [150])
        r = scan.delivery_reading(gaps)                                        # 5 quantities, 8 trade sizes
        self.assertTrue(r["spike"])
        late = drows([100] * 9 + [None], [50] * 9 + [150])                     # the latest day has no figure
        r = scan.delivery_reading(late)
        self.assertEqual((r["dq"], r["spike"]), (None, False))
        self.assertAlmostEqual(r["tq"], 150 / ((9 * 50 + 150) / 10.0), places=6)

    def test_both_ratios_must_clear_1_2(self):
        self.assertEqual(scan.DELIVERY_RATIO, 1.2)
        self.assertEqual((scan.DELIVERY_BARS, scan.DELIVERY_MIN), (30, 5))
        # delivery quantity up but the average trade size flat: no tag
        self.assertFalse(scan.delivery_reading(drows([100] * 4 + [300], [50] * 5))["spike"])
        self.assertFalse(scan.delivery_reading(drows([100] * 5, [50] * 4 + [150]))["spike"])
        # 1.2 x the base is 1.15 x the mean here: under 1.2, then 1.4 x the base is 1.30: over
        self.assertFalse(scan.delivery_reading(drows([100] * 4 + [120], [50] * 4 + [60]))["spike"])
        self.assertTrue(scan.delivery_reading(drows([100] * 4 + [140], [50] * 4 + [70]))["spike"])

    def test_only_the_last_30_sessions_count(self):
        qty = [10000] * 10 + [100] * 29 + [300]
        r = scan.delivery_reading(drows(qty, [50] * 39 + [150]))
        self.assertAlmostEqual(r["dq"], 2.8125, places=4)

    def test_reading_the_file(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "X.csv")
            with open(path, "w") as fh:
                fh.write("date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series\n"
                         "2026-10-14,1000,20,100,10,50,eq\n"
                         "not-a-date,1,1,1,1,1,EQ\n"
                         "2026-10-15,1000,20,-,,abc,EQ\n"
                         "2026-10-14,1000,20,120,12,50,EQ\n"                  # a later row for a date wins
                         "2026-10-16,1000,0,150,15,,\n")
            rows = scan.read_delivery_csv(path)
            self.assertEqual([r["date"] for r in rows], [D(2026, 10, 14), D(2026, 10, 15), D(2026, 10, 16)])
            self.assertEqual([r["deliv_qty"] for r in rows], [120.0, None, 150.0])
            self.assertEqual([r["qty_per_trade"] for r in rows], [50.0, None, None])
            self.assertEqual([r["series"] for r in rows], ["EQ", "EQ", ""])
            with open(path, "w") as fh:
                fh.write("date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series\n")
            self.assertEqual(scan.read_delivery_csv(path), [])
            self.assertEqual(scan.load_delivery(["X", "GONE"], d), {})              # no delivery/ folder: nothing
            os.makedirs(os.path.join(d, "delivery"))
            shutil.copy(DELIV_SPIKE, os.path.join(d, "delivery", "X.csv"))
            self.assertEqual(list(scan.load_delivery(["X", "GONE"], d)), ["X"])
        finally:
            shutil.rmtree(d)

    def flat_rows(self, n=260, volume=200000, end=D(2026, 10, 16)):
        start = end - datetime.timedelta(days=n - 1)
        return [(start + datetime.timedelta(days=i), 100.0, 100.5, 99.5, 100.0, float(volume)) for i in range(n)]

    def test_scan_daily_tag_and_dq(self):
        rows = self.flat_rows(60)
        series = scan.series_from_daily(rows)
        deliv = {"SPK": scan.read_delivery_csv(DELIV_SPIKE), "OLD": scan.read_delivery_csv(DELIV_SPIKE)[:-1],
                 "QUIET": drows([100] * 30, [50] * 30), "BE": scan.read_delivery_csv(DELIV_BE)}
        data = {s: series for s in ("SPK", "OLD", "QUIET", "BE", "NONE")}
        hits, counts, last = scan.scan_daily(data, ["SPK", "OLD", "QUIET", "BE", "NONE"],
                                             scan.parse_now("2026-10-19 08:45"), 0, 0, deliv)
        self.assertEqual([h["symbol"] for h in hits], ["SPK"])                  # the only one with a tag
        h = hits[0]
        self.assertEqual(h["setups"], ["delivery_spike"])
        self.assertAlmostEqual(h["dq"], 2.81, places=2)
        self.assertEqual(h["dq_date"], "2026-10-16")
        hits, _, _ = scan.scan_daily(data, ["SPK", "NONE"], scan.parse_now("2026-10-19 08:45"), 0, 0)
        self.assertEqual(hits, [])                                              # no delivery data: no tag
        self.assertIn("delivery_spike", scan.LABEL)

    def test_scan_longterm_tag_and_dq(self):
        rows = self.flat_rows(260)
        deliv = {"SPK": scan.read_delivery_csv(DELIV_SPIKE), "BE": scan.read_delivery_csv(DELIV_BE)}
        hits, _, _ = scan.scan_longterm({"SPK": rows, "BE": rows, "NONE": rows}, ["SPK", "BE", "NONE"],
                                        screen="delivery_spike", delivery=deliv)
        self.assertEqual([h["symbol"] for h in hits], ["SPK"])
        self.assertEqual(hits[0]["setups"], ["delivery_spike"])
        self.assertAlmostEqual(hits[0]["dq"], 2.81, places=2)
        hits, _, _ = scan.scan_longterm({"BE": rows, "NONE": rows}, ["BE", "NONE"], delivery=deliv)
        by = {h["symbol"]: h for h in hits}
        self.assertAlmostEqual(by["BE"]["dq"], 2.81, places=2)                   # reported, not tagged
        self.assertNotIn("delivery_spike", by["BE"]["setups"])
        self.assertIsNone(by["NONE"]["dq"])
        self.assertIn("delivery_spike", scan.LT_LABEL)

    def test_the_commands_read_trader_data_delivery(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.makedirs(os.path.join("trader", "data", "daily"))
            os.makedirs(os.path.join("trader", "data", "delivery"))
            with open(os.path.join("trader", "data", "daily", "SPK.csv"), "w") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for r in self.flat_rows(260):
                    fh.write("%s,%s,%s,%s,%s,%d\n" % (r[0].isoformat(), r[1], r[2], r[3], r[4], r[5]))
            shutil.copy(DELIV_SPIKE, os.path.join("trader", "data", "delivery", "SPK.csv"))
            argv = ["--symbols", "SPK", "--no-write", "--min-price", "0", "--min-volume", "0"]
            code, text, err = run(["daily", "--cache", "--now", "2026-10-19 08:45", "--json"] + argv)
            self.assertEqual(code, 0, err)
            doc = json.loads(text)
            self.assertEqual(doc["hits"][0]["setups"], ["delivery_spike"])
            self.assertAlmostEqual(doc["hits"][0]["dq"], 2.81, places=2)
            self.assertEqual(doc["delivery_symbols"], 1)
            code, text, _ = run(["daily", "--cache", "--now", "2026-10-19 08:45"] + argv)
            self.assertIn("delivery spike (institutional-size trades, high delivery) (dq 2.8)", text)
            self.assertIn("Delivery data (trader/data/delivery) for 1 of the symbols", text)
            code, text, _ = run(["longterm", "--screen", "delivery_spike", "--now", "2026-10-19 08:45", "--json",
                                 "--symbols", "SPK", "--no-write", "--min-volume", "0"])
            doc = json.loads(text)
            self.assertEqual([h["setups"] for h in doc["hits"]], [["delivery_spike"]])
            self.assertEqual(doc["hits"][0]["dq_date"], "2026-10-16")
            os.remove(os.path.join("trader", "data", "delivery", "SPK.csv"))        # the track that writes it is absent
            code, text, _ = run(["longterm", "--now", "2026-10-19 08:45", "--json", "--symbols", "SPK", "--no-write"])
            doc = json.loads(text)
            self.assertIsNone(doc["hits"][0]["dq"])
            self.assertNotIn("delivery_symbols", doc)
            code, text, _ = run(["daily", "--cache", "--now", "2026-10-19 08:45"] + argv)
            self.assertEqual(code, 1)
            self.assertNotIn("Delivery data", text)
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
