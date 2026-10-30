# Clean-room specs

Plain-language notes on four GPL-3.0 projects by Benny Thadikaran, written so the
features could be rebuilt from scratch under this repo's MIT licence. They describe
behaviour, NSE endpoints, formulas and thresholds in our own words; no code was copied.

| spec | studied | built into |
| --- | --- | --- |
| `eod2.md` | eod2 (EOD pipeline) | trader-data: calendar, delivery, corporate actions, index, transport; trader-scanner breadth and relative strength |
| `NseIndiaApi.md` | NseIndiaApi (NSE website API wrapper) | trader-data, trader-news deals and results calendar |
| `SwingTrend.md` | SwingTrend (market structure) | the swing engine in scan, backtest, invest, exit, timemachine |
| `stock-pattern.md` | stock-pattern (chart patterns) | trader-scanner `patterns` |
| `swing_engine.py` | (ours) | the canonical swing engine block the scripts copy byte for byte |

The studied repos themselves are not part of this repo. To look at them locally,
clone them next to this file's folder (they are gitignored, never committed):

```bash
cd reference
git clone --depth 1 https://github.com/BennyThadikaran/eod2
git clone --depth 1 https://github.com/BennyThadikaran/NseIndiaApi
git clone --depth 1 https://github.com/BennyThadikaran/SwingTrend
git clone --depth 1 https://github.com/BennyThadikaran/stock-pattern
```

Keep the clean-room split: anyone writing code for this repo works from these
specs, not from the GPL sources.
