import logging
from datetime import datetime, timedelta, timezone

import httpx

from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class ArtifactHubSource(Source):
    def __init__(
        self,
        client: httpx.AsyncClient,
        notifier: Notifier,
        packages: list[str],
        lookback_hours: int,
    ):
        self.client = client
        self.notifier = notifier
        self.packages = packages
        self.lookback = timedelta(
            hours=lookback_hours
        )

    @property
    def name(self) -> str:
        return "artifacthub"

    async def run(self) -> None:
        cutoff = (
            datetime.now(timezone.utc)
            - self.lookback
        )

        logger.info(
            "ArtifactHub: checking packages "
            "published since %s",
            cutoff.isoformat(),
        )

        updates = []

        for package_name in self.packages:
            package = await self.get_package(
                package_name,
                cutoff,
            )

            if package is None:
                continue

            (
                version,
                app_version,
                has_new_app_version,
            ) = package

            chart_name = package_name.split("/", 1)[1]

            if has_new_app_version:
                updates.append(
                    f"- <b>{chart_name}</b> "
                    f"<code>{version}</code> → "
                    f"app <code>{app_version}</code>"
                )
            else:
                updates.append(
                    f"- <b>{chart_name}</b> "
                    f"<code>{version}</code>"
                )

        if not updates:
            logger.info(
                "ArtifactHub: no updates found"
            )
            return

        message = "📢 <b>Helm chart updates:</b>\n\n"
        message += "\n".join(updates)

        await self.notifier.send(message)

        logger.info(
            "ArtifactHub alert sent to Telegram"
        )

    async def get_package(
        self,
        package_name: str,
        cutoff: datetime,
    ):
        url = (
            "https://artifacthub.io/api/v1/packages/helm/"
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

        older_version = next(
            (
                v
                for v in versions[1:]
                if datetime.fromtimestamp(
                    v["ts"],
                    tz=timezone.utc,
                ) <= cutoff
            ),
            None,
        )

        older_app_version = (
            older_version.get("app_version")
            if older_version is not None
            else None
        )

        has_new_app_version = (
            app_version is not None
            and older_app_version is not None
            and app_version != older_app_version
        )

        return (
            version,
            app_version,
            has_new_app_version,
        )
