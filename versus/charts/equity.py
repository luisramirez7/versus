"""Equity-curve image for the end-of-round recap. Pure: no discord, no network."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class EquityStyle:
    background: str = "#313338"
    panel: str = "#2B2D31"
    ink: str = "#E6E9EF"
    muted: str = "#98A2B0"
    accent: str = "#F2B233"
    up: str = "#3CC27A"
    down: str = "#F0626C"
    lines: tuple[str, ...] = (
        "#F2B233",
        "#5AA9FF",
        "#3CC27A",
        "#F0626C",
        "#C084FC",
        "#FB923C",
        "#2DD4BF",
        "#E879F9",
    )


DARK = EquityStyle()

Point = tuple[datetime, Decimal]


def render_equity_curve(
    series: dict[str, list[Point]],
    *,
    starting_cash: Decimal,
    title: str = "Equity",
    style: EquityStyle = DARK,
) -> bytes | None:
    """One line per player, % return on the y-axis. Returns None if there isn't enough data to draw."""
    series = {name: pts for name, pts in series.items() if len(pts) >= 3}
    if not series or starting_cash <= 0:
        return None

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=160)
    fig.patch.set_facecolor(style.background)
    ax.set_facecolor(style.panel)

    # Rank by final return so the legend reads like the leaderboard.
    ordered = sorted(series.items(), key=lambda kv: kv[1][-1][1], reverse=True)
    for i, (name, pts) in enumerate(ordered):
        xs = [p[0].astimezone(ET) for p in pts]
        ys = [float((p[1] / starting_cash - 1) * 100) for p in pts]
        color = style.lines[i % len(style.lines)]
        ax.plot(xs, ys, color=color, linewidth=2.2 if i == 0 else 1.6, label=f"{name}  {ys[-1]:+.2f}%")
        ax.plot(xs[-1], ys[-1], "o", color=color, markersize=5)

    ax.axhline(0, color=style.muted, linewidth=0.8, alpha=0.6)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.1f}%"))
    ax.grid(axis="y", color=style.muted, alpha=0.15, linewidth=0.6)
    ax.grid(axis="x", visible=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=style.muted, labelsize=9)

    first, last = ordered[0][1][0][0].astimezone(ET), ordered[0][1][-1][0].astimezone(ET)
    fmt = "%-I:%M %p" if (last - first).days < 1 else "%b %-d"
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda v, _: matplotlib.dates.num2date(v, tz=ET).strftime(fmt))
    )
    ax.set_title(title, loc="left", color=style.ink, fontsize=13, fontweight="bold", pad=12)
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=style.ink)
    for text in leg.get_texts():
        text.set_color(style.ink)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
