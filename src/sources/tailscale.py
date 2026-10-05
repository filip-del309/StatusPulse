import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo

import httpx
import xml.etree.ElementTree as ET

from config import (
    TailscaleFilterConfig,
    TailscaleMessageConfig,
)
from notifications.base import Notifier
from sources.base import Source


logger = logging.getLogger(__name__)


class TailscaleSource(Source):
    URL = "https://status.tailscale.com/feed.rss"

    def __init__(self, client: httpx.AsyncClient, notifier: Notifier, filters: TailscaleFilterConfig, message: TailscaleMessageConfig):
        self.client = client
        self.notifier = notifier
        self.filters = filters
        self.message_config = message
        self.last_updated_at: datetime | None = None

    @property
    def name(self) -> str:
        return "tailscale"

    async def run(self) -> None:
        logger.info("Checking Tailscale...")

        incidents = await self.get_incidents()

        if not incidents:
            logger.info("Tailscale: no incidents")
            return

        latest_updated_at = self.parse_date(incidents[0].findtext("pubDate"))

        if self.last_updated_at is None:
            self.last_updated_at = latest_updated_at
            logger.info("Tailscale: initialized last_updated_at=%s", latest_updated_at.isoformat())

            return

        new_incidents = []

        for incident in incidents:
            pub_date_string = incident.findtext("pubDate")

            if not pub_date_string:
                continue

            pub_date = self.parse_date(pub_date_string)

            if pub_date <= self.last_updated_at:
                break

            new_incidents.append(incident)

        if not new_incidents:
            logger.info("Tailscale: no new updates")
            return

        logger.info("Tailscale: found %d new update(s)", len(new_incidents))

        for incident in reversed(new_incidents):
            description = incident.findtext("description", "")
            status = self.parse_status(description)
            components = self.parse_components(description)

            title = incident.findtext("title", "Unknown incident")
            pub_date = incident.findtext("pubDate", "unknown")
            link = incident.findtext("link", "unknown")

            if not self.matches_filters(components):
                logger.info("Tailscale incident filtered out: %s (components=%s)", title, components)
                continue

            message = self.build_message(
                title=title,
                status=status,
                components=components,
                pub_date=pub_date,
                link=link,
            )

            await self.notifier.send(message)

            logger.info("Tailscale alert sent: %s (%s)", title, status)

        self.last_updated_at = latest_updated_at

        logger.info("Tailscale alerts processed")

    async def get_incidents(self) -> list[ET.Element]:
        response = await self.client.get(self.URL)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        return root.findall("./channel/item")

    def matches_filters(self, components: list[str]) -> bool:
        if not self.filters.components_enabled:
            return True

        if not self.filters.components:
            logger.warning("Tailscale component filter is enabled but no components are configured")
            return False

        configured_components = {self.normalize_component(component) for component in self.filters.components}

        incident_components = {self.normalize_component(component) for component in components}

        matches = bool(configured_components & incident_components)

        logger.debug("Tailscale component filter: configured=%s, incident=%s, matches=%s", configured_components, incident_components, matches,)

        return matches

    @staticmethod
    def parse_status(description: str) -> str:
        match = re.search(r"<b>Status:\s*(.*?)</b>", description, re.IGNORECASE)

        if match:
            return match.group(1).strip()

        return "unknown"

    @staticmethod
    def parse_components(description: str) -> list[str]:
        affected_components_match = re.search(r"<b>Affected components</b>(.*)", description, re.IGNORECASE | re.DOTALL)

        if not affected_components_match:
            return []

        components_section = (affected_components_match.group(1))
        components = re.findall(r"<li>\s*(.*?)\s*</li>", components_section, re.IGNORECASE | re.DOTALL)

        result = []

        for component in components:
            component = re.sub(r"<[^>]+>", "", component).strip() # removing HTLM tags
            component = re.sub(r"\s+", " ", component) # replacing multiple whitespace characters by one space
            component = re.sub(r"\s*\([^)]*\)\s*$", "", component).strip() # removing parentheses from the end

            if component:
                result.append(component)

        return result

    @staticmethod
    def normalize_component(component: str) -> str:
        return " ".join(component.lower().split())

    def build_message(self, title: str, status: str, components: list[str], pub_date: str, link: str) -> str:
        escaped_title = escape(title)
        escaped_status = escape(status)
        escaped_components = [escape(component) for component in components]

        formatted_pub_date = self.format_date(pub_date)

        escaped_link = escape(link, quote=True)

        if status.lower() in ("resolved", "complete"):
            header = ("✅ <b>Tailscale Incident:</b>")
        else:
            header = ("🚨 <b>Tailscale Incident:</b>")

        fields = []

        if self.message_config.title:
            fields.append(f"- <b>Message:</b> {escaped_title}")

        if self.message_config.status:
            fields.append(f"- <b>Status:</b> {escaped_status}")

        if self.message_config.components:
            if escaped_components:
                components_text = ", ".join(escaped_components)
            else:
                components_text = "None"

            fields.append(f"- <b>Components:</b> {components_text}")

        if self.message_config.updated_at:
            fields.append(f"- <b>Incident update:</b> {formatted_pub_date}")

        if self.message_config.link:
            fields.append(f"- <b>Link:</b> {escaped_link}")

        if not fields:
            return header

        return (f"{header}\n\n" + "\n".join(fields))

    @staticmethod
    def parse_date(date_string: str) -> datetime:
        return parsedate_to_datetime(date_string)

    @staticmethod
    def format_date(date_string: str) -> str:
        date = TailscaleSource.parse_date(date_string)

        date = date.astimezone(ZoneInfo("Europe/Warsaw"))

        return date.strftime("%a, %d %b %Y, %H:%M:%S %Z")


