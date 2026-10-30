# The NSE Day Trader skill

15 Claude skills for NSE trading and long-term investing: scan Nifty 500 charts, time entries and exits, analyse fundamentals, argue the bull and bear case, backtest years of data, manage your portfolio and see it all on one report before you risk a rupee.

An NSE (India) port and extension of Jake Schincariol's MIT-licensed
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
Plain Python 3.8+, standard library only, no API keys. Nothing in this pack
places an order on its own; paper mode (log only, never sent) is the default.

## Install

In Claude Code:

```
/plugin marketplace add Vatsa10/nse-trader-skills
/plugin install nse-trader-skill@nse-trader-skill
```

Or as plain skills (`/trader-invest` and so on) from a clone:

```bash
git clone https://github.com/Vatsa10/nse-trader-skills
cp -r nse-trader-skills/trader-* ~/.claude/skills/
```

## The fifteen

**Data**

| command | job | what it does |
| --- | --- | --- |
| `/trader-data` | Data Layer | Fetches and caches daily, intraday and circuit-band data with fallbacks (Yahoo, NSE API, NSE bhavcopy, Kite MCP, CSV import); `doctor` tells you which sources work from your network. Also: a self-updating NSE holiday calendar, delivery % and ticket size, exact split/bonus adjustment from NSE corporate actions, index P/E and India VIX, and the NIFTY option chain (PCR, OI walls, max pain). |

**Long-term investing**

| command | job | what it does |
| --- | --- | --- |
| `/trader-invest` | Long-term Analyst | Fundamentals from screener.in / NSE / Kite, a transparent 0 to 100 scorecard (valuation, quality, growth, stability, technicals), cyclical and PSU flags, a verdict, and a staggered CNC buy plan with tax notes. Technicals include the swing-structure cushion; hints for Nifty 50 P/E (slower buying when the market is rich) and institutional delivery days. |
| `/trader-backtest` | Backtester | Years of daily data: buy-and-hold, SIP (with XIRR), SMA cross, 52-week breakout, dip-buy, swing-structure trend and rebalance. Real Zerodha charges, LTCG/STCG tax, benchmark vs NIFTYBEES, walk-forward and overfitting warnings, HTML report. |
| `/trader-portfolio` | Portfolio Manager | Imports Kite holdings or Console tradebook, shows lots and LTCG dates, flags concentration (stock, sector, group, PSU, gold/silver, no index), tax-aware rebalancing, FY tax estimate, SIP planner. |

**Intraday and swing**

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Nifty 500 setups: opening-range breakouts (optional daily-trend filter), swing pullbacks and reversals, chart `patterns` (VCP, double bottom, flag, ascending triangle, inverse head and shoulders, rising-support retest) with level, stop and target, a `longterm` screen (200-DMA, 52w high/low, swing trend, relative strength vs Nifty, delivery spikes) and market `breadth`. |
| `/trader-news` | News Monitor | Yahoo and Google News India, falling back to NSE corporate announcements; bulk, block and short-sell deals; a "results soon" gap-risk flag from NSE board meetings. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar from 09:15 and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Stop and target (range, percent, ATR or swing structure), reward:risk after charges, real circuit band, and a CNC mode for swing and delivery holds. |
| `/trader-timemachine` | Time Machine | 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | PASS or BLOCK against your INR limits, charges and circuit bands. |
| `/trader-budget` | Budget Manager | Whole-share sizing to your max loss per trade. |
| `/trader-broker` | Order Executor | Zerodha Kite tickets, three approval gates, contract-note `reconcile`. |
| `/trader-alerts` | Price Alerts | Price, stop, 09:15 bell and 15:15 flat-by alarms. |

**Decide and review**

| command | job | what it does |
| --- | --- | --- |
| `/trader-thesis` | Thesis Panel | For one stock or trade: builds an evidence brief from everything the other skills recorded, has five isolated agents argue it from trading angles (bear analyst, risk manager, FII/DII flows, value-trap hunter, 3am stop-out...), then a critic scores the arguments, flags traps and gives a buy / wait / avoid (or take / skip) verdict with the fact that would change it. Never an order. |
| `/trader-report` | Report | One self-contained HTML dashboard of everything the skills wrote: market (Nifty P/E, VIX, breadth, FII/DII, option chain, pre-open gaps), sortable scan tables, per-stock charts with levels and stops, thesis, plan, portfolio and backtest. Claude can publish it as a private Artifact page you open and share. |

## Long-term workflow

```
python3 trader-data/data.py calendar
python3 trader-data/data.py daily IOC NIFTYBEES --years 5
python3 trader-data/data.py actions IOC && python3 trader-data/data.py adjust IOC
python3 trader-data/data.py index --days 30
python3 trader-scanner/scan.py longterm
python3 trader-invest/invest.py fetch IOC
python3 trader-invest/invest.py analyse IOC
python3 trader-backtest/backtest.py run --strategy sip --symbol IOC --param amount=5000 --tax
python3 trader-invest/invest.py plan IOC --amount 5000 --tranches 3
python3 trader-portfolio/portfolio.py import kite-holdings.json && python3 trader-portfolio/portfolio.py risk
```

Or just ask Claude: "should I buy IOC for the long term?" and it runs these.

## Intraday and swing workflow

```
python3 trader-data/data.py preopen                     # 09:08 onwards: gaps and the order imbalance
python3 trader-data/data.py flows                       # yesterday's FII/DII net (builds history daily)
python3 trader-data/data.py chain NIFTY                 # sentiment context: PCR, OI walls, VIX
python3 trader-scanner/scan.py daily --cache            # pre-market setups, with level and swing stop
python3 trader-scanner/scan.py patterns                 # chart patterns from the daily cache
python3 trader-news/news.py                             # deals and "results soon" first
python3 trader-scanner/scan.py orb --trend-filter       # after 09:35, only stocks in a daily uptrend
python3 trader-scanner/scan.py patterns --intraday      # after 10:15: double bottom and flag on 5-minute bars
python3 trader-entry/entry.py watchlist --setup level   # the moment a level breaks
python3 trader-exit/exit.py plan                        # then timemachine, budget, risk, broker
```

## Using it in Claude

Ask in plain words and Claude picks the skills: "should I buy RELIANCE for
the long term?", "scan for setups and build my watchlist", "any results
this week on my holdings?".

- **Decisions:** `/trader-thesis RELIANCE` gathers everything the other
  skills recorded into one brief, has five isolated agents argue it (bear
  analyst, risk manager, FII/DII flows, value-trap hunter, 3am stop-out...),
  and ends with a verdict and the one fact that would change it.
- **Answers come back as an interactive page:** with `"output": "html"` in
  `trader/config.json` (the default), every skill ends by rebuilding the
  sections it fills of `trader/report.html` (market, news, scans, pattern
  study, symbols, thesis, plan, portfolio, backtest) and Claude publishes it
  as an Artifact: a private claude.ai page with charts and sortable,
  filterable tables that you can open on any device and share from its Share
  menu. It is always the same page, so it stays one living dashboard, and the
  chat reply is just the headline and the link. `"output": "text"` keeps
  answers in the chat. `--public` leaves out holdings, proposed orders and
  risk verdicts; they go on a published page only after you say yes.
- **Other skills:** Claude Code loads every installed skill side by side.
  The general [adhd](https://github.com/UditAkhourii/adhd) brainstorming
  skill (MIT, whose method trader-thesis adapts) suits open strategy
  questions; trader-thesis is the concrete "this stock, this trade" version.
- **Windows:** the tools print the rupee sign and work on a plain Windows
  console or pipe, and try IPv4 first on networks that drop IPv6.

## How the old limitations are handled

**Live sources get blocked.** `trader-data` tries several sources in order
(Yahoo query1 and query2, NSE's API with a cookie-primed session, NSE's
bhavcopy archive) and caches everything under `trader/data/`. Every other
skill reads the cache when a live fetch fails and tells you how to refresh.
The most reliable path for a Zerodha user is Kite's official MCP server: Claude
calls `get_historical_data`, `get_quotes` or `get_holdings` and the skills
import the saved JSON. `data.py doctor` shows which sources work from your
network. The scanner ships a real Nifty 500 list snapshot for when NSE blocks
the download.

**Test data was synthetic.** No multi-year price history ships with this
repo, because free sources do not license their data for redistribution. Run
`data.py record SYMBOL` on your machine (for example RELIANCE, TCS, INFY,
NIFTYBEES) to save real daily and intraday data under `tests/fixtures/real/`;
the offline suite then runs its real-data checks on those files and skips
them when the folder is absent. Run `NSE_LIVE=1 python3 -m unittest tests.test_live`
to test every live source end to end.

**The holiday list was hard-coded.** `data.py calendar` reads NSE's
holiday master and its trading-department circulars (ad-hoc closures, special
and Muhurat sessions) into `trader/data/calendar.json`, which every skill
reads; the built-in tables remain the offline fallback.

**Charges and circuit limits were estimates.** Charges now use Zerodha's
published rates (checked 2026-10-29, including the ₹15.34 DP charge on
delivery sells) from one model you can override in `trader/charges.json`.
`ticket.py reconcile` compares the estimate with your Console tradebook and
contract-note charges and calibrates the model, so later figures say
"calibrated from N contract notes". Circuit bands come from NSE or Kite
`get_quotes` via `data.py bands`; risk blocks a limit price outside today's
band, and anything unknown is labelled unknown instead of guessed.

## Tests

```bash
python3 -m unittest discover -s tests            # offline; real-data checks run if you recorded any
NSE_LIVE=1 python3 -m unittest tests.test_live   # live sources, on your machine
```

## Fine print

Not financial advice. Scorecards and backtests describe the past and your
inputs; they do not predict prices. Tax figures are estimates; check with a CA.
Most intraday traders in India lose money (SEBI, FY23). Run `data.py calendar`
each year so the holiday list stays current. MIT licence, see LICENSE; original
work by Jake Schincariol. The calendar, delivery, corporate-action, index,
option-chain, swing-structure and chart-pattern features were written fresh
from plain-language notes on Benny Thadikaran's GPL-3.0 projects (eod2,
NseIndiaApi, SwingTrend, stock-pattern); no code from them is included.
