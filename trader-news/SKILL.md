---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every symbol you
  watch and flags what moves it: Yahoo Finance India headlines per symbol
  (SYMBOL.NS), Google News India search results for the company, and how far
  each stock is from yesterday's close in ₹. Shows only what is new since the
  last check, newest first, in IST, tags each item (earnings, analyst, deal,
  capital, circuit/halt, regulatory, macro...) and never repeats one. Also
  reads NSE bulk deals, block deals and short-selling disclosures on your
  symbols (size against the 20-day average volume, net buying per client),
  and puts a "results soon" warning at the top of the morning briefing when
  a stock's results or board meeting is within 2 NSE sessions (gap risk for
  an intraday trade). Use when the user says "any news on RELIANCE", "why is
  HINDCOPPER moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", "any bulk
  deals", "block deals", "who is buying", "results date", "when are results",
  "board meeting", or after /trader-scanner finds setups.
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
python3 "${CLAUDE_SKILL_DIR}/news.py" --deal-days 90          # deals window (default 30; --no-deals skips)
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-calendar           # skip the results-date check
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
| NSE bulk, block and short-selling deals | `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=DD-MM-YYYY&to=DD-MM-YYYY` (also `block_deals`, `short_selling`): three requests per run for the whole `--deal-days` window, not one per symbol, then filtered to your symbols. Same cookie-primed NSE session as the announcements |
| NSE board meetings | `https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=RELIANCE` (forthcoming meetings), one request per symbol on the same NSE session. A purpose containing "financial results" is a results date, "dividend" a dividend board date |
| `trader/data/calendar.json` | optional, from trader-data: `{"years": [..], "holidays": {"YYYY-MM-DD": name}, "special_sessions": {..}}`. Laid over the built-in 2024 to 2026 holiday tables to count sessions to a results date; without it the built-in tables alone are used, and a year neither covers counts weekends only, with a note |
| `trader/data/flows.csv` | optional, from trader-data: `date,category,buy_cr,sell_cr,net_cr`, rupees crore, category `FII/FPI` or `DII`. One line after the results dates: the latest day's net and the 5-session net. Market context, not a signal; a missing or unreadable file prints nothing (`--flows PATH` reads another file) |
| `trader/data/daily/<SYMBOL>.adj.csv` (else `.csv`) | `date,open,high,low,close,volume`: the 20-day average volume a deal is measured against |
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

## Bulk, block and short-selling deals

Deals on your symbols arrive as ordinary items tagged `deal`: new since the
last check, newest first, IST, never repeated. NSE gives a date, not a time,
so a deal is stamped 15:30 IST of its day. The title says who did what:
`Bulk deal: ALPHA GROWTH FUND bought 1,000,000 shares at ₹1,350.50 [2.50% of
20-day avg volume, LARGE]`. The percent needs the trader-data cache; without
it the deal prints with no percent. **LARGE** is 1 % or more of the 20-day
average volume. Short-selling rows name no client.

Under the items, `Net deals by client` lists bought minus sold per named
client over the window, for the symbols with a deal shown (all with `--all`;
`--json` always has `deal_flow`). A big net buyer is a lead, not a signal:
say who, how much, and that the filing does not say why.

An empty deal list is normal (most days have nothing on a given stock). If NSE
blocks (HTTP 401/403 or a block page), the session is primed once more, and if
it still refuses you get one note line; headlines and moves still print, and
the deals are simply "not checked", which is not "none".

## Results and board-meeting calendar

The first lines of the output (above the price lines) come from NSE's board
meetings, for results and dividend dates from today on:

```
RESULTS SOON  HINDCOPPER  results and dividend board date 2026-10-16 (Fri), 2 sessions away: gap risk, an overnight gap can jump past an intraday stop
coming up     RELIANCE    results 2026-10-19 (Mon), 3 sessions away
```

**RESULTS SOON** means a results date 0, 1 or 2 NSE sessions away (0 is
today: results often land after the close and the gap is the next morning).
Sessions skip weekends and NSE holidays (built-in 2024 to 2026, plus `trader/data/calendar.json`). Other
results and dividend dates within 30 days print as `coming up`; meetings on
other business (fund raising, say) are left out. `--json` has `results_soon`
(the symbols) and `calendar` (every date, with `sessions_away`). When
`flows.csv` exists the briefing adds one `FII/DII <date> (Rs crore): ...` line
and `--json` has `flows`; read it as the day's backdrop, never as a reason to
buy or sell.

What to do with it: tell the user before `/trader-entry` that an intraday
trade in a RESULTS SOON stock can gap past its stop at the open, and that a
swing trade carries the result. A board meeting date is the company's
intimation, not the outcome: open the announcement. No meetings listed means
none announced yet, which is not the same as none coming.

If NSE blocks the meetings request, the session is primed once more; if it
still refuses, one note line says results dates were not checked, and the
headlines, deals and moves still print. Do not tell the user "no results
soon" when that note is there.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **Exchange filings beat headlines.** Results, board meetings, dividends,
  bonus and fundraising are announced on nseindia.com (Corporate
  announcements). If a headline matters, check the company's announcement
  there before relying on it. This tool reads NSE's JSON endpoints politely
  (cookie-primed session, spacing between calls, one re-prime) and never
  scrapes pages.
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
- Never hammers a server: one request per feed per symbol, half a second
  apart; the deals are three requests for the whole run and the results
  calendar one per symbol, spaced and abandoned after the first refusal.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
