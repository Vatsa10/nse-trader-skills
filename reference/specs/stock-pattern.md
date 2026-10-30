# Spec: ideas from stock-pattern (clean-room, own words)

Source studied: `nse-ref/stock-pattern` (GPL-3.0, read-only). Related repos read for the data and trend questions: `SwingTrend`, `eod2`, `eod2_data`. Nothing below is copied; thresholds that match the repo are marked "repo" so they can be tuned or dropped. Thresholds marked "ours" are not from the repo.

## 1. What the repo does

A command-line scanner. For each symbol in a list it loads the last 160 daily (or weekly, monthly, intraday) bars from a CSV folder, computes swing pivots, runs one or more detectors, and prints or plots the symbols whose chart currently shows a pattern that has not yet broken out. The detectors are bull and bear versions of: pole-and-flag, volatility contraction (VCP), double bottom or top, head and shoulders, plus triangles (ascending, descending, symmetric), up and down trendlines, and five harmonic shapes (AB=CD, Bat, Gartley, Crab, Butterfly). Output is a JSON list per run, a matplotlib chart viewer, and an optional state file that suppresses repeats. A second tool replays any detector across past dates. There are no buy or sell signals and no stops; the last bar's close is always the final leg of the pattern.

## 2. The pivot method (what everything rests on)

- A bar is a pivot high if its High is the maximum of a centred window of `left + 1 + right` bars (default 6 and 6). A pivot low is the same with Low as the minimum. Each pivot stores its price and that bar's volume.
- Ties: the earliest bar in the window wins, so of two equal highs only the first is a pivot.
-Quirk: the code centres the window one bar late, so with defaults a pivot has 7 bars on its left and 5 on its right. The unit test with 1 and 1 shows the same shift (the last bar of a 3-bar window is the one tested). Use a true symmetric window instead.
- A bar can be both a pivot high and low (outside bar). Pivot highs and lows are merged into one date-sorted list, not forced to alternate; the detectors then pick extremes between anchors (for example "lowest pivot after A", "highest pivot between A and C") and handle duplicate dates ad hoc.
- Pivot confirmation lags by `right` bars, so recent swings are invisible; the last close stands in for the last leg.
- Tolerance unit: "average bar length" = median of (High - Low) over the pattern's span. Most equality tests are multiples of it (0.5, 0.6, 0.8, 1.0). Volatility unit: 15-bar simple mean of true range (used only by double tops and bottoms).
- Well-formedness check: a pivot taken from High must not equal that bar's Low (and vice versa), which discards flat bars.

## 3. Every pattern it ships (summary rules)

Notation: letters are alternating zigzag points left to right; "now" is the last close; U is the tolerance unit.

- **Bull flag (pole and flag)**: needs 50 bars. Take the highest High of the last 7 bars; it must be at least 4 bars before the last bar (setting named FLAG_MAX_BARS, default 5, counted inclusively) and be at least as high as the highest High of the last 30 and last 90 bars. Pole base = the most recent confirmed pivot low (only lows are computed for this scan). The lowest Low since the pole top must stay at or above the midpoint of base and top. Trend gate: 20-bar mean close at least 8% above the 50-bar mean close. Bear flag is the mirror (pivot highs, fresh 90-bar low, 20-bar mean at least 8% below the 50-bar mean, bounce capped at the midpoint).
- **VCP, bull (repo flavour)**: A is the highest pivot, B the lowest pivot after A, D the lowest pivot after B, C the highest pivot between B and D, E = now. Requires |A-C| <= 1.0U (similar highs), D above B by at least 0.8U (higher low), B below A, C, D and E, now below C, and if C is above A it must be within 0.5U. C must remain the highest close since C and D the lowest close since D. If it fails, C becomes the new A and the search advances. Bear VCP is the mirror. This is a flat-top, rising-low squeeze; it does not check that successive pullback depths shrink.
- **Double bottom**: A = the lowest pivot, C = the lowest pivot after A, B = the highest pivot between. Needs: (B - C) < 4 x ATR15 at C; |A - C| <= 0.5U; C-pivot volume below A-pivot volume; B above both lows; now between C and B. Then: the pivots must be true lows/highs of their bars; C must be the lowest close from C onward, B the highest close from B onward, and no close after C above B. On failure C becomes the new A. Double top is the mirror.
- **Head and shoulders / inverse**: five alternating pivots A..E plus now F. Inverse: head C below both shoulders A and E; both neckline highs B and D above both shoulders' lows; |B - D| < 1.0U; head-versus-right-shoulder gap |C - E| > 0.6U; now above E. The neckline is the line through B and D; the pattern is dropped if the highest High after E exceeds the lower of B and D by more than 1.0U (already broken), or if now is already above the sloped neckline extrapolated to today. Regular H&S is the mirror.
- **Triangles**: six points A..F (F = now). Ascending: |A-C| and |C-E| <= 1.0U and B < D < F < E. Descending: |B-D| <= 1.0U and A > C > E > F >= D. Symmetric: A > C > E, B < D < F, E > F. Extra: span since A and since B must be within a 1.8 ratio; trendlines (highs A-C, lows B-D) must not have crossed; no close outside either line over the whole window. Slope guards exist but look inverted (they abort on the very slopes that define the shape), so treat the repo's triangle output as unreliable.
- **Uptrend / downtrend line**: line through two pivot lows (highs), rising (falling). Count pivots within 0.1% of the first anchor's price of the line; need at least 3. No close beyond the line since the first anchor. Now above the line by at most 10%. Best line = most touches, tie broken by smaller total distance.
- **Harmonics** (bull versions; bear mirrored): five points X,A,B,C and D = now, ratios snapped to the nearest of 0.236, 0.382, 0.5, 0.618, 0.707, 0.786, 0.886, 1.0. Key ratios: AB=CD (C retraces 0.382-0.886 of AB, D at an equal leg); Bat (B 0.382-0.5 of XA, D near 0.886 XA); Gartley (B 0.618, D near 0.786); Crab (B 0.382-0.618, D near 1.618 XA); Butterfly (B 0.786, D near 1.27 XA). Shared: fewer than 7 closes beyond the terminal point and D in the half of the zone nearest the reversal.

## 4. How it loads, scans, dedups, reports

- **Config** (`user.json`): DATA_PATH, SYM_LIST, LOADER (daily or intraday file loader), DEFAULT_TF, DATE_FORMAT, optional PATTERNS (custom groups), SAVE_STATE, SAVE_FOLDER, CHART options, FLAG_MAX_BARS, POST_SCAN_PLOT. A setup wizard writes it.
- **Daily loader**: file `<symbol lowercased>.csv` in DATA_PATH; first column date index; columns Open, High, Low, Close, Volume required, any extra columns ignored. A tail reader returns the last `period` rows (default 160; 7x for weekly, 30x for monthly) and can stop at an `end_date`, which is how a past day is scanned "as of". Weekly and monthly are made by resampling (week ending Sunday, month start, quarter). Intraday loader: same file shape, resamples 1-minute data to 5/10/15/25/30/60/75/125-minute bars anchored on a configured exchange start time.
- **eod2 / eod2_data**: eod2 writes exactly that layout: `daily/<sym lowercase>.csv`, header `Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY`, adjusted for splits and bonus (not dividends), SME stocks in `<sym>_sme.csv`. stock-pattern consumes Date and the five OHLCV columns only; delivery quantity and trade counts are ignored. The eod2 README lists stock-pattern as a sibling consumer; there is no code dependency either way.
- **Scan loop**: per symbol, sort and de-duplicate the index, compute pivots once (lows only for up-trendline and bull flag, highs only for down-trendline and bear flag, else both), run each selected detector, log and swallow detector errors (the first error stops that symbol's remaining detectors). Symbols run in a process pool. Groups: all, bull, bear, harmonic bull/bear, triangles, trendlines, or one named pattern.
- **Dedup**: with SAVE_STATE and a symbol file (not when scanning a past date), a state file keyed "symbol-pattern" remembers each detection's start date. Same key and same start = suppressed; new start = reported; keys no longer detected are deleted. (The update branch appends a changed hit twice; do not copy that.)
- **Backtest tool**: scans a window of end dates (default 30 days, ending 120 bars before the data's last date, 160-bar look-back) and dedups hits by the pattern's start date per symbol. Its call to the detectors omits the config argument that the live scan passes, so it appears broken as shipped.
- **Report**: JSON list of hits (symbol, pattern, optional alt-name, start, end, labelled points, extra levels, slope data) with a final meta record (timeframe, end date, config path); chart viewer draws candles, lines between consecutive points, horizontal projection levels, and key-driven paging.

### Versus SwingTrend

SwingTrend is not pivot-based and shares no code with stock-pattern (neither imports the other). It is a one-bar-at-a-time state machine on closes: the trend is set by the first close beyond the opening bar's range. In an uptrend it tracks the highest high; the first bar that fails to make one freezes that high as a swing-high candidate and starts tracking the lowest low since. A close above the frozen high is a structure break; if the pullback low sat at least a threshold (default 5%) below the frozen high, that low becomes the "change of character" (CoCh) level and a leg is counted. A close below CoCh flips the trend and resets the leg count. More than 20 bars without a new swing point marks it sideways; 40 bars are needed for a stable reading. State can be packed and restored, so it can run incrementally day by day. stock-pattern is stateless geometry on the last window; SwingTrend is stateful trend, breakout level and reversal level (a ready-made trailing stop).

## 5. Patterns worth adding, ranked

### Shared definitions (write once, test once)

- Rows are oldest-first (date, open, high, low, close, volume) as returned by scan.py's `read_daily_csv`; a detector is a pure function of the rows up to and including the last bar (no look-ahead, so it replays).
- Pivots: bar i is a pivot high if its high is strictly above each of the L highs before it and at least each of the R highs after it (so the earlier of equal highs wins); pivot low mirrors with lows. A bar that qualifies as both is ignored. Defaults L=R=5 daily, 3 on 5-minute bars.
- Zigzag: merge consecutive pivots of the same type, keeping the more extreme (earlier on a tie).
- U = median(high - low) over the stated span. Tick = 0.05 rupee. "Level" = trigger price + one tick; "stop" = swing low - one tick.
- State: `coiled` (pattern formed, no close beyond its trigger yet) or `breakout` (last close is the first close above the trigger, and no more than 5% above it).
- Speed: last 260 bars only; one linear pass for pivots; pair searches use the 8 most recent pivots of a type.
- Test convention: a bar's high = close + 0.5 and low = close - 0.5 (so U = 1.0), volume ignored, L=R=2, the pattern's long-history gates relaxed as stated.
- Watchlist hit (JSON, extends the existing hit shape): `symbol`, `setups` (tag list), `price`, `close_date`, plus `pattern` (name), `state`, `level` (trigger, what trader-entry reads), `stop`, `risk_pct` ((level - stop)/level x 100), `target` (measured move, or null), `anchor_date`, `pivots` (list of [date, price]), `vol_x` and `vol_dry` where relevant. Write `mode: "patterns"`, same top-level keys as the longterm doc. Dedup: key (symbol, pattern, anchor_date); carry `first_seen` from the previous file.

### 1. VCP (volatility contraction), swing / long-term, effort M

Evidence: best documented setup (Minervini's trend template plus shrinking pullbacks); repo's version is weaker (flat highs plus one higher low).
Rules (all must hold):
1. At least 200 bars; close > 50-day mean > 200-day mean; the 200-day mean higher than 20 bars ago; close within 25% of the 252-bar high (trend gate, ours).
2. Base high H0 = the highest pivot high in the last 120 bars, at least 15 bars old.
3. Take the zigzag from H0 onward as high, low, high, low... A contraction j runs from high h(j-1) to the next pivot low l(j); depth d(j) = (h(j-1) - l(j)) / h(j-1). Use confirmed pivots only; n completed contractions, 2 <= n <= 5.
4. d(1) between 8% and 35%; each later depth <= 0.85 x the previous; each low above the previous low; last depth <= 10%; every high h(j) >= 0.95 x H0.
5. Trigger P = h(n-1), the high the final contraction fell from. Coiled: P x 0.95 <= close < P, close > l(n), no close >= P since l(n).
6. Level = P + tick. Stop = l(n) - tick. Drop if risk_pct > 12 (ours). `vol_dry` = mean volume of last 10 bars < mean of the 50 before; `vol_x` = last volume / 50-bar mean (breakout state wants >= 1.4, advisory).
Test (L=R=2, trend gate off, min base age 10): closes 100,110,120,130,140,150,140,130,120,130,140,148,142,136,141,146. Expect VCP, coiled: H0 high 150.5, contractions 20.6% then 8.75%, P = 148.5, level 148.55, stop 135.45. Negative: change the 136 to 118 (low undercuts the first low) and nothing fires.

### 2. Double bottom, swing and intraday (5-minute), effort M

Rules: pivot lows A then C with 10 <= bars(C) - bars(A) <= 80 (daily); B = the highest pivot high between them. |low A - low C| <= 0.5U (repo; try 0.75U after a backtest). Rebound B - max(lows) >= max(3U, 5% of C) (ours; repo instead caps the height at 4 ATR, which blocks real patterns). Prior decline: the highest high in the 60 bars before A is >= 1.15 x low A. No close below min(lows) - 0.5U after A, and no close above B after C. Coiled: close >= midpoint of (C low, B high) and < B high. Breakout state per shared rules. `vol_dry` advisory: pivot-bar volume at C below A (repo makes it a hard gate; evidence weak, so advisory).
Level = B high + tick. Stop = min(lows) - 0.5U. Target = B high + (B high - min(lows)).
Intraday: same on 5-minute bars of the current session, L=R=3, separation 8..60 bars, prior decline >= 1.5%, rebound >= 0.8%, only hits before `no_entries_after`.
Test (L=R=2, gap 5, prior window 5): closes 130,125,120,115,110,105,100,106,112,108,104,100,103,107,110. Expect coiled; A and C lows 99.5, B high 112.5, level 112.55, stop 99.0, target 125.5. Negative: last close 113 (above B) is not a coiled hit.

### 3. Pole-and-flag v2 (replaces our closes-only bull_flag), swing and intraday, effort S

Rules (repo-derived plus ours):
1. At least 50 bars. Pole top T = the highest high of the last 7 bars (earliest on a tie), 4 to 6 bars before the last bar, and at least the highest high of the last 90 bars (or all bars if fewer).
2. Pole base = the latest confirmed pivot low before T. Pole gain (T high - base low)/base low >= 15% (ours; replaces the repo's 20-vs-50-mean 8% proxy) and base-to-top <= 20 bars (ours).
3. Retracement: lowest low from T to now >= base + 0.5 x pole height (repo).
Level = T high + tick. Stop = lowest low since T - tick. Target = T high + pole height.
Intraday 5-minute: top within the last 12 bars, 4+ bars old, highest high of the session so far; gain >= 2%, base-to-top <= 24 bars; same 50% retrace; hit only before the cut-off.
Test (L=R=2, 50-bar gate relaxed): closes 102,101,100,101,102,102,102,102,102,102,106,110,114,118,122,126,123,121,122,120,122. Expect coiled: base 99.5 (index 2), T high 126.5 (index 15), gain 27%, flag low 119.5 above midpoint 113, level 126.55, stop 119.45, target 153.5. Negative: replace the flag with 118,112,114,111,113 (low 110.5 under the midpoint) and nothing fires.

### 4. Ascending triangle / flat-top coil, swing, effort M

Rules: five consecutive zigzag points high, low, high, low, high (A..E) spanning 20 to 90 bars. The three highs within 1.0U of each other (repo uses pairwise 1.0U); the lows rise by at least 0.5U; R = max of the highs. Coiled: close >= midpoint of (D low, R) and < R, and no close above R since A. Level = R + tick. Stop = D low - 0.5U. Overlaps VCP; when both fire, merge into one hit with both tags.
Test (L=R=2, span gate off): closes 100,106,112,107,101,107,112,108,104,108,112,109,110. Expect coiled: highs 112.5 x3, lows 100.5 then 103.5, level 112.55, stop 103.0.

### 5. Inverse head and shoulders, swing, effort M

Rules: last five zigzag points low, high, low, high, low = A..E. Head C <= min(A, E) - 0.6U (repo) and C is the lowest low of the preceding 60 bars (ours). Both neckline highs B, D above max(A, E); |B - D| <= 1.0U (repo); |A - E| <= half of (N - C) where N = max(B, D) (ours, shoulder symmetry). N is a horizontal neckline. Coiled: close >= midpoint of (E, N) and < N, and no close above N since E. Level = N + tick. Stop = E low - 0.5U. Target = N + (N - C).
Test (L=R=2): closes 120,110,100,108,115,105,95,90,97,105,115,108,102,106,110. Expect coiled: A 99.5, B 115.5, C 89.5, D 115.5, E 101.5, N = 115.5, level 115.55, stop 101.0, target 141.5. Negative: last close 100 (under the midpoint) is not a hit.

### 6. Rising-support retest (uptrend line), swing, effort M

Rules: over the last 120 bars, any two pivot lows at least 5 bars apart with a rising line through their lows; touches = pivot lows (from the first anchor on) whose low lies within 0.5U of the line (repo uses 0.1% of price, too tight for NSE); need at least 3. No close below the line since the first anchor. Last close at or above the line and at most 3% above it (repo allows 10%, too loose for a retest). Pick the line with most touches, tie to the one with the latest last touch.
Level = last bar's high + tick. Stop = line value at the last bar - 1.0U. Useful because the stop is close.
Test (L=R=2): closes 108,104,100,105,110,112,110,108,106,110,114,116,115,113,112,114,117,118,116. Expect a hit: touches at lows 99.5, 105.5, 111.5 (slope 1 per bar), line at the last bar 115.5, close 116, level 116.55, stop 114.5.

## 6. How the pieces connect

Reference chain: eod2 (data) -> stock-pattern (pattern + level + no stop) -> SwingTrend (trend filter, trail). Ours: trader-data -> trader-scanner -> trader-entry -> trader-exit -> trader-timemachine / trader-backtest.

| step | reference | ours, exact hook |
| --- | --- | --- |
| data | eod2 daily CSVs, split-adjusted, lowercase names | trader-data writes `trader/data/daily/<SYMBOL>.csv` (`.adj.csv` wins); scan.py reads them via `daily_cache_path` and `read_daily_csv`. An optional eod2 import would map Date,Open,High,Low,Close,Volume to our lowercase header, skip `_sme` files, and could keep DLV_QTY as a future breakout confirmation. |
| daily detection | `init.py` loop over detectors | new `find_patterns(rows)` pure function plus `scan_patterns(rows_by_sym, symbols, ...)` shaped like `scan_longterm`, and `cmd_patterns` shaped like `cmd_longterm`, registered in `main()`. It needs highs, lows and about 1 year of bars, which the spark-based `scan_daily` / `find_setups` (closes only, 3 months) cannot supply, so it must read the cache. Add a tag/label table like `LT_SCREENS`. Write `trader/watchlist.json` (mode "patterns") so `symbols_from_file` and entry.py keep working. |
| intraday detection | `IEODFileLoader` resampling | in `scan_orb`'s stage 2, after `confirm(sym)` returns real highs and lows, run the intraday flag and double-bottom detectors on the output of `session_bars` (already gap-checked by `usable`). |
| trigger | none (pre-breakout only) | trader-entry `cmd_watchlist` already copies `hits[].level` into `evaluate(setup="level", level=...)`, which fires on the first completed bar closing above the level inside `no_entries_before`/`no_entries_after`. So a pattern hit's `level` field needs no entry.py change; it works on 5-minute bars for intraday hits. For a daily-bar swing trigger, `evaluate` would need a daily-interval branch (it only knows 5m and 1m). |
| stop / plan | none | trader-exit `gather` + `build_plan` accept `--stop price:X` for a `level` setup, so the hit's `stop` goes in directly; `compute` rejects risk under 0.05% and stops at or above entry. Gap: `build_plan` always enforces `resolve_flat_by` and prices costs with `costs(..., intraday=True)`; a swing hold needs a delivery mode (`costs` already has `intraday=False`). |
| replay, intraday | none | trader-timemachine `normalize_plan` accepts only 5m; `resolve_rule` returns None unless setup is "orb", so `history_replay` is skipped for pattern plans. Extend `resolve_rule` with a "level" rule (first close above the level, stop by its existing percent fallback) to replay intraday flags. |
| replay, swing | backtest.py (date-window re-scan) | trader-backtest: add a strategy type beside the existing `Strategy` classes (decided at the close of day i, filled next open); the detector is called on rows[:i+1] only, which is safe because pivots are confirmed by R-bar lag. Stops come from the hit. |
| trend / trail | SwingTrend | a small stateful tracker (see section 4 description) written as `swing_state` for trader-exit's trailing stop on held positions; the change-of-character level is the suggested trail. Not scanner work; effort M, optional. |

Detectors 2 and 3 run daily and intraday; 1, 4, 5, 6 are daily only. Both write the same hit shape.

## 7. Rejected

- Harmonics (AB=CD, Bat, Gartley, Crab, Butterfly): fixed Fibonacci ratios snapped to eight values, many knobs, no solid edge evidence, and a third of the repo's code.
- Symmetric triangle and descending triangle: direction is not given by shape; the repo's slope guards look inverted.
- Bear flag, bear VCP, double top, head and shoulders, down-trendline: we trade long only. Double top and head and shoulders could later warn on held symbols in trader-exit, but not as universe scans.
- The repo's own VCP: flat highs plus one higher low is too loose; replaced by shrinking depths.
- Hard volume gate on double bottoms: weak evidence, kept advisory.
- Copying the pivot window's off-by-one, the duplicate-date handling, and the triangle line-cross logic.
- Weekly and monthly resampling, plotting, process pool, wizard: our scanner is stdlib-only and text-first.
- Using delivery quantity (DLV_QTY) as a gate now: eod2 has it but trader-data does not; keep as a later confirmation.
