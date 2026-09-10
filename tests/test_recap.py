from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from versus.bot.recap import Highlight, pick_highlights
from versus.charts.equity import render_equity_curve


def test_equity_curve_renders_png_and_skips_thin_series():
    t0 = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
    ana = [(t0 + timedelta(minutes=i), Decimal(1000) + i * Decimal("0.7")) for i in range(60)]
    luis = [(t0 + timedelta(minutes=i), Decimal(1000) - i * Decimal("0.3")) for i in range(60)]
    png = render_equity_curve({"Ana": ana, "Luis": luis}, starting_cash=Decimal(1000))
    assert png is not None and png[:4] == b"\x89PNG"
    assert render_equity_curve({"Ana": ana[:2]}, starting_cash=Decimal(1000)) is None
    assert render_equity_curve({}, starting_cash=Decimal(1000)) is None


def test_pick_highlights_ignores_unreacted_fills():
    h = lambda n, sym, side, c: Highlight(n, sym, side, c)
    tallies = [
        (h("Ana", "NVDA", "buy", 3), h("Ana", "NVDA", "buy", 0)),
        (h("Luis", "TSLA", "sell", 1), h("Luis", "TSLA", "sell", 4)),
    ]
    fav, clown = pick_highlights(tallies)
    assert fav.label == "Ana's NVDA buy" and fav.count == 3
    assert clown.label == "Luis's TSLA sell" and clown.count == 4
    assert pick_highlights([(h("A", "X", "buy", 0), h("A", "X", "buy", 0))]) == (None, None)
    assert pick_highlights([]) == (None, None)
