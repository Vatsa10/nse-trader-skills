---
name: trader-thesis
description: >-
  Bull-and-bear thesis for one NSE symbol before real money moves. Use on
  /trader-thesis, "should I buy IOC?", "make the case against TCS", "bull and
  bear case", "devil's advocate", "write a thesis", "is this a value trap?",
  "take this trade?" or any open-ended buy, hold or trade decision. Builds one
  evidence brief from what trader-data, trader-invest, trader-scanner and
  trader-news already wrote (every number with its file and date, every gap
  listed), then runs 5 isolated parallel Agent branches under different frames
  (short seller, risk manager, FII/DII flows, value-trap hunter, forensic
  accountant, 3am stop-out, tax and costs, opportunity cost vs a NIFTYBEES
  SIP), and a critic that scores evidence, materiality and probability, flags
  traps and deepens the top 3. Ends in a fixed shape: brief, bull, bear, traps,
  verdict (buy / wait / avoid, or take / skip), what to watch, hand-off. About
  8-10 Agent calls. Not financial advice: nothing is bought without the
  user's explicit yes.
argument-hint: "SYMBOL [--mode longterm|intraday|swing]"
---

# trader-thesis

One symbol, one money decision, argued from both sides before you commit. The
method is parallel divergent ideation (the ADHD skill, adapted): many isolated
branches that each see only the same recorded numbers, then a critic. The
verdict is a summary of the evidence. It is never an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/thesis.py" brief IOC --mode longterm       # writes trader/thesis/IOC-brief.json
python3 "${CLAUDE_SKILL_DIR}/thesis.py" brief RELIANCE --mode swing --data trader/data
python3 "${CLAUDE_SKILL_DIR}/thesis.py" frames --mode longterm          # the frame table (--json for prompts)
python3 "${CLAUDE_SKILL_DIR}/thesis.py" render trader/thesis/IOC-thesis.json   # refuses a missing section
```

## Pre-flight gate

This costs about 8-10 Agent calls and a minute or two, several times a direct
answer. Do not pay it when a direct answer is enough.

- **Run** when the user typed `/trader-thesis` or asked for a thesis, a bull
  and bear case, a devil's advocate, or "is this a value trap", or when a real
  money decision is open-ended and the cost of the obvious answer being wrong
  is real (a first position, a large size, a concentrated bet).
- **Skip and answer directly** for a lookup ("what is IOC's PE"), a closed
  question ("quick", "just"), a tiny test position, or a symbol the user has
  already decided on. Append one line: "run /trader-thesis IOC for the
  argued case".

## Step 0: facts first

The branches may only argue from recorded numbers, so build them before any
branch runs. Run what is missing and skip what exists and is fresh:

1. trader-data: daily bars, `delivery`, `actions`, `index` (Nifty 50, India VIX),
   `flows`; trader-news for the symbol (it keeps the results and dividend board
   dates in trader/data/events.json, which the brief reads).
2. Long term: trader-invest `fetch` then `analyse SYM` (it saves
   trader/invest/SYM.json, which is how the analysis reaches the brief),
   and trader-scanner `longterm` for the swing and relative-strength tags.
3. Intraday or swing: trader-scanner, and trader-exit for `trader/plan.json`.
4. trader-news for headlines on the symbol (quote them in the branch input if
   used, with date and source; they are not in the brief file).
5. `thesis.py brief SYM --mode MODE`. Read its `missing` list and tell the
   user what is absent. A missing source is a stated gap, never a guess.

## Phase 1: DIVERGE (no critic)

Pick 5 frames from `thesis.py frames --mode MODE`, at least one tagged wild,
and always one bear-side frame. Spawn 5 **parallel** Agent calls, one per
frame. Each gets only: the brief JSON, the user's question and constraints
(capital, horizon), and the frame's vantage prompt. The instruction:

> You are in DIVERGENT mode. You are a generator, not a critic. Argue only
> from the numbers in the brief, and cite the field and its date. If a number
> you need is not in the brief, say so; do not invent one. Give 5 short
> distinct arguments under this frame, each with a rupee impact where you
> can. Do not evaluate or rank. Output a JSON array only:
> `[{"point": "...", "side": "bull|bear|risk", "evidence": "field and date", "rupee_impact": "..."}]`

Invariant: the calls are parallel and isolated. Never pass one branch's output
to another. Branches that see each other anchor each other.

## Phase 2: CONVERGE (critic on)

1. **Score** each argument 0 to 10 on **evidence** (is it backed by a number
   in the brief), **materiality** (rupee impact on the user's position) and
   **probability**. Evidence of 0 means it is opinion: keep it out of the
   verdict.
2. **Flag traps**: attractive but wrong (a 6% yield on a cyclical's peak
   profit, a low PE on shrinking earnings, a breakout on thin delivery). One
   line of reason each.
3. **Cluster** by angle (valuation, earnings quality, flows, execution,
   costs), not by wording.
4. **Deepen the top 3** (weighted score, traps excluded). One Agent call each:
   what would have to be true, the load-bearing risk, the observable trigger
   that would change the verdict, the first concrete step.

## Output shape (enforced by `render`)

Write the finished thesis to `trader/thesis/SYM-thesis.json`, then run
`thesis.py render` on it. It exits 1 and names the missing section.

| key | contents |
| --- | --- |
| `symbol`, `mode`, `date` | `mode` is longterm, intraday or swing |
| `brief` | a line or two: the question, the brief file, the data age and what is missing |
| `bull_case`, `bear_case` | lists of `{point, evidence, rupee_impact}` (non-empty) |
| `traps` | list of `{idea, reason}` (may be empty) |
| `verdict` | `{call, conviction, changes_if, rationale}`: call is buy / wait / avoid (longterm) or take / skip; conviction low / medium / high; `changes_if` is the one fact that would flip it |
| `watch` | list of `{what, when}`: levels from the brief, results and ex-dates from `actions` |
| `handoff` | `{skill, command, note}` |
| `deepened` | optional, the top 3: `{idea, must_be_true, load_bearing_risk, trigger, first_step}` |

Say the verdict in plain words with the data age and the gaps. Never round a
weak case up, and take a position: "wait, conviction medium" beats "it depends".

## Hand-off

- Buy or wait on a long-term name: trader-invest `plan SYM --amount N`.
- Take on a trade: trader-exit for stop and target, then trader-risk for size.
- Always trader-risk before any order, then trader-broker. The verdict is not
  an order. Nothing is bought or sold until the user types an explicit yes.

## Frames

About a dozen to choose from (`thesis.py frames`): bear / short seller, risk
manager, macro and FII/DII flows, contrarian / crowd positioning, value-trap
hunter, forensic accountant, 3am stop-out, tax and costs, time-horizon
inversion, regulator / SEBI surveillance, patient compounder, opportunity cost
vs a NIFTYBEES SIP. Tags: longterm, intraday, wild.

## Works with /adhd

If the general `/adhd` skill is installed, use it for open strategy
brainstorming ("how should I build a portfolio"). Use trader-thesis once a
concrete symbol decision is on the table: it adds the shared evidence brief,
trading frames and the fixed verdict. Do not chain them on the same question.

Without the Agent tool, run the frames one after another in this context and
say plainly that isolation is lost: later frames can anchor on earlier ones,
so lean on the brief and treat the result as one wider thought.

## Safety

- Education and tooling, not financial advice.
- Never place an order from this skill. A verdict goes to trader-risk and then
  trader-broker, and is placed only when the user types an explicit yes for
  that order.
- Never edit the brief to make a case look better; never invent a number.

## Licence

The method is adapted from ADHD (MIT, https://github.com/UditAkhourii/adhd).
See `THIRD_PARTY_NOTICES.md` in this folder for its licence and what changed.

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
