# The NSE Day Trader skill

13 Claude skills for NSE trading and long-term investing: scan Nifty 500 charts, time entries and exits, analyse fundamentals, backtest years of data and manage your portfolio before you risk a rupee.

An NSE (India) port and extension of Jake Schincariol's MIT-licensed
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
Plain Python 3.8+, standard library only, no API keys. Nothing in this pack
places an order on its own; paper mode (log only, never sent) is the default.

## Install

In Claude Code, from the folder that holds this repo:

```
/plugin marketplace add ./nse-trader-skill
/plugin install nse-trader-skill@nse-trader-skill
```

Or as plain skills (`/trader-invest` and so on):

```bash
cp -r nse-trader-skill/trader-* ~/.claude/skills/
```

## The thirteen

**Data**

| command | job | what it does |
| --- | --- | --- |
| `/trader-data` | Data Layer | Fetches and caches daily, intraday and circuit-band data with fallbacks (Yahoo, NSE API, NSE bhavcopy, Kite MCP, CSV import); `doctor` tells you which sources work from your network. |

**Long-term investing**

| command | job | what it does |
| --- | --- | --- |
| `/trader-invest` | Long-term Analyst | Fundamentals from screener.in / NSE / Kite, a transparent 0 to 100 scorecard (valuation, quality, growth, stability, technicals), cyclical and PSU flags, a verdict, and a staggered CNC buy plan with tax notes. |
| `/trader-backtest` | Backtester | Years of daily data: buy-and-hold, SIP (with XIRR), SMA cross, 52-week breakout, dip-buy and rebalance. Real Zerodha charges, LTCG/STCG tax, benchmark vs NIFTYBEES, walk-forward and overfitting warnings, HTML report. |
| `/trader-portfolio` | Portfolio Manager | Imports Kite holdings or Console tradebook, shows lots and LTCG dates, flags concentration (stock, sector, group, PSU, gold/silver, no index), tax-aware rebalancing, FY tax estimate, SIP planner. |

**Intraday and swing**

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Nifty 500 setups: opening-range breakouts, swing setups, and a `longterm` screen (above 200-DMA, near 52w high/low, oversold). |
| `/trader-news` | News Monitor | Yahoo and Google News India, falling back to NSE corporate announcements. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar from 09:15 and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Stop and target, reward:risk after charges, real circuit band. |
| `/trader-timemachine` | Time Machine | 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | PASS or BLOCK against your INR limits, charges and circuit bands. |
| `/trader-budget` | Budget Manager | Whole-share sizing to your max loss per trade. |
| `/trader-broker` | Order Executor | Zerodha Kite tickets, three approval gates, contract-note `reconcile`. |
| `/trader-alerts` | Price Alerts | Price, stop, 09:15 bell and 15:15 flat-by alarms. |

## Long-term workflow

```
python3 trader-data/data.py daily IOC NIFTYBEES --years 5
python3 trader-invest/invest.py fetch IOC
python3 trader-invest/invest.py analyse IOC
python3 trader-backtest/backtest.py run --strategy sip --symbol IOC --param amount=5000 --tax
python3 trader-invest/invest.py plan IOC --amount 5000 --tranches 3
python3 trader-portfolio/portfolio.py import kite-holdings.json && python3 trader-portfolio/portfolio.py risk
```

Or just ask Claude: "should I buy IOC for the long term?" and it runs these.

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
Most intraday traders in India lose money (SEBI, FY23). Update the NSE holiday
lists each year. MIT licence, see LICENSE; original work by Jake Schincariol.
