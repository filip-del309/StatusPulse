from .models import (
    CloudflareFilterConfig,
    CloudflareMessageConfig,
    ScalewayFilterConfig,
    ScalewayMessageConfig,
    ScheduleConfig,
    TailscaleFilterConfig,
    TailscaleMessageConfig,
)

from croniter import croniter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def load_schedule(data: dict) -> ScheduleConfig:
    return ScheduleConfig(
        type=data["type"],
        interval=data.get("interval"),
        cron=data.get("cron"),
        timezone=data.get("timezone", "UTC"),
    )


def load_tailscale_filters(data: dict) -> TailscaleFilterConfig:
    components_data = data.get("components", {})

    return TailscaleFilterConfig(
        components_enabled=components_data.get("enabled", False),
        components=components_data.get("values", [])
    )


def load_tailscale_message(data: dict) -> TailscaleMessageConfig:
    return TailscaleMessageConfig(
        title=data.get("title", True),
        status=data.get("status", True),
        components=data.get("components", True),
        updated_at=data.get("updated_at", True),
        link=data.get("link", True)
    )


def load_cloudflare_filters(data: dict) -> CloudflareFilterConfig:
    severity_data = data.get("severity", {})
    components_data = data.get("components", {})
    required_matches = data.get("required_matches", 1)

    return CloudflareFilterConfig(
        required_matches=required_matches,
        severity_enabled=severity_data.get("enabled", False),
        severities=severity_data.get("values", []),
        components_enabled=components_data.get("enabled", False),
        components=components_data.get("values", []),
    )


def load_cloudflare_message(data: dict) -> CloudflareMessageConfig:
    return CloudflareMessageConfig(
        title=data.get("title", True),
        severity=data.get("severity", True),
        status=data.get("status", True),
        components=data.get("components", True),
        updated_at=data.get("updated_at",True),
        link=data.get("link", True),
    )


def load_scaleway_filters(data: dict) -> ScalewayFilterConfig:
    severity_data = data.get("severity", {})
    components_data = data.get("components", {})
    required_matches = data.get("required_matches", 1)

    return ScalewayFilterConfig(
        required_matches=required_matches,
        severity_enabled=severity_data.get("enabled", False),
        severities=severity_data.get("values", []),
        components_enabled=components_data.get("enabled", False),
        components=components_data.get("values", []),
    )


def load_scaleway_message(data: dict) -> ScalewayMessageConfig:
    return ScalewayMessageConfig(
        title=data.get("title", True),
        severity=data.get("severity", True),
        status=data.get("status", True),
        components=data.get("components", True),
        updated_at=data.get("updated_at", True),
        link=data.get("link", True),
    )


def load_schedule(data: dict) -> ScheduleConfig:
    schedule_type = data["type"]
    interval = data.get("interval")
    cron = data.get("cron")
    timezone = data.get("timezone", "UTC")

    if schedule_type == "interval":
        if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
            raise ValueError(
                "Interval schedule requires a positive integer 'interval'."
            )

    elif schedule_type == "cron":
        if not isinstance(cron, str) or not cron.strip():
            raise ValueError(
                "Cron schedule requires a non-empty 'cron' expression."
            )

        try:
            croniter(cron)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid cron expression: {cron!r}") from exc

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"Invalid schedule timezone: {timezone!r}") from exc

    else:
        raise ValueError(
            f"Unknown schedule type {schedule_type!r}. "
            "Expected 'interval' or 'cron'."
        )

    return ScheduleConfig(
        type=schedule_type,
        interval=interval,
        cron=cron,
        timezone=timezone,
    )
