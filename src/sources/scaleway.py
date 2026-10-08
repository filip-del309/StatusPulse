import logging
import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import httpx

from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class ScalewayIncidentParser(HTMLParser):
    def __init__(self):
        super().__init__()

        self.severity = None
        self.components_text = ""

        self._inside_components = False

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ):
        attributes = dict(attrs)

        classes = attributes.get(
            "class",
            "",
        ).split()

        if (
            tag == "h1"
            and "incident-name" in classes
        ):
            for class_name in classes:
                if class_name.startswith("impact-"):
                    self.severity = (
                        class_name
                        .removeprefix("impact-")
                        .capitalize()
                    )

        if (
            tag == "div"
            and "components-affected" in classes
        ):
            self._inside_components = True

    def handle_data(
        self,
        data: str,
    ):
        if self._inside_components:
            self.components_text += data

    def handle_endtag(
        self,
        tag: str,
    ):
        if (
            tag == "div"
            and self._inside_components
        ):
            self._inside_components = False


class ScalewaySource(Source):
    URL = "https://status.scaleway.com/history.atom"

    NS = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(
        self,
        client: httpx.AsyncClient,
        notifier: Notifier,
        filters,
        message,
    ):
        self.client = client
        self.notifier = notifier
        self.filters = filters
        self.message = message
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

            if link == "unknown":
                logger.info(
                    "Scaleway: no incident link found: %s",
                    title,
                )

                continue

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

            severity, components = (
                await self.get_incident_details(
                    link
                )
            )

            logger.info(
                "Scaleway incident details: "
                "severity=%s, components=%s",
                severity,
                components,
            )

            if not self.matches_filters(
                severity,
                components,
            ):
                logger.info(
                    "Scaleway: skipping incident '%s' "
                    "because it does not match filters",
                    title,
                )
                continue

            status = escape(last_status)

            formatted_updated_at = self.format_date(
                updated_at_string
            )

            message = self.build_message(
                title=title,
                status=status,
                severity=severity,
                components=components,
                updated_at=formatted_updated_at,
                link=link,
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

    async def get_incident_details(
        self,
        link: str,
    ) -> tuple[str | None, list[str]]:
        response = await self.client.get(
            link
        )

        response.raise_for_status()

        parser = ScalewayIncidentParser()

        parser.feed(
            response.text
        )

        components = self.parse_components(
            parser.components_text
        )

        return (
            parser.severity,
            components,
        )

    @staticmethod
    def parse_components(
        components_text: str,
    ) -> list[str]:
        components = []

        matches = re.findall(
            r"([^()]+?)\s*\(([^()]*)\)",
            components_text,
        )

        for category, values in matches:
            category = category.strip()

            prefixes = [
                "This incident affects:",
                "This incident affected:",
                "This scheduled maintenance affected:",
            ]

            for prefix in prefixes:
                if prefix in category:
                    category = category.split(
                        prefix,
                        1,
                    )[1].strip()

                    break

            for value in values.split(","):
                value = value.strip()

                if not value:
                    continue

                components.append(
                    f"{category} - {value}"
                )

        return components

    @staticmethod
    def parse_date(
        date_string: str,
    ) -> datetime:
        return datetime.fromisoformat(
            date_string
        )

    @staticmethod
    def parse_latest_update(
        content: str,
    ) -> list[str]:
        statuses = re.findall(
            r"<strong\b[^>]*>\s*(.*?)\s*</strong>",
            content,
            re.DOTALL | re.IGNORECASE,
        )

        return [
            re.sub(
                r"\s+",
                " ",
                status,
            ).strip()
            for status in statuses
        ]

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

    def matches_filters(self, severity: str | None, components: list[str]) -> bool:
        matches = 0
        enabled_filters = 0

        if self.filters.severity_enabled:
            enabled_filters += 1

            if (
                severity is not None
                and severity in self.filters.severities
            ):
                matches += 1

        if self.filters.components_enabled:
            enabled_filters += 1

            if any(
                component in self.filters.components
                for component in components
            ):
                matches += 1

        if enabled_filters == 0:
            return True

        return matches >= self.filters.required_matches

    def build_message(
        self,
        title: str,
        status: str,
        severity: str | None,
        components: list[str],
        updated_at: str,
        link: str,
    ) -> str:
        message_parts = []

        if self.message.title:
            message_parts.append(
                f"- <b>Message:</b> {title}"
            )

        if self.message.status:
            message_parts.append(
                f"- <b>Status:</b> {status}"
            )

        if self.message.severity:
            message_parts.append(
                f"- <b>Severity:</b> "
                f"{escape(severity or 'unknown')}"
            )

        if self.message.components:
            message_parts.append(
                f"- <b>Components:</b> "
                f"{escape(', '.join(components) or 'unknown')}"
            )

        if self.message.updated_at:
            message_parts.append(
                f"- <b>Incident update:</b> "
                f"{updated_at}"
            )

        if self.message.link:
            message_parts.append(
                f"- <b>Link:</b> {escape(link)}"
            )

        message = "\n".join(message_parts)

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

        return message
