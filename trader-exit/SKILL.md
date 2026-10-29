---
name: trader-exit
description: >-
  Exit Planner for NSE intraday trades. Sets your stop and your target before
  you enter, so you know when to get out. Reads the entry from trader-entry
  (or a price you give), puts the stop at the opening-range midpoint by
  default (or the range low, your price, a percent, or a multiple of the ATR),
  rounds it down to the paisa, and sets the target at 2R (or 1R, or your
  price). Shows approximate Indian intraday costs (brokerage, STT, exchange,
  SEBI, GST, stamp, from the shared Zerodha charges model, calibrated from
  contract notes when trader/charges.json says so) and reward:risk after
  costs, and warns when the stop sits at or past the stock's real lower
  circuit from trader/data/bands.json (or a 5/10/20% band heuristic). Refuses a stop at or above the entry, a target
  at or below it, or a stop too tight to survive the spread, and says why.
  Writes trader/plan.json for the Time Machine, the Budget Manager and Price
  Alerts. Long side only. Use when the user says "where's my stop", "set my
  stop and target", "plan the exit", "when do I get out", "stop loss", "what's
  my R", "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X] [--band 5|10|20]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap, a fast market or a circuit limit can fill past it, or not fill at all.
Day trading loses most people money, and a neat plan does not change that.

Market: NSE cash equities, session 09:15 to 15:30 IST (UTC+05:30, no DST).
Prices in rupees (₹, Indian digit grouping).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo .NS or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 364.18 --or-high 362.18 --or-low 354.47
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol HINDCOPPER --entry 310.40 --setup level --stop pct:1 --band 10
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:15`, `--qty N` (shares for the cost estimate,
default about ₹1,00,000 worth), `--band 2|5|10|20` (the stock's circuit band),
`--json`, `--now "2026-10-19 10:00"` (IST), `--entry PRICE` with a waiting
entry.json to plan ahead, `--setup level`, `--offline FILE` (a saved Yahoo
chart JSON for an ATR stop). Symbols are NSE symbols (RELIANCE, M&M,
BAJAJ-AUTO); the tool adds `.NS` for Yahoo. F&O contracts are refused.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:15) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars (`SYMBOL.NS`) up to the entry bar, free and unofficial. query1 first, then query2 |
| `trader/data/intraday/<SYMBOL>-5m.json` | the trader-data cache for an `atr:K` stop: with `--cache`, or when Yahoo fails (it prints the data's time) |
| `trader/data/bands.json` | the price bands trader-data writes: `band_pct`, `lower`, `upper` per symbol. Read when present (`--bands PATH`) |
| `trader/charges.json` | optional charges override, deep-merged over the built-in Zerodha defaults; its `calibration` (written by trader-broker `reconcile`) scales the total (`--charges PATH`) |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, approximate costs, reward:risk after costs, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding.** The stop rounds AWAY from the entry to the paisa (down, for a
  long). Risk = entry minus stop. Target = entry + R x risk, rounded half up
  to the paisa. `2R` by default; `1R`, `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread, normal wiggle would stop you out. An entry.json that has not
  triggered (unless you give `--entry`). A short: v2 plans longs only (an
  intraday short on NSE is MIS only, with no overnight short in cash).
- **Flat by.** 15:15 IST by default (or `flat_by` in the config). Out by
  then, win or lose, no overnight hold. Zerodha auto squares off intraday
  (MIS) positions at about 15:20, so a later flat-by gets a note, and one at
  or after the 15:30 close is refused. NSE has no early closes. An entry at
  or after the flat-by time is refused.
- **Costs (approximate).** Zerodha intraday (MIS) charges for a round trip,
  verified 2026-10-29 from zerodha.com/charges: brokerage min(₹20, 0.03%) an
  order, STT 0.025% on the sell, NSE exchange transaction 0.00307%, SEBI ₹10
  a crore, GST 18% on brokerage + exchange + SEBI, stamp 0.003% on the buy.
  `trader/charges.json`, when present, is deep-merged over these numbers and
  the total is multiplied by its `calibration.factor`. The plan labels the
  figure "estimate", or "estimate (calibrated from N contract notes)" once
  trader-broker `reconcile` has compared it with real contract notes. Shown at
  the stop and at the target, with reward:risk after costs. Your contract note
  is the real number.
- **Circuit bands.** NSE stocks have daily price bands (2, 5, 10 or 20%, or
  none for some F&O stocks). With `trader/data/bands.json` (trader-data
  `bands`) the plan shows the stock's real band and lower circuit price and
  warns when the stop is at or under the lower circuit, or the move is at or
  over the band; a stock with no band gets a note that nothing stops a fast
  fall; a bands file from another day gets a refresh note. Without the file:
  `--band`, else a stop 5% or more under the entry gets a warning. If the
  stock locks at the lower circuit, a stop there may not fill.
- **Bars for an ATR stop.** Yahoo, query1 then query2. With `--cache`, or
  when Yahoo fails, the trader-data cache `trader/data/intraday/<SYMBOL>-5m.json`
  with a note of its time. With no cache either it stops with the way out:
  run `python3 trader-data/data.py doctor`; or call the Kite MCP
  `get_historical_data`, save the JSON and run `python3 trader-data/data.py
  import-kite FILE --kind historical --symbol SYMBOL`.
- **Holidays.** A plan for an NSE holiday says so ("NSE holiday: Dussehra").
  The list covers 2026 only: outside it, check nseindia.com.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk, the approximate
   costs (and whether they are calibrated) and flat by. If there is no
   `trader/data/bands.json`, suggest `python3 trader-data/data.py bands`, or
   pass `--band` if the user knows the stock's band.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: the reel

RELIANCE (synthetic test bars), Monday 2026-10-19. The entry is 364.18 at
10:00 AM IST (the 09:55 bar, bar 8) and the opening range is 354.47 to
362.18. `exit.py plan --stop or-mid --target 1R --write`:

```
Exit plan: RELIANCE long, Mon 2026-10-19, opening-range breakout on 5-minute bars
  Entry         ₹364.18 at 10:00 AM (bar 8)
  Stop          ₹358.32  opening-range midpoint: (₹362.18 + ₹354.47) / 2 = ₹358.325, rounded down
  Target        ₹370.04  1R (one times the risk)
  Risk a share  ₹5.86 (1.61% of the entry)
  Reward:risk   1.0
  Costs         about ₹82.14 at the stop, ₹83.07 at the target for 274 shares (intraday charges, estimate); reward:risk after costs 0.9
  Flat by       15:15 IST, win or lose: no overnight hold (MIS auto square-off about 15:20)
```

The default 2R target would be 364.18 + 2 x 5.86 = 375.90. trader-budget then
sizes it: a ₹500 max loss per trade buys floor(500 / 5.86) = 85 shares, less
whatever the costs eat.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk, and
  the approximate costs. It never prints a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. v2 plans longs only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

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
