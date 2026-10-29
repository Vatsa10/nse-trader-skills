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

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
for _m in (ticket, check):
    _m.CHARGES_FILE = ""         # the embedded defaults, whatever is in the repo's trader/
    _m.BANDS_FILE = ""
BANDS = os.path.join(FIX, "trader-risk", "bands.json")
TRADEBOOK = os.path.join(FIX, "trader-broker", "tradebook.csv")
NOTES_CSV = os.path.join(FIX, "trader-broker", "contract-charges.csv")
NOTES_JSON = os.path.join(FIX, "trader-broker", "contract-charges.json")
RELIANCE_FIX = os.path.join(FIX, "trader-risk", "orders-reliance.json")
BUDGET_CFG = os.path.join(ROOT, "trader-budget", "config.example.json")

NOW = "2026-10-19 10:15"          # IST
TODAY = "2026-10-19"
CFG = {"budget": 100000, "paper": True, "flat_by": "15:15"}
HC_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "HINDCOPPER", "transaction_type": "BUY",
           "quantity": 40, "product": "MIS", "order_type": "MARKET", "validity": "DAY"}
HC_ORDER = {"symbol": "HINDCOPPER", "side": "buy", "qty": "40", "product": "MIS", "type": "market",
            "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "320.50",
            "stop_loss_stop_price": "308.10",
            "why": "opening-range breakout on HINDCOPPER: 40 shares, bound by your ₹250 max loss per trade",
            "plan": {"entry": 314.30, "stop": 308.10, "target": 320.50}}
HC = ticket.to_kite(HC_ORDER)
BATCH = ["HINDCOPPER", "RELIANCE", "TCS", "INFY", "BEL"]
REL_ORDER = {"symbol": "RELIANCE", "side": "buy", "qty": "5", "product": "CNC", "type": "limit",
             "limit_price": "1450.50", "time_in_force": "day"}
REL = ticket.to_kite(REL_ORDER)
REL_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
            "quantity": 5, "product": "CNC", "order_type": "LIMIT", "price": 1450.5, "validity": "DAY"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(ticket.inr(1234567.89), "₹12,34,567.89")
        self.assertEqual(ticket.inr(248), "₹248")
        self.assertEqual(ticket.inr(248, True), "₹248.00")
        self.assertEqual(ticket.inr(100000), "₹1,00,000")
        self.assertEqual(ticket.inr(-999.5), "-₹999.50")

    def test_costs_are_small_and_positive(self):
        c = ticket.costs(40 * 314.30, 40 * 308.10, "MIS")
        self.assertTrue(10 < c < 60, c)
        self.assertGreater(ticket.costs(100000, 100000, "CNC"), ticket.costs(100000, 100000, "MIS"))

    def test_same_model_as_trader_risk(self):
        self.assertEqual(ticket.CHARGES_DEFAULTS, check.CHARGES_DEFAULTS)
        for args in ((4, 1452.30, 1432.30, "MIS"), (40, 314.30, 308.10, "CNC")):
            q, b, s, p = args
            self.assertEqual(ticket.costs(q * b, q * s, p), check.costs(*args)["total"], args)


class IST(unittest.TestCase):
    def test_parse_now_is_ist(self):
        import datetime
        self.assertEqual(ticket.parse_now("2026-10-19 10:15"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(ticket.ist_iso(ticket.parse_now(NOW)), "2026-10-19T10:15:00+05:30")


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=REL, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes RELIANCE", "yes 5 RELIANCE",
                  "yes 1450.5"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "haan", "go", "do it", "yeah", "yep", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes TCS", "yes 10", "yes but make it 10", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 1400"):
            self.assertFalse(self.yes(r), r)

    def test_a_bracket_yes(self):
        for r in ("yes", "yes 40 HINDCOPPER", "yes 308.10", "yes max loss 248", "yes target 320.50"):
            self.assertTrue(self.yes(r, HC, ["HINDCOPPER"]), r)
        for r in ("yes 41", "yes but stop at 307", "yes, stop 308.10", "yes RELIANCE", "sure"):
            self.assertFalse(self.yes(r, HC, ["HINDCOPPER", "RELIANCE"]), r)


class TicketShape(unittest.TestCase):
    def test_tool_input_is_the_kite_place_order_call(self):
        self.assertEqual(ticket.tool_input(HC), HC_CALL)
        self.assertEqual(ticket.tool_input(REL), REL_CALL)
        self.assertEqual(ticket.fingerprint(HC), ticket.fingerprint(dict(HC_ORDER, why="other")))
        self.assertNotEqual(ticket.fingerprint(HC_ORDER), ticket.fingerprint(dict(HC_ORDER, qty="41")))
        sl = ticket.to_kite({"symbol": "BEL", "side": "sell", "qty": "10", "type": "stop_limit", "stop_price": "400",
                             "limit_price": "399.5", "time_in_force": "ioc", "product": "MIS"})
        self.assertEqual((sl["order_type"], sl["trigger_price"], sl["price"], sl["validity"]), ("SL", 400, 399.5, "IOC"))
        self.assertEqual(ticket.to_kite({"symbol": "BEL", "side": "sell", "qty": 1, "type": "stop",
                                         "stop_price": 400})["order_type"], "SL-M")
        with self.assertRaises(ticket.TicketError):
            ticket.to_kite(HC_CALL)                     # Kite names are output only

    def test_mis_bracket_is_sl_m_plus_limit(self):
        exits = ticket.exit_calls(HC)
        self.assertEqual([t for t, _, _ in exits], ["place_order", "place_order"])
        stop, tgt = exits[0][1], exits[1][1]
        self.assertEqual((stop["order_type"], stop["trigger_price"], stop["transaction_type"], stop["quantity"],
                          stop["product"]), ("SL-M", 308.1, "SELL", 40, "MIS"))
        self.assertEqual((tgt["order_type"], tgt["price"]), ("LIMIT", 320.5))

    def test_cnc_bracket_is_a_gtt_oco(self):
        o = ticket.to_kite(dict(REL_ORDER, order_class="bracket", stop_loss_stop_price="1400",
                                take_profit_limit_price="1520"))
        (tool, args, words), = ticket.exit_calls(o)
        self.assertEqual(tool, "place_gtt_order")
        self.assertEqual((args["trigger_type"], args["trigger_values"]), ("two-leg", [1400, 1520]))
        self.assertEqual([l["transaction_type"] for l in args["orders"]], ["SELL", "SELL"])

    def test_plain_english(self):
        self.assertEqual(ticket.plain(REL), "BUY 5 RELIANCE on NSE, limit ₹1,450.50, CNC, good for today")
        parts = ticket.plain(HC).split(" / ")
        self.assertEqual(parts[:3], ["BUY 40 HINDCOPPER on NSE, market, MIS, good for today",
                                     "stop-loss 308.10", "target 320.50"])
        self.assertIn("max loss about ₹248.00 if the stop fills at 308.10", parts[3])
        self.assertIn("charges, estimate", parts[3])
        self.assertIn("lower circuit", parts[3])
        self.assertEqual(ticket.max_loss(HC), (248.0, 308.1))
        self.assertEqual(ticket.max_loss(ticket.to_kite(dict(HC_ORDER, plan=None))), (None, 308.1))

    def test_shape_rules(self):
        for bad in (dict(REL, quantity=2.5), dict(REL, exchange="BSE"), dict(REL, product="NRML"),
                    dict(REL, order_type="STOP"), dict(REL, tradingsymbol="nifty24oct25000ce lower")):
            with self.assertRaises(ticket.TicketError):
                ticket.check_shape(bad)
        self.assertEqual(ticket.norm_symbol("m&m.ns"), "M&M")
        self.assertEqual(ticket.norm_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO")


class SharedSchema(unittest.TestCase):
    def test_same_fields_and_fingerprints_as_trader_risk(self):
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)
        with open(RELIANCE_FIX) as fh:
            orders = json.load(fh)["orders"]
        for o in orders + [HC_ORDER, REL_ORDER]:
            self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
            self.assertEqual(ticket.fingerprint(ticket.to_kite(o)), check.fingerprint(o))

    def test_risk_check_then_show_is_ready(self):
        d = tempfile.mkdtemp()
        try:
            orders, risk = os.path.join(d, "orders.json"), os.path.join(d, "risk.json")
            state, none = os.path.join(d, "broker.json"), os.path.join(d, "none")
            shutil.copy(RELIANCE_FIX, orders)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", orders, "--config", BUDGET_CFG, "--portfolio", none,
                                   "--prices", none, "--state", state, "--now", "2026-10-19 09:45",
                                   "--out", risk])
            self.assertEqual(code, 0)
            code, text, err = run(["show", "--index", "1", "--orders", orders, "--config", BUDGET_CFG,
                                   "--risk", risk, "--state", state, "--now", "2026-10-19 09:45"])
            self.assertEqual(code, 0, text + err)
            self.assertIn("READY: trader-risk PASS", text)
            self.assertIn('"tradingsymbol": "RELIANCE"', text)
            self.assertIn('"order_type": "SL-M"', text)
            self.assertEqual(run(["approve", "--index", "1", "--reply", "yes", "--orders", orders, "--config",
                                  BUDGET_CFG, "--risk", risk, "--state", state, "--now", "2026-10-19 09:45"])[0], 0)
        finally:
            shutil.rmtree(d)


class DesktopConfig(unittest.TestCase):
    def test_example_is_the_hosted_kite_mcp(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")) as fh:
            server = json.load(fh)["mcpServers"]["kite"]
        self.assertEqual((server["command"], server["args"]), ("npx", ["mcp-remote", "https://mcp.kite.trade/mcp"]))
        self.assertNotIn("env", server)   # no keys: Kite login is OAuth in the browser


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        self.cfg = os.path.join(self.d, "config.json")
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.plan = os.path.join(self.d, "plan.json")
        self.none = os.path.join(self.d, "none")
        self.write(self.orders, {"created_by": "trader-budget", "orders": [HC_ORDER]})
        self.write(self.cfg, CFG)
        self.write(self.plan, {"symbol": "HINDCOPPER", "date": TODAY, "entry": 314.30, "stop": 308.10,
                               "target": 320.50, "flat_by": "15:15"})

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, path, doc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def read(self, path):
        with open(path) as fh:
            return json.load(fh)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def pass_risk(self, date=TODAY, verdict="PASS", kind="entry", reasons=()):
        orders = self.read(self.orders)["orders"]
        self.write(self.risk, {"date": date, "results": [
            {"index": i, "fingerprint": ticket.fingerprint(o), "verdict": verdict, "kind": kind,
             "reasons": list(reasons)} for i, o in enumerate(orders, 1)]})

    def live(self):
        self.write(self.cfg, dict(CFG, paper=False))

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_paper_is_the_default_and_never_sends(self):
        self.pass_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("PAPER mode (logged only, never sent), Zerodha Kite", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("do NOT call place_order", text)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("paper mode", err)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "paper", "--filled-qty", "40",
                                   "--filled-price", "314.30", "--portfolio", self.pf))
        self.assertEqual(code, 0)
        self.assertEqual(self.read(self.pf)["cash"], 100000 - 12572.0)

    def test_the_full_live_two_yes_flow(self):
        self.live()
        self.pass_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, LIVE KITE ACCOUNT, REAL MONEY, Zerodha Kite", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn('"tradingsymbol": "HINDCOPPER"', text)
        self.assertIn('"order_type": "SL-M"', text)
        self.assertIn("do NOT cancel each other", text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        self.assertIn("Allow once", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("call place_order", text)
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1]), HC_CALL)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id",
                                      "260119000123456", "--filled-qty", "40", "--filled-price", "314.30",
                                      "--portfolio", self.pf, now="2026-10-19 10:16"))
        self.assertEqual(code, 0)
        self.assertIn("SL-M", text)
        st = self.read(self.state)
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 248.0, TODAY))
        self.assertEqual(appr["at"], "2026-10-19T10:15:00+05:30")
        self.assertEqual((plc["broker_order_id"], plc["max_loss"]), ("260119000123456", 248.0))
        pf = self.read(self.pf)
        self.assertEqual((pf["cash"], pf["positions"][0]["shares"]), (87428.0, 40))

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed, 0 paper today", text)
        self.assertIn("max loss ₹248.00", text)

    def test_flatten_by_1515_ist(self):
        self.write(self.pf, {"cash": 87428.0, "positions": [{"ticker": "HINDCOPPER", "shares": 40, "lots": [
            {"id": "HINDCOPPER-1", "date": TODAY, "shares": 40, "cost": 12572.0}]}]})
        late = "2026-10-19 15:15"
        code, text, _ = run(["flatten", "--symbol", "hindcopper", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", self.plan, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 40 HINDCOPPER on NSE, market, MIS, good for today (flat by 15:15 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("cancel_order", text)
        self.assertIn("15:20", text)
        doc = self.read(self.orders)
        self.assertEqual(doc["orders"][0], dict(doc["orders"][0], symbol="HINDCOPPER", side="sell", qty="40",
                                                type="market", product="MIS", time_in_force="day"))
        self.assertNotIn("tradingsymbol", doc["orders"][0])
        self.assertEqual(ticket.tool_input(ticket.to_kite(doc["orders"][0])), dict(HC_CALL, transaction_type="SELL"))
        self.live()
        self.pass_risk(kind="exit")
        self.assertEqual(run(self.args("show", now=late))[0], 0)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 40 HINDCOPPER", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "40",
                                   "--filled-price", "316.00", "--portfolio", self.pf, now="2026-10-19 15:16"))
        self.assertEqual(code, 0)
        pf = self.read(self.pf)
        self.assertEqual((pf["cash"], pf["positions"]), (100068.0, []))
        code, _, err = run(["flatten", "--symbol", "HINDCOPPER", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_default_time_and_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "BEL", "--qty", "25", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.none, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("flat by 15:15 IST", text)
        self.assertNotIn("Check it against get_positions", text)
        self.assertEqual(run(["flatten", "--symbol", "BEL", "--qty", "0", "--orders", self.orders])[0], 1)
        self.assertEqual(run(["flatten", "--symbol", "BEL", "--qty", "2.5", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.pass_risk()
        doc = self.read(self.orders)
        doc["orders"][0]["qty"] = "41"
        self.write(self.orders, doc)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.pass_risk(date="2026-10-16")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.pass_risk(verdict="BLOCK", reasons=["window: no new entries before 09:30"])
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-19 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-19 09:20"))[0], 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.pass_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        self.assertEqual(run(self.args("decline", "--index", "1", "--reply", "no thanks"))[0], 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        self.write(self.pf, {"cash": 0, "positions": [{"ticker": "TCS", "shares": 3, "lots": [
            {"id": "TCS-1", "date": "2025-01-02", "shares": 2, "cost": 6000.0},
            {"id": "TCS-2", "date": "2026-01-02", "shares": 1, "cost": 3500.0}]}]})
        code, text, _ = run(["fill", "--symbol", "TCS", "--side", "sell", "--qty", "2", "--price", "3100",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0, text)
        self.assertIn("₹6,200.00", text)
        pf = self.read(self.pf)
        self.assertEqual(pf["cash"], 6200.0)
        self.assertEqual([l["id"] for l in pf["positions"][0]["lots"]], ["TCS-2"])
        self.assertEqual(self.read(self.state)["orders"][-1]["status"], "filled")
        for qty in ("5", "0.5"):
            code, _, err = run(["fill", "--symbol", "TCS", "--side", "sell", "--qty", qty, "--price", "3100",
                                "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)


class Bands(unittest.TestCase):
    def test_the_ticket_shows_the_band(self):
        doc = check.load_bands(BANDS)
        rel = ticket.to_kite(dict(REL_ORDER, product="MIS"))
        lines = ticket.band_lines(rel, doc, TODAY)
        self.assertEqual(lines, ["circuit band today: 1306.80 to 1597.20 (10% band, as of 2026-10-19, "
                                 "fixture in the trader-data bands shape)"])
        hi = ticket.to_kite(dict(REL_ORDER, limit_price="1600.00"))
        self.assertIn("the limit price 1600.00 is outside the band", " ".join(ticket.band_lines(hi, doc, TODAY)))
        unknown = ticket.band_lines(HC, None, TODAY)
        self.assertIn("circuit band unknown: run trader-data bands", unknown[0])
        self.assertIn("no price band", ticket.band_lines(HC, doc, TODAY)[0])
        self.assertIn("(stale (bands.json is as of 2026-10-19))", ticket.band_lines(rel, doc, "2026-10-30")[0])
        bel = ticket.to_kite({"symbol": "BEL", "side": "buy", "qty": "2", "product": "CNC", "type": "market",
                              "time_in_force": "day", "order_class": "bracket", "stop_loss_stop_price": "385",
                              "take_profit_limit_price": "425"})
        self.assertIn("stop may not fill if the stock hits lower circuit", " ".join(ticket.band_lines(bel, doc, TODAY)))

    def test_show_reads_the_bands_file(self):
        d = tempfile.mkdtemp()
        try:
            orders = os.path.join(d, "orders.json")
            shutil.copy(RELIANCE_FIX, orders)
            none = os.path.join(d, "none")
            base = ["show", "--orders", orders, "--config", BUDGET_CFG, "--risk", none, "--state", none,
                    "--now", "2026-10-19 09:45"]
            _, text, _ = run(base + ["--bands", BANDS])
            self.assertIn("circuit band today: 1306.80 to 1597.20", text)
            _, text, _ = run(base)
            self.assertIn("circuit band unknown: run trader-data bands", text)
            _, text, _ = run(base + ["--bands", BANDS, "--json", "--index", "1"])
            j = json.loads(text)
            self.assertEqual((j["band"]["lower"], j["band_state"], j["charges_label"]), (1306.8, "known", "estimate"))
        finally:
            shutil.rmtree(d)


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.charges = os.path.join(self.d, "charges.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_tradebook_parsing_and_estimates(self):
        trades, notes = ticket.read_tradebook(TRADEBOOK)
        self.assertEqual(len(trades), 7)
        self.assertIn("1 non-equity row skipped", notes[0])
        days = ticket.estimate_day_charges(trades, ticket.load_charges(""))
        self.assertEqual(sorted(days), ["2026-10-19", "2026-10-21"])
        d19, d21 = days["2026-10-19"], days["2026-10-21"]
        # 19th: RELIANCE bought and sold (intraday), HINDCOPPER bought and kept (delivery, no DP)
        self.assertEqual(round(d19["computed"], 2), 21.15)
        self.assertEqual(d19["parts"]["dp"], 0.0)
        # 21st: HINDCOPPER delivery sell in two fills of ONE order (one DP charge), INFY intraday
        self.assertEqual(d21["parts"]["dp"], 15.34)
        self.assertAlmostEqual(d21["parts"]["brokerage"], 4.5 + 4.47, places=2)   # CNC brokerage is 0
        self.assertEqual(round(d21["computed"], 2), 44.52)
        self.assertEqual(ticket.parse_day("21/10/2026"), "2026-10-21")
        self.assertEqual(ticket.parse_day("2026-10-21T10:14:55"), "2026-10-21")

    def test_actual_files(self):
        csv_days = ticket.read_actual(NOTES_CSV)
        self.assertEqual({k: round(v, 2) for k, v in csv_days.items()},
                         {"2026-10-19": 22.05, "2026-10-21": 46.10, "2026-10-22": 30.0})
        self.assertEqual(ticket.read_actual(NOTES_JSON), {"2026-10-19": 22.05, "2026-10-21": 46.10})
        rows = os.path.join(self.d, "rows.json")
        with open(rows, "w") as fh:
            json.dump([{"date": "2026-10-19", "brokerage": 3.51, "stt": 15, "exchange_txn": 0.75, "sebi": 0.02,
                        "stamp": 2, "gst": 0.77, "dp": 0}], fh)
        self.assertAlmostEqual(ticket.read_actual(rows)["2026-10-19"], 22.05)

    def test_reconcile_calibrates_and_every_skill_uses_it(self):
        code, text, err = run(["reconcile", TRADEBOOK, "--actual", NOTES_CSV, "--charges", self.charges,
                               "--now", "2026-10-29 18:00"])
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-19       3        ₹21.15        ₹22.05      +₹0.90  1.0424", text)
        self.assertIn("2026-10-21       4        ₹44.52        ₹46.10      +₹1.58  1.0355", text)
        self.assertIn("2026-10-22 has contract-note charges but no trades", text)
        self.assertIn("Calibration: factor 1.0390", text)
        with open(self.charges) as fh:
            cal = json.load(fh)["calibration"]
        self.assertEqual((cal["factor"], cal["samples"], cal["updated"]), (1.039, 2, "2026-10-29"))
        self.assertEqual(ticket.CHARGES_FILE, "")
        m = check.load_charges(self.charges)
        self.assertEqual(check.charges_label(m), "estimate (calibrated from 2 contract notes)")
        self.assertEqual(check.costs(4, 1452.30, 1432.30, "MIS", m)["total"], round(6.1228 * 1.039, 2))
        # the same notes again (as JSON) do not double count; a wild ratio is left out
        wild = os.path.join(self.d, "wild.json")
        with open(wild, "w") as fh:
            json.dump({"2026-10-19": 22.05, "2026-10-21": 400}, fh)
        code, text, _ = run(["reconcile", TRADEBOOK, "--actual", wild, "--charges", self.charges,
                             "--now", "2026-10-30 18:00"])
        self.assertIn("2026-10-21 left out of the calibration", text)
        with open(self.charges) as fh:
            cal = json.load(fh)["calibration"]
        self.assertEqual((cal["factor"], cal["samples"], cal["updated"]), (1.039, 2, "2026-10-30"))
        code, text, _ = run(["reconcile", "--show", "--charges", self.charges])
        self.assertEqual(code, 0)
        self.assertIn("calibration: factor 1.039 from 2 contract notes, updated 2026-10-30", text)
        self.assertIn("DP ₹15.34 a scrip a sell day", text)
        self.assertIn("exchange txn NSE 0.00307%", text)

    def test_overrides_in_the_charges_file_are_kept(self):
        with open(self.charges, "w") as fh:
            json.dump({"intraday": {"brokerage_cap": 10}}, fh)
        run(["reconcile", TRADEBOOK, "--actual", NOTES_JSON, "--charges", self.charges, "--now", "2026-10-29 18:00"])
        with open(self.charges) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["intraday"], {"brokerage_cap": 10})
        self.assertEqual(doc["calibration"]["samples"], 2)

    def test_without_notes_nothing_is_written_and_errors_are_one_line(self):
        code, text, _ = run(["reconcile", TRADEBOOK, "--charges", self.charges])
        self.assertEqual(code, 0)
        self.assertIn("nothing was calibrated", text)
        self.assertFalse(os.path.exists(self.charges))
        code, _, err = run(["reconcile", "--show", "--charges", self.charges])
        self.assertEqual(code, 0)
        for argv, want in ((["reconcile"], "wants a tradebook"),
                           (["reconcile", "/nonexistent.csv"], "no tradebook"),
                           (["reconcile", TRADEBOOK, "--actual", "/nonexistent.json"], "no contract-note charges")):
            code, _, err = run(argv + ["--charges", self.charges])
            self.assertEqual(code, 1)
            self.assertIn(want, err)
            self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
