import asyncio
import logging
import re
import xml.etree.ElementTree as ET

from datetime import datetime
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from api import set_active_alert, remove_active_alert
from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class ScalewayIncidentParser(HTMLParser):
    def __init__(self):
        super().__init__()

        self.severity = None
        self.components_text = ""
        self._inside_components = False
        self._components_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()

        if tag == "h1" and "incident-name" in classes:
            for class_name in classes:
                if class_name.startswith("impact-"):
                    self.severity = (
                        class_name.removeprefix("impact-").capitalize()
                    )

        if tag == "div" and "components-affected" in classes:
            self._inside_components = True
            self._components_depth = 1
        elif self._inside_components and tag == "div":
            self._components_depth += 1

    def handle_endtag(self, tag):
        if not self._inside_components or tag != "div":
            return

        self._components_depth -= 1

        if self._components_depth <= 0:
            self._inside_components = False
            self._components_depth = 0

    def handle_data(self, data):
        if self._inside_components:
            self.components_text += data


class ScalewaySource(Source):
    URL = "https://status.scaleway.com/history.atom"

    NS = {"atom": "http://www.w3.org/2005/Atom"}

    HTTP_TIMEOUT = httpx.Timeout(
        30.0,
        connect=10.0,
    )

    MAX_ATTEMPTS = 3

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

        try:
            incidents = await self.get_incidents()
        except (httpx.HTTPError, ET.ParseError):
            logger.exception(
                "Scaleway: failed to retrieve or parse incident feed"
            )
            return

        if not incidents:
            logger.info("Scaleway: no incidents")
            return

        await self.sync_active_alerts(incidents)

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
                latest_updated_at.isoformat(),
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

            try:
                updated_at = self.parse_date(updated_at_string)
            except (TypeError, ValueError):
                logger.warning(
                    "Scaleway: invalid incident date: %s",
                    updated_at_string,
                )
                continue

            if updated_at <= self.last_updated_at:
                break

            new_incidents.append(incident)

        if not new_incidents:
            logger.info("Scaleway: no new updates")
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

            updated_at_string = incident.findtext(
                "atom:updated",
                "",
                namespaces=self.NS,
            )

            link_element = incident.find(
                "atom:link[@rel='alternate']",
                self.NS,
            )

            link = (
                link_element.get("href")
                if link_element is not None
                else None
            )

            if not link:
                logger.warning(
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

            try:
                severity, components = await self.get_incident_details(
                    link
                )
            except (httpx.HTTPError, ET.ParseError):
                logger.exception(
                    "Scaleway: could not retrieve details for '%s'",
                    title,
                )
                continue

            if not self.matches_filters(severity, components):
                logger.info(
                    "Scaleway: skipping incident '%s' "
                    "because it does not match filters",
                    title,
                )
                continue

            formatted_updated_at = (
                self.format_date(updated_at_string)
                if updated_at_string
                else ""
            )

            message = self.build_message(
                title=title,
                status=last_status,
                severity=severity,
                components=components,
                updated_at=formatted_updated_at,
                link=link,
            )

            try:
                await self.notifier.send(message)
            except Exception:
                logger.exception(
                    "Scaleway: failed to send notification for '%s'",
                    title,
                )
                continue

            logger.info(
                "Scaleway alert sent: %s (%s)",
                title,
                last_status,
            )

        self.last_updated_at = latest_updated_at

        logger.info("Scaleway alerts processed")

    async def _get_with_retry(self, url: str) -> httpx.Response:
        """Wykonuje żądanie HTTP z timeoutem i ponawianiem."""

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                response = await self.client.get(
                    url,
                    timeout=self.HTTP_TIMEOUT,
                )
                response.raise_for_status()
                return response

            except httpx.TimeoutException:
                logger.warning(
                    "Scaleway: request timeout "
                    "(attempt %d/%d): %s",
                    attempt,
                    self.MAX_ATTEMPTS,
                    url,
                )

            except httpx.RequestError:
                logger.warning(
                    "Scaleway: request failed "
                    "(attempt %d/%d): %s",
                    attempt,
                    self.MAX_ATTEMPTS,
                    url,
                    exc_info=True,
                )

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise

                logger.warning(
                    "Scaleway: server returned HTTP %s "
                    "(attempt %d/%d): %s",
                    exc.response.status_code,
                    attempt,
                    self.MAX_ATTEMPTS,
                    url,
                )

            if attempt < self.MAX_ATTEMPTS:
                delay = 2 ** attempt

                logger.info(
                    "Scaleway: retrying request in %d seconds",
                    delay,
                )

                await asyncio.sleep(delay)

        raise httpx.TimeoutException(
            f"Scaleway request failed after "
            f"{self.MAX_ATTEMPTS} attempts: {url}"
        )

    async def get_incidents(self) -> list[ET.Element]:
        response = await self._get_with_retry(self.URL)

        root = ET.fromstring(response.content)

        incidents = root.findall("atom:entry", self.NS)

        valid_incidents = []

        for incident in incidents:
            updated_at_string = incident.findtext(
                "atom:updated",
                namespaces=self.NS,
            )

            if not updated_at_string:
                logger.warning(
                    "Scaleway: skipping incident without updated date"
                )
                continue

            try:
                self.parse_date(updated_at_string)
            except (TypeError, ValueError):
                logger.warning(
                    "Scaleway: skipping incident with invalid date: %s",
                    updated_at_string,
                )
                continue

            valid_incidents.append(incident)

        valid_incidents.sort(
            key=lambda incident: self.parse_date(
                incident.findtext(
                    "atom:updated",
                    namespaces=self.NS,
                )
            ),
            reverse=True,
        )

        return valid_incidents

    async def get_incident_details(
        self,
        link: str,
    ) -> tuple[str | None, list[str]]:
        response = await self._get_with_retry(link)

        parser = ScalewayIncidentParser()
        parser.feed(response.text)
        parser.close()

        components = self.parse_components(parser.components_text)

        return parser.severity, components

    @staticmethod
    def parse_components(components_text: str) -> list[str]:
        components = []

        matches = re.findall(
            r"([^()]+?)\s*\(([^()]*)\)",
            components_text,
        )

        prefixes = (
            "This incident affects:",
            "This incident affected:",
            "This scheduled maintenance affected:",
        )

        for category, values in matches:
            category = category.strip()

            for prefix in prefixes:
                if prefix in category:
                    category = category.split(prefix, 1)[1].strip()
                    break

            for value in values.split(","):
                value = value.strip()

                if value:
                    components.append(f"{category} - {value}")

        return components

    @staticmethod
    def parse_date(date_string: str) -> datetime:
        parsed = datetime.fromisoformat(date_string)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))

        return parsed

    @staticmethod
    def parse_latest_update(content: str) -> list[str]:
        statuses = re.findall(
            r"<strong\b[^>]*>\s*(.*?)\s*</strong>",
            content,
            re.DOTALL | re.IGNORECASE,
        )

        return [
            re.sub(r"\s+", " ", status).strip()
            for status in statuses
        ]

    @staticmethod
    def format_date(date_string: str) -> str:
        date = ScalewaySource.parse_date(date_string)
        date = date.astimezone(ZoneInfo("Europe/Warsaw"))

        return date.strftime("%a, %d %b %Y, %H:%M:%S %Z")

    def matches_filters(
        self,
        severity: str | None,
        components: list[str],
    ) -> bool:
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

            configured_components = {
                component.strip().casefold()
                for component in self.filters.components
            }

            incident_components = {
                component.strip().casefold()
                for component in components
            }

            if configured_components & incident_components:
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
                f"- <b>Message:</b> {escape(title)}"
            )

        if self.message.status:
            message_parts.append(
                f"- <b>Status:</b> {escape(status)}"
            )

        if self.message.severity and severity:
            message_parts.append(
                f"- <b>Severity:</b> {escape(severity)}"
            )

        if self.message.components and components:
            message_parts.append(
                f"- <b>Components:</b> "
                f"{escape(', '.join(components))}"
            )

        if self.message.updated_at and updated_at:
            message_parts.append(
                f"- <b>Incident update:</b> {escape(updated_at)}"
            )

        if self.message.link:
            message_parts.append(
                f"- <b>Link:</b> {escape(link, quote=True)}"
            )

        details = "\n".join(message_parts)

        normalized_status = status.lower()

        if normalized_status in ("resolved", "completed"):
            header = "✅ <b>Scaleway Incident:</b>"
        elif normalized_status == "scheduled":
            header = "📅 <b>Scaleway Scheduled Maintenance:</b>"
        else:
            header = "🚨 <b>Scaleway Incident:</b>"

        return f"{header}\n\n{details}" if details else header

    async def sync_active_alerts(
        self,
        incidents: list[ET.Element],
    ) -> None:
        logger.info("Scaleway: syncing active alerts")

        latest_incidents = {}

        for incident in incidents:
            link_element = incident.find(
                "atom:link[@rel='alternate']",
                self.NS,
            )

            if link_element is None:
                continue

            link = link_element.get("href")

            if link and link not in latest_incidents:
                latest_incidents[link] = incident

        active_count = 0

        for link, incident in latest_incidents.items():
            title = incident.findtext(
                "atom:title",
                "Unknown incident",
                namespaces=self.NS,
            )

            content = incident.findtext(
                "atom:content",
                "",
                namespaces=self.NS,
            )

            statuses = self.parse_latest_update(content)

            if not statuses:
                logger.debug(
                    "Scaleway: no status found for %s",
                    title,
                )
                continue

            status = statuses[0]
            normalized_status = status.strip().lower()

            resolved_statuses = {
                "resolved",
                "completed",
                "complete",
                "closed",
                "cancelled",
                "canceled",
            }

            if normalized_status in resolved_statuses:
                remove_active_alert(self.name, link)
                continue

            updated_at_string = incident.findtext(
                "atom:updated",
                "",
                namespaces=self.NS,
            )

            try:
                severity, components = await self.get_incident_details(link)
            except (httpx.HTTPError, ET.ParseError):
                logger.exception(
                    "Scaleway: failed to sync active incident '%s'",
                    title,
                )
                continue

            if not self.matches_filters(severity, components):
                remove_active_alert(self.name, link)
                continue

            set_active_alert(
                self.name,
                {
                    "id": link,
                    "source": "Scaleway",
                    "title": title,
                    "status": status,
                    "severity": severity,
                    "components": components,
                    "updated_at": (
                        self.format_date(updated_at_string)
                        if updated_at_string
                        else ""
                    ),
                    "url": link,
                },
            )

            active_count += 1

            await asyncio.sleep(1)

        logger.info(
            "Scaleway: active alerts synced (%d active)",
            active_count,
        )
