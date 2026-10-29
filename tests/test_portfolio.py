import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

pf = load("trader-portfolio", "portfolio")

SKILL = os.path.join(ROOT, "trader-portfolio")
F = os.path.join(FIX, "trader-portfolio")
KITE = os.path.join(F, "kite-holdings.json")
CONSOLE = os.path.join(F, "console-holdings.csv")
TRADEBOOK = os.path.join(F, "tradebook.csv")
PRICES = os.path.join(F, "prices.json")
TARGETS = os.path.join(SKILL, "targets.example.json")
TODAY = datetime.date(2026, 10, 29)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = pf.main(argv)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.groups = pf.load_groups()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cli(self, *args):
        return run(list(args[:1]) + ["--dir", self.dir, "--today", "2026-10-29"] + list(args[1:]))

    def imported(self, *files):
        code, out, err = self.cli("import", *(files or (KITE, TRADEBOOK)))
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "holdings.json")) as fh:
            return json.load(fh)

    def by_sym(self, doc):
        return {h["symbol"]: h for h in doc["holdings"]}


class Import(Base):
    def test_kite_get_holdings_with_t1(self):
        doc = self.imported(KITE)
        h = self.by_sym(doc)
        self.assertEqual(sorted(h), ["HAL", "HINDCOPPER", "TATAGOLD", "TATSILV"])
        self.assertEqual((h["TATSILV"]["qty"], h["TATSILV"]["t1_qty"]), (442, 50))
        self.assertEqual((h["TATAGOLD"]["qty"], h["TATAGOLD"]["avg_price"]), (675, 14.82))
        self.assertEqual(h["HAL"]["product"], "CNC")
        self.assertIsNone(h["HAL"]["buy_date"])
        for k in ("as_of", "source", "cash", "holdings"):
            self.assertIn(k, doc)
        for row in doc["holdings"]:
            for k in ("symbol", "qty", "avg_price", "last_price", "buy_date", "product"):
                self.assertIn(k, row)

    def test_mcp_text_wrapper_is_unwrapped(self):
        with open(KITE) as fh:
            raw = json.load(fh)
        wrapped = {"content": [{"type": "text", "text": json.dumps(raw["data"])}]}
        p = os.path.join(self.dir, "wrapped.json")
        with open(p, "w") as fh:
            json.dump(wrapped, fh)
        self.assertEqual(len(pf.parse_kite(p)), 4)

    def test_console_csv_skips_the_preamble(self):
        rows = {r["symbol"]: r for r in pf.parse_console(CONSOLE)}
        self.assertEqual(rows["HINDCOPPER"]["qty"], 10)
        self.assertEqual(rows["HINDCOPPER"]["avg_price"], 472.35)
        self.assertEqual(rows["HINDCOPPER"]["last_price"], 356.85)
        self.assertEqual(rows["HAL"]["sector"], "AEROSPACE & DEFENSE")
        self.assertEqual(pf.detect(CONSOLE), "console")
        self.assertEqual(pf.detect(TRADEBOOK), "tradebook")

    def test_tradebook_fifo_lots_and_realised(self):
        doc = self.imported(KITE, TRADEBOOK)
        h = self.by_sym(doc)
        self.assertEqual(h["TATAGOLD"]["lots"], [{"date": "2025-08-20", "qty": 300, "price": 13.95},
                                                 {"date": "2026-04-10", "qty": 375, "price": 15.52}])
        self.assertEqual(h["TATAGOLD"]["buy_date"], "2025-08-20")
        self.assertEqual([l["qty"] for l in h["TATSILV"]["lots"]], [392, 50])
        real = {(m["symbol"], m["sell_date"]): m for m in doc["realised"]}
        self.assertEqual(real[("TATAGOLD", "2026-02-10")]["buy_date"], "2024-12-02")   # FIFO: oldest first
        self.assertEqual(real[("TATAGOLD", "2026-02-10")]["term"], "long")
        self.assertEqual(real[("ITC", "2026-05-05")]["gain"], 440.0)
        self.assertEqual(real[("INFY", "2026-07-01")]["term"], "short")
        self.assertEqual(doc["notes"], [])

    def test_tradebook_only_builds_holdings(self):
        doc = self.imported(TRADEBOOK)
        h = self.by_sym(doc)
        self.assertEqual(h["TATAGOLD"]["qty"], 675)
        self.assertAlmostEqual(h["TATAGOLD"]["avg_price"], 14.82, places=2)
        self.assertNotIn("ITC", h)
        self.assertTrue(any("no last price" in n for n in doc["notes"]))

    def test_later_snapshot_keeps_lots(self):
        self.imported(KITE, TRADEBOOK)
        doc = self.imported(KITE)
        self.assertEqual(len(self.by_sym(doc)["TATAGOLD"]["lots"]), 2)
        self.assertEqual(len(doc["realised"]), 4)

    def test_lot_mismatch_is_reported(self):
        doc = self.imported(CONSOLE, TRADEBOOK)        # Console has no T1: 392 vs lots of 442
        self.assertTrue(any("TATSILV" in n and "442" in n for n in doc["notes"]))


class LongTerm(unittest.TestCase):
    def test_more_than_twelve_months(self):
        d = datetime.date(2025, 10, 1)
        self.assertEqual(pf.lt_date(d), datetime.date(2026, 10, 2))
        self.assertFalse(pf.is_long_term(d, datetime.date(2026, 10, 1)))
        self.assertTrue(pf.is_long_term(d, datetime.date(2026, 10, 2)))
        self.assertEqual(pf.lt_date(datetime.date(2024, 2, 29)), datetime.date(2025, 3, 1))

    def test_fy(self):
        self.assertEqual(pf.fy_bounds("2026-27")[:2], (datetime.date(2026, 4, 1), datetime.date(2027, 3, 31)))
        self.assertEqual(pf.fy_bounds("FY27")[2], "2026-27")
        self.assertEqual(pf.fy_of(datetime.date(2026, 3, 31)), "2025-26")


class Show(Base):
    def test_values_weights_lots(self):
        self.imported()
        code, out, _ = self.cli("show", "--json")
        s = json.loads(out)
        self.assertEqual(s["value"], 29764.2)
        self.assertEqual(s["invested"], 29516.2)
        self.assertAlmostEqual(sum(r["weight"] for r in s["holdings"]), 100, delta=0.05)
        rows = {r["symbol"]: r for r in s["holdings"]}
        self.assertEqual(rows["HAL"]["day_change"], 26.6)
        hal = rows["HAL"]["lots"][0]
        self.assertEqual((hal["lt_date"], hal["long_term"]), ("2026-10-02", True))
        tg = rows["TATAGOLD"]["lots"][1]
        self.assertEqual((tg["lt_date"], tg["long_term"], tg["days_to_lt"]), ("2027-04-11", False, 164))
        code, text, _ = self.cli("show")
        self.assertIn("₹29,764.20", text)
        self.assertIn("includes T1", text)


class Risk(Base):
    def test_sample_portfolio_warnings(self):
        self.imported()
        code, out, _ = self.cli("risk", "--json")
        r = json.loads(out)
        warn = {c["check"] for c in r["checks"] if c["level"] == "warn"}
        self.assertEqual(warn, {"single stock", "commodity", "broad index"})
        self.assertAlmostEqual(r["commodity_pct"], 72.3, delta=0.05)
        self.assertAlmostEqual(r["psu_pct"], 27.7, delta=0.05)
        self.assertEqual(r["index_pct"], 0)
        single = [c for c in r["checks"] if c["check"] == "single stock"][0]
        self.assertEqual(single["symbols"], ["HAL"])

    def test_fundamentals_sector_wins(self):
        self.imported()
        os.makedirs(os.path.join(self.dir, "data", "fundamentals"))
        with open(os.path.join(self.dir, "data", "fundamentals", "HINDCOPPER.json"), "w") as fh:
            json.dump({"symbol": "HINDCOPPER", "sector": "Non-Ferrous Metals"}, fh)
        r = pf.risk(pf.load_holdings(self.dir), self.dir, self.groups, TODAY)
        self.assertIn("Non-Ferrous Metals", [s["sector"] for s in r["sectors"]])

    def test_group_exposure(self):
        doc = {"holdings": [
            {"symbol": "RELIANCE", "qty": 10, "avg_price": 1400, "last_price": 1450},
            {"symbol": "JIOFIN", "qty": 40, "avg_price": 300, "last_price": 310},
            {"symbol": "NIFTYBEES", "qty": 100, "avg_price": 280, "last_price": 285}]}
        r = pf.risk(doc, self.dir, self.groups, TODAY)
        g = [c for c in r["checks"] if c["check"] == "group"][0]
        self.assertEqual(g["level"], "warn")
        self.assertEqual(g["symbols"], ["RELIANCE", "JIOFIN"])
        self.assertNotIn("broad index", {c["check"] for c in r["checks"] if c["level"] == "warn"})

    def test_asset_classes(self):
        for sym, cls in (("GOLDBEES", "gold_etf"), ("TATAGOLD", "gold_etf"), ("SILVERBEES", "silver_etf"),
                         ("TATSILV", "silver_etf"), ("NIFTYBEES", "index_etf"), ("ITBEES", "sector_etf"),
                         ("BANKBEES", "sector_etf"), ("HAL", "equity")):
            self.assertEqual(pf.asset_class(sym, self.groups), cls, sym)
        for sym in ("HINDCOPPER", "IOC", "HAL", "BEL", "ONGC", "COALINDIA", "NTPC"):
            self.assertIn(sym, self.groups["psu"])


class Charges(unittest.TestCase):
    def test_delivery_model(self):
        c = pf.delivery_charges(buy_value=10000)
        # STT 10 + stamp 1.50 + txn 0.307 + SEBI 0.01 + GST 18% of (0.307 + 0.01)
        self.assertEqual(c["total"], round(10 + 1.5 + 0.307 + 0.01 + 0.18 * 0.317, 2))
        s = pf.delivery_charges(sell_value=10000, sell_scrips=1)
        self.assertEqual(s["dp"], 15.34)
        self.assertEqual(s["stamp"], 0)
        self.assertEqual(c["label"], "estimate")

    def test_calibration_and_override(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "charges.json"), "w") as fh:
                json.dump({"calibration": {"factor": 1.1, "samples": 3}}, fh)
            ch = pf.load_charges(d)
            self.assertEqual(ch["delivery"]["stt_buy_pct"], 0.1)          # deep merge keeps defaults
            c = pf.delivery_charges(buy_value=10000, ch=ch)
            self.assertAlmostEqual(c["total"], pf.delivery_charges(buy_value=10000)["total"] * 1.1, places=1)
            self.assertEqual(c["label"], "estimate (calibrated from 3 contract notes)")
        finally:
            shutil.rmtree(d)

    def test_matches_trader_risk_when_available(self):
        try:
            check = load("trader-risk", "check")
        except Exception:
            self.skipTest("trader-risk not loadable")
        if not hasattr(check, "charge_parts"):
            self.skipTest("trader-risk has no charge_parts yet")
        parts = check.charge_parts(5000, 3000, "CNC", model=pf.CHARGES_DEFAULT)
        mine = pf.delivery_charges(5000, 3000, 1)
        self.assertAlmostEqual(sum(parts.values()), mine["total"], places=1)


class Tax(Base):
    def test_fy_2026_27(self):
        self.imported()
        code, out, _ = self.cli("tax", "--fy", "2026-27", "--json")
        t = json.loads(out)
        e = t["estimate"]
        self.assertEqual(e["gross"], {"equity_st": -1900.0, "other_st": 450.0, "equity_lt": 440.0, "other_lt": 0.0})
        self.assertEqual(e["tax"], 0)
        self.assertEqual(e["carry_forward"]["short_term_loss"], 1010.0)
        self.assertEqual(e["exemption_left"], 125000.0)
        self.assertEqual(len(t["realised"]), 3)
        self.assertTrue(any("check with a CA" in n for n in t["notes"]))

    def test_fy_2025_26_gold_ltcg_has_no_exemption(self):
        self.imported()
        t = pf.tax_report(pf.load_holdings(self.dir), self.dir, self.groups, TODAY, "2025-26")
        self.assertEqual(t["estimate"]["after_setoff"]["other_lt"], 300.0)
        self.assertEqual(t["estimate"]["tax"], 37.5)
        self.assertEqual(t["estimate"]["tax_with_cess"], 39.0)

    def test_exemption_and_setoff(self):
        e = pf.tax_estimate({"equity_lt": 200000, "equity_st": 10000})
        self.assertEqual(e["exemption_used"], 125000)
        self.assertEqual(e["tax"], 75000 * 0.125 + 10000 * 0.20)
        e = pf.tax_estimate({"equity_lt": 50000, "equity_st": -20000})
        self.assertEqual(e["after_setoff"]["equity_lt"], 30000)
        self.assertEqual(e["exemption_left"], 95000)
        e = pf.tax_estimate({"equity_lt": -5000, "equity_st": 8000})       # LT loss cannot touch ST gains
        self.assertEqual(e["after_setoff"]["equity_st"], 8000)
        self.assertEqual(e["carry_forward"]["long_term_loss"], 5000)

    def test_harvest_idea(self):
        doc = {"holdings": [{"symbol": "ITC", "qty": 100, "avg_price": 300, "last_price": 450,
                             "lots": [{"date": "2024-01-10", "qty": 100, "price": 300}]}], "realised": []}
        t = pf.tax_report(doc, self.dir, self.groups, TODAY, "2026-27")
        h = [i for i in t["ideas"] if i["kind"] == "harvest ltcg"]
        self.assertEqual((h[0]["qty"], h[0]["gain"]), (100, 15000.0))


class Rebalance(Base):
    def reb(self, *extra):
        self.imported()
        code, out, err = self.cli("rebalance", "--targets", TARGETS, "--prices", PRICES, "--cash", "5000",
                                  "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_tax_aware_holds_back_short_term_gains(self):
        r = self.reb()
        rows = {x["symbol"]: x for x in r["rows"]}
        self.assertEqual((rows["TATAGOLD"]["side"], rows["TATAGOLD"]["qty"]), ("sell", 300))
        self.assertIn("2027-04-11", rows["TATAGOLD"]["note"])
        self.assertIsNone(rows["TATSILV"]["side"])
        self.assertIn("short-term", rows["TATSILV"]["note"])
        self.assertEqual(rows["NIFTYBEES"]["side"], "buy")
        self.assertEqual([m["term"] for m in r["tax"]["sells"]], ["long"])
        self.assertGreaterEqual(r["cash_left"], 0)
        self.assertEqual(r["base"], 34764.2)

    def test_ignore_mode_reaches_targets(self):
        r = self.reb("--tax-mode", "ignore")
        rows = {x["symbol"]: x for x in r["rows"]}
        self.assertEqual(rows["TATSILV"]["qty"], 302)
        self.assertEqual(rows["TATSILV"]["side"], "sell")
        self.assertLessEqual(rows["TATSILV"]["qty"], 442 - 50)                # T1 shares stay
        for x in r["rows"]:
            if x["symbol"] != "HAL":
                self.assertLess(abs(x["weight_after"] - x["target_pct"]), 3.0, x["symbol"])
        self.assertGreaterEqual(r["cash_left"], 0)

    def test_orders_file_matches_trader_risk(self):
        self.imported()
        code, _, err = self.cli("rebalance", "--targets", TARGETS, "--prices", PRICES, "--cash", "5000", "--write")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "orders.json")) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["created_by"], doc["status"]), ("trader-portfolio", "proposed"))
        sides = [o["side"] for o in doc["orders"]]
        self.assertEqual(sides, sorted(sides, key=lambda s: s != "sell"))     # sells first
        for o in doc["orders"]:
            self.assertEqual((o["product"], o["type"], o["time_in_force"]), ("CNC", "market", "day"))
            self.assertIsInstance(o["qty"], str)
            self.assertTrue(o["qty"].isdigit())
        try:
            check = load("trader-risk", "check")
            fields = set(check.ORDER_FIELDS)
        except Exception:
            self.skipTest("trader-risk not loadable")
        for o in doc["orders"]:
            self.assertTrue(set(o) - {"why"} <= fields, o)

    def test_limit_orders_on_tick(self):
        r = self.reb("--limit-pct", "0.5")
        for o in r["orders"]:
            self.assertEqual(o["type"], "limit")
            lp = float(o["limit_price"])
            self.assertAlmostEqual(lp * 100, round(lp * 100), places=6)
        self.assertEqual(pf.tick_round(361.23, True, "equity"), 361.25)
        self.assertEqual(pf.tick_round(4625.03, False, "equity"), 4625.0)

    def test_no_write_by_default_and_missing_price(self):
        self.reb()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "orders.json")))
        code, _, err = self.cli("rebalance", "--targets", TARGETS, "--cash", "5000")
        self.assertEqual(code, 1)
        self.assertIn("no price for", err)


class Sip(Base):
    def test_carry_ledger(self):
        self.imported()
        code, out, err = self.cli("sip", "--monthly", "5000", "--targets", TARGETS, "--prices", PRICES,
                                  "--json", "--write")
        self.assertEqual(code, 0, err)
        s = json.loads(out)
        rows = {r["symbol"]: r for r in s["rows"]}
        self.assertEqual(rows["HAL"]["qty"], 0)
        self.assertEqual(rows["HAL"]["carry"], 750.0)
        self.assertEqual(rows["NIFTYBEES"]["qty"], 6)
        self.assertAlmostEqual(s["spent"] + s["carry_out"], 5000, places=1)
        with open(os.path.join(self.dir, "sip.json")) as fh:
            self.assertEqual(json.load(fh)["carry"]["HAL"], 750.0)
        code, out, _ = run(["sip", "--dir", self.dir, "--today", "2026-11-29", "--monthly", "5000",
                            "--targets", TARGETS, "--prices", PRICES, "--json"])
        s2 = json.loads(out)
        self.assertAlmostEqual(s2["carry_in"], s["carry_out"], places=1)
        self.assertEqual({r["symbol"]: r for r in s2["rows"]}["JUNIORBEES"]["qty"], 1)


class Files(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-portfolio\n"))
        fm = text.split("---\n")[1]
        self.assertGreater(len(fm), 200)
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        self.assertIn("not financial advice", low)
        self.assertIn("yes", low)
        self.assertIn("get_holdings", text)

    def test_no_em_dashes(self):
        for name in ("SKILL.md", "portfolio.py", "groups.json", "targets.example.json"):
            with open(os.path.join(SKILL, name), encoding="utf-8") as fh:
                self.assertNotIn(chr(0x2014), fh.read(), name)

    def test_inr(self):
        self.assertEqual(pf.inr(12345678.9), "₹1,23,45,678.90")
        self.assertEqual(pf.inr(-1111.5), "-₹1,111.50")


if __name__ == "__main__":
    unittest.main()
