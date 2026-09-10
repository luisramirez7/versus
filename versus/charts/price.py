"""Price and comparison charts as PNG bytes.

Rendering goes through the object-oriented API with an explicit Agg canvas, so no pyplot
state is ever created (nothing to show, nothing to close) and it is safe off the event loop.
"""

from __future__ import annotations

import io
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import matplotlib
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from versus.market.fmp import Bar

matplotlib.use("Agg")

CANDLE_MAX = 160  # more bars than this and candles turn into a line


@dataclass(frozen=True)
class ChartStyle:
    background: str = "#313338"
    panel: str = "#2B2D31"
    ink: str = "#E6E9EF"
    muted: str = "#98A2B0"
    accent: str = "#F2B233"
    up: str = "#3CC27A"
    down: str = "#F0626C"
    grid: str = "#3F4147"
    series: tuple[str, ...] = ("#F2B233", "#5DADE2", "#3CC27A", "#C39BD3")
    width: float = 10.0
    height: float = 5.6
    dpi: int = 160


DARK = ChartStyle()


@dataclass(frozen=True)
class FillMarker:
    ts: datetime
    side: str  # buy|sell
    price: Decimal


# ----- public -----


def render_price_chart(
    bars: Sequence[Bar],
    *,
    symbol: str,
    label: str,
    fills: Sequence[FillMarker] = (),
    style: ChartStyle = DARK,
) -> bytes:
    if not bars:
        raise ValueError("no bars to draw")
    fig = _figure(style)
    gs = fig.add_gridspec(
        2, 1, height_ratios=(78, 22), hspace=0.06, left=0.03, right=0.91, top=0.9, bottom=0.09
    )
    ax = fig.add_subplot(gs[0])
    vax = fig.add_subplot(gs[1], sharex=ax)
    _style_axis(ax, style)
    _style_axis(vax, style)

    n = len(bars)
    xs = list(range(n))
    opens = [float(b.open) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    closes = [float(b.close) for b in bars]
    colors = [style.up if c >= o else style.down for o, c in zip(opens, closes, strict=True)]

    if n <= CANDLE_MAX:
        _candles(ax, xs, opens, highs, lows, closes, colors)
    else:
        ax.plot(xs, closes, color=style.accent, linewidth=1.4)
        ax.fill_between(xs, closes, min(lows), color=style.accent, alpha=0.12, linewidth=0)
    ax.set_ylim(*_pad(min(lows), max(highs)))
    ax.set_xlim(-0.8, n - 0.2)

    vax.bar(xs, [b.volume for b in bars], width=0.7, color=colors, alpha=0.55, linewidth=0)
    vax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _compact(v)))
    vax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=3))
    vax.set_ylim(0, max(b.volume for b in bars) * 1.1 or 1)

    _fill_markers(ax, bars, fills, style)

    idx, labels = _time_ticks([b.ts for b in bars])
    vax.set_xticks(idx, labels)
    ax.tick_params(labelbottom=False)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.2f}"))

    ax.set_title(f"{symbol} · {label}", loc="left", color=style.ink, fontsize=13, fontweight="bold")
    last, change = closes[-1], closes[-1] - opens[0]
    pct = change / opens[0] * 100 if opens[0] else 0.0
    ax.set_title(
        f"{last:,.2f}   {_signed(change, ',.2f')} ({_signed(pct, '.2f')}%)",
        loc="right",
        color=style.up if change >= 0 else style.down,
        fontsize=12,
    )
    return _png(fig, style)


def render_comparison(series: dict[str, Sequence[Bar]], *, label: str, style: ChartStyle = DARK) -> bytes:
    if not 2 <= len(series) <= 4:
        raise ValueError("compare 2 to 4 symbols")
    if any(not bars for bars in series.values()):
        raise ValueError("every series needs at least one bar")
    fig = _figure(style)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.03, right=0.91, top=0.9, bottom=0.09)
    _style_axis(ax, style)

    timeline = sorted({b.ts for bars in series.values() for b in bars})
    position = {ts: i for i, ts in enumerate(timeline)}
    for (symbol, bars), color in zip(series.items(), style.series, strict=False):
        base = float(bars[0].close)
        xs = [position[b.ts] for b in bars]
        ys = [(float(b.close) / base - 1) * 100 if base else 0.0 for b in bars]
        ax.plot(xs, ys, color=color, linewidth=1.6, label=f"{symbol}  {_signed(ys[-1], '.2f')}%")
    ax.axhline(0, color=style.muted, linewidth=0.8, alpha=0.6)
    ax.set_xlim(-0.8, len(timeline) - 0.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{_signed(v, '.1f')}%"))

    idx, labels = _time_ticks(timeline)
    ax.set_xticks(idx, labels)
    ax.legend(loc="upper left", frameon=False, labelcolor=style.ink, fontsize=10)
    ax.set_title(
        f"{' vs '.join(series)} · {label}", loc="left", color=style.ink, fontsize=13, fontweight="bold"
    )
    return _png(fig, style)


# ----- pieces -----


def _figure(style: ChartStyle) -> Figure:
    fig = Figure(figsize=(style.width, style.height), dpi=style.dpi, facecolor=style.background)
    FigureCanvasAgg(fig)
    return fig


def _style_axis(ax: Axes, style: ChartStyle) -> None:
    ax.set_facecolor(style.panel)
    ax.yaxis.tick_right()
    ax.grid(axis="y", color=style.grid, linewidth=0.6, alpha=0.7)
    ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)
    for side in ("top", "left", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(style.grid)
    ax.tick_params(colors=style.muted, labelsize=9, length=0)


def _candles(ax: Axes, xs, opens, highs, lows, closes, colors) -> None:
    span = (max(highs) - min(lows)) or 1.0
    ax.vlines(xs, lows, highs, colors=colors, linewidth=0.9)
    bodies = [max(abs(c - o), span * 0.002) for o, c in zip(opens, closes, strict=True)]
    bottoms = [min(o, c) for o, c in zip(opens, closes, strict=True)]
    ax.bar(xs, bodies, bottom=bottoms, width=0.62, color=colors, linewidth=0)


def _fill_markers(ax: Axes, bars: Sequence[Bar], fills: Sequence[FillMarker], style: ChartStyle) -> None:
    if not fills:
        return
    stamps = [b.ts for b in bars]
    step = stamps[-1] - stamps[-2] if len(stamps) > 1 else timedelta(days=1)
    for f in fills:
        if f.ts < stamps[0] or f.ts >= stamps[-1] + step:
            continue
        i = bisect_right(stamps, f.ts) - 1
        buy = f.side == "buy"
        ax.scatter(
            [i],
            [float(f.price)],
            marker="^" if buy else "v",
            s=70,
            color=style.up if buy else style.down,
            edgecolors=style.background,
            linewidths=0.8,
            zorder=5,
        )


def _time_ticks(stamps: Sequence[datetime], target: int = 7) -> tuple[list[int], list[str]]:
    """Tick positions (bar indexes) and labels. Intraday bars get times, or day names across days."""
    n = len(stamps)
    if n == 0:
        return [], []
    intraday = n > 1 and stamps[1] - stamps[0] < timedelta(days=1)
    days = sorted({t.date() for t in stamps})
    if intraday and len(days) > 1:
        first_of_day = {t.date(): i for i, t in reversed(list(enumerate(stamps)))}
        idx = [first_of_day[d] for d in days]
        keep = max(1, -(-len(idx) // target))
        idx = idx[::keep]
        return idx, [stamps[i].strftime("%a %b %-d") for i in idx]
    step = max(1, (n - 1) // (target - 1)) if n > 1 else 1
    idx = list(range(0, n, step))
    if intraday:
        fmt = "%H:%M"
    elif stamps[-1] - stamps[0] > timedelta(days=250):
        fmt = "%b %Y"
    else:
        fmt = "%b %-d"
    return idx, [stamps[i].strftime(fmt) for i in idx]


def _pad(lo: float, hi: float, frac: float = 0.06) -> tuple[float, float]:
    span = (hi - lo) or abs(hi) * 0.02 or 1.0
    return lo - span * frac, hi + span * frac


def _compact(v: float) -> str:
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}".removesuffix(".0") + suffix
    return f"{v:.0f}"


def _signed(v: float, spec: str) -> str:
    return ("+" if v >= 0 else "−") + format(abs(v), spec)


def _png(fig: Figure, style: ChartStyle) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=style.background, dpi=style.dpi)
    fig.clear()
    return buf.getvalue()
