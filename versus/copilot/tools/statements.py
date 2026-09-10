"""FMP financial statements: income-statement, balance-sheet-statement, cash-flow-statement,
financial-growth. One tool, an enum picks the statement."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _error, _one, pct, pick

Statement = Literal["income", "balance", "cashflow", "growth"]
Period = Literal["annual", "quarter"]
MAX_PERIODS = 4

PATHS: dict[str, str] = {
    "income": "income-statement",
    "balance": "balance-sheet-statement",
    "cashflow": "cash-flow-statement",
    "growth": "financial-growth",
}
COMMON = {"date": "date", "period": "period", "fiscalYear": "fiscal_year"}
FIELDS: dict[str, dict[str, str]] = {
    "income": {
        **COMMON,
        "revenue": "revenue",
        "grossProfit": "gross_profit",
        "researchAndDevelopmentExpenses": "r_and_d",
        "operatingExpenses": "operating_expenses",
        "operatingIncome": "operating_income",
        "ebitda": "ebitda",
        "netIncome": "net_income",
        "epsDiluted": "eps_diluted",
        "weightedAverageShsOutDil": "diluted_shares",
    },
    "balance": {
        **COMMON,
        "cashAndShortTermInvestments": "cash_and_short_term",
        "totalCurrentAssets": "current_assets",
        "totalAssets": "total_assets",
        "totalCurrentLiabilities": "current_liabilities",
        "shortTermDebt": "short_term_debt",
        "longTermDebt": "long_term_debt",
        "totalDebt": "total_debt",
        "netDebt": "net_debt",
        "totalLiabilities": "total_liabilities",
        "totalStockholdersEquity": "equity",
    },
    "cashflow": {
        **COMMON,
        "netIncome": "net_income",
        "operatingCashFlow": "operating_cash_flow",
        "capitalExpenditure": "capex",
        "freeCashFlow": "free_cash_flow",
        "stockBasedCompensation": "stock_comp",
        "commonStockRepurchased": "buybacks",
        "netDividendsPaid": "dividends_paid",
        "acquisitionsNet": "acquisitions",
        "netChangeInCash": "net_change_in_cash",
    },
    "growth": {
        **COMMON,
        "revenueGrowth": "revenue_growth",
        "grossProfitGrowth": "gross_profit_growth",
        "operatingIncomeGrowth": "operating_income_growth",
        "netIncomeGrowth": "net_income_growth",
        "epsdilutedGrowth": "eps_growth",
        "freeCashFlowGrowth": "fcf_growth",
        "debtGrowth": "debt_growth",
        "threeYRevenueGrowthPerShare": "revenue_per_share_growth_3y",
        "fiveYRevenueGrowthPerShare": "revenue_per_share_growth_5y",
    },
}
NOTES = {
    "income": "amounts in the reported currency; margins are % of revenue",
    "balance": "amounts in the reported currency; net_debt = total_debt - cash",
    "cashflow": "amounts in the reported currency; buybacks and dividends are negative outflows",
    "growth": "growth figures are fractions vs the prior period (0.06 = +6%); *_3y/_5y are cumulative",
}


def shape(statement: str, row: dict) -> dict:
    out = pick(row, FIELDS[statement])
    if statement == "income":
        rev = row.get("revenue")
        for src, label in (
            ("grossProfit", "gross_margin_pct"),
            ("operatingIncome", "operating_margin_pct"),
            ("netIncome", "net_margin_pct"),
        ):
            m = pct(row.get(src), rev)
            if m is not None:
                out[label] = m
    if statement == "cashflow":
        m = pct(row.get("freeCashFlow"), row.get("operatingCashFlow"))
        if m is not None:
            out["fcf_conversion_pct"] = m
    return out


async def financials(ctx: CopilotContext, symbol: str, statement: str, period: str, limit: int) -> dict:
    if statement not in PATHS:
        return {"error": f"statement must be one of {', '.join(PATHS)}"}
    if period not in ("annual", "quarter"):
        return {"error": "period must be annual or quarter"}
    n = max(1, min(int(limit), MAX_PERIODS))
    rows = await ctx.svc.fmp.rows(PATHS[statement], symbol=symbol, period=period, limit=n)
    if not rows:
        return {"error": f"{symbol}: no {period} {statement} statement (ETFs and some ADRs have none)"}
    rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)[:n]
    return {
        "symbol": symbol,
        "statement": statement,
        "period": period,
        "currency": rows[0].get("reportedCurrency"),
        "periods": [shape(statement, r) for r in rows],
        "note": NOTES[statement],
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def get_financials(
        symbol: str, statement: Statement = "income", period: Period = "annual", limit: int = 4
    ) -> str:
        """Reported financial statements, newest first: income (revenue, margins, EPS), balance (cash,
        debt, equity), cashflow (operating cash flow, capex, free cash flow, buybacks) or growth (period
        over period growth rates). Use it for "is revenue growing", "how much debt", "does X buy back
        stock". For ratios like P/E use get_company instead.

        Args:
            symbol: One ticker.
            statement: income, balance, cashflow or growth.
            period: annual or quarter.
            limit: How many periods, at most 4.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await financials(ctx, sym, statement, period, limit))

    return [get_financials]
