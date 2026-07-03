import logging
import os

import requests
from dotenv import load_dotenv


LOGGER = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        load_dotenv()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def send_test_message(self):
        self.send_text("Housing alerts är igång.")

    def send_listing(self, source_name, listing):
        message = "\n".join(
            [
                "Ny lägenhet hittad",
                "",
                f"Källa: {source_name}",
                f"Titel: {listing.get('title') or '-'}",
                f"Distrikt: {listing.get('district') or '-'}",
                f"Område: {listing.get('area') or '-'}",
                f"Adress: {listing.get('address') or '-'}",
                f"Hyra: {listing.get('rent') or 'Ej angiven'}",
                f"Rum: {listing.get('rooms') or 'Ej angivet'}",
                f"Storlek: {listing.get('size') or '-'}",
                f"Länk: {listing.get('url') or '-'}",
            ]
        )
        return self.send_text(message)

    def send_text(self, text):
        if not self.configured:
            LOGGER.info("Telegram är inte konfigurerat, hoppar över notis.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": False},
                timeout=20,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            LOGGER.warning("Telegram-notis misslyckades: %s", exc)
            return False
