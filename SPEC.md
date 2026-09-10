# Versus — Discord bot spec

*Working name. "Versus" is the mechanic (invest against your friends); rename freely. 2026-09-09.*

A Discord bot that runs a timed paper-trading match inside one channel. Everyone who joins gets the same fake cash, trades real US stocks and ETFs at real prices while the round is live, and the pinned leaderboard decides who wins. Discord supplies identity, the friend group, chat, and notifications. The bot supplies the match: rules, fills, valuation, standings, settlement.

## 1. Scope

**In v1**

- One party per channel, Free-For-All. Anyone in the channel can join before the round goes live.
- Window presets: Day Sprint (1 session), Week (5 sessions), Month (21 sessions), Quarter (63 sessions). A session is a regular NYSE trading day; rounds end at that session's 4:00 PM ET close.
- Market orders only, fractional shares, long-only, no margin, $0 commission.
- Fill at the first fresh price observed at least one second after the server receives the order. The player cannot know the fill price when they submit.
- Universe: NYSE, Nasdaq and NYSE American common stock and plain ETFs. Price ≥ $5, market cap ≥ $300M, 30-day average volume ≥ 500k shares, actively trading. No leveraged or inverse ETFs, no OTC.
- Public trade feed in the channel; private (ephemeral) portfolio view.
- Pinned leaderboard embed, edited after every fill and once a minute during the session.
- Settlement at the end time from the last observed prices, a recap embed with final standings and best/worst trades, then the channel is free for a new party.
- Scoring: total return. Ties broken by fewer fills, then earlier join.

**Explicitly later**

- Fund mode (team portfolios via Discord roles), house-rule toggles, "Beat the Market" scoring, limit orders, queued after-hours orders, crypto toggle, `/chart` PNG rendering, `/ask` copilot, prizes of any kind, a Discord Activity (embedded web UI) on top of the same engine.

## 2. Commands

All commands are slash commands. Every command acknowledges within 3 seconds (deferred where work is needed). "Host" is the member who created the party.

| Command | Who | What happens |
|---|---|---|
| `/party create [cash] [preset]` | anyone | Creates the channel's party in **lobby** status. `cash` ∈ {1000, 10000, 100000}, default 1000. `preset` ∈ {day, week, month, quarter}, default day. Posts the lobby embed with a **Join** button. Fails if the channel already has an unsettled party. |
| `/join` | anyone | Joins the lobby (also the Join button). Creates the member's portfolio with the starting cash. Allowed while the party is in lobby or scheduled; refused once live. |
| `/leave` | member | Leaves before the round is live. |
| `/party start` | host | If the market is open now, the round goes **live** immediately and ends at the close of the Nth session. If closed, the round is **scheduled** for the next open. Needs at least 2 members. Pins the leaderboard embed. |
| `/party status` | anyone | Lobby / schedule / live status embed: preset, cash, start, end, member count, time remaining. |
| `/party cancel` | host | Cancels a party that is not yet live. |
| `/party end` | host | Ends a live round now and settles at current prices (useful for testing and for "let's call it"). |
| `/buy symbol [shares] [dollars]` | member | Market buy. Exactly one of `shares` or `dollars`. Symbol autocomplete from FMP search, filtered to the tradeable universe. Deferred; replies when filled with price, qty, cash left. Posts a feed line publicly. |
| `/sell symbol [shares] [dollars] [all]` | member | Market sell. `all:true` closes the position. Same flow as buy. |
| `/portfolio [member]` | member | Ephemeral: cash, positions with qty, average cost, last price, unrealized P&L, equity, return, rank. Any member's portfolio can be viewed since trades are already public in the feed. |
| `/quote symbol` | anyone | Ephemeral quote: last price, day change, market cap, eligible or not (with the reason). |
| `/leaderboard` | anyone | Re-posts the standings embed (and re-pins it if the pin was lost). |
| `/history [member]` | member | Ephemeral: last 15 fills. |
| `/rules` | anyone | The round's rules as an embed. |

Error replies are ephemeral and say what to do next: "Market is closed. Opens Thu 9:30 AM ET. After-hours orders come in v1.1." / "NVDX is a leveraged ETF and not tradeable in this round." / "Not enough cash: $412.10 available, order needs $500.00."

## 3. Bot-posted messages

| Event | Message |
|---|---|
| Party created | Lobby embed: preset, cash, host, members list, **Join** button. Edited as members join. |
| Round scheduled | "Scheduled. Trading opens Thu Sep 10, 9:30 AM ET and the round ends Thu Sep 10, 4:00 PM ET." |
| Round live | "🔔 Round is live. $1,000 each, 5 players, ends Fri 4:00 PM ET. `/buy` to get started." Leaderboard embed posted and pinned. |
| Fill | Feed line, public, plain text: `▲ Ana bought 2.235 NVDA @ 223.67 ($500.00)` / `▼ Luis sold all 3 TSLA @ 367.81 ($1,103.43, +4.2%)`. |
| Rejected order | Ephemeral only. Nothing public. |
| Leaderboard | One pinned embed, edited in place. Rank, name, equity, return, top holding. Footer: "Prices as of 2:41 PM ET · delayed data notice when applicable". |
| Last hour | "⏱ One hour left." (Day Sprint only; longer rounds get a "final session" notice at that day's open.) |
| Settled | Recap embed: final standings with return %, winner callout, best single trade, worst single trade, most trades, then "Channel is free. `/party create` to run it back." |

## 4. Rules engine

```
starting_cash        1_000 | 10_000 | 100_000
preset               day=1 | week=5 | month=21 | quarter=63 sessions
order types          market only
sizing               shares (6 dp) or dollars; min notional $1
commission           0
spread_bps           5   (buy at quote + 2.5 bp, sell at quote − 2.5 bp)
fill_delay_s         1   (first quote observed ≥ 1 s after receipt)
fill_timeout_s       30  (if no fresh quote arrives, reject: "no fresh price")
universe             exchange ∈ {NYSE, NASDAQ, AMEX}; price ≥ 5; market_cap ≥ 300M;
                     avg_volume ≥ 500k; actively trading; not leveraged/inverse (name match: 2X, 3X, Ultra, Inverse, Short, Bull, Bear, Daily … ETF)
adv_cap              2% of avg_volume per order
short selling        no (sell qty ≤ held qty)
hours                regular session only, 9:30–16:00 ET, NYSE holidays and half days from FMP
scoring              total_return = equity / starting_cash − 1
tie-break            fewer fills, then earlier joined_at
```

Fill sequence for `/buy NVDA dollars:500`:

1. Ack (defer, ephemeral). Record `received_at`.
2. Validate: party live; member has a portfolio; market open; symbol eligible (cached instrument row, refreshed daily); requested notional ≤ cash (for dollars) or shares × last × (1 + spread) ≤ cash (for shares); order shares ≤ 2% ADV.
3. Await the price service: the first observation for NVDA with `observed_at ≥ received_at + 1s`. The poller runs every 5 s during the session, so this resolves in 1–10 s.
4. One DB transaction under a per-portfolio asyncio lock: insert order (filled) and fill, upsert position with new average cost, debit cash. Reject inside the transaction if cash is no longer sufficient at the fill price (recomputes qty for dollar orders instead: qty = dollars / fill price).
5. Reply ephemerally with the fill; post the feed line; mark the party's board dirty.

Sells mirror buys; `all:true` sells the full position; realized P&L on the sell is computed from average cost and shown in the feed line.

## 5. Valuation, leaderboard, settlement

- `equity = cash + Σ qty × last_price` from the in-memory price map (falls back to the latest price snapshot if a symbol has never been polled this process).
- The board refreshes when dirty (debounced 3 s) and every 60 s during the session. Outside the session it refreshes once at the close and is otherwise static.
- Every 60 s during the session a `leaderboard_snapshots` row is written per portfolio (for the recap and a later equity chart).
- At `end_at` the scheduler flips the round to **settling**, takes the last observed price per held symbol at or before `end_at`, computes final equity and ranks, writes `rounds.settlement_json` (prices used, equities, ranks, fills count), posts the recap, marks the round and party **settled**. Re-running settlement is a no-op once settled.

## 6. Price service

- One poller. Every 5 s during the session it calls FMP `stable/batch-quote-short?symbols=` for the union of all symbols held or pending across live rounds (chunks of 100). One call per chunk. At a few hundred symbols this is well under FMP's limits.
- Observations go to an in-memory `{symbol: (price, observed_at)}` map and, once a minute per symbol, to `price_snapshots`.
- Eligibility data comes from `stable/profile?symbol=` (price, marketCap, averageVolume, exchange, isEtf, isActivelyTrading, companyName), cached in `instruments` for 24 h.
- Market hours from `stable/exchange-market-hours?exchange=NYSE` and `stable/holidays-by-exchange`, cached daily. Half days honored via `adjCloseTime`.
- Autocomplete from `stable/search-symbol?query=` filtered to the three exchanges, top 10.

Verified live on 2026-09-09: batch quotes return `symbol, price, change, volume`; profile returns the fields above; search returns `symbol, name, exchange`; market hours return open/close and `isMarketOpen`; holidays return `date, isClosed, adjCloseTime`.

## 7. Data model

```
parties(id, guild_id, channel_id, host_user_id, status lobby|scheduled|live|settling|settled|cancelled,
        lobby_message_id, board_message_id, created_at)
rounds(id, party_id, preset, sessions, starting_cash, spread_bps, start_at, end_at,
       status, settlement_json, settled_at)
portfolios(id, round_id, owner_type user|team, owner_id (discord user id), display_name, cash, joined_at)
positions(portfolio_id, symbol, qty, avg_cost)                    pk (portfolio_id, symbol)
orders(id, portfolio_id, symbol, side, req_shares, req_dollars, status pending|filled|rejected,
       reject_reason, interaction_id unique, received_at)
fills(id, order_id, price, qty, notional, realized_pnl, quoted_at, filled_at)   append-only
price_snapshots(symbol, price, observed_at)                        pk (symbol, observed_at)
leaderboard_snapshots(round_id, portfolio_id, ts, equity, cash, rank)
instruments(symbol, name, exchange, is_etf, price, market_cap, avg_volume, eligible, reason, checked_at)
```

Monetary columns are `Numeric(18, 4)`, quantities `Numeric(18, 6)`. SQLite for development and small deployments, Postgres for anything shared; SQLAlchemy 2 async covers both. All timestamps UTC; display in ET.

## 8. Scheduler

One asyncio task, tick every 15 s:

- `scheduled → live` when `now ≥ start_at` (post the live message, pin the board).
- `live → settling → settled` when `now ≥ end_at` (settle, post recap, unpin).
- Board refresh on dirty (debounced) and every 60 s during the session; snapshot every 60 s.
- Poller cadence: 5 s in session, idle otherwise. Transitions are guarded by the current status so a missed tick is harmless.

## 9. Discord specifics

- **Intents:** default only. No message-content intent needed; everything is interactions.
- **Permissions on invite:** Send Messages, Embed Links, Manage Messages (to pin), Read Message History, Use Application Commands.
- **Command sync:** guild-scoped to `DEV_GUILD_ID` in development (instant), global in production (up to an hour to propagate).
- **Rate limits:** the pinned board is edited at most once per 3 s per channel; feed lines are ordinary messages. Fine for friend-group volume.
- **Identity:** `owner_id` is the Discord user id; `display_name` is captured at join and refreshed at settle.
- **Ephemeral vs public:** portfolio, quote, history, errors are ephemeral. Fills, lobby, board, recap are public.
- **Verification:** past 100 servers Discord requires bot verification and a privacy policy. Not a v1 concern.

## 10. Not investment advice, and data licensing

Every embed footer carries: "Paper trading with virtual money. Not a broker or investment adviser. Not investment advice." Prices shown to other members in a channel are third-party display under every self-serve data plan, including FMP Ultimate; a small private test is one thing, a public bot listing is another. Get a delayed-display quote from FMP Enterprise or EODHD before listing the bot, and add the "Data delayed 15 minutes" legend to the board footer when that feed is wired.

## 11. Hosting

One process. Dockerfile included; runs on a single small Fly.io machine with a volume for SQLite, or point `DATABASE_URL` at Postgres. Secrets: `DISCORD_TOKEN`, `FMP_API_KEY`. Roughly $5–10 a month on top of the FMP plan.
