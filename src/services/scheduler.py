import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from config import ScheduleConfig
from sources.base import Source


logger = logging.getLogger(__name__)


async def run_source(
    source: Source,
    schedule: ScheduleConfig,
) -> None:
    if schedule.type == "interval":
        if schedule.interval is None:
            raise ValueError(
                f"Source '{source.name}' uses interval schedule "
                "but interval is not configured"
            )

        await run_interval(
            source=source,
            interval=schedule.interval,
        )
        return

    if schedule.type == "cron":
        if schedule.cron is None:
            raise ValueError(
                f"Source '{source.name}' uses cron schedule "
                "but cron expression is not configured"
            )

        await run_cron(
            source=source,
            cron=schedule.cron,
            timezone=schedule.timezone,
        )
        return

    raise ValueError(
        f"Unknown schedule type '{schedule.type}' "
        f"for source '{source.name}'. "
        "Expected 'interval' or 'cron'."
    )


async def run_interval(
    source: Source,
    interval: int,
) -> None:
    logger.info(
        "Scheduler started for '%s' "
        "(interval=%ss)",
        source.name,
        interval,
    )

    while True:
        try:
            await source.run()

        except Exception:
            logger.exception(
                "Error while running '%s'",
                source.name,
            )

        await asyncio.sleep(interval)


async def run_cron(
    source: Source,
    cron: str,
    timezone: str,
) -> None:
    tz = ZoneInfo(timezone)

    logger.info(
        "Scheduler started for '%s' "
        "(cron='%s', timezone='%s')",
        source.name,
        cron,
        timezone,
    )

    while True:
        now = datetime.now(tz)

        iterator = croniter(
            cron,
            now,
        )

        next_run = iterator.get_next(datetime)

        delay = (
            next_run - now
        ).total_seconds()

        logger.info(
            "Next run for '%s': %s",
            source.name,
            next_run.isoformat(),
        )

        await asyncio.sleep(delay)

        try:
            await source.run()

        except Exception:
            logger.exception(
                "Error while running '%s'",
                source.name,
            )
