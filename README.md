# Versus

A Discord bot that runs timed paper-trading matches between friends. Everyone in a channel gets the same fake cash, trades real US stocks and ETFs at real prices while the round is live, and the pinned leaderboard decides who wins.

*Working name. Not a broker, not investment advice, no real money anywhere.*

## How a round goes

1. `/party create cash:1000 preset:day` opens a lobby in the channel. Friends hit **Join** (or `/join`). You can run a round solo — one player is enough to start.
2. `/party start` (host). If the market is open the round is live now; otherwise it's scheduled for the next open. It ends at the close of the last session (Day Sprint = 1 session, Week = 5, Month = 21, Quarter = 63). Latecomers can **Join** while it's live — matchmaking-style, they drop in with full starting cash and play whatever time is left.
3. `/buy NVDA dollars:500`, `/sell AAPL all:true`. Orders fill at the first fresh price at least one second after the bot receives them, so nobody can front-run a stale quote. Fills post publicly to the channel; `/portfolio` is private.
4. The pinned leaderboard updates after every fill and once a minute.
   `/chart NVDA range:5d` posts a candlestick or line chart with your own fills marked; `compare:AAPL,SPY` overlays up to three symbols as % change.
5. At the end the round settles from stored prices and the bot posts final standings with best and worst trades. `/party create` runs it back.
6. `/movers`, `/earnings [symbol]`, `/macro`, `/analyst symbol`, `/whoowns symbol` and `/screen` post market data straight from FMP: today's tradeable gainers and losers or sector moves, an earnings record or the week's calendar (your holdings first, then the S&P 500), indexes and yields, analyst targets, insider, congressional and 13F activity, and a sector/size screener.
7. `/ask why is NVDA down today?` (or `/ask how am I doing?`, `/ask who reports this week?`, `/ask is Congress buying NVDA?`) asks the copilot, which can look up quotes, momentum, fundamentals and statements, analyst targets, earnings, calendars, movers and sector moves, a stock screener, macro readings, insider, congressional and 13F ownership, ETF holdings, earnings-call transcripts, news, and your own portfolio before answering. It explains and compares; it never picks for you and cannot trade. Optional: set `FIREWORKS_API_KEY` in `.env`; without it the command says so and everything else works.

The full spec, including the fill model, universe rules, and data model, is in [SPEC.md](SPEC.md).

## Run it

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # DISCORD_TOKEN, FMP_API_KEY, DEV_GUILD_ID
python -m versus doctor   # checks the token, database, FMP, and the market calendar
python -m versus          # runs the bot
pytest -q                 # engine tests (fills, valuation, settlement, calendar, lifecycle)
```

**Discord setup.** Create an application at the Discord developer portal, add a bot, copy the token into `.env`. Invite it with the `bot` and `applications.commands` scopes and these permissions: Send Messages, Embed Links, Manage Messages (to pin the board), Read Message History. No privileged intents are needed. Set `DEV_GUILD_ID` to your server's id while developing so commands appear instantly.

**Market data.** Prices, eligibility, search, and the session calendar come from Financial Modeling Prep. Note that self-serve FMP plans do not license display to other users; see the licensing section in the spec before listing the bot publicly.

## Layout

```
versus/
  config.py        settings from .env
  db/              SQLAlchemy models (scaled-integer money, UTC timestamps) and session factory
  market/          FMP client, price poller with wait-for-fresh, NYSE calendar, tradeable-universe cache
  engine/          fills (the only writer of cash and positions), valuation, settlement, round lifecycle
  bot/             discord.py cogs, embeds, pinned-board manager, scheduler, service wiring
tests/             engine tests against in-memory SQLite with a fake clock and injected prices
```

## Deploy

One process, one small machine on Fly.io (app `versus-bot`, region `iad`, SQLite on a 1 GB volume). Never run two copies with the same token: they would answer every command twice.

```bash
brew install flyctl && fly auth login
fly apps create versus-bot --org personal
fly volumes create data --app versus-bot --region iad --size 1 --yes
grep -E '^(DISCORD_TOKEN|FMP_API_KEY|DEV_GUILD_ID)=' .env | fly secrets import --app versus-bot --stage
fly deploy --app versus-bot --ha=false --yes
fly logs --app versus-bot            # look for "logged in as"
```

Redeploy after changes with `fly deploy --app versus-bot --ha=false`. For anything shared across many servers, point `DATABASE_URL` at Postgres and install the `postgres` extra.
