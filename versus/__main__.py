"""`python -m versus` runs the bot. `python -m versus doctor` checks the environment."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from versus.config import settings


async def doctor() -> int:
    from versus.db import Database
    from versus.market.clock import MarketCalendar
    from versus.market.fmp import FMPClient

    ok = True
    print(f"Discord token   {'set' if settings.discord_token else 'MISSING'}")
    ok &= bool(settings.discord_token)
    print(f"Dev guild       {settings.dev_guild_id or '(none: commands sync globally)'}")
    print(f"Database        {settings.database_url}")
    try:
        db = Database(settings.database_url)
        await db.create_all()
        await db.dispose()
        print("                tables ok")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"                FAILED: {e}")
    if not settings.fmp_api_key:
        ok = False
        print("FMP key         MISSING")
    else:
        fmp = FMPClient(settings.fmp_api_key, settings.fmp_base_url)
        try:
            hours = await fmp.market_hours("NYSE")
            quotes = await fmp.batch_quotes(["SPY", "NVDA"])
            cal = await MarketCalendar.load(fmp)
            now = datetime.now(UTC)
            print(
                f"FMP             ok · NYSE {'open' if hours.is_open else 'closed'} · SPY {quotes['SPY'].price} · "
                f"{len(cal.holidays)} holidays loaded · next open <t:{int(cal.next_open(now).timestamp())}:f>"
            )
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"FMP             FAILED: {e}")
        finally:
            await fmp.aclose()
    print("\nAll good." if ok else "\nFix the items marked MISSING/FAILED.")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="versus")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "doctor"])
    args = parser.parse_args()
    if args.command == "doctor":
        sys.exit(asyncio.run(doctor()))
    from versus.bot.main import main as run

    run()


if __name__ == "__main__":
    main()
