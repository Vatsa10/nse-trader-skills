"""trader-exit tests. Bars for the ATR stop reuse tests/fixtures/trader-timemachine/RELIANCE-60d5m.json.gz
(SYNTHETIC NSE bars: real US five-minute bars moved onto the 09:15-15:30 IST grid and renamed
RELIANCE.NS; the prices are not RELIANCE's)."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")

RIL = os.path.join(FIX, "trader-timemachine", "RELIANCE-60d5m.json.gz")

# trader/plan.json for the reel, exactly as the contract writes it
REEL_PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
             "entry": 364.18, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
             "opening_range": {"high": 362.18, "low": 354.47, "minutes": 15},
             "stop": 358.32, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
             "target": 370.04, "target_r": 1.0, "target_label": "1R (one times the risk)",
             "risk_per_share": 5.86, "reward_risk": 1.0, "flat_by": "15:15"}
REEL_ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-19",
              "status": "triggered", "time": "10:00 AM", "bar_start": "09:55", "bar_index": 8, "price": 364.18,
              "level": 362.18, "interval": "5m", "opening_range": {"high": 362.18, "low": 354.47, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-19T04:30:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=362.18, ol=354.47, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_the_reel_or_mid_1r(self):
        p = c(364.18, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("358.32"))           # floor(358.325)
        self.assertEqual(p["risk"], Decimal("5.86"))
        self.assertEqual(p["target"], Decimal("370.04"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertIn("rounded down", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(364.18, "or-mid")
        self.assertEqual(p["target"], Decimal("375.90"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_cents(self):
        p = c(364.1759948730469, "or-mid", "1R", 362.17999267578125, 354.4700012207031)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("364.18"), Decimal("358.32"), Decimal("370.04")))

    def test_other_stops(self):
        p = c(364.18, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("354.47"), Decimal("9.71"), Decimal("383.60")))
        p = c(364.18, "price:358.329")
        self.assertEqual(p["stop"], Decimal("358.32"))              # rounds away from the entry
        p = c(364.18, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("360.53"), Decimal("3.65"), Decimal("371.48")))
        p = c(364.18, "atr:1.5", atr=3.2181, n=9)
        self.assertEqual(p["stop"], Decimal("359.35"))              # 364.18 - 4.82715 = 359.35285
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(364.18, "or-mid", "price:372")
        self.assertEqual(p["reward_risk"], Decimal("1.33"))
        self.assertEqual(p["target_label"], "your price (1.33R)")
        self.assertEqual(p["target_r"], Decimal("1.33"))

    def test_refusals_say_why(self):
        cases = [("price:364.18", "2R", "at or above the entry"),
                 ("price:365", "2R", "at or above the entry"),
                 ("or-mid", "price:364", "at or below the entry"),
                 ("price:364.01", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(364.18, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 364.18 is 0.182: a ₹0.19 risk is allowed, ₹0.18 is not
        self.assertEqual(c(364.18, "price:363.99")["risk"], Decimal("0.19"))
        with self.assertRaises(exitp.PlanError):
            c(364.18, "price:364.00")
        with self.assertRaises(exitp.PlanError):
            c(358.00, "or-mid")                                     # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(364.18, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(364.18, "or-mid", oh=354.47, ol=362.18)

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, RIL)
        day = exitp.datetime.date(2026, 10, 19)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=8)
        self.assertEqual(len(bars), 9)                              # 09:15 ... the 09:55 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 362.18 - 354.47, places=4)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 9)
        self.assertAlmostEqual(atr, sum(trs) / 9, places=9)
        self.assertAlmostEqual(atr, 3.2182, places=4)
        # later in the day it uses the last 14 bars; the file's in-progress 10:30 bar and tick never count
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 15)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 10:01"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          \u20b9358.32  opening-range midpoint", text)
        self.assertIn("Target        \u20b9370.04  1R (one times the risk)", text)
        self.assertIn("Risk a share  \u20b95.86 (1.61% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertIn("intraday charges, estimate", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 375.90)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-19 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("\u20b9375.90", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-20 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance.ns", "--entry", "364.18", "--or-high", "362.18", "--or-low", "354.47",
                                  "--time", "10:00", "--entry-bar", "8", "--date", "2026-10-19", "--target", "1R",
                                  "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", RIL, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (359.35, 4.83, 373.84))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 9))
        self.assertAlmostEqual(p["atr"], 3.2182, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", RIL, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "362.19", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (362.19, 358.32, None))

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, setup="level", level=362.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("v2 plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:364.10")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "364.18", "--or-high", "362.18")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_mis_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:00", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:00")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))

        code, text, _ = self.plan("--flat-by", "15:25")
        self.assertEqual(code, 0)
        self.assertIn("MIS auto square-off", text)
        code, _, err = self.plan("--flat-by", "15:30")
        self.assertEqual(code, 1)
        self.assertIn("after the 3:30 PM close", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, time="3:20 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_holidays_costs_and_circuit_bands(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, date="2026-10-20"))
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-20 10:01", "--json"])
        self.assertIn("NSE holiday: Dussehra.", json.loads(text)["warnings"])
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, date="2027-01-04"))
        code, text, _ = run(exitp, ["plan", "--now", "2027-01-04 10:01", "--json"])
        self.assertTrue(any("covers 2026 only" in w for w in json.loads(text)["warnings"]))
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, _ = self.plan("--qty", "100", "--json")
        p = json.loads(text)
        self.assertEqual(p["costs"]["qty"], 100)
        self.assertTrue(0 < p["costs"]["at_stop"] < p["costs"]["at_target"] < 100)
        self.assertLess(p["net_reward_risk"], p["reward_risk"])
        self.assertFalse(any("circuit" in w for w in p["warnings"]))
        code, text, _ = self.plan("--stop", "pct:6", "--json")
        self.assertTrue(any("5% circuit band" in w for w in json.loads(text)["warnings"]))
        code, text, _ = self.plan("--stop", "pct:3", "--band", "2", "--json")
        self.assertTrue(any("2% circuit band" in w for w in json.loads(text)["warnings"]))

    def test_inr_and_costs(self):
        self.assertEqual(exitp.inr(1234567.891), "\u20b912,34,567.89")
        self.assertEqual(exitp.inr(999), "\u20b9999.00")
        self.assertEqual(exitp.inr(100000), "\u20b91,00,000.00")
        c = exitp.costs(1000, 1010, 100)            # turnover 2,01,000: brokerage 20 + 20 cap
        self.assertEqual(c["brokerage"], Decimal("40.00"))
        self.assertEqual(c["stt"], Decimal("25.25"))
        self.assertEqual(c["stamp"], Decimal("3.00"))
        self.assertEqual(exitp.yahoo_symbol("m&m"), "M&M.NS")
        with self.assertRaises(exitp.PlanError):
            exitp.nse_symbol("NIFTY26NOV24000CE")

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


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
        """A newly loaded exit.py that reads trader/data/calendar.json from the current folder."""
        with open(os.path.join("trader", "data", "calendar.json"), "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return load("trader-exit", "exit")

    def warnings(self, mod, day):
        with open(os.path.join("trader", "entry.json"), "w") as fh:
            json.dump(dict(REEL_ENTRY, date=day), fh)
        code, text, err = run(mod, ["plan", "--now", "%s 10:01" % day, "--json"])
        self.assertEqual(code, 0, err)
        return json.loads(text)["warnings"]

    def test_2027_holiday_closed_and_weekday_open(self):
        x = self.fresh(CAL_2027)
        self.assertIn("NSE holiday: Republic Day.", self.warnings(x, "2027-01-26"))
        self.assertIsNone(x.session(datetime.date(2027, 1, 26)))
        w = self.warnings(x, "2027-01-27")
        self.assertFalse(any("holiday" in t or "covers" in t for t in w), w)
        self.assertEqual(x.session(datetime.date(2027, 1, 27)), (x.OPEN, x.CLOSE))
        self.assertIn(2027, x.HOLIDAY_YEARS)
        self.assertNotIn("2027-01-26", exitp.NSE_HOLIDAYS)               # the module loaded at import is untouched

    def test_special_saturday_opens_and_muhurat_closes_the_regular_market(self):
        x = self.fresh(CAL_2027)
        self.assertEqual(x.session(datetime.date(2027, 2, 6)), (x.OPEN, x.CLOSE))
        self.assertEqual(x.holiday_note(datetime.date(2027, 2, 6)), "")
        self.assertIsNone(x.session(datetime.date(2027, 10, 29)))        # a Friday
        w = " ".join(self.warnings(x, "2027-10-29"))
        self.assertIn("Muhurat", w)
        self.assertIn("no normal 09:15-15:30 market", w)

    def test_builtin_2026_still_holds_and_other_years_still_warn(self):
        x = self.fresh(CAL_2027)
        self.assertIn("NSE holiday: Dussehra.", self.warnings(x, "2026-10-20"))
        self.assertTrue(any("covers 2026, 2027 only" in t for t in self.warnings(x, "2028-01-03")))

    def test_missing_or_corrupt_file_falls_back_to_the_builtin_table(self):
        self.assertEqual(load("trader-exit", "exit").HOLIDAY_YEARS, (2026,))    # no file at all
        for bad in ("{not json", "[1, 2]", "", '{"years": "x", "holidays": []}'):
            x = self.fresh(bad)
            self.assertEqual(x.HOLIDAY_YEARS, (2026,), bad)
            self.assertTrue(any("covers 2026 only" in t for t in self.warnings(x, "2027-01-26")), bad)
            self.assertIn("NSE holiday: Dussehra.", self.warnings(x, "2026-10-20"), bad)


class V4DataChargesBands(unittest.TestCase):
    """v4: the trader-data caches, the shared charges model and bands.json."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "data", "intraday"))
        with open(os.path.join("trader", "entry.json"), "w") as fh:
            json.dump(REEL_ENTRY, fh)
        self.saved = exitp.yahoo_get

    def tearDown(self):
        exitp.yahoo_get = self.saved
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 10:01"] + list(args))

    def put_cache(self):
        import gzip
        with gzip.open(RIL, "rt", encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join("trader", "data", "intraday", "RELIANCE-5m.json"), "w") as fh:
            fh.write(text)

    def test_default_charges_match_the_spec(self):
        c = exitp.costs(1000, 1010, 100, model=exitp.load_charges(""))
        self.assertEqual(c["brokerage"], Decimal("40.00"))
        self.assertEqual(c["stt"], Decimal("25.25"))
        self.assertEqual(c["txn"], Decimal("6.17"))                  # 2,01,000 x 0.00307%
        self.assertEqual(c["stamp"], Decimal("3.00"))
        self.assertEqual(c["dp"], Decimal("0.00"))
        d = exitp.costs(1000, 1010, 100, intraday=False, model=exitp.load_charges(""))
        self.assertEqual(d["brokerage"], Decimal("0.00"))
        self.assertEqual(d["stt"], Decimal("201.00"))
        self.assertEqual(d["dp"], Decimal("15.34"))
        self.assertEqual(exitp.charges_label(exitp.load_charges("")), "estimate")
        self.assertEqual(exitp.CHARGES_DEFAULT["exchange_txn_pct"]["NSE"], 0.00307)

    def test_charges_json_is_merged_and_calibrated(self):
        base = exitp.costs(1000, 1010, 100, model=exitp.load_charges(""))["total"]
        self.write(os.path.join("trader", "charges.json"),
                   {"calibration": {"factor": 1.1, "samples": 3, "updated": "2026-10-28"}})
        m = exitp.load_charges()
        self.assertEqual(m["intraday"]["brokerage_cap"], 20)         # the rest is kept
        self.assertEqual(exitp.charges_label(m), "estimate (calibrated from 3 contract notes)")
        cal = exitp.costs(1000, 1010, 100, model=m)["total"]
        self.assertAlmostEqual(float(cal), float(base) * 1.1, places=1)
        code, text, err = self.plan("--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertIn("calibrated from 3 contract notes", p["costs"]["label"])
        self.assertEqual(p["costs"]["calibration_samples"], 3)
        code, text, _ = self.plan()
        self.assertIn("estimate (calibrated from 3 contract notes)", text)

    def test_bands_json_gives_the_real_circuit(self):
        self.write(os.path.join("trader", "data", "bands.json"),
                   {"as_of": "2026-10-19", "source": "nse sec_bhavdata", "bands": {
                       "RELIANCE": {"band_pct": 2, "lower": 360.0, "upper": 375.0}}})
        code, text, err = self.plan("--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual(p["band"]["band_pct"], 2)
        self.assertTrue(any("lower circuit \u20b9360.00" in w and "2% band" in w for w in p["warnings"]), p["warnings"])
        code, text, _ = self.plan()
        self.assertIn("Circuit band  2%, lower circuit \u20b9360.00", text)
        # a wide band and a far lower circuit: no warning; a no-band stock says so
        self.write(os.path.join("trader", "data", "bands.json"),
                   {"as_of": "2026-10-19", "source": "x", "bands": {
                       "RELIANCE": {"band_pct": 20, "lower": 290.0, "upper": 435.0}}})
        p = json.loads(self.plan("--stop", "pct:6", "--json")[1])
        self.assertFalse(any("circuit" in w for w in p["warnings"]), p["warnings"])   # beats the 5% heuristic
        self.write(os.path.join("trader", "data", "bands.json"),
                   {"as_of": "2026-10-16", "source": "x", "bands": {"RELIANCE": {"band_pct": None,
                                                                                "lower": None, "upper": None}}})
        p = json.loads(self.plan("--json")[1])
        self.assertTrue(any("no price band" in w for w in p["warnings"]))
        self.assertTrue(any("not the plan's day" in w for w in p["warnings"]))

    def test_atr_falls_back_to_the_cache(self):
        def boom(path):
            raise exitp.PlanError("Yahoo did not answer (HTTP 403 on query1 and query2)")
        exitp.yahoo_get = boom
        code, _, err = self.plan("--stop", "atr:1.5", "--json")
        self.assertEqual(code, 1)
        self.assertIn("data.py doctor", err)
        self.assertIn("import-kite", err)
        self.assertIn("get_historical_data", err)
        self.put_cache()
        code, text, err = self.plan("--stop", "atr:1.5", "--json")
        self.assertEqual(code, 0, err)
        self.assertIn("using cached data from 2026-10-19", err)
        self.assertIn("trader-data/data.py intraday RELIANCE", err)
        self.assertEqual(json.loads(text)["atr_bars"], 9)

    def test_cache_flag_skips_yahoo(self):
        calls = []
        exitp.yahoo_get = lambda path: calls.append(path)
        self.put_cache()
        code, text, err = self.plan("--stop", "atr:1.5", "--cache", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [])
        self.assertIn("using cached data", err)
        self.assertEqual(exitp.HOSTS, ("query1", "query2"))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("## Safety", text)
        self.assertIn("## Safety", risk)
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money", "/trader-timemachine", "/trader-budget", "longs only", "\u20b9358.32", "\u20b9370.04", "\u20b95.86", "15:15", "circuit", "approximate", "375.90", "too tight to survive the spread"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
