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
        self.saved = (news.Fetcher.get, news.Fetcher.get_nse, news.Fetcher.nse_json)

        real = news.Fetcher.nse_json

        def no_net(self_, url, name, ticker):      # deals and meetings: never the real network
            if self_.offline:
                return real(self_, url, name, ticker)
            raise news.NewsError("NSE answered HTTP 401")
        news.Fetcher.nse_json = no_net

    def tearDown(self):
        news.Fetcher.get, news.Fetcher.get_nse, news.Fetcher.nse_json = self.saved
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


class FakeResponse(object):
    def __init__(self, body):
        self.body, self.headers = body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener(object):
    """Stands in for the cookie-primed opener: the home page answers, then the API answers
    with the scripted replies (bytes, or an HTTP status code to raise)."""

    def __init__(self, replies, log):
        self.replies, self.log = replies, log

    def open(self, req, timeout=None):
        url = req.full_url
        self.log.append(url)
        if url == news.NSE_HOME:
            return FakeResponse(b"<html>home</html>")
        reply = self.replies.pop(0)
        if isinstance(reply, int):
            raise news.urllib.error.HTTPError(url, reply, "refused", {}, None)
        return FakeResponse(reply)


class NseSession(unittest.TestCase):
    """The one NSE session: a bot wall gets one re-prime, then a clean NewsError."""

    def setUp(self):
        self.saved = news.urllib.request.build_opener
        self.log, self.scripts = [], []
        news.urllib.request.build_opener = lambda *a: FakeOpener(self.scripts.pop(0), self.log)

    def tearDown(self):
        news.urllib.request.build_opener = self.saved

    def test_403_then_a_fresh_session_works(self):
        self.scripts = [[403], [b'{"data": []}']]
        f = news.Fetcher(pause=0)
        self.assertEqual(f.nse_json("https://www.nseindia.com/api/x", "x.json", "RELIANCE"), b'{"data": []}')
        self.assertEqual(self.log.count(news.NSE_HOME), 2)     # primed twice, no more

    def test_html_block_page_twice_gives_up(self):
        self.scripts = [[b"<html>Access Denied</html>"], [b"<html>Access Denied</html>"]]
        with self.assertRaises(news.NewsError) as ctx:
            news.Fetcher(pause=0).nse_json("https://www.nseindia.com/api/x", "x.json", "RELIANCE")
        self.assertIn("keeps refusing", str(ctx.exception))
        self.assertEqual(self.log.count(news.NSE_HOME), 2)

    def test_other_errors_are_not_retried(self):
        self.scripts = [[503]]
        with self.assertRaises(news.NewsError) as ctx:
            news.Fetcher(pause=0).nse_json("https://www.nseindia.com/api/x", "x.json", "RELIANCE")
        self.assertIn("HTTP 503", str(ctx.exception))
        self.assertEqual(self.log.count(news.NSE_HOME), 1)

    def test_the_session_is_reused(self):
        self.scripts = [[b"[]", b"[]"]]
        f = news.Fetcher(pause=0)
        f.nse_json("https://www.nseindia.com/api/a", "a.json", "RELIANCE")
        f.nse_json("https://www.nseindia.com/api/b", "b.json", "RELIANCE")
        self.assertEqual(self.log.count(news.NSE_HOME), 1)


class Deals(unittest.TestCase):
    """Bulk, block and short-selling deals: hand-made JSON shaped as NSE's bulk-block-short-deals answer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.saved = (news.Fetcher.get, news.Fetcher.nse_json)
        daily = os.path.join("trader", "data", "daily")
        os.makedirs(daily)
        days = [datetime.date(2026, 9, 14) + datetime.timedelta(days=i) for i in range(25)]
        for name, vol in (("RELIANCE.adj.csv", 40000000), ("RELIANCE.csv", 10000000)):   # the adjusted file wins
            with open(os.path.join(daily, name), "w") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for d in days:
                    fh.write("%s,1,1,1,1,%d\n" % (d.isoformat(), vol))
        self.base = ["--offline", os.path.join(DIR, "deals"), "--state", "s.json", "--now", NOW, "--no-moves",
                     "--portfolio", "x", "--watchlist", "x", "--plan", "x"]

    def tearDown(self):
        news.Fetcher.get, news.Fetcher.nse_json = self.saved
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_parse_deals(self):
        rows = news.parse_deals(raw(os.path.join("deals", "deals-bulk_deals.json")), "Bulk deal")
        self.assertEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual((first["symbol"], first["client"], first["side"], first["qty"], first["price"]),
                         ("RELIANCE", "ALPHA GROWTH FUND", "BUY", 1000000, 1350.5))
        self.assertEqual(first["date"], datetime.date(2026, 10, 13))
        self.assertEqual(rows[3]["remarks"], "Promoter group")
        self.assertEqual(rows[0]["remarks"], "")                     # NSE's "-" is no remark
        short = news.parse_deals(raw(os.path.join("deals", "deals-short_selling.json")), "Short selling")
        self.assertEqual((short[0]["symbol"], short[0]["qty"], short[0]["client"], short[0]["side"]),
                         ("RELIANCE", 150000, None, None))
        self.assertEqual(news.parse_deals(b'{"data": []}', "Bulk deal"), [])
        self.assertEqual(news.parse_deals(b"[]", "Bulk deal"), [])
        with self.assertRaises(news.NewsError):
            news.parse_deals(b"<html>Access Denied</html>", "Bulk deal")

    def test_items_percent_of_average_volume_and_flag(self):
        code, out, err = run(["--tickers", "RELIANCE", "HINDCOPPER", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        items = json.loads(out)["items"]
        deals = [i for i in items if i["kind"] == "deal"]
        self.assertEqual(len(deals), 4)                              # HINDCOPPER's 25 Sep deal is outside 48 h
        self.assertTrue(all("deal" in i["tags"] for i in deals))     # short selling too
        times = [i["time"] for i in items]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(deals[0]["time"], "2026-10-13T10:00:00+00:00")   # 15:30 IST, the close
        by = {(i["deal"]["kind"], i["deal"]["qty"]): i for i in deals}
        big = by[("Bulk deal", 1000000)]
        self.assertEqual(big["deal"]["pct_of_avg_volume"], 2.5)
        self.assertTrue(big["deal"]["large"])
        self.assertIn("ALPHA GROWTH FUND bought 1,000,000 shares at ₹1,350.50 [2.50% of 20-day avg volume, LARGE]",
                      big["title"])
        small = by[("Bulk deal", 200000)]
        self.assertEqual(small["deal"]["pct_of_avg_volume"], 0.5)
        self.assertFalse(small["deal"]["large"])
        self.assertNotIn("LARGE", small["title"])
        self.assertIn("sold 200,000 shares", small["title"])
        self.assertEqual(by[("Block deal", 300000)]["deal"]["pct_of_avg_volume"], 0.75)
        self.assertIn("150,000 shares disclosed short", by[("Short selling", 150000)]["title"])
        self.assertNotIn("TCS", {i["ticker"] for i in items})        # not watched: filtered after the fetch
        self.assertNotIn("INFY", {i["ticker"] for i in items})

    def test_no_cache_no_percent(self):
        code, out, err = run(["--tickers", "HINDCOPPER", "--hours", "1000", "--json"] + self.base)
        deal = [i for i in json.loads(out)["items"] if i["kind"] == "deal"][0]
        self.assertIsNone(deal["deal"]["pct_of_avg_volume"])
        self.assertFalse(deal["deal"]["large"])
        self.assertNotIn("avg volume", deal["title"])
        self.assertIn("(Promoter group)", deal["title"])

    def test_net_buy_minus_sell_per_client(self):
        code, out, err = run(["--tickers", "RELIANCE", "HINDCOPPER", "--json", "--dry-run"] + self.base)
        flow = json.loads(out)["deal_flow"]
        self.assertEqual([(f["symbol"], f["client"], f["net"]) for f in flow],
                         [("RELIANCE", "ALPHA GROWTH FUND", 800000), ("HINDCOPPER", "GAMMA SECURITIES", 400000),
                          ("RELIANCE", "BETA INSURANCE CO", 300000)])
        self.assertEqual((flow[0]["bought"], flow[0]["sold"], flow[0]["pct_of_avg_volume"]), (1000000, 200000, 2.0))
        self.assertIsNone(flow[1]["pct_of_avg_volume"])
        code, out, err = run(["--tickers", "RELIANCE", "--dry-run"] + self.base)
        self.assertIn("ALPHA GROWTH FUND", out)
        self.assertIn("net +800,000 shares (bought 1,000,000, sold 200,000; 2.00% of 20-day avg volume)", out)
        self.assertNotIn("GAMMA", out)                               # HINDCOPPER is not in this run
        self.assertNotIn(chr(0x2014), out)

    def test_each_deal_shows_once(self):
        code, out, err = run(["--tickers", "RELIANCE"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("Block deal: BETA INSURANCE CO bought 300,000 shares", out)
        code, out, err = run(["--tickers", "RELIANCE"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertNotIn("Net deals by client", out)                 # the net view goes with new deals
        code, out, err = run(["--tickers", "RELIANCE", "--all"] + self.base)
        self.assertIn("Net deals by client", out)

    def test_empty_data_is_normal(self):
        base = list(self.base)
        base[1] = os.path.join(DIR, "deals-empty")
        code, out, err = run(["--tickers", "RELIANCE", "--json"] + base)
        data = json.loads(out)
        self.assertEqual((code, data["items"], data["deal_flow"]), (1, [], []))
        self.assertFalse([n for n in data["notes"] if "bulk/block" in n])

    def test_no_deals_flag(self):
        code, out, err = run(["--tickers", "RELIANCE", "--no-deals", "--json"] + self.base)
        self.assertEqual(json.loads(out)["items"], [])

    def test_one_fetch_per_type_for_the_date_range(self):
        calls = []

        def fake(self_, url, name, ticker):
            calls.append(url)
            return b'{"data": []}'
        news.Fetcher.nse_json = fake
        news.Fetcher.get = lambda self_, url, name: (_ for _ in ()).throw(news.NewsError("HTTP 403 for x"))
        run(["--tickers", "RELIANCE", "TCS", "INFY", "--state", "s.json", "--now", NOW, "--no-moves", "--no-google",
             "--portfolio", "x", "--watchlist", "x", "--plan", "x", "--deal-days", "10"])
        deals = [u for u in calls if "bulk-block-short-deals" in u]
        self.assertEqual(len(deals), 3)                              # three types, not three symbols
        self.assertEqual(["optionType=%s" % k for k in ("bulk_deals", "block_deals", "short_selling")],
                         [u.split("?")[1].split("&")[0] for u in deals])
        self.assertTrue(all(u.endswith("&from=04-10-2026&to=14-10-2026") for u in deals))

    def test_bot_wall_is_one_note_and_other_sources_stay(self):
        calls = []

        def walled(self_, url, name, ticker):
            calls.append(url)
            raise news.NewsError("NSE keeps refusing (a block page or HTTP 401/403 even after a fresh session)")
        news.Fetcher.nse_json = walled
        news.Fetcher.get = lambda self_, url, name: raw(name)
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--state", "s.json", "--now", NOW,
                              "--no-moves", "--portfolio", "x", "--watchlist", "x", "--plan", "x", "--json"])
        data = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(data["items"]), 5)                      # the headlines are all still there
        notes = [n for n in data["notes"] if "bulk/block" in n]
        self.assertEqual(len(notes), 1)
        self.assertIn("keeps refusing", notes[0])
        self.assertEqual(len([c for c in calls if "bulk-block-short-deals" in c]), 1)   # asked once, not three times
        self.assertEqual(len([n for n in data["notes"] if "board meetings" in n]), 1)
        self.assertEqual(len(calls), 2)                              # deals once, meetings once, then stop


class Calendar(unittest.TestCase):
    """Results and board-meeting dates: hand-made JSON shaped as NSE's corporate-board-meetings answer.
    NOW is Wednesday 2026-10-14 IST."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.saved = (news.Fetcher.get, news.Fetcher.nse_json)
        self.dir = os.path.join(DIR, "calendar")
        self.base = ["--offline", self.dir, "--state", "s.json", "--now", NOW, "--no-moves", "--no-deals",
                     "--portfolio", "x", "--watchlist", "x", "--plan", "x", "--dry-run"]

    def tearDown(self):
        news.Fetcher.get, news.Fetcher.nse_json = self.saved
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write_calendar(self, **doc):
        with open("calendar.json", "w") as fh:
            json.dump(doc, fh)
        return "calendar.json"

    def test_sessions_count_weekends_out(self):
        d = datetime.date
        self.assertEqual(news.sessions_between(d(2026, 10, 14), d(2026, 10, 14)), 0)
        self.assertEqual(news.sessions_between(d(2026, 10, 14), d(2026, 10, 16)), 2)
        self.assertEqual(news.sessions_between(d(2026, 10, 16), d(2026, 10, 19)), 1)    # Friday to Monday
        self.assertEqual(news.sessions_between(d(2026, 10, 16), d(2026, 10, 20)), 2)
        self.assertEqual(news.sessions_between(d(2026, 10, 17), d(2026, 10, 18)), 0)    # Saturday to Sunday
        self.assertEqual(news.sessions_between(d(2026, 10, 19), d(2026, 10, 16)), -1)

    def test_sessions_use_the_calendar_holidays_and_special_sessions(self):
        d = datetime.date
        path = self.write_calendar(years=[2026], holidays={"2026-10-15": "Test holiday"},
                                   special_sessions={"2026-10-17": "Special Saturday"})
        cal = news.load_calendar(path)
        self.assertEqual(news.sessions_between(d(2026, 10, 14), d(2026, 10, 16), cal), 1)   # Thursday is shut
        self.assertEqual(news.sessions_between(d(2026, 10, 16), d(2026, 10, 19), cal), 2)   # Saturday is open
        self.assertFalse(news.is_session(d(2026, 10, 15), cal))
        self.assertTrue(news.is_session(d(2026, 10, 17), cal))
        self.assertIsNone(news.load_calendar("none.json"))
        with open("bad.json", "w") as fh:
            fh.write("{not json")
        with self.assertRaises(news.NewsError):
            news.load_calendar("bad.json")

    def test_parse_meetings(self):
        rows = news.parse_meetings(raw(os.path.join("calendar", "meetings-HINDCOPPER.json")), "HINDCOPPER")
        self.assertEqual([(m["date"].isoformat(), m["results"], m["dividend"]) for m in rows],
                         [("2026-10-16", True, True), ("2026-11-20", False, False), ("2026-10-01", True, False)])
        tcs = news.parse_meetings(raw(os.path.join("calendar", "meetings-TCS.json")), "TCS")
        self.assertEqual((tcs[0]["results"], tcs[0]["dividend"]), (True, True))      # "Financial Results/Dividend"
        self.assertEqual(news.parse_meetings(b'{"data": []}', "INFY"), [])
        with self.assertRaises(news.NewsError):
            news.parse_meetings(b"<html>Access Denied</html>", "INFY")

    def test_results_soon_within_two_sessions(self):
        f = news.Fetcher(offline=self.dir)
        watch = {"HINDCOPPER": None, "RELIANCE": None, "TCS": None, "WIPRO": None, "INFY": None}
        now = news.parse_now(NOW)
        events = news.collect_calendar(f, watch, now)
        by = {e["ticker"]: e for e in events}
        self.assertEqual(sorted(by), ["HINDCOPPER", "RELIANCE", "TCS", "WIPRO"])     # INFY: empty list, nothing
        self.assertEqual((by["TCS"]["sessions_away"], by["TCS"]["soon"]), (0, True))
        self.assertEqual((by["HINDCOPPER"]["sessions_away"], by["HINDCOPPER"]["soon"]), (2, True))
        self.assertTrue(by["HINDCOPPER"]["results"] and by["HINDCOPPER"]["dividend"])   # both tags
        self.assertEqual((by["RELIANCE"]["sessions_away"], by["RELIANCE"]["soon"]), (3, False))   # Thu, Fri, Mon
        self.assertEqual((by["WIPRO"]["dividend"], by["WIPRO"]["results"], by["WIPRO"]["soon"]), (True, False, False))
        self.assertEqual([e["ticker"] for e in events], ["TCS", "HINDCOPPER", "RELIANCE", "WIPRO"])   # by date
        self.assertEqual(len([e for e in events if e["ticker"] == "HINDCOPPER"]), 1)   # past and fund-raising dropped

    def test_briefing_opens_with_the_warning(self):
        code, out, err = run(["--tickers", "HINDCOPPER", "RELIANCE", "TCS", "WIPRO", "INFY"] + self.base)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^RESULTS SOON  TCS\s+results and dividend board date 2026-10-14 \(Wed\), today: gap risk")
        self.assertRegex(lines[1], r"^RESULTS SOON  HINDCOPPER\s+results and dividend board date 2026-10-16 \(Fri\), "
                                   r"2 sessions away: gap risk, an overnight gap can jump past an intraday stop")
        self.assertRegex(lines[2], r"^coming up\s+RELIANCE\s+results 2026-10-19 \(Mon\), 3 sessions away$")
        self.assertRegex(lines[3], r"^coming up\s+WIPRO\s+dividend board date 2026-10-30 \(Fri\), 12 sessions away$")
        self.assertEqual(lines[4], "")
        self.assertNotIn("INFY", "\n".join(lines[:4]))
        self.assertNotIn(chr(0x2014), out)
        code, out, err = run(["--tickers", "RELIANCE", "TCS", "--json"] + self.base)
        data = json.loads(out)
        self.assertEqual(data["results_soon"], ["TCS"])
        self.assertEqual([e["date"] for e in data["calendar"]], ["2026-10-14", "2026-10-19"])

    def test_holidays_from_the_calendar_file_pull_results_closer(self):
        args = ["--tickers", "RELIANCE", "--json"] + self.base
        self.assertEqual(json.loads(run(args)[1])["results_soon"], [])               # weekends only: 3 sessions
        path = self.write_calendar(years=[2026], holidays={"2026-10-15": "Test holiday"})
        data = json.loads(run(args + ["--calendar", path])[1])
        self.assertEqual(data["results_soon"], ["RELIANCE"])                         # Thursday shut: 2 sessions
        self.assertEqual(data["calendar"][0]["sessions_away"], 2)
        self.assertEqual([n for n in data["notes"] if "holiday calendar" in n], [])

    def test_calendar_that_misses_the_year_says_so_and_counts_weekends(self):
        path = self.write_calendar(years=[2025], holidays={"2026-10-15": "Would be shut"})
        data = json.loads(run(["--tickers", "RELIANCE", "--json", "--calendar", path] + self.base)[1])
        self.assertEqual(data["calendar"][0]["sessions_away"], 3)
        self.assertEqual(len([n for n in data["notes"] if "does not cover 2026" in n]), 1)

    def test_the_morning_briefing_still_has_the_news(self):
        code, out, err = run(["--tickers", "HINDCOPPER", "--hours", "6", "--now", "2026-10-19T10:15:00+05:30",
                              "--offline", DIR, "--state", "s.json", "--no-moves", "--no-deals", "--dry-run",
                              "--portfolio", "x", "--watchlist", "x", "--plan", "x"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("RESULTS SOON", out)                        # no meetings file: nothing to warn about
        self.assertIn("2026-10-19 09:40 IST  HINDCOPPER", out)

    def test_bot_wall_is_one_note_and_the_news_stays(self):
        calls = []

        def walled(self_, url, name, ticker):
            calls.append(url)
            raise news.NewsError("NSE keeps refusing (a block page or HTTP 401/403 even after a fresh session)")
        def feeds(self_, url, name):
            if not os.path.isfile(os.path.join(DIR, name)):
                raise news.NewsError("HTTP 404 for %s" % url)
            return raw(name)
        news.Fetcher.nse_json = walled
        news.Fetcher.get = feeds
        code, out, err = run(["--tickers", "RELIANCE", "TCS", "--hours", "24", "--state", "s.json", "--now", NOW,
                              "--no-moves", "--no-deals", "--portfolio", "x", "--watchlist", "x", "--plan", "x"])
        self.assertEqual(code, 0, err)
        self.assertIn("Reliance Industries Q2 net profit rises 9%", out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("note: NSE board meetings")]), 1)
        self.assertIn("results dates not checked", out)
        self.assertNotIn("RESULTS SOON", out)
        self.assertEqual(len([c for c in calls if "corporate-board-meetings" in c]), 1)   # TCS was not tried


if __name__ == "__main__":
    unittest.main()
