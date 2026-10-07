import asyncio
import logging

import httpx

from config import load_config
from notifications.telegram import TelegramNotifier
from services.scheduler import run_source
from sources.artifacthub import ArtifactHubSource
from sources.cloudflare import CloudflareSource
from sources.tailscale import TailscaleSource
from sources.scaleway import ScalewaySource


class SecretFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()

        self.secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        for secret in self.secrets:
            message = message.replace(secret, "***REDACTED***")

        record.msg = message
        record.args = ()

        return True


def configure_logging(secrets: list[str]) -> None:
    handler = logging.StreamHandler()

    handler.addFilter(SecretFilter(secrets))

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "[%(levelname)s] "
            "%(name)s: %(message)s"
        ),
        handlers=[handler],
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)

async def main():
    config = load_config()

    configure_logging([config.telegram.bot_token])

    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(
            bot_token=config.telegram.bot_token,
            chat_id=config.telegram.chat_id,
            client=client,
        )

        sources = [
            (
                TailscaleSource(
                    client=client,
                    notifier=notifier,
                    filters=(config.sources.tailscale.filters),
                    message=(config.sources.tailscale.message),
                ),
                config.sources.tailscale,
            ),
            (
                CloudflareSource(
                    client=client,
                    notifier=notifier,
                    filters=(config.sources.cloudflare.filters),
                    message=(config.sources.cloudflare.message),
                ),
                config.sources.cloudflare,
            ),
            (
                ScalewaySource(
                    client=client,
                    notifier=notifier,
                ),
                config.sources.scaleway,
            ),
            (
                ArtifactHubSource(
                    client=client,
                    notifier=notifier,
                    packages=(config.sources.artifacthub.packages),
                    lookback_hours=(config.sources.artifacthub.lookback_hours),
                ),
                config.sources.artifacthub,
            ),
        ]

        tasks = []

        for source, source_config in sources:
            if not source_config.enabled:
                logging.info("Source '%s' is disabled", source.name,)
                continue

            tasks.append(
                asyncio.create_task(
                    run_source(
                        source=source,
                        schedule=(source_config.schedule)
                    )
                )
            )

        if tasks:
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
