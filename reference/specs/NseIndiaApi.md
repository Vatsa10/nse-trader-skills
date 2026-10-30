# Spec: ideas from NseIndiaApi (clean-room, behaviour only)

Source studied read-only: NseIndiaApi 5.1.0 (all of src/, docs/, tests/, pyproject), plus its main consumer eod2, its data repo eod2_data, and the two scanners that sit on that data. Nothing here is code from those repos. Field names, URLs and parameter names are facts about NSE. Anything marked UNVERIFIED is not in the repos and must be confirmed with a recorded fixture before use.

## 1. What the repo does and how it gets past NSE's bot defences

A thin synchronous wrapper over NSE's website JSON endpoints (`/api/...`), a second family of "NextApi" endpoints, and the dated report files on the archives host. It returns NSE's own JSON untouched and adds only a few helpers (option-chain compile, max pain, date chunking, archive extraction).

Session lifecycle, in order:
1. One HTTP client with fixed browser-like headers on every call: a Firefox-on-Windows user agent, Accept `*/*`, Accept-Language `en-US,en;q=0.5`, Accept-Encoding gzip/deflate, and a Referer pointing at an equity quote page (HDFCBANK). Timeout 15 s. HTTP/2 is optional (the old "server mode" was only this switch; docs say some server users find NSE steadier with it).
2. Priming: if the cookie store is empty, GET `https://www.nseindia.com/option-chain` once and keep whatever cookies come back. Nothing else is parsed.
3. Cookie persistence: default store is a Mozilla-format cookie file in the download folder, reloaded next run, with expired cookies dropped on load but session cookies kept; saved on exit. Not thread-safe. The alternative store is memory-only (one per worker; each new instance pays the priming call). Version 4 deleted cookies on exit; version 5 deliberately keeps them.
4. Throttle: one limiter, 3 requests per second, shared by API calls and file downloads (separate bucket names, same budget). Per instance unless a limiter is injected.
5. Retries (around every call): up to 5 retries on timeout, connect error, read error, protocol error, and HTTP 429/502/503/504. Wait is exponential (factor 1 s, doubling) with full random jitter, capped at 8 s; a Retry-After header (seconds or HTTP date) is honoured, also capped at 8 s. On a protocol error (server drops the connection) the whole client is rebuilt and cookies reloaded or re-primed before retrying. Other 4xx raise immediately. Notably there is no 401/403 re-priming: stale cookies are only refreshed after a protocol error or file expiry.
6. Downloads of archive files: skip if the file already exists locally; stream in 1 MB chunks to a `.part` file and rename atomically; HTTP 404 means "not published yet" and is never retried; zip/gz are unpacked and the archive deleted.

## 2. Catalogue of every public method

Base `https://www.nseindia.com/api` (A), NextApi `A/NextApi/apiClient/GetQuoteApi` (Q), archives `https://nsearchives.nseindia.com` (R). Dates are `DD-MM-YYYY` in query strings except option expiry (`DD-Mon-YYYY`). Default windows: 30 days back, deals/circulars 7, past IPOs 90. L = long-term use, I = intraday use. "eod2" = called daily by the eod2 project, so battle-tested.

| Method | Endpoint and params | Shape / value | Use |
|---|---|---|---|
| status | A/marketStatus | list under `marketState`: market, marketStatus, tradeDate, index, last, variation, message | I: live open/closed/pre-open |
| lookup | A/NextApi/globalSearch/{equity,derivatives,etf,all,others}?symbol=q | `data[]` symbol, companyName, series, lastPrice, url | L,I: symbol check |
| holidays (eod2) | A/holiday-master?type=trading or clearing | dict by segment (CM, FO, CD, COM, ...) of {tradingDate DD-Mon-YYYY, weekDay, description}; includes weekend holidays and the Muhurat day (description starts "Diwali Laxmi Pujan") | L,I |
| circulars (eod2) | A/circulars?from_date&to_date&dept=CODE&sub=text | `data[]` sub, cirDate, circDisplayNo, circFilelink, circDepartment. Dept codes include CMTR (equity trade), FAO, CMLS (listing), SURV, IPO | I: ad-hoc closures |
| press_releases | A/press-release-cms20?fromDate&toDate | list of content{title, field_date, file url} | skip |
| block_deals | A/block-deal | live today: `data[]`, totals, marketStatus | I |
| bulk_deals | A/historicalOR/bulk-block-short-deals?optionType=bulk_deals/block_deals/short_selling&from&to | `data[]`: BD_DT_DATE, BD_SYMBOL, BD_CLIENT_NAME, BD_BUY_SELL, BD_QTY_TRD, BD_TP_WATP, BD_REMARKS; short sells SS_DATE, SS_SYMBOL, SS_QTY. Max span 365 d | L,I |
| actions (eod2) | A/corporates-corporateActions?index=equities/sme/debt/mf&symbol&from_date&to_date | list: symbol, series, subject, exDate, recDate, faceVal, isin, comp, bcStartDate/EndDate | L,I |
| announcements | A/corporate-announcements?index&symbol&fo_sec=true&from_date&to_date | symbol, desc, attchmntText, attchmntFile, an_dt, sort_date, seq_id | L,I (we have) |
| board_meetings | A/corporate-board-meetings (same params) | bm_symbol, bm_date, bm_purpose, bm_desc, attachment | L,I |
| financial_results | A/corporates-financial-results?index&period=quarterly/annual/half-yearly&symbol&dates | filing metadata: fromDate, toDate, relatingTo, consolidated, audited, broadCastDate, xbrl link. No numbers | L |
| results_comparison | A/results-comparision?symbol (NSE's spelling) | `resCmpData[]` last ~5 quarters: re_from_dt, re_to_dt, re_net_sale, re_net_profit, re_basic_eps, re_oper_exp ... amounts in rupee lakhs (strings) | L |
| shareholding | A/corporate-share-holdings-master?index&symbol | list newest first: date, pr_and_prgrp (promoter %), public_val, employeeTrusts, xbrl | L |
| annual_reports | A/annual-reports?index&symbol | `data[]` fromYr, toYr, fileName (PDF url) | L, skip |
| equity_meta_info | Q?functionName=getMetaData&symbol | symbol, ISIN, series, flags (FnO, suspended, delisted, ETF) | L,I |
| quote / get_detailed_scrip_data | Q?functionName=getSymbolData&symbol&series=EQ&marketType=N | `equityResponse[0]`: orderBook (5 levels, totals, perBuyQty/perSellQty), metaData (OHLC, VWAP-like averagePrice, pChange), tradeInfo (volume, value, ffmc, impactCost, deliveryToTradedQuantity, deliveryquantity, applicableMargin, secwisedelposdate), priceInfo (52w high/low and dates, priceBand, cmDailyVolatility, cmAnnualVolatility), secInfo (sector, basic industry, sector PE, symbol PE, indexList, surveillance margins) | L,I |
| equity_quote | derived from quote | one OHLCV bar | I |
| live_volume_gainers | A/live-analysis-volume-gainers | `data[]` symbol, volume, week1AvgVolume, week1volChange, ltp, pChange, turnover | I |
| list_equity_stocks_by_index | A/equity-stock-indices?index=NAME (A/equity-stockIndex for "permitted to trade", "securities in f&o") | `data[]` per stock: lastPrice, pChange, dayHigh/Low, yearHigh/Low, nearWKH, perChange30d/365d, totalTradedValue; first row is the index itself. ~150 index names | L,I: universes, movers |
| gainers / losers | local sort of the above on pChange | | I |
| list_indices | A/allIndices | `data[]` index, last, pe, pb, dy, advances, declines, perChange30d/365d, yearHigh/Low | L |
| advance_decline | A/equity-stockIndices-adu?index | advances, declines, unchanged | I |
| fetch_index_names | A/index-names | short and full names | skip |
| list_etf / sme / sgb | A/etf, A/live-analysis-emerge, A/sovereign-gold-bonds | listings with NAV, prices | skip |
| IPO current/upcoming/past | A/ipo-current-issue, A/all-upcoming-issues?category=ipo, A/public-past-issues?from_date&to_date | issue dates, price band, subscription times | skip |
| fetch_equity_historical_data | Q?functionName=getHistoricalTradeData&symbol&series&fromDate&toDate, chunks of 100 days | CH_TIMESTAMP, open/high/low/close, CH_TOT_TRADED_QTY, VWAP, CH_TOTAL_TRADES, 52w, previous close | L,I (we use the older `/api/historical/cm/equity`) |
| fetch_historical_index_data | A/historicalOR/indicesHistory?indexType&from&to, yearly chunks | EOD_* OHLC, turnover | L |
| fetch_historical_vix_data | A/historicalOR/vixhistory?from&to | EOD_CLOSE_INDEX_VAL, EOD_PREV_CLOSE, VIX_PERC_CHG | I |
| fetch_historical_fno_data | A/historicalOR/foCPV?instrumentType(futidx,futstk,optidx,optstk,futivx)&symbol&expiryDate&year&optionType&strikePrice&from&to | FH_* prices, open interest, change in OI, lot | I |
| fetch_fno_underlying | A/underlying-information | index and stock lists with F&O | L |
| get_futures_expiry | A/liveEquity-derivatives?index=nse50_fut / nifty_bank_fut / finnifty_fut | `data[].expiryDate`; sort by date | I |
| option_chain | A/option-chain-contract-info?symbol then A/option-chain-v3?symbol&type=Indices/Equity&expiry | see item 5 | I |
| compile_option_chain, max_pain | local maths on the above | PCR, max OI strikes, max pain | I |
| fno_lots | R/content/fo/fo_mktlots.csv (two header rows; symbol is column 2; lot size taken from the next-expiry column; empty lot means leaving F&O) | symbol to lot | I |
| equity_bhavcopy (eod2) | R/content/historical/EQUITIES/{yyyy}/{MON}/cm{DDMONYYYY}bhav.csv.zip before 8 Jul 2024; R/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip after | OHLCV by symbol and ISIN | L (we have) |
| delivery_bhavcopy (eod2) | R/products/content/sec_bhavdata_full_{DDMMYYYY}.csv | adds NO_OF_TRADES, DELIV_QTY, DELIV_PER | L,I |
| indices_bhavcopy (eod2) | R/content/indices/ind_close_all_{DDMMYYYY}.csv | all indices incl. P/E, P/B, div yield | L |
| fno_bhavcopy | R/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip | F&O EOD | I |
| priceband_report | R/content/equities/sec_list_{DDMMYYYY}.csv | bands (we have the undated one) | have |
| pr_bhavcopy (eod2) | R/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip | bundle of reports incl. an mcap file (series, category, last trade date) and 52-week HL | L |
| cm_mii_security_report | R/content/cm/NSE_CM_security_{DDMMYYYY}.csv.gz | security master | skip |
| fetch_daily_reports_file_metadata (eod2) | A/daily-reports?key=CM / INDEX / FO / CD ... | `currentDate`, `CurrentDay[]`, previous-day list; each entry has a `fileKey` such as CM-UDIFF-BHAVCOPY-CSV, CM-BHAVDATA-FULL, INDEX-SNAPSHOT | L,I: "is today's file up?" |
| download_document | any archive URL, optional member list | PDFs and zips | skip |

## 3. What is battle-tested (eod2) and how the pieces connect

eod2 runs once a day after close and touches only: daily-reports metadata, equity bhavcopy, delivery bhavcopy, indices bhavcopy, PR bhavcopy (breadth job), holidays, circulars (CMTR), and corporate actions. Nothing else from the wrapper is exercised daily; option chain, deals, quotes and shareholding are library-only.

How it chains: NseIndiaApi (fetch, throttle, unzip) -> eod2 (daily sync loop) -> eod2_data (plain CSV per symbol: date, OHLC, volume, series, total trades, qty per trade, delivery qty; already split/bonus adjusted; plus meta with holidays, special sessions, pending delivery dates, cached actions; ISIN-to-symbol history; a breadth CSV) -> scanners (stock-pattern, swing-trend, charting) that only read the CSVs and never touch NSE.

eod2 behaviours worth copying:
- Check the daily-reports metadata first. If today's equity or index file is not listed, stop quietly instead of hammering 404s. Delivery file is allowed to lag: remember the date in a pending list, retry on later runs, give up after 5 days.
- Holidays: fetch the CM list once per year (refetch when today is listed as a holiday but the list may be stale). Add the Muhurat day back as a trading day (description match on "laxmi pujan"; the live feed puts an asterisk on it). Skip Sundays outright.
- Ad-hoc calendar changes: once a day read CMTR circulars since the last check. Subject containing "trading holiday" and "on account of" adds a closure; subject containing "live trading session" adds a special session (weekend or holiday sessions). Dates are fuzzy-parsed out of the subject; if unparseable, log and move on.
- Corporate actions: pull the next 8 days of actions per segment, refresh every 7 days, apply on the ex-date. Series filter EQ, BE, BZ and SME series; ignore rights-issue symbols (suffix -RE) and bonus text mentioning debentures or preference shares. Verify after adjusting: if the ex-date close versus the previous close is still outside 0.67x to 1.5x, warn.
- All-or-nothing writes: stage every file change, commit only if the whole day parsed, else roll back the day's last line in every file.
- ISIN, not symbol, is the identity: keep an ISIN-to-symbol history so a ticker rename renames the file instead of orphaning history; missing ISIN means "retry in a few hours".
- Nifty 50 P/E from the indices file is flagged when at or below 20 or at or above 25.
- Breadth from own data: percent of stocks above 50 and 200 day averages, net new 52-week highs (cumulative highs minus lows), advance-decline line (running sum of net advancers over total), a ratio-adjusted McClellan oscillator (net advancers percent, fast EMA 19 minus slow EMA 39).

Mapped onto us: NseIndiaApi's job = the NSE half of `trader-data/data.py` (Session, nse_api, Bhav). eod2's loop = `data.py daily/status`. eod2_data = `trader/data/daily/*.csv`, `bands.json`, `sources.json`. The scanners = `trader-scanner`, `trader-entry`, `trader-exit`, `trader-backtest`, `trader-invest`, which read the cache. So new NSE facts should land as small JSON/CSV files under `trader/data/` written by `data.py`, and other skills read the file (or load data.py by path, as it already loads alerts.py).

## 4. What we already do (so it is not proposed)

Cookie-primed per-run session with 0.6 s spacing and a 401 re-prime (data.py); NSE quote, historical and chart APIs; bhavcopy in both formats with fallback; price bands from quote with sec_list fallback; announcements fallback (news.py, own session); fundamentals plus promoter %, change and pledge via screener with NSE quote gap-fill (invest.py, own session, one fresh cookie jar per symbol, no spacing); hardcoded holidays 2024 to 2026 plus special sessions; Nifty 500 list; guessed split/bonus gaps in backtest; screener dividend yield; SEBI-deadline estimate of next results date. Four separate NSE session implementations exist (data, news, invest, scanner list download).

## 5. Ranked list

### 1. Corporate actions: splits, bonus, dividends, buy-backs (L and I) , effort M
- Fetch: GET `https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol=SYM&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`. With no dates it returns only forthcoming items, so history needs explicit dates (chunk to a year). Headers as data.py's nse_api (JSON accept, XHR header, quote-page Referer). Segment values equities, sme, debt, mf.
- Read: symbol, series, subject, exDate (DD-Mon-YYYY, "-" if none), recDate, faceVal, isin. Real subjects seen: "Interim Dividend - Rs 30 Per Share", "Interim Dividend - Re 0.50 Per Share", "Buy Back", "Bonus 2:1". Split subjects mention "split" or "splt" with from and to face values; reverse splits say "consolidation".
- Interpret: bonus "a:b" means a new shares for every b held, so older prices divide by (1 + a/b). Split from face value X to Y: divide older prices by X/Y; the same ratio rule works for consolidation (factor below 1). Dividend: the first number after Rs or Re is rupees per share; sum trailing 12 months for yield; ex-date drops price by about that amount. Skip debenture and preference bonus text and `-RE` rights symbols. If no number can be parsed, report "unparsed corporate action" and never guess a factor.
- Failures: 401/403 re-prime once then hint; empty list is valid; non-JSON body means bot wall.
- Where: `data.py` new `actions` command writing `trader/data/actions.json` (per symbol, refreshed weekly, with fetched_on). Consumers: `backtest.py check` (replace "likely gap" guesses with confirmed ex-dates and offer the factor), `entry.py`/`risk` (warn if ex-date within 5 sessions), `invest.py` (trailing dividend yield cross-check), `portfolio.py` (dividend income).
- Test: fixture with three rows (Bonus 2:1 exDate X, Interim Dividend - Rs 5 Per Share, a split from Rs 10 to Rs 2 with the alternate "Sub-Division" wording as UNVERIFIED text). Assert factors 3.0 and 5.0, dividend 5.0, and that an unparseable subject returns None.

### 2. Live calendar: holidays, ad-hoc closures, special sessions, market status (L and I), effort S to M
- Fetch: `/api/holiday-master?type=trading`, read the `CM` list: tradingDate DD-Mon-YYYY, description. Also `/api/circulars?from_date&to_date&dept=CMTR` and read `data[].sub`: "trading holiday" with "on account of" means closed; "live trading session" means a special open day. Optional live state: `/api/marketStatus` -> `marketState[]` where market is "Capital Market", fields marketStatus and marketStatusMessage.
- Interpret: weekend entries are harmless (filter to weekdays for holidays, but keep a weekend entry whose description contains "laxmi pujan" as a special session, not closed; today's code treats Muhurat as closed, so decide per circular timings). Cache `trader/data/calendar.json` with fetched_on; refresh at most daily, or when asked about a date the cache does not cover.
- Fall back to the hardcoded dict on any failure; when the live list disagrees with the dict, print both. Remove the "holiday list covers 2026 only" warnings when the cache covers the year.
- Where: `alerts.py` owns NSE_HOLIDAYS and is loaded by data, scan, entry, exit, risk, timemachine, so change it once (load cache first, dict second).
- Test: fixture with CM entries for 15-Jan (weekday), 15-Feb (Sunday) and an 08-Nov "Diwali Laxmi Pujan*" entry plus two circular subjects. Assert Jan 15 closed, Feb 15 ignored, Nov 8 a special session, a "live trading session on Saturday" subject adds that Saturday.

### 3. Delivery percentage and trade size from the file we already download (L and I), effort S
- No new request: `sec_bhavdata_full_DDMMYYYY.csv` (already in `bhav_urls`) carries NO_OF_TRADES, DELIV_QTY, DELIV_PER next to the OHLC. Column names have padding spaces; DELIV values are "-" for some rows; strip and treat "-" as missing. For BE/BZ (trade-to-trade) series all volume is delivery, so use volume.
- Store extra columns in a side file (`trader/data/delivery/SYM.csv`: date, trades, qty_per_trade, deliv_qty, deliv_pct) so the OHLC cache format stays unchanged. Ratios: delivery qty and qty per trade each divided by their 30-day average; both above 1 flags "heavy delivery with big tickets" (a proxy for institutional buying), with 1.5x and 2x as strong marks. This file lags the main bhavcopy by hours, so mark days "pending" and retry for 5 sessions.
- Freshness: `/api/daily-reports?key=CM` -> `CurrentDay[].fileKey` and `currentDate` tell whether the three files (CM-UDIFF-BHAVCOPY-CSV, CM-BHAVDATA-FULL, INDEX-SNAPSHOT) are up before downloading.
- Where: `data.py` `parse_bhav` (extend), `scan.py` (rank by delivery multiple), `invest.py` (accumulation note).
- Test: five-row CSV with padded headers, one "-" delivery, one BE row; assert delivery ratio math and the missing-value handling.

### 4. Results and board-meeting calendar, results numbers fallback (L and I), effort M
- Fetch: `/api/corporate-board-meetings?index=equities&symbol=SYM&from_date&to_date` (forthcoming by default). Fields bm_symbol, bm_date, bm_purpose, bm_desc. Treat purpose text containing "financial results" as a results date, "dividend" as a dividend board date. Add `fo_sec=true` for F&O only.
- For filed results: `/api/corporates-financial-results?index=equities&period=quarterly&symbol=SYM` (relatingTo, toDate, consolidated vs non-consolidated, broadCastDate) and `/api/results-comparision?symbol=SYM` -> `resCmpData[]` with re_net_sale, re_net_profit, re_basic_eps in rupee lakhs strings (divide by 100 for crore). Use as an independent check or fallback when the screener page fails.
- Interpret: results inside the next 2 sessions is a gap risk, warn in entry and risk for intraday; for long-term replace invest.py's 45/60-day deadline guess with the real date when present.
- Where: `news.py` (results-soon flag in the morning briefing), `invest.py` (`results_due`), `entry.py`/`check.py` warning.
- Test: board meeting fixture with purpose text "to consider Quarterly Unaudited Financial results and Dividend" dated within 2 sessions; assert both tags and the warning; a fixture with an empty list gives no warning.

### 5. Index option chain: PCR, max pain, OI walls, VIX (I), effort M
- Fetch: `/api/option-chain-contract-info?symbol=NIFTY` -> `expiryDates` (DD-Mon-YYYY, nearest first; empty or missing is an error). Then `/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=DD-Mon-YYYY` (type=Equity for stocks). Symbols upper-case. Cache the chosen expiry per symbol until it passes. Priming must be fresh; this endpoint is the most likely to return an empty object when cookies are stale, so on empty re-prime once.
- Read: `records.underlyingValue`, `records.timestamp`, `records.data[]` each with strikePrice, expiry (the sample uses `expiryDate`, the wrapper reads `expiryDates`; accept either), and optional CE and PE objects with openInterest, changeinOpenInterest, impliedVolatility, lastPrice, totalTradedVolume. A strike may lack one side: count it as zero.
- Compute for one expiry: PCR = total put OI / total call OI (none if call OI is 0); strike with largest call OI (resistance) and largest put OI (support); ATM = underlying rounded to the strike gap; max pain = the strike where the summed intrinsic payout to option buyers is smallest, evaluated over all strikes. Our extra: PCR of change-in-OI for the day, more relevant intraday than total OI. Read PCR as a sentiment hint (rising above about 1.2 supportive, below about 0.7 weak), not a signal; label walls as levels, not guarantees.
- VIX: `/api/historicalOR/vixhistory?from&to` -> EOD_CLOSE_INDEX_VAL and VIX_PERC_CHG for regime context (rising VIX plus falling index = reduce size).
- Where: `data.py` command `chain` writing `trader/data/chain-NIFTY.json`; `entry.py` prints one context line; `budget/size.py` may shrink size on high VIX.
- Test: three strikes with known OI (including one CE-only strike); assert PCR, max pain strike, walls, and empty-object handling.

### 6. Bulk, block and short-selling deals (L and I), effort S to M
- Fetch once per day for all symbols: `/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=DD-MM-YYYY&to=DD-MM-YYYY` (also block_deals, short_selling). Range at most 365 days. Today's live block window: `/api/block-deal`. An empty `data` list is normal, not an error.
- Read BD_SYMBOL, BD_CLIENT_NAME, BD_BUY_SELL, BD_QTY_TRD, BD_TP_WATP, BD_DT_DATE. Compute deal quantity as a percent of the symbol's 20-day average volume from our cache; flag at 1 percent or more (L) and any same-day deal on a watched symbol (I). Net buy minus sell by named client over 30 days is the long-term view.
- Where: `news.py` as an extra source tagged "deal" (the tag exists), filtered to watch symbols after one fetch.
- Test: two rows for one symbol (BUY 1,000,000 and SELL 200,000) and one for another; assert net quantity, percent of ADV, and the empty-list path.

### 7. Market context: index valuation and breadth (L), effort S to M
- Valuation: `/api/allIndices` -> `data[]` with pe, pb, dy for NIFTY 50 and others (strings). Flag Nifty 50 P/E at or below 20 (cheap) or at or above 25 (rich); these are eod2's thresholds, make them configurable. Adjust long-term tranche pacing in `invest.py plan` (slow buying above the rich mark).
- Breadth with no new endpoint: from our Nifty 500 daily cache compute, per session, percent above 50 and 200 day averages, net new 52-week highs, advance-decline share and the ratio-adjusted oscillator described in section 3. Use `/api/equity-stockIndices-adu?index=NIFTY 500` for a live advance/decline count.
- Where: `scan.py` summary header; new section in `data.py status`.
- Test: five synthetic symbols across 60 days; assert percent above average and the A/D running sum on a hand-calculated day.

### 8. Second quote path with liquidity and risk fields (I and L), effort S
- Fetch: `/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&symbol=SYM&series=EQ&marketType=N` -> `equityResponse[0]`. Read tradeInfo.impactCost (liquidity: above about 0.5 percent means slippage risk), applicableMargin, deliveryToTradedQuantity, priceInfo.cmDailyVolatility, priceBand, and orderBook.perBuyQty/perSellQty (book imbalance, snapshot only). `getMetaData` returns listing flags (suspended, F&O, ETF).
- Use as fallback when `/api/quote-equity` fails (and vice versa); also supplies delivery % intraday for yesterday's session (`secwisedelposdate` says which day).
- Where: `data.py nse_quote`, consumed by `invest.py` (volatility, impact cost) and `size.py` (margin, impact-cost cap).
- Test: trimmed JSON with tradeInfo and priceInfo; assert extraction and that a missing key gives None rather than 0.

### Cross-cutting transport hardening (not a feature, S)
Persist the cookie jar between runs (stdlib Mozilla jar, drop expired, reuse until a 401/403); honour Retry-After with a cap and jitter; treat a dropped connection as "re-prime and retry once"; make invest.py and news.py reuse one session helper (load data.py by path like alerts) instead of priming a fresh jar per symbol; keep spacing at least 0.4 s (NSE tolerance in the wrapper is 3 per second). Add `doctor` probes for each new endpoint so a changed shape is reported rather than silently empty.

## 6. Rejected

- Shareholding endpoint (promoter % only, no pledge): screener already gives promoter, change and pledge; keep as unscheduled fallback.
- Annual reports, press releases, circular browsing in general: documents, no decision value (only CMTR calendar use kept).
- IPO lists, SME, ETF, SGB listings: outside the stated scope.
- Gainers/losers/volume gainers: scanner already ranks its own universe; live volume-gainers is noisy.
- Historical F&O contract data: heavy, backtest does not trade derivatives.
- PR bhavcopy zip: only needed for eod2's universe filter; our Nifty 500 list suffices.
- AmiBroker export, charting, plugins, HTTP/2, Redis rate limiting, filelock/dedupe: not applicable to a single-user stdlib tool.
- FII/DII flows (UNVERIFIED, not in these repos): NSE's `/api/fiidiiTradeReact` is believed to give category, buyValue, sellValue, netValue in crore, but I cannot spec it from source; record a real fixture first and treat it as a possible item 9.
- Pre-open data (UNVERIFIED, absent from these repos): `/api/market-data-pre-open?key=NIFTY` is believed to exist; same rule. alerts.py already knows the 09:00-09:08 window.
