---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every symbol you
  watch and flags what moves it: Yahoo Finance India headlines per symbol
  (SYMBOL.NS), Google News India search results for the company, and how far
  each stock is from yesterday's close in ₹. Shows only what is new since the
  last check, newest first, in IST, tags each item (earnings, analyst, deal,
  capital, circuit/halt, regulatory, macro...) and never repeats one. Use
  when the user says "any news on RELIANCE", "why is HINDCOPPER moving",
  "what's the news on my watchlist", "morning briefing", "pre-market news",
  "monitor the news", "did anything break", or after /trader-scanner finds
  setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on results, a circuit, a
SEBI order or a block deal. Before a setup becomes a trade, this skill reads
headline feeds for every stock on the watchlist and tells you only what is
new. It also prints how far each stock has moved today, so a headline and a
move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE HDFCBANK --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-google --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner); NSE symbols, `.NS` optional |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| Google News RSS (India) | `https://news.google.com/rss/search?q=<COMPANY or SYMBOL>+NSE&hl=en-IN&gl=IN&ceid=IN:en` (`--no-google` to skip) |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag). query1 first, then query2 |
| NSE corporate announcements | when both headline feeds fail for a symbol (or with `--nse`): a cookie-primed session, `https://www.nseindia.com/` first, then `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE`. Results, board meetings, outcomes: the primary source |
| `trader/data/intraday/<SYMBOL>-5m.json`, `trader/data/daily/<SYMBOL>.csv` | the trader-data cache: the move when Yahoo's chart fails (or with `--cache`), with "using cached data from <time>" |
| `trader/state/news.json` | what has been shown (kept 30 days) |

The Google query uses the company name when the watchlist or portfolio has
one ("Reliance Industries"), else the symbol. A story found in both feeds is
shown once.

When Yahoo and Google are both blocked for a symbol, the check falls back to
NSE's corporate announcements; when NSE refuses too, the notes list the
pages to open by hand (the NSE quote page's announcements, Yahoo Finance
news, Google News). Say that the headline feeds were blocked; an empty list
then means "could not look", not "nothing happened". For the price move with
no live data, the error names the way out: `python3 trader-data/data.py
doctor`, or the Kite MCP `get_historical_data` then `data.py import-kite`.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **Exchange filings beat headlines.** Results, board meetings, dividends,
  bonus and fundraising are announced on nseindia.com (Corporate
  announcements). If a headline matters, check the company's announcement
  there before relying on it. This tool does not scrape NSE.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: earnings, analyst, legal, deal, capital (dividend, bonus, QIP, OFS,
  block deal), people, risk, halt (upper/lower circuit, ASM/GSM, suspension),
  regulatory (SEBI, RBI, USFDA), macro (RBI policy, FII/DII flows, crude,
  rupee, GIFT Nifty).
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too. A stock near its
  circuit limit can lock and stop trading for the day.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter first: anything tagged earnings, halt, legal,
   regulatory or risk, and every MOVING symbol.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "Q2 results
   are due this week: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: results inside the trade, a circuit, a QIP. Never act on a
   headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 09:15 open.

## Example: the reel

Monday 2026-10-19, HINDCOPPER. The check at 10:15 IST leads with "Hindustan
Copper Q2 results preview: net profit seen rising on higher realisations"
(09:40 IST, earnings) and "Metal stocks lead Nifty gains as FII buying
returns" (09:20 IST, macro). The brief: "Results are coming, so the stock
can gap overnight; today's trade is MIS, in and out before 15:15." At 10:52
the price line reads
`HINDCOPPER ₹324.85 +3.31% vs yesterday's close ₹314.45 at 10:52 IST  MOVING`.
When a symbol has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, half a second apart.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
