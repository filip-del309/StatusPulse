import os
from pathlib import Path


def get_env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.getenv(name, default)

    if value is not None:
        value = value.strip()

    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")

    return value


def get_bool(name: str, default: bool = False) -> bool:
    value = get_env(name)

    if value is None:
        return default

    normalized = value.lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


def get_int(name: str, default: int) -> int:
    value = get_env(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer value for {name}: {value!r}"
        ) from exc


def get_float(name: str, default: float) -> float:
    value = get_env(name)

    if value is None:
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid numeric value for {name}: {value!r}"
        ) from exc


def get_path(name: str, default: str) -> Path:
    value = get_env(name, default)

    if value is None:
        raise ValueError(f"Missing path configuration: {name}")

    return Path(value).expanduser()
