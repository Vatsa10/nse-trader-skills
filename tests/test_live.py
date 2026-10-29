"""Live checks against the real sources. Skipped unless NSE_LIVE=1.

    NSE_LIVE=1 python3 -m unittest tests.test_live -v
    NSE_LIVE=1 NSE_LIVE_STRICT=1 ...   # a blocked source (403) fails instead of skipping

Each test fetches into a temporary folder and checks shapes and sanity, e.g.
RELIANCE closes between 100 and 100000 and a full session has 75 bars.
"""

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

LIVE = os.environ.get("NSE_LIVE") == "1"
STRICT = os.environ.get("NSE_LIVE_STRICT") == "1"
SYM = os.environ.get("NSE_LIVE_SYMBOL", "RELIANCE")

data = load("trader-data", "data") if LIVE else None


@unittest.skipUnless(LIVE, "live sources: set NSE_LIVE=1")
class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dd = os.path.join(self.tmp, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_d(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = data.main(list(argv) + ["--data", self.dd])
        text = out.getvalue() + err.getvalue()
        sys.stderr.write("\n" + text)
        return code, text

    def ok_or_blocked(self, code, text):
        if code != 0 and not STRICT and any(k in text for k in ("403", "blocks", "bot wall", "401")):
            self.skipTest("blocked from this machine: " + text.strip().splitlines()[-1][:200])
        self.assertEqual(code, 0, text)

    def check_daily(self, min_rows):
        rows = data.read_daily(data.data_paths(self.dd, SYM)[0])
        self.assertGreaterEqual(len(rows), min_rows)
        errs, _ = data.validate_daily(rows)
        self.assertEqual(errs, [])
        for r in rows[-5:]:
            self.assertTrue(100 <= r["close"] <= 100000, r)
        last = datetime.date.fromisoformat(rows[-1]["date"])
        self.assertLessEqual((datetime.date.today() - last).days, 10)
        return rows

    def test_doctor(self):
        code, text = self.run_d("doctor")
        with open(os.path.join(self.dd, "sources.json")) as fh:
            s = json.load(fh)
        self.assertEqual(set(s["sources"]), set(n for n, _, _, _ in data.probes(data.parse_now(None))))
        self.assertEqual(code, 0 if s["price_sources_ok"] else 1)

    def test_daily_yahoo(self):
        code, text = self.run_d("daily", SYM, "--years", "1", "--source", "yahoo")
        self.ok_or_blocked(code, text)
        self.check_daily(200)
        self.assertTrue(os.path.exists(data.data_paths(self.dd, SYM)[1]))

    def test_daily_nse_api(self):
        code, text = self.run_d("daily", SYM, "--years", "0.5", "--source", "nse")
        self.ok_or_blocked(code, text)
        self.check_daily(100)

    def test_daily_bhavcopy(self):
        code, text = self.run_d("daily", SYM, "--years", "0.05", "--source", "bhavcopy")
        self.ok_or_blocked(code, text)
        self.check_daily(5)

    def test_daily_incremental_second_run_fetches_nothing(self):
        code, text = self.run_d("daily", SYM, "--years", "0.1")
        self.ok_or_blocked(code, text)
        code, text = self.run_d("daily", SYM)
        self.assertIn("up to date", text)

    def check_intraday(self):
        _, bars = data.read_intraday(data.data_paths(self.dd, SYM)[2])
        self.assertTrue(bars)
        self.assertEqual(data.validate_intraday(bars)[0], [])
        by = {}
        for b in bars:
            by.setdefault(data.ts_to_ist(b[0]).date(), []).append(b)
            self.assertTrue(100 <= b[4] <= 100000, b)
        today = (data.utc_now() + data.IST).date()
        full = [d for d in by if d != today]
        for d in full:
            self.assertEqual(len(by[d]), data.BARS_PER_SESSION, d)
        return by

    def test_intraday_yahoo(self):
        code, text = self.run_d("intraday", SYM, "--days", "5", "--source", "yahoo")
        self.ok_or_blocked(code, text)
        self.check_intraday()

    def test_intraday_nse_chart(self):
        code, text = self.run_d("intraday", SYM, "--source", "nse")
        self.ok_or_blocked(code, text)
        self.check_intraday()

    def check_bands(self):
        with open(os.path.join(self.dd, "bands.json")) as fh:
            b = json.load(fh)
        e = b["bands"][SYM]
        self.assertIn(e["band_pct"], (None, 2, 5, 10, 20))
        if e["lower"] is not None:
            self.assertLess(e["lower"], e["upper"])

    def test_bands_nse_quote(self):
        code, text = self.run_d("bands", SYM, "--source", "nse")
        self.ok_or_blocked(code, text)
        self.check_bands()

    def test_bands_sec_list(self):
        code, text = self.run_d("bands", SYM, "--source", "seclist")
        self.ok_or_blocked(code, text)
        self.check_bands()

    def test_nifty_list_and_news(self):
        sess = data.Session()
        try:
            code, body = sess.get(data.NIFTY_LIST)
        except data.SourceError as e:
            if STRICT:
                raise
            self.skipTest("niftyindices not reachable: %s" % e)
        if code == 403 and not STRICT:
            self.skipTest("niftyindices blocked")
        self.assertEqual(code, 200)
        self.assertIn("symbol", body[:500].decode("utf-8", "replace").lower())
        try:
            code, body = sess.get(data.GNEWS)
        except data.SourceError as e:
            if STRICT:
                raise
            self.skipTest("Google News not reachable: %s" % e)
        self.assertEqual(code, 200)
        self.assertIn(b"<rss", body[:500])

    def test_record_round_trip(self):
        out = os.path.join(self.tmp, "real")
        code, text = self.run_d("daily", SYM, "--years", "0.2")
        self.ok_or_blocked(code, text)
        code, text = self.run_d("record", SYM, "--no-fetch", "--out", out)
        self.assertEqual(code, 0, text)
        with open(os.path.join(out, SYM, "provenance.json")) as fh:
            self.assertTrue(json.load(fh)["fetched_at"])


if __name__ == "__main__":
    unittest.main()
