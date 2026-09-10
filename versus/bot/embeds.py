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

from .format import DISCLAIMER, arrow, money, pct, qty, signed_money, trunc, ts

GOLD = 0xF2B233
INK = 0x2B3440
UP = 0x3CC27A
DOWN = 0xF0626C

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _footer(e: discord.Embed, extra: str | None = None) -> discord.Embed:
    e.set_footer(text=f"{extra} · {DISCLAIMER}" if extra else DISCLAIMER)
    return e


def lobby_embed(st: PartyState) -> discord.Embed:
    rnd = st.round
    e = discord.Embed(title="Party lobby", color=GOLD)
    e.description = (
        f"**{PRESET_LABELS[rnd.preset]}** · {rnd.sessions} session{'s' if rnd.sessions > 1 else ''} · "
        f"{money(rnd.starting_cash)} each\n"
        f"Host: <@{st.party.host_user_id}>"
    )
    names = "\n".join(f"• {m.display_name}" for m in st.members) or "_Nobody yet_"
    e.add_field(name=f"Players ({len(st.members)})", value=names, inline=False)
    e.add_field(
        name="Join", value="Hit **Join** or type `/join`. The host starts with `/party start`.", inline=False
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
    return (
        f"🔔 **Round is live.** {money(rnd.starting_cash)} each, {len(st.members)} players, "
        f"ends {ts(rnd.end_at)} ({ts(rnd.end_at, 'R')}). `/buy` to get started."
    )


def scheduled_message(st: PartyState) -> str:
    rnd = st.round
    return f"📅 Scheduled. Trading opens {ts(rnd.start_at)} and the round ends {ts(rnd.end_at)}."


def board_embed(
    st: PartyState, board: list[Standing], as_of: datetime, delayed: bool = False
) -> discord.Embed:
    rnd = st.round
    e = discord.Embed(title="Leaderboard", color=GOLD)
    if not board:
        e.description = "_No players yet._"
        return _footer(e)
    rows = ["```", f"{'#':>2} {'player':<14} {'equity':>12} {'return':>8}  top"]
    for s in board:
        top = (
            f"{s.top_holding} {s.top_weight * 100:.0f}%"
            if s.top_holding and s.top_weight is not None
            else "cash"
        )
        rows.append(
            f"{s.rank:>2} {trunc(s.display_name, 14):<14} {money(s.equity):>12} {pct(s.return_pct):>8}  {top}"
        )
    rows.append("```")
    lead = board[0]
    if len(board) > 1:
        gap = lead.equity - board[1].equity
        rows.append(f"{MEDALS[1]} **{lead.display_name}** leads by {money(gap)}.")
    if rnd.end_at:
        rows.append(f"Ends {ts(rnd.end_at, 'R')}.")
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
    e = discord.Embed(title="Final standings", color=GOLD)
    rows = []
    for st_ in s.standings:
        medal = MEDALS.get(st_.rank, f"`{st_.rank}.`")
        top = f" · {st_.top_holding}" if st_.top_holding else ""
        rows.append(f"{medal} **{st_.display_name}** · {money(st_.equity)} · {pct(st_.return_pct)}{top}")
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
