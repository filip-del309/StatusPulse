import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from config.models import UpgradeAIConfig

import httpx
import yaml

from notifications.base import Notifier
from sources.base import Source
from services.git import GitRepository
from services.storage import Storage
from services.releases import get_release_notes_between
from services.upgrade_advisor import UpgradeAdvisor


logger = logging.getLogger(__name__)

API_URL = "https://artifacthub.io/api/v1"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HTTP_MAX_ATTEMPTS = 5
HTTP_MAX_DELAY = 120


class ArtifactHubSource(Source):
    def __init__(self, client: httpx.AsyncClient, notifier: Notifier, packages, lookback_hours: int, statuspulse_url: str, repository: GitRepository, storage: Storage, upgrade_ai_config: UpgradeAIConfig,):
        self.client = client
        self.notifier = notifier
        self.packages = packages
        self.lookback = timedelta(hours=lookback_hours)
        self.statuspulse_url = statuspulse_url.rstrip("/")
        self.repository = repository
        self.storage = storage
        self.upgrade_advisor = UpgradeAdvisor(upgrade_ai_config)
        self.packages_data = {item["package"]: item for item in self.storage.get_artifacthub_packages()}
        self.releases_data = {item["package"]: item for item in self.storage.get_release_notes()}
        self._ai_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "artifacthub"

    async def close(self) -> None:
        await self.upgrade_advisor.close()

    async def run(self) -> None:
        await asyncio.to_thread(self.repository.pull)
        cutoff = datetime.now(timezone.utc) - self.lookback
        logger.info("ArtifactHub: checking packages published since %s", cutoff.isoformat())
        repository_data = await asyncio.to_thread(self.scan_repository)
        updates = []

        for index, package in enumerate(self.packages):
            result = await self.get_package(package, repository_data, cutoff)
            if result:
                updates.extend(result)
            if index < len(self.packages) - 1:
                await asyncio.sleep(1)

        if not updates:
            logger.info("ArtifactHub: no updates found")
            return

        message = "📢 <b>Helm chart updates:</b>\n\n" + "\n".join(updates)
        await self.notifier.send(message)

    def scan_repository(self) -> dict:
        repository_path = Path(self.repository.path)
        if not repository_path.exists():
            logger.warning("ArtifactHub: repository directory does not exist: %s", repository_path)
            return {}

        applications = []
        applicationset_root = repository_path / "app-sets-infra" / "root"

        for file_path in repository_path.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in {".yaml", ".yml"}:
                continue

            is_applicationset_file = applicationset_root in file_path.parents

            try:
                with file_path.open("r", encoding="utf-8") as file:
                    for document in yaml.safe_load_all(file):
                        if not isinstance(document, dict):
                            continue

                        kind = document.get("kind")
                        path = None

                        if kind == "Application":
                            source = document.get("spec", {}).get("source", {})
                            path = source.get("path")
                        elif kind == "ApplicationSet" and is_applicationset_file:
                            template_spec = document.get("spec", {}).get("template", {}).get("spec", {})
                            path = template_spec.get("source", {}).get("path")

                        if not isinstance(path, str) or not path:
                            continue

                        path = path.removeprefix("./")
                        path_parts = Path(path).parts
                        if path_parts and path_parts[0] == "helm-versions":
                            applications.append({"path": path, "file": str(file_path)})

            except (yaml.YAMLError, OSError):
                logger.warning("ArtifactHub: could not parse repository file %s", file_path, exc_info=True)

        logger.info("ArtifactHub: found %d application paths in repository", len(applications))

        for application in applications:
            logger.debug("ArtifactHub: discovered path=%s, file=%s", application["path"], application["file"])

        return self.find_repository_versions(applications)

    def find_repository_versions(self, applications: list[dict]) -> dict:
        repository_data = {}
        folders = sorted({folder for package in self.packages for folder in package.folders}, key=len, reverse=True)

        for package in self.packages:
            package_versions = []

            for application in applications:
                path = application["path"]
                matched_folder = next((folder for folder in folders if path.startswith(f"helm-versions/{folder}-")), None)

                if matched_folder is None or matched_folder not in package.folders:
                    continue

                prefix = f"helm-versions/{matched_folder}-"
                version = path[len(prefix):].strip("/")

                if not version:
                    continue

                package_versions.append({"version": version, "path": path, "file": application["file"], "folder": matched_folder})

            if not package_versions:
                logger.warning("ArtifactHub: no version found for package '%s', folders=%s", package.name, package.folders)
                repository_data[package.name] = {}
                continue

            package_versions.sort(key=lambda item: self.version_key(item["version"]))
            oldest = package_versions[0]

            repository_data[package.name] = {
                "version": oldest["version"],
                "path": oldest["path"],
                "file": oldest["file"],
                "folder": oldest["folder"],
                "folders": {item["folder"]: item["version"] for item in package_versions},
            }

            logger.info(
                "ArtifactHub: package '%s': oldest version=%s, folder=%s, all folder versions=%s",
                package.name,
                oldest["version"],
                oldest["folder"],
                repository_data[package.name]["folders"],
            )

        return repository_data

    async def get_package_response(self, url: str) -> httpx.Response | None:
        try:
            return await request_with_retry(self.client, url)
        except (httpx.HTTPStatusError, httpx.RequestError, RuntimeError) as exc:
            logger.error("ArtifactHub: could not fetch package data from %s: %s", url, exc)
            return None

    async def get_package(self, package, repository_data: dict, cutoff: datetime):
        package_name = package.name
        url = f"{API_URL}/packages/helm/{package_name}"
        response = await self.get_package_response(url)

        if response is None:
            logger.warning("ArtifactHub: skipping package '%s' because its data could not be fetched", package_name)
            return None

        try:
            package_response = response.json()
        except ValueError:
            logger.error("ArtifactHub returned invalid JSON for %s", package_name)
            return None

        versions = package_response.get("available_versions", [])
        if not isinstance(versions, list) or not versions:
            logger.info("ArtifactHub package '%s' has no versions", package_name)
            return None

        latest = versions[0]
        if not isinstance(latest, dict) or not latest.get("version"):
            logger.warning("ArtifactHub returned invalid latest-version data for %s", package_name)
            return None

        version = str(latest["version"])
        app_version = latest.get("app_version")
        timestamp = latest.get("ts")

        if not isinstance(timestamp, (int, float)):
            logger.warning("ArtifactHub latest version has no valid timestamp: %s@%s", package_name, version)
            version_date = datetime.min.replace(tzinfo=timezone.utc)
        else:
            version_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        is_new = version_date > cutoff
        package_repository_data = repository_data.get(package_name, {})

        if not package_repository_data:
            logger.info("ArtifactHub package '%s': no matching repository folder found", package_name)
            if not is_new:
                return None
            chart_name = package_name.split("/", 1)[-1]
            return [f"- <b>{chart_name}</b> <code>{version}</code> (new release)"]

        repository_versions = package_repository_data.get("folders", {})
        if not repository_versions:
            repository_versions = {package_repository_data["folder"]: package_repository_data["version"]}

        chart_name = package_name.split("/", 1)[1]
        oldest_folder, oldest_version = min(repository_versions.items(), key=lambda item: self.version_key(item[1]))
        release_folder = sorted(package.folders)[0] if package.folders else oldest_folder

        release_result = await self.get_package_release_notes(
            package=package,
            folder=release_folder,
            previous_chart_version=oldest_version,
            latest_chart_version=version,
        )

        latest_chart_data = {}
        try:
            latest_chart_data = await get_version(self.client, package_name, version)
        except (httpx.HTTPError, ValueError, KeyError):
            logger.exception("Could not retrieve latest chart metadata: package=%s, version=%s", package_name, version)

        latest_app_version = latest_chart_data.get("app_version") or app_version
        package_values_diff = []
        package_values_diff_available = False

        if oldest_version != version:
            try:
                package_values_diff = await get_values_diff(self.client, package_name, oldest_version, version)
                package_values_diff_available = True
            except (httpx.HTTPError, ValueError, KeyError, RuntimeError):
                logger.exception("Could not calculate values diff for package=%s", package_name)

        upgrade_guide = None

        if oldest_version != version:
            try:
                cached_guide = await asyncio.to_thread(self.storage.get_upgrade_guide, package_name, oldest_version, version)

                if cached_guide is not None:
                    upgrade_guide = cached_guide["guide"]
                    logger.info("Upgrade guide cache hit: package=%s, range=%s -> %s", package_name, oldest_version, version)
                else:
                    previous_chart_data = await get_version(self.client, package_name, oldest_version)
                    previous_app_version = previous_chart_data.get("app_version")
                    readme_value = latest_chart_data.get("readme")
                    readme = readme_value.strip() if isinstance(readme_value, str) else ""

                    upgrade_guide = await self.upgrade_advisor.generate(
                        chart=chart_name,
                        previous_chart_version=oldest_version,
                        latest_chart_version=version,
                        previous_app_version=previous_app_version,
                        latest_app_version=latest_app_version,
                        readme=readme,
                        values_diff=package_values_diff if package_values_diff_available else None,
                    )

                    if upgrade_guide:
                        await asyncio.to_thread(self.storage.save_upgrade_guide, package_name, oldest_version, version, upgrade_guide)
                        logger.info("Package upgrade guide generated: package=%s, range=%s -> %s", package_name, oldest_version, version)

            except Exception:
                logger.exception("Could not generate package upgrade guide: package=%s, range=%s -> %s", package_name, oldest_version, version)

        package_data = {
            "package": package_name,
            "chart": chart_name,
            "version": version,
            "app_version": app_version,
            "published": version_date,
            "is_new": is_new,
            "repository_versions": repository_versions,
            "values_diff": {"package": package_values_diff},
            "upgrade_guide_previous_version": oldest_version,
            "upgrade_guide_latest_version": version,
        }

        self.packages_data[package_name] = package_data
        await asyncio.to_thread(self.storage.save_artifacthub_package, package_name, package_data)

        logger.info(
            "ArtifactHub package '%s': version=%s, published=%s, cutoff=%s, new=%s",
            package_name,
            version,
            version_date.isoformat(),
            cutoff.isoformat(),
            is_new,
        )

        if not is_new:
            return None

        outdated_versions = sorted({used_version for used_version in repository_versions.values() if used_version != version}, key=self.version_key)

        return [
            f"- <b>{chart_name}</b> <code>{used_version}</code> → <code>{version}</code>"
            for used_version in outdated_versions
        ] or None

    @staticmethod
    def version_key(version: str):
        parts = version.lstrip("vV").split(".")
        result = []

        for part in parts:
            match = ""
            for character in part:
                if character.isdigit():
                    match += character
                else:
                    break
            result.append(int(match) if match else 0)

        return tuple(result)

    async def get_package_release_notes(self, package, folder: str, previous_chart_version: str, latest_chart_version: str) -> dict:
        chart_name = package.name.split("/", 1)[1]
        source = package.releases

        async def persist_result(result: dict) -> dict:
            await asyncio.to_thread(self.storage.save_release_notes, package.name, folder, result)
            self.releases_data[package.name] = {"package": package.name, "folder": folder, **result}
            return result

        if source is None:
            return await persist_result({
                "chart": chart_name,
                "previous_chart_version": previous_chart_version,
                "latest_chart_version": latest_chart_version,
                "status": "not_configured",
                "releases": [],
            })

        cached = self.releases_data.get(package.name)
        if cached and cached.get("previous_chart_version") == previous_chart_version and cached.get("latest_chart_version") == latest_chart_version and cached.get("status") in {"ok", "unchanged"}:
            logger.debug("Release Notes cache hit: %s/%s (%s -> %s)", package.name, folder, previous_chart_version, latest_chart_version)
            return cached

        try:
            previous_chart, latest_chart = await asyncio.gather(
                get_version(self.client, package.name, previous_chart_version),
                get_version(self.client, package.name, latest_chart_version),
            )

            previous_app_version = previous_chart.get("app_version")
            latest_app_version = latest_chart.get("app_version")

            if not previous_app_version or not latest_app_version:
                raise ValueError(f"Missing app_version for {package.name}: {previous_chart_version} -> {latest_chart_version}")

            result = {
                "chart": chart_name,
                "previous_chart_version": previous_chart_version,
                "latest_chart_version": latest_chart_version,
                "previous_version": previous_app_version,
                "latest_version": latest_app_version,
                "provider": source.provider,
                "project": source.project,
            }

            if previous_app_version == latest_app_version:
                result.update({"status": "unchanged", "releases": []})
            else:
                releases = await get_release_notes_between(
                    client=self.client,
                    source=source,
                    previous_version=previous_app_version,
                    latest_version=latest_app_version,
                )
                result.update({"status": "ok", "releases": releases})

            await persist_result(result)
            logger.info("Release Notes updated: %s/%s, app version %s -> %s", package.name, folder, previous_app_version, latest_app_version)
            return result

        except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as exc:
            logger.warning("Could not fetch Release Notes for %s (%s): %s", package.name, folder, exc)
            result = {
                "chart": chart_name,
                "previous_chart_version": previous_chart_version,
                "latest_chart_version": latest_chart_version,
                "provider": source.provider,
                "project": source.project,
                "status": "error",
                "error": str(exc),
                "releases": [],
            }
            try:
                await persist_result(result)
            except Exception:
                logger.exception("Could not save Release Notes status for %s/%s", package.name, folder)
            return result


async def request_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, timeout=HTTP_TIMEOUT)

            if response.status_code == 429:
                if attempt == HTTP_MAX_ATTEMPTS:
                    response.raise_for_status()

                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, HTTP_MAX_DELAY)
                except ValueError:
                    delay = min(2 ** attempt, HTTP_MAX_DELAY)

                delay = min(max(delay, 1), HTTP_MAX_DELAY)
                logger.warning("ArtifactHub rate limit (429), attempt %d/%d; retrying in %.1f seconds: %s", attempt, HTTP_MAX_ATTEMPTS, delay, url)
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code < 500 or attempt == HTTP_MAX_ATTEMPTS:
                raise

            delay = min(2 ** attempt, HTTP_MAX_DELAY)
            logger.warning("ArtifactHub HTTP %s, attempt %d/%d; retrying in %s seconds: %s", status_code, attempt, HTTP_MAX_ATTEMPTS, delay, url)
            await asyncio.sleep(delay)

        except httpx.RequestError:
            if attempt == HTTP_MAX_ATTEMPTS:
                raise

            delay = min(2 ** attempt, HTTP_MAX_DELAY)
            logger.warning("ArtifactHub request failed, attempt %d/%d; retrying in %s seconds: %s", attempt, HTTP_MAX_ATTEMPTS, delay, url, exc_info=True)
            await asyncio.sleep(delay)

    raise RuntimeError(f"ArtifactHub request failed after {HTTP_MAX_ATTEMPTS} attempts: {url}")


async def get_version(client: httpx.AsyncClient, package_name: str, version: str) -> dict:
    parts = package_name.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid ArtifactHub package name: {package_name!r}; expected repository/package")

    repository, chart_name = parts
    url = f"{API_URL}/packages/helm/{quote(repository, safe='')}/{quote(chart_name, safe='')}/{quote(version, safe='')}"
    response = await request_with_retry(client, url)

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(f"ArtifactHub returned invalid JSON: {url}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Unexpected ArtifactHub response for {package_name}:{version}")

    return data


async def download_values(client: httpx.AsyncClient, package_id: str, version: str) -> dict:
    if not package_id:
        raise ValueError("ArtifactHub package_id is missing")

    url = f"{API_URL}/packages/{quote(package_id, safe='')}/{quote(version, safe='')}/values"
    response = await request_with_retry(client, url)

    try:
        values = yaml.safe_load(response.text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse values for package_id={package_id}, version={version}") from exc

    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError(f"Expected a YAML mapping for package_id={package_id}, version={version}")

    return values


def diff_dict(old, new, path="") -> list[dict]:
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    changes = []

    for key in sorted(set(old) | set(new), key=str):
        current_path = f"{path}.{key}" if path else str(key)

        if key not in old:
            changes.append({"type": "added", "path": current_path, "new": new[key]})
        elif key not in new:
            changes.append({"type": "removed", "path": current_path, "old": old[key]})
        elif isinstance(old[key], dict) and isinstance(new[key], dict):
            changes.extend(diff_dict(old[key], new[key], current_path))
        elif old[key] != new[key]:
            changes.append({"type": "changed", "path": current_path, "old": old[key], "new": new[key]})

    return changes


async def get_values_diff(client: httpx.AsyncClient, package_name: str, previous_version: str, latest_version: str) -> list[dict]:
    if previous_version == latest_version:
        return []

    logger.info("ArtifactHub: fetching values diff for %s: %s -> %s", package_name, previous_version, latest_version)

    previous, latest = await asyncio.gather(
        get_version(client, package_name, previous_version),
        get_version(client, package_name, latest_version),
    )

    previous_package_id = previous.get("package_id")
    latest_package_id = latest.get("package_id")

    if not previous_package_id or not latest_package_id:
        raise ValueError(f"ArtifactHub response is missing package_id for {package_name}: {previous_version} -> {latest_version}")

    if previous_package_id != latest_package_id:
        raise ValueError(f"ArtifactHub package ID changed for {package_name}: {previous_package_id} != {latest_package_id}")

    previous_values, latest_values = await asyncio.gather(
        download_values(client, previous_package_id, previous_version),
        download_values(client, latest_package_id, latest_version),
    )

    changes = diff_dict(previous_values, latest_values)
    logger.info("ArtifactHub: found %d default-values change(s) for %s", len(changes), package_name)
    return changes
