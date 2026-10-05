import logging
import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import httpx

from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class ScalewaySource(Source):
    URL = "https://status.scaleway.com/history.atom"

    NS = {
        "atom": "http://www.w3.org/2005/Atom",
    }

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
        return "scaleway"

    async def run(self) -> None:
        logger.info("Checking Scaleway...")

        incidents = await self.get_incidents()

        if not incidents:
            logger.info("Scaleway: no incidents")
            return

        latest_updated_at = self.parse_date(
            incidents[0].findtext(
                "atom:updated",
                namespaces=self.NS,
            )
        )

        if self.last_updated_at is None:
            self.last_updated_at = latest_updated_at

            logger.info(
                "Scaleway: initialized last_updated_at=%s",
                self.last_updated_at.isoformat(),
            )

            return

        new_incidents = []

        for incident in incidents:
            updated_at_string = incident.findtext(
                "atom:updated",
                namespaces=self.NS,
            )

            if not updated_at_string:
                continue

            updated_at = self.parse_date(
                updated_at_string
            )

            if updated_at <= self.last_updated_at:
                break

            new_incidents.append(incident)

        if not new_incidents:
            logger.info(
                "Scaleway: no new updates"
            )
            return

        logger.info(
            "Scaleway: found %d new update(s)",
            len(new_incidents),
        )

        for incident in reversed(new_incidents):
            title = incident.findtext(
                "atom:title",
                "Unknown incident",
                namespaces=self.NS,
            )

            title = escape(title)

            updated_at_string = incident.findtext(
                "atom:updated",
                "unknown",
                namespaces=self.NS,
            )

            link_element = incident.find(
                "atom:link[@rel='alternate']",
                self.NS,
            )

            link = (
                link_element.get("href")
                if link_element is not None
                else "unknown"
            )

            link = escape(link)

            content = incident.findtext(
                "atom:content",
                "",
                namespaces=self.NS,
            )

            statuses = self.parse_latest_update(content)

            if not statuses:
                logger.info(
                    "Scaleway: no status found for incident: %s",
                    title,
                )
                continue

            last_status = statuses[0]

            if (
                len(statuses) > 1
                and last_status.lower()
                not in (
                    "resolved",
                    "completed",
                    "in progress",
                )
            ):
                logger.info(
                    "Scaleway: skipping incident '%s', "
                    "last status is '%s'",
                    title,
                    last_status,
                )
                continue

            status = escape(last_status)

            formatted_updated_at = self.format_date(
                updated_at_string
            )

            message = (
                f"- <b>Message:</b> {title}\n"
                f"- <b>Status:</b> {status}\n"
                f"- <b>Incident update:</b> "
                f"{formatted_updated_at}\n"
            )

            message += (
                f"- <b>Link:</b> {link}\n"
            )

            if status.lower() in (
                "resolved",
                "completed",
            ):
                message = (
                    "✅ <b>Scaleway Incident:</b>\n\n"
                    + message
                )
            elif status.lower() in (
                "scheduled",
            ):
                message = (
                    "📅 <b>Scaleway Scheduled Maintenance:</b>\n\n"
                    + message
                )
            else:
                message = (
                    "🚨 <b>Scaleway Incident:</b>\n\n"
                    + message
                )

            await self.notifier.send(message)

            logger.info(
                "Scaleway alert sent: %s (%s)",
                title,
                status,
            )

        self.last_updated_at = latest_updated_at

        logger.info(
            "Scaleway alerts sent"
        )

    async def get_incidents(
        self,
    ) -> list[ET.Element]:
        response = await self.client.get(
            self.URL
        )

        response.raise_for_status()

        root = ET.fromstring(
            response.content
        )

        incidents = root.findall(
            "atom:entry",
            self.NS,
        )

        incidents.sort(
            key=lambda incident: self.parse_date(
                incident.findtext(
                    "atom:updated",
                    namespaces=self.NS,
                )
            ),
            reverse=True,
        )

        return incidents

    @staticmethod
    def parse_date(
        date_string: str,
    ) -> datetime:
        return datetime.fromisoformat(
            date_string
        )

    @staticmethod
    def parse_latest_update(content: str) -> list[str]:
        statuses = re.findall(r"<strong\b[^>]*>\s*(.*?)\s*</strong>", content, re.DOTALL | re.IGNORECASE)

        return [re.sub(r"\s+", " ", status).strip() for status in statuses]


    @staticmethod
    def format_date(
        date_string: str,
    ) -> str:
        date = ScalewaySource.parse_date(
            date_string
        )

        date = date.astimezone(
            ZoneInfo("Europe/Warsaw")
        )

        return date.strftime(
            "%a, %d %b %Y, %H:%M:%S %Z"
        )
