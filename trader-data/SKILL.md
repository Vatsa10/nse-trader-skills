---
name: trader-data
description: >-
  Market Data for NSE, the data layer the other trader skills read. Fills a
  daily OHLC cache, a 5 minute intraday cache and a price band file for any
  NSE symbol, and keeps working when free sources block you: it tries Yahoo
  (query1, then query2), then the NSE website API with a cookie-primed
  browser session, then the NSE bhavcopy archives one trading day at a time,
  and says exactly which source failed and why ("403: NSE blocks this IP").
  When nothing free works, it normalises what Claude pulls from Zerodha's
  official Kite MCP (get_historical_data, get_quotes, get_ohlc) or a CSV the
  user downloaded from Kite Console or nseindia.com. Validates every write
  (OHLC sanity, no duplicates, ascending dates, gaps against the NSE trading
  calendar), fetches only missing dates, probes every source with `doctor`,
  and records real data as test fixtures. Use when the user says "get me
  RELIANCE history", "update my data", "download prices", "Yahoo is not
  working", "NSE blocked me", "what is the price band / circuit limit",
  "import this CSV", "is my data fresh", or before a backtest, an
  investment check or a scan that needs daily bars.
argument-hint: "[daily|intraday|bands|doctor|status] [symbol]"
---

# trader-data

Market Data owns the price files every other skill reads. It never trades
and never gives a view on a stock: it fetches, checks and files numbers.
Free sources are unofficial, delayed and get blocked; the script assumes
that and falls back, and the Kite MCP path is the reliable one.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/data.py" doctor                              # which sources work from here
python3 "${CLAUDE_SKILL_DIR}/data.py" daily RELIANCE TCS --years 3         # auto: yahoo -> nse -> bhavcopy
python3 "${CLAUDE_SKILL_DIR}/data.py" daily RELIANCE --source bhavcopy     # force one source
python3 "${CLAUDE_SKILL_DIR}/data.py" daily RELIANCE --fill-gaps           # also refetch holes
python3 "${CLAUDE_SKILL_DIR}/data.py" intraday RELIANCE --days 60          # 5 minute bars
python3 "${CLAUDE_SKILL_DIR}/data.py" bands RELIANCE HINDCOPPER            # price bands -> bands.json
python3 "${CLAUDE_SKILL_DIR}/data.py" import-kite kite-hist.json --kind historical --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/data.py" import-kite kite-quotes.json --kind quotes
python3 "${CLAUDE_SKILL_DIR}/data.py" import-kite kite-ohlc.json --kind ohlc --date 2026-10-23
python3 "${CLAUDE_SKILL_DIR}/data.py" import-csv ~/Downloads/RELIANCE.csv --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/data.py" status                              # cached, fresh, gaps
python3 "${CLAUDE_SKILL_DIR}/data.py" record RELIANCE --date 2026-10-23   # real data -> test fixtures
```

Every command takes `--data DIR` (default `trader/data`) and `--now
"YYYY-MM-DD HH:MM"` (IST) to pin the clock.

## What it writes

| file | what | read by |
| --- | --- | --- |
| `trader/data/daily/<SYM>.csv` | `date,open,high,low,close,volume`, ascending, unadjusted | invest, backtest, portfolio, scanner |
| `trader/data/daily/<SYM>.adj.csv` | same header, split and bonus adjusted | same |
| `trader/data/intraday/<SYM>-5m.json` | Yahoo v8 chart JSON shape, 09:15-15:25 bars, last 60 sessions | entry, timemachine, scanner, alerts, exit |
| `trader/data/bands.json` | `{"as_of","source","bands":{SYM:{"band_pct","lower","upper"}},"unknown":[...]}` | risk, exit, broker |
| `trader/data/sources.json` | doctor's result per source: ok, latency, error, checked_at | everyone, to pick a source |
| `trader/data/cache_meta.json` | which source filled each file and when | status, record |

`band_pct` is `null` for a stock with no band (F&O stocks; they still have
dynamic circuit limits in `lower` / `upper`). A symbol whose band could not
be found goes in `"unknown"`, never into `bands` with a guess.

## Sources, in order

Daily (`--source auto`):

1. Yahoo `query1` then `query2`: `/v8/finance/chart/<SYM>.NS?interval=1d&period1=..&period2=..&events=div,split`
   with a browser User-Agent; each host gets two retries with a 2 s, then 4 s
   back-off on a 429 or 5xx. Yahoo's OHLC is split and bonus adjusted, so it
   goes to `.adj.csv` as is, and the unadjusted `.csv` multiplies back each
   split after the date (Yahoo shows a 1:1 bonus as a 2:1 split).
2. NSE JSON API: GET `https://www.nseindia.com/` first for cookies (browser
   headers incl. Accept-Language and Referer), then
   `/api/historical/cm/equity?symbol=X&series=["EQ"]&from=dd-mm-yyyy&to=dd-mm-yyyy`
   in 100 day chunks. Unadjusted.
3. NSE bhavcopy archives: `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`
   (then the UDiFF `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`), one file
   per NSE trading day, one a second, weekends and holidays skipped, each day
   fetched once for all symbols in the run, at most `--max-days` (300) per run.

Intraday: Yahoo `interval=5m&range=60d`, then NSE
`/api/chart-databyindex?index=<SYM>EQN` (today only, ticks bucketed into 5
minute bars, no volume). Bands: NSE `/api/quote-equity?symbol=X`
(`priceInfo.pPriceBand`, `lowerCP`, `upperCP`), then
`nsearchives .../content/equities/sec_list.csv` (Band column, no limits).

Only dates after the last cached session are fetched. The last complete
session is today after 18:00 IST on a trading day (bhavcopy is published in
the evening), else the trading day before.

## When free sources fail: Kite MCP (the reliable path)

Zerodha's official Kite MCP (`https://mcp.kite.trade/mcp`) works wherever the
user's Kite login works, needs no keys in this script, and is read-only for
these tools. If its tools are available in the session, call them, save the
raw JSON result exactly as returned, then import:

| need | call | save to | then run |
| --- | --- | --- | --- |
| daily history | `get_historical_data` with `instrument_token` (from `search_instruments`, e.g. RELIANCE 738561), `from_date` "2023-10-01 00:00:00", `to_date` today, `interval` "day" | `trader/data/kite/RELIANCE-day.json` | `import-kite FILE --kind historical --symbol RELIANCE` |
| intraday | same with `interval` "5minute" (or "minute"), at most 60 days per call for 5minute | `trader/data/kite/RELIANCE-5m.json` | same command; minute candles are rolled up to 5 minutes |
| bands + today's bar | `get_quotes` with `instruments` ["NSE:RELIANCE", "NSE:HINDCOPPER"] | `trader/data/kite/quotes.json` | `import-kite FILE --kind quotes` |
| today's OHLC | `get_ohlc` with `instruments` ["NSE:TCS"], after 15:30 IST | `trader/data/kite/ohlc.json` | `import-kite FILE --kind ohlc --date 2026-10-23` |

Notes on Kite shapes: candles are `[timestamp, open, high, low, close,
volume]` with `+0530` timestamps (lists or `{date, open, ...}` objects are
both accepted). In quotes, `ohlc.close` is the PREVIOUS close; today's close
is `last_price`, so a daily bar is only saved when the quote's `timestamp`
is at or after 15:30 IST. `lower_circuit_limit` / `upper_circuit_limit` go to
`bands.json` (band % snapped to 2, 5, 10 or 20 when it fits). Kite daily
candles may already be adjusted for splits and bonuses: if older prices look
halved after a bonus, re-import with `--adjusted` so they land in `.adj.csv`.

## Or a CSV the user downloads

`import-csv FILE --symbol SYM` reads Kite Console exports, the nseindia.com
"Historical data" CSV, Yahoo / generic OHLC CSVs, eod2-style files and even a
bhavcopy (it picks the symbol's row). Columns are found case-insensitively:
Date (or Timestamp), Open, High, Low, Close (or Close Price, Last, LTP),
Volume (or Total Traded Quantity). Dates: yyyy-mm-dd, dd-mm-yyyy, dd/mm/yyyy,
dd-Mon-yyyy, with or without a time. Thousands separators, quotes, padded
headers and newest-first order are fine. A row that fails the OHLC check
stops the import unless `--drop-bad`; `--adjusted` files it in `.adj.csv`;
`--replace` replaces instead of merging.

## Validation on every write

- Errors (the write is refused): low above open/close, high below them, a
  price at or below 0, a duplicate date, dates not ascending.
- Warnings: NSE trading days missing (calendar 2024, 2025, 2026: NSE circulars
  CMTR/59722, CMTR/65587, the 2026 list in trader-alerts plus the 15 Jan 2026
  BMC election closure), a bar on a closed day, a 1/2, 1/5 or 1/10 overnight
  gap (a split or bonus: refresh `.adj.csv` from Yahoo). Muhurat, budget-day
  and Saturday DR sessions are accepted as real.
- Intraday: 5 minute boundaries, 09:15-15:30 IST, 75 bars per full session.

## doctor

Probes Yahoo query1 and query2, the NSE home page and quote API, the
nsearchives bhavcopy, the niftyindices Nifty 500 list, Google News RSS and
the Kite MCP endpoint, each with a timeout, prints a table (ok / FAIL,
latency, error and a hint such as "403: NSE blocks this IP (cloud, VPN and
datacenter IPs usually are); use Kite MCP or import-csv") and writes
`trader/data/sources.json`. Exit 0 if at least one price source works.

## record (real data for the tests)

`record SYM [--date D]` refreshes daily, intraday and bands for SYM, then
copies them into `tests/fixtures/real/<SYM>/` (`daily.csv`,
`intraday-5m-<date>.json`, `bands.json`) with a `provenance.json` (source,
fetched_at). `--no-fetch` copies what is cached. The offline suite then
checks those files too. Live tests: `NSE_LIVE=1 python3 -m unittest tests.test_live`.

## Steps

1. If the user has never run it, run `doctor` first and say in one line which
   sources work from their machine.
2. Run `daily` (and `intraday` for day-trading skills) for the symbols the
   next skill needs. Show the one-line result per symbol, and any warning.
3. If every source fails, say so plainly and use Kite MCP as above; if that
   is not connected, ask the user to download a CSV and run `import-csv`.
4. Before an order, run `bands` (or import `get_quotes`) so trader-risk can
   check the price is inside the circuit limits.

## Rules

- Never fill a gap or a band with a guess. Unknown stays unknown.
- Never present free data as live or official. Yahoo and NSE website data
  are unofficial and can lag; Zerodha's own numbers are the reference.
- Be polite to free endpoints: one request at a time, pauses between them.
- Never ask for or store Kite keys or passwords; Kite login happens in the
  browser via Kite's OAuth.

## Safety

- Education and tooling, not financial advice. Data is not a recommendation.
- This skill never places, changes or cancels an order. Orders go only
  through trader-broker, after trader-risk passes and the user types an
  explicit yes for that one order.
