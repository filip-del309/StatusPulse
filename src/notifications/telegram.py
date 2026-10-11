import asyncio
import logging

import httpx

from .base import Notifier


logger = logging.getLogger(__name__)


class TelegramNotifier(Notifier):

    def __init__(self, bot_token: str, chat_id: str, client: httpx.AsyncClient):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = client

    async def send(self, message: str) -> None:
        url = (
            f"https://api.telegram.org/bot"
            f"{self.bot_token}/sendMessage"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self.client.post(
                    url,
                    json=payload,
                    timeout=httpx.Timeout(30.0),
                )

                response.raise_for_status()

                logger.info("Telegram message sent successfully")
                return

            except httpx.TimeoutException:
                logger.warning(
                    "Telegram timeout (attempt %d/%d)",
                    attempt,
                    max_attempts,
                )

            except httpx.RequestError:
                logger.exception(
                    "Telegram request failed (attempt %d/%d)",
                    attempt,
                    max_attempts,
                )

            except httpx.HTTPStatusError:
                logger.exception(
                    "Telegram returned an HTTP error"
                )
                raise

            if attempt < max_attempts:
                delay = 2 ** attempt

                logger.info(
                    "Retrying Telegram delivery in %d seconds",
                    delay,
                )

                await asyncio.sleep(delay)

        raise httpx.TimeoutException(
            f"Telegram delivery failed after {max_attempts} attempts"
        )
