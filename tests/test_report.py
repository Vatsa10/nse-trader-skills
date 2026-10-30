import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import load, ROOT

rp = load("trader-report", "report")

VOID = {"meta", "link", "input", "br", "hr", "img", "col", "line"}


class Check(HTMLParser):
    """Collects tags, flags unbalanced non-void tags and any external resource the page would load (a script,
    style, image, frame or font). A plain <a href> to a news story is navigation, not a load, and is allowed only
    when it opens safely (rel=noopener)."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.stack, self.errors, self.external, self.scripts = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for k in ("src", "href"):
            if re.match(r"(?i)\s*(https?:)?//", d.get(k) or ""):
                if tag == "a" and "noopener" in (d.get("rel") or ""):
                    continue
                self.external.append((tag, d[k]))
        if tag == "script":
            self.scripts += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("unexpected </%s> (open: %s)" % (tag, self.stack[-3:]))
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rp.main(argv)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.trader = os.path.join(self.dir, "trader")
        self.data = os.path.join(self.trader, "data")
        self.out = os.path.join(self.trader, "report.html")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, *extra):
        code, out, err = run(["build", "--data", self.data, "--trader", self.trader, "--out", self.out] + list(extra))
        with open(self.out, encoding="utf-8") as fh:
            return code, fh.read()

    def assert_valid(self, page):
        self.assertTrue(page.startswith("<!doctype html>"))
        c = Check()
        c.feed(page)
        self.assertEqual(c.errors, [])
        self.assertEqual(c.stack, [])
        self.assertEqual(c.external, [])
        self.assertEqual(c.scripts, 1)                       # one inline script, no src
        self.assertNotRegex(page, r"(?i)<script[^>]*\bsrc=")
        self.assertNotRegex(page, r"(?i)<link[^>]*\bhref=[\"']?https?:")
        self.assertNotRegex(page, r"(?i)<img[^>]*\bsrc=[\"']?https?:")
        self.assertNotIn(chr(0x2014), page)

    def tree(self):
        idx = os.path.join(self.data, "index")
        header = "date,open,high,low,close,volume,pe,pb,div_yield\n"
        w(os.path.join(idx, "nifty-50.csv"), header +
          "2026-10-27,25000,25100,24900,25050.5,0,22.1,3.4,1.3\n"
          "2026-10-28,25050,25200,25000,25150.25,0,23.5,3.5,1.3\n"
          "2026-10-29,25150,25300,25100,25302.75,0,25.6,3.6,1.2\n")
        w(os.path.join(idx, "india-vix.csv"), header +
          "2026-10-28,13,14,12,13.4,0,,,\n2026-10-29,13,14,12,12.85,0,,,\n")
        w(os.path.join(self.data, "breadth.csv"),
          "date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc,net_adv_ratio\n"
          "2026-10-28,55.5,48.2,3,100,4.5,0.1\n2026-10-29,61.25,50.1,5,120,6.75,0.2\n")
        w(os.path.join(self.data, "flows.csv"),
          "date,category,buy_cr,sell_cr,net_cr\n"
          "2026-10-28,FII/FPI,9000,10000,-1000.5\n2026-10-28,DII,8000,7000,1000\n"
          "2026-10-29,FII/FPI,9000,8700,300\n2026-10-29,DII,8000,7500,500\n")
        w(os.path.join(self.data, "chain-NIFTY.json"), json.dumps(
            {"underlying": "NIFTY", "expiry": "03-Nov-2026", "pcr_oi": 1.17, "pcr_change_oi": 0.9, "max_pain": 25300,
             "atm": 25300, "max_call_oi_strike": 25500, "max_put_oi_strike": 25000}))
        w(os.path.join(self.data, "preopen.json"), json.dumps(
            {"key": "NIFTY 50", "timestamp": "29-Oct-2026 09:08:00", "date": "2026-10-29", "advances": 30, "declines": 20,
             "stocks": [{"symbol": "AAA", "iep": 110.0, "prev_close": 100.0, "change_pct": 10.0},
                        {"symbol": "BBB", "iep": 95.0, "prev_close": 100.0, "change_pct": -5.0}]}))
        w(os.path.join(self.trader, "watchlist.json"), json.dumps(
            {"mode": "daily", "as_of": "2026-10-29 close", "hits": [
                {"symbol": "M&M", "setups": ["52w_high"], "price": 3100.5, "level": 3090.0, "stop": 3000.0,
                 "swing_trend": "up", "coc": 2950.0, "dq": 1.8},
                {"symbol": "TCS", "setups": ["breakout"], "price": 4100.0, "swing_trend": "up", "dq": 0.9}]}))
        w(os.path.join(self.trader, "longterm.json"), json.dumps(
            {"mode": "longterm", "as_of": "2026-10-29 close", "hits": [
                {"symbol": "ITC", "screens": ["quality"], "price": 450.0, "swing_trend": "up", "swing_coc": 420.0,
                 "mansfield_rs": 3.2, "dq": 1.1}]}))
        rows = "".join("2026-09-%02d,100,%d,%d,%d,1000\n" % (d, 100 + d, 90 + d, 95 + d) for d in range(1, 29))
        w(os.path.join(self.data, "daily", "M&M.csv"), "date,open,high,low,close,volume\n" + rows)
        w(os.path.join(self.data, "delivery", "M&M.csv"), "date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series\n" +
          "".join("2026-09-%02d,1000,10,%d,50,100,EQ\n" % (d, 400 + d) for d in range(1, 11)))
        w(os.path.join(self.data, "actions.json"), json.dumps({"fetched_at": "2026-10-29", "symbols": {"M&M": {
            "from": "2023-01-01", "fetched_through": "2026-10-29", "actions": [
                {"ex_date": "2026-08-01", "kind": "split", "factor": 2.0, "dividend": None, "subject": "Split Rs 10 to Rs 5"}]}}}))
        w(os.path.join(self.trader, "thesis", "ITC.json"), json.dumps(
            {"symbol": "ITC", "verdict": "Hold and add on dips", "bull_case": ["Cash flows"], "bear_case": "Tobacco tax",
             "traps": ["Cheap for a reason"], "as_of": "2026-10-20"}))
        w(os.path.join(self.trader, "plan.json"), json.dumps(
            {"symbol": "RELIANCE", "side": "buy", "setup": "orb", "entry": 1452.3, "stop": 1432.3, "target": 1472.3,
             "risk_per_share": 20.0, "reward_risk": 1.0, "date": "2026-10-19"}))
        w(os.path.join(self.trader, "holdings.json"), json.dumps(
            {"as_of": "2026-10-29", "source": "kite", "cash": 5000, "holdings": [
                {"symbol": "INFY", "qty": 10, "avg_price": 1500.0, "last_price": 1600.0}]}))
        bt = os.path.join(self.trader, "backtests", "run-1")
        w(os.path.join(bt, "summary.json"), json.dumps(
            {"name": "run-1", "strategy": "buy_and_hold", "symbols": ["NIFTYBEES"], "fill": "open", "slippage_bps": 5,
             "tax": False, "generated": "2026-10-29T10:00:00+05:30",
             "metrics": {"start": "2020-01-01", "end": "2026-10-29", "cagr": 0.1234, "max_drawdown": -0.3, "sharpe": 0.8}}))
        w(os.path.join(bt, "report.html"), "<html></html>")
        w(os.path.join(self.trader, "news.json"), json.dumps(
            {"checked": "2026-10-30T09:00:00+00:00", "results_soon": ["RELIANCE"],
             "calendar": [{"ticker": "RELIANCE", "date": "2026-10-30", "purpose": "Financial Results",
                           "sessions_away": 0, "soon": True}],
             "moves": [{"ticker": "RELIANCE", "price": 1160.8, "pct": -0.57, "moving": False}],
             "deal_flow": [{"symbol": "RELIANCE", "client": "SOME FUND LLP", "bought": 2040212, "sold": 0, "net": 2040212,
                            "pct_of_avg_volume": 13.44}],
             "items": [{"ticker": "TCS", "source": "Google News", "title": "TCS jumps <b>", "link": "https://example.com/a",
                        "time": "2026-10-29T03:50:00+00:00", "tags": ["macro"]}], "flows": {}, "notes": []}))
        w(os.path.join(self.data, "pattern-study.json"), json.dumps(
            {"mode": "pattern-study", "symbols": 500, "split_date": "2025-08-08", "options": {"horizon": 40, "cost_pct": 0.25},
             "patterns": {"double_bottom": {"in": {"trades": 94, "win_pct": 54.3, "exp_r": -0.004, "se_r": 0.07, "pf": 1.0,
                                                   "control_r": -0.02, "lift_r": 0.02, "median_risk_pct": 16.4,
                                                   "exits": {"target": 43, "stop": 23, "time": 28}}}},
             "note": "A study of history, not a forecast."}))
        w(os.path.join(self.trader, "entry.json"), json.dumps(
            {"symbol": "RELIANCE", "setup": "orb", "status": "triggered", "time": "09:45", "price": 1452.3,
             "date": "2026-10-19"}))
        w(os.path.join(self.trader, "timemachine.json"), json.dumps(
            {"date": "2026-10-19", "verdict": "Coin flip: no edge after costs.",
             "mc": {"replays": 10000, "p_target": 0.41, "p_stop": 0.44, "p_neither": 0.15, "avg_R": -0.02}}))


class Build(Base):
    def test_full_tree(self):
        self.tree()
        code, page = self.build("--symbol", "M&M")
        self.assertEqual(code, 0)
        self.assert_valid(page)
        for k in ("market", "news", "scans", "study", "symbols", "thesis", "plan", "portfolio", "backtest"):
            self.assertIn('<section data-tab="%s"' % k, page)
        self.assertNotIn("Not available", page)
        self.assertIn("Results soon (gap risk):</b> RELIANCE", page)
        self.assertIn("TCS jumps &lt;b&gt;", page)                  # headlines escaped
        self.assertIn("SOME FUND LLP", page)
        self.assertIn('href="https://example.com/a"', page)
        self.assertIn("out-of-sample", page.replace("in-sample", ""))   # the study's split is explained
        self.assertIn("Entry signal (trader-entry)", page)
        self.assertIn("Coin flip: no edge after costs.", page)
        # numbers match the inputs
        self.assertIn("25302.75", page)
        self.assertIn("+0.61% on 2026-10-28", page)          # 25302.75 / 25150.25 - 1
        self.assertIn("25.6", page)
        self.assertIn("expensive (&gt;= 25)", page)
        self.assertIn("12.85", page)
        self.assertIn("1.17", page)
        self.assertIn("25500", page)
        self.assertIn("-1000.5", page)
        self.assertIn("AAA", page)
        self.assertLess(page.index("AAA"), page.index("BBB"))  # biggest gap first
        self.assertIn("3100.5", page)
        self.assertIn("Hold and add on dips", page)
        self.assertIn("Tobacco tax", page)
        self.assertIn("1452.3", page)
        self.assertIn("12.34%", page)
        self.assertIn('href="backtests/run-1/report.html"', page)
        self.assertIn("Split Rs 10 to Rs 5", page)
        # the scanner level, stop and coc become horizontal lines on the symbol chart
        self.assertIn("coc 2950", page)
        self.assertIn("stop 3000", page)
        # the portfolio total is quantity times last price
        self.assertIn("16000", page)
        self.assertIn("not financial advice", page.lower())

    def test_source_and_date_shown(self):
        self.tree()
        code, page = self.build()
        self.assertIn("trader/data/index/nifty-50.csv</code>, as of 2026-10-29", page)
        self.assertIn("trader/watchlist.json</code>, as of 2026-10-29 close", page)
        self.assertIn("trader/data/chain-NIFTY.json", page)
        self.assertIn("<h3>Sources</h3>", page)
        # symbols section is not available without --symbol
        self.assertIn("Not available", page)
        self.assertIn("--symbol RELIANCE", page)

    def test_empty_tree(self):
        code, page = self.build()
        self.assertEqual(code, 0)
        self.assert_valid(page)
        self.assertIn("Not available", page)
        for cmd in ("trader-scanner/scan.py daily", "trader-data/data.py index", "trader-portfolio/portfolio.py import",
                    "trader-backtest/backtest.py"):
            self.assertIn(cmd, page)
        self.assertNotIn("<section", page)

    def test_corrupt_json_is_skipped(self):
        self.tree()
        w(os.path.join(self.trader, "watchlist.json"), "{not json")
        w(os.path.join(self.data, "chain-NIFTY.json"), "")
        code, page = self.build()
        self.assertEqual(code, 0)
        self.assert_valid(page)
        self.assertIn("Skipped inputs", page)
        self.assertIn("trader/watchlist.json skipped", page)
        self.assertIn("chain-NIFTY.json skipped", page)
        self.assertIn("ITC", page)                          # the other scan file still shows

    def test_sections_and_public(self):
        self.tree()
        code, page = self.build("--sections", "market,portfolio")
        self.assertIn('<section data-tab="market"', page)
        self.assertNotIn('<section data-tab="scans"', page)
        self.assertIn('<section data-tab="portfolio"', page)
        w(os.path.join(self.trader, "orders.json"), json.dumps(
            {"status": "proposal", "orders": [{"symbol": "ORDERSYM", "side": "BUY", "qty": 7}]}))
        code, page = self.build("--public")
        self.assertNotIn("INFY", page)                        # holdings: money, left out
        self.assertNotIn("ORDERSYM", page)                    # proposed orders: left out
        self.assertIn("1452.3", page)                         # the trade plan itself is not private
        self.assertIn("Left out on purpose (--public): proposed orders and risk verdicts", page)
        self.assertIn("Left out on purpose", page)
        self.assertEqual(run(["build", "--sections", "nope", "--out", self.out])[0], 2)

    def test_html_escaping_of_symbol(self):
        self.tree()
        code, page = self.build("--symbol", "M&M")
        self.assertIn("M&amp;M", page)
        self.assertNotRegex(page, r"M&M(?!;)(?<!M&amp;M)")
        self.assertNotIn("<h3>M&M</h3>", page)
        self.assertIn("<h3>M&amp;M</h3>", page)
        w(os.path.join(self.trader, "thesis", "X.json"), json.dumps({"symbol": "<b>x</b>", "verdict": "a<script>b"}))
        code, page = self.build()
        self.assertNotIn("<b>x</b>", page)
        self.assertNotIn("a<script>b", page)
        self.assert_valid(page)


class Svg(unittest.TestCase):
    def test_points_for_five_values(self):
        pts = rp.scale_points([1.0, 2.0, 3.0, 4.0, 5.0], 1.0, 5.0, 0, 400, 0, 100)
        self.assertEqual(pts, "0.0,100.0 100.0,75.0 200.0,50.0 300.0,25.0 400.0,0.0")

    def test_none_is_left_out_and_x_follows_index(self):
        pts = rp.scale_points([1.0, None, 3.0], 1.0, 3.0, 0, 200, 0, 100)
        self.assertEqual(pts, "0.0,100.0 200.0,0.0")

    def test_line_chart_polyline(self):
        svg = rp.line_chart("T", "D", ["a", "b", "c", "d", "e"],
                            [{"name": "S", "values": [1.0, 2.0, 3.0, 4.0, 5.0], "cls": "s1"}], pad=0.0)
        want = rp.scale_points([1.0, 2.0, 3.0, 4.0, 5.0], 1.0, 5.0)
        self.assertIn('<polyline class="ln s1" fill="none" points="%s"/>' % want, svg)
        self.assertIn("viewBox", svg)
        self.assertIn("<title>T</title><desc>D</desc>", svg)
        self.assertIn("<details>", svg)                     # table fallback
        self.assertNotIn("http", svg)

    def test_no_css_rule_fills_a_line(self):
        # a browser showed every line chart as a filled shape: "svg .ln.s1{...fill:...}" (meant for bars) beat
        # the polyline's fill="none" attribute and the weaker "svg .ln{fill:none}"
        import re
        for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", rp.CSS):
            if ".ln" in rule[0] and "fill:" in rule[1]:
                self.assertIn("fill:none", rule[1].replace(" ", ""), rule[0])

    def test_thesis_items_read_as_sentences(self):
        li = rp.rich_item({"point": "P/E above the index", "evidence": "fundamentals.pe", "rupee_impact": "Rs 9,400"})
        self.assertTrue(li.startswith("P/E above the index<br>"))
        self.assertIn("evidence: fundamentals.pe; rupee impact: Rs 9,400", li)
        self.assertNotIn("{", li)
        self.assertEqual(rp.rich_item({"idea": "x", "reason": "y"}), 'x<br><span class="src">reason: y</span>')
        self.assertEqual(rp.rich_item("M&M"), "M&amp;M")

    def test_downsample_keeps_ends(self):
        xs = list(range(1000))
        out = rp.downsample(xs, 50)
        self.assertEqual(len(out), 50)
        self.assertEqual((out[0], out[-1]), (0, 999))
        self.assertEqual(rp.downsample([1, 2, 3], 50), [1, 2, 3])

    def test_bar_chart_has_one_rect_per_value(self):
        svg = rp.bar_chart("T", "D", ["a", "b"], [{"name": "S", "values": [2.0, -1.0], "cls": "s1"}])
        self.assertEqual(svg.count("<rect"), 2)


class Skill(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-report", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        low = " ".join(text.lower().split())
        self.assertIn("not financial advice", low)
        self.assertIn("yes", low)
        self.assertIn("name: trader-report\n", text)
        self.assertGreater(len(text.splitlines()), 60)


if __name__ == "__main__":
    unittest.main()
