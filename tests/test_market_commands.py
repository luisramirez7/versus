"""The market slash commands' embeds, fed by the copilot fetchers over the table-driven fake."""

from __future__ import annotations

import pytest

from versus.bot.embeds import (
    analyst_embed,
    calendar_embed,
    earnings_embed,
    macro_embed,
    movers_embed,
    ownership_embed,
    screen_embed,
)
from versus.bot.format import compact, spct
from versus.copilot.tools import CopilotContext
from versus.copilot.tools.analyst import analyst_view, earnings
from versus.copilot.tools.calendar import calendar
from versus.copilot.tools.market import macro, movers, screen
from versus.copilot.tools.ownership import congress, insiders, institutions

from .test_copilot_fmp import fmp, svc  # noqa: F401  (fixture re-export)


def test_format_helpers():
    assert compact(5_417_511_070_000) == "$5.42T" and compact(4_894_465_080) == "$4.89B"
    assert compact(300_000_000) == "$300M" and compact(950) == "$950" and compact(None) == "—"
    assert compact(-1_500_000, prefix="") == "-1.50M"
    assert spct(9.0) == "+9.00%" and spct(-0.5, 1) == "−0.5%" and spct(None) == "—"


@pytest.fixture
def ctx(svc):  # noqa: F811
    return CopilotContext(svc, user_id=1, channel_id=1)


async def test_movers_and_sectors_embeds(ctx):
    e = movers_embed(await movers(ctx, "gainers"))
    assert e.title == "Top gainers today" and "AAPL" in e.description and "+9.00%" in e.description
    assert "MGN" not in e.description and "tradeable" in e.footer.text
    s = movers_embed(await movers(ctx, "sectors"))
    assert s.title.startswith("Sector performance") and s.description.index(
        "Technology"
    ) < s.description.index("Energy")


async def test_earnings_and_calendar_embeds(ctx):
    e = earnings_embed(await earnings(ctx, "AAPL"))
    assert "2026-10-29" in e.description and "in 43d" in e.description and "1 of 2" in e.description
    assert "beat" in e.description and "miss" in e.description
    c = calendar_embed(await calendar(ctx, "earnings", [], 7))
    assert c.title.startswith("Earnings · 2026-09-16") and "ORCL" in c.description and "AAPL" in c.description
    assert "S&P 500" in c.footer.text
    empty = calendar_embed(await calendar(ctx, "earnings", ["ZZZZ"], 7))
    assert "Nothing on the calendar" in empty.description


async def test_macro_and_analyst_embeds(ctx):
    m = macro_embed(await macro(ctx))
    assert "S&P 500" in m.description and "Bitcoin" in m.description
    assert m.fields[0].name.startswith("Treasury yields") and "10y **4.83%**" in m.fields[0].value
    assert "CPI index **326.0** (2025-12)" in m.fields[1].value

    a = analyst_embed(await analyst_view(ctx, "AAPL"))
    assert "Consensus: Buy" in a.description and "70 buy / 32 hold / 9 sell" in a.description
    assert "$150.00" in a.description and "+25.00%" in a.description
    assert "2026" in a.description and "2028" in a.description and "score **B**" in a.description


async def test_ownership_and_screen_embeds(ctx):
    o = ownership_embed(
        "AAPL", await insiders(ctx, "AAPL"), await congress(ctx, "AAPL"), await institutions(ctx, "AAPL")
    )
    names = [f.name for f in o.fields]
    assert (
        names[0] == "Insiders"
        and names[1].startswith("Congress · 2")
        and names[2] == "Institutions · Q2 2026"
    )
    assert "Q3 2026: **0** buys, **4** sells" in o.fields[0].value and "$456k" in o.fields[0].value
    assert "Whitehouse" in o.fields[1].value and "Blackrock 7.9%" in o.fields[2].value

    s = screen_embed(await screen(ctx, "Technology", None, 2_000_000_000, None, False, 10))
    assert s.title == "Screen · Technology · ≥ $2.00B" and "NVDA" in s.description
    e = screen_embed({"filters": {"marketCapMoreThan": 3e8, "marketCapLowerThan": 5e9}, "results": []})
    assert e.title == "Screen · $300M–$5.00B" and "No matches" in e.description
