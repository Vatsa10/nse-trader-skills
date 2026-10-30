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
  keeps a self-updating NSE holiday calendar (`calendar`, so 2027 and later
  work without a code change), turns the delivery file into delivery
  analytics (`delivery`: delivery quantity, trade size, the institutional
  money flag), reads NSE corporate actions (`actions`: splits, consolidations,
  bonus issues, dividends in rupees per share) and rebuilds the split and
  bonus adjusted `.adj.csv` exactly from them (`adjust`), keeps index levels
  with the Nifty 50 P/E, P/B, dividend yield and India VIX (`index`), summarises
  an index option chain (`chain`: put-call ratio, open interest walls, max
  pain, at-the-money strike), keeps the FII and DII cash-market flows day by
  day (`flows`, a context hint), reads the pre-open book for gap-ups, gap-downs
  and the buy/sell imbalance (`preopen`), and
  records real data as test fixtures. Use when the user says "get me
  RELIANCE history", "update my data", "download prices", "Yahoo is not
  working", "NSE blocked me", "what is the price band / circuit limit",
  "import this CSV", "is my data fresh", "corporate actions", "split",
  "bonus", "adjust for the split", "ex-date", "nifty pe", "is the market
  expensive", "vix", "option chain", "pcr", "max pain", "fii", "dii",
  "fpi flows", "what are the institutions doing", "pre-open", "preopen",
  "gap up", "gap down", "how will the market open", or before a backtest,
  an investment check or a scan that needs daily bars.
argument-hint: "[daily|intraday|bands|calendar|delivery|actions|adjust|index|chain|flows|preopen|doctor|status] [symbol]"
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
python3 "${CLAUDE_SKILL_DIR}/data.py" delivery RELIANCE TCS               # DQ, TQ, VOL, institutional money flag
python3 "${CLAUDE_SKILL_DIR}/data.py" delivery RELIANCE --no-fetch         # from the cached delivery files only
python3 "${CLAUDE_SKILL_DIR}/data.py" calendar                            # keep the NSE holiday calendar current
python3 "${CLAUDE_SKILL_DIR}/data.py" calendar --refresh                   # refetch holidays and circulars now
python3 "${CLAUDE_SKILL_DIR}/data.py" actions RELIANCE TCS                 # splits, bonus, dividends -> actions.json
python3 "${CLAUDE_SKILL_DIR}/data.py" adjust RELIANCE TCS                  # rebuild RELIANCE.adj.csv from the raw file
python3 "${CLAUDE_SKILL_DIR}/data.py" adjust RELIANCE --force              # also replace a Yahoo-supplied adj file
python3 "${CLAUDE_SKILL_DIR}/data.py" index                                # NIFTY 50, NIFTY BANK, NIFTY 500, India VIX + Nifty 50 P/E
python3 "${CLAUDE_SKILL_DIR}/data.py" index "NIFTY IT" --days 260         # another index; 260 snapshot files for the P/E percentile
python3 "${CLAUDE_SKILL_DIR}/data.py" index --years 2                      # also backfill index levels (no P/E) from NSE history
python3 "${CLAUDE_SKILL_DIR}/data.py" chain NIFTY                          # option chain: PCR, OI walls, max pain -> chain-NIFTY.json
python3 "${CLAUDE_SKILL_DIR}/data.py" chain BANKNIFTY --expiry 29-Oct-2026
python3 "${CLAUDE_SKILL_DIR}/data.py" flows                                # FII/DII flows: latest day + 5 and 20 session sums -> flows.csv
python3 "${CLAUDE_SKILL_DIR}/data.py" flows --no-fetch                     # summarise the cached flows.csv only
python3 "${CLAUDE_SKILL_DIR}/data.py" preopen                              # pre-open book of the Nifty 50 -> preopen.json (run 09:08 to 09:15 IST)
python3 "${CLAUDE_SKILL_DIR}/data.py" preopen --key FO                     # NIFTY50 (default), ALL, FO, SME or OTHERS
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
| `trader/data/delivery/<SYM>.csv` | `date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series`, ascending; a value NSE did not publish is an empty cell, never 0 | scanner, invest, `delivery` |
| `trader/data/delivery/pending.json` | `{"pending": ["2026-10-22"]}`: days whose delivery file was not published yet | data.py only |
| `trader/data/actions.json` | corporate actions per symbol: splits, bonus, dividends (contract below) | invest, portfolio, entry, backtest, `adjust` |
| `trader/data/index/<slug>.csv` | `date,open,high,low,close,volume,pe,pb,div_yield` per index (contract below) | invest, scanner, risk, entry |
| `trader/data/chain-<SYMBOL>.json` | option chain summary for one expiry (contract below), intraday | entry, risk |
| `trader/data/flows.csv` | `date,category,buy_cr,sell_cr,net_cr`: FII/FPI and DII cash-market flows in rupee crore, one row per date and category (contract below) | scanner, invest |
| `trader/data/preopen.json` | the latest pre-open book: advances, declines and one entry per stock with its indicative price (contract below) | scanner, entry |
| `trader/data/nse_cookies.txt` | the NSE cookie jar (Mozilla format), reused between runs | data.py only |
| `trader/data/calendar.json` | NSE holidays and special sessions (contract below) | data, alerts, and the skills that load alerts |

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
   (then the UDiFF `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`, or for
   days before 2024-07-08 the legacy
   `/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip`, so
   `--years max` reaches back), one file
   per NSE trading day, one a second, weekends and holidays skipped, each day
   fetched once for all symbols in the run, at most `--max-days` (300) per run.

Intraday: Yahoo `interval=5m&range=60d`, then NSE
`/api/chart-databyindex?index=<SYM>EQN` (today only, ticks bucketed into 5
minute bars, no volume). Bands: NSE `/api/quote-equity?symbol=X`
(`priceInfo.pPriceBand`, `lowerCP`, `upperCP`), then
`nsearchives .../content/equities/sec_list.csv` (Band column, no limits).

NSE transport (www.nseindia.com and nsearchives; Yahoo keeps its own loop):
a 429, 502, 503, 504, a timeout or a dropped connection is retried up to 5
times, waiting 2^n seconds with random jitter, never more than 8 s; a
`Retry-After` header (seconds or an HTTP date) replaces that wait, with the
same 8 s cap. Any other status comes straight back, and a 404 on an archive
file is final ("not published yet"), never retried. `doctor` does not retry:
it reports the first answer. Cookies are saved to `trader/data/nse_cookies.txt`
(Mozilla format); expired ones are dropped on load, session cookies are kept,
a file under 30 minutes old skips the home page prime, and a 401 primes once
more. A cookie file that cannot be read is a cold start, never an error.

Report readiness: when a bhavcopy run is about to fetch today's file (after
18:00 IST on a trading day), it first asks `GET /api/daily-reports?key=CM` once
per run. If `currentDate` is not today, or `CurrentDay` lists neither
`CM-UDIFF-BHAVCOPY-CSV` nor `CM-BHAVDATA-FULL`, it says "today's bhavcopy is
not published yet", requests nothing for today (no 404s to hammer) and exits 0;
earlier missing sessions are still filled. Only the files that are listed are
requested (UDiFF alone: no delivery file, so the day goes into `pending.json`).
If the call fails or has no file list, it says so and falls back to the 18:00
rule above. The `delivery` command skips an unlisted file the same way.

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
- Warnings: NSE trading days missing (calendar 2024, 2025, 2026 built in: NSE
  circulars CMTR/59722, CMTR/65587, the 2026 list in trader-alerts plus the
  15 Jan 2026 BMC election closure; any other year from `calendar.json`, see
  below), a bar on a closed day, a 1/2, 1/5 or 1/10 overnight
  gap (a split or bonus: refresh `.adj.csv` from Yahoo). Muhurat, budget-day
  and Saturday DR sessions are accepted as real.
- Intraday: 5 minute boundaries, 09:15-15:30 IST, 75 bars per full session.

## Delivery analytics (`delivery`, `trader/data/delivery/`)

NSE's `sec_bhavdata_full` file (the first bhavcopy URL above) carries the
trade count and the delivery quantity next to the OHLC. The price cache keeps
its six columns, so the extra numbers go in `trader/data/delivery/<SYM>.csv`.
They are filled automatically whenever a `daily` run fetches that file
(series EQ, BE, BZ; rights symbols ending `-RE` are skipped), and `delivery`
fetches the missing sessions of the symbols you name (the last 35 sessions,
one file a second, each file once).

- `qty_per_trade` = volume / trades, 2 decimals (empty when trades is 0 or
  blank). `deliv_pct` = `deliv_qty` / volume, computed, because NSE's own
  column is mostly `-`.
- `-` becomes an empty cell, never 0, and an empty day is left out of the
  means. In the trade-to-trade series (BE, BZ) every share is delivered, so
  `deliv_qty` is the full volume.
- The UDiFF and legacy bhavcopies have no delivery fields, so a day served by
  them keeps volume and trades and leaves the delivery cells empty, and the
  date goes into `pending.json`. Every later run retries pending days; a
  date still missing after 5 days is dropped with a warning.

`delivery SYM...` prints, for the latest day, against the mean of the last 30
sessions that have data (the latest day included; `--window N` changes it):

| number | formula |
| --- | --- |
| DQ | `deliv_qty` / mean `deliv_qty` |
| TQ | `qty_per_trade` / mean `qty_per_trade` |
| VOL | `volume` / mean `volume` |
| band (on DQ) | 2 or more strong, 1.5 or more high, above 1 above normal, else normal |
| institutional money | DQ above 1.2 and TQ above 1.2, on a non-trade-to-trade series |

Fewer than 5 delivery values in the window prints "not enough delivery data".
Heavy delivery in big tickets is a proxy for institutional buying, a hint to
look closer and not a signal to trade. `--no-fetch` works from the cache and
asks NSE for nothing. Exit 1 if any symbol had no usable data.

## Corporate actions and exact adjustment (`actions`, `adjust`)

Only Yahoo supplies an adjusted series; bhavcopy and the NSE API are raw, so a
split or bonus leaves a fake crash in the cache. `actions` reads NSE's
corporate-actions feed and `adjust` rebuilds `<SYM>.adj.csv` from it, exactly.

`actions SYM...` (no names: every symbol in the daily cache; `--years N` how
far back for a symbol with no cache, default 3; `--refresh` refetches the
whole span) asks `GET /api/corporates-corporateActions?index=equities&symbol=SYM&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`
for explicit date ranges in chunks of 365 days, from the first cached bar to
30 days past today (so an announced ex-date shows up as UPCOMING). The next
run asks only for what is new: anything before the span already covered, and
from 7 days before `fetched_through` onward; actions inside a refetched range
are replaced, not merged, so a withdrawn one disappears. Fetched today already
means no request. If a symbol's fetch fails its entry stays as it was.

```json
{"fetched_at": "2026-10-23T13:30:00Z",
 "symbols": {"RELIANCE": {"from": "2023-10-23", "fetched_through": "2026-10-23", "actions": [
   {"ex_date": "2024-10-28", "kind": "bonus", "factor": 2.0, "dividend": null,
    "subject": "Bonus 1:1", "series": "EQ", "isin": "INE002A01018", "face_val": 10.0},
   {"ex_date": "2026-08-14", "kind": "dividend", "factor": null, "dividend": 5.5,
    "subject": "Final Dividend - Rs 5.50 Per Share", "series": "EQ", "isin": "INE002A01018", "face_val": 10.0}]}}}
```

- `kind` is `split`, `bonus`, `split+bonus` (the factors multiply), `dividend`
  or `unparsed`. `factor` is what older prices are divided by (2.0 for a 1:1
  bonus or a 2-for-1 split, 0.1 for a 1-for-10 consolidation). `dividend` is
  rupees per share (keep them: invest and portfolio use them for yield and
  income). An `unparsed` entry also has `"about": "split" | "bonus" | "dividend"`.
- Only series EQ, BE, BZ; rights symbols (`-RE`), buy backs, rights issues and
  rows with no ex-date are left out. Subjects are read after lowercasing:
  - split, "splt" or "consolidation": from that word on, the first two numbers
    (decimals allowed) with only letters, spaces, `/`, `-`, `.` between them;
    factor = first / second (old face value over new);
  - bonus with a ratio `a:b` (spaces allowed): factor = 1 + a/b. Bonus text
    that mentions deb, pref, ncrps or dvr is not a share bonus and is skipped;
  - dividend: the first number after `Rs` or `Re` is rupees per share.
  A split, bonus or dividend named without a readable number is stored as
  `unparsed` and printed as "unparsed corporate action"; no factor is guessed.

`adjust SYM...` (no names: every cached symbol that has actions) rebuilds
`<SYM>.adj.csv` from `<SYM>.csv`; the raw file is never changed.

- For each action with an ex-date inside the cached span, rows dated before it
  have open, high, low and close divided by the cumulative factor of every
  later action, rounded to 2 decimals (no snapping to 0.05, which distorts
  cheap stocks), and volume multiplied by it. An action whose ex-date is after
  the last bar waits until its bars arrive.
- Verification, per ex-date: the adjusted close on the ex-date over the adjusted
  close before it should be 0.67 to 1.5, else "possible adjustment failure"
  (the file is still written, the warning says check the factor). No bar on the
  ex-date: "unverified". Duplicate dates in the raw file: refused.
- All or nothing per symbol: any error (duplicate dates, a price that rounds
  to 0.00) leaves the existing adj file untouched and writes nothing.
- It also warns when the actions were fetched more than 7 days before the last
  bar, or start later than the cache does.
- Provenance: the adj file's source is kept in `cache_meta.json` under
  `adjusted` (`yahoo`, `actions`, `import-csv:FILE`, `kite-mcp`; `unknown` for a
  file from before this was tracked). A file from anything but `actions` is
  never overwritten unless `--force`, and every run says which source the file
  came from.
- `daily` runs `adjust` for you after a non-Yahoo source when actions are known
  for the symbol (and stays quiet when the adj file is Yahoo's). It never
  fetches actions itself: run `actions` now and then, or when `daily` warns of
  a 1/2, 1/5 or 1/10 overnight gap.

## Index levels, valuation and India VIX (`index`, `trader/data/index/`)

`index [NAME...]` keeps one CSV per index. The file name is the slug: the
lowercase name with every run of characters outside a-z and 0-9 replaced by
one `-` (`NIFTY 50` is `nifty-50.csv`, `Nifty Bank` is `nifty-bank.csv`,
`India VIX` is `india-vix.csv`, `Nifty Financial Services 25/50` is
`nifty-financial-services-25-50.csv`). Other skills read these files by header
name, so the header is fixed:

```
date,open,high,low,close,volume,pe,pb,div_yield
2026-10-23,25060,25250,25000,25200.15,360000000,25,3.5,1.27
2026-10-23,13.4,13.6,12.7,12.95,,,,
```

Ascending dates; a value NSE shows as `-` or leaves out is an empty cell,
never 0 (the second row is India VIX: no volume, no P/E, P/B or yield; some
indices, such as Nifty Bank, have no valuation on some days). A new row fills
cells in and never wipes a value that is already there.

- Default indices: NIFTY 50, NIFTY BANK, NIFTY 500, and India VIX always;
  name others in quotes (`index "NIFTY IT"`), matched by slug.
- Daily snapshots: `https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv`
  (Index Name, the four index values, Volume, P/E, P/B, Div Yield). One file
  holds every index, so it is fetched once for all of them. With nothing
  cached it takes the last 30 sessions; afterwards only the sessions after the
  last cached row. `--days N` fetches the last N sessions again (values merge),
  `--max-days` caps a run at 300 files. A 404 for today is "not published
  yet" and not an error (it appears in the evening); a past day with no file
  is skipped and counted; a 403 stops the run.
- History backfill, `--years Y`: `/api/historicalOR/indicesHistory?indexType=NIFTY 50&from=DD-MM-YYYY&to=DD-MM-YYYY`
  and `/api/historicalOR/vixhistory?from=..&to=..` (India VIX goes to
  `india-vix.csv` with the same header). The answer is `{"data": [...]}` with
  `EOD_TIMESTAMP` like `29-OCT-2026`, in either order, and is sorted ascending.
  NSE returns at most 100 rows a request, so the span is asked for in 120 day
  chunks (about 85 sessions); a chunk that still comes back with 100 rows is
  split in two and asked again, and a single day that fills the cap is reported
  ("rows may be missing"), never accepted silently. They carry only the four
  index values, so `pe`, `pb` and `div_yield` stay empty for those days; the
  snapshot files are the only source of P/E. The backfill asks only for the
  span before the first cached row.
- Valuation line for NIFTY 50 (eod2's rule of thumb, not a signal): P/E at or
  above 25 is expensive, at or below 20 cheap, otherwise neutral
  (`--pe-expensive`, `--pe-cheap` change the marks; the defaults are the
  constants `PE_EXPENSIVE` and `PE_CHEAP`). Once 250 rows with a P/E exist it
  also prints the percentile of today's P/E among them; before that it says how
  many it has and that `index --days 260` builds the history (about a second a
  file). The last India VIX close is printed too.
- Say it plainly when presenting this: valuation tells how rich the market is,
  not when to buy or sell, and can stay high or low for years.

## Option chain (`chain`, `chain-<SYMBOL>.json`)

Intraday context for an index (or an F&O stock): `chain NIFTY [--expiry
DD-Mon-YYYY]` asks `GET /api/option-chain-contract-info?symbol=NIFTY` for the
expiry dates (nearest first; an empty or missing list is an error), then
`GET /api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=29-Oct-2026`
(`type=Equity` for stocks; NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY and
NIFTYNXT50 are indices). The nearest expiry is remembered in the output file
and reused (no contract-info call) until it has passed; an expiry you name with
`--expiry` is used as given and not remembered.

```json
{"symbol": "NIFTY", "type": "Indices", "underlying": 25060.4, "timestamp": "23-Oct-2026 15:30:00",
 "expiry": "2026-10-29", "expiry_pick": "nearest", "fetched_at": "2026-10-23T10:30:00Z",
 "source": "nse-option-chain", "pcr_oi": 0.6429, "pcr_change_oi": 2.0,
 "total_call_oi": 7000, "total_put_oi": 4500, "total_call_oi_change": 350, "total_put_oi_change": 700,
 "max_call_oi_strike": 25200.0, "max_call_oi": 4000, "max_put_oi_strike": 25000.0, "max_put_oi": 3000,
 "atm": 25100, "strike_gap": 100.0, "max_pain": 25100.0, "strikes": 3,
 "vix": "India VIX 12.95 on 2026-10-23 (-3.4% on the day), 1.0x its 20-session average (about normal)"}
```

- `pcr_oi` = total put OI / total call OI; `pcr_change_oi` = the same on the
  day's change in OI (the more telling one intraday). Either is `null` when
  the call side is 0.
- `max_call_oi_strike` and `max_put_oi_strike` are the strikes with the most
  call and put open interest (the usual resistance and support talk). `atm` is
  the underlying rounded to the strike gap. `max_pain` is the strike where the
  summed payout to option buyers is smallest, over every strike (the lowest on
  a tie).
- A strike with only a CE or only a PE counts the missing side as 0. Rows are
  matched to the chosen expiry under either key NSE has used (`expiryDate` or
  `expiryDates`); rows of another expiry are left out.
- An empty object back (stale cookies, a bot wall) primes a fresh session and
  asks once more; still empty gives a clear message and no file is written.
- `vix` appears only when `index` has already written `index/india-vix.csv`.
- Present PCR as a sentiment hint, never a signal: above about 1.2 leans
  supportive, below about 0.7 leans weak, in between balanced. OI walls and
  max pain are levels where positions sit, not guarantees that price stops
  there.

## FII / DII flows (`flows`, `flows.csv`)

`flows` asks `GET /api/fiidiiTradeReact` for the latest day's cash-market
figures: a list of `{"buyValue", "category" ("DII" or "FII/FPI"), "date"
("29-Oct-2026"), "netValue", "sellValue"}` with the values as strings in
rupee crore. NSE shows only the latest day (provisional, published after the
close), so the history is yours to build: every run appends to
`trader/data/flows.csv`.

```
date,category,buy_cr,sell_cr,net_cr
2026-10-29,DII,18458.64,16534.23,1924.41
2026-10-29,FII/FPI,13400.29,14343.89,-943.6
```

- One row per date and category, ISO dates, ascending (then category).
  Running it again the same day replaces that day's rows, never duplicates
  them; an older answer for a day already stored replaces it too.
- It prints the latest day and, per category, the net sum over the last 5 and
  20 recorded sessions once that many days are stored (until then it says how
  many days it has). Run it once a day to build the history; days you did not
  run it are not recoverable from NSE.
- An empty or non-JSON answer exits 1 and writes nothing. `--no-fetch`
  summarises the cached file without asking NSE.
- Say it plainly when presenting this: FII and DII flows are a context hint,
  not a signal. They say who was buying or selling, not what price does next.
- `doctor` probes the endpoint as `nse-flows`.

## Pre-open book (`preopen`, `preopen.json`)

`preopen [--key NIFTY50|ALL|FO|SME|OTHERS]` (default NIFTY50) asks `GET
/api/market-data-pre-open?key=K`. The verified keys are `NIFTY 50` (URL
encoded, with the space; plain `NIFTY` answers `{"data": [], "msg": "No Data
Found"}`), `ALL`, `FO`, `SME` and `OTHERS`; `--key NIFTY50` is the command
line spelling of the first. The answer is `metadata` (symbol, series, iep,
previousClose, pChange, finalQuantity, totalTurnover) plus
`detail.preOpenMarket` (the order book, `IEP`, `totalBuyQuantity`,
`totalSellQuantity`) per stock, with the advances, declines and unchanged
counts and NSE's timestamp on top. It overwrites `trader/data/preopen.json`
(the key it holds says which book):

```json
{"key": "NIFTY50", "timestamp": "30-Oct-2026 09:09:07", "date": "2026-10-30",
 "fetched_at": "2026-10-30T03:40:00Z", "advances": 21, "declines": 21, "unchanged": 8,
 "stocks": [{"symbol": "TRENT", "series": "EQ", "iep": 3018.0, "prev_close": 2861.7,
             "change_pct": 5.46, "final_qty": 30201, "total_buy_qty": 82070,
             "total_sell_qty": 58165, "turnover": 91146618.0}]}
```

- `iep` is the indicative equilibrium price (`metadata.iep`, else
  `detail.preOpenMarket.IEP`); `change_pct` is its move over `prev_close`;
  `final_qty` is the quantity matched at it; `turnover` is in rupees. `date`
  is the day of NSE's timestamp (ISO). Stocks are sorted by symbol; a value
  NSE did not send is `null`.
- It prints the top 5 gap-ups and gap-downs (only stocks with a matched
  quantity, so a price nobody trades at does not top the list) and the total
  buy against sell quantity as an imbalance.
- The pre-open session: 09:00 to 09:08 IST orders are entered, changed and
  cancelled (so a book read before about 09:08 is INDICATIVE and the IEP moves
  with every order), 09:08 to 09:12 matching, and the normal market opens at
  09:15. A book from 09:08 on is the matched one, but the 09:15 open can still
  differ. Outside the session NSE serves the last book it has.
- "No Data Found" or an empty list prints a clear message, exits 1 and writes
  nothing. Thin stocks (SME, OTHERS, small caps in ALL) gap on a few lots:
  read the matched quantity and turnover before trusting a gap. A hint to look
  closer, not a signal.
- `doctor` probes the endpoint as `nse-preopen`.

## The NSE calendar (`calendar`, `calendar.json`)

The built-in holiday tables stop at 2026. `data.py calendar` keeps a cache,
`trader/data/calendar.json`, so 2027 and later work without a code change.
Run it once now and then once a day (or whenever `status` says "no holiday
list for <year>"). It is also the file trader-alerts reads, and through
trader-alerts the skills that load it by path.

```json
{"fetched_at": "2027-03-08T04:30:00Z",
 "years": [2026, 2027],
 "holidays": {"2027-01-26": "Republic Day", "2027-04-08": "Circular: Trading Holiday on account of ..."},
 "special_sessions": {"2026-11-08": "Diwali Laxmi Pujan (Muhurat)", "2027-03-06": "Circular: Live Trading Session on ..."},
 "last_circular_check": "2027-03-08",
 "warnings": ["circular mentions a trading holiday but no date could be read, not guessed: ..."]}
```

- `holidays` are weekday closures only (ISO date to name). `years` are the
  years whose holiday list NSE has published, so a weekday of those years
  that is not in `holidays` is open.
- `special_sessions` are sessions on a weekend or holiday, plus the Muhurat
  evening session.
- Is NSE open on date d: a special session means open; Saturday and Sunday
  mean closed; a date in `holidays` means closed; a year in `years` means
  open; any other year falls back to the built-in tables. A missing or
  corrupt file means the built-in tables, never an error.
- Sources: `GET /api/holiday-master?type=trading` (the `CM` list: weekend
  rows are dropped), and `GET /api/circulars?from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&dept=CMTR`
  since the last check (first run: 5 days back, then at most once a day).
  A circular subject with "trading holiday" and "on account of" adds a
  closure; "live trading session" (or "muhurat") adds a special session. The
  date is read from the subject; if none can be read a warning is recorded
  and nothing is guessed. Entries that came from a circular are prefixed
  "Circular: " and survive a holiday-list refetch.
- The holiday list is refetched when the current year is missing from
  `years`, when today is a listed closure and the list is over a day old, or
  with `--refresh`. The command prints what changed (`+` added, `-` removed)
  and, for 2024 to 2026, where NSE's list and the built-in table disagree.
  If NSE blocks the fetch, nothing is written and the built-in answer stays.
- Muhurat (Diwali Laxmi Pujan) is a real session of about an hour in the
  evening, so it is recorded as a special session, never as a closure: data
  bars exist for that day and a bar on it is accepted. trader-alerts reports
  it as a special session ("regular market closed, special session: ...")
  because the normal 09:15-15:30 market is shut and the evening timings come
  from an NSE circular that is not tracked here.

## doctor

Probes Yahoo query1 and query2, the NSE home page and quote API, the NSE
holiday-master, circulars, corporate-actions, daily-reports, FII/DII flows, pre-open and option-chain contract-info endpoints (a changed shape
is reported, not silently empty), the index snapshot archive
(`ind_close_all_DDMMYYYY.csv`), the nsearchives bhavcopy, the niftyindices
Nifty 500 list, Google News RSS and the Kite MCP endpoint, each with a timeout, prints a table (ok / FAIL,
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
