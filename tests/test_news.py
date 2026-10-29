import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-10-14T00:00:00+05:30"
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE")
        self.assertEqual(len(items), 4)
        first = items[0]
        self.assertEqual(first["title"], "Reliance Industries Q2 net profit rises 9%, beats estimates")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 13, 14, 0, tzinfo=UTC))   # 19:30 IST
        self.assertEqual(first["source"], "Yahoo Finance")
        self.assertTrue(first["id"].startswith("rss:"))

    def test_google_news_rss_strips_the_outlet(self):
        items = news.parse_rss(raw("google-RELIANCE.xml"), "RELIANCE", "Google News")
        self.assertEqual([i["title"] for i in items],
                         ["Reliance Industries Q2 net profit rises 9%, beats estimates",
                          "Reliance shares: Jefferies raises price target after Q2"])
        self.assertEqual(items[0]["source"], "Google News")

    def test_urls_are_india_editions(self):
        self.assertEqual(news.YAHOO % news.yahoo_symbol("reliance"),
                         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN")
        self.assertEqual(news.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.GOOGLE % news.google_query("RELIANCE", "Reliance Industries Limited"),
                         "https://news.google.com/rss/search?q=Reliance+Industries+NSE&hl=en-IN&gl=IN&ceid=IN:en")
        self.assertEqual(news.google_query("HINDCOPPER"), "HINDCOPPER")
        with self.assertRaises(news.NewsError):
            news.norm_symbol("not a symbol")

    def test_times(self):
        self.assertEqual(news.parse_time("Tue, 13 Oct 2026 20:02:03 +0000").hour, 20)
        self.assertEqual(news.parse_time("2026-09-01T16:30:35+05:30").hour, 11)
        self.assertIsNone(news.parse_time("soon"))
        self.assertEqual(news.parse_now("2026-10-19T10:15"), datetime.datetime(2026, 10, 19, 4, 45, tzinfo=UTC))
        self.assertEqual(news.to_ist(datetime.datetime(2026, 1, 15, 3, 45, tzinfo=UTC)).strftime("%H:%M"), "09:15")
        self.assertEqual(news.to_ist(datetime.datetime(2026, 7, 15, 3, 45)).strftime("%H:%M"), "09:15")

    def test_no_sec_left(self):
        for name in ("SEC_FEED", "parse_sec_atom", "sec_user_agent", "cik_map", "ITEMS_8K"):
            self.assertFalse(hasattr(news, name), name)


class Tagging(unittest.TestCase):
    def test_touches_and_tags(self):
        items = news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE")
        watch = {"RELIANCE": "Reliance", "TCS": "Tata Consultancy Services", "INFY": "Infosys"}
        news.tag(items, watch)
        by = {i["title"][:20]: i for i in items}
        self.assertEqual(by["Reliance and Tata Co"]["touches"], ["RELIANCE", "TCS"])
        self.assertIn("earnings", by["Reliance Industries "]["tags"])
        jio = by["Reliance Jio IPO: SE"]
        self.assertIn("capital", jio["tags"])
        self.assertIn("regulatory", jio["tags"])

    def test_indian_market_tags(self):
        items = [{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "HINDCOPPER",
                  "title": "HINDCOPPER hits lower circuit after SEBI order; FII selling, RBI policy ahead",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"HINDCOPPER": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro"):
            self.assertIn(t, tags)

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE")
        b = news.parse_rss(raw("google-RELIANCE.xml"), "RELIANCE", "Google News")
        items = news.dedupe(news.tag(a + b, {"RELIANCE": None}))
        self.assertEqual(len(items), 5)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", none, "--watchlist", none, "--plan", none]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 5)          # 4 Yahoo + 2 Google, one story in both
        self.assertEqual(data["notes"], [])
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "24"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_ist_times_and_no_em_dash(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--no-google"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-13 19:30 IST  RELIANCE", out)
        self.assertIn("Checked 2026-10-14 00:00 IST", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)
        self.assertNotIn("Google News", out)

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "RELIANCE.NS", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "HINDCOPPER"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["HINDCOPPER", "RELIANCE"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "24"] + args)
        self.assertEqual(code, 0, err)
        self.assertEqual({t for i in json.loads(out)["items"] for t in i["touches"]}, {"RELIANCE"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)
        code, _, err = run(["--tickers", "bad symbol!"] + self.base)
        self.assertEqual(code, 2)

    def test_the_hindcopper_morning(self):
        code, out, err = run(["--tickers", "HINDCOPPER", "--hours", "6", "--no-moves",
                              "--now", "2026-10-19T10:15:00+05:30"] + self.base[:4])
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-19 09:40 IST  HINDCOPPER", out)
        self.assertNotIn("10:32 IST", out)                   # published after the pinned 10:15 clock
        lines = out.splitlines()
        q2 = [l for l in lines if "Q2 results preview" in l]
        self.assertIn("earnings", lines[lines.index(q2[0]) + 1])
        self.assertEqual(len([l for l in lines if l.startswith("  2026-10-1")]), 3)
        code, out, err = run(["--tickers", "HINDCOPPER", "--hours", "6", "--dry-run", "--all",
                              "--now", "2026-10-19T10:52:00+05:30"] + self.base[:4])
        self.assertRegex(out, r"HINDCOPPER\s+₹324\.85 \+3\.31% vs yesterday's close ₹314\.45 at 10:52 IST  MOVING")
        self.assertIn("upper circuit", out)

    def test_moves(self):
        m = news.parse_chart(raw("chart-HINDCOPPER.json"), "HINDCOPPER")
        self.assertEqual((m["prev_close"], m["pct"]), (314.45, 3.31))
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "10:52")
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet])[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0)[0])
        self.assertEqual(news.inr(1234567.5), "₹12,34,567.50")
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "24", "--dry-run"] + self.base)
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("note:", out)

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


class V4Fallbacks(unittest.TestCase):
    """v4: NSE announcements when the headline feeds are blocked; moves from the trader-data cache.
    nse-only/nse-HINDCOPPER.json is a SYNTHETIC answer in the shape of NSE's corporate-announcements API."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.saved = (news.Fetcher.get, news.Fetcher.get_nse)

    def tearDown(self):
        news.Fetcher.get, news.Fetcher.get_nse = self.saved
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_parse_nse(self):
        items = news.parse_nse(raw(os.path.join("nse-only", "nse-HINDCOPPER.json")), "HINDCOPPER")
        self.assertEqual(len(items), 2)
        it = items[0]
        self.assertEqual((it["source"], it["kind"], it["ticker"]), ("NSE", "announcement", "HINDCOPPER"))
        self.assertTrue(it["title"].startswith("Board Meeting Intimation: Hindustan Copper"))
        self.assertEqual(it["time"], datetime.datetime(2026, 10, 13, 13, 15, tzinfo=UTC))   # 18:45 IST
        self.assertIn("nsearchives.nseindia.com", it["link"])
        self.assertEqual(len(news.parse_nse(b'{"data": []}', "X")), 0)
        with self.assertRaises(news.NewsError):
            news.parse_nse(b"<html>Access Denied</html>", "X")

    def test_offline_feeds_missing_falls_back_to_nse(self):
        d = os.path.join(DIR, "nse-only")
        code, out, err = run(["--tickers", "HINDCOPPER", "--offline", d, "--state", "s.json", "--now", NOW,
                              "--portfolio", "x", "--watchlist", "x", "--plan", "x", "--json"])
        data = json.loads(out)
        self.assertEqual([i["source"] for i in data["items"]], ["NSE", "NSE"])
        self.assertIn("earnings", data["items"][0]["tags"])

    def test_live_all_blocked_prints_manual_links(self):
        def blocked(self_, url, name):
            raise news.NewsError("HTTP 403 for %s" % url)

        def nse_blocked(self_, ticker):
            raise news.NewsError("NSE answered HTTP 401")
        news.Fetcher.get, news.Fetcher.get_nse = blocked, nse_blocked
        code, out, err = run(["--tickers", "RELIANCE", "--state", "s.json", "--now", NOW,
                              "--portfolio", "x", "--watchlist", "x", "--plan", "x", "--json"])
        notes = json.loads(out)["notes"]
        joined = "\n".join(notes)
        self.assertIn("NSE announcements for RELIANCE: NSE answered HTTP 401", joined)
        self.assertIn("open by hand: https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE", joined)
        self.assertIn("No cache", joined)                     # the move: no live data and no cache
        self.assertIn("data.py doctor", joined)
        self.assertIn("import-kite", joined)

    def test_live_blocked_uses_nse_and_the_cache(self):
        def blocked(self_, url, name):
            raise news.NewsError("HTTP 403 for %s" % url)

        def nse_ok(self_, ticker):
            return raw(os.path.join("nse-only", "nse-HINDCOPPER.json"))
        news.Fetcher.get, news.Fetcher.get_nse = blocked, nse_ok
        os.makedirs(os.path.join("trader", "data", "intraday"))
        with open(os.path.join("trader", "data", "intraday", "HINDCOPPER-5m.json"), "wb") as fh:
            fh.write(raw("chart-HINDCOPPER.json"))
        code, out, err = run(["--tickers", "HINDCOPPER", "--state", "s.json", "--now", NOW,
                              "--portfolio", "x", "--watchlist", "x", "--plan", "x"])
        self.assertEqual(code, 0, err)
        self.assertIn("NSE", out)
        self.assertIn("Board Meeting Intimation", out)
        self.assertRegex(out, r"HINDCOPPER\s+₹")
        self.assertIn("using cached data from 2026-10-", out)
        self.assertIn("trader-data/data.py intraday HINDCOPPER", out)

    def test_daily_csv_move(self):
        os.makedirs(os.path.join("trader", "data", "daily"))
        with open(os.path.join("trader", "data", "daily", "TCS.csv"), "w") as fh:
            fh.write("date,open,high,low,close,volume\n2026-10-27,1,1,1,100,5\n2026-10-28,1,1,1,104,5\n")
        m = news.cached_move("TCS", "HTTP 403")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (104.0, 100.0, 4.0))
        self.assertIn("2026-10-28 daily close", m["note"])
        self.assertEqual(news.CHART_HOSTS, ("query1", "query2"))


if __name__ == "__main__":
    unittest.main()
