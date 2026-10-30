---
name: trader-alerts
description: >-
  Price Alerts for NSE. Rings when a price, a stop or the 09:15 opening bell
  hits. Turns the trade plan into three alerts in one go (the stop, the
  target and "flat by 15:15" before Zerodha's MIS auto square-off), plus
  above / below / stop / target levels in ₹, percent moves from the previous
  close, any time of day, and the NSE opening bell, each ringing once so you
  are not spammed. Knows NSE hours (09:15-15:30 IST) and the 2026 NSE
  holidays, works out IST itself, fetches free delayed Yahoo quotes
  (SYMBOL.NS) one symbol at a time, and writes trader/prices.json for sizing
  and the risk check. Use when the user says "alert me when", "tell me if
  RELIANCE hits", "watch my stop", "watch my trade", "remind me to square
  off by 3:15", "is the market open", "is NSE open today", "get me a price",
  or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. The SL-M stop and the LIMIT target exits (or
a GTT for delivery) sit at Zerodha and do the work; the alerts tell the user
what happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open (IST)
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by 15:15 (no flat-by for a CNC plan) from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol HINDCOPPER --stop 308.10
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol HINDCOPPER --target 320.50
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:15 --label "flat by 15:15"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote HINDCOPPER --write       # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset hindcopper-plan-stop
```

`--now "2026-10-19 15:15"` pins the clock (IST); `--offline DIR` reads saved
quote files instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes (₹), merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:15") on a trading day | once per trading day |
| `opening_bell` | NSE is open (09:15 IST) | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. Symbols are
NSE symbols (RELIANCE, M&M, BAJAJ-AUTO); a typed `.NS` is stripped.

## Quotes

- Yahoo first: `query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`
  (`&` is sent as `%26`, so M&M is `M%26M.NS`; NIFTY is `^NSEI`, BANKNIFTY
  `^NSEBANK`), an unofficial free JSON endpoint, one request per symbol, half
  a second apart. On a 429 it waits and tries once more; on a 429, a 5xx or a
  network error after that it tries `query2`. It sends a browser-like
  User-Agent.
- When Yahoo fails for a symbol, or with `--cache`: the trader-data cache,
  `trader/data/intraday/<SYMBOL>-5m.json` (the last 5-minute close), then
  `trader/data/daily/<SYMBOL>.csv` (the last daily close). The quote's source
  is `cache` and it carries a note: "using cached data from <time>, run
  `python3 trader-data/data.py intraday SYMBOL` to refresh". A cached price
  can be hours old: say so, and never let it stand in for a live price near
  a stop. With no cache either, the error says: run `python3
  trader-data/data.py doctor`; or call Kite MCP `get_historical_data` and
  `data.py import-kite`.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. Zerodha's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from the Kite connector (a quote / LTP
  tool) and write it into `trader/prices.json` as
  `{"as_of": "...Z", "prices": {"HINDCOPPER": 324.85}}`.

## The NSE clock

Normal market 09:15 to 15:30 IST (UTC+05:30, no daylight saving). Pre-open
09:00-09:08 is order collection only and is not treated as open. No early
closes. Closed on weekends and on the NSE equity holidays for 2024, 2025 and
2026 (built in; the same calendar block sits in every script that needs it).
2026: Jan 15 Maharashtra municipal elections, Jan 26 Republic Day, Mar 3 Holi,
Mar 26 Shri Ram Navami, Mar 31 Shri Mahavir Jayanti, Apr 3 Good Friday,
Apr 14 Ambedkar Jayanti, May 1 Maharashtra Day, May 28 Bakri Id, Jun 26
Muharram, Sep 14 Ganesh Chaturthi, Oct 2 Gandhi Jayanti, Oct 20 Dussehra,
Nov 10 Diwali Balipratipada, Nov 24 Guru Nanak Jayanti, Dec 25 Christmas.

Other years come from `trader/data/calendar.json`, which trader-data keeps
current (`python3 trader-data/data.py calendar`). When the file exists its
holidays and special sessions are merged over the built-in tables, and a year
it lists is treated as fully known. A missing or unreadable file means the
built-in tables only, never an error. For a year with no calendar the clock
says "holiday list covers 2024, 2025, 2026 only: check nseindia.com, or run python3
trader-data/data.py calendar".

Special sessions are not the same as closures. A session on a weekend or
holiday with normal hours (Budget day, a Saturday session) counts as a
trading day, and the clock adds a "Special session today" line. The Muhurat
session (Diwali Laxmi Pujan, Sunday 8 Nov 2026; about an hour in the evening,
timings by NSE circular) is reported as a special session, not as a plain
holiday: the regular 09:15-15:30 market is closed that day, so price alerts
and the clock treat it as closed and say "regular market closed, special
session: ...". Alert times are not tracked for the Muhurat hour.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
   A plan from trader-exit with `"product": "CNC"` (a delivery hold) has no
   flat-by time: it gets only the stop and the target alert, and you confirm
   those two.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means an
   exit has probably filled: check `get_positions` / `get_orders`, record the
   fill with trader-broker, and cancel the other exit. "Flat by 15:15" means
   close anything still open: trader-broker `flatten`, then trader-risk, then
   the user's yes. Never act for them.

## Example: the reel

HINDCOPPER on Monday 2026-10-19: entry 314.30, stop 308.10, target 320.50,
flat by 15:15. `add --from-plan` writes `hindcopper-plan-stop`,
`hindcopper-plan-target` and `hindcopper-flat-by` for that day. At 15:15:

```
NSE OPEN (2026-10-19 15:15 IST, normal market).
ALERT Flat by 15:15: it is 15:15 IST (set for 15:15), the market closes at 15:30. If HINDCOPPER is still
open, close it: trader-broker flatten --symbol HINDCOPPER builds the SELL ticket, then trader-risk and
your yes. Zerodha squares off open MIS positions around 15:20.
```

It rings once. The next trading day (Oct 20 is Dussehra, so Oct 21) the
plan's alerts are no longer watched; with `add --opening-bell`, the bell
rings at 09:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level in ₹, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper by default (`"paper": true`): tickets are logged, never sent. Going live is the user's own decision,
  in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, margin/leverage or short selling unless the config allows it and the user confirms.
- Kite login happens in the browser via Kite's OAuth; credentials never go in the chat.
