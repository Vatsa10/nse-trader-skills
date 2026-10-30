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
from _load import load, FIX

th = load("trader-thesis", "thesis")

SAMPLE = os.path.join(FIX, "trader-thesis", "sample-thesis.json")
TODAY = datetime.date(2026, 10, 30)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = th.main(argv)
    return code, out.getvalue(), err.getvalue()


class Brief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.trader = os.path.join(self.tmp, "trader")
        self.data = os.path.join(self.trader, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tree(self):
        d = self.data
        rows = ["date,open,high,low,close,volume"]
        start = datetime.date(2025, 10, 1)
        n = 300
        for i in range(n):
            c = 100 + i * 0.5 if i < 250 else 225 - (i - 250) * 2
            rows.append("%s,%s,%s,%s,%s,1000" % ((start + datetime.timedelta(days=i)).isoformat(), c, c, c, c))
        write(os.path.join(d, "daily", "IOC.csv"), "\n".join(rows) + "\n")
        write(os.path.join(d, "fundamentals", "IOC.json"), json.dumps(
            {"as_of": "2026-10-29", "price": 130.41, "pe": 5.44, "sector": "Oil",
             "annual": [{"fy": "FY26", "revenue_cr": 1, "net_profit_cr": 2}],
             "quarterly": [{"q": "Q1FY27", "revenue_cr": 3, "net_profit_cr": -4}]}))
        write(os.path.join(self.trader, "invest", "IOC.json"), json.dumps(
            {"as_of": "2026-10-29", "score": 68, "verdict": "Reasonable, size small",
             "pillars": {"valuation": {"score": 100}}, "flags": ["PSU"]}))
        write(os.path.join(self.trader, "longterm.json"), json.dumps(
            {"as_of": "2026-10-29", "hits": [{"symbol": "IOC", "setups": ["near_52w_low"], "swing_trend": "down",
                                              "swing_coc": 140.5, "mansfield_rs": -3.2, "dq": 1.4},
                                             {"symbol": "TCS", "setups": ["x"]}]}))
        write(os.path.join(d, "delivery", "IOC.csv"),
              "date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series\n"
              "2026-10-28,100,10,50,50.0,5,EQ\n2026-10-29,100,10,60,60.0,6,EQ\n")
        write(os.path.join(d, "actions.json"), json.dumps(
            {"fetched_at": "2026-10-29", "symbols": {"IOC": {"from": "2025-01-01", "fetched_through": "2026-10-29",
                "actions": [{"ex_date": "2026-08-01", "kind": "dividend", "factor": None, "dividend": 5.0},
                            {"ex_date": "2026-11-20", "kind": "dividend", "factor": None, "dividend": 3.0}]}}}))
        write(os.path.join(d, "index", "nifty-50.csv"),
              "date,open,high,low,close,volume,pe,pb,div_yield\n2026-10-29,1,1,1,25000,0,22.5,3.4,1.3\n")
        write(os.path.join(d, "breadth.csv"), "date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc\n"
                                              "2026-10-29,55.5,60.1,12,100.2,3.3\n")

    def test_present_sources_are_read_with_dates(self):
        self.tree()
        b = th.build_brief("IOC", "longterm", self.data, TODAY)
        s = b["sources"]
        self.assertEqual(s["daily"]["date"], "2026-07-27")
        v = s["daily"]["values"]
        self.assertEqual(v["last_close"], 127.0)
        self.assertEqual(v["high_52w"], 225.0)
        self.assertIsNotNone(v["dma_200"])
        self.assertIsNotNone(v["return_1y_pct"])
        self.assertLess(v["max_drawdown_1y_pct"], 0)
        self.assertTrue(s["daily"]["file"].endswith("daily/IOC.csv"))
        self.assertEqual(s["fundamentals"]["date"], "2026-10-29")
        self.assertEqual(s["fundamentals"]["values"]["latest_quarter"]["net_profit_cr"], -4)
        self.assertEqual(s["invest_analysis"]["values"]["pillars"]["valuation"], 100)
        hits = s["longterm"]["values"]["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["swing_coc"], 140.5)
        self.assertEqual(s["delivery"]["values"]["latest_deliv_pct"], 60.0)
        self.assertEqual(s["delivery"]["values"]["mean_deliv_pct_30"], 55.0)
        self.assertEqual(s["actions"]["values"]["next_ex_date"], "2026-11-20")
        self.assertEqual(s["nifty_50"]["values"]["pe"], 22.5)
        self.assertEqual(s["breadth"]["values"]["pct_200"], 60.1)

    def test_missing_sources_are_listed(self):
        self.tree()
        b = th.build_brief("IOC", "longterm", self.data, TODAY)
        names = [m["source"] for m in b["missing"]]
        for n in ("watchlist", "india_vix", "flows", "chain_nifty"):
            self.assertIn(n, names)
        self.assertNotIn("plan", names)          # longterm does not read the plan
        self.assertTrue(all(m["why"] for m in b["missing"]))
        b2 = th.build_brief("IOC", "swing", self.data, TODAY)
        self.assertIn("plan", [m["source"] for m in b2["missing"]])

    def test_empty_tree_does_not_crash(self):
        b = th.build_brief("IOC", "longterm", self.data, TODAY)
        self.assertEqual(b["sources"], {})
        self.assertEqual(len(b["missing"]), 12)
        self.assertTrue(b["notes"])

    def test_brief_command_writes_file(self):
        self.tree()
        out = os.path.join(self.tmp, "o", "IOC-brief.json")
        code, text, _ = run(["brief", "ioc", "--data", self.data, "--out", out, "--today", "2026-10-30"])
        self.assertEqual(code, 0)
        self.assertIn("MISSING", text)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["symbol"], "IOC")

    def test_bad_symbol(self):
        code, _, err = run(["brief", "../x", "--data", self.data])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE symbol", err)


class Frames(unittest.TestCase):
    def test_frame_table(self):
        code, text, _ = run(["frames"])
        self.assertEqual(code, 0)
        for fid in ("bear", "risk", "macro", "value_trap", "forensic", "stopout", "tax_costs", "opportunity"):
            self.assertIn(fid, text)
        self.assertGreaterEqual(len(th.frame_rows()), 10)
        self.assertTrue(all("wild" in f["tags"] or f["tags"] for f in th.frame_rows()))

    def test_mode_filter_keeps_wild(self):
        ids = [f["id"] for f in th.frame_rows("intraday")]
        self.assertIn("stopout", ids)
        self.assertNotIn("forensic", ids)
        code, text, _ = run(["frames", "--json", "--mode", "longterm"])
        self.assertTrue(json.loads(text))


class Render(unittest.TestCase):
    def setUp(self):
        with open(SAMPLE, encoding="utf-8") as fh:
            self.doc = json.load(fh)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_all_sections(self):
        code, text, _ = run(["render", SAMPLE])
        self.assertEqual(code, 0)
        for h in ("## Brief", "## Bull case", "## Bear case", "## Traps", "## Verdict", "## What to watch",
                  "## Hand-off"):
            self.assertIn(h, text)
        self.assertIn("**WAIT** (conviction: medium)", text)
        self.assertIn("Q2FY27 results show a return to profit", text)
        self.assertIn("Not financial advice", text)
        self.assertNotIn(chr(0x2014), text)

    def test_render_refuses_missing_section(self):
        for key in th.REQUIRED:
            doc = dict(self.doc)
            del doc[key]
            p = os.path.join(self.tmp, "t.json")
            write(p, json.dumps(doc))
            code, out, err = run(["render", p])
            self.assertEqual(code, 1, key)
            self.assertIn("missing section: %s" % key, err)
            self.assertEqual(out, "")

    def test_render_refuses_bad_verdict(self):
        doc = dict(self.doc)
        doc["verdict"] = {"call": "take", "conviction": "high", "changes_if": "x"}   # take is not a longterm call
        p = os.path.join(self.tmp, "t.json")
        write(p, json.dumps(doc))
        code, _, err = run(["render", p])
        self.assertEqual(code, 1)
        self.assertIn("verdict.call", err)
        doc["mode"] = "swing"
        write(p, json.dumps(doc))
        self.assertEqual(run(["render", p])[0], 0)

    def test_render_unreadable_file(self):
        code, _, err = run(["render", os.path.join(self.tmp, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()
