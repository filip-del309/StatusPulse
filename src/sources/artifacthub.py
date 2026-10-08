import logging
from datetime import datetime, timedelta, timezone
import asyncio
from pathlib import Path

import httpx
import yaml

from notifications.base import Notifier
from sources.base import Source
from sources.helm import get_values_diff
from services.git import GitRepository

logger = logging.getLogger(__name__)


class ArtifactHubSource(Source):
    def __init__(
        self,
        client: httpx.AsyncClient,
        notifier: Notifier,
        packages,
        lookback_hours: int,
        statuspulse_url: str,
        repository: GitRepository,
    ):
        self.client = client
        self.notifier = notifier
        self.packages = packages
        self.lookback = timedelta(hours=lookback_hours)
        self.statuspulse_url = statuspulse_url.rstrip("/")
        self.repository = repository
        self.packages_data = {}

    @property
    def name(self) -> str:
        return "artifacthub"

    async def run(self) -> None:
        await asyncio.to_thread(
            self.repository.pull,
        )

        cutoff = (datetime.now(timezone.utc) - self.lookback)

        logger.info(
            "ArtifactHub: checking packages "
            "published since %s",
            cutoff.isoformat(),
        )

        repository_data = await asyncio.to_thread(self.scan_repository)

        updates = []

        for package in self.packages:
            package_data = repository_data.get(package.name,{})

            result = await self.get_package(
                package,
                package_data,
                cutoff,
            )

            if result is None:
                continue

            updates.extend(result)

        if not updates:
            logger.info("ArtifactHub: no updates found")
            return

        statuspulse_url = (
            f"{self.statuspulse_url}/status/artifacthub"
        )

        message = (
            f"📢 <b>Helm chart updates:</b>\n\n"
            f"{statuspulse_url}\n\n"
        )

        message += "\n".join(updates)

        await self.notifier.send(message)

        logger.info(
            "ArtifactHub alert sent to Telegram"
        )

    def scan_repository(self) -> dict:
        repository_path = Path(
            self.repository.path
        )

        if not repository_path.exists():
            logger.warning(
                "ArtifactHub: repository directory "
                "does not exist: %s",
                repository_path,
            )

            return {}

        applications = []

        for file_path in repository_path.rglob("*"):
            if not file_path.is_file():
                continue

            if file_path.suffix not in (
                ".yaml",
                ".yml",
            ):
                continue

            try:
                with file_path.open(
                    "r",
                    encoding="utf-8",
                ) as file:
                    documents = yaml.safe_load_all(file)

                    for document in documents:
                        if not document:
                            continue

                        if document.get("kind") != "Application":
                            continue

                        source = (
                            document
                            .get("spec", {})
                            .get("source", {})
                        )

                        path = source.get("path")

                        if not path:
                            continue

                        path = path.removeprefix("./")

                        path_parts = Path(path).parts

                        if not path_parts:
                            continue

                        if path_parts[0] != "helm-versions":
                            continue

                        applications.append({
                            "path": path,
                            "file": str(file_path),
                        })

            except yaml.YAMLError:
                logger.warning(
                    "ArtifactHub: could not parse YAML file %s",
                    file_path,
                )

        return self.find_repository_versions(
            applications,
        )
    
    def find_repository_versions(
        self,
        applications: list[dict],
    ) -> dict:
        repository_data = {}

        folders = sorted(
            {
                folder
                for package in self.packages
                for folder in package.folders
            },
            key=len,
            reverse=True,
        )

        for package in self.packages:
            package_data = {}

            for folder in package.folders:
                prefix = f"helm-versions/{folder}-"

                versions = []

                for application in applications:
                    path = application["path"]

                    if not path.startswith(prefix):
                        continue

                    matched_folder = next(
                        (
                            configured_folder
                            for configured_folder in folders
                            if path.startswith(
                                f"helm-versions/{configured_folder}-"
                            )
                        ),
                        None,
                    )

                    if matched_folder != folder:
                        continue

                    version = path[len(prefix):]

                    if not version:
                        continue

                    versions.append({
                        "version": version,
                        "path": path,
                        "file": application["file"],
                    })

                if not versions:
                    logger.warning(
                        "ArtifactHub: no version found "
                        "for package '%s', folder '%s'",
                        package.name,
                        folder,
                    )

                    continue

                versions.sort(
                    key=lambda item: self.version_key(
                        item["version"]
                    )
                )

                oldest = versions[0]

                package_data[folder] = {
                    "version": oldest["version"],
                    "path": oldest["path"],
                    "file": oldest["file"],
                }

                logger.info(
                    "ArtifactHub: package '%s', folder '%s': "
                    "oldest repository version=%s",
                    package.name,
                    folder,
                    oldest["version"],
                )

            repository_data[package.name] = package_data

        return repository_data

    async def get_package(self, package, repository_data: dict, cutoff: datetime):
        package_name = package.name

        url = (
            f"https://artifacthub.io/api/v1/packages/helm/"
            f"{package_name}"
        )

        response = await self.client.get(url)
        response.raise_for_status()

        versions = response.json().get(
            "available_versions",
            [],
        )

        if not versions:
            logger.info(
                "ArtifactHub package '%s' has no versions",
                package_name,
            )

            return None

        last_version = versions[0]

        version = last_version["version"]
        app_version = last_version.get("app_version")

        version_date = datetime.fromtimestamp(
            last_version["ts"],
            tz=timezone.utc,
        )

        is_new = version_date > cutoff

        if not repository_data:
            logger.info(
                "ArtifactHub package '%s': "
                "no matching repository folder found",
                package_name,
            )

            if not is_new:
                return None

            return [
                f"- <b>{package_name.split('/', 1)[1]}</b> "
                f"<code>{version}</code> "
                f"(new release)"
            ]

        repository_versions = {}
        values_diff = {}

        for folder, data in repository_data.items():
            used_version = data["version"]

            repository_versions[folder] = used_version

            if used_version == version:
                values_diff[folder] = []

                continue

            logger.info(
                "ArtifactHub values diff: package=%s, "
                "folder=%s, used_version=%s, latest_version=%s",
                package_name,
                folder,
                used_version,
                version,
            )

            values_diff[folder] = await get_values_diff(
                self.client,
                package_name,
                used_version,
                version,
            )

        self.packages_data[package_name] = {
            "package": package_name,
            "chart": package_name.split("/", 1)[1],
            "version": version,
            "app_version": app_version,
            "published": version_date,
            "is_new": is_new,
            "repository_versions": repository_versions,
            "values_diff": values_diff,
        }

        logger.info(
            "ArtifactHub package '%s': "
            "version=%s, published=%s, cutoff=%s, new=%s",
            package_name,
            version,
            version_date.isoformat(),
            cutoff.isoformat(),
            is_new,
        )

        if not is_new:
            return None

        updates = []

        for folder, used_version in repository_versions.items():
            if used_version == version:
                continue

            updates.append(
                f"- <b>{package_name.split('/', 1)[1]}</b> "
                f"<code>{used_version}</code> → "
                f"<code>{version}</code>"
            )

        if not updates:
            return None

        return updates

    @staticmethod
    def version_key(version: str):
        parts = version.lstrip("v").split(".")

        result = []

        for part in parts:
            number = ""

            for character in part:
                if character.isdigit():
                    number += character
                else:
                    break

            if number:
                result.append(int(number))
            else:
                result.append(0)

        return tuple(result)
