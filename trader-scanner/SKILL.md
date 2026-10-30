---
name: trader-scanner
description: >-
  Chart Scanner for NSE stocks. Scans hundreds of charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the symbol list
  from the Nifty 500 constituents (niftyindices.com) or, optionally, every
  EQ-series stock in NSE's EQUITY_L.csv, runs a pre-market scan of daily
  closes (20-day closing-high breakout, bull flag, reclaimed the 20-day
  average, oversold, oversold bounce, swing pullback in an uptrend, swing
  reversal up) and an intraday opening-range breakout scan on 5-minute bars
  from the 09:15 IST open, optionally dropping names whose daily swing trend
  is down (--trend-filter), then writes
  trader/watchlist.json for trader-news and trader-entry. Also a long-term
  chart-pattern scan of the daily cache (VCP, double bottom, pole and flag,
  ascending triangle, inverse head and shoulders, rising-support retest, each
  with a trigger level, a stop and a target), an intraday double bottom and
  intraday flag scan on the session's 5-minute bars (patterns --intraday), and a long-term
  screen of the trader-data daily cache (above the 200-day average, near the
  52-week high, near the 52-week low as a value screen, RSI, and market
  structure: swing up with a change-of-character stop level, or a swing
  reversal up; relative strength over the Nifty, Mansfield), and a market
  breadth reading from the same cache (percent of stocks above their 50- and
  200-day averages, net new highs, advance-decline line, McClellan oscillator)
  that adds a one-line regime note to every scan, and a delivery spike tag
  (institutional-size trades with high delivery) from the delivery files
  trader-data keeps. Prints the day's FII/DII cash-market flows (fii dii net
  buying and selling) and the pre-open session's advances and declines, and
  stamps each opening-range or intraday-pattern hit with its pre-open gap,
  when trader-data has saved them. Free Yahoo data
  (SYMBOL.NS), bar by bar, with the trader-data cache as the fallback when
  Yahoo or NSE block the download. Use when the user says "scan the market", "find
  me setups", "what is breaking out on NSE", "opening range breakouts", "scan
  the Nifty 500", "scan for bull flags", "oversold stocks", "pre-market scan",
  "build my watchlist", "what should I look at today", "stocks above the
  200 DMA", "near 52-week high", "52-week low value stocks", "stocks in a
  swing uptrend", "swing reversal", "pullback in an uptrend", "change of
  character level", "only breakouts in an uptrend", "find VCP setups",
  "double bottoms", "intraday double bottom", "intraday flag", "chart patterns", "pole and flag", "inverse head and
  shoulders", "stocks coiling under resistance", "market breadth", "is the
  market healthy", "McClellan", "relative strength vs Nifty", "delivery
  spike", "high delivery volume", "institutional buying", "fii dii", "FII
  selling", "pre-open gap", "gap up at the open", or at the start
  of a trading day before /trader-news.
argument-hint: "[universe | daily | orb | longterm | patterns | breadth] [--at HH:MM] [--cache] [--trend-filter] [--intraday]"
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
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --trend-filter             # drop names whose daily swing trend is down
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --cache                  # closes from trader/data/daily, no Yahoo
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --cache                    # bars from trader/data/intraday, no Yahoo
python3 "${CLAUDE_SKILL_DIR}/scan.py" longterm                       # long-term screen -> trader/longterm.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" longterm --screen near_52w_low --near-low 10
python3 "${CLAUDE_SKILL_DIR}/scan.py" patterns                       # chart patterns -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" patterns --pattern vcp --state coiled
python3 "${CLAUDE_SKILL_DIR}/scan.py" patterns --intraday           # intraday double bottom + flag, 5-minute bars, during the session
python3 "${CLAUDE_SKILL_DIR}/scan.py" patterns --intraday --offline FILE --at 10:45   # replay a saved day
python3 "${CLAUDE_SKILL_DIR}/scan.py" breadth                        # market breadth -> trader/data/breadth.csv
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
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol`; daily hits carry `level` and `coc`, which `entry.py watchlist --setup level` reads unchanged |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 15:00 IST) |
| `trader/data/daily/<SYMBOL>.csv` | read by `daily --cache`, `longterm`, and `daily` when Yahoo fails (written by trader-data; `<SYMBOL>.adj.csv` wins when present) |
| `trader/data/intraday/<SYMBOL>-5m.json` | read by `orb --cache` and `orb` when Yahoo fails (written by trader-data, Yahoo v8 shape) |
| `trader/data/universe.csv` | read by `universe` when niftyindices.com refuses (written by trader-data) |
| `trader/data/index/nifty-50.csv` | read by `longterm` as the relative-strength benchmark when it exists (written by trader-data: `date,open,high,low,close,volume,pe,pb,div_yield`); else `trader/data/daily/NIFTYBEES.adj.csv`, else `NIFTYBEES.csv`; none means `mansfield_rs` is null, with a note |
| `trader/data/delivery/<SYMBOL>.csv` | read by `daily --cache` and `longterm` when it exists (written by trader-data): `date,volume,trades,deliv_qty,deliv_pct,qty_per_trade,series`, an empty field is missing. No file means no `delivery_spike` tag and `dq` null |
| `trader/data/breadth.csv` | written by `breadth`: `date,pct_50,pct_200,net_new_highs,ad_line,mcclellan_osc,net_adv_ratio,fast_ema,slow_ema`. When it exists every scan prints one regime line from its newest row (and puts it in the file as `regime`) |
| `trader/data/flows.csv` | read by `daily`, `orb` and `patterns` when it exists (written by trader-data): `date,category,buy_cr,sell_cr,net_cr`, ISO dates ascending, category `FII/FPI` or `DII`, rupee crore. Missing, corrupt or empty means no line and no `flows` key |
| `trader/data/preopen.json` | read by `orb` and `patterns --intraday` when it exists (written by trader-data): `date`, `advances`, `declines`, `unchanged` and `stocks[]` with `symbol`, `iep`, `change_pct`. Used only when its `date` is the scan's session day |
| `trader/data/calendar.json` | read for the trading calendar when it exists (written by trader-data): `years`, `holidays`, `special_sessions`. Missing or unreadable means the built-in 2026 table |
| `nifty500-snapshot.csv` (+ `.json`) | bundled with this skill: the Nifty 500 list as of 2026-07-13, the last fallback |
| `trader/watchlist.json` (patterns) | written by `patterns`: `mode: "patterns"`, per hit `pattern`, `state`, `level`, `stop`, `risk_pct`, `target`, `anchor_date`, `pivots`, `vol_x`, `vol_dry`, `first_seen`. It replaces the file the other scans wrote |
| `trader/watchlist.json` (patterns --intraday) | written by `patterns --intraday`: `mode: "patterns-intraday"`, per hit `pattern` (`intraday_double_bottom` or `intraday_flag`), `setups`, `state`, `level`, `stop`, `risk_pct`, `target`, `at`, `anchor_time`, `pivots`. `entry.py watchlist --setup level` reads `level` unchanged |
| `trader/longterm.json` | written by `longterm`: same shape as the watchlist, `mode: "longterm"`, per hit the 200-day average, 52-week high and low and the distance from each, and the swing fields (`swing_trend`, `swing_coc`, `swing_event`, `swing_bars_since`) |

## Market context: FII / DII flows and the pre-open

Two files trader-data keeps add context to the scans (the scanner works
without them, and a missing or corrupt file is no line and never an error).

- **Flows.** `daily`, `orb` and `patterns` print one header line from
  `trader/data/flows.csv`, like `FII/FPI net -943.60 cr, DII net +1,924.41 cr
  on 2026-10-29 (5-session FII net -2,543.60 cr, DII net +5,024.41 cr).` The
  figures are the newest date's net rupee crore per category and the sum over
  the last 5 dates in the file (fewer when the file has fewer; one date has no
  running part). The same numbers go in the watchlist document as `flows`
  (`date`, `fii_net_cr`, `dii_net_cr`, `sessions`, `fii_net_cr_5d`,
  `dii_net_cr_5d`). Say what it is: a day's institutional cash flow, context
  and not a signal, and the file's last date may not be today.
- **Pre-open.** `orb` and `patterns --intraday` read
  `trader/data/preopen.json`. When its `date` equals the session day being
  scanned, the header prints `Pre-open 2026-10-19: 1,402 advances, 812
  declines, 103 unchanged.`, the document gets `preopen` (`date`,
  `advances`, `declines`, `unchanged`), and each hit whose symbol is in the
  file gets `preopen_gap_pct` (the file's `change_pct`) and `preopen_iep` (the
  indicative equilibrium price). A file from another day is ignored. A
  pre-open gap is the call auction's indicative price, not a traded price.

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

Yahoo's spark endpoint returns closes for 20 symbols a request (six months
of daily bars, about 125, enough for the swing engine's 40-bar warm-up), so
the Nifty 500 takes 25 requests and about 15 seconds with the polite pause
(0.3 s).
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
| `swing_pullback` | the swing engine (see the long-term screen) says up and stable, a swing high is pending, the close is over the change-of-character level, and the swing high formed 3 to 15 bars ago (`SWING_PULLBACK_MIN`, `SWING_PULLBACK_MAX`) |
| `swing_reversal_up` | the swing trend turned up on the last bar |
| `delivery_spike` | with `--cache` only: the same delivery reading as in the long-term screen (below), on the latest day |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume, in rupees).

The swing setups run the engine on closes alone with Yahoo spark (high = low
= close, a closing-basis structure) and on the real highs and lows with
`--cache` (`swing_basis` in the hit says which). Every daily hit with an
uptrend carries `level` (the pending swing high, or the leg's high when none
is pending) and `coc` (the change-of-character level, a structural stop
reference). `level` is the key `entry.py watchlist --setup level` reads, so
`/trader-entry` can watch for the first completed 5-minute bar to close over
it; `coc` is a daily stop, usually far from price for an intraday trade, so
size a day trade with trader-exit's own stops and keep `coc` for a swing or
delivery hold.

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
| swing trend, `swing_coc` | a market-structure engine (below) over the last 252 sessions |
| `dq` | the latest delivery quantity over its mean of the last 30 sessions in the delivery file (the latest included); null without a file or under 5 values |
| `mansfield_rs` | Mansfield relative strength over the Nifty: (RS / its 22-session average - 1) x 100, with RS = stock close / index close x 100 on the dates both have; null without a benchmark |

| screen | the rule |
| --- | --- |
| `above_200dma` | the last close is above the 200-day average |
| `near_52w_high` | within `--near-high` (5) percent of the 52-week high |
| `near_52w_low` | within `--near-low` (10) percent above the 52-week low: a value screen, cheap is not the same as good |
| `oversold` | RSI 14 under 30 |
| `swing_up` | the swing trend is up and stable (more than 40 bars fed) |
| `swing_reversal_up` | the swing trend turned up on the last bar |
| `rs_positive` | `mansfield_rs` is above zero and above what it was 5 sessions ago (`MANSFIELD_RISE_BARS`): outperforming and still gaining on the index |
| `delivery_spike` | `dq` over 1.2 and the quantity per trade over 1.2 times its own 30-session mean, on a series that is not BE or BZ, and the delivery row is for the same day as the last close |

The swing engine is a one-pass, bar-by-bar market-structure tracker on
highs, lows and closes. In an uptrend the first bar that fails to make a new
high freezes that high as the pending swing high. A close above it is a
break of structure (`event: "bos"`), and the lowest low of the pullback
becomes the change-of-character level (`swing_coc`), a structural trailing
stop on a closing basis. A close under `swing_coc` reverses the trend
(`event: "reversal"`). Pullbacks shallower than 5 % of the swing high do
not move `swing_coc` (`SWING_RETRACE`). It looks at no future bar, so it
replays cleanly. The first trend comes from an arbitrary opening range, so
the engine is called stable only after 40 bars and the tags need that.
`swing_coc` can sit far under price in a steep rally; it is a reference,
not an order. `swing_bars_since` counts the bars since the last swing event
(a long count means sideways).

`--screen NAME` keeps one screen (default: a stock that matches any).
Liquidity: last close at least `--min-price` (₹20) and a 20-day average
volume of at least `--min-volume` (1,00,000 shares). Hits are listed closest
to the 52-week high first (closest to the low first for `near_52w_low`) and
written to `trader/longterm.json`. The cache is as fresh as the last
`python3 trader-data/data.py daily` run; the screen prints the dates it
used and warns when the newest close is more than 5 days old. A screen is a
starting list for reading annual reports and filings, not a list of stocks
to buy. Not financial advice.

## Delivery (the cache only)

NSE publishes how much of each day's volume was actually delivered, and the
size of the average trade. A day with both well above normal reads as
institutional money. `daily --cache` and `longterm` read
`trader/data/delivery/<SYMBOL>.csv` (trader-data writes it; the scanner
works without it). Over the last 30 sessions in the file, the latest
included and an empty figure left out of the mean:

- `dq` = the latest delivery quantity / the mean delivery quantity.
- `tq` = the latest quantity per trade / its mean (not written to the
  hit, used for the tag).
- `delivery_spike` when `dq` and `tq` are both over 1.2 (`DELIVERY_RATIO`),
  the series is not BE or BZ (trade-to-trade, where the whole volume is
  delivered and the ratio says nothing), and the delivery date is the last
  close's date. A file lagging the price data by a day gives `dq` and
  `dq_date` but no tag.
- Under 5 values in the window: no reading, no tag (`DELIVERY_MIN`).

Rough bands for `dq` when you describe it: 2 or more is strong, 1.5 or more
high, over 1 above normal. A spike is a day of heavy buying or heavy selling
by big hands: delivery does not say which. Say so.

## Market breadth and the regime line (no network)

`breadth` builds a market-health series from the same cache. It reads only
the split and bonus adjusted `<SYMBOL>.adj.csv` files (a split in an
unadjusted file fakes a new 52-week low) for the universe, `--symbols`, or
every stock in the cache, says how many unadjusted ones it skipped, and
`--unadjusted` includes them anyway. It writes the last 500 dates
(`--days 0` for all) to `trader/data/breadth.csv`. Per date, over the stocks
that have a bar that day:

| column | meaning |
| --- | --- |
| `pct_50`, `pct_200` | percent of stocks closing above their 50- and 200-day average (only stocks with that many sessions are counted) |
| `net_new_highs` | running sum of new highs minus new lows, a new high being a high over the highest high of the 252 sessions before it |
| `ad_line` | running sum of (advances - declines) / stocks with a previous close; a ratio line, not a count |
| `net_adv_ratio` | (advances - declines) / stocks with a previous close x 100 |
| `fast_ema`, `slow_ema` | 19- and 39-day EMAs of `net_adv_ratio`, each seeded with the plain average of its first 19 (39) values and first written on that date |
| `mcclellan_osc` | `fast_ema` - `slow_ema`, first written on the 39th date |

Every run prints the caveat: current constituents only, so the history is
survivorship-biased (a stock that was delisted is not in it). Running sums
(`net_new_highs`, `ad_line`) start at the first date written, so only their
changes mean anything. The regime line the other scans print reads the
newest row: 60 % or more above the 200-day average is "broad strength", 40 %
or less "weak", between is "mixed" (`BREADTH_STRONG`, `BREADTH_WEAK`; ours).
It is context for a human, not a gate: none of the scans uses it to drop a
hit. If a scan shows no regime line, there is no `breadth.csv`: run `breadth`
after a `trader-data daily` refresh.

## The chart-pattern scan (no network)

`patterns` reads the same daily cache as `longterm` (the `.adj.csv` when
there is one), the last 260 sessions of each stock with at least 50, and the
same liquidity filters (`--min-price` 20, `--min-volume` 1,00,000). It needs
the real highs and lows, so it never uses Yahoo's closes-only spark.

Pivots are the raw material. A bar is a pivot high when its high is
strictly above the 5 highs before it and not below the 5 after it (the
earlier of equal highs wins); a pivot low mirrors that; a bar that is both is
ignored. A pivot therefore shows up 5 sessions after it formed, and a turn in
the last 5 sessions is invisible: the scan says so. A detector reads only
bars up to the last one, so the same call replays on history. The zigzag
merges neighbouring pivots of one type into the more extreme. `U` below is
the median bar range (high minus low) over the pattern, and a tick is ₹0.05.

| pattern | the rule (all must hold) | level / stop / target |
| --- | --- | --- |
| `vcp` | 200+ sessions; close over the 50-day over the 200-day average, the 200-day rising, close within 25 % of the 252-bar high. The base high is the highest pivot high of the last 120 bars, at least 15 old. From it, 2 to 5 pullbacks: the first 8 to 35 % deep, each at most 0.85 of the one before, each low above the last, the last at most 10 %, every later high within 5 % of the base high. Coiled: the close is within 5 % under the trigger (the high the last pullback fell from) and over its low | trigger + tick / last low - tick / none; hits with over 12 % risk are dropped |
| `double_bottom` | two pivot lows 10 to 80 bars apart, within 0.5 U of each other; a pivot high between them, a rebound of at least max(3 U, 5 % of the low); the 60 bars before the first low reached 1.15 times it; no close under the lows minus 0.5 U, no close over the middle high after the second low. Coiled: the close is at or over the midpoint of the second low and the middle high | middle high + tick / lows - 0.5 U / middle high + (middle high - low) |
| `flag` | the highest high of the last 7 bars is the highest of the last 90 and 4 to 6 bars old; the latest pivot low before it is at most 20 bars earlier and the pole gain is at least 15 %; the lowest low since the top holds the midpoint of the pole | top + tick / flag low - tick / top + pole height |
| `ascending_triangle` | high, low, high, low, high spanning 20 to 90 bars; the three highs within 1 U; the second low at least 0.5 U over the first. Coiled at or over the midpoint of the last low and the top | top + tick / last low - 0.5 U / top + (top - first low) |
| `inverse_head_shoulders` | low, high, low, high, low: the head at least 0.6 U under both shoulders and the lowest of 60 bars; both necklines over both shoulders and within 1 U of each other; the shoulders within half the head depth of each other. Coiled at or over the midpoint of the right shoulder and the neckline | neckline + tick / right shoulder - 0.5 U / neckline + (neckline - head) |
| `rising_support` | over the last 120 bars, a rising line through two pivot lows 5+ bars apart with 3 or more pivot lows within 0.5 U of it, no close under it since the first; the last close at most 3 % over it (most touches wins) | last high + tick / line - 1 U / none |

State is `coiled` (formed, no close over its trigger yet) or `breakout` (the
last close is the first one over the trigger and at most 5 % over it). A
`vcp` and an `ascending_triangle` on one chart merge into one hit with both
tags and the triangle's numbers under `also`. `vol_dry` (the last 10 days'
average volume under the 50 before, or the second low on less volume than
the first) and `vol_x` (the last volume over its 50-day mean) are advisory,
not gates. A hit is deduplicated by symbol, pattern and anchor date, and
`first_seen` carries over from the previous `trader/watchlist.json` when that
file was also written by `patterns`.

`level` is the key `entry.py watchlist --setup level` reads: it fires on the
first completed 5-minute bar that closes over it, inside the entry window.
`stop` goes to trader-exit as `--stop price:X`. These are daily patterns held
for days, not intraday trades: the stop can be far under price and trader-exit's
intraday plan will flat the position by the close. For an intraday trade use
`patterns --intraday` (next) or the opening-range scan. The detectors'
thresholds are constants in `scan.py` (`PAT`), marked repo or ours.

### Intraday patterns (`patterns --intraday`, during the session)

The double bottom and the pole and flag, on the current session's 5-minute
bars, through the same fetch path as `orb` (spark closes for every symbol,
then a v8 chart with real highs and lows for the `--max-confirm` most traded;
`--cache`, `--offline PATH` with `--at` or `--now`, `--at`, `--until`,
`--config` work as in `orb`). The session so far is the look-back, the pivot
window is 3 bars each side (`INTRA`), and a chart must have every bar from
9:15 to the cutoff (as in `orb`).

| pattern | the rule (all must hold) | level / stop / target |
| --- | --- | --- |
| `intraday_double_bottom` | two pivot lows 8 to 60 bars apart, within 0.5 U of each other; a pivot high between them at least 0.8 % over the lows; the session before the first low reached 1.015 times it; no close under the lows minus 0.5 U, none over the middle high after the second low | middle high + tick / lows - 0.5 U / middle high + (middle high - low) |
| `intraday_flag` | the highest high of the last 12 bars is the highest of the session so far and 4 or more bars old; the latest pivot low before it is at most 24 bars earlier and the pole gain is at least 2 %; the lowest low since the top holds the midpoint of the pole | top + tick / flag low - tick / top + pole height |

A hit exists only when the cutoff is at or before `no_entries_after`
(`--until`, config, default 15:00): later, the scan prints one line and writes
nothing. No scan before 10:15 (12 bars). State is `coiled` or `breakout`
(a flag is always coiled). The file is `trader/watchlist.json` with `mode:
"patterns-intraday"` and replaces the one the other scans wrote.
`--min-price` defaults to 50 and `--min-volume` (volume so far) to 0 here.
A pivot shows up 3 bars late, so a turn in the last 15 minutes is not visible.

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

`--trend-filter` runs the swing engine on each hit's daily bars from the
trader-data cache (only bars before the session day, `--data-dir` to point
elsewhere) and drops the hits whose daily trend is down. A name with no
cache, under 41 daily bars, or no trend yet stays in the list, marked
`daily_trend: "unknown"` with a `trend_note`; the others get
`daily_trend: "up"`. The file and the printout say how many were dropped.

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
- Times are IST (UTC+05:30, no daylight saving). The trading calendar is
  `trader/data/calendar.json` when it exists and parses (trader-data writes
  it): a date in `special_sessions` is open, a weekend is closed, a date in
  `holidays` is closed, a year listed in `years` is otherwise open. NSE
  equity holidays are also built in for 2026 only; with no calendar file, or
  for a year neither has, the scanner warns "holiday list covers 2026 only:
  check nseindia.com". NSE has no early closes. The Diwali Muhurat session
  (a Sunday, timings by circular) is treated as closed unless the calendar
  lists it as a special session.
- Circuit limits: NSE stocks have daily price bands (2, 5, 10 or 20 %). A
  breakout into the upper circuit may not be fillable; say so.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 is a common time): `scan.py orb`, with
   `--trend-filter` when the user wants breakouts only in a daily uptrend. Tell
   the user how many usable charts broke their range and when, and that a
   later hit is closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is
   moving it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick
   for them.
5. If a scan stops because Yahoo or NSE refused: if the Kite MCP tools are in
   the session, call `get_historical_data` (or `get_ohlc`), save the raw JSON
   and run `python3 trader-data/data.py import-kite FILE --kind historical --symbol SYMBOL`; otherwise run
   `python3 trader-data/data.py doctor`. Then re-run the scan with `--cache`.
6. For chart patterns: `scan.py patterns` after a `trader-data daily` refresh.
   Give the counts, the dates of the closes, and the hits with state, level,
   stop and risk. A pattern is a shape on a chart, not a forecast; say when a
   hit is a `breakout` that has already moved. Never say "buy".
7. For the market's health, `scan.py breadth` after a refresh. Give the
   latest percent above the 200-day average and the McClellan reading, say it
   is survivorship-biased, and never turn it into a call on the index.
8. For long-term ideas (no day trade): `scan.py longterm` after a
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
