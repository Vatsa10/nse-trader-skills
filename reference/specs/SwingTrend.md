# SwingTrend: clean-room spec

Source studied: `nse-ref/SwingTrend` v2.0.4 (GPL-3.0; every file read: `swingtrend/Swing.py`, 4 test files, docs, README, pyproject, CI). Everything below is a behavioural description in our own words and our own numbers. Nothing here is code, pseudocode of its methods, or quoted text. Write the engine fresh (MIT, stdlib, plain floats, no dates required).

## 1. What it is

A one-pass, bar-by-bar market-structure tracker. Feed it high, low, close in time order. It keeps a trend label (UP, DOWN, or none yet), the pending swing point (a swing high in uptrends, swing low in downtrends), and one protective level called the Change of Character (CoCh). Closing beyond the pending swing point is a Break of Structure (BOS) that confirms the trend and moves the CoCh up (down). Closing beyond the CoCh reverses the trend. It needs no lookback window and no future bars, so it is causal and safe for backtests. The CoCh is a ready-made structural trailing stop. An optional retrace filter ignores shallow pullbacks.

## 2. Algorithm spec

### 2.1 Inputs and parameters
Bars as (high, low, close), oldest first, one per period, any timeframe. Open and volume are not used. Caller supplies clean, ascending, split-adjusted bars; bars with missing high/low/close are skipped and do not count.

| Param | Default | Meaning |
|---|---|---|
| retrace_pct | 5.0 | A BOS counts only if the pullback before it was at least this deep (percent of the swing point). 0 or none = every BOS counts. |
| sideways_bars | 20 | "Sideways" when bars_since is strictly greater. |
| min_bars | 40 | Trend output is "stable" only when total bars fed is strictly greater. Docs recommend feeding 60+. |

Suggested for us: daily long-term 5 to 8; intraday 5-minute none/0 (docs say shallow filtering suits trailing long positions, none suits short timeframes).

### 2.2 State
- trend: none, UP, DOWN.
- hi, lo: the working extremes of the current structure. In an uptrend hi is the highest high of the current leg, lo the lowest low since that high (the pullback low). Either can be unset.
- pivot: the pending swing point (swing high when UP, swing low when DOWN) or unset.
- coc: the reversal level. Unset until a trend starts.
- bars_since: bars since the last structural event (see 2.5).
- total_bars, legs (confirmed BOS count in the current trend).
- Our addition: keep `last_bos_level` and `last_pivot` after the pivot clears (the reference forgets the swing high once broken, so "last swing high" is otherwise lost).

### 2.3 Phase 0: no trend yet
1. First bar: hi = its high, lo = its low. Nothing else.
2. Each later bar, first test the close against the stored range: close above hi starts an UPTREND; close below lo starts a DOWNTREND (mutually exclusive).
3. UP start: coc = the stored lo (before this bar's low is applied); hi = this bar's high. DOWN start: coc = stored hi; lo = this bar's low.
4. Then, always: hi = max(hi, bar high), lo = min(lo, bar low). So the initial range keeps widening on bars that do not break out, and an outside bar that starts a trend can leave lo below coc.
5. bars_since stays 0, legs 0 in this phase. total_bars counts every bar.

### 2.4 Phase 1: trend running. UP rules (DOWN is the exact mirror: swap high/low, above/below, swing high/low, hi/lo)
Per bar, in this order. bars_since is incremented first.

Step A, extremes.
- A1 (pivot pending): if bar high > hi, hi = bar high and bars_since = 0 (a poke above the old high). The pending swing point is NOT raised; it stays at its original level. Then lo = min(lo, bar low).
- A2 (no pivot, bar high > hi): new high. hi = bar high, lo = bar low (pullback low restarts at this bar), bars_since = 0. Nothing else this bar except step B.
- A3 (no pivot, bar high <= hi, equality counts as failing): a swing high is born. pivot = hi, lo = this bar's low, bars_since = 1 (this bar counted). Continue to B.

Step B, reversal. If close < coc (strict), the trend reverses: DOWN, new coc = hi (highest high of the finished up-leg, including any poke), hi unset, pivot unset, lo = this bar's low, bars_since = 0, legs = 0. Emit a reversal event carrying the old coc as the broken level. Stop processing the bar.

Step C, BOS (only when a pivot is pending after A1/A3). If close > pivot (strict): depth = (pivot - lo) / pivot, computed as a positive fraction. Clear the pivot; bars_since = 0.
- If retrace_pct is set and depth < retrace_pct/100 (strict): ignored. coc, legs unchanged, no event. The pivot is gone, so the next bar that fails to exceed hi creates a fresh pivot at the (higher) hi.
- Else: coc = lo (the lowest low between the swing high bar and now), legs += 1, emit a BOS event with the broken pivot level. lo is NOT reset here.

Compare depth to the threshold by cross-multiplication or with a small epsilon so exact-equal cases (3/30 vs 10%) are stable. Exactly equal confirms.

DOWN mirror details: depth = (pullback high - pivot) / pivot; reversal when close > coc; a BOS sets coc = the pullback high; a reversal sets coc = lo (lowest low of the finished down-leg), then hi = this bar's high, lo and pivot unset.

### 2.5 Counters and outputs
- bars_since: +1 every bar in a trend; reset to 0 on a new extreme, any BOS (confirmed or ignored), and a reversal; set to 1 when a pivot forms. is_sideways = bars_since > sideways_bars. Note trend can be UP and sideways at once.
- is_stable = total_bars > min_bars.
- Per-bar snapshot to return: trend, coc, pivot (pending), last_bos_level, hi, lo, bars_since, legs, sideways, stable, and one event label for the bar: start_up, start_down, bos, reversal, or none.
- Stop use: while UP, the structural stop is coc, on a closing basis (a wick through it does nothing).

### 2.6 Branch catalogue (hand-checkable; threshold off unless stated)
Notation bar = H/L/C.
- P1 first bar 20/18/19: hi 20, lo 18, no trend.
- P2 range widens, no break: hi20 lo18, bar 21/19/19.5: hi 21, still none.
- P3 down start: hi20 lo18, bar 19/17/17.5: DOWN, lo 17, coc 20.
- P4 up start: hi20 lo18, bar 22/19/21: UP, hi 22, coc 18.
- P5 outside bar up start: hi20 lo18, bar 23/16/22: UP, hi 23, coc 18, lo 16 (lo below coc).
- U1 new high, no pivot: UP hi30 lo27 coc20, bar 32/29/31: hi 32, lo 29, bars 0.
- U2 pivot born: same state, bar 29.5/28/28.5: pivot 30, lo 28, bars 1, no reversal (28.5 > 20).
- U3 equal high counts as failure: bar 30/28/29: pivot 30, lo 28.
- U4 pending, plain pullback bar: pivot30 hi30 lo28 bars1, bar 29/27/27.5: bars 2, lo 27.
- U5 poke without BOS: pivot30 hi30 lo28 bars4, bar 31/29/29.5: hi 31, bars 0, pivot still 30, no BOS.
- U6 BOS: pivot30 lo27 coc20, bar 32/29/31: depth 10%; off: coc 27, legs+1, pivot cleared, hi 32, bars 0, event bos(30).
- U7 BOS ignored by filter: same with retrace 15: coc stays 20, legs same, pivot cleared, hi 32, bars 0, no event. With retrace exactly 10 it confirms.
- U8 close equal to pivot is no BOS: pivot 30, bar 31/29/30.
- U9 fresh pivot after an ignored BOS: from U7 state, bar 31/29/30: pivot 32, lo 29, bars 1.
- U10 reversal with pivot pending: pivot30 hi30 lo28 coc25, bar 28/24/24.5: lo 24 then close<25: DOWN, coc 30, lo 24, hi/pivot unset, legs 0, event reversal(25).
- U11 pivot born and reversal in the same bar: hi30 coc25 no pivot, bar 28/23/24: DOWN, coc 30, lo 23.
- U12 close equal to coc does not reverse.
- U13 gap through coc reverses on the close like any other bar; gap up through pivot is a BOS if it closes above. Gaps need no special case.
- D1 mirror BOS: DOWN pivot(low)10, pullback high 11.5, coc 14, bar 11/8.5/9: lo 8.5, depth (11.5-10)/10 = 15%, coc 11.5, legs+1, event bos(10).
- D2 mirror reversal: same state, bar 15/12/14.5: close>14: UP, coc = lowest low of leg, hi 15.

### 2.7 Full-series tests (use literal numbers)
Series A (H/L/C): 10/8/9, 11/9/10.5, 12/10/11.8, 11.5/10.5/11, 11.2/10.2/10.4, 12.6/11/12.4, 12.4/11.6/11.8, 11.9/9.8/10.0.
- Bar 2: UP start, coc 8. Bar 3: hi 12 lo 10. Bar 4: pivot 12, lo 10.5. Bar 5: lo 10.2, bars_since 2. Bar 6: BOS (level 12, depth 15%), coc 10.2, legs 1, hi 12.6, bars_since 0. Bar 7: pivot 12.6, lo 11.6. Bar 8: lo 9.8, close 10.0 < 10.2: reversal. End: DOWN, coc 12.6, lo 9.8, legs 0, bars_since 0.
- Same series with retrace 20: bar 6 BOS ignored (15% < 20). End: UP, coc 8, pivot 12.6, lo 9.8, legs 0, bars_since 2, no events after bar 2.
- With retrace 5 or 10: same as the first run.

Series B (mirror, exactly): 12/10/11, 11/9/9.5, 10/8/8.2, 9.5/8.5/9, 9.8/8.8/9.6, 9/7.4/7.6, 8.4/7.6/8.2, 10.2/8.1/10.0.
- Bar 2: DOWN, coc 12. Bar 4: pivot(low) 8, hi 9.5. Bar 6: BOS (level 8, depth 22.5%), coc 9.8, legs 1. Bar 7: pivot 7.4. Bar 8: close 10.0 > 9.8: reversal. End: UP, coc 7.4, hi 10.2, legs 0.

### 2.8 Edge cases and our decisions
- Too little data: trend stays none until a close breaks the first widening range; with a tight range this can take long. Below min_bars treat output as unreliable (stable = false). The first coc comes from an arbitrary opening range, so start-dependent; feed a fixed warm-up window (60 to 250 daily bars) and ignore events before stable.
- Inside bars: any bar with high not above hi creates the pivot at once (single-bar confirmation). Without the retrace filter this is very sensitive. This is by design; the filter is the sensitivity dial.
- Equal highs/lows: treated as failing to extend (pivot forms); equal close to pivot or coc triggers nothing.
- Shallow trends: with a large retrace filter in a steep rally no BOS is accepted, so coc lags far behind (docs show a coc 43% under price). As a stop it can be uselessly wide; cap with ATR or a percent.
- Deviation from reference (ours, deliberate): run the reversal test on every bar, including a new-extreme bar. The reference skips it on a bar that sets a new high, so an outside bar that makes a new high and closes under coc would not reverse until a later bar. Otherwise identical.
- Reference quirks NOT to copy: reporting trend 0/"down" in its per-bar series when trend is none; the poke leaving the pivot stale is kept (a later close above the original pivot still counts as BOS).
- Unadjusted prices make false gaps; use `.adj.csv`. Pandas dataframes, plot-line output, logging, callbacks and pack/unpack are not needed (see section 4).

## 3. Siblings: stock-pattern and eod2

Neither imports SwingTrend (no references found). All three are by the same author and GPL. Their logic is different:

- stock-pattern pivots: a rolling-window local extremum (default 6 bars each side) for highs and lows, used as raw material for patterns (triangles, head and shoulders, flags, VCP, harmonics, trendlines, which work on 6 consecutive pivots). The window is slid by a centre index that is one bar off (it actually looks one extra bar left and one fewer right; with 1/1 it degenerates to "highest of the last three bars"). Pivots are non-causal in a full-history scan (confirmed only after the right-hand bars), have a fixed lag, and carry no trend state. SwingTrend is the opposite: no window, close-based, state machine, causal, outputs trend plus a stop level.
- eod2: data supplier. Adjusted-for-splits-and-bonuses daily CSVs (Date, Open, High, Low, Close, Volume, delivery columns), not dividend adjusted, not survivorship-free. Its chart tool has two support/resistance finders (5-bar or 7-bar strict fractals, de-duplicated by a distance threshold, v2 requiring repeated touches) and a Supertrend (ATR 10, factor 3). Fractal levels are static horizontal levels, Supertrend is a volatility trailing line; neither knows structure.
- Our analogue of eod2 is trader-data's `trader/data/daily/<SYM>.adj.csv` (date,open,high,low,close,volume), already read by `scan.py: read_daily_csv`, `backtest.py: load_symbol`, `invest.py: read_daily`.

## How the pieces connect

Data: eod2-style adjusted daily CSV (our trader-data cache) and Yahoo 5m charts feed everything. Roles:
- SwingTrend-style engine: trend state and a stop level (CoCh) from a stream of bars. Best at "is this in an uptrend, where is it wrong".
- stock-pattern-style pivots: discrete turning points for geometry (flags, triangles). Needs lag and lookahead care; we do not adopt (section 5).
- eod2 S/R: horizontal levels, would serve as the `--level` for entry; we already have 20-day closing high and 52-week high.

Mapped to our chain:
- Daily long-term: trader-data daily cache -> `scan.py longterm` (add swing_up and swing_reversal_up tags) -> `invest.py analyse` (swing cushion in technicals) -> `backtest.py run --strategy swing_trend` to validate the same rule on history. Same engine, same retrace default 5 to 8.
- Daily to intraday: `scan.py daily` (pre-market) writes hits with `level` = pending swing high and a stop hint = coc -> `entry.py watchlist --setup level` already reads a hit's `level` field (no entry.py change) -> `exit.py plan --stop price:X`/`pct:X`/`atr:K`.
- Intraday 5m: `scan.py orb` -> optionally drop names whose daily swing trend is DOWN -> `entry.py check/watch` -> `exit.py plan` (static or_mid, or_low, pct, atr; a 5m swing stop is rank 6) -> `timemachine.py` (history_replay re-runs the rule on 60 days of sessions; its `rule_stop` would need the same new rule).
- Honest limit: the daily CoCh is usually far from price for an intraday (MIS) trade, so exit.py keeps its own stops for those; the daily swing stop belongs to delivery/swing positions (invest, backtest, portfolio review).

## 4. Where to use it (ranked, max 6)

Shared piece: one small stream engine (about 60 lines), copied into each script that needs it because every skill folder is a single standalone file (tests load scripts by path). Add one test that feeds Series A and B to every copy and checks they agree.

### 1. Long-term screen tags in scan.py (long-term, S)
Hook: `longterm_metrics(rows)` (rows are (date,o,h,l,c,v); h=r[2], l=r[3], c=r[4]) computes the engine over the last N rows and adds swing_trend, swing_coc, swing_event, swing_bars_since. `longterm_tags(m, ...)` appends `swing_up` (trend UP and stable) and `swing_reversal_up` (event on the final bar). Add both to `LT_SCREENS` (that also feeds the `--screen` choices and the printed counts), add fields to the hit dict in `scan_longterm`, a column in the `cmd_longterm` table. LT_BARS is already 200, enough warm-up.
Test (pure helper `swing_tags(bars, retrace=5, min_bars=0)`): Series A first 6 bars -> trend UP, event bos, coc 10.2, tags ["swing_up"]; add bars 7 and 8 -> trend DOWN, tags []. Series B 8 bars -> tags ["swing_reversal_up"]. With default min_bars 40 and these 8 bars -> [].

### 2. swing_trend strategy in backtest.py (long-term/swing daily, M)
Hook: new `Strategy` subclass beside `Breakout52w`, registered in `STRATEGIES` and `STRATEGY_TYPES` (params: retrace_pct 5, entry "bos" or "reversal", warmup 40). In `setup()` run the engine once over `data[sym]["high"/"low"/"close"]` to get a per-bar event list (causal, so no look-ahead). `signal(i, port)`: if flat and the event at i is the chosen entry (bos, or reversal-to-up/start_up), return buy_all; if held and the event is a reversal to DOWN, return sell_all. `simulate` already fills signals at the next open and `sweep` can grid retrace_pct. Add a named example to `strategies.example.json`.
Test: Series A with opens (bar6 open 12.4, bar8 open 10.0), bar 9 added (open 10.0, 10.3/9.9/10.0), entry "bos", warmup 0, slippage 0: one BUY filled at index 6 price 12.4 (signal at index 5), one SELL at index 8 price 10.0 (signal at index 7), one closed trade with a negative pnl.

### 3. Daily pullback and reversal setups in scan.py daily (daily then intraday, M)
Hook: `find_setups(closes)` and `scan_daily`. Spark gives closes only (docstring: "closes only"; `daily_closes` returns (date, close)). Run the engine in close-only mode (high = low = close: a closing-basis structure) and add `swing_pullback` (UP, pivot pending, coc below close, bars_since between 3 and 15) and `swing_reversal_up` to `SETUPS`. Change the `fetch_spark(symbols, "3mo", "1d", ...)` range to "6mo" (same request count, about 125 bars, clears the 40-bar warm-up). Put `level` (the pending pivot) and `coc` into each hit so `entry.py watchlist --setup level` picks the level up unchanged. With `--cache` the real highs and lows exist (`series_from_daily`), so use them.
Test: Series A bars 1 to 7 plus 11.5-close bar 12/11/11.5 and 11.8/10.9/11.2 (9 bars, min_bars 0): trend UP, pivot 12.6, bars_since 3, coc 10.2, close 11.2 -> tag swing_pullback, hit level 12.6, coc 10.2. With only 7 bars (bars_since 1): no tag.

### 4. Daily-trend filter for orb (intraday, M)
Hook: in `cmd_orb` after `scan_orb` returns hits, if `--trend-filter` is set load each hit's daily rows with `load_daily_cache` and keep only names whose daily engine trend is UP (unknown/no cache = keep, with a note in the hit). Pure helper so it is testable; `scan_orb` itself is unchanged.
Test: three symbols' daily bars: Series A 6 bars (UP), Series A 8 bars (DOWN), 3 bars (unknown). Hits for all three go in -> output keeps the first and third, drops the second, third flagged "trend unknown".

### 5. Swing cushion metric in the technicals pillar of invest.py (long-term, M)
Hook: `technicals(rows, ...)` receives (date, close) from `read_daily`, so it has no highs/lows. Add a sibling reader that returns highs and lows too (leave `read_daily`'s shape alone, since `close_on_or_before` and tests use it), compute the engine, and add `swing_trend`, `swing_cushion_pct` = (last close - coc) / last close x 100 (negative if close is on the wrong side, as in DOWN). In `analyse()` add the key to the tuple copied into `m` (next to dist_200dma_pct), add `swing_cushion_pct` to the technicals block of `scorecard.example.json` (suggest weight 1, bad -10, good 15), and print trend in `print_analysis`'s Price line. No data -> existing "missing" scoring.
Test: Series A 6 bars (last close 12.4, coc 10.2): cushion 17.74, trend UP, `score_metric(17.74, good 15, bad -10)` = 100. Series A 8 bars (close 10.0, coc 12.6): cushion -26.0, score 0.

### 6. `--stop swing` in exit.py (intraday 5m, M; weakest)
Hook: `parse_stop` (add "swing"), `build_plan` (the existing `if stop_rule == "atr"` block already loads the chart and `session_bars(...)` up to the entry bar; run the engine, retrace off, on those bars), `compute(...)` (new `swing_coc` argument; stop = floor to the paisa as for the other rules). Refuse with a plain reason if the trend is not UP, coc is unset, or coc >= entry (the existing "stop at or above the entry" error covers the last). Mirror in `timemachine.py: rule_stop/history_replay` so the history replay uses the same rule. Limit: static plan, and by 10:00 there are few bars, so coc often equals the opening range anyway. Real value would be an in-trade trailing stop, which exit.py does not do today.
Test: `compute(12.5, "swing", None, "r", 2, swing_coc=10.2)` -> stop 10.20, risk 2.30, target 17.10, reward:risk 2.0. Session bars = Series A 8 bars (DOWN) -> PlanError "trend is down".

## 5. Rejected ideas
- Pandas `run`/`add_series`/plot-line API: we are stdlib only; a plain stream function covers it.
- Callbacks (on_breakout/on_reversal): the per-bar event label does the same job without indirection.
- pack/unpack state persistence: our daily cache already holds history and replay is deterministic and cheap.
- Its per-bar TREND series (0 for none): misleading; we emit "none".
- stock-pattern pivots, triangles, H&S, harmonics, VCP: look-ahead lag, heavy, detect before breakout and give no stop, far more code than the value; not a trading rule.
- stock-pattern's `get_max_min` window (off-by-one) as our pivot definition: not worth copying a flawed window.
- eod2 S/R fractal levels and Supertrend: distinct, unrelated to this task; a Supertrend stop could be a separate later idea.
- Short side: all our skills are long-only; DOWN is used only as a filter/exit signal.
- Swing on 1-minute bars: far too noisy without the filter; NSE free 1m history is only about a week.
- is_sideways as a hard filter: just report it in hits; thresholds unvalidated.
- Importing the library or vendoring any of its code: GPL; we write fresh.
