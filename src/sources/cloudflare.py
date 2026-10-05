import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx

from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class CloudflareSource(Source):
    URL = "https://www.cloudflarestatus.com/api/v2/incidents.json"

    def __init__(
        self,
        client: httpx.AsyncClient,
        notifier: Notifier,
    ):
        self.client = client
        self.notifier = notifier
        self.last_updated_at: datetime | None = None

    @property
    def name(self) -> str:
        return "cloudflare"

    async def run(self) -> None:
        logger.info("Checking Cloudflare...")

        incidents = await self.get_incidents()

        if not incidents:
            logger.info("Cloudflare: no incidents")
            return

        latest_updated_at = self.parse_date(incidents[0]["updated_at"])

        if self.last_updated_at is None:
            self.last_updated_at = latest_updated_at

            logger.info(
                "Cloudflare: initialized last_updated_at=%s",
                latest_updated_at.isoformat(),
            )

            return

        new_incidents = []

        for incident in incidents:
            updated_at_string = incident.get("updated_at")
            

            if not updated_at_string:
                continue

            updated_at = self.parse_date(updated_at_string)

            if updated_at <= self.last_updated_at:
                break

            status = incident.get("status")

            created_at_string = incident.get("created_at")

            if not created_at_string:
                continue

            created_at = self.parse_date(created_at_string)

            if created_at <= self.last_updated_at and status != 'resolved': 
                continue

            new_incidents.append(incident)

        if not new_incidents:
            logger.info(
                "Cloudflare: no new updates"
            )
            return

        logger.info(
            "Cloudflare: found %d new update(s)",
            len(new_incidents),
        )

        for incident in reversed(new_incidents):
            name = incident.get(
                "name",
                "Unknown incident",
            )

            status = incident.get(
                "status",
                "unknown",
            ).capitalize()

            created_at = incident.get(
                "created_at",
                "unknown",
            )

            impact = incident.get(
                "impact",
                "unknown",
            ).capitalize()

            shortlink = incident.get(
                "shortlink",
                "unknown",
            )

            formatted_created_at = self.format_date(
                created_at
            )

            message = (
                f"- <b>Message:</b> {name}\n"
                f"- <b>Status:</b> {status}\n"
                f"- <b>Incident start:</b> "
                f"{formatted_created_at}\n"
                f"- <b>Impact:</b> {impact}\n"
                f"- <b>Shortlink:</b> {shortlink}\n"
            )

            if status.lower() == "resolved":
                message = (
                    "✅ <b>Cloudflare Incident:</b>\n\n"
                    + message
                )
            else:
                message = (
                    "🚨 <b>Cloudflare Incident:</b>\n\n"
                    + message
                )

            await self.notifier.send(message)

            logger.info(
                "Cloudflare alert sent: %s (%s)",
                name,
                status,
            )

        self.last_updated_at = latest_updated_at

        logger.info(
            "Cloudflare alerts sent"
        )

    async def get_incidents(self) -> list[dict]:
        response = await self.client.get(
            self.URL
        )

        response.raise_for_status()

        return response.json().get(
            "incidents",
            [],
        )

    @staticmethod
    def parse_date(
        date_string: str,
    ) -> datetime:
        return datetime.fromisoformat(
            date_string.replace(
                "Z",
                "+00:00",
            )
        )

    @staticmethod
    def format_date(
        date_string: str,
    ) -> str:
        date = CloudflareSource.parse_date(
            date_string
        )

        date = date.astimezone(
            ZoneInfo("Europe/Warsaw")
        )

        return date.strftime(
            "%a, %d %b %Y, %H:%M:%S %Z"
        )
