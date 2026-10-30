---
name: trader-timemachine
description: >-
  Time Machine. Replays every possible outcome of an NSE intraday trade before
  you risk a rupee: 10,000 simulated sessions plus every past time the setup fired.
  Takes the plan from trader-exit (entry, stop, target), rebuilds the rest of
  the day 10,000 times from the stock's own 5-minute bars over the last 60
  days, and re-runs the same rule on every past session (an opening-range
  breakout, or a level plan from a pattern scan, with a percent or swing stop). Prints the odds of
  target first, stop first, or neither by the 15:15 IST flat-by, where the price
  sits then, the average R before and after approximate Indian intraday costs, the history, and one honest verdict, then hands
  off to /trader-budget. Use when the user says "run the time machine",
  "replay this trade", "what are the odds", "simulate this trade", "monte
  carlo", "backtest this setup", "how often does this hit the target", "is
  this a coin flip", or after /trader-exit writes a plan, before
  /trader-budget.
argument-hint: "[path to plan.json, or TICKER]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session. It is a simulation built from the stock's own
recent bars, not a prediction. Day trading loses most people money, and a
good-looking replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:30      # replay to the close instead of 15:15
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-19 \
    --entry 364.18 --stop 358.32 --target 370.04 --entry-time "10:00 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

For a pattern or level plan, `--setup level --level PRICE` (and
`--stop-rule pct|swing`) give the same history; `--product CNC` prices a
delivery plan with delivery charges. Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--qty N` (shares for the cost estimate, default about ₹1,00,000 worth),
`--now "2026-10-19 10:00"` (IST, UTC+05:30) to pin the clock. Symbols are NSE
symbols; the tool asks Yahoo for `SYMBOL.NS`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range, and for a level setup its `level` (a CNC delivery plan also says `"product": "CNC"`) |
| Yahoo chart API | one request: 60 days of 5-minute bars for `SYMBOL.NS` (`range=60d&interval=5m`), free and unofficial, can lag. query1 first, then query2 |
| `trader/data/intraday/<SYMBOL>-5m.json` | the trader-data cache, same Yahoo shape: read with `--cache`, or when Yahoo fails, with a note of the data's time (`"source": "cache"`) |
| `trader/charges.json` | optional charges override and calibration factor (`--charges PATH`), see Costs below |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, approximate costs and `avg_R_after_costs`, the fan-chart bands, 160 sample paths and a 24-bin histogram of the close |

## When Yahoo fails

With `--cache`, or when query1 and query2 both fail, the run reads
`trader/data/intraday/<SYMBOL>-5m.json` (trader-data) and prints "using
cached data from <time> ... run `python3 trader-data/data.py intraday
SYMBOL` to refresh". The cache needs the plan's day and at least 20 full
sessions before it, like Yahoo's 60 days. With no cache either it stops
with the way out: run `python3 trader-data/data.py doctor`; or call the Kite
MCP `get_historical_data` for 60 days of 5-minute candles, save the raw
JSON and run `python3 trader-data/data.py import-kite FILE --kind historical --symbol SYMBOL`.

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: all 75
   five-minute bars, 09:15 to 15:30 IST. Gaps, special sessions (Muhurat)
   and today are left out. Every bar becomes four ratios to the bar before it:
   open, high, low and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default 3:15 PM, the
   flat-by before the broker's MIS auto square-off at about 15:20; at most
   3:30 PM, the close). NSE has no early closes. Green, which means target
   first or above the entry at `--until`.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at `--until` divided by the risk. A gap through the stop still
   counts as -1R here. A real fill can be worse, and a stock locked at its
   lower circuit may not fill at all.
   **Costs.** It also reports the average R after approximate Zerodha
   intraday charges, the shared charges model verified 2026-10-29 from
   zerodha.com/charges (brokerage min(₹20, 0.03%) an order, STT 0.025% on the
   sell, NSE exchange 0.00307%, SEBI ₹10 a crore, GST 18% on brokerage +
   exchange + SEBI, stamp 0.003% on the buy) for `--qty` shares, for the
   replays and for the history. `trader/charges.json` is deep-merged over
   those numbers and its `calibration.factor` scales the total; the report
   says "estimate", or "estimate (calibrated from N contract notes)".
4. **The history.** The plan's own rule runs again on every one of those past
   sessions. That is what really happened, not a simulation.
   - **Opening-range breakout:** the same opening range, stop rule and target R;
     the first close beyond the range is the entry.
   - **Level plan** (`setup: level`, a pattern hit's level): a price level from
     today cannot be reused on a session where the stock traded elsewhere, so
     the level is kept as a distance from the plan day's open. On each past
     session the entry is the first bar closing above the open times that
     distance, between 09:30 and 15:00 (trader-entry's default window). The
     stop is the plan's stop distance as a percent of the entry (the
     fallback), or the swing level below; the target is the plan's R.
   - **Swing stop** (`stop_rule: swing`, trader-exit `--stop swing`): on each
     past session the change-of-character level is worked out from that
     session's bars up to the entry bar, the way trader-exit does it. A session
     where trader-exit would have refused the stop (the swing trend not up, the
     level at or above the entry, or under 0.05% away) is skipped and counted
     (`skipped_no_stop`), and the report says so.
   - A level plan with no `level` in plan.json, a CNC plan (held overnight, so
     one intraday session is the wrong yardstick), or any other setup, skips
     the history and says why.
   - **Delivery plans** (`"product": "CNC"`): the replays still cover the plan's
     day only, but costs use the delivery charges (STT both sides, stamp, DP on
     the sell) and the report warns that a gap or a later day can change the
     outcome.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them. So a 2R plan with target first
   24.1 % and stop first 50.5 % is close to break-even (it needs 33 % of
   the 74.6 % that hit a side, and got 32 %), not a loser.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the
   average R when its sign goes against the lean. Those can only point the
   other way because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 full sessions, when the entry bar is
not in the data (wrong date, not closed yet, a future day), or when the plan
does not make sense (a stop above a buy's entry, an entry off the 5-minute
grid). A live in-progress bar is never used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. The default `--until` is 15:15 IST, the
   flat-by, so "neither" means still open at the time you would get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at `--until`, the average R and the average R after
   costs, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news and circuit limits can do things those 60 days never did. Check
   `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: the reel

RELIANCE (synthetic test bars), Monday 2026-10-19. The 15-minute opening
range is 354.47 to 362.18. The 09:55 bar closes above it: entry 364.18 at
10:00 AM IST (bar 8), stop 358.32 (the range midpoint, rounded down), target
370.04 (1R, risk ₹5.86 a share).

```
10,000 replays of the rest of the session, 10:00 AM to 3:15 PM (63 five-minute bars), seed 19.
  target first   26.9 %
  stop first     22.0 %
  neither        51.0 %   still open at 3:15 PM
  green          52.5 %   target first, or above the entry at 3:15 PM
  Price at 3:15 PM: median 364.41, 90 % of replays between 356.10 and 372.78.
  Average R: +0.051
  After intraday charges, estimate (274 shares: about ₹82.14 at the stop, ₹83.07 at the target): average R +0.000.
History: the same rule on each of those 57 real sessions fired 32 times:
13 target, 15 stop, 4 neither. Average R -0.076. After costs -0.172.
Leans toward the target: target first 26.9 % vs stop first 22.0 %.
51.0 % of the replays hit neither by 3:15 PM; the price at 3:15 PM decides those.
```

The replays lean slightly toward the target, but costs eat the whole edge,
and the real history was worse than even. That is the honest answer for this
setup, and it is what the user should hear before `/trader-budget` sizes it.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- NSE holidays are flagged (the built-in 2024 to 2026 lists plus `trader/data/calendar.json`;
  check nseindia.com for a year in neither).
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. Costs are approximate, labelled so, and never sold as exact.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`): tickets are logged only, never sent. Going live is the user's
  own decision, in their own config.
- Nothing in this pack places an order except trader-broker (Zerodha Kite MCP), and only when all three hold:
  trader-risk passed that exact order today, the user typed an explicit yes for that one order in the chat,
  and the user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, MIS leverage or short selling unless the config allows it and the user confirms.
- Kite login happens through Kite's own OAuth in the browser; no keys in the chat.

<!-- html output: keep byte-identical in every SKILL.md (tests/test_repo.py checks) -->
## Results in Claude: an interactive page, not a wall of text

`output` in `trader/config.json` decides how a result reaches the user: `"html"` (the default when the key
is missing) or `"text"`. In html mode, when Claude has an Artifact tool (Claude Code, claude.ai):

1. Run this skill's command as usual (with `--write` where it has one, so the result lands in a file).
2. Rebuild the page with the sections that skill fills, for the symbols in play:
   `python3 "${CLAUDE_SKILL_DIR}/../trader-report/report.py" build --public --sections SECTIONS [--symbol SYM ...]`
3. Publish `trader/report.html` as an Artifact. Always the same file, so the page keeps one URL and every
   skill updates the same living dashboard (first publish: icon `chart`).
4. Reply in chat in 2 to 4 lines: the headline (verdict, setup, PASS or BLOCK), any warning (stale data,
   a missing source, results soon), and the link. The detail lives on the page, not in the chat.

| skill | sections |
| --- | --- |
| trader-data | market, symbols |
| trader-scanner | scans, market (`patterns --study`: study) |
| trader-news | news, market |
| trader-invest, trader-thesis | symbols, thesis, market |
| trader-entry, trader-exit, trader-timemachine, trader-budget, trader-risk | plan, symbols |
| trader-backtest | backtest |
| trader-portfolio | portfolio (only with the user's yes, see below) |
| trader-alerts, trader-broker | plan (its orders only with the user's yes, see below) |
| trader-report | all of them |

`--public` leaves out holdings, proposed orders and risk verdicts. Publishing distributes the page, so those
go on it only after the user says yes to that exact page; otherwise keep `--public` or answer in chat.
Without an Artifact tool, or in text mode, answer in chat as the steps above describe and mention
`trader/report.html` for a browser. The page is a view of the files; the files stay the source of truth,
and nothing on it places an order.
<!-- end html output -->
