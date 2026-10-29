---
name: trader-scanner
description: >-
  Chart Scanner for NSE stocks. Scans hundreds of charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the symbol list
  from the Nifty 500 constituents (niftyindices.com) or, optionally, every
  EQ-series stock in NSE's EQUITY_L.csv, runs a pre-market scan of daily
  closes (20-day closing-high breakout, bull flag, reclaimed the 20-day
  average, oversold, oversold bounce) and an intraday opening-range breakout
  scan on 5-minute bars from the 09:15 IST open, then writes
  trader/watchlist.json for trader-news and trader-entry. Also a long-term
  screen of the trader-data daily cache (above the 200-day average, near the
  52-week high, near the 52-week low as a value screen, RSI). Free Yahoo data
  (SYMBOL.NS), bar by bar, with the trader-data cache as the fallback when
  Yahoo or NSE block the download. Use when the user says "scan the market", "find
  me setups", "what is breaking out on NSE", "opening range breakouts", "scan
  the Nifty 500", "scan for bull flags", "oversold stocks", "pre-market scan",
  "build my watchlist", "what should I look at today", "stocks above the
  200 DMA", "near 52-week high", "52-week low value stocks", or at the start
  of a trading day before /trader-news.
argument-hint: "[universe | daily | orb | longterm] [--at HH:MM] [--cache]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads hundreds of
free NSE charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and
a setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
500 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # Nifty 500 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --source equity       # every NSE EQ-series stock
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,HDFCBANK,TCS   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:00                 # cut the clock at 10:00 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --cache                  # closes from trader/data/daily, no Yahoo
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --cache                    # bars from trader/data/intraday, no Yahoo
python3 "${CLAUDE_SKILL_DIR}/scan.py" longterm                       # long-term screen -> trader/longterm.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" longterm --screen near_52w_low --near-low 10
```

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 500,000, orb 0),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:00"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason is
one plain line).

Symbols are NSE symbols (RELIANCE, M&M, BAJAJ-AUTO, HINDCOPPER, BEL); the
scanner asks Yahoo for `SYMBOL.NS` (with `&` sent as `%26`) and drops a
`.NS` the user typed. F&O contracts are refused: cash equities only.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 15:00 IST) |
| `trader/data/daily/<SYMBOL>.csv` | read by `daily --cache`, `longterm`, and `daily` when Yahoo fails (written by trader-data; `<SYMBOL>.adj.csv` wins when present) |
| `trader/data/intraday/<SYMBOL>-5m.json` | read by `orb --cache` and `orb` when Yahoo fails (written by trader-data, Yahoo v8 shape) |
| `trader/data/universe.csv` | read by `universe` when niftyindices.com refuses (written by trader-data) |
| `nifty500-snapshot.csv` (+ `.json`) | bundled with this skill: the Nifty 500 list as of 2026-07-13, the last fallback |
| `trader/longterm.json` | written by `longterm`: same shape as the watchlist, `mode: "longterm"`, per hit the 200-day average, 52-week high and low and the distance from each |

## The universe

`universe` writes the list the other two commands scan.

1. Default, `--source nifty500`: niftyindices.com's
   `ind_nifty500list.csv` (column `Symbol`). 500 large, mid and small caps
   that trade enough for a 5-minute chart to mean something.
2. `--source equity`: NSE's `EQUITY_L.csv` from archives.nseindia.com, every
   listed equity, keeping `SERIES == EQ` only (no SME, BE trade-for-trade or
   other series). NSE often refuses scripted downloads; when it does, the
   scanner says so and falls back to the Nifty 500 list.
3. Every symbol must look like an NSE symbol: A to Z, 0 to 9, `&` and `-`, up
   to 20 characters. Anything else is dropped and counted.

Both downloads send a browser-like User-Agent; both sites block plain
scripts. When niftyindices.com refuses too (or with `universe --cache`), the
scanner reads `trader/data/universe.csv` (trader-data; any CSV with a
`Symbol`, `SYMBOL` or `symbol` column), then the bundled offline snapshot
`nifty500-snapshot.csv` next to scan.py (501 rows, NSE Indices'
`ind_nifty500list.csv` as of 2026-07-13, from a GitHub mirror; the date and
source are in `nifty500-snapshot.json`). It says which list it used. The
snapshot goes stale at each semi-annual rebalance (March, September): say so
and re-run `universe` when the site answers. With no `trader/universe.txt`
at all, `daily` and `orb` scan the same fallback list and say so.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so the Nifty
500 takes 25 requests and about 15 seconds with the polite pause (0.3 s).
Liquidity comes from the spark meta: last price at least `--min-price`
(₹50) and last session's volume at least `--min-volume` (5,00,000 shares).
Run it before the 09:15 open or after the 15:30 close: during the session
the volume is the day's so far, and today's daily bar is left out until
15:30 IST.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume, in rupees).

## The long-term screen (no network)

`longterm` reads the trader-data daily cache, `trader/data/daily/<SYMBOL>.csv`
(the split and bonus adjusted `.adj.csv` when there is one), for the
universe, `--symbols`, `--watchlist`, or every stock in the cache when there
is no universe file. A stock needs at least 200 sessions. Per stock:

| number | how |
| --- | --- |
| 200-day / 50-day average | the mean of the last 200 / 50 closes |
| 52-week high / low | the highest high / lowest low of the last 252 sessions |
| off the high | last close / 52-week high - 1, in percent (0 or below) |
| over the low | last close / 52-week low - 1, in percent |
| RSI 14 | Wilder, on the last 120 closes |

| screen | the rule |
| --- | --- |
| `above_200dma` | the last close is above the 200-day average |
| `near_52w_high` | within `--near-high` (5) percent of the 52-week high |
| `near_52w_low` | within `--near-low` (10) percent above the 52-week low: a value screen, cheap is not the same as good |
| `oversold` | RSI 14 under 30 |

`--screen NAME` keeps one screen (default: a stock that matches any).
Liquidity: last close at least `--min-price` (₹20) and a 20-day average
volume of at least `--min-volume` (1,00,000 shares). Hits are listed closest
to the 52-week high first (closest to the low first for `near_52w_low`) and
written to `trader/longterm.json`. The cache is as fresh as the last
`python3 trader-data/data.py daily` run; the screen prints the dates it
used and warns when the newest close is more than 5 days old. A screen is a
starting list for reading annual reports and filings, not a list of stocks
to buy. Not financial advice.

## The opening-range breakout scan (during the session)

The NSE session runs 09:15 to 15:30 IST: 75 five-minute bars. The opening
range is the first `--or-minutes` (15) of the session: the 9:15, 9:20 and
9:25 five-minute bars. A hit is the first completed 5-minute bar to CLOSE
above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt, a stock locked at its circuit limit, or a
thin stock. Null closes are dropped, a bar off the 5-minute grid (the live
tick, like 10:43:07) is dropped, and a bar counts only once it is complete
(start + 5 minutes). Before 9:35 AM, on a weekend or NSE holiday, after the
close, or after `no_entries_after` (default 15:00, before the broker's ~15:20
MIS square-off) it prints one plain line and writes nothing.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query1 first, query2 on a
  429, a 5xx or a network error, one retry after a 5-second wait on a 429, a
  browser User-Agent.
- When Yahoo fails for a symbol, `daily` reads `trader/data/daily/<SYMBOL>.csv`
  and `orb` reads `trader/data/intraday/<SYMBOL>-5m.json` (both written by
  trader-data), and the scan prints "using cached data from <time> ... run
  `python3 trader-data/data.py daily|intraday SYMBOL` to refresh". `--cache`
  skips Yahoo. With no cache either it stops with the way out: run
  `python3 trader-data/data.py doctor`; or, when the Kite MCP is connected,
  call `get_historical_data`, save the JSON and run `data.py import-kite`.
  Say which data the scan used: Yahoo, the cache, or both.
- Partial results are said as partial: "Fetched 480 of 500 charts (20 failed
  or had no data)". Never round a partial scan up to "the whole market".
- Times are IST (UTC+05:30, no daylight saving). NSE equity holidays are
  built in for 2026 only; for any other year the scanner warns "holiday list
  covers 2026 only: check nseindia.com". NSE has no early closes. The Diwali
  Muhurat session (a Sunday, timings by circular) is treated as closed.
- Circuit limits: NSE stocks have daily price bands (2, 5, 10 or 20 %). A
  breakout into the upper circuit may not be fillable; say so.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 is a common time): `scan.py orb`. Tell the user
   how many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is
   moving it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick
   for them.
5. If a scan stops because Yahoo or NSE refused: if the Kite MCP tools are in
   the session, call `get_historical_data` (or `get_ohlc`), save the raw JSON
   and run `python3 trader-data/data.py import-kite FILE --kind historical --symbol SYMBOL`; otherwise run
   `python3 trader-data/data.py doctor`. Then re-run the scan with `--cache`.
6. For long-term ideas (no day trade): `scan.py longterm` after a
   `trader-data daily` refresh. Give the counts, the dates of the closes, and
   the hits with their distance from the 52-week high and low; never call a
   52-week-low stock "cheap enough to buy".

## Example

Monday Oct 19 2026, scan at 10:00 AM IST over the test fixture's 234-chart
wall (`tests/fixtures/trader-scanner/wall5m-2026-10-19.json.gz`: SYNTHETIC
charts with NSE names, not real NSE prices):

```
python3 scan.py orb --offline wall5m-2026-10-19.json.gz --now "2026-10-19 10:00" --min-price 0
Fetched 233 of 234 charts (1 failed or had no data); 231 usable; 95 candidates, 95 confirmed with real highs and lows.
42 of 231 charts closed above their opening-range high by 10:00 AM:
   9:35 AM  BLUESTARCO        ₹158.55  range 156.79 to 158.22  +0.21% over
  ...
  10:00 AM  RELIANCE          ₹364.18  range 354.47 to 362.18  +0.55% over
```

RELIANCE's range is the 9:15 to 9:25 bars, high 362.18, low 354.47; the 9:55
bar closed at 364.18, 0.55 % over the high, at 10:00 AM. The user picks
RELIANCE and Claude hands it to `/trader-news RELIANCE`, then
`/trader-entry RELIANCE`.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data (IST).
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

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
