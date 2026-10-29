---
name: trader-risk
description: >-
  Risk Manager for NSE cash equities. Checks every trade against your limits
  and stops anything over them, before it can reach the broker. For a day
  trade: a stop is required, the loss at the stop plus estimated charges
  must fit your max loss per trade (measured from the higher of the plan's
  entry and a fresh price), reward:risk must meet your minimum, the day's
  total risk must fit your daily loss limit, no more than your trades per
  day, no new entries outside your IST entry window or when NSE is closed,
  and no new MIS (intraday) entries after 15:00. It also keeps the basics:
  whole shares only, product MIS or CNC, blocked symbols, order size,
  position cap, cash buffer, no MIS leverage unless you allow it, short
  sales (MIS only), F&O and non-NSE symbols, malformed brackets, duplicates
  and a daily order limit. Getting out is never blocked by a day limit.
  Prints PASS or BLOCK with the reason and records the verdicts so
  trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my rules",
  "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker. Most people who day trade lose money (SEBI's own
studies say about 9 in 10 intraday equity traders do). These limits keep
each loss and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 09:45"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |
| `trader/data/bands.json` | optional: today's circuit bands (trader-data `bands`, or `import-kite` of Kite `get_quotes`) |
| `trader/charges.json` | optional: charge rate overrides and the calibration (trader-broker `ticket.py reconcile`) |

## The order shape

```json
{"symbol": "RELIANCE", "side": "buy", "qty": "4", "product": "MIS", "type": "market",
 "time_in_force": "day", "order_class": "bracket",
 "take_profit_limit_price": "1472.30", "stop_loss_stop_price": "1432.30"}
```

`symbol` is the NSE symbol (RELIANCE, M&M, BAJAJ-AUTO; a trailing `.NS` is
stripped). `qty` is whole shares: no fractional shares, no rupee-notional
orders. `product` is `MIS` (intraday, squared off the same day) or `CNC`
(delivery); missing means `default_product` (MIS) with a warning. `type` is
market, limit, stop (SL-M) or stop_limit (SL). Zerodha has no native bracket
order: a "bracket" here is the entry plus a separate SL-M stop and a LIMIT
target that trader-broker places; the max loss is still checked per ticket.

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, no qty or a fractional qty, a notional amount, product not MIS/CNC, a limit order with no limit price, extended hours; a bracket not `day` or missing its stop-loss or target |
| asset | options or futures (F&O is out of scope), crypto, anything that is not an NSE symbol |
| short | a sell of more than you hold (`allow_short` false); with it on, a short must be MIS (no overnight short in cash) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need MIS leverage while `allow_mis_leverage` is false |
| stop | (entries) no stop while `require_stop` is true; a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) plus estimated charges over `max_loss_per_trade_inr` |
| circuit | (every order) a limit or stop price outside today's price band in `trader/data/bands.json`; for an MIS bracket the stop-loss and target legs too (they are placed today): "limit price outside today's circuit" |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) today's entries' max losses plus this one over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after` (IST), or NSE is closed |
| mis cutoff | (entries) a new MIS entry after `no_mis_after` (15:00): the broker squares MIS off around 15:20 |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **Charges** are a calibrated estimate from one `costs()` helper (the same
  in trader-budget and trader-broker). Embedded Zerodha rates, verified
  2026-10-29 from zerodha.com/charges: intraday (MIS) brokerage min(₹20,
  0.03%) per executed order, STT 0.025% on the sell, stamp 0.003% on the
  buy; delivery (CNC) brokerage ₹0, STT 0.1% on both sides, stamp 0.015% on
  the buy, DP ₹15.34 per scrip per CNC sell day; exchange txn 0.00307%, SEBI
  ₹10 a crore, GST 18% on brokerage + exchange + SEBI. `trader/charges.json`
  (copy `trader-budget/charges.example.json`) is deep-merged over these, and
  the total is multiplied by its `calibration.factor`. The output says
  "estimate", or "estimate (calibrated from N contract notes)" once the
  user has run trader-broker `ticket.py reconcile` on their tradebook and
  contract notes. The contract note is still the real number.
- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old).
- **The window** is IST (fixed UTC+05:30), worked out in the tool, never
  from the computer's clock zone. NSE trades 09:15 to 15:30; weekends and the
  NSE holidays are closed: the built-in 2026 list plus `trader/data/calendar.json`
  (`python3 trader-data/data.py calendar` writes it). For a year in neither it
  warns: "holiday list covers 2026 only: check nseindia.com". The Muhurat
  session is treated as closed.
  Set both `no_entries_*` to null to switch the window off.
- **Circuit bands** come from `trader/data/bands.json` (`--bands`), which
  trader-data writes (`bands`, or `import-kite` from a saved Kite
  `get_quotes` result: its lower and upper circuit limits). The latest file
  up to 5 days old counts. A limit or stop price outside the band is a
  BLOCK (the exchange would reject it). A long's stop below the lower
  circuit is a warning: "the stop may not fill if the stock hits lower
  circuit". A stock with `band_pct` null has no fixed band (F&O stocks:
  dynamic limits). With no band data, a stop more than 5% away gets the old
  warning plus "band unknown: run trader-data bands".

Warnings do not block, but say them to the user: a LIVE account, a missing
product, MIS leverage in use, a stop that lives only in the plan, a price
too old to use, a stop past the lower circuit, an unknown band.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop (with charges), reward:risk, and how much of today's limits it
   uses. For a BLOCK, give the reason and the fix (fewer shares, a closer
   stop from trader-exit, wait for the window, or skip the trade). Never
   change a limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint.

## Example: the reel

The RELIANCE bracket from trader-budget at 09:45 IST on Monday 2026-10-19,
on a fresh ₹10,000 paper account with the example config:

```
PASS  #1 buy RELIANCE 4 shares MIS, entry
          entry 1452.30 (the plan's entry), stop 1432.30, target 1472.30: risk 20.00 a share
          max loss ₹86.12 (limit ₹100) incl. ~₹6.12 charges (estimate), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹300 daily loss

Charges are an estimate (Zerodha rates verified 2026-10-29); the contract note is the real number. Calibrate with trader-broker: ticket.py reconcile.
1 passed, 0 blocked at 2026-10-19 09:45 IST. Cash after the passed orders: ₹4,190.80 (paper account).
```

One more share and it is blocked: 5 x 20.00 + ₹7.65 charges = ₹107.65, over
the ₹100 max loss. At 09:55 with a fresh 1458.00 quote the same 4 shares
risk ₹108.93: blocked. At 15:05 a new MIS entry is blocked by the MIS
cutoff. The 15:15 sell that closes the position passes: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one that fills, and a
  stop can fill worse than its price in a fast market.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper by default (`"paper": true`): Kite has no paper account, so paper tickets are logged only,
  never sent. Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, MIS leverage or short selling unless the config allows it and the user confirms.
- Kite login is OAuth in the browser; no keys or tokens in the chat.
