---
name: trader-invest
description: >-
  Long-term stock analyst for NSE cash equities. Answers "should I buy IOC
  for the long term?", "is TCS a good long-term buy?", "compare BPCL and
  IOC", "how should I invest 5000 in ITC", "value trap or value?". Fetches
  fundamentals from screener.in (consolidated, then standalone), the NSE
  quote API, your own JSON or a Kite MCP quote, then scores valuation (PE,
  PB, dividend yield, earnings yield vs the 10y G-sec), quality (ROE, ROCE,
  debt/equity, promoter holding and pledge), growth (3y/5y revenue and
  profit CAGR, loss years), stability and long-term technicals (200-DMA,
  52-week range, 1/3/5y CAGR, drawdown, volatility, swing-structure cushion
  above the change-of-character level) on a transparent, editable weighted
  scorecard, with hints (not scored) for the Nifty 50 P/E and for
  institutional-money delivery days. Flags cyclicals (judge on mid-cycle earnings)
  and PSUs (policy risk). Prints a 0-100 score per pillar and overall, a
  verdict band, the reasons, the data age and every missing field. Writes a
  staggered CNC (delivery) buy plan in whole shares with Zerodha charges,
  STCG/LTCG tax and a review checklist. Not financial advice: nothing is
  bought without the user's explicit yes.
argument-hint: "SYMBOL [--amount 5000]"
---

# trader-invest

The day-trading skills in this pack look at minutes. This one looks at years.
It turns "should I buy X for the long term?" into numbers you can check: what
the business earns, how well, how steadily, what you pay for it, and how the
stock has behaved. It does not predict prices. A high score means the numbers
on the scorecard look good today, not that the stock will go up.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/invest.py" fetch IOC                      # screener.in, then NSE quote API
python3 "${CLAUDE_SKILL_DIR}/invest.py" fetch IOC --from ioc.json      # your numbers when screener.in blocks
python3 "${CLAUDE_SKILL_DIR}/invest.py" fetch IOC --kite-quotes q.json # price from Kite MCP get_quotes
python3 "${CLAUDE_SKILL_DIR}/invest.py" analyse IOC                    # --horizon 5y --gsec 6.5
python3 "${CLAUDE_SKILL_DIR}/invest.py" compare IOC BPCL HINDPETRO
python3 "${CLAUDE_SKILL_DIR}/invest.py" plan IOC --amount 5000 --tranches 3
python3 "${CLAUDE_SKILL_DIR}/invest.py" --json analyse IOC             # --json works for every command
# analyse also saves trader/invest/IOC.json (read by trader-thesis and trader-report); --no-save skips it
```

## When to use

- The user asks about holding a stock for months or years, a SIP-style
  staggered buy, dividends, "value", "cheap", "multibagger", or compares
  companies for the long run.
- Not for intraday or swing trades: that is trader-scanner, trader-entry and
  trader-exit. Not for F&O.

## Workflow

1. **Daily bars**: run trader-data for the symbol so
   `trader/data/daily/<SYM>.csv` (or the split/bonus adjusted `<SYM>.adj.csv`)
   exists. Without it the technicals pillar is scored as missing, not good.
2. **Fetch**: `invest.py fetch SYM` writes
   `trader/data/fundamentals/<SYM>.json` (the shared contract that
   trader-portfolio reads). Each source prints ok or FAIL with its own error.
3. **Analyse**: `invest.py analyse SYM`. Read the verdict, the pillars, the
   flags and the missing list to the user in plain words.
4. **Plan** (only if the user wants to buy): `invest.py plan SYM --amount N`.
   Add `--orders trader/orders.json` to write tranche 1 as a CNC limit order.
5. **Broker**: trader-risk checks the order, then trader-broker shows the CNC
   ticket. Nothing is placed until the user types an explicit yes.

## Hints that are not scored

- **Swing cushion** (scored, technicals pillar, weight 1): the daily bars run
  through a market-structure tracker (pullbacks under 5% ignored). The
  change-of-character level (CoCh) is the structural stop; the cushion is
  `(last close - CoCh) / last close`, in percent. It is negative in a
  downtrend (the close is on the wrong side). Needs more than 40 bars, else
  it is scored as missing. `analyse` prints a `Swing:` line with the trend.
  Reads `high` and `low` from the same daily file (`.adj.csv` first).
- **Market valuation**: if `trader/data/index/nifty-50.csv` exists
  (`date,open,high,low,close,volume,pe,pb,div_yield`, written by
  trader-data), `analyse` prints a `Market:` line with the latest Nifty 50
  P/E and the rule of thumb: 25 or more is expensive, 20 or less is cheap
  (`NIFTY_PE_EXPENSIVE`, `NIFTY_PE_CHEAP` in invest.py). It is context, not a
  timing signal, and does not change the score. In `plan`, an expensive
  market adds a note and a `pace_hint` (two more tranches, 1.5 times the gap,
  for example 5 tranches 45 days apart instead of 3 every 30). The plan
  itself is unchanged: the pacing is the user's call.
- **Delivery**: if `trader/data/delivery/<SYM>.csv` exists (header
  `date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series`, an empty
  cell is missing), `analyse` prints how many of the last 30 sessions were
  institutional-money days: delivery quantity and quantity per trade both
  above 1.2 times their 30-session mean, on a series other than BE or BZ.
  Under 5 values it says there is not enough data. A hint only: it never
  changes the score.

## Sources and their failure modes

| order | source | gives | fails when |
| --- | --- | --- | --- |
| 1 | screener.in company page (consolidated, then standalone), browser UA, stdlib html.parser | top ratios, P&L (sales, net profit, EPS, dividend payout), quarters, balance sheet, shareholding, sector | it blocks bots (403), asks for login, or changes its HTML |
| 2 | NSE `/api/quote-equity?symbol=` after a cookie-priming visit to nseindia.com | exact last price, PE, sector, industry, issued size, 52w range | NSE returns 401/403 without fresh cookies |
| 3 | `--from FILE`, your JSON in the fundamentals shape | anything you fill in; overrides 1 and 2 | never: it is the fallback |
| 4 | `--kite-quotes FILE`, the saved Kite MCP get_quotes output | the price; PE, PB, market cap and yield are rescaled to it | the key `NSE:SYM` is missing |

If screener.in fails, tell the user, then either save their page with
`--screener-html page.html` or write the numbers into a JSON file
(`tests/fixtures/trader-invest/manual-IOC.json` shows the shape) and use `--from`.

## Price from the Kite MCP

When Zerodha's Kite MCP tools are in the session, call `get_quotes` with
`["NSE:IOC"]`, save the raw JSON result to `trader/data/kite-quotes-IOC.json`
and run `invest.py fetch IOC --kite-quotes trader/data/kite-quotes-IOC.json`.
It keeps the stored fundamentals and swaps in the broker's price. No keys or
tokens are needed by the script; Kite login is OAuth in the browser.

## The scorecard

`scorecard.example.json` holds every weight and threshold. Copy it to
`trader/scorecard.json` and edit: it is deep-merged over the example. Each
metric scores 0 at `bad` and 100 at `good`, linear and clamped. A pillar is
the weighted mean of its metrics; the overall score is the weighted mean of
the pillars (valuation 25, quality 30, growth 25, stability 10, technicals 10).

- **Missing is never good**: a missing metric scores `missing_score` (25) and
  is listed. Under 60% coverage the verdict is capped at "Value trap risk / wait".
- **Cyclicals** (oil, gas, metals, mining, coal, or profits that swing more
  than 45% of their average) are valued on **mid-cycle PE** = market cap /
  average net profit of the last 5 years, and capped at "Reasonable, size small".
- **PSUs** (President of India / government as promoter, or the built-in PSU
  list) get a policy-risk flag: fuel pricing, subsidy sharing, dividends and
  offers for sale are set by the owner.
- **Banks and NBFCs** skip debt/equity and ROCE (not meaningful).
- **Caps**: promoter pledge over 20% or 2+ loss years caps at "Value trap risk
  / wait"; a loss in the latest quarter caps at "Reasonable, size small".

## Reading the verdict

| score | verdict | what to tell the user |
| --- | --- | --- |
| 70+ | Strong long-term candidate | the numbers are good across pillars; still buy in steps |
| 55-70 | Reasonable, size small | some pillars are weak or the business is cyclical; a small position only |
| 40-55 | Value trap risk / wait | cheap or popular, but the quality, growth or data do not back it; wait for results |
| under 40 | Avoid for long term | the scorecard does not support a long hold |

Always say the reasons, the flags, the data age and the missing fields with
the verdict. Never round a capped verdict up.

## Worked example: IOC (Indian Oil), 29 Oct 2026

Fixture data from `tests/fixtures/trader-invest/` (sources and dates are in
each file's provenance; daily bars in the tests are synthetic):

```
IOC  ₹130.41   score 68/100   REASONABLE, SIZE SMALL
  Oil, Gas & Consumable Fuels | mcap 1,84,154 cr | source: screener.in (standalone) + nse quote api | data coverage 95%
  valuation 100, quality 62, growth 75, stability 4, technicals 56
  Valuation: PE 5.44 (mid-cycle 7.56), PB 0.81, yield 6.36%; earnings yield vs 10y G-sec 6.50%: 6.73 points
  Latest quarter Q1FY27: revenue 2,75,972 cr, net profit -2,661 cr (-146.8% y/y)
  Flags: cyclical (judge on mid-cycle earnings); PSU (policy risk); 3y profit CAGR 65% starts from a weak year
  Verdict caps: Q1FY27 loss; cyclical business
  Data age: fetched 2026-10-29, latest annual FY26, next results (Q2FY27) due by 2026-11-14
  Missing (never treated as good): promoter_pledge_pct
```

How to say it: IOC is cheap on every valuation measure, even on mid-cycle
earnings, and pays a high dividend, but its profit swings with crude prices
and government fuel pricing (FY23 ₹8,242 cr, FY24 ₹39,619 cr, Q1 FY27 a ₹2,661
cr loss). It is a cyclical PSU: a small position bought in steps, judged on
the next few quarters, not a buy-and-forget compounder. `compare IOC TCS`
shows the contrast: TCS scores 78 (Strong long-term candidate) on quality and
steady growth, with weaker technicals after its 2026 fall.

```
IOC staggered CNC buy plan: ₹5,000 at about ₹130.41 -> 38 shares in 3 tranche(s)
  T1  buy 13 @ ~130.41 = ₹1,695.33, charges ₹2.01 (estimate)  now, at a limit near ₹130.41
  T2  buy 13 @ ~130.41 = ₹1,695.33, charges ₹2.01 (estimate)  after the Q2FY27 results (due by 2026-11-14)
  T3  buy 12 @ ~130.41 = ₹1,564.92, charges ₹1.86 (estimate)  on or after 2026-12-28, only if the thesis holds
  later sell of all 38: charges about ₹20.48 incl. DP ₹15.34
```

## Charges and tax in the plan

Charges follow the shared model (verified 2026-10-29 from zerodha.com/charges),
deep-merged with `trader/charges.json`: delivery brokerage ₹0, STT 0.1% on buy
and sell, stamp 0.015% on the buy, exchange 0.00307%, SEBI ₹10 a crore, GST
18% on brokerage + exchange + SEBI, and a DP charge of ₹15.34 per scrip per
day on the later sell. Shown as "estimate", or "estimate (calibrated from N
contract notes)" once trader-broker `reconcile` has calibrated it.

Tax: STCG 20% if sold within 12 months; LTCG 12.5% on gains above ₹1.25 lakh
a year (shared across all listed equity), plus cess. Each tranche has its own
holding period. Dividends are taxed at the slab rate.

## Honest caveats

- screener.in may block or change its HTML at any time; the NSE API needs
  fresh cookies. Fall back to `--from` with your own numbers.
- Free data can be stale or wrong: check the data age line and the latest
  quarter against the company's NSE filings before acting.
- Standalone vs consolidated matters (IOC's subsidiaries change the numbers).
- The scorecard is a rule of thumb with editable weights. It cannot see
  management quality, regulation, competition or what is already priced in.

## Safety

- Education and tooling, not financial advice. A verdict is a summary of the
  numbers, not a recommendation to buy.
- Never place an order from this skill. A plan goes to trader-risk and then
  trader-broker, and is placed only when the user types an explicit yes for
  that order and approves the order tool's card.
- Never change the scorecard or charges file to make a stock look better.
