import datetime
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

inv = load("trader-invest", "invest")

D = os.path.join(FIX, "trader-invest")
TODAY = "2026-10-29"

# synthetic, split/bonus adjusted daily closes: log-linear between public-ish anchor points plus a
# deterministic wiggle. Shapes the technicals, not real bars (record real ones with trader-data).
ANCHORS = {
    "IOC": [("2021-10-29", 85.0), ("2022-06-20", 70.0), ("2023-03-28", 76.0), ("2024-02-15", 185.0),
            ("2024-07-31", 196.0), ("2025-03-03", 120.0), ("2025-10-29", 150.0), ("2026-02-16", 188.0),
            ("2026-09-25", 125.5), ("2026-10-29", 130.41)],
    "TCS": [("2021-10-29", 3350.0), ("2022-06-15", 3250.0), ("2023-03-28", 3150.0), ("2024-09-02", 4500.0),
            ("2024-12-16", 4550.0), ("2025-06-30", 3450.0), ("2025-12-31", 3250.0), ("2026-09-01", 2060.0),
            ("2026-10-08", 2076.0), ("2026-10-29", 2190.0)],
}


def synth_daily(symbol):
    pts = [(datetime.date.fromisoformat(d), p) for d, p in ANCHORS[symbol]]
    rows, d, i = [], pts[0][0], 0
    while d <= pts[-1][0]:
        if d.weekday() < 5:
            while pts[i + 1][0] < d:
                i += 1
            (d0, p0), (d1, p1) = pts[i], pts[i + 1]
            t = (d - d0).days / float((d1 - d0).days)
            base = math.exp(math.log(p0) + t * (math.log(p1) - math.log(p0)))
            k = len(rows)
            wig = 0.012 * math.sin(k * 0.9) + 0.008 * math.sin(k * 0.23)
            if d in (pts[-1][0],):
                wig = 0.0
            c = round(base * (1 + wig), 2)
            rows.append((d.isoformat(), c))
        d += datetime.timedelta(days=1)
    return rows


def write_daily(trader_dir, symbol, adjusted=True):
    path = os.path.join(trader_dir, "data", "daily", symbol + (".adj.csv" if adjusted else ".csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("date,open,high,low,close,volume\n")
        for d, c in synth_daily(symbol):
            fh.write("%s,%.2f,%.2f,%.2f,%.2f,1000000\n" % (d, c, c * 1.01, c * 0.99, c))
    return path


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = inv.main(argv)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.td = os.path.join(self.tmp, "trader")
        os.makedirs(self.td)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fetch(self, sym, *extra):
        argv = ["--trader-dir", self.td, "--today", TODAY, "fetch", sym, "--no-net",
                "--screener-html", os.path.join(D, "screener-%s.html" % sym)] + list(extra)
        code, out, err = run(argv)
        self.assertEqual(code, 0, out + err)
        with open(os.path.join(self.td, "data", "fundamentals", sym + ".json")) as fh:
            return json.load(fh)

    def analyse(self, sym, *extra):
        code, out, err = run(["--trader-dir", self.td, "--today", TODAY, "--json", "analyse", sym] + list(extra))
        self.assertEqual(code, 0, out + err)
        return json.loads(out)


class Parse(unittest.TestCase):
    def test_screener_ioc_top_ratios_and_tables(self):
        with open(os.path.join(D, "screener-IOC.html"), encoding="utf-8") as fh:
            got = inv.parse_screener(fh.read())
        self.assertEqual(got["market_cap_cr"], 184154)
        self.assertEqual(got["price"], 130)
        self.assertEqual(got["pe"], 5.44)
        self.assertEqual(got["div_yield_pct"], 6.36)
        self.assertEqual(got["roe_pct"], 19.4)
        self.assertEqual(got["roce_pct"], 13.2)
        self.assertEqual((got["high_52w"], got["low_52w"]), (189, 125))
        self.assertEqual(got["basis"], "standalone")
        self.assertEqual([a["fy"] for a in got["annual"]], ["FY21", "FY22", "FY23", "FY24", "FY25", "FY26"])
        fy26 = got["annual"][-1]
        self.assertEqual(fy26["net_profit_cr"], 36802)
        self.assertEqual(fy26["revenue_cr"], 886224)
        self.assertAlmostEqual(fy26["dividend_ps"], 8.29, places=2)
        self.assertEqual(got["ttm"]["net_profit_cr"], 28452)
        self.assertEqual(got["quarterly"][-1], {"q": "Q1FY27", "revenue_cr": 275972, "net_profit_cr": -2661})
        self.assertEqual(got["quarterly"][0]["q"], "Q1FY26")
        self.assertEqual(got["promoter_pct"], 51.5)
        self.assertEqual(got["debt_to_equity"], 0.58)
        self.assertEqual(got["sector"], "Oil, Gas & Consumable Fuels")
        self.assertEqual(got["promoter_name"], "President of India")
        self.assertNotIn("promoter_pledge_pct", got)

    def test_screener_tcs_consolidated_and_pledge(self):
        with open(os.path.join(D, "screener-TCS.html"), encoding="utf-8") as fh:
            got = inv.parse_screener(fh.read())
        self.assertEqual(got["basis"], "consolidated")
        self.assertEqual(got["price"], 2190)
        self.assertEqual(got["promoter_pledge_pct"], 0.0)
        self.assertEqual(got["quarterly"][-1]["q"], "Q2FY27")
        self.assertEqual(got["debt_to_equity"], 0.09)

    def test_screener_garbage_raises(self):
        with self.assertRaises(inv.InvestError):
            inv.parse_screener("<html><body>Please verify you are a human</body></html>")

    def test_nse_quote(self):
        with open(os.path.join(D, "nse-quote-IOC.json")) as fh:
            got = inv.parse_nse_quote(json.load(fh))
        self.assertEqual(got["pe"], 5.44)
        self.assertEqual(got["price"], 130.41)
        self.assertEqual(got["sector"], "Oil Gas & Consumable Fuels")
        self.assertEqual(got["industry"], "Refineries & Marketing")
        self.assertAlmostEqual(got["market_cap_cr"], 184154, delta=5)
        with self.assertRaises(inv.InvestError):
            inv.parse_nse_quote({"error": "blocked"})

    def test_kite_quotes(self):
        with open(os.path.join(D, "kite-quotes-IOC.json")) as fh:
            data = json.load(fh)
        self.assertEqual(inv.parse_kite_quotes(data, "IOC")["price"], 126.0)
        self.assertEqual(inv.parse_kite_quotes({"data": data}, "IOC")["price"], 126.0)
        with self.assertRaises(inv.InvestError):
            inv.parse_kite_quotes(data, "TCS")

    def test_quarter_labels_and_results_deadline(self):
        self.assertEqual(inv.quarter_label("Jun 2026"), "Q1FY27")
        self.assertEqual(inv.quarter_label("Mar 2026"), "Q4FY26")
        self.assertEqual(inv.quarter_label("Dec 2025"), "Q3FY26")
        self.assertEqual(inv.next_quarter("Q4FY26"), "Q1FY27")
        self.assertEqual(inv.results_due("Q2FY27"), datetime.date(2026, 11, 14))
        self.assertEqual(inv.results_due("Q4FY26"), datetime.date(2026, 5, 30))

    def test_inr_and_cagr(self):
        self.assertEqual(inv.inr(12345678.9), "₹1,23,45,678.90")
        self.assertEqual(inv.inr(5000), "₹5,000")
        self.assertIsNone(inv.cagr(-5, 10, 3))
        self.assertAlmostEqual(inv.cagr(100, 200, 5), 14.87, places=2)


class Fetch(Base):
    def test_fetch_merges_screener_and_nse_into_the_contract_shape(self):
        f = self.fetch("IOC", "--nse-json", os.path.join(D, "nse-quote-IOC.json"))
        for k in ("symbol", "as_of", "source", "price", "market_cap_cr", "pe", "pb", "div_yield_pct", "roe_pct",
                  "roce_pct", "debt_to_equity", "promoter_pct", "promoter_pledge_pct", "sector", "annual",
                  "quarterly"):
            self.assertIn(k, f)
        self.assertEqual(f["as_of"], TODAY)
        self.assertIsNone(f["promoter_pledge_pct"])
        self.assertIn("screener.in", f["source"])
        self.assertIn("nse", f["source"])
        self.assertEqual(f["nse_sector"], "Oil Gas & Consumable Fuels")
        self.assertEqual(set(f["annual"][0]), {"fy", "revenue_cr", "net_profit_cr", "eps", "dividend_ps",
                                               "payout_pct"})

    def test_kite_price_rescales_price_ratios(self):
        f = self.fetch("IOC", "--kite-quotes", os.path.join(D, "kite-quotes-IOC.json"))
        self.assertEqual(f["price"], 126.0)
        self.assertEqual(f["price_source"], "kite get_quotes")
        self.assertAlmostEqual(f["pe"], round(5.44 * 126 / 130, 2), places=2)
        self.assertGreater(f["div_yield_pct"], 6.36)

    def test_manual_json_when_screener_is_blocked(self):
        code, out, err = run(["--trader-dir", self.td, "--today", TODAY, "fetch", "IOC", "--no-net",
                              "--screener-html", os.path.join(D, "nope.html"),
                              "--from", os.path.join(D, "manual-IOC.json")])
        self.assertEqual(code, 0, out + err)
        self.assertIn("FAIL", out)
        self.assertIn("manual json", out)
        with open(os.path.join(self.td, "data", "fundamentals", "IOC.json")) as fh:
            f = json.load(fh)
        self.assertEqual(f["promoter_pledge_pct"], 0)
        self.assertNotIn("_provenance", f)

    def test_no_source_explains_the_fallback(self):
        code, out, _ = run(["--trader-dir", self.td, "fetch", "IOC", "--no-net"])
        self.assertEqual(code, 2)
        self.assertIn("--from FILE", out)


class Analyse(Base):
    def setUp(self):
        super().setUp()
        self.fetch("IOC", "--nse-json", os.path.join(D, "nse-quote-IOC.json"))
        self.fetch("TCS")
        write_daily(self.td, "IOC")
        write_daily(self.td, "TCS")

    def test_ioc_is_cyclical_psu_and_not_strong(self):
        a = self.analyse("IOC")
        self.assertTrue(a["cyclical"])
        self.assertTrue(a["psu"])
        self.assertNotEqual(a["verdict"], "Strong long-term candidate")
        self.assertIn(a["verdict"], ("Reasonable, size small", "Value trap risk / wait"))
        self.assertTrue(any("mid-cycle" in x for x in a["flags"]))
        self.assertTrue(any("policy risk" in x.lower() for x in a["flags"]))
        self.assertTrue(any("Q1FY27" in c and "loss" in c for c in a["caps"]))
        self.assertIsNotNone(a["metrics"]["midcycle_pe"])
        self.assertGreater(a["metrics"]["midcycle_pe"], a["metrics"]["pe"])
        self.assertIn("promoter_pledge_pct", a["missing_fields"])
        self.assertIn("quality.promoter_pledge_pct", a["missing_metrics"])
        self.assertGreater(a["pillars"]["valuation"]["score"], 70)
        self.assertLess(a["pillars"]["stability"]["score"], 40)
        self.assertTrue(any("next results due by 2026-11-14" in x for x in a["data_age"]))

    def test_tcs_is_a_quality_compounder(self):
        a = self.analyse("TCS")
        self.assertFalse(a["cyclical"])
        self.assertFalse(a["psu"])
        self.assertEqual(a["verdict"], "Strong long-term candidate")
        self.assertEqual(a["growth"]["loss_years"], 0)
        self.assertGreater(a["pillars"]["quality"]["score"], 85)

    def test_verdicts_differ_sensibly(self):
        ioc, tcs = self.analyse("IOC"), self.analyse("TCS")
        self.assertGreater(tcs["score"], ioc["score"])
        self.assertGreater(ioc["pillars"]["valuation"]["score"], tcs["pillars"]["valuation"]["score"])
        self.assertGreater(tcs["pillars"]["quality"]["score"], ioc["pillars"]["quality"]["score"])

    def test_technicals_from_daily(self):
        t = self.analyse("IOC")["technicals"]
        self.assertEqual(t["last_close"], 130.41)
        self.assertEqual(t["last_date"], TODAY)
        self.assertLess(t["dist_200dma_pct"], 0)
        self.assertGreater(t["off_52w_high_pct"], 20)
        self.assertIsNotNone(t["price_cagr_5y"])
        self.assertGreater(t["max_drawdown_pct"], 30)

    def test_missing_daily_is_scored_low_not_good(self):
        os.remove(os.path.join(self.td, "data", "daily", "TCS.adj.csv"))
        a = self.analyse("TCS")
        self.assertEqual(a["pillars"]["technicals"]["score"], 25)
        self.assertTrue(any("no daily bars" in x for x in a["data_age"]))

    def test_unadjusted_daily_warns(self):
        os.remove(os.path.join(self.td, "data", "daily", "IOC.adj.csv"))
        write_daily(self.td, "IOC", adjusted=False)
        a = self.analyse("IOC")
        self.assertTrue(any("UNADJUSTED" in x for x in a["data_age"]))

    def test_thin_data_caps_the_verdict(self):
        f = {"symbol": "THIN", "as_of": TODAY, "price": 100, "pe": 8, "annual": [], "quarterly": []}
        card = inv.load_scorecard(self.td)
        a = inv.analyse(f, card, today=datetime.date(2026, 10, 29))
        self.assertLess(a["coverage_pct"], 60)
        self.assertIn(a["verdict"], ("Value trap risk / wait", "Avoid for long term"))
        self.assertIn("roe_pct", a["missing_fields"])

    def test_pledge_caps_the_verdict(self):
        path = os.path.join(self.td, "data", "fundamentals", "TCS.json")
        with open(path) as fh:
            f = json.load(fh)
        f["promoter_pledge_pct"] = 35
        a = inv.analyse(f, inv.load_scorecard(self.td), today=datetime.date(2026, 10, 29))
        self.assertEqual(a["verdict"], "Value trap risk / wait")

    def test_user_scorecard_override_and_gsec(self):
        with open(os.path.join(self.td, "scorecard.json"), "w") as fh:
            json.dump({"bands": [{"min": 0, "verdict": "Avoid for long term"}]}, fh)
        a = self.analyse("TCS", "--gsec", "7.5")
        self.assertEqual(a["verdict"], "Avoid for long term")
        self.assertEqual(a["gsec_10y_pct"], 7.5)
        self.assertIn("scorecard.json", a["scorecard"])

    def test_text_output_shows_reasons_age_and_missing(self):
        code, out, _ = run(["--trader-dir", self.td, "--today", TODAY, "analyse", "IOC"])
        self.assertEqual(code, 0)
        for s in ("Strengths:", "Concerns:", "Data age:", "Missing (never treated as good)", "not financial advice",
                  "cyclical: judge on mid-cycle earnings", "PSU"):
            self.assertIn(s.lower(), out.lower(), s)
        self.assertNotIn("\u2014", out)

    def test_compare(self):
        code, out, _ = run(["--trader-dir", self.td, "--today", TODAY, "compare", "IOC", "TCS"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("symbol"))
        self.assertTrue(lines[1].startswith("TCS"))
        code, out, _ = run(["--trader-dir", self.td, "--today", TODAY, "--json", "compare", "IOC", "TCS"])
        data = json.loads(out)
        self.assertEqual([r["symbol"] for r in data["ranked"]], ["TCS", "IOC"])

    def test_missing_fundamentals_is_an_error(self):
        code, _, err = run(["--trader-dir", self.td, "analyse", "BPCL"])
        self.assertEqual(code, 2)
        self.assertIn("fetch BPCL", err)


class Plan(Base):
    def setUp(self):
        super().setUp()
        self.fetch("IOC", "--nse-json", os.path.join(D, "nse-quote-IOC.json"))
        write_daily(self.td, "IOC")

    def plan(self, *extra):
        code, out, err = run(["--trader-dir", self.td, "--today", TODAY, "--json", "plan", "IOC"] + list(extra))
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def test_three_tranches_whole_shares(self):
        p = self.plan("--amount", "5000", "--tranches", "3")
        self.assertEqual(p["price"], 130.41)   # NSE price beats screener's rounded 130; daily bar not newer
        self.assertEqual(p["total_qty"], 38)
        self.assertEqual([t["qty"] for t in p["tranches"]], [13, 13, 12])
        self.assertTrue(all(t["order"]["product"] == "CNC" for t in p["tranches"]))
        self.assertLessEqual(p["invested_incl_charges"], 5000)
        self.assertEqual(p["charges_label"], "estimate")
        self.assertIn("2026-11-14", p["tranches"][1]["condition"])

    def test_delivery_charges_model(self):
        ch = inv.load_charges(self.td)
        buy = inv.delivery_charges("buy", 10000, ch)
        self.assertEqual(buy["brokerage"], 0)
        self.assertEqual(buy["stt"], 10.0)
        self.assertEqual(buy["stamp"], 1.5)
        self.assertEqual(buy["dp"], 0)
        self.assertAlmostEqual(buy["total"], 10 + 1.5 + 0.307 + 0.01 + 0.18 * 0.317, places=2)
        sell = inv.delivery_charges("sell", 10000, ch)
        self.assertEqual(sell["dp"], 15.34)
        self.assertEqual(sell["stamp"], 0)

    def test_calibration_and_override(self):
        with open(os.path.join(self.td, "charges.json"), "w") as fh:
            json.dump({"calibration": {"factor": 1.1, "samples": 4}}, fh)
        p = self.plan("--amount", "5000")
        self.assertEqual(p["charges_label"], "estimate (calibrated from 4 contract notes)")
        ch = inv.load_charges(self.td)
        self.assertEqual(ch["delivery"]["dp_per_scrip_sell"], 15.34)
        self.assertAlmostEqual(inv.delivery_charges("buy", 10000, ch)["total"],
                               1.1 * inv.delivery_charges("buy", 10000, inv.CHARGES)["total"], places=1)

    def test_tax_and_checklist(self):
        p = self.plan("--amount", "5000")
        self.assertEqual(p["tax"]["stcg_pct"], 20)
        self.assertEqual(p["tax"]["ltcg_pct"], 12.5)
        self.assertEqual(p["tax"]["ltcg_exempt_per_year"], 125000)
        self.assertEqual(p["tax"]["long_term_from"], "2027-10-30")
        self.assertEqual(p["tax"]["example_ltcg_tax"], 0)
        text = " ".join(p["review_checklist"])
        self.assertIn("Q2FY27 due by 2026-11-14", text)
        self.assertIn("PSU", text)
        self.assertIn("cyclical", text)
        self.assertIn("15.34", p["sell_note"])

    def test_small_amount_and_too_small(self):
        p = self.plan("--amount", "300", "--tranches", "3")
        self.assertEqual(p["total_qty"], 2)
        self.assertEqual(len(p["tranches"]), 2)
        code, _, err = run(["--trader-dir", self.td, "plan", "IOC", "--amount", "100"])
        self.assertEqual(code, 2)
        self.assertIn("does not buy one share", err)

    def test_orders_file_for_trader_risk(self):
        path = os.path.join(self.td, "orders.json")
        p = self.plan("--amount", "5000", "--orders", path)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["orders"]), 1)
        o = data["orders"][0]
        self.assertEqual((o["symbol"], o["side"], o["product"], o["qty"]), ("IOC", "buy", "CNC", "13"))
        self.assertIn("yes", p["next_step"])

    def test_text_plan(self):
        code, out, _ = run(["--trader-dir", self.td, "--today", TODAY, "plan", "IOC", "--amount", "5000"])
        self.assertEqual(code, 0)
        self.assertIn("₹5,000", out)
        self.assertIn("LTCG", out)
        self.assertIn("Not financial advice", out)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-invest", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-invest\n"))
        fm = text.split("---")[1]
        self.assertGreater(len(fm), 200)
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        self.assertIn("not financial advice", low)
        self.assertIn("yes", low)
        for f in ("SKILL.md", "invest.py", "scorecard.example.json"):
            with open(os.path.join(ROOT, "trader-invest", f), encoding="utf-8") as fh:
                self.assertNotIn("\u2014", fh.read(), f)


if __name__ == "__main__":
    unittest.main()
