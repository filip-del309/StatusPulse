import httpx

from .base import Notifier


class TelegramNotifier(Notifier):

    def __init__(self, bot_token: str, chat_id: str, client: httpx.AsyncClient):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = client

    async def send(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        response = await self.client.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
        )

        response.raise_for_status()
