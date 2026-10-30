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
