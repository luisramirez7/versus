# How to play Versus

Everyone gets the same fake cash, trades real stock/ETF prices while the round is live, and the pinned leaderboard decides who wins. No real money, ever.

## Quick start

1. `/party create` — pick starting cash (`1000` / `10000` / `100000`) and a length (`day` / `week` / `month` / `quarter`).
2. Hit **Join** on the lobby post (or run `/join`).
3. Host runs `/party start` — live now if the market's open, otherwise scheduled for the next open.
4. Trade: `/buy NVDA dollars:500` or `/sell AAPL all:true`. Fills post publicly.
5. `/portfolio` (private) to check yourself, `/chart NVDA` for a candlestick.
6. Watch the pinned leaderboard — it updates live.
7. Round ends automatically → final standings post → `/party create` to run it back.

## Everyday commands

- **`/buy symbol [shares] [dollars]`** — market buy, give shares *or* dollars, not both.
- **`/sell symbol [shares] [dollars] [all]`** — market sell, `all:true` closes the position.
- **`/portfolio [member]`** — your cash, positions, P&L, rank (private); add `member` to peek at someone else's.
- **`/leaderboard`** — re-posts (and re-pins) the standings.
- **`/rules`** — this round's exact rules: cash, window, fees, fill mechanics.

## Niche commands, with examples

- **`/quote symbol`** — price check without committing: `/quote TSLA` tells you the last price, day change, and whether it's actually tradeable this round.
- **`/history [member]`** — your last 15 fills, private: `/history` for yourself, `/history member:@Ana` to see someone else's trade log.
- **`/chart symbol [range] [compare]`** — beyond the default candlestick: `/chart NVDA range:1m` for a month view, or `/chart NVDA range:5d compare:AAPL,SPY` to overlay up to 3 tickers as % change.
- **`/party status`** — where the round stands right now: `/party status` shows time remaining, member count, start/end.
- **`/party cancel`** — host only, before it goes live: kills a lobby nobody's ready for yet.
- **`/party end`** — host only, live round: `/party end` settles immediately at current prices instead of waiting for the window to close.
- **`/leave`** — drop out before the round starts: `/leave`.
- **`/ask question`** — the copilot, read-only, never trades for you: `/ask why is NVDA down today?` (private) or `/ask how am I doing? public:true` to post the answer to the channel instead of just yourself.

Not investment advice. Paper money only.
