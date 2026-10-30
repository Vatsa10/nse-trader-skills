---
name: trader-broker
description: >-
  Order Executor for NSE via Zerodha Kite. Sends approved orders to your
  Kite account, paper mode (logged only, never sent) by default, never
  without your yes. Shows each order as a plain ticket (a bracket shows its
  stop-loss, target and the max loss at the stop in ₹) plus the exact JSON
  for the Kite MCP place_order tool, records your yes word for word, then
  Claude's own Allow once / Deny card asks again. Zerodha has no bracket
  orders, so the exits are separate SL-M and LIMIT orders (MIS) or one GTT
  OCO (CNC). Builds the SELL ticket that closes an intraday position by your
  flat-by time (default 15:15 IST, before the ~15:20 MIS auto square-off),
  records fills, and logs each trade's max loss so trader-risk can hold the
  daily limit. Shows today's circuit band on each ticket, and reconciles the
  charge estimates with your Zerodha tradebook and contract notes to
  calibrate them. Covers connecting Zerodha's official hosted Kite MCP server
  (OAuth login in the browser, no keys in chat) and setting order tools to
  "Needs approval". Use when the user says "place the order", "buy it",
  "execute", "send it to Zerodha", "close my position", "square off",
  "flatten", "get me out", "connect Kite", "reconcile charges", "contract
  note", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches NSE only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the Kite order tools are set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

**Paper is the default.** Kite has no paper-trading account, so here "paper"
means the ticket is approved and logged (`result --status paper`) and
`place_order` is **never called**. Only the user can switch `"paper": false`
in their own `trader/config.json`.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status paper --filled-qty 40 --filled-price 314.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id 260119000123456   # live only
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol HINDCOPPER --side sell --qty 40 --price 320.50
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol HINDCOPPER --qty 40
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-10-19 10:15"` pins the clock (IST,
UTC+05:30). Whole shares only; NSE cash equity only (no F&O, no BSE).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today (IST) is READY |
| `trader/state/broker.json` | every yes, no, paper, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash (₹) and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

An order in `trader/orders.json` uses the pack's shared schema (trader-budget
writes it, trader-risk fingerprints it, `ticket.py` reads it):

```json
{"symbol": "RELIANCE", "side": "buy", "qty": "4", "product": "MIS", "type": "market",
 "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "1472.30",
 "stop_loss_stop_price": "1432.30"}
```

`ticket.py` only translates it to Kite JSON when it renders the ticket:
`type` market / limit / stop / stop_limit becomes `order_type` MARKET / LIMIT /
SL-M / SL (`limit_price` -> `price`, `stop_price` -> `trigger_price`),
`time_in_force` day / ioc becomes `validity` DAY / IOC, and a bracket's
`stop_loss_stop_price` / `take_profit_limit_price` become the separate exit
orders. `product`: `MIS` (intraday, auto square-off around 15:20 IST) or
`CNC` (delivery, settles T+1). `why` and `plan` are never sent.

## Setup: Zerodha's official Kite MCP server

Zerodha hosts the server at `https://mcp.kite.trade/mcp`. Login is Kite's
own OAuth in the browser; **there are no API keys to paste anywhere**.

1. The user needs a Zerodha account with Kite access, and Node.js (for `npx`).
2. In Claude Desktop: Settings > Developer > Edit Config. That opens
   `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
   Windows: `%APPDATA%\Claude\`). Add this block (also in
   `claude_desktop_config.example.json` here):

```json
{
  "mcpServers": {
    "kite": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.kite.trade/mcp"]
    }
  }
}
```

3. Restart Claude Desktop. The first Kite tool call returns a login link;
   the user opens it and logs in to Kite **in their browser**. The session
   lasts for the trading day; log in again the next day.
   **Credentials never go in the chat.** Never ask for the Kite password,
   TOTP/2FA code, PIN or request token. If the user pastes one anyway, tell
   them to delete that message and change it on kite.zerodha.com.
4. **Set the tool permissions.** Customize > Connectors (Settings >
   Connectors in older versions), open kite, and for each tool:
   - `place_order`, `modify_order`, `cancel_order`, `place_gtt_order`,
     `modify_gtt_order`, `delete_gtt_order`: **Needs approval**. Never Always allow.
   - read-only tools (`get_holdings`, `get_positions`, `get_orders`,
     `get_trades`, `get_order_history`, `get_gtts`, margins, quotes): Always
     allow is fine.
5. Check it: ask Claude for the Kite holdings and positions (read only) and
   confirm they match the Kite app.

In Claude Code instead of Claude Desktop, keep the order tools on ask in
`.claude/settings.json`, e.g. `"permissions": {"ask": ["mcp__kite__place_order",
"mcp__kite__modify_order", "mcp__kite__cancel_order", "mcp__kite__place_gtt_order",
"mcp__kite__modify_gtt_order", "mcp__kite__delete_gtt_order"]}`, and never put
an order tool in `allow`.

**"Brackets" on Zerodha.** Zerodha discontinued bracket orders, so a bracket
here is separate orders, each with its own Allow once card:

- **MIS (intraday):** the entry, then after it fills an `SL-M` exit at the
  stop (`trigger_price`) and a `LIMIT` exit at the target. These two do
  **not** cancel each other: when one fills, cancel the other with
  `cancel_order`, or it can open a new position. Both are DAY orders.
- **CNC (delivery):** the entry, then one GTT OCO (`place_gtt_order`,
  `trigger_type` "two-leg") with the stop and the target; one leg cancels
  the other. A GTT is checked by Zerodha against the last price and can fill
  worse on a gap.

Stocks have daily circuit limits (2/5/10/20 %). Each ticket shows the real
band from `trader/data/bands.json` (`--bands`): "circuit band today: 1306.80
to 1597.20 (10% band, as of 2026-10-19, ...)". trader-data writes that file
(`trader-data bands`, or save the Kite MCP `get_quotes` result, which carries
each stock's lower and upper circuit limit, and run `trader-data
import-kite`). A limit, trigger, stop-loss or target outside the band is
flagged (trader-risk blocks it: the exchange would reject it). A stop below
the lower circuit may not fill at all while the stock is locked there; the
ticket says so. With no band data the ticket says "circuit band unknown: run
trader-data bands"; tell the user the stop may not fill past the band.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "haan", "yes to all", or a yes that names a
   different stock or amount). Ask again. Never turn a reply into a yes yourself.
4. **Paper (default):** do not call any Kite order tool. Record
   `ticket.py result --index N --status paper` (with `--filled-qty` and
   `--filled-price` at the quoted price if the user wants the paper portfolio
   updated).
   **Live:** only after "Recorded your yes", call `place_order` with exactly
   the JSON the ticket printed. Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <Kite order_id> --filled-qty Q --filled-price P` (fill from
   `get_order_history` or `get_trades`), or `--status denied`, or `--status
   failed` with `--note`. A failed order is not retried without a new check
   and a new yes.
6. For a bracket, `result` prints the exit calls. Place each (live) with its
   own Allow once card.

## Steps: while it is open, and out by 15:15

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`).
- When the stop or the target fills, record it:
  `ticket.py fill --symbol HINDCOPPER --side sell --qty 40 --price 308.10`,
  and cancel the other exit order.
- By `flat_by` (default 15:15 IST), anything still open gets closed:
  1. Ask Kite how many shares are held: `get_positions` (read only).
  2. `ticket.py flatten --symbol HINDCOPPER --qty 40` writes a MARKET SELL
     (MIS) for those shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. Cancel the open SL-M and LIMIT exits first (`get_orders`, then
     `cancel_order` each, Needs approval), then `place_order` with the ticket,
     then `result`.
- If nothing is done, Zerodha squares off open MIS positions around
  15:20 IST and charges for it. Do not rely on that.

Intraday short selling is only possible as MIS and is squared off the same
day; there is no overnight short in cash equity. This pack blocks shorts
unless the user's config allows them.

## Example: the reel

```
ORDER 1 of 1, PAPER mode (logged only, never sent), Zerodha Kite
  BUY 40 HINDCOPPER on NSE, market, MIS, good for today
  stop-loss 308.10
  target 320.50
  max loss about ₹248.00 if the stop fills at 308.10 plus about ₹13.20 charges, estimate (it can fill
  worse in a fast market, or not at all past the lower circuit)
  why: opening-range breakout on HINDCOPPER: 40 shares, bound by your ₹250 max loss per trade
  circuit band today: no price band (F&O stock: the exchange's dynamic price limits apply; as of 2026-10-19)
  READY: trader-risk PASS, waiting for your yes
  Paper: Claude would call place_order with exactly:
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "HINDCOPPER", "transaction_type": "BUY",
     "quantity": 40, "product": "MIS", "order_type": "MARKET", "validity": "DAY"}
  Zerodha has no bracket orders. After the entry fills, the exits are separate calls ...
```

The user replies "yes". In paper mode Claude records it and logs it; nothing
is sent. Live, Claude calls `place_order`, and the card reads "Claude wants
to use place_order" with that JSON and two buttons: Allow once, Deny. The
`why` and the plan stay in the file; they are never sent.

## Reconcile charges with your contract notes

Every charge in this pack (trader-budget, trader-risk, these tickets) is an
estimate: Zerodha rates verified 2026-10-29 from zerodha.com/charges,
deep-merged with `trader/charges.json` if present, times a calibration
factor. Calibrate it from the user's own contract notes:

1. The user downloads the tradebook CSV from Console (Reports > Tradebook):
   `trade_date, symbol (or tradingsymbol), exchange, segment, trade_type,
   quantity, price, order_id, ...`. F&O rows are skipped.
2. They give each day's charges from the contract note, typed or exported,
   as CSV or JSON rows with `date, brokerage, stt, exchange_txn, sebi,
   stamp, gst, dp, total` (total alone is enough), or a simple JSON
   `{"2026-10-19": 22.05}`.
3. Run `ticket.py reconcile tradebook.csv --actual charges.csv`. Per day and
   symbol, shares bought and sold the same day count as intraday, the rest
   as delivery (with the DP charge once per scrip per sell day); brokerage
   is per order_id. It prints estimated vs actual per day with the diff and
   the ratio, and writes `calibration` = {factor: the median of actual /
   estimated over every reconciled day, samples, updated, history} into
   `trader/charges.json`, keeping any rate overrides already there. A day
   whose ratio is outside 0.5 to 2 is left out with a note (wrong day, or
   F&O charges mixed in). Reconciling the same day again replaces it.
4. `ticket.py reconcile --show` prints the rates in use and the calibration.

This file is local state, so reconcile writes it without asking. Afterwards
every output says "estimate (calibrated from N contract notes)". The
contract note stays the real number.

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never calls an order tool in paper mode.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats Kite passwords, TOTP codes, PINs or tokens.
- Never switches `"paper"` to live. The user does that themselves.
- Never trades F&O, never uses MIS leverage beyond what the config allows, never shorts on its own.
- Never promises a fill price: a market order and an SL-M fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper by default (`"paper": true`): tickets are logged, never sent. Going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, margin/leverage or short selling unless the config allows it and the user confirms.
- Kite login happens in the browser via Kite's OAuth; credentials never go in the chat.
- Charges shown are an estimate (Zerodha brokerage, STT, exchange, SEBI, GST, stamp duty, DP), calibrated
  from the user's contract notes once `ticket.py reconcile` has run; the contract note is the real number.

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
