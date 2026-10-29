---
name: trader-backtest
description: >-
  Backtester. Replays a long-term or swing strategy on years of NSE daily bars
  before you commit money to it: buy and hold, monthly SIP, SMA crossover,
  52-week-high breakout, swing-structure trend following (break of structure
  in, change of character out), buy-the-dip below the 200-DMA, and multi-ETF
  rebalancing (for example NIFTYBEES / GOLDBEES / ITBEES). Long-only CNC,
  fills at the next day's open, slippage, whole shares, Zerodha delivery
  charges (DP included) and optional STCG/LTCG tax on FIFO lots. Prints CAGR,
  XIRR, max drawdown and its duration, volatility, Sharpe, Sortino, win rate,
  exposure, charges, tax, and a NIFTYBEES benchmark with alpha and beta, then
  writes summary.json, trades.csv, equity.csv and a self-contained HTML report.
  Walk-forward split and parameter sweep with an overfitting warning. Use when
  the user says "backtest", "how would a SIP in NIFTYBEES have done", "test a
  golden cross", "52 week high strategy", "buy the dip", "rebalance my ETFs",
  "XIRR", "CAGR", "drawdown", "would this have worked", or "optimise these
  parameters".
argument-hint: "[strategy] [SYMBOL] or --name NAME"
---

# trader-backtest

The backtester answers one question before money moves: if this rule had run
on the stock's own daily history, what would it have earned, how deep would
it have fallen, and how long would you have sat underwater? It is a
historical simulation, not a prediction. Education and tooling, not
financial advice. It never places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

## Get the data first

It reads only the daily cache written by **trader-data**:
`trader/data/daily/<SYMBOL>.csv` (header `date,open,high,low,close,volume`),
and prefers `trader/data/daily/<SYMBOL>.adj.csv` (split and bonus adjusted)
when it exists. If a symbol is missing, run `/trader-data` for it (or, with
the Kite MCP connected, `get_historical_data` then `trader-data import-kite`).
Fetch NIFTYBEES too: it is the default benchmark when cached.

```bash
python3 "${CLAUDE_SKILL_DIR}/backtest.py" check RELIANCE NIFTYBEES     # rows, range, adjusted?, likely splits/bonuses
```

## Commands

```bash
B="${CLAUDE_SKILL_DIR}/backtest.py"
python3 "$B" run --strategy buy_hold --symbol NIFTYBEES
python3 "$B" run --strategy sip --symbol NIFTYBEES --param amount=10000
python3 "$B" run --strategy sma_cross --symbol RELIANCE --param fast=50 --param slow=200 --tax
python3 "$B" run --strategy breakout_52w --symbol TCS --param exit_low=50 --param trail_pct=20
python3 "$B" run --strategy swing_trend --symbol RELIANCE --param retrace_pct=5 --param entry=bos
python3 "$B" run --strategy swing_trend --symbol RELIANCE --sweep retrace_pct=0,3,5,8 --split 0.7
python3 "$B" run --strategy dip_buy --symbol NIFTYBEES --param dip_pct=8 --param exit_above_pct=5
python3 "$B" run --strategy rebalance --weights NIFTYBEES=0.6,GOLDBEES=0.2,ITBEES=0.2 --param freq=quarterly
python3 "$B" run --name three-etf-quarterly            # a named strategy (trader/strategies.json, else the example)
python3 "$B" run --strategy sma_cross --symbol RELIANCE --split 0.7                       # walk-forward check
python3 "$B" run --strategy sma_cross --symbol RELIANCE --sweep fast=10,20,50 slow=100,200 --split 0.7
python3 "$B" strategies                                # every type, its parameters, the named strategies
```

Flags: `--from / --to YYYY-MM-DD` (earlier bars still warm up the averages),
`--capital` (default ₹1,00,000; 0 for SIP), `--fill open|close` (default
open: the next day's open after the signal close), `--slippage-bps`
(default 5), `--tax`, `--rf` (risk-free, default 6.5 % a year),
`--benchmark SYMBOL|none` (default NIFTYBEES when cached), `--metric` for the
sweep (sharpe, cagr, sortino, total_return, xirr, calmar), `--auto-adjust`,
`--json`, `--no-write`, `--out DIR`, `--root DIR`.

`strategies.example.json` in this folder shows the named-strategy format.
Copy it to `trader/strategies.json` to keep the user's own.

## The strategies (all long-only CNC, whole shares)

| type | rule |
| --- | --- |
| `buy_hold` | buy with all the cash on the first day, hold |
| `sip` | `amount` on the first trading day of each month, whole shares, leftover cash carried to next month |
| `sma_cross` | long while SMA(fast) is above SMA(slow) at the close, flat below |
| `breakout_52w` | buy on a close above the highest close of the last `lookback` (252) days; sell on a close below the lowest low of the last `exit_low` days, or `trail_pct` % under the best close since entry |
| `swing_trend` | market structure on daily bars: `entry` `bos` buys the next open after a close breaks the pending swing high in an uptrend, `reversal` buys when the trend turns up; sells when a close breaks the change-of-character level and the trend turns down. `retrace_pct` (5) ignores pullbacks shallower than that percent (null = every break counts), `warmup` (40) bars must have been seen before an entry. Causal, so sweep and `--split` are honest |
| `dip_buy` | buy when the close is `dip_pct` % below SMA(`ma`); sell `exit_above_pct` % above it (null holds forever) |
| `rebalance` | `weights` per symbol (rest is cash), `freq` monthly, quarterly, or threshold (`band` points of drift) |

## Execution and costs

- Signals use the close; orders fill at the **next day's open** (or that
  close with `--fill close`). SIP and calendar rebalances fill on their own
  day, since they need no signal. An order signalled on the last bar is
  dropped and noted.
- Slippage: buys pay `open * (1 + bps/10000)`, sells get `* (1 - bps/10000)`.
- Charges: the shared Zerodha delivery model (0 brokerage, STT 0.1 % both
  sides, stamp 0.015 % on buys, exchange 0.00307 %, SEBI ₹10 a crore, GST
  18 %, DP ₹15.34 once per scrip per sell day), deep-merged with
  `trader/charges.json` and multiplied by its calibration factor. Labelled
  "estimate" or "estimate (calibrated from N contract notes)".
- Tax (`--tax`): FIFO lots, STCG 20 % under 365 days, LTCG 12.5 % above
  ₹1.25 lakh per financial year, short-term losses set off against long-term
  gains. Paid on the first bar of the next FY from cash; if cash is short it
  sells just enough of the largest holding. The last FY's tax is deducted at
  the end as "due". Today's rates are used for every year.

## What it writes

`trader/backtests/<name>-<YYYYMMDD-HHMMSS>/` (IST):

| file | what |
| --- | --- |
| `summary.json` | params, every metric, benchmark, split, sweep table, data info, caveats |
| `trades.csv` | every fill and tax payment: date, symbol, side, qty, price, value, charges, realized P&L, holding days, why |
| `equity.csv` | daily cash, holdings, equity, flows, daily return, NAV (growth of 100), drawdown, benchmark NAV |
| `report.html` | one self-contained page: inline SVG equity and drawdown charts, results, caveats, orders. No scripts |

## How to read the results

1. **Money first.** Invested, final equity, profit. For a SIP, **XIRR** is the
   number that matters (it weights each rupee by how long it was in);
   CAGR is the time-weighted return of the strategy itself.
2. **Pain next.** Max drawdown, when it started and bottomed, whether it
   recovered, and the longest stretch underwater in calendar days. Ask the
   user if they would really have held through that.
3. **Risk-adjusted.** Sharpe and Sortino against 6.5 % risk-free. Below 0
   means a fixed deposit would have done better for less risk.
4. **Against the benchmark.** NIFTYBEES bought and held with the same cash
   flows, no charges. Alpha is the yearly excess after beta; beta under 1 is
   less market exposure. A strategy that loses to the benchmark after costs
   is not worth the effort.
5. **Trades.** Closed trades, win rate, average win and loss, exposure (share
   of days invested), charges and tax paid.
6. **Split.** In-sample vs out-of-sample. If out-of-sample is much worse,
   say the rule was fitted to the past.
7. **Sweep.** Report the median, not just the best. Repeat any overfitting
   warning exactly as printed. The best of many combinations is partly luck.

Always repeat the caveats line by line in substance: the data source,
adjusted or not (and any flagged split/bonus dates), survivorship bias,
charges are an estimate, past performance does not predict the future.

## Split and bonus warnings

A symbol that has a `<SYMBOL>.adj.csv` is trusted as adjusted: the guess is
off for it, and the caveats say so. With only an unadjusted file, it scans for overnight gaps that match a
split or bonus (to about 1/2, 2/3, 1/3, 1/4, 1/5, 1/10) and lists the
dates. Results across those dates are wrong (a fake 50 % crash). Fix it by
fetching adjusted data with trader-data, or `--auto-adjust` to back-adjust by
the matched ratio after checking the dates on nseindia.com (corporate
actions). A real crash can match a ratio too, so never auto-adjust blindly.

## Rules

- Every number comes from the run. Never round a weak result into an edge.
- Say the period, the fill model, slippage, and whether tax was on.
- Never present the best sweep combination as the expected result.
- No short selling, no leverage, no F&O. Long-only CNC only.
- It never places, sizes or sends an order. To act on a result, the user
  goes through trader-risk and trader-broker, and gives their own "yes".

## Safety

- Education and tooling, not financial advice. Past performance does not
  predict future returns; a backtest is a simulation on old data.
- Nothing here places an order. Any real order goes through trader-risk and
  trader-broker and needs the user's explicit "yes" for that one order.
- Tax output is a rough estimate at today's rates, not tax advice.
