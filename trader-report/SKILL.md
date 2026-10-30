---
name: trader-report
description: >-
  One-page dashboard. Gathers what the other trader skills have already written
  (Nifty 50 close, P/E and India VIX, market breadth, FII/DII flows, the option
  chain summary, pre-open gaps, scanner watchlists, per-symbol price charts with
  200-DMA and swing levels, delivery bars, corporate actions, the thesis notes,
  the trade plan, risk verdicts, proposed orders, holdings and the latest
  backtest) into ONE self-contained HTML file with inline charts and sortable,
  filterable tables. Missing inputs are listed with the command that fills them.
  Use when the user says "report", "dashboard", "show me everything",
  "interactive report", "summary page", "make a page", "share this", or "put
  it all on one page". Needs no network; invents no numbers.
argument-hint: "[--symbol SYM ...] [--sections a,b] [--public]"
---

# trader-report

This skill turns the files the other skills left in `trader/` into one HTML
page you can open, sort, filter and share. It reads files only: no network, no
new data, no calculation beyond a 200-day average, a delivery ratio and the
holdings totals, each labelled on the page. Education and tooling, not
financial advice. It never places an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

## When to run

- The user asks for a report, a dashboard, "everything on one page", a summary
  page, or something to share.
- After a morning routine (data, scan, news) to see it all at once.
- Re-run it after any skill writes new files; it rebuilds the page in under a
  second.

## Command

```
python3 "${CLAUDE_SKILL_DIR}/report.py" build
python3 "${CLAUDE_SKILL_DIR}/report.py" build --symbol RELIANCE --symbol "M&M"
python3 "${CLAUDE_SKILL_DIR}/report.py" build --sections market,scans --title "Morning" --out trader/morning.html
python3 "${CLAUDE_SKILL_DIR}/report.py" build --public
```

Defaults: `--data trader/data`, `--trader trader`, `--out trader/report.html`.
`--symbol` is repeatable and adds a per-symbol section. `--public` leaves out
the portfolio, the proposed orders and the risk verdicts (the trade plan stays) so
the page is safe to hand around. Exit code 0 even when nothing is there: an empty tree gives a
valid page whose footer lists what to run.

## Sections: what each needs and who fills it

| Section | Reads | Filled by |
|---|---|---|
| Market | `data/index/nifty-50.csv`, `india-vix.csv`, `data/breadth.csv`, `data/flows.csv`, `data/chain-NIFTY.json`, `data/preopen.json` | trader-data (`index`, `chain`, flows, pre-open), trader-scanner (`breadth`) |
| Scans | `watchlist.json`, `longterm.json` | trader-scanner (`daily`, `orb`, `patterns`, `longterm`) |
| Symbols | `data/daily/<SYM>.adj.csv` or `.csv`, `data/delivery/<SYM>.csv`, `data/actions.json`, `data/fundamentals/<SYM>.json` | trader-data, trader-invest |
| Thesis | `thesis/*.json` (verdict, bull_case, bear_case, traps) | trader-thesis |
| Plan and risk | `plan.json`, `state/risk.json`, `orders.json` | trader-exit, trader-risk, trader-budget |
| Portfolio | `holdings.json` | trader-portfolio (`import`) |
| Backtest | latest `backtests/*/summary.json`, linked to its `report.html` | trader-backtest |

Every section is optional. A missing or unreadable input drops that section
(or that block) and adds a line to the "Not available" footer with the exact
command; a corrupt JSON file is skipped with a note, never a crash. Each
section shows its source file and as-of date, and the footer lists them all.

## What the page shows

- Market: Nifty 50 close and P/E against the rule of thumb (25 or more is
  expensive, 20 or less is cheap; a hint, not a signal), India VIX, breadth and
  McClellan lines, FII and DII net flow bars, option chain PCR and walls, the
  biggest pre-open gaps.
- Scans: sortable tables; click a header to sort, type in the box to filter.
- Symbols: close, 200-day average, 52-week band, and any scanner level, stop,
  target or change-of-character drawn as dashed lines; delivery DQ bars;
  corporate actions; fundamentals.
- Charts are inline SVG, readable in light and dark, with a table fallback
  under each one.

## How to use it in Claude

1. Build the page: run the command above, with the user's `--symbol` choices.
2. Where you have an Artifact tool (Claude Code, claude.ai): publish
   `trader/report.html` as a private Artifact page so the user can open it and
   share it. To update it later, republish to the same page (same file path),
   not a new one. Follow that tool's own design guidance when it asks for it.
3. Without an Artifact tool: tell the user the path and open the file locally
   in a browser. It works offline.
4. Publishing distributes the content. Never publish holdings, account details,
   orders or risk verdicts without the user's explicit yes for that page. If
   they have not said yes, build with `--public` for anything you publish, or
   keep the full page local.
5. Say what is missing. Read the "Not available" footer back to the user and
   offer to run those commands.

## Rules

- Every number comes from an input file. Never fill a gap with an estimate.
- Quote the as-of date next to any figure you repeat from the page.
- The page is a view, not a recommendation, and it never places an order.

## Safety

- Education and tooling, not financial advice. Past performance does not
  predict future returns.
- The holdings, plan and orders sections can identify the user's money. They
  stay on the user's machine unless the user says yes to publishing them.
- Nothing here places an order. Any real order goes through trader-risk and
  trader-broker and needs the user's explicit yes for that one order.

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
