import copy
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
RIL = os.path.join(DIR, "RELIANCE-60d5m.json.gz")
# SYNTHETIC NSE data: real 5-min bars from a US stock, moved onto the 09:15-15:30 IST grid (75 bars a
# session, NSE holidays 09-14 and 10-02 dropped) and renamed RELIANCE.NS. The prices are not RELIANCE's.
with gzip.open(RIL, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-19 10:35"
NOW_UTC = tm.parse_now(NOW)

# the reel's plan, as trader-exit writes it (CONTRACT.md)
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
        "entry": 364.18, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
        "opening_range": {"high": 362.18, "low": 354.47, "minutes": 15},
        "stop": 358.32, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 370.04, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 5.86, "reward_risk": 1.0, "flat_by": "15:15"}

# fingerprints of the reel's refs/sim-out.json (sim.py mid 1, seed 19): proves bit-for-bit equality
SIM_BANDS = "e4d34fb7ffb4f673"
SIM_PATHS = "7216874e68232016"
SIM_TRADES = "06d6869f7bcff117"
SIM_HIST = {"lo": 352.63, "hi": 376.57,
            "bins": [105, 95, 157, 213, 247, 372, 416, 555, 664, 719, 764, 802, 795, 709, 704, 584, 488, 428,
                     344, 244, 185, 120, 79, 56]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def et(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_reel_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:15"))
        self.assertEqual(m["pool_sessions"], 57)
        self.assertEqual(m["bars_left"], 63)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-07-27", "2026-10-16"))
        self.assertEqual(m["p_target"], 0.2693)
        self.assertEqual(m["p_stop"], 0.2203)
        self.assertEqual(m["p_neither"], 0.5104)
        self.assertEqual(m["p_green"], 0.5249)
        self.assertEqual(m["median_close"], 364.41)
        self.assertEqual(m["p5_close"], 356.1)
        self.assertEqual(m["p95_close"], 372.78)
        self.assertEqual(m["avg_R"], 0.051)
        self.assertEqual(m["avg_R_after_costs"], 0.0)
        self.assertEqual((m["costs"]["qty"], m["costs"]["rupees_at_stop"], m["costs"]["rupees_at_target"]),
                         (274, 82.14, 83.07))
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit_with_the_reel_sim(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [356.39, 360.86, 364.39, 367.92, 373.46])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (57, 32))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (13, 15, 4, 14))
        self.assertEqual(h["avg_R"], -0.076)
        self.assertEqual(h["avg_R_after_costs"], -0.172)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-07-28", 307.97, 304.89, 311.05, "target"))

    def test_the_verdict_leans_to_the_target(self):
        self.assertEqual(self.doc["verdict"], "Leans toward the target: target first 26.9 % vs stop first 22.0 %.")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.55, 0.5))
        self.assertEqual(self.doc["verdict_notes"],
                         ["51.0 % of the replays hit neither by 3:15 PM; the price at 3:15 PM decides those."])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [364.18] * 5)
        self.assertEqual(len(m["bands"]), 64)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("10:00", "15:15", 64))
        self.assertTrue(all(p[0] == 364.18 and len(p) == 64 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 364.18, 358.32, 370.04, 8, "10:00 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (5.86, 1.0, "15:15"))
        self.assertEqual(self.doc["warnings"], [])

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Monday 2026-10-19: entry 364.18 at 10:00 AM (bar 8), stop 358.32, target 370.04",
                     "10,000 replays", "10:00 AM to 3:15 PM (63 five-minute bars)", "seed 19", "57 full sessions",
                     "target first   26.9 %", "stop first     22.0 %", "neither        51.0 %   still open at 3:15 PM",
                     "green          52.5 %", "median 364.41, 90 % of replays between 356.10 and 372.78",
                     "Average R: +0.051", "After intraday charges, estimate (274 shares: about \u20b982.14 at the "
                     "stop, \u20b983.07 at the target): average R +0.000",
                     "fired 32 times: 13 target, 15 stop, 4 neither. Average R -0.076. After costs -0.172.",
                     "Leans toward the target: target first 26.9 % vs stop first 22.0 %.\n51.0 % of the replays "
                     "hit neither by 3:15 PM; the price at 3:15 PM decides those.", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2693, 0.2203, 364.41))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the reel numbers above
        # are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 0, "off_grid": 1, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-19", "10:25", 14))
        # with the real clock (any time later) the data's own time still marks 10:30 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "10:25")
        entry = [b for b in bars if b["date"] == "2026-10-19" and b["slot"] == 8][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:55", 364.1759948730469))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-19 09:57"))[1]
        today = [b for b in bars if b["date"] == "2026-10-19"]
        self.assertEqual(len(today), 8)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-19 09:57"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:50 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-19 10:00"), replays=200)["mc"]["pool_sessions"], 57)

    def test_ist_is_a_fixed_offset(self):
        stamps = [1793331900,   # 2026-10-30 03:45Z = 09:15 IST
                  1793591100,   # 2026-11-02 03:45Z = 09:15 IST (no clock change in India)
                  1793587500,   # 2026-11-02 02:45Z = 08:15 IST, before the open
                  1793613600,   # 2026-11-02 10:00Z = 15:30 IST, the close: no bar starts there
                  1793613300]   # 2026-11-02 09:55Z = 15:25 IST, the last bar
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:25", 74)])
        self.assertEqual(dropped["outside_hours"], 2)
        self.assertEqual(tm.ist_to_utc(tm.to_ist(tm.utc_of(1793331900))), tm.utc_of(1793331900))

    def test_a_short_session_is_left_out(self):
        def cut(res):  # 2026-09-04 stops at 13:00 IST: 45 bars
            keep_bars(res, lambda ts: not (et(ts).date().isoformat() == "2026-09-04" and et(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 56)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 45}])
        self.assertEqual(doc["history"]["sessions"], 56)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-16")
        self.assertNotIn("2026-10-19", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 1)
        self.assertEqual(doc["mc"]["pool_sessions"], 56)   # 2026-07-27 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: et(ts).date().isoformat() >= "2026-09-21")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: et(ts).date().isoformat() >= "2026-09-18")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_holidays_are_flagged(self):
        doc = machine(plan(date="2026-10-16", entry_time=None), now=NOW_UTC, replays=50)
        self.assertFalse(any("holiday" in w for w in doc["warnings"]))
        tm.NSE_HOLIDAYS["2026-10-16"] = "Test Holiday"
        try:
            doc = machine(plan(date="2026-10-16", entry_time=None), now=NOW_UTC, replays=50)
            self.assertIn("NSE holiday: Test Holiday", doc["warnings"])
        finally:
            del tm.NSE_HOLIDAYS["2026-10-16"]
        self.assertNotIn("2026-09-14", tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"),
                                                                       NOW_UTC)[1]))

    def test_costs_and_rupees(self):
        self.assertEqual(tm.inr(1234567.891), "\u20b912,34,567.89")
        self.assertEqual(tm.inr(81.912), "\u20b981.91")
        self.assertAlmostEqual(tm.costs(1000, 1010, 100), 40 + 25.25 + 6.1707 + 0.201 + 0.18 * (40 + 6.1707 + 0.201)
                               + 3.0, places=2)
        self.assertGreater(tm.costs(1000, 1010, 100, intraday=False), tm.costs(1000, 1010, 100))
        small = machine(replays=200)["mc"]
        big = tm.time_machine(TEXT, plan(), NOW_UTC, replays=200, qty=10)["mc"]
        self.assertEqual(big["costs"]["qty"], 10)
        self.assertLess(big["avg_R_after_costs"], small["avg_R_after_costs"])   # Rs 20 brokerage hurts small size

    def test_the_data_must_be_the_plans_ticker(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="TCS"))
        self.assertIn("data is for RELIANCE", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 10:50 has not happened in this data yet
            machine(plan(entry_time="10:40 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:17 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=365.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "ticker"),
            (dict(symbol="BAD SYM"), "NSE ticker"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 19"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:00 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00am", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(symbol="reliance.ns")["symbol"], "RELIANCE")

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:30", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("15:30", 66, "15:30"))
        self.assertIn("still open at 3:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:30 PM", replays=500)["mc"], m)
        self.assertEqual(machine(until="15:15", replays=500)["mc"], machine(replays=500)["mc"])
        for bad, words in (("10:00 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("15:17", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=368.0, stop=362.0, target=374.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry(self):
        self.assertEqual(tm.cents_away(358.325, True), 358.32)
        self.assertEqual(tm.cents_away(358.325, False), 358.33)
        self.assertEqual(tm.cents_away(354.4700012207031, True), 354.47)
        self.assertEqual(tm.cents_away(354.46999999999997, True), 354.47)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=354.47, target=373.89), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=360.0, target=372.54), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 1.1478, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 57)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=370.04, target=358.32, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:55"}),
                         "No replay reached the target or the stop by 3:55 PM: the price at 3:55 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # a 2R plan (entry 370.58, stop 367.06, target 377.62): it needs 33 %, got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_near_break_even(self):
        # stop 362.00, 2R target 368.54: 33.4 % vs 60.4 % looks lopsided, but 2R only needs a third
        doc = machine(plan(stop=362.0, target=368.54, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R"]), (2.0, 0.3343, 0.6039, 0.094))
        self.assertEqual(doc["verdict"], "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 36 % (target first 33.4 % vs stop "
                                         "first 60.4 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_2r_on_the_fixture_a_clear_lean(self):
        # the reel's entry and stop with a 2R target 375.90: the target first only 2.6 % vs stop first 22.1 %
        doc = machine(plan(target=375.90))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0259, 0.221, 0.7531, 0.048))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 10 % (target first 2.6 % vs stop "
                                         "first 22.1 %).")
        self.assertEqual(doc["verdict_notes"], [
            "75.3 % of the replays hit neither by 3:15 PM; the price at 3:15 PM decides those.",
            "Average R is +0.048 all the same: the replays that hit neither pull it the other way."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0] + "\n" + doc["verdict_notes"][1], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m.ns", "query2"))
        self.assertIn("Mozilla/5.0", tm.UA)

    def test_429_waits_retries_then_falls_back_to_query2(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query1", "query1", "query2"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query1", "query1"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query1", "query2"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the ticker", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query1"])
        finally:
            shutil.rmtree(d)


class V4CacheAndCharges(unittest.TestCase):
    """v4: the trader-data intraday cache and the shared charges model."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.makedirs(os.path.join("trader", "data", "intraday"))
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)
        self._fetch = tm.fetch_chart

    def tearDown(self):
        tm.fetch_chart = self._fetch
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def put_cache(self):
        with open(os.path.join("trader", "data", "intraday", "RELIANCE-5m.json"), "w") as fh:
            fh.write(TEXT)

    def test_live_failure_reads_the_cache(self):
        def boom(sym):
            raise tm.TMError("Yahoo did not answer for RELIANCE on query1 and query2 (HTTP 403)")
        tm.fetch_chart = boom
        code, _, err = run(["run", "--now", NOW, "--replays", "200"])
        self.assertEqual(code, 1)
        for must in ("No cache at trader/data/intraday/RELIANCE-5m.json", "data.py doctor",
                     "get_historical_data", "import-kite"):
            self.assertIn(must, err)
        self.put_cache()
        code, text, err = run(["run", "--now", NOW, "--replays", "200", "--json"])
        self.assertEqual(code, 0, err)
        self.assertIn("using cached data from 2026-10-19", err)
        self.assertIn("trader-data/data.py intraday RELIANCE", err)
        doc = json.loads(text)
        self.assertEqual(doc["source"], "cache")
        self.assertIn("using cached data", doc["data"]["cache_note"])

    def test_cache_flag_never_calls_yahoo(self):
        calls = []
        tm.fetch_chart = lambda sym: calls.append(sym)
        self.put_cache()
        code, _, err = run(["run", "--cache", "--now", NOW, "--replays", "200"])
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [])

    def test_charges_model_and_calibration(self):
        base = tm.load_charges("")
        self.assertEqual(tm.charges_label(base), "estimate")
        self.assertAlmostEqual(tm.costs(1000, 1010, 100, intraday=False, model=base),
                               201.0 + 6.1707 + 0.201 + 0.18 * (6.1707 + 0.201) + 15.0 + 15.34, places=2)
        with open(os.path.join("trader", "charges.json"), "w") as fh:
            json.dump({"calibration": {"factor": 1.2, "samples": 4}}, fh)
        m = tm.load_charges()
        self.assertEqual(tm.charges_label(m), "estimate (calibrated from 4 contract notes)")
        self.assertAlmostEqual(tm.costs(1000, 1010, 100, model=m), 1.2 * tm.costs(1000, 1010, 100, model=base))
        self.put_cache()
        code, text, err = run(["run", "--cache", "--now", NOW, "--replays", "200"])
        self.assertEqual(code, 0, err)
        self.assertIn("After intraday charges, estimate (calibrated from 4 contract notes)", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", RIL, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   26.9 %", text)
        self.assertIn("Leans toward the target: target first 26.9 % vs stop first 22.0 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2693, 0.051))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (32, -0.076))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", RIL, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.2203)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:23], "Leans toward the target")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-10-19", "--entry", "364.18",
                               "--stop", "358.32", "--target", "370.04", "--entry-time", "10:00 AM",
                               "--offline", RIL, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2693, 0.5249))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (13, 15, -0.076))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "364.18", "--offline", RIL])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(RIL, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", RIL, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", RIL, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 10:00 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("   370.04 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("   358.32 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("   364.18 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("10:00", text)
        self.assertIn("3:15", text)
        self.assertEqual(lines[-2:], ["Leans toward the target: target first 26.9 % vs stop first 22.0 %.",
                                      "51.0 % of the replays hit neither by 3:15 PM; the price at 3:15 PM decides those."])
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", RIL, "--now", NOW])
        self.assertEqual(again, text)


def flat_session(moves, n=75, base=100.0):
    """A hand-made session of n bars: every bar opens at the previous close and closes at base, or at
    moves[slot] where given; high and low pad the open and the close by 0.05."""
    out, prev = [], base
    for i in range(n):
        c = moves.get(i, prev)
        out.append({"o": prev, "h": max(prev, c) + 0.05, "l": min(prev, c) - 0.05, "c": c})
        prev = c
    return out


def hlc_session(hlc):
    """Bars from (high, low, close) tuples; a bar opens at the previous close (the first at its close)."""
    out, prev = [], None
    for h, l, c in hlc:
        out.append({"o": c if prev is None else prev, "h": h, "l": l, "c": c})
        prev = c
    return out


SERIES_A = [(10, 8, 9), (11, 9, 10.5), (12, 10, 11.8), (11.5, 10.5, 11), (11.2, 10.2, 10.4), (12.6, 11, 12.4),
            (12.4, 11.6, 11.8), (11.9, 9.8, 10.0)]
SERIES_B = [(12, 10, 11), (11, 9, 9.5), (10, 8, 8.2), (9.5, 8.5, 9), (9.8, 8.8, 9.6), (9, 7.4, 7.6),
            (8.4, 7.6, 8.2), (10.2, 8.1, 10.0)]


def bar_dicts(hlc):
    return [{"h": h, "l": l, "c": c} for h, l, c in hlc]


class LevelRule(unittest.TestCase):
    def test_level_plan_is_carried_and_checked(self):
        p = plan(setup="level", level=364.0)
        self.assertEqual((p["setup"], p["level"]), ("level", 364.0))
        self.assertNotIn("level", plan())
        for bad in (0, -3, "abc"):
            with self.assertRaises(tm.TMError):
                plan(setup="level", level=bad)
        with self.assertRaises(tm.TMError):
            plan(product="CFD")

    def test_resolve_rule_turns_the_level_into_a_distance_from_the_open(self):
        p = plan(setup="level", level=367.64, stop_rule="price")
        rule = tm.resolve_rule(p, {0: {"o": 364.0}})
        self.assertEqual(rule, {"setup": "level", "level_pct": 1.0, "stop_rule": "pct", "target_r": 1.0,
                                "stop_pct": 1.6091})
        self.assertEqual(tm.resolve_rule(plan(setup="level", level=367.64, stop_rule="swing"),
                                         {0: {"o": 364.0}})["stop_rule"], "swing")
        self.assertIsNone(tm.resolve_rule(plan(setup="level"), {0: {"o": 364.0}}))      # no level in the plan
        self.assertIsNone(tm.resolve_rule(p, {}))                                       # no first bar
        self.assertIsNone(tm.resolve_rule(plan(setup="flag"), {0: {"o": 364.0}}))

    def test_history_replay_on_hand_made_sessions(self):
        rule = {"setup": "level", "level_pct": 1.0, "stop_rule": "pct", "stop_pct": 1.0, "target_r": 2.0}
        a = flat_session({10: 101.5, 11: 103.6})          # closes above 101 on slot 10, runs to the target
        b = flat_session({10: 101.5, 11: 100.0})          # same entry, then falls through the stop
        c = flat_session({20: 100.9})                     # never closes above 101: no entry
        d = flat_session({10: 101.5, 11: 101.6})          # enters and then goes nowhere
        e = flat_session({1: 105.0, 2: 100.0})            # closes above on slot 1, before 09:30: not an entry
        sessions = [("2026-01-%02d" % (i + 5), s) for i, s in enumerate([a, b, c, d, e])]
        h = tm.history_replay(sessions, rule, 71)
        self.assertEqual((h["sessions"], h["triggered"]), (5, 3))
        self.assertEqual((h["target"], h["stop"], h["close"]), (1, 1, 1))
        ta, tb, td = h["trades"]
        self.assertEqual((ta["date"], ta["entry"], ta["stop"], ta["target"], ta["outcome"], ta["R"]),
                         ("2026-01-05", 101.5, 100.48, 103.54, "target", 2.0))
        self.assertEqual((tb["outcome"], tb["R"]), ("stop", -1.0))
        self.assertEqual((td["outcome"], td["R"]), ("close", 0.1))
        self.assertEqual(ta["time"], tm.clock12(tm.OPEN_MIN + 5 * 11))
        self.assertNotIn("skipped_no_stop", h)

    def test_entries_stop_at_the_cutoff_and_at_until(self):
        rule = {"setup": "level", "level_pct": 1.0, "stop_rule": "pct", "stop_pct": 1.0, "target_r": 2.0}
        late = flat_session({69: 101.5})                   # closes above at 15:05, after the 15:00 cutoff
        edge = flat_session({68: 101.5})                   # the bar closing at 15:00 still counts
        h = tm.history_replay([("2026-01-05", late), ("2026-01-06", edge)], rule, 71)
        self.assertEqual([t["date"] for t in h["trades"]], ["2026-01-06"])
        h = tm.history_replay([("2026-01-06", edge)], rule, 40)             # --until before the entry bar
        self.assertEqual(h["triggered"], 0)

    def test_a_sell_level_mirrors(self):
        rule = {"setup": "level", "level_pct": -1.0, "stop_rule": "pct", "stop_pct": 1.0, "target_r": 2.0}
        s = flat_session({10: 98.5, 11: 96.4})
        h = tm.history_replay([("2026-01-05", s)], rule, 71, "sell")
        t = h["trades"][0]
        self.assertTrue(t["target"] < t["entry"] < t["stop"])
        self.assertEqual((t["entry"], t["outcome"]), (98.5, "target"))

    def test_level_replay_on_the_fixture_follows_the_rule(self):
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1])
        open0 = days["2026-10-19"][0]["o"]
        lvl = round(open0 * 1.004, 2)
        doc = machine(plan(setup="level", level=lvl, stop_rule="pct"), replays=100)
        h = doc["history"]
        self.assertEqual(h["rule"]["setup"], "level")
        self.assertEqual(h["rule"]["stop_rule"], "pct")
        self.assertGreater(h["triggered"], 5)
        self.assertEqual(h["sessions"], 57)
        by_date = dict(tm.full_sessions(days, "2026-10-19")[0])
        pct = h["rule"]["level_pct"]
        for t in h["trades"]:
            D = by_date[t["date"]]
            line = D[0]["o"] * (1 + pct / 100.0)
            first = next(i for i in range(2, 69) if D[i]["c"] > line)
            self.assertEqual(t["entry"], round(D[first]["c"], 2), t["date"])
            self.assertTrue(t["stop"] < t["entry"] < t["target"], t)
        text = "\n".join(tm.report(doc))
        self.assertIn("first close above", text)
        self.assertIn("the day's open", text)
        self.assertNotIn("opening range", text.split("History:")[1].splitlines()[0])

    def test_level_plan_without_its_level_says_why(self):
        doc = machine(plan(setup="level"), replays=100)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("needs its level price", doc["history"]["note"])

    def test_cli_flags_run_a_level_history(self):
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1])
        lvl = round(days["2026-10-19"][0]["o"] * 1.004, 2)
        code, out, err = run(["run", "--symbol", "RELIANCE", "--date", "2026-10-19", "--entry", "364.18", "--stop", "358.32",
                              "--target", "370.04", "--entry-time", "10:00 AM", "--setup", "level", "--level", str(lvl),
                              "--stop-rule", "pct", "--offline", RIL, "--now", NOW, "--replays", "200", "--json"])
        self.assertEqual(code, 0, err)
        h = json.loads(out)["history"]
        self.assertEqual(h["rule"]["setup"], "level")
        self.assertGreater(h["triggered"], 0)


class SwingRule(unittest.TestCase):
    def test_the_engine_block_is_the_shared_one(self):
        # the pattern: the same Series A and B as every other copy of the engine
        snap, ev = tm.swing_run([(h, l, c) for h, l, c in SERIES_A], 5.0, 20, 0)
        self.assertEqual((snap["trend"], snap["coc"], snap["legs"]), ("down", 12.6, 0))
        self.assertEqual(ev, [None, "start_up", None, None, None, "bos", None, "reversal"])
        snap, ev = tm.swing_run(SERIES_B, 5.0, 20, 0)
        self.assertEqual((snap["trend"], snap["coc"]), ("up", 7.4))
        self.assertEqual(ev[-1], "reversal")

    def test_rule_stop_swing_for_a_buy(self):
        rule = {"stop_rule": "swing"}
        self.assertEqual(tm.rule_stop(rule, None, None, 12.5, True, bar_dicts(SERIES_A[:6])), 10.2)
        odd = list(SERIES_A[:6])
        odd[4] = (11.2, 10.249, 10.4)                                       # a pullback low of 10.249 rounds down
        self.assertEqual(tm.rule_stop(rule, None, None, 12.5, True, bar_dicts(odd)), 10.24)
        self.assertIsNone(tm.rule_stop(rule, None, None, 10.0, True, bar_dicts(SERIES_A)))          # the trend is down
        self.assertIsNone(tm.rule_stop(rule, None, None, 10.1, True, bar_dicts(SERIES_A[:6])))      # level above the entry
        self.assertIsNone(tm.rule_stop(rule, None, None, 10.205, True, bar_dicts(SERIES_A[:6])))    # under 0.05 % away
        self.assertIsNone(tm.rule_stop(rule, None, None, 12.5, True, None))
        self.assertIsNone(tm.rule_stop(rule, None, None, 12.5, True, bar_dicts([(10, 8, 9)] * 5)))  # no trend yet

    def test_rule_stop_swing_for_a_sell(self):
        rule = {"stop_rule": "swing"}
        self.assertEqual(tm.rule_stop(rule, None, None, 7.6, False, bar_dicts(SERIES_B[:6])), 9.8)
        self.assertIsNone(tm.rule_stop(rule, None, None, 7.6, True, bar_dicts(SERIES_B[:6])))

    def test_history_recomputes_the_stop_per_session_and_skips_refusals(self):
        rule = {"setup": "level", "level_pct": round(100 * (12.0 / 9 - 1), 4), "stop_rule": "swing", "target_r": 2.0}
        x = hlc_session(SERIES_A)                          # closes above 12.0 on slot 5; coc 10.2; then 9.8 < 10.2
        y = hlc_session([(10, 8, 9), (13, 8, 8.5), (13, 7, 7.5), (12.5, 7.4, 12.2), (12.3, 11, 12.0), (12.2, 11, 12.1),
                         (12.2, 11, 12.1), (12.2, 11, 12.1)])   # closes above 12 while the trend is down
        h = tm.history_replay([("2026-01-05", x), ("2026-01-06", y)], rule, 7)
        self.assertEqual((h["sessions"], h["triggered"], h["skipped_no_stop"]), (2, 1, 1))
        t = h["trades"][0]
        self.assertEqual((t["entry"], t["stop"], t["target"], t["outcome"], t["R"]), (12.4, 10.2, 16.8, "stop", -1.0))

    def test_resolve_rule_keeps_the_swing_stop_for_orb_and_level(self):
        orb = tm.resolve_rule(plan(stop_rule="swing"), {})
        self.assertEqual((orb["setup"], orb["stop_rule"]), ("orb", "swing"))
        self.assertNotIn("stop_pct", orb)
        self.assertEqual(tm.resolve_rule(plan(stop_rule="atr"), {})["stop_rule"], "pct")     # unchanged

    def test_swing_report_line(self):
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1])
        lvl = round(days["2026-10-19"][0]["o"] * 1.004, 2)
        doc = machine(plan(setup="level", level=lvl, stop_rule="swing"), replays=100)
        h = doc["history"]
        self.assertEqual(h["rule"]["stop_rule"], "swing")
        self.assertIn("skipped_no_stop", h)
        for t in h["trades"]:
            self.assertTrue(t["stop"] < t["entry"] < t["target"], t)
        self.assertIn("swing change-of-character level", "\n".join(tm.report(doc)))


class DeliveryPlan(unittest.TestCase):
    def test_a_cnc_plan_is_priced_as_delivery_and_skips_the_history(self):
        mis = machine(plan(), replays=200)
        cnc = machine(plan(product="CNC", flat_by=None), replays=200)
        self.assertEqual(cnc["plan"]["product"], "CNC")
        self.assertNotIn("product", mis["plan"])
        self.assertNotIn("flat_by", cnc["plan"])
        self.assertEqual((cnc["mc"]["costs"]["segment"], mis["mc"]["costs"]["segment"]), ("delivery", "intraday"))
        self.assertIn("delivery (CNC)", cnc["mc"]["costs"]["label"])
        self.assertGreater(cnc["mc"]["costs"]["rupees_at_stop"], mis["mc"]["costs"]["rupees_at_stop"])
        self.assertIsNone(cnc["history"]["rule"])
        self.assertIn("delivery (CNC)", cnc["history"]["note"])
        self.assertTrue(any("held overnight" in w for w in cnc["warnings"]))
        self.assertIn("After delivery charges", "\n".join(tm.report(cnc)))
        self.assertIn("After intraday charges", "\n".join(tm.report(mis)))
        self.assertEqual(mis["warnings"], [])


if __name__ == "__main__":
    unittest.main()
