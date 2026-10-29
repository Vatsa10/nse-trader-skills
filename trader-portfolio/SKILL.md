---
name: trader-portfolio
description: >-
  Long-term holdings manager for an NSE delivery (CNC) portfolio. Imports
  your Zerodha holdings from the Kite MCP get_holdings tool or a Kite Console
  holdings or tradebook CSV (FIFO lots with buy dates, T1 shares included),
  then shows value, P&L, weights, day change and when each lot turns
  long-term; checks concentration (single stock, sector, theme, promoter
  group such as Reliance/Jio, Tata, Adani, Bajaj, PSU share, gold and silver
  ETF share, no broad index); proposes whole-share CNC rebalance and monthly
  SIP orders with Zerodha delivery charges and a FIFO tax estimate; and
  estimates realised STCG/LTCG for a financial year with the ₹1.25 lakh
  exemption and tax-harvesting ideas. Use when the user says "my holdings",
  "my portfolio", "am I too concentrated", "rebalance", "how much gold do I
  have", "monthly SIP of ₹5,000", "LTCG", "tax harvesting", "when does this
  become long-term", or pastes a Console export.
argument-hint: "[import FILE | show | risk | rebalance | tax | sip]"
---

# trader-portfolio

The long-term side of the pack. trader-budget sizes one day trade; this skill
looks after what you already own: what it is worth, how lopsided it is, what
moving it toward your own target mix would cost in charges and tax, and what
a fixed monthly amount buys in whole shares. It proposes orders only.
trader-risk checks them and trader-broker asks the user yes or no.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" import trader/kite-holdings.json        # Kite MCP get_holdings, raw
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" import holdings.csv tradebook.csv       # Kite Console exports
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" show
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" risk
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" rebalance --targets trader/targets.json --cash 5000
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" rebalance --targets trader/targets.json --cash 5000 --write
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" tax --fy 2026-27
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" sip --monthly 5000 --targets trader/targets.json --write
```

Every command takes `--json`, `--dir` (the `trader/` folder) and `--today
YYYY-MM-DD` (IST, for replaying a date).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/holdings.json` | written by `import`: `{"as_of","source","cash","holdings":[{"symbol","qty","avg_price","last_price","buy_date","product":"CNC","t1_qty","lots":[...]}],"realised":[...]}` |
| `trader/data/fundamentals/<SYM>.json` | sector, when trader-invest has fetched it |
| `trader/data/daily/<SYM>.csv` | last close, when a price is missing (trader-data) |
| `trader/prices.json` | optional prices for symbols you do not hold yet (`--prices`) |
| `trader/charges.json` | optional charges override, calibrated by trader-broker reconcile |
| `trader/orders.json` | proposed CNC orders, only with `--write` |
| `trader/sip.json` | the SIP carry ledger, only with `sip --write` |
| `groups.json` (this folder) | promoter groups, PSU list, ETF asset classes, themes, fallback sectors |
| `targets.example.json` (this folder) | a target mix to copy into `trader/targets.json` and edit |

## Step 1: get the holdings in

Best path, Kite MCP: if the Zerodha Kite MCP tools are connected in this
session, call `get_holdings`, save the raw JSON it returns to
`trader/kite-holdings.json` exactly as it came, then run `import` on it. The
script never needs keys or tokens. `quantity` plus `t1_quantity` is what you
own: T1 shares are bought but not yet in the demat, shown with a `*`.

Without the MCP: Kite Console > Portfolio > Holdings > Download (XLSX: open
it and save as CSV), and for buy dates Console > Reports > Tradebook
(Equity, CSV, from your very first buy). Import both in one command:

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" import holdings.csv tradebook.csv
```

The tradebook is replayed first in, first out (the order Indian tax uses for
demat shares) into lots with buy dates and into realised sales for `tax`. If
the lots do not add up to the holding (bonus, split, transfer in, a tradebook
that starts too late) it says so. Importing a holdings snapshot later keeps
the lots and realised sales from an earlier tradebook import (`--fresh` to
start over). `--cash N` records free cash.

## Step 2: show and risk

`show` prints quantity, average, last price, value, P&L, weight and day
change, then every lot with the date it turns long-term (held more than 12
months: bought 2025-10-01, long-term from 2026-10-02).

`risk` checks, with defaults you can change:

| check | default |
| --- | --- |
| single stock (stocks and sector ETFs) | over 15% (`--max-stock`) |
| sector and theme (Defence, Metals and mining, Railways...) | over 35% (`--max-sector`) |
| promoter group (Reliance incl. JIOFIN and the Jio Platforms listing, Tata, Adani, Bajaj, Birla...) | over 25% (`--max-group`) |
| PSU share (HINDCOPPER, IOC, HAL, BEL, ONGC, COALINDIA, NTPC...) | over 35%, always reported |
| gold and silver ETFs (GOLDBEES, TATAGOLD, SILVERBEES, TATSILV...) | over 30% (`--max-commodity`) |
| broad index ETF (NIFTYBEES, JUNIORBEES, MID150BEES...) | none at all |

Sector comes from `trader/data/fundamentals/<SYM>.json` first, then the
Console CSV's sector column, then `groups.json`. A Tata Mutual Fund ETF is not
Tata group stock: only stocks count toward a group. Say each warning in one
plain line. They are rules of thumb, not a verdict on any stock.

## Step 3: rebalance (proposal only)

Copy `targets.example.json` to `trader/targets.json` and ask the user for
their own symbols and weights. Do not suggest a mix as advice; the example is
a format. Fields: `weights` (renormalised to 100), `cash_pct`,
`min_trade_inr` (default ₹500), `others` (`hold` leaves holdings not in the
list alone; `sell` targets them at 0).

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" rebalance --targets trader/targets.json --cash 5000
```

Whole shares only. Sells come first, then buys from the cash plus the sale
proceeds, largest shortfall first, never spending more than there is. T1
shares are never sold. Charges are Zerodha delivery from the shared model:
₹0 brokerage, STT 0.1% on both sides, stamp 0.015% on buys, NSE txn
0.00307%, SEBI ₹10 a crore, GST 18%, and DP ₹15.34 once per scrip sold that
day, labelled "estimate" (or calibrated from N contract notes).

Tax-aware (the default, `--tax-mode aware`): India taxes demat sales FIFO,
so you cannot pick a lot. Instead the sale stops before the first FIFO lot
that would realise a short-term gain, and says when that lot turns
long-term. Long-term lots and losses sell. `--tax-mode ignore` sells to the
target regardless. The tax estimate is this financial year's tax with the
sales minus without them, after loss set-off and the exemption.

Show the table (now, target, after), the orders, the charges and the tax
line. Then, only if the user wants it, add `--write`: the orders go to
`trader/orders.json` in the trader-risk order format (`symbol`, `side`, `qty`,
`product` "CNC", `type` "market" or "limit" with `limit_price` when
`--limit-pct` is given, `time_in_force` "day", plus a `why` note). Hand over
to `/trader-risk`, then `/trader-broker`, which asks the user to say yes to
each order.

## Step 4: tax

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" tax --fy 2026-27 --slab 30
```

Realised gains from the tradebook for 1 April to 31 March, split into
equity short-term (20%), equity long-term (12.5% above ₹1.25 lakh a year),
gold/silver ETF short-term (your slab) and gold/silver ETF long-term (12.5%,
no exemption). Short-term losses offset short and long-term gains;
long-term losses only long-term gains; the rest carries forward (eight years,
only if the return is filed on time). Plus 4% cess.

Ideas it lists: book long-term equity gains up to the unused ₹1.25 lakh by
selling and buying back (the cost resets higher), losses that could offset
this year's taxable gains, and short-term lots within 60 days of turning
long-term. Always present it as "an estimate, check with a CA": charges,
grandfathering for shares bought before 1 Feb 2018, bonus and split
adjustments, surcharge and your other income are not in it.

## Step 5: SIP

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" sip --monthly 5000 --targets trader/targets.json --write
```

Each symbol gets its weight of the month's amount plus what it carried from
last month, buys the whole shares that fit with charges, and carries the
rest. An expensive share (HAL at about ₹4,600 on ₹750 a month) is bought
when its pot is big enough. `--write` writes the orders and the carry ledger
`trader/sip.json`; run it once a month. Running twice in a month warns.

## Example: the sample portfolio

TATAGOLD 675 @ 14.82, TATSILV 442 @ 22.60 (50 of them T1), HINDCOPPER 10 @
472.35, HAL 1 @ 4,800 (fixture prices, not market data). `risk`: gold and
silver are 72% (warn), HAL is 15.5% (warn), no broad index (warn), PSU 27.7%.
`rebalance` toward the example targets with ₹5,000: sell 300 TATAGOLD (the
long-term lot from 2025-08-20), hold back the 2026-04-10 lot and all of
TATSILV (short-term gains until 2027-04-11 and 2027-03-06), buy NIFTYBEES.

## Rules

- Proposals only. This skill never calls a broker tool and never places, modifies or cancels an order.
- Orders reach `trader/orders.json` only with `--write`, after the user has seen the table.
- Every price comes from the import, `--prices`, the daily cache or the targets file. Nothing is guessed;
  a missing price is an error.
- No projections: never say what a holding will return.
- Kite Console and get_holdings prices can lag. The exchange price is the one that fills.

## Safety

- Education and tooling, not financial advice. Tax figures are an estimate: check with a CA.
- Nothing is placed by this skill. trader-broker places an order only when trader-risk passed that exact
  order today and the user typed an explicit yes for that one order in the chat.
- Never set an order tool to "Always allow". Kite login is OAuth in the browser; no keys or tokens in the
  chat.
