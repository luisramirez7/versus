"""Read-only tools the /ask copilot can call, one module per FMP API family plus the game itself.

Built per request from a CopilotContext, so the model never passes ids: it sees the caller's own
portfolio only, the round it is in, and public market data. There is no trading tool by design.

Each module exposes `build(ctx) -> list[BaseTool]` and the plain async fetchers the tools wrap, so a
slash command (or a per-family sub-agent later) can reuse a fetcher without going through the model.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from . import analyst, calendar, company, game, market, news, ownership, quotes, statements
from ._base import CopilotContext, strip_html

MODULES = (game, quotes, company, statements, analyst, calendar, market, ownership, news)


def build_tools(ctx: CopilotContext) -> list[BaseTool]:
    return [t for m in MODULES for t in m.build(ctx)]


__all__ = ["MODULES", "CopilotContext", "build_tools", "strip_html"]
