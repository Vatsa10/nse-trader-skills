---
name: trader-budget
description: >-
  Budget Manager for NSE cash equities. Sizes every position to your budget
  in rupees and your max loss per trade, charges included. Reads the trade
  plan from trader-exit (entry, stop, target), finds the whole shares that
  fit inside four limits (max loss per trade incl. estimated charges,
  position cap, max order, cash after your buffer), says which limit decided
  it, and writes ONE order (entry, SL-M stop, LIMIT target; product MIS by
  default) to trader/orders.json. A stop too far away for your max loss is a
  plain refusal, not a smaller order. Nothing is placed. Also splits a budget
  across your own list of NSE stocks or ETFs in whole shares. Use when the
  user says "how many shares", "size this trade", "how much should I buy",
  "I have ₹10,000", "I only want to risk ₹100 a trade", "set my rules",
  "keep 10% in cash", "split my money across NIFTYBEES and GOLDBEES", or
  after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1452.30, stop 1432.30")
into an exact order ("buy 4 shares MIS"), inside rules the user sets once.
The stop decides the size: the further away the stop, the fewer shares. It
writes a proposal only. trader-risk checks it and trader-broker asks the
user yes or no. Day trading loses most people money. Sizing to a max loss
keeps each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                 # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write         # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1452.00 --json  # a limit order at 1452.00
python3 "${CLAUDE_SKILL_DIR}/size.py" --product CNC           # delivery instead of intraday
python3 "${CLAUDE_SKILL_DIR}/size.py" --targets "${CLAUDE_SKILL_DIR}/targets.example.json" --prices trader/prices.json
```

`--now "2026-10-19 09:45"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional for a plan, required for targets mode (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the proposed order(s) it writes with `--write` |
| `trader/charges.json` | optional: rate overrides and the calibration from trader-broker reconcile (start from `charges.example.json`) |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how many rupees this is for (example ₹10,000). Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose at the stop, charges included (default ₹100).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹8,000) and the smallest order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (₹300), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "15:00"), **no_mis_after** ("15:00") and **flat_by** ("15:15"), all IST. The
  broker squares MIS positions off around 15:20.
- **default_product**: MIS (intraday) for day trades; CNC for delivery.
- **allow_mis_leverage**: false. MIS can borrow up to about 5x on many stocks; this pack sizes
  from cash only unless the user turns it on.
- **blocked**: symbols they never want to trade.
- Keep **paper** true until they have watched it work: paper tickets are logged, never sent.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only (NSE has no fractional shares). The size is the largest
whole number that fits all four:

| limit | shares |
| --- | --- |
| max loss | shares x (entry minus stop) + estimated charges <= `max_loss_per_trade_inr` |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

Charges are a calibrated estimate, the same `costs()` trader-risk uses.
Embedded Zerodha rates, verified 2026-10-29 from zerodha.com/charges:
intraday brokerage min(₹20, 0.03%) an order, delivery brokerage ₹0, STT
0.025% on an intraday sell or 0.1% on both sides of delivery, exchange txn
0.00307%, SEBI ₹10 a crore, GST 18% on brokerage + exchange + SEBI, stamp
duty 0.003% (intraday) or 0.015% (delivery) on the buy, and DP ₹15.34 per
scrip on a delivery sell day. A delivery (CNC) trade costs more round trip
than intraday, so it can size smaller: the reel's RELIANCE trade is 3 shares
as CNC.

To change a rate (a different broker plan, a rate revision), copy
`charges.example.json` to `trader/charges.json` and edit only what differs:
it is deep-merged over the defaults. Its `calibration` block is written by
trader-broker `ticket.py reconcile`, which compares these estimates with the
user's contract notes and stores `factor` = the median of actual /
estimated; every charge is multiplied by it, and the output then says
"estimate (calibrated from N contract notes)". `--charges PATH` uses another
file.

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it, the plan is for another day,
the symbol is blocked, or the plan is a short. Say the reason in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees,
the charges and the max loss at the stop. Say plainly that a stop is an
order, not a promise: in a fast market, or past the stock's circuit band, it
can fill below the stop or not at all. Then hand over to `/trader-risk`.

## Example: the reel

RELIANCE on Monday 2026-10-19. Opening-range breakout: entry 1452.30 at
09:45 IST, stop 1432.30 (the opening-range midpoint), target 1472.30 (1R).
Risk 20.00 a share. The example config: ₹10,000 budget, 10% cash buffer,
₹100 max loss.

| limit | the math | shares |
| --- | --- | --- |
| max loss | 4 x 20.00 + ~₹6.12 charges = ₹86.12 (5 would be ₹107.65) | **4 (decides it)** |
| position cap | 75% of ₹10,000 = ₹7,500 / 1452.30 | 5 |
| max order | ₹8,000 / 1452.30 | 5 |
| cash | ₹10,000 minus ₹1,000 = ₹9,000 / 1452.30 | 6 |

4 shares = ₹5,809.20. It writes:

```json
{"symbol": "RELIANCE", "side": "buy", "qty": "4", "product": "MIS", "type": "market", "time_in_force": "day",
 "order_class": "bracket", "take_profit_limit_price": "1472.30", "stop_loss_stop_price": "1432.30",
 "why": "opening-range breakout on RELIANCE: 4 shares MIS, bound by your ₹100 max loss per trade (4 x 20.00 + ~₹6.12 charges = ₹86.12 at the stop)",
 "plan": {"entry": 1452.3, "stop": 1432.3, "target": 1472.3, "risk_per_share": 20.0, "charges": 6.12,
          "max_loss": 86.12, "setup": "orb", "entry_time": "09:45 AM", "flat_by": "15:15"}}
```

`why` and `plan` are notes for trader-risk and trader-broker, never sent to
Kite. trader-broker places the entry, then a separate SL-M stop (Zerodha has
no native bracket order). With the stop at 1300.00 one share risks more than
₹100: no order. If the price has run to 1458.00, reward:risk falls to 0.56:
refused, because the move it was waiting for has mostly happened.

## Targets mode: your own list

`--targets FILE` (start from `targets.example.json`: NIFTYBEES, GOLDBEES,
BANKBEES, ITBEES) or `--weights NIFTYBEES=60,GOLDBEES=40` splits a budget
across symbols the user chooses. The cash buffer comes off the top, weights
are renormalised to 100, any symbol over `max_position_pct` is pinned at the
cap and its excess shared out, each slice is bought in whole shares at the
prices in `--prices`, and slices under `min_order_inr` are dropped. What
whole shares cannot use is shown as "left idle". The orders are CNC
(delivery) by default (`--product MIS` to change). They have no stop:
trader-risk blocks them while `require_stop` is true, so use a separate
config (`--config`) for a long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes F&O, MIS
leverage or short positions.

## Safety

- Education and tooling, not financial advice.
- Paper by default (`"paper": true`): tickets are logged only, never sent. Going live is the user's
  own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, MIS leverage or short selling unless the config allows it and the user confirms.
- Kite login is OAuth in the browser; no keys or tokens in the chat.

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
