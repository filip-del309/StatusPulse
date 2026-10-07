import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import (CloudflareFilterConfig, CloudflareMessageConfig)
from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class CloudflareSource(Source):
    URL = "https://www.cloudflarestatus.com/api/v2/incidents.json"

    def __init__(self, client: httpx.AsyncClient, notifier: Notifier, filters: CloudflareFilterConfig, message: CloudflareMessageConfig):
        self.client = client
        self.notifier = notifier
        self.filters = filters
        self.message_config = message

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
            # self.last_updated_at = latest_updated_at
            self.last_updated_at = self.parse_date("2026-10-05T11:23:33.000Z")
            logger.info("Cloudflare: initialized last_updated_at=%s", latest_updated_at.isoformat())

            return

        new_incidents = []

        for incident in incidents:
            updated_at_string = incident.get("updated_at")

            if not updated_at_string:
                continue

            updated_at = self.parse_date(updated_at_string)

            if updated_at <= self.last_updated_at:
                break

            status = incident.get("status", "")

            created_at_string = incident.get("created_at")

            if not created_at_string:
                continue

            created_at = self.parse_date(created_at_string)

            if (created_at <= self.last_updated_at and status != "resolved"):
                continue

            if not self.matches_filters(incident):
                logger.info("Cloudflare incident filtered out: %s", incident.get("name", "Unknown incident"))
                continue

            new_incidents.append(incident)

        if not new_incidents:
            logger.info("Cloudflare: no new matching updates")
            return

        logger.info("Cloudflare: found %d new matching update(s)", len(new_incidents),)

        for incident in reversed(new_incidents):
            message = self.build_message(incident)

            await self.notifier.send(message)

            logger.info("Cloudflare alert sent: %s (%s)", incident.get("name", "Unknown incident"), incident.get("status", "unknown"))

        self.last_updated_at = latest_updated_at

        logger.info("Cloudflare alerts sent")

    def matches_filters(self, incident: dict) -> bool:
        """
        Apply Cloudflare filters.

        required_matches:
            0 = no filtering
            1 = severity OR components
            2 = severity AND components

        Only enabled filters count toward
        required_matches.
        """

        active_filters = 0
        matches = 0

        if self.filters.severity_enabled:
            active_filters += 1

            severity = self.normalize(incident.get("impact", ""))

            configured_severities = {self.normalize(value) for value in self.filters.severities}

            if (severity and severity in configured_severities):
                matches += 1

        if self.filters.components_enabled:
            active_filters += 1

            incident_components = {self.normalize(component) for component in self.get_components(incident)}
            configured_components = {self.normalize(value) for value in self.filters.components}

            if (incident_components & configured_components):
                matches += 1

        if active_filters == 0:
            return True

        required_matches = min(max(self.filters.required_matches, 1), active_filters)
        return matches >= required_matches

    @staticmethod
    def extract_component_name(name: str,) -> str:
        """
        Extract the component name after
        the last ' - '.

        Example:

        'Cloudflare Sites and Services - API Shield'
        -> 'API Shield'
        """

        name = name.strip()

        if " - " not in name:
            return name

        return name.rsplit(" - ", 1)[1].strip()

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(value.strip().lower().split())

    def build_message(self, incident: dict) -> str:
        name = incident.get("name", "Unknown incident")
        status = incident.get("status", "unknown").capitalize()
        impact = incident.get("impact", "unknown").capitalize()
        created_at = incident.get("created_at", "unknown")
        shortlink = incident.get("shortlink", "unknown")
        components = self.get_components(incident)

        lines = []

        if self.message_config.title:
            if status.lower() in ("resolved", "completed", "canceled"):
                lines.append("✅ <b>Cloudflare Incident:</b>")
            else:
                lines.append("🚨 <b>Cloudflare Incident:</b>")

            lines.append("")

        if self.message_config.title:
            lines.append(f"- <b>Message:</b> {name}")

        if self.message_config.severity:
            lines.append(f"- <b>Severity:</b> {impact}")

        if self.message_config.status:
            lines.append(f"- <b>Status:</b> {status}")

        if self.message_config.components:
            components_text = (", ".join(components) if components else "None")
            lines.append(f"- <b>Components:</b> {components_text}")

        if self.message_config.updated_at:
            formatted_created_at = (self.format_date(created_at))
            lines.append(f"- <b>Incident start:</b> {formatted_created_at}")

        if self.message_config.link:
            lines.append(f"- <b>Shortlink:</b> {shortlink}")

        return "\n".join(lines)

    @staticmethod
    def get_components(incident: dict) -> list[str]:
        """
        Extract component names from incident
        updates.

        Example:

        Cloudflare Sites and Services - API Shield
        -> API Shield
        """

        components = []

        for update in incident.get("incident_updates", []):
            affected_components = (update.get("affected_components") or [])

            for component in affected_components:
                name = component.get("name")

                if not name:
                    continue

                component_name = (CloudflareSource.extract_component_name(name))

                if component_name:
                    components.append(component_name)

        return list(dict.fromkeys(components))


    async def get_incidents(self) -> list[dict]:
        response = await self.client.get(self.URL)
        response.raise_for_status()

        return response.json().get("incidents", [])

    @staticmethod
    def parse_date(date_string: str,) -> datetime:
        return datetime.fromisoformat(date_string.replace("Z", "+00:00"))

    @staticmethod
    def format_date(date_string: str) -> str:
        date = CloudflareSource.parse_date(date_string)

        date = date.astimezone(ZoneInfo("Europe/Warsaw"))

        return date.strftime("%a, %d %b %Y, %H:%M:%S %Z")
