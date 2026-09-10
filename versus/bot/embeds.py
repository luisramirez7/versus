"""Every embed and feed line the bot posts. Copy lives here so it reads consistently."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import discord

from versus.engine.fills import FillResult
from versus.engine.rounds import PartyState
from versus.engine.rules import CASH_OPTIONS, PRESET_LABELS, Rules
from versus.engine.settlement import Settlement
from versus.engine.valuation import PortfolioView, Standing
from versus.market.universe import Eligibility

from .format import DISCLAIMER, arrow, compact, money, pct, qty, signed_money, spct, trunc, ts

GOLD = 0xF2B233
INK = 0x2B3440
UP = 0x3CC27A
DOWN = 0xF0626C

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


LATE = "⏱"


def _footer(e: discord.Embed, extra: str | None = None) -> discord.Embed:
    e.set_footer(text=f"{extra} · {DISCLAIMER}" if extra else DISCLAIMER)
    return e


def _joined_late(rnd, joined_at: datetime | None) -> bool:
    """A drop-in: joined after the round actually went live."""
    return rnd.start_at is not None and joined_at is not None and joined_at > rnd.start_at


def lobby_embed(st: PartyState) -> discord.Embed:
    rnd = st.round
    e = discord.Embed(title="🎉 Party lobby", color=GOLD)
    e.description = (
        f"**{PRESET_LABELS[rnd.preset]}** · {rnd.sessions} session{'s' if rnd.sessions > 1 else ''} · "
        f"{money(rnd.starting_cash)} each\n"
        f"Host: <@{st.party.host_user_id}>"
    )
    names = "\n".join(f"👤 {m.display_name}" for m in st.members) or "_Nobody yet — don't be shy._"
    e.add_field(name=f"Players ({len(st.members)})", value=names, inline=False)
    e.add_field(
        name="Join",
        value="Hit **Join** or type `/join`. Host drops the flag with `/party start` — "
        "and stragglers can still jump in once it's live. ⏱",
        inline=False,
    )
    return _footer(e)


def status_embed(st: PartyState) -> discord.Embed:
    rnd, party = st.round, st.party
    e = discord.Embed(title=f"Round status: {party.status}", color=GOLD if party.status == "live" else INK)
    lines = [
        f"**{PRESET_LABELS[rnd.preset]}** · {money(rnd.starting_cash)} each · {len(st.members)} players",
    ]
    if rnd.start_at:
        lines.append(f"Starts {ts(rnd.start_at)} ({ts(rnd.start_at, 'R')})")
    if rnd.end_at:
        lines.append(f"Ends {ts(rnd.end_at)} ({ts(rnd.end_at, 'R')})")
    e.description = "\n".join(lines)
    return _footer(e)


def live_message(st: PartyState) -> str:
    rnd = st.round
    players = len(st.members)
    return (
        f"🔔 **We're live!** {money(rnd.starting_cash)} each, {players} "
        f"trader{'s' if players != 1 else ''}, ends {ts(rnd.end_at)} ({ts(rnd.end_at, 'R')}). "
        f"Lock in — `/buy` to make your first move. 📈"
    )


def scheduled_message(st: PartyState) -> str:
    rnd = st.round
    return (
        f"📅 **Locked in.** Trading opens {ts(rnd.start_at)} ({ts(rnd.start_at, 'R')}) and the round "
        f"ends {ts(rnd.end_at)}. Get your watchlist ready. 👀"
    )


def board_embed(
    st: PartyState, board: list[Standing], as_of: datetime, delayed: bool = False
) -> discord.Embed:
    rnd = st.round
    e = discord.Embed(title="📊 Leaderboard", color=GOLD)
    if not board:
        e.description = "_No players yet. Be the first — `/buy` something._"
        return _footer(e)
    any_late = False
    rows = ["```", f"{'#':>2} {'player':<14} {'equity':>12} {'return':>8}  top"]
    for s in board:
        top = (
            f"{s.top_holding} {s.top_weight * 100:.0f}%"
            if s.top_holding and s.top_weight is not None
            else "cash"
        )
        late = _joined_late(rnd, s.joined_at)
        any_late = any_late or late
        row = f"{s.rank:>2} {trunc(s.display_name, 14):<14} {money(s.equity):>12} {pct(s.return_pct):>8}  {top}"
        rows.append(f"{row}  {LATE}" if late else row)
    rows.append("```")
    lead = board[0]
    if len(board) > 1:
        gap = lead.equity - board[1].equity
        flair = "🔥 " if lead.return_pct > 0 else ""
        rows.append(f"{MEDALS[1]} {flair}**{lead.display_name}** leads by {money(gap)}.")
    if any_late:
        rows.append(f"{LATE} dropped in mid-round — playing the clock that's left.")
    if rnd.end_at:
        rows.append(f"⏳ Ends {ts(rnd.end_at, 'R')}.")
    e.description = "\n".join(rows)
    stamp = f"Prices as of {as_of.astimezone().strftime('%-I:%M %p').lower()}"
    if delayed:
        stamp += " · data delayed 15 minutes"
    return _footer(e, stamp)


def portfolio_embed(v: PortfolioView) -> discord.Embed:
    color = UP if v.return_pct >= 0 else DOWN
    e = discord.Embed(title=f"{v.display_name}'s portfolio", color=color)
    head = f"**{money(v.equity)}** · {pct(v.return_pct)}"
    if v.rank:
        head += f" · rank #{v.rank}"
    head += f"\nCash {money(v.cash)} · {v.fills} fill{'s' if v.fills != 1 else ''}"
    e.description = head
    if v.holdings:
        rows = ["```", f"{'sym':<6} {'qty':>10} {'avg':>9} {'last':>9} {'value':>11} {'p&l':>10}"]
        for h in v.holdings:
            last = f"{h.last:,.2f}{'*' if h.stale else ''}"
            rows.append(
                f"{h.symbol:<6} {qty(h.qty):>10} {h.avg_cost:>9,.2f} {last:>9} {money(h.market_value):>11} "
                f"{signed_money(h.unrealized):>10}"
            )
        rows.append("```")
        if any(h.stale for h in v.holdings):
            rows.append("_\\* no fresh price yet; valued at cost_")
        e.add_field(name="Positions", value="\n".join(rows), inline=False)
    else:
        e.add_field(name="Positions", value="_All cash. `/buy` something._", inline=False)
    return _footer(e)


def quote_embed(
    symbol: str, price: Decimal | None, change: Decimal | None, elig: Eligibility
) -> discord.Embed:
    e = discord.Embed(title=f"{symbol} · {trunc(elig.name, 60)}", color=INK)
    if price is None:
        e.description = "_No price available right now._"
    else:
        chg = f" ({signed_money(change)})" if change is not None else ""
        e.description = f"**{money(price)}**{chg}"
    if elig.eligible:
        e.add_field(name="Tradeable", value="Yes", inline=True)
    else:
        e.add_field(name="Tradeable", value=f"No: {elig.reason}", inline=True)
    return _footer(e)


def rules_embed(rules: Rules, st: PartyState) -> discord.Embed:
    e = discord.Embed(title="Rules", color=INK)
    e.description = "\n".join(
        [
            f"**Window:** {rules.preset_label} ({rules.sessions} session{'s' if rules.sessions > 1 else ''}, regular hours only)",
            f"**Cash:** {money(rules.starting_cash)} each (options: {', '.join(f'${c:,}' for c in CASH_OPTIONS)})",
            "**Orders:** market only, fractional shares, long-only, no margin, $0 commission",
            f"**Fill:** first fresh price ≥ {rules.fill_delay_s:.0f}s after your order; buy +{rules.spread_bps / 2:.1f} bp, sell −{rules.spread_bps / 2:.1f} bp",
            (
                f"**Universe:** NYSE/Nasdaq stocks and plain ETFs, price ≥ ${rules.min_price}, "
                f"cap ≥ ${rules.min_market_cap / 1e6:.0f}M, avg volume ≥ {rules.min_avg_volume / 1e3:.0f}k; "
                "no leveraged or inverse ETFs"
            ),
            f"**Size cap:** {rules.adv_cap_pct:.0%} of average daily volume per order",
            "**Scoring:** total return. Ties: fewer fills, then earlier join.",
        ]
    )
    return _footer(e)


def recap_embed(st: PartyState, s: Settlement, extras=None) -> discord.Embed:
    rnd = st.round
    e = discord.Embed(title="🏁 Final standings", color=GOLD)
    rows = []
    any_late = False
    for st_ in s.standings:
        medal = MEDALS.get(st_.rank, f"`{st_.rank}.`")
        top = f" · {st_.top_holding}" if st_.top_holding else ""
        late = _joined_late(rnd, st_.joined_at)
        any_late = any_late or late
        tail = f" {LATE}" if late else ""
        rows.append(
            f"{medal} **{st_.display_name}** · {money(st_.equity)} · {pct(st_.return_pct)}{top}{tail}"
        )
    if any_late:
        rows.append(f"_{LATE} dropped in mid-round — played the time that was left._")
    if s.standings:
        w = s.standings[0]
        margin = ""
        if len(s.standings) > 1:
            margin = f" by {money(w.equity - s.standings[1].equity)}"
        rows.insert(0, f"🏆 **{w.display_name}** wins{margin} with {pct(w.return_pct)}.")
        rows.insert(
            1,
            f"_{PRESET_LABELS[rnd.preset]} · {money(rnd.starting_cash)} each · {len(s.standings)} players_\n",
        )
    e.description = "\n".join(rows) or "_Nobody played._"
    if s.best_trade:
        b = s.best_trade
        e.add_field(
            name="Best trade",
            value=f"{b.display_name}: {b.symbol} {signed_money(b.pnl)} ({pct(b.pct)})",
            inline=True,
        )
    if s.worst_trade:
        w_ = s.worst_trade
        e.add_field(
            name="Worst trade",
            value=f"{w_.display_name}: {w_.symbol} {signed_money(w_.pnl)} ({pct(w_.pct)})",
            inline=True,
        )
    if s.most_active:
        e.add_field(name="Most active", value=f"{s.most_active[0]} · {s.most_active[1]} fills", inline=True)
    if extras is not None:
        if extras.crowd_favorite:
            h = extras.crowd_favorite
            e.add_field(name="Crowd favorite 🔥", value=f"{h.label} · {h.count}×", inline=True)
        if extras.most_clowned:
            h = extras.most_clowned
            e.add_field(name="Most clowned 🤡", value=f"{h.label} · {h.count}×", inline=True)
    return _footer(e, f"Settled {s.ended_at.astimezone().strftime('%b %-d, %-I:%M %p').lower()}")


def feed_line(display_name: str, r: FillResult) -> str:
    verb = "bought" if r.side == "buy" else "sold"
    what = f"all {qty(r.qty)}" if (r.side == "sell" and r.closed_position) else qty(r.qty)
    line = (
        f"{arrow(r.side)} **{display_name}** {verb} {what} {r.symbol} @ {r.price:,.2f} ({money(r.notional)}"
    )
    if r.realized_pnl is not None:
        line += f", {signed_money(r.realized_pnl)} {pct(r.realized_pct or Decimal(0))}"
    return line + ")"


def fill_confirmation(r: FillResult) -> str:
    verb = "Bought" if r.side == "buy" else "Sold"
    parts = [
        (
            f"{verb} **{qty(r.qty)} {r.symbol}** @ {r.price:,.4f} for {money(r.notional)} "
            f"(quote {r.quote_price:,.2f}, filled {r.fill_delay_s:.0f}s after your order)."
        )
    ]
    if r.realized_pnl is not None:
        parts.append(f"Realized {signed_money(r.realized_pnl)} ({pct(r.realized_pct or Decimal(0))}).")
    if r.closed_position:
        parts.append("Position closed.")
    elif r.avg_cost_after is not None:
        parts.append(f"Now holding {qty(r.position_qty_after)} @ avg {r.avg_cost_after:,.2f}.")
    parts.append(f"Cash left: {money(r.cash_after)}.")
    return " ".join(parts)


def ask_embed(question: str, answer: str, model: str, lookups: int) -> discord.Embed:
    e = discord.Embed(title=trunc(question, 200), description=answer, color=INK)
    e.set_footer(text=f"{DISCLAIMER} · {model} · {lookups} lookup{'s' if lookups != 1 else ''}")
    return e


# ----- market data commands (inputs are the copilot fetchers' dicts) -----

DATA_FOOTER = f"{DISCLAIMER} · Data: Financial Modeling Prep"


def _data(e: discord.Embed, extra: str | None = None) -> discord.Embed:
    e.set_footer(text=f"{DATA_FOOTER} · {extra}" if extra else DATA_FOOTER)
    return e


def _table(header: str, rows: list[str]) -> str:
    return "\n".join(["```", header, *rows, "```"])


def movers_embed(d: dict) -> discord.Embed:
    if d["kind"] == "sectors":
        e = discord.Embed(title=f"Sector performance · {d['date']}", color=INK)
        rows = [f"{trunc(s['sector'], 24):<24} {spct(s['avg_change_pct']):>8}" for s in d["sectors"]]
        e.description = _table(f"{'sector':<24} {'avg chg':>8}", rows)
        return _data(e)
    titles = {"gainers": "Top gainers today", "losers": "Top losers today", "active": "Most active today"}
    e = discord.Embed(title=titles[d["kind"]], color={"gainers": UP, "losers": DOWN}.get(d["kind"], INK))
    if not d["movers"]:
        e.description = "_Nothing tradeable is moving yet._"
        return _data(e)
    rows = [
        f"{m['symbol']:<6} {m['price']:>9,.2f} {spct(m['change_pct']):>8}  {trunc(m['name'] or '', 22)}"
        for m in d["movers"]
    ]
    e.description = _table(f"{'sym':<6} {'price':>9} {'chg':>8}  name", rows)
    return _data(e, "tradeable names only")


def earnings_embed(d: dict) -> discord.Embed:
    e = discord.Embed(title=f"{d['symbol']} · earnings", color=INK)
    lines = []
    nxt = d.get("next_report")
    if nxt:
        when = "today" if nxt["days_away"] == 0 else f"in {nxt['days_away']}d"
        lines.append(
            f"**Next report:** {nxt['date']} ({when}) · EPS est **{nxt['eps_estimate']}** · "
            f"revenue est {compact(nxt['revenue_estimate'])}"
        )
    else:
        lines.append("**Next report:** not scheduled yet")
    if d["recent"]:
        rows = [
            f"{r['date']:<11} {r['eps'] if r['eps'] is not None else '—':>6} {r['eps_estimate'] if r['eps_estimate'] is not None else '—':>6} "
            f"{spct(r['eps_surprise_pct'], 1):>8}  {'beat' if r['beat'] else 'miss' if r['beat'] is not None else ''}"
            for r in d["recent"]
        ]
        lines.append(_table(f"{'date':<11} {'eps':>6} {'est':>6} {'surprise':>8}", rows))
        lines.append(f"Beat estimates **{d['beats']} of {d['of']}** recent quarters.")
    e.description = "\n".join(lines)
    return _data(e)


def calendar_embed(d: dict) -> discord.Embed:
    e = discord.Embed(title=f"Earnings · {d['from']} to {d['to']}", color=INK)
    if not d["events"]:
        e.description = f"_Nothing on the calendar for {d['scope']}._"
        return _data(e)
    rows = [
        f"{ev['date'][5:]:<6} {ev['symbol']:<6} {ev['eps_estimate'] if ev['eps_estimate'] is not None else '—':>7} "
        f"{compact(ev['revenue_estimate']):>9}"
        for ev in d["events"]
    ]
    e.description = _table(f"{'date':<6} {'sym':<6} {'eps est':>7} {'rev est':>9}", rows)
    more = f" · {d['matches'] - len(d['events'])} more" if d["matches"] > len(d["events"]) else ""
    return _data(e, f"{d['scope']}{more}")


def macro_embed(d: dict) -> discord.Embed:
    e = discord.Embed(title="Market snapshot", color=INK)
    if d["markets"]:
        rows = [
            f"{trunc(m['name'], 16):<16} {m['price']:>11,.2f} {spct(m['change_pct']):>8}"
            for m in d["markets"]
        ]
        e.description = _table(f"{'':<16} {'last':>11} {'chg':>8}", rows)
    y = d.get("treasury_yields_pct") or {}
    if any(k in y for k in ("3m", "2y", "10y", "30y")):
        e.add_field(
            name=f"Treasury yields · {y.get('as_of', '')}",
            value=" · ".join(f"{k} **{y[k]:.2f}%**" for k in ("3m", "2y", "10y", "30y") if k in y),
            inline=False,
        )
    econ = d.get("economy") or {}
    bits = []
    if "fed_funds_pct" in econ:
        bits.append(f"Fed funds **{econ['fed_funds_pct']['value']:.2f}%**")
    if "unemployment_pct" in econ:
        bits.append(f"Unemployment **{econ['unemployment_pct']['value']:.1f}%**")
    if "cpi_index" in econ:
        bits.append(f"CPI index **{econ['cpi_index']['value']:.1f}** ({econ['cpi_index']['as_of'][:7]})")
    if bits:
        e.add_field(name="Economy", value=" · ".join(bits), inline=False)
    return _data(e)


def analyst_embed(d: dict) -> discord.Embed:
    e = discord.Embed(title=f"{d['symbol']} · what analysts think", color=INK)
    g, t = d.get("analyst_grades") or {}, d.get("price_target") or {}
    lines = []
    if g:
        counts = " / ".join(
            f"{g.get(k, 0)} {label}" for k, label in (("buy", "buy"), ("hold", "hold"), ("sell", "sell"))
        )
        lines.append(f"**Consensus: {g.get('consensus', '—')}** ({counts})")
    if t.get("consensus") is not None:
        up = (
            f" · **{spct(t['upside_pct'])}** vs {money(d['price'])}"
            if t.get("upside_pct") is not None
            else ""
        )
        lines.append(
            f"**Target:** {money(t['consensus'])} (range {money(t.get('low', 0))}–{money(t.get('high', 0))}){up}"
        )
    if d.get("estimates"):
        rows = [
            f"{est['fiscal_year_end'][:4]:<5} {compact(est['revenue_avg']):>9} {est['eps_avg'] if est['eps_avg'] is not None else '—':>7} "
            f"{est['analysts'] or '—':>4}"
            for est in d["estimates"]
        ]
        lines.append(_table(f"{'FY':<5} {'revenue':>9} {'eps':>7} {'#':>4}", rows))
    s = d.get("fmp_scores_1_to_5") or {}
    if s:
        lines.append(f"FMP factor score **{s.get('rating', '—')}** (overall {s.get('overall', '—')}/5)")
    e.description = "\n".join(lines) or "_No analyst coverage._"
    return _data(e)


def ownership_embed(symbol: str, insiders: dict, congress: dict, institutions: dict) -> discord.Embed:
    e = discord.Embed(title=f"Who's trading {symbol}", color=INK)
    if "error" not in insiders:
        q = insiders.get("latest_quarter") or {}
        head = (
            f"Q{q['quarter']} {q['year']}: **{q.get('buys', 0)}** buys, **{q.get('sells', 0)}** sells\n"
            if q.get("quarter")
            else ""
        )
        rows = [
            f"{r['date']} {trunc(r['name'] or '', 18):<18} {r['type'] or '':<10} {compact(r['value']):>8}"
            for r in insiders.get("recent", [])[:5]
        ]
        e.add_field(name="Insiders", value=head + (_table("", rows) if rows else "_none_"), inline=False)
    if congress.get("trades"):
        rows = [
            f"{r['date']} {trunc(r['name'], 18):<18} {r['type']:<9} {r['amount']}"
            for r in congress["trades"][:5]
        ]
        e.add_field(
            name=f"Congress · {congress['disclosed_total']} disclosures", value=_table("", rows), inline=False
        )
    if "error" not in institutions:
        s = institutions.get("summary") or {}
        top = ", ".join(
            f"{h['investor'].title()} {h['ownership_pct']:.1f}%"
            for h in institutions.get("top_holders", [])[:3]
        )
        e.add_field(
            name=f"Institutions · {institutions['quarter']}",
            value=(
                f"**{s.get('investors_holding', '—')}** funds hold **{s.get('ownership_pct', 0):.1f}%** · "
                f"{s.get('increased', '—')} added, {s.get('reduced', '—')} cut\n{top}"
            ),
            inline=False,
        )
    if not e.fields:
        e.description = "_No ownership data._"
    return _data(e)


def screen_embed(d: dict) -> discord.Embed:
    f = d["filters"]
    bits = [b for b in (f.get("sector"), f.get("industry")) if b]
    cap = (
        f"{compact(f['marketCapMoreThan'])}–{compact(f['marketCapLowerThan'])}"
        if f.get("marketCapLowerThan")
        else f"≥ {compact(f['marketCapMoreThan'])}"
    )
    bits.append(cap)
    e = discord.Embed(title=f"Screen · {' · '.join(bits)}", color=INK)
    if not d["results"]:
        e.description = "_No matches._"
        return _data(e)
    rows = [
        f"{r['symbol']:<6} {r.get('price', 0):>9,.2f} {compact(r.get('market_cap')):>8}  {trunc(r.get('industry') or r.get('name') or '', 22)}"
        for r in d["results"]
    ]
    e.description = _table(f"{'sym':<6} {'price':>9} {'cap':>8}  industry", rows)
    return _data(e, "largest first · tradeable names only")
