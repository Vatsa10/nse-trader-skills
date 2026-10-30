import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

size = load("trader-budget", "size")
check = load("trader-risk", "check")
size.CHARGES_FILE = ""           # the embedded defaults, whatever is in the repo's trader/
check.CHARGES_FILE = ""

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-10-19 09:45"
TODAY = "2026-10-19"
PRICES = {"NIFTYBEES": 285.40, "GOLDBEES": 102.30, "BANKBEES": 598.20, "ITBEES": 41.75}
CONTRACT_CFG = {
    "budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 8000, "min_order_inr": 500,
    "max_loss_per_trade_inr": 100, "max_daily_loss_inr": 300, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "15:00", "no_mis_after": "15:00", "flat_by": "15:15",
    "default_product": "MIS", "broker": "zerodha", "paper": True, "blocked": [],
    "allow_mis_leverage": False, "allow_short": False,
}
SPLIT = {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 50, "min_order_inr": 500, "paper": True,
         "blocked": []}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def shares(result):
    return {p["ticker"]: p["shares"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-19T04:22:00Z"):
    return {"as_of": quote_time, "prices": {"RELIANCE": price}, "detail": {"RELIANCE": {"time": quote_time}}}


class TheReel(unittest.TestCase):
    """₹10,000, 10% buffer, ₹100 max loss, RELIANCE risk 20.00 -> 4 shares, ₹5,809.20, max loss ₹86.12."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_4_shares_bound_by_the_max_loss(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["product"]), (4, "max loss", "MIS"))
        self.assertEqual((s["risk_per_share"], s["cost"], s["charges"], s["max_loss"]), (20.0, 5809.2, 6.12, 86.12))
        self.assertEqual((s["reward_risk"], s["cash_buffer"]), (1.0, 1000))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 4), ("position cap", 5), ("max order", 5), ("cash", 6)])
        self.assertIsNone(s["refusal"])

    def test_charges_count_against_the_max_loss(self):
        # 5 x 20.00 = ₹100 exactly, but charges push it over: 4 shares
        self.assertEqual(size.loss_at_stop(5, 1452.30, 1432.30, "MIS"), 107.65)
        self.assertEqual(size.costs(4, 1452.30, 1432.30), check.costs(4, 1452.30, 1432.30))
        for args in ((4, 1452.30, 1432.30, "CNC"), (1000, 1000, 1000, "MIS"), (7, 99.95, 101.10, "CNC")):
            self.assertEqual(size.costs(*args), check.costs(*args), args)
        self.assertEqual(size.CHARGES_DEFAULTS, check.CHARGES_DEFAULTS)

    def test_the_bracket_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE", "side": "buy", "qty": "4", "product": "MIS", "type": "market",
            "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "1472.30",
            "stop_loss_stop_price": "1432.30",
            "plan": {"entry": 1452.3, "stop": 1432.3, "target": 1472.3, "risk_per_share": 20.0, "charges": 6.12,
                     "max_loss": 86.12, "setup": "orb", "entry_time": "09:45 AM", "flat_by": "15:15"}})
        self.assertIn("bound by your ₹100 max loss", why)
        self.assertIn("₹86.12", why)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(os.path.join(FIX, "trader-risk", "orders-reliance.json"), encoding="utf-8") as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_cnc_product(self):
        s = size.size_trade(plan(), cfg(), today=TODAY, product="CNC")
        # delivery: no brokerage, but STT both sides and the ₹15.34 DP charge on the sell: 3 shares
        self.assertEqual((s["product"], s["qty"]), ("CNC", 3))
        self.assertGreater(s["charges"], 6.12)
        self.assertLessEqual(s["max_loss"], 100)
        self.assertEqual(size.bracket_order(s)["product"], "CNC")
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(), cfg(), today=TODAY, product="NRML")

    def test_a_limit_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=1452.30, today=TODAY))
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", "1452.30", "4"))

    def test_cli_writes_one_bracket_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 4 shares of RELIANCE (MIS) = ₹5,809.20 at 1452.30", text)
            self.assertIn("4 x 20.00 + ~₹6.12 charges = ₹86.12", text)
            self.assertIn("Charges are an estimate, approximate (Zerodha rates verified 2026-10-29)", text)
            self.assertIn("ticket.py reconcile", text)
            self.assertIn("<- binds", text)
            self.assertIn("Flat by 15:15 IST", text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual((doc["status"], doc["paper"], doc["created_at"]), ("proposed", True, "2026-10-19T04:15:00Z"))
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 86.12)

    def test_printed_paths_use_forward_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                   "--now", NOW, "--write", out])
            self.assertEqual(code, 0, err)
            self.assertIn("Wrote 1 proposed bracket order to " + out.replace(os.sep, "/"), text)
            self.assertNotIn("\\", text + err)
            missing = os.path.join(d, "trader", "none.json")
            for argv in (["--plan", missing, "--config", CFG], ["--targets", missing, "--config", CFG],
                         ["--plan", PLAN, "--config", missing + "x", "--prices", missing]):
                code, text, err = run(argv)
                self.assertNotIn("\\", text + err, argv)
            self.assertIn(d.replace(os.sep, "/") + "/trader/none.json", run(["--plan", missing, "--config", CFG])[2])

    def test_cli_json_limit_and_product(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--product", "cnc", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["orders"][0]["limit_price"], "1452.30")
        self.assertEqual(out["orders"][0]["product"], "CNC")


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=1300.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your ₹100 max loss per trade", s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_each_limit_can_bind(self):
        self.assertEqual(size.size_trade(plan(), cfg(max_order_inr=3000), today=TODAY)["bound_by"], "max order")
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (1, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=4000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (2, "cash", 3000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=500), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (5, "position cap"))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1458.0, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"], s["qty"]), (1458.0, "price", 25.7, 3))
        s = size.size_trade(plan(), cfg(), price=1458.0, today=TODAY)
        self.assertIn("reward:risk 0.56", s["refusal"])
        self.assertEqual(size.size_trade(plan(), cfg(), price=1449.0, today=TODAY)["entry"], 1452.3)

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(1458.0), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-19 09:55"])
            out = json.loads(text)
            self.assertEqual((code, out["orders"]), (1, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("09:52 IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-19 10:30"])[1])
            self.assertEqual(out["sizing"]["qty"], 4)
            self.assertIn("not used", out["notes"][0])

    def test_bad_plans(self):
        for p, word in ((plan(stop=1460.0), "not below the entry"),
                        (plan(target=1450.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-21")
        self.assertIn("make a new plan", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="IDEA", entry=8.0, stop=7.0, target=9.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=40), today=TODAY)
        self.assertLess(s["cost"], 500)
        self.assertIn("under your ₹500 minimum order", s["refusal"])

    def test_missing_plan_and_bad_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class TargetsMode(unittest.TestCase):
    """A user-supplied list split across ₹10,000 in whole shares."""

    def test_the_example_targets(self):
        r = size.size(rows(TARGETS), split(), prices=PRICES)
        self.assertEqual(shares(r), {"NIFTYBEES": 16, "GOLDBEES": 18, "BANKBEES": 2, "ITBEES": 33})
        self.assertEqual(r["cash_buffer"], 1000)
        self.assertAlmostEqual(r["invested"] + r["idle"], 9000, places=2)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertAlmostEqual(p["rupees"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["rupees"], 5000)

    def test_orders_are_cnc_whole_shares(self):
        r = size.size(rows(TARGETS), split(), prices=PRICES)
        orders = size.orders_from(r, "my targets")
        self.assertEqual(orders[0]["product"], "CNC")
        for o in orders:
            self.assertNotIn("notional", o)
            self.assertEqual(o["qty"], str(int(o["qty"])))
        self.assertEqual(size.orders_from(r, product="MIS")[0]["product"], "MIS")

    def test_prices_are_required(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(TARGETS), split())
        self.assertIn("need a price", str(e.exception))

    def test_cap_redistributes(self):
        w = size.parse_weights({"NIFTYBEES": 80, "GOLDBEES": 20})
        r = size.size(w, split(max_position_pct=40, min_order_inr=1), prices={"NIFTYBEES": 100.0, "GOLDBEES": 100.0})
        self.assertEqual(shares(r), {"NIFTYBEES": 40, "GOLDBEES": 40})
        self.assertEqual(r["idle"], 1000)

    def test_slices_under_the_minimum_are_dropped(self):
        w = size.parse_weights({"NIFTYBEES": 95, "ITBEES": 5})
        r = size.size(w, split(max_position_pct=100), prices=PRICES)
        self.assertNotIn("ITBEES", shares(r))
        self.assertIn("₹500 minimum", r["dropped"][0]["reason"])

    def test_blocked_and_top(self):
        r = size.size(rows(TARGETS), split(blocked=["goldbees"]), prices=PRICES)
        self.assertNotIn("GOLDBEES", shares(r))
        r = size.size(rows(TARGETS), split(max_position_pct=100), prices=PRICES, top=2)
        self.assertEqual(sorted(shares(r)), ["GOLDBEES", "NIFTYBEES"])

    def test_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg("niftybees=50,GOLDBEES=20,BANKBEES=15,ITBEES=15"))
        self.assertEqual(shares(size.size(w, split(), prices=PRICES)), shares(size.size(rows(TARGETS), split(),
                                                                                        prices=PRICES)))

    def test_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), {"cash_buffer_pct": 10}, prices=PRICES)
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "BEL", "name": "", "weight": 1}, {"ticker": "BEL", "name": "", "weight": 2}],
                      split(), prices={"BEL": 400.0})

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split(), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--write", out])
            self.assertEqual(code, 0, text)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            self.assertIn("₹", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]], ["NIFTYBEES", "GOLDBEES", "BANKBEES", "ITBEES"])
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no targets file", err)


class Charges(unittest.TestCase):
    def test_the_example_file_is_the_embedded_defaults(self):
        with open(os.path.join(ROOT, "trader-budget", "charges.example.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), size.CHARGES_DEFAULTS)
        self.assertEqual(size.load_charges(os.path.join(ROOT, "trader-budget", "charges.example.json")),
                         size.CHARGES_DEFAULTS)

    def test_a_calibrated_charges_file_changes_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "charges.json")
            with open(path, "w") as fh:
                json.dump({"calibration": {"factor": 1.04, "samples": 2, "updated": "2026-10-29"}}, fh)
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--charges", path, "--json"])
            self.assertEqual(code, 0)
            s = json.loads(text)["sizing"]
            self.assertEqual((s["charges"], s["max_loss"]), (6.37, 86.37))
            self.assertEqual(s["charges_label"], "estimate (calibrated from 2 contract notes)")
            self.assertEqual(size.CHARGES_FILE, "")          # the flag does not leak


if __name__ == "__main__":
    unittest.main()
