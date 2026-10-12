import asyncio
import logging
from urllib.parse import quote

import httpx
import yaml


logger = logging.getLogger(__name__)

API_URL = "https://artifacthub.io/api/v1"

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

HTTP_MAX_ATTEMPTS = 3


async def request_with_retry(
    client: httpx.AsyncClient,
    url: str,
) -> httpx.Response:
    max_attempts = 5
    max_delay = 120

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(
                url,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code == 429:
                if attempt == max_attempts:
                    response.raise_for_status()

                retry_after = response.headers.get("Retry-After")

                try:
                    delay = (
                        float(retry_after)
                        if retry_after
                        else min(10 * attempt, max_delay)
                    )
                except ValueError:
                    delay = min(10 * attempt, max_delay)

                delay = min(max(delay, 1), max_delay)

                logger.warning(
                    "ArtifactHub rate limit (429), attempt %d/%d. "
                    "Retrying in %.1f seconds: %s",
                    attempt,
                    max_attempts,
                    delay,
                    url,
                )

                await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code

            # Nie ponawiamy błędów klienta, np. 400, 401, 403, 404.
            if status_code < 500:
                raise

            if attempt == max_attempts:
                raise

            delay = min(2 ** attempt, max_delay)

            logger.warning(
                "ArtifactHub returned HTTP %s "
                "(attempt %d/%d). Retrying in %s seconds: %s",
                status_code,
                attempt,
                max_attempts,
                delay,
                url,
            )

            await asyncio.sleep(delay)

        except httpx.RequestError:
            if attempt == max_attempts:
                raise

            delay = min(2 ** attempt, max_delay)

            logger.warning(
                "ArtifactHub request failed "
                "(attempt %d/%d). Retrying in %s seconds: %s",
                attempt,
                max_attempts,
                delay,
                url,
                exc_info=True,
            )

            await asyncio.sleep(delay)

    raise RuntimeError(
        f"ArtifactHub request failed after "
        f"{max_attempts} attempts: {url}"
    )


async def get_version(
    client: httpx.AsyncClient,
    package_name: str,
    version: str,
) -> dict:
    """
    Pobiera szczegóły konkretnej wersji pakietu.

    package_name powinien mieć format:
    repozytorium/nazwa-pakietu
    """

    parts = package_name.split("/", 1)

    if len(parts) != 2 or not all(parts):
        raise ValueError(
            f"Invalid ArtifactHub package name: {package_name!r}. "
            "Expected format: repository/package"
        )

    repository, chart_name = parts

    url = (
        f"{API_URL}/packages/helm/"
        f"{quote(repository, safe='')}/"
        f"{quote(chart_name, safe='')}/"
        f"{quote(version, safe='')}"
    )

    response = await request_with_retry(
        client,
        url,
    )

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError(
            f"Unexpected ArtifactHub response for "
            f"{package_name}:{version}"
        )

    return data


async def download_values(
    client: httpx.AsyncClient,
    package_id: str,
    version: str,
) -> dict:
    """
    Pobiera values.yaml bezpośrednio z ArtifactHub API.

    Nie wymaga lokalnej instalacji Helm ani pobierania archiwum OCI.
    """

    if not package_id:
        raise ValueError(
            "ArtifactHub package_id is missing"
        )

    url = (
        f"{API_URL}/packages/"
        f"{quote(package_id, safe='')}/"
        f"{quote(version, safe='')}/values"
    )

    response = await request_with_retry(
        client,
        url,
    )

    try:
        values = yaml.safe_load(response.text)

    except yaml.YAMLError as exc:
        raise ValueError(
            f"Could not parse values for "
            f"package_id={package_id}, version={version}"
        ) from exc

    if values is None:
        return {}

    if not isinstance(values, dict):
        raise ValueError(
            f"Expected a YAML mapping for "
            f"package_id={package_id}, version={version}"
        )

    return values


def diff_dict(
    old,
    new,
    path="",
) -> list[dict]:
    """Porównuje dwie struktury słowników i zwraca listę zmian."""

    changes = []

    old = old or {}
    new = new or {}

    keys = set(old) | set(new)

    for key in sorted(keys):
        current_path = (
            f"{path}.{key}"
            if path
            else str(key)
        )

        if key not in old:
            changes.append({
                "type": "added",
                "path": current_path,
                "new": new[key],
            })

        elif key not in new:
            changes.append({
                "type": "removed",
                "path": current_path,
                "old": old[key],
            })

        elif (
            isinstance(old[key], dict)
            and isinstance(new[key], dict)
        ):
            changes.extend(
                diff_dict(
                    old[key],
                    new[key],
                    current_path,
                )
            )

        elif old[key] != new[key]:
            changes.append({
                "type": "changed",
                "path": current_path,
                "old": old[key],
                "new": new[key],
            })

    return changes


async def get_values_diff(
    client: httpx.AsyncClient,
    package_name: str,
    previous_version: str,
    latest_version: str,
) -> list[dict]:
    """Porównuje values dwóch wersji pakietu w ArtifactHub."""

    if previous_version == latest_version:
        return []

    logger.info(
        "ArtifactHub: fetching values diff for %s: %s -> %s",
        package_name,
        previous_version,
        latest_version,
    )

    # Pobieramy szczegóły obu wersji.
    latest = await get_version(
        client,
        package_name,
        latest_version,
    )

    previous = await get_version(
        client,
        package_name,
        previous_version,
    )

    # Identyfikator pakietu powinien być dostępny w odpowiedzi API.
    latest_package_id = latest.get("package_id")
    previous_package_id = previous.get("package_id")

    if not latest_package_id or not previous_package_id:
        raise ValueError(
            f"ArtifactHub response does not contain package_id "
            f"for {package_name}: "
            f"{previous_version} -> {latest_version}"
        )

    if latest_package_id != previous_package_id:
        raise ValueError(
            f"ArtifactHub package ID changed for {package_name}: "
            f"{previous_package_id} != {latest_package_id}"
        )

    # Pobieramy wartości bezpośrednio z API.
    latest_values = await download_values(
        client,
        latest_package_id,
        latest_version,
    )

    previous_values = await download_values(
        client,
        previous_package_id,
        previous_version,
    )

    changes = diff_dict(
        previous_values,
        latest_values,
    )

    logger.info(
        "ArtifactHub: found %d values change(s) for %s",
        len(changes),
        package_name,
    )

    return changes
