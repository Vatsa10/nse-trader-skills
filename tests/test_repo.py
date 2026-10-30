import json
import os
import re
import unittest

from _load import ROOT

# v2, the day-trading pack: the eleven folders the reel's repo card shows
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-data",
          "trader-invest", "trader-backtest", "trader-portfolio", "trader-thesis", "trader-report"]
ABOUT = ("15 Claude skills for NSE trading and long-term investing: scan Nifty 500 charts, time entries and exits, "
         "analyse fundamentals, argue the bull and bear case, backtest years of data, manage your portfolio "
         "and see it all on one report before you risk a rupee.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-risk", "trader-broker", "trader-alerts",
              "trader-invest", "trader-portfolio", "trader-backtest", "trader-thesis", "trader-report"]
SWING_COPIES = [("trader-scanner", "scan.py"), ("trader-backtest", "backtest.py"),
                ("trader-invest", "invest.py"), ("trader-exit", "exit.py"),
                ("trader-timemachine", "timemachine.py")]
CALENDAR_COPIES = [("trader-alerts", "alerts.py"), ("trader-data", "data.py"), ("trader-scanner", "scan.py"),
                   ("trader-entry", "entry.py"), ("trader-exit", "exit.py"), ("trader-risk", "check.py"),
                   ("trader-timemachine", "timemachine.py"), ("trader-news", "news.py")]
ONLINE_COPIES = [("trader-alerts", "alerts.py"), ("trader-data", "data.py"), ("trader-entry", "entry.py"),
                 ("trader-exit", "exit.py"), ("trader-invest", "invest.py"), ("trader-news", "news.py"),
                 ("trader-scanner", "scan.py"), ("trader-timemachine", "timemachine.py")]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".claude")]
        if os.path.abspath(base) == os.path.join(ROOT, "reference"):
            dirs[:] = [d for d in dirs if d == "specs"]   # the rest is the studied GPL repos, gitignored
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        # .claude/ is a user's local Claude Code folder (gitignored), not part of the pack
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d != ".claude")
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "reference", "tests"] + SKILLS))

    def test_no_gpl_reference_code_is_tracked(self):
        # reference/ holds the GPL repos the clean-room specs were written from; only reference/specs ships
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignore = fh.read().split()
        self.assertIn("reference/*", ignore)
        self.assertIn("!reference/specs/", ignore)

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "nse-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "nse-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "nse-trader-skill")

    def test_readme_carries_the_reel_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The NSE Day Trader skill"))
        for line in (ABOUT,
                     "/plugin install nse-trader-skill@nse-trader-skill",
                     "## Fine print"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue  # the README points v1 users to the v1 commit
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)

    def test_swing_engine_is_the_same_everywhere(self):
        # each skill is one standalone script, so the engine is copied; the copies must not drift
        marks = ("# --- swing engine:", "# --- end swing engine ---")
        blocks = {}
        for s, f in SWING_COPIES:
            with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                text = fh.read()
            a, b = text.find(marks[0]), text.find(marks[1])
            self.assertTrue(0 <= a < b, "%s/%s has no swing engine block" % (s, f))
            blocks[f] = text[a:b]
        self.assertEqual(len(set(blocks.values())), 1, sorted(blocks))

    def test_nse_calendar_is_the_same_everywhere(self):
        # one calendar (built-in 2024 to 2026 tables, calendar.json on top), pasted into every script that needs it
        marks = ("# --- nse calendar:", "# --- end nse calendar ---")
        blocks = {}
        for s, f in CALENDAR_COPIES:
            with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                text = fh.read()
            a, b = text.find(marks[0]), text.find(marks[1])
            self.assertTrue(0 <= a < b, "%s/%s has no nse calendar block" % (s, f))
            blocks[f] = text[a:b]
        self.assertEqual(len(blocks), len(CALENDAR_COPIES))
        self.assertEqual(len(set(blocks.values())), 1, sorted(blocks))
        with open(os.path.join(ROOT, "reference", "specs", "nse_calendar.py"), encoding="utf-8") as fh:
            spec = fh.read()
        self.assertEqual(spec[spec.find(marks[0]):spec.find(marks[1])], next(iter(blocks.values())))

    def test_every_online_script_tries_ipv4_first(self):
        # networks that hand out IPv6 for NSE and Yahoo but drop it made each request wait out its timeout
        marks = ("# --- ipv4 first:", "# --- end ipv4 first ---")
        with open(os.path.join(ROOT, "reference", "specs", "ipv4_first.py"), encoding="utf-8") as fh:
            spec = fh.read()
        want = spec[spec.find(marks[0]):spec.find(marks[1])]
        for s, f in ONLINE_COPIES:
            with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text[text.find(marks[0]):text.find(marks[1])], want, "%s/%s" % (s, f))
        for s in SKILLS:   # and no other script goes online without it
            for f in os.listdir(os.path.join(ROOT, s)):
                if f.endswith(".py") and (s, f) not in ONLINE_COPIES:
                    with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                        self.assertNotIn("urllib.request", fh.read(), "%s/%s goes online: add the ipv4 block" % (s, f))

    def test_the_ipv4_block_orders_addresses(self):
        import socket
        v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2600::1", 443, 0, 0))
        v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))
        ns = {}
        saved = socket.getaddrinfo
        try:
            socket.getaddrinfo = lambda *a, **k: [v6, v4]
            with open(os.path.join(ROOT, "reference", "specs", "ipv4_first.py"), encoding="utf-8") as fh:
                exec(fh.read(), ns)
            self.assertEqual(socket.getaddrinfo("x", 443), [v4, v6])     # IPv4 first, IPv6 kept as a fallback
            patched = socket.getaddrinfo
            with open(os.path.join(ROOT, "reference", "specs", "ipv4_first.py"), encoding="utf-8") as fh:
                exec(fh.read(), {})
            self.assertIs(socket.getaddrinfo, patched)                   # loading twice does not wrap twice
        finally:
            socket.getaddrinfo = saved

    def test_every_skill_answers_with_the_interactive_page(self):
        # in Claude, results come back as the trader-report page (output: html), the same block in every skill
        marks = ("<!-- html output:", "<!-- end html output -->")
        with open(os.path.join(ROOT, "reference", "specs", "html_output.md"), encoding="utf-8") as fh:
            spec = fh.read()
        want = spec[spec.find(marks[0]):spec.find(marks[1])]
        self.assertTrue(want)
        for s in SKILLS:
            with open(os.path.join(ROOT, s, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text[text.find(marks[0]):text.find(marks[1])], want, s)
            self.assertIn(s, want, "%s has no row in the html output table" % s)
        # every section the table names is one the report builds
        import importlib.util
        spec_ = importlib.util.spec_from_file_location("report_for_repo", os.path.join(ROOT, "trader-report", "report.py"))
        rp = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(rp)
        for row in re.findall(r"(?m)^\| (trader-[^|]+)\|([^|]+)\|$", want):
            for word in re.findall(r"\b([a-z]+)\b", re.sub(r"\(.*?\)|`.*?`", "", row[1])):
                if word not in ("all", "of", "them"):
                    self.assertIn(word, rp.ALL_SECTIONS, row)
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            self.assertEqual(json.load(fh).get("output"), "html")

    def test_every_script_prints_the_rupee_sign_on_a_cp1252_pipe(self):
        marks = ("# --- utf8 output:", "# --- end utf8 output ---")
        with open(os.path.join(ROOT, "reference", "specs", "utf8_output.py"), encoding="utf-8") as fh:
            spec = fh.read()
        want = spec[spec.find(marks[0]):spec.find(marks[1])]
        for s in SKILLS:
            for f in os.listdir(os.path.join(ROOT, s)):
                if f.endswith(".py"):
                    with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                        text = fh.read()
                    self.assertEqual(text[text.find(marks[0]):text.find(marks[1])], want, "%s/%s" % (s, f))
        # a real run: Windows' default for a pipe, as when Claude Code runs the tool
        import shutil
        import subprocess
        import sys
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            daily = os.path.join(tmp, "trader", "data", "daily")
            os.makedirs(daily)
            with open(os.path.join(daily, "RUPEE.csv"), "w") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for i in range(260):
                    c = 100 + i * 0.1
                    fh.write("2025-%02d-%02d,%.2f,%.2f,%.2f,%.2f,500000\n" % (1 + i // 28, 1 + i % 28, c, c + 1, c - 1, c))
            env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONDONTWRITEBYTECODE="1")
            got = subprocess.run([sys.executable, os.path.join(ROOT, "trader-scanner", "scan.py"), "longterm",
                                  "--symbols", "RUPEE"], cwd=tmp, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=120)
            self.assertNotIn(b"UnicodeEncodeError", got.stderr, got.stderr.decode("utf-8", "replace"))
            self.assertIn("₹".encode("utf-8"), got.stdout)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
