import csv
import datetime
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT, FIX

bt = load("trader-backtest", "backtest")

# SYNTHETIC daily bars (see SYNTHETIC.txt there): weekday dates 2019-2024, trend + sine + seeded noise.
SYN = os.path.join(FIX, "trader-backtest", "synthetic")
# REAL NSE daily bars (bhavcopy via eod2_data, split/bonus adjusted), added by the trader-data agent.
REAL = os.path.join(FIX, "real")
D = datetime.date
ZERO = {"delivery": {"stt_buy_pct": 0, "stt_sell_pct": 0, "stamp_buy_pct": 0, "dp_per_scrip_sell": 0},
        "exchange_txn_pct": {"NSE": 0}, "sebi_per_crore": 0}


def zero_charges():
    return bt.deep_merge(bt.CHARGES_DEFAULT, ZERO)


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("date,open,high,low,close,volume\n")
        for d, o, c in rows:
            fh.write("%s,%s,%s,%s,%s,1000\n" % (d.isoformat(), o, max(o, c), min(o, c), c))


def weekdays(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def load_syn(*syms):
    series = {}
    for s in syms:
        series[s], _ = bt.load_symbol(SYN, s)
    return bt.align(series)


def run_cli(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bt.main(args)
    return code, out.getvalue(), err.getvalue()


class Charges(unittest.TestCase):
    def test_delivery_buy_and_sell_by_hand(self):
        ch = bt.CHARGES_DEFAULT
        # buy 1,00,000: STT 100 + stamp 15 + txn 3.07 + SEBI 0.10 + GST 18 % of 3.17 (0.5706) = 118.7406
        b = bt.order_charges("buy", 100000, ch)
        self.assertAlmostEqual(b["stt"], 100.0, places=6)
        self.assertAlmostEqual(b["stamp"], 15.0, places=6)
        self.assertAlmostEqual(b["exchange"], 3.07, places=6)
        self.assertAlmostEqual(b["sebi"], 0.10, places=6)
        self.assertAlmostEqual(b["gst"], 0.5706, places=6)
        self.assertAlmostEqual(b["total"], 118.7406, places=6)
        self.assertEqual(b["brokerage"], 0.0)
        # sell 1,00,000 with DP: STT 100 + txn 3.07 + SEBI 0.10 + GST 0.5706 + DP 15.34 = 119.0806
        s = bt.order_charges("sell", 100000, ch, dp=True)
        self.assertAlmostEqual(s["total"], 119.0806, places=6)
        self.assertAlmostEqual(bt.order_charges("sell", 100000, ch, dp=False)["total"], 103.7406, places=6)

    def test_calibration_factor_and_label_and_override_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, "trader"))
        self.assertEqual(bt.charges_label(bt.load_charges(tmp)), "estimate")
        with open(os.path.join(tmp, "trader", "charges.json"), "w") as fh:
            json.dump({"calibration": {"factor": 1.1, "samples": 4}}, fh)
        ch = bt.load_charges(tmp)
        self.assertEqual(ch["delivery"]["dp_per_scrip_sell"], 15.34)  # deep merge keeps defaults
        self.assertAlmostEqual(bt.order_charges("buy", 100000, ch)["total"], 118.7406 * 1.1, places=6)
        self.assertEqual(bt.charges_label(ch), "estimate (calibrated from 4 contract notes)")

    def test_dp_charged_once_per_scrip_per_sell_day(self):
        dates = weekdays(D(2024, 1, 1), D(2024, 1, 5))
        data = {"AAA": {k: [100.0] * 5 for k in ("open", "high", "low", "close", "volume")}}
        p = bt.Portfolio(100000, data, dates, bt.CHARGES_DEFAULT)
        p.buy("AAA", 100, 0, "close")
        p.sell("AAA", 40, 2, "close")
        p.sell("AAA", 40, 2, "close")
        p.sell("AAA", 20, 3, "close")
        sells = [f for f in p.fills if f["side"] == "SELL"]
        no_dp = bt.order_charges("sell", 4000, bt.CHARGES_DEFAULT)["total"]
        self.assertAlmostEqual(sells[0]["charges"], round(no_dp + 15.34, 2), places=2)
        self.assertAlmostEqual(sells[1]["charges"], round(no_dp, 2), places=2)
        self.assertGreater(sells[2]["charges"], 15.34)

    def test_whole_shares_never_overspend(self):
        dates = weekdays(D(2024, 1, 1), D(2024, 1, 2))
        data = {"AAA": {k: [333.33, 333.33] for k in ("open", "high", "low", "close", "volume")}}
        p = bt.Portfolio(10000, data, dates, bt.CHARGES_DEFAULT)
        p.execute([{"op": "buy_all", "sym": "AAA"}], 0, "close")
        self.assertEqual(p.qty("AAA"), 29)  # 30 * 333.33 = 9999.90 leaves no room for charges
        self.assertGreaterEqual(p.cash, 0)


class Xirr(unittest.TestCase):
    def test_closed_form_ten_percent(self):
        # -1000 now, -1000 in 365 days, +2310 in 730 days: 1000*1.1^2 + 1000*1.1 = 2310, so exactly 10 %
        flows = [(D(2023, 1, 1), -1000), (D(2024, 1, 1), -1000), (D(2024, 12, 31), 2310)]
        self.assertAlmostEqual(bt.xirr(flows), 0.10, places=8)

    def test_bisection_fallback_and_no_sign_change(self):
        self.assertIsNone(bt.xirr([(D(2023, 1, 1), -1000), (D(2024, 1, 1), -10)]))
        # a near-total loss, where Newton from 10 % overshoots: -1000 -> +1 a year later is -99.9 %
        self.assertAlmostEqual(bt.xirr([(D(2023, 1, 1), -1000), (D(2024, 1, 1), 1)]), -0.999, places=6)
        # a huge gain: 1000 -> 50,000 in a year is +4900 %
        self.assertAlmostEqual(bt.xirr([(D(2023, 1, 1), -1000), (D(2024, 1, 1), 50000)]), 49.0, places=6)

    def test_sip_hand_computed(self):
        # Jan price 100, Feb 110, Mar 121 (open = close all month), SIP 1000 on the first trading day, no charges.
        # Jan 2: 10 sh (cash 0). Feb 1: +1000, 9 sh at 110 (cash 10). Mar 1: +1000 -> 1010, 8 sh at 121 (cash 42).
        # Mar 31: 27 sh * 121 + 42 = 3309. Flows -1000 Jan 2, -1000 Feb 1, -1000 Mar 1, +3309 Mar 29 (last bar).
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rows = []
        for d in weekdays(D(2024, 1, 2), D(2024, 3, 29)):
            p = {1: 100, 2: 110, 3: 121}[d.month]
            rows.append((d, p, p))
        write_csv(os.path.join(tmp, "SIPX.csv"), rows)
        dates, data = bt.align({"SIPX": bt.read_csv_bars(os.path.join(tmp, "SIPX.csv"))})
        res = bt.simulate("sip", {"amount": 1000}, ["SIPX"], data, dates, zero_charges(), 0, len(dates), 0.0)
        port = res["port"]
        self.assertEqual(port.qty("SIPX"), 27)
        self.assertAlmostEqual(port.cash, 42.0, places=6)
        self.assertEqual([q["qty"] for q in port.fills], [10, 9, 8])
        m = bt.metrics(res)
        self.assertAlmostEqual(m["final_equity"], 3309.0, places=6)
        self.assertEqual(m["invested"], 3000.0)
        flows = [(D(2024, 1, 2), -1000), (D(2024, 2, 1), -1000), (D(2024, 3, 1), -1000), (D(2024, 3, 29), 3309)]
        r = m["xirr"]
        npv = sum(a / (1 + r) ** ((d - flows[0][0]).days / 365.0) for d, a in flows)
        self.assertAlmostEqual(npv, 0.0, places=6)
        self.assertAlmostEqual(r, bt.xirr(flows), places=10)
        self.assertGreater(r, 0.5)  # 10 % a month is a big annual rate


class Drawdown(unittest.TestCase):
    def test_known_series(self):
        dd = bt.drawdown([100, 120, 90, 95, 130, 100])
        self.assertAlmostEqual(dd["max_drawdown"], -0.25)
        self.assertEqual((dd["peak"], dd["trough"], dd["recovery"]), (1, 2, 4))
        self.assertEqual(dd["longest_underwater_bars"], 3)
        self.assertFalse(dd["longest_unrecovered"])
        self.assertEqual([round(x, 4) for x in dd["series"]], [0, 0, -0.25, round(95 / 120.0 - 1, 4), 0, round(100 / 130.0 - 1, 4)])

    def test_unrecovered_and_dates(self):
        ds = [D(2024, 1, 1) + datetime.timedelta(days=k) for k in range(6)]
        dd = bt.drawdown([100, 110, 100, 90, 80, 85], ds)
        self.assertAlmostEqual(dd["max_drawdown"], 80 / 110.0 - 1)
        self.assertIsNone(dd["recovery"])
        self.assertTrue(dd["longest_unrecovered"])
        self.assertEqual(dd["longest_underwater_days"], 4)
        self.assertEqual(dd["peak_date"], "2024-01-02")


class Fills(unittest.TestCase):
    def setUp(self):
        self.dates, self.data = load_syn("SYNTREND")

    def test_next_open_fill_after_the_signal_close(self):
        res = bt.simulate("sma_cross", {"fast": 20, "slow": 100}, ["SYNTREND"], self.data, self.dates,
                          bt.CHARGES_DEFAULT, 0, len(self.dates), 100000, fill="open", slippage_bps=10)
        f, s = bt.sma(self.data["SYNTREND"]["close"], 20), bt.sma(self.data["SYNTREND"]["close"], 100)
        idx = {d.isoformat(): i for i, d in enumerate(self.dates)}
        fills = [x for x in res["port"].fills if x["side"] in ("BUY", "SELL")]
        self.assertGreater(len(fills), 2)
        for x in fills:
            j = idx[x["date"]]
            o = self.data["SYNTREND"]["open"][j]
            if x["side"] == "BUY":
                self.assertGreater(f[j - 1], s[j - 1])        # signal on the previous close
                self.assertAlmostEqual(x["price"], round(o * 1.001, 4), places=4)
            else:
                self.assertLess(f[j - 1], s[j - 1])
                self.assertAlmostEqual(x["price"], round(o * 0.999, 4), places=4)
            self.assertEqual(x["fill"], "open")

    def test_close_fill_uses_the_signal_day(self):
        res = bt.simulate("sma_cross", {"fast": 20, "slow": 100}, ["SYNTREND"], self.data, self.dates,
                          bt.CHARGES_DEFAULT, 0, len(self.dates), 100000, fill="close", slippage_bps=0)
        f, s = bt.sma(self.data["SYNTREND"]["close"], 20), bt.sma(self.data["SYNTREND"]["close"], 100)
        idx = {d.isoformat(): i for i, d in enumerate(self.dates)}
        b = [x for x in res["port"].fills if x["side"] == "BUY"][0]
        j = idx[b["date"]]
        self.assertGreater(f[j], s[j])
        self.assertTrue(s[j - 1] is None or f[j - 1] <= s[j - 1])  # first day above (or first day defined)
        self.assertAlmostEqual(b["price"], self.data["SYNTREND"]["close"][j], places=4)

    def test_warmup_before_from_and_no_lookahead(self):
        start, end = bt.window(self.dates, "2022-01-01", None)
        res = bt.simulate("sma_cross", {"fast": 50, "slow": 200}, ["SYNTREND"], self.data, self.dates,
                          bt.CHARGES_DEFAULT, start, end, 100000)
        self.assertEqual(res["rows"][0]["date"], D(2022, 1, 3))
        # truncating the future must not change any decision made before the cut
        cut = start + 300
        res2 = bt.simulate("sma_cross", {"fast": 50, "slow": 200}, ["SYNTREND"], self.data, self.dates,
                           bt.CHARGES_DEFAULT, start, cut, 100000)
        before = [x for x in res["port"].fills if x["date"] < self.dates[cut - 1].isoformat()]
        before2 = [x for x in res2["port"].fills if x["date"] < self.dates[cut - 1].isoformat()]
        self.assertEqual(before, before2)


class Tax(unittest.TestCase):
    def test_fifo_lots_and_terms(self):
        dates = [D(2022, 1, 3), D(2023, 3, 1), D(2023, 6, 1)]
        data = {"AAA": {"open": [100.0, 150.0, 200.0], "high": [100.0, 150.0, 200.0], "low": [100.0, 150.0, 200.0],
                        "close": [100.0, 150.0, 200.0], "volume": [1, 1, 1]}}
        p = bt.Portfolio(100000, data, dates, zero_charges())
        p.buy("AAA", 10, 0, "close")
        p.buy("AAA", 10, 1, "close")
        p.sell("AAA", 15, 2, "close")
        r = p.realized
        self.assertEqual([(x["qty"], x["buy_px"], x["term"]) for x in r], [(10, 100.0, "LT"), (5, 150.0, "ST")])
        self.assertAlmostEqual(r[0]["gain"], 1000.0)
        self.assertAlmostEqual(r[1]["gain"], 250.0)
        self.assertEqual(r[0]["days"], 514)
        self.assertEqual(p.lots["AAA"], [{"qty": 5, "px": 150.0, "date": D(2023, 3, 1), "chg_ps": 0.0}])

    def test_rates_exemption_and_set_off(self):
        t = bt.realized_tax(10000, 200000)
        self.assertAlmostEqual(t["tax"], 0.20 * 10000 + 0.125 * 75000)  # 11,375
        self.assertAlmostEqual(bt.realized_tax(0, 125000)["tax"], 0.0)
        self.assertAlmostEqual(bt.realized_tax(-50000, 200000)["tax"], 0.125 * 25000)  # ST loss offsets LT
        self.assertAlmostEqual(bt.realized_tax(40000, -90000)["tax"], 8000.0)          # LT loss cannot offset ST

    def test_tax_paid_at_fy_end_in_a_run(self):
        # buy Jan 2024 at 100, sell Feb 2024 at 200 (ST gain), FY24 ends Mar 31: tax paid on the first April bar
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rows = []
        for d in weekdays(D(2024, 1, 1), D(2024, 4, 30)):
            rows.append((d, 200 if d >= D(2024, 2, 1) else 100, 200 if d >= D(2024, 2, 1) else 100))
        write_csv(os.path.join(tmp, "TT.csv"), rows)
        dates, data = bt.align({"TT": bt.read_csv_bars(os.path.join(tmp, "TT.csv"))})

        class Manual(bt.Strategy):  # buy all on day 0, sell all on 2024-02-15
            def schedule(self, i, port):
                if i == self.start:
                    return [{"op": "buy_all", "sym": "TT"}]
                if self.dates[i] == D(2024, 2, 15):
                    return [{"op": "sell_all", "sym": "TT"}]
                return []
        bt.STRATEGIES["manual"] = Manual
        bt.STRATEGY_TYPES["manual"] = {"params": {}, "multi": False, "about": "test only"}
        self.addCleanup(bt.STRATEGIES.pop, "manual")
        self.addCleanup(bt.STRATEGY_TYPES.pop, "manual")
        res = bt.simulate("manual", {}, ["TT"], data, dates, zero_charges(), 0, len(dates), 10000, tax=True)
        tax_rows = [f for f in res["port"].fills if f["side"] == "TAX"]
        self.assertEqual(len(tax_rows), 1)
        self.assertEqual(tax_rows[0]["date"], "2024-04-01")  # first bar of FY25 settles FY24
        self.assertAlmostEqual(tax_rows[0]["value"], 2000.0)   # 20 % STCG on 100 sh * (200 - 100)
        self.assertEqual(res["taxes"][0]["fy"], "FY24")
        self.assertAlmostEqual(res["rows"][-1]["equity"], 18000.0)
        self.assertAlmostEqual(bt.metrics(res)["tax_paid"], 2000.0)
        self.assertEqual(bt.fy_label(bt.fy_of(D(2024, 2, 1))), "FY24")
        self.assertEqual(bt.fy_label(bt.fy_of(D(2024, 4, 1))), "FY25")

    def test_strategy_run_with_tax_settles_each_fy(self):
        dates, data = load_syn("SYNTREND")
        res = bt.simulate("sma_cross", {"fast": 20, "slow": 100}, ["SYNTREND"], data, dates, bt.CHARGES_DEFAULT,
                          0, len(dates), 100000, tax=True)
        self.assertTrue(res["taxes"])
        for t in res["taxes"]:
            self.assertGreaterEqual(t["tax"], 0)
            if not t["provisional"]:
                self.assertEqual(datetime.date.fromisoformat(t["paid_on"]).month, 4)
        notax = bt.simulate("sma_cross", {"fast": 20, "slow": 100}, ["SYNTREND"], data, dates, bt.CHARGES_DEFAULT,
                            0, len(dates), 100000, tax=False)
        self.assertLess(res["rows"][-1]["equity"], notax["rows"][-1]["equity"])


class Rebalance(unittest.TestCase):
    W = {"NIFTYBEES": 0.6, "GOLDBEES": 0.2, "ITBEES": 0.2}

    def setUp(self):
        self.dates, self.data = load_syn("NIFTYBEES", "GOLDBEES", "ITBEES")

    def weights_at(self, port, i, kind="open"):
        eq = port.equity(i, kind)
        return {s: port.qty(s) * self.data[s][kind][i] / eq for s in self.W}

    def test_initial_allocation_hits_the_weights(self):
        res = bt.simulate("rebalance", {"weights": self.W, "freq": "quarterly"}, sorted(self.W), self.data, self.dates,
                          bt.CHARGES_DEFAULT, 0, 1 + 1, 300000, slippage_bps=0)
        port = res["port"]
        p = bt.Portfolio(300000, self.data, self.dates, bt.CHARGES_DEFAULT)
        p.target(self.W, 0, "open")
        w = self.weights_at(p, 0)
        for s, tw in self.W.items():
            self.assertAlmostEqual(w[s], tw, delta=0.005)
            self.assertGreater(self.data[s]["open"][0], 0)
        self.assertLess(p.cash / 300000, 0.01)
        self.assertEqual(sorted(port.lots), sorted(self.W))

    def test_quarterly_rebalances_only_in_jan_apr_jul_oct(self):
        res = bt.simulate("rebalance", {"weights": self.W, "freq": "quarterly"}, sorted(self.W), self.data, self.dates,
                          bt.CHARGES_DEFAULT, 0, len(self.dates), 300000)
        months = set(datetime.date.fromisoformat(f["date"]).month for f in res["port"].fills if f["note"] == "quarterly rebalance")
        self.assertTrue(months)
        self.assertTrue(months <= {1, 4, 7, 10})
        # right after any rebalance the weights are back near target
        idx = {d.isoformat(): i for i, d in enumerate(self.dates)}
        last = [f for f in res["port"].fills if f["note"] == "quarterly rebalance"][-1]
        j = idx[last["date"]]
        port2 = bt.simulate("rebalance", {"weights": self.W, "freq": "quarterly"}, sorted(self.W), self.data,
                            self.dates, bt.CHARGES_DEFAULT, 0, j + 1, 300000)["port"]
        w = self.weights_at(port2, j)
        for s, tw in self.W.items():
            self.assertAlmostEqual(w[s], tw, delta=0.01)

    def test_threshold_band(self):
        res = bt.simulate("rebalance", {"weights": self.W, "freq": "threshold", "band": 5}, sorted(self.W), self.data,
                          self.dates, bt.CHARGES_DEFAULT, 0, len(self.dates), 300000)
        notes = [f["note"] for f in res["port"].fills if "drifted" in f["note"]]
        self.assertTrue(notes)
        wide = bt.simulate("rebalance", {"weights": self.W, "freq": "threshold", "band": 50}, sorted(self.W), self.data,
                           self.dates, bt.CHARGES_DEFAULT, 0, len(self.dates), 300000)
        self.assertFalse([f for f in wide["port"].fills if "drifted" in f["note"]])

    def test_bad_weights_refused(self):
        with self.assertRaises(bt.BTError):
            bt.simulate("rebalance", {"weights": {"NIFTYBEES": 0.8, "GOLDBEES": 0.4}}, ["GOLDBEES", "NIFTYBEES"],
                        self.data, self.dates, bt.CHARGES_DEFAULT, 0, 10, 300000)


class Strategies(unittest.TestCase):
    def setUp(self):
        self.dates, self.data = load_syn("SYNTREND")

    def sim(self, stype, params, **kw):
        return bt.simulate(stype, params, ["SYNTREND"], self.data, self.dates, bt.CHARGES_DEFAULT, 0,
                           len(self.dates), kw.pop("capital", 100000), **kw)

    def test_buy_hold_one_order_whole_shares(self):
        res = self.sim("buy_hold", {})
        f = res["port"].fills
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["date"], self.dates[0].isoformat())
        self.assertIsInstance(f[0]["qty"], int)
        self.assertLess(res["port"].cash, f[0]["price"] * 1.002)

    def test_sip_one_buy_per_month(self):
        res = self.sim("sip", {"amount": 5000}, capital=0)
        months = set((d.year, d.month) for d in self.dates)
        self.assertEqual(len(res["port"].flows), len(months))
        m = bt.metrics(res)
        self.assertEqual(m["invested"], 5000 * len(months))
        self.assertIsNotNone(m["xirr"])

    def test_breakout_and_dip_buy_trade_and_exit(self):
        b = self.sim("breakout_52w", {"lookback": 252, "exit_low": 50, "trail_pct": 15})
        buys = [f for f in b["port"].fills if f["side"] == "BUY"]
        self.assertTrue(buys)
        c = self.data["SYNTREND"]["close"]
        idx = {d.isoformat(): i for i, d in enumerate(self.dates)}
        for f in buys:
            j = idx[f["date"]] - 1
            self.assertGreater(c[j], max(c[j - 252:j]))
        d = bt.simulate("dip_buy", {"ma": 200, "dip_pct": 10, "exit_above_pct": None}, ["SYNTREND"], self.data,
                        self.dates, bt.CHARGES_DEFAULT, 0, len(self.dates), 100000)
        self.assertFalse([f for f in d["port"].fills if f["side"] == "SELL"])  # null exit = hold

    def test_sma_cross_rejects_fast_not_below_slow(self):
        with self.assertRaises(bt.BTError):
            self.sim("sma_cross", {"fast": 200, "slow": 50})

    def test_metrics_shape_and_benchmark(self):
        res = self.sim("sma_cross", {"fast": 50, "slow": 200})
        bars, inf = bt.load_symbol(SYN, "NIFTYBEES")
        m = bt.metrics(res, 0.065, {"symbol": "NIFTYBEES", "bars": bars, "adjusted": False})
        for k in ("cagr", "xirr", "total_return", "max_drawdown", "volatility", "sharpe", "sortino", "win_rate",
                  "avg_win", "exposure", "closed_trades", "charges_paid", "tax_paid", "longest_underwater_days"):
            self.assertIn(k, m)
        self.assertGreater(m["charges_paid"], 0)
        self.assertTrue(0 < m["exposure"] < 1)
        self.assertIsNotNone(m["benchmark"]["beta"])
        # lump sum with no flows: XIRR and CAGR agree
        self.assertAlmostEqual(m["xirr"], m["cagr"], places=6)
        # benchmark against itself: beta 1, alpha about 0
        same = bt.metrics(self.sim("buy_hold", {}), 0.065, {"symbol": "SYNTREND", "bars": bt.load_symbol(SYN, "SYNTREND")[0], "adjusted": False})
        self.assertAlmostEqual(same["benchmark"]["beta"], 1.0, delta=0.05)

    def test_sharpe_sortino_known_returns(self):
        rs = bt.risk_stats([0.01, -0.01] * 50, 0.0)
        self.assertAlmostEqual(rs["sharpe"], 0.0, places=9)
        self.assertAlmostEqual(rs["volatility"], bt.stdev([0.01, -0.01] * 50) * math.sqrt(252))


# Series A from the swing spec (high, low, close); bar 9 is ours, so the reversal's next open exists
SERIES_A = [(10, 8, 9), (11, 9, 10.5), (12, 10, 11.8), (11.5, 10.5, 11), (11.2, 10.2, 10.4), (12.6, 11, 12.4),
            (12.4, 11.6, 11.8), (11.9, 9.8, 10.0), (10.3, 9.9, 10.0)]
SERIES_A_OPEN = [9, 10, 11, 11.8, 11, 10.4, 12.4, 11.8, 10.0]


def swing_data(bars=SERIES_A, opens=SERIES_A_OPEN, sym="SWG"):
    dates = weekdays(D(2024, 1, 1), D(2024, 3, 29))[:len(bars)]
    d = {"open": list(opens), "high": [b[0] for b in bars], "low": [b[1] for b in bars],
         "close": [b[2] for b in bars], "volume": [1000.0] * len(bars)}
    return dates, {sym: d}


class SwingTrend(unittest.TestCase):
    def run_swing(self, params, dates=None, data=None, **kw):
        if dates is None:
            dates, data = swing_data()
        return bt.simulate("swing_trend", params, ["SWG"], data, dates, zero_charges(), 0, len(dates), 1000.0,
                           slippage_bps=0.0, **kw)

    def test_spec_series_one_buy_at_the_bos_next_open_and_one_sell(self):
        res = self.run_swing({"retrace_pct": 5, "entry": "bos", "warmup": 0})
        fills = [f for f in res["port"].fills if f["side"] in ("BUY", "SELL")]
        dates, _ = swing_data()
        self.assertEqual([f["side"] for f in fills], ["BUY", "SELL"])
        self.assertEqual(fills[0]["date"], dates[6].isoformat())      # signal on index 5, fill on the next open
        self.assertEqual(fills[0]["price"], 12.4)
        self.assertEqual(fills[1]["date"], dates[8].isoformat())      # reversal on index 7, fill on the next open
        self.assertEqual(fills[1]["price"], 10.0)
        self.assertIn("12.00", fills[0]["note"])
        self.assertIn("10.20", fills[1]["note"])
        self.assertEqual(len(res["port"].closed), 1)
        self.assertLess(res["port"].closed[0]["pnl"], 0)

    def test_warmup_holds_back_early_entries(self):
        res = self.run_swing({"retrace_pct": 5, "entry": "bos", "warmup": 6})   # the bos is bar 6: total 6 is not > 6
        self.assertEqual(res["port"].fills, [])
        res = self.run_swing({"retrace_pct": 5, "entry": "bos", "warmup": 5})
        self.assertEqual(len([f for f in res["port"].fills if f["side"] == "BUY"]), 1)

    def test_big_retrace_filter_ignores_the_bos(self):
        res = self.run_swing({"retrace_pct": 20, "entry": "bos", "warmup": 0})   # 15 % pullback < 20 %
        self.assertEqual(res["port"].fills, [])

    def test_reversal_entry_buys_the_trend_start(self):
        res = self.run_swing({"retrace_pct": 5, "entry": "reversal", "warmup": 0})
        buys = [f for f in res["port"].fills if f["side"] == "BUY"]
        dates, _ = swing_data()
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["date"], dates[2].isoformat())   # start_up on index 1, fill on index 2's open
        self.assertEqual(buys[0]["price"], 11.0)

    def test_bad_params_refused(self):
        for bad in ({"entry": "nope"}, {"retrace_pct": -1}, {"warmup": -3}):
            with self.assertRaises(bt.BTError):
                self.run_swing(dict({"retrace_pct": 5, "entry": "bos", "warmup": 0}, **bad))

    def test_registered_with_defaults_and_in_the_example(self):
        self.assertIs(bt.STRATEGIES["swing_trend"], bt.SwingTrend)
        self.assertEqual(bt.STRATEGY_TYPES["swing_trend"]["params"], {"retrace_pct": 5, "entry": "bos", "warmup": 40})
        with open(os.path.join(ROOT, "trader-backtest", "strategies.example.json")) as fh:
            named = json.load(fh)["strategies"]
        self.assertEqual(named["swing-structure"]["type"], "swing_trend")

    def test_no_lookahead(self):
        # cutting the series after the bos bar must not change the buy
        dates, data = swing_data()
        short = {"SWG": {k: v[:7] for k, v in data["SWG"].items()}}
        res = bt.simulate("swing_trend", {"retrace_pct": 5, "entry": "bos", "warmup": 0}, ["SWG"], short, dates[:7],
                          zero_charges(), 0, 7, 1000.0, slippage_bps=0.0)
        buys = [f for f in res["port"].fills if f["side"] == "BUY"]
        self.assertEqual([(f["date"], f["price"]) for f in buys], [(dates[6].isoformat(), 12.4)])

    def test_sweep_and_walk_forward_on_synthetic_bars(self):
        dates, data = load_syn("SYNTREND")
        cfg = {"strategy": "swing_trend", "symbols": ["SYNTREND"], "params": {"entry": "bos", "warmup": 40},
               "capital": 100000.0, "fill": "open", "slippage_bps": 5.0, "tax": False}
        sw = bt.sweep(cfg, data, dates, bt.CHARGES_DEFAULT, 0, len(dates),
                      bt.parse_sweep(["retrace_pct=0,3,5,8"]), "cagr", 0.7)
        self.assertEqual(sw["combinations"], 4)
        self.assertIn("best_out_of_sample", sw)
        self.assertEqual(sorted(t["params"]["retrace_pct"] for t in sw["table"]), [0, 3, 5, 8])

    def test_cli_run_with_split(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        daily = os.path.join(tmp, "trader", "data", "daily")
        os.makedirs(daily)
        shutil.copy(os.path.join(SYN, "SYNTREND.csv"), daily)
        code, text, err = run_cli(["run", "--name", "swing-structure", "--symbol", "SYNTREND", "--root", tmp,
                                   "--no-write", "--benchmark", "none", "--split", "0.7", "--json"])
        self.assertEqual(code, 0, err)
        s = json.loads(text)
        self.assertEqual(s["strategy"], "swing_trend")
        self.assertEqual(s["params"], {"retrace_pct": 5, "entry": "bos", "warmup": 40})
        self.assertIn("out_of_sample", s["split"])


class Data(unittest.TestCase):
    def test_adj_file_silences_the_guessed_gap_warnings(self):
        # a .adj.csv that still holds a 50 % overnight gap is trusted: no flags, no "likely split" text anywhere
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        daily = os.path.join(tmp, "trader", "data", "daily")
        os.makedirs(daily)
        rows = [(d, 100.0 if i < 30 else 50.0, 100.0 if i < 30 else 50.0)
                for i, d in enumerate(weekdays(D(2024, 1, 1), D(2024, 4, 30)))]
        write_csv(os.path.join(daily, "GAPCO.csv"), rows)
        write_csv(os.path.join(daily, "GAPCO.adj.csv"), rows)
        write_csv(os.path.join(daily, "RAWCO.csv"), rows)
        _, adj = bt.load_symbol(daily, "GAPCO", auto_adjust=True)
        self.assertEqual(adj["corporate_action_flags"], [])
        self.assertFalse(adj["auto_adjusted"])
        _, raw = bt.load_symbol(daily, "RAWCO")
        self.assertEqual(len(raw["corporate_action_flags"]), 1)   # the same bars without an adj file are flagged
        code, text, err = run_cli(["check", "GAPCO", "RAWCO", "--root", tmp])
        self.assertEqual(code, 0, err)
        self.assertEqual(text.count("likely corporate action"), 1)
        code, text, err = run_cli(["run", "--strategy", "buy_hold", "--symbol", "GAPCO", "--root", tmp,
                                   "--no-write", "--benchmark", "none"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Likely split", text)
        self.assertIn("gap check is off", text)

    def test_split_detection_and_auto_adjust(self):
        bars, inf = bt.load_symbol(SYN, "SPLITCO")
        self.assertFalse(inf["adjusted"])
        self.assertEqual([f["date"] for f in inf["corporate_action_flags"]], ["2022-06-15"])
        self.assertEqual(inf["corporate_action_flags"][0]["expected"], 0.5)
        bars2, inf2 = bt.load_symbol(SYN, "SPLITCO", auto_adjust=True)
        self.assertTrue(inf2["auto_adjusted"])
        self.assertEqual(bt.detect_corporate_actions(bars2), [])
        self.assertEqual(bt.detect_corporate_actions(bt.load_symbol(SYN, "SYNTREND")[0]), [])

    def test_adj_file_preferred(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copy(os.path.join(SYN, "SPLITCO.csv"), os.path.join(tmp, "SPLITCO.csv"))
        bars = bt.apply_adjustments(bt.read_csv_bars(os.path.join(tmp, "SPLITCO.csv")),
                                    bt.detect_corporate_actions(bt.read_csv_bars(os.path.join(tmp, "SPLITCO.csv"))))
        write_csv(os.path.join(tmp, "SPLITCO.adj.csv"), [(b["date"], b["open"], b["close"]) for b in bars])
        _, inf = bt.load_symbol(tmp, "SPLITCO")
        self.assertTrue(inf["adjusted"])
        self.assertEqual(inf["corporate_action_flags"], [])

    def test_missing_symbol_points_at_trader_data(self):
        with self.assertRaises(bt.BTError) as cm:
            bt.load_symbol(SYN, "NOPE")
        self.assertIn("trader-data", str(cm.exception))


class Sweep(unittest.TestCase):
    def test_overfit_warning_rule(self):
        self.assertIsNotNone(bt.overfit_warning(1.5, 0.4, 6, "sharpe"))
        self.assertIsNone(bt.overfit_warning(0.5, 0.4, 6, "sharpe"))
        self.assertIsNone(bt.overfit_warning(1.5, 0.4, 2, "sharpe"))
        self.assertIsNotNone(bt.overfit_warning(0.30, 0.10, 9, "cagr"))
        self.assertIsNone(bt.overfit_warning(0.12, 0.10, 9, "cagr"))

    def test_sweep_with_split(self):
        dates, data = load_syn("SYNTREND")
        cfg = {"strategy": "sma_cross", "symbols": ["SYNTREND"], "params": {}, "capital": 100000.0, "fill": "open",
               "slippage_bps": 5.0, "tax": False}
        sw = bt.sweep(cfg, data, dates, bt.CHARGES_DEFAULT, 0, len(dates),
                      bt.parse_sweep(["fast=10,20,50,150", "slow=100,200"]), "sharpe", 0.7)
        self.assertEqual(sw["combinations"], 7)       # fast=150 slow=100 is invalid
        self.assertEqual(sw["skipped_invalid"], 1)
        self.assertEqual(sw["table"][0], sw["best"])
        self.assertIn("best_out_of_sample", sw)
        self.assertEqual(sw["split_date"], dates[int(round(len(dates) * 0.7))].isoformat())
        self.assertTrue(any("combinations were tried" in w for w in sw["warnings"]))


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        daily = os.path.join(self.tmp, "trader", "data", "daily")
        os.makedirs(daily)
        for f in os.listdir(SYN):
            if f.endswith(".csv"):
                shutil.copy(os.path.join(SYN, f), daily)

    def test_run_writes_the_four_files(self):
        out = os.path.join(self.tmp, "out")
        code, text, err = run_cli(["run", "--strategy", "sma_cross", "--symbol", "SYNTREND", "--root", self.tmp,
                                   "--out", out, "--tax", "--split", "0.7"])
        self.assertEqual(code, 0, err)
        for f in ("summary.json", "trades.csv", "equity.csv", "report.html"):
            self.assertTrue(os.path.isfile(os.path.join(out, f)), f)
        with open(os.path.join(out, "summary.json")) as fh:
            s = json.load(fh)
        self.assertEqual(s["benchmark"]["symbol"], "NIFTYBEES")  # cached, so used by default
        self.assertIn("out_of_sample", s["split"])
        self.assertTrue(any("Survivorship" in c for c in s["caveats"]))
        self.assertTrue(any("Past performance" in c for c in s["caveats"]))
        with open(os.path.join(out, "report.html"), encoding="utf-8") as fh:
            h = fh.read()
        self.assertIn("<svg", h)
        self.assertNotIn("<script", h)
        self.assertNotIn("http://", h.replace("http://www.w3.org/2000/svg", ""))
        with open(os.path.join(out, "equity.csv")) as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["date"], "2019-01-01")
        self.assertIn("Walk-forward split", text)
        self.assertIn("Caveats:", text)

    def test_default_output_folder_and_named_config(self):
        code, text, err = run_cli(["run", "--name", "three-etf-quarterly", "--root", self.tmp, "--benchmark", "none"])
        self.assertEqual(code, 0, err)
        runs = os.listdir(os.path.join(self.tmp, "trader", "backtests"))
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].startswith("three-etf-quarterly-"))

    def test_sip_json_and_errors(self):
        code, text, err = run_cli(["run", "--strategy", "sip", "--symbol", "NIFTYBEES", "--param", "amount=5000",
                                   "--root", self.tmp, "--no-write", "--json"])
        self.assertEqual(code, 0, err)
        s = json.loads(text)
        self.assertEqual(s["metrics"]["initial_capital"], 0.0)
        self.assertIsNotNone(s["metrics"]["xirr"])
        code, _, err = run_cli(["run", "--strategy", "sma_cross", "--symbol", "NOPE", "--root", self.tmp, "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("trader-data", err)
        code, text, err = run_cli(["check", "SPLITCO", "--root", self.tmp])
        self.assertIn("2022-06-15", text)

    def test_sweep_cli(self):
        code, text, err = run_cli(["run", "--strategy", "sma_cross", "--symbol", "SYNTREND", "--root", self.tmp,
                                   "--no-write", "--sweep", "fast=10,20,50", "slow=100,200"])
        self.assertEqual(code, 0, err)
        self.assertIn("Sweep: 6 combinations", text)


class Files(unittest.TestCase):
    def test_no_em_dashes_and_frontmatter(self):
        folder = os.path.join(ROOT, "trader-backtest")
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    self.assertNotIn("\u2014", fh.read(), f)
        with open(os.path.join(folder, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-backtest\n"))
        fm = text.split("\n---\n", 1)[0]
        self.assertGreater(len(fm), 200)
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("not financial advice", text)
        self.assertIn("trader-data", text)
        with open(os.path.join(folder, "strategies.example.json")) as fh:
            named = json.load(fh)["strategies"]
        for v in named.values():
            self.assertIn(v["type"], bt.STRATEGIES)


REAL_SYMS = sorted(d for d in os.listdir(REAL) if os.path.isfile(os.path.join(REAL, d, "daily.csv"))) \
    if os.path.isdir(REAL) else []


@unittest.skipUnless(REAL_SYMS, "no real NSE fixtures in tests/fixtures/real")
class RealNSE(unittest.TestCase):
    """Real NSE daily bars (see each provenance.json). They are split/bonus adjusted per the source, so they are
    staged as <SYM>.adj.csv."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for s in REAL_SYMS:
            shutil.copy(os.path.join(REAL, s, "daily.csv"), os.path.join(cls.tmp, s + ".adj.csv"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_reads_every_file_and_no_false_split_flags(self):
        for s in REAL_SYMS:
            bars = bt.read_csv_bars(os.path.join(self.tmp, s + ".adj.csv"))
            self.assertGreater(len(bars), 200, s)
            self.assertTrue(all(b["low"] <= b["close"] <= b["high"] for b in bars), s)
            self.assertEqual(bt.detect_corporate_actions(bars), [], s)

    def test_planted_bonus_on_real_bars_is_caught(self):
        s = REAL_SYMS[0]
        bars = bt.read_csv_bars(os.path.join(self.tmp, s + ".adj.csv"))
        k = len(bars) // 2
        raw = [dict(b) for b in bars]
        for b in raw[k:]:
            for f in ("open", "high", "low", "close"):
                b[f] /= 2.0
        flags = bt.detect_corporate_actions(raw)
        self.assertEqual([f["date"] for f in flags], [bars[k]["date"].isoformat()])
        fixed = bt.apply_adjustments(raw, flags)
        self.assertAlmostEqual(fixed[0]["close"], bars[0]["close"] / 2.0, places=6)

    def test_strategies_run_on_real_data(self):
        for s in REAL_SYMS:
            series = {s: bt.load_symbol(self.tmp, s)[0]}
            dates, data = bt.align(series)
            for stype, params, cap in (("buy_hold", {}, 100000), ("sip", {"amount": 10000}, 0),
                                       ("sma_cross", {"fast": 20, "slow": 50}, 100000)):
                res = bt.simulate(stype, params, [s], data, dates, bt.CHARGES_DEFAULT, 0, len(dates), cap, tax=True)
                m = bt.metrics(res)
                self.assertGreater(m["final_equity"], 0, (s, stype))
                self.assertGreaterEqual(m["max_drawdown"], -1.0)
                self.assertLessEqual(m["max_drawdown"], 0.0)
                if stype == "buy_hold":
                    c = data[s]["close"]
                    # whole-share buy and hold tracks the price, less a little for charges and leftover cash
                    self.assertAlmostEqual(m["final_equity"] / 100000, c[-1] / data[s]["open"][0], delta=0.03 * c[-1] / c[0] + 0.02)

    def test_real_rebalance_with_niftybees(self):
        if "NIFTYBEES" not in REAL_SYMS or len(REAL_SYMS) < 3:
            self.skipTest("needs NIFTYBEES and two more symbols")
        others = [s for s in REAL_SYMS if s != "NIFTYBEES"][:2]
        w = {"NIFTYBEES": 0.5, others[0]: 0.25, others[1]: 0.25}
        series = dict((s, bt.load_symbol(self.tmp, s)[0]) for s in w)
        dates, data = bt.align(series)
        res = bt.simulate("rebalance", {"weights": w, "freq": "quarterly"}, sorted(w), data, dates,
                          bt.CHARGES_DEFAULT, 0, len(dates), 500000)
        port = res["port"]
        eq = port.equity(len(dates) - 1)
        self.assertGreater(eq, 0)
        self.assertEqual(sorted(port.lots), sorted(w))


if __name__ == "__main__":
    unittest.main()
