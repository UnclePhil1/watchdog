"""Telegram alert delivery and a lightweight /status command responder.

Uses raw HTTP calls to the Telegram Bot API via httpx rather than a full
bot framework, since the only inbound feature needed is a single /status
command.
"""

import logging
import threading
import time

import httpx

logger = logging.getLogger("watchdog.telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=15)

    def send(self, text: str, max_retries: int = 4) -> bool:
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        delay = 1.5
        for attempt in range(1, max_retries + 1):
            try:
                resp = self._client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Telegram send failed (attempt %s/%s, status=%s): %s",
                    attempt, max_retries, resp.status_code, resp.text,
                )
            except httpx.HTTPError as e:
                logger.warning("Telegram send error (attempt %s/%s): %s", attempt, max_retries, e)
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
        logger.error("Telegram send permanently failed after %s attempts", max_retries)
        return False


class StatusResponder(threading.Thread):
    """Long-polls getUpdates and replies to /status with a status_provider() string."""

    def __init__(self, token: str, chat_id: str, status_provider, notifier: TelegramNotifier):
        super().__init__(daemon=True, name="telegram-status-responder")
        self.token = token
        self.chat_id = str(chat_id)
        self.status_provider = status_provider
        self.notifier = notifier
        self._stop_event = threading.Event()
        self._client = httpx.Client(timeout=40)
        self._offset = None

    def _skip_backlog(self):
        try:
            resp = self._client.get(
                TELEGRAM_API.format(token=self.token, method="getUpdates"),
                params={"limit": 1, "offset": -1},
            )
            results = resp.json().get("result", [])
            if results:
                self._offset = results[-1]["update_id"] + 1
        except Exception as e:
            logger.warning("Could not skip Telegram update backlog: %s", e)

    def run(self):
        self._skip_backlog()
        while not self._stop_event.is_set():
            try:
                params = {"timeout": 30}
                if self._offset is not None:
                    params["offset"] = self._offset
                resp = self._client.get(
                    TELEGRAM_API.format(token=self.token, method="getUpdates"), params=params
                )
                data = resp.json()
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    message = update.get("message") or update.get("channel_post")
                    if not message:
                        continue
                    text = (message.get("text") or "").strip()
                    chat_id = str(message.get("chat", {}).get("id"))
                    if chat_id != self.chat_id:
                        continue
                    if text.startswith("/status"):
                        self.notifier.send(self.status_provider())
            except httpx.HTTPError as e:
                logger.warning("Telegram getUpdates error: %s", e)
                time.sleep(3)
            except Exception:
                logger.exception("Unexpected error in Telegram status responder")
                time.sleep(3)

    def stop(self):
        self._stop_event.set()
