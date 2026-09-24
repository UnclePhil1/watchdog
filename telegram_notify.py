"""Telegram alert delivery and a lightweight slash-command responder.

Uses raw HTTP calls to the Telegram Bot API via httpx rather than a full
bot framework, since the only inbound feature needed is a handful of
simple commands (/status, /watch_sol, /unwatch_sol).
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


class CommandResponder(threading.Thread):
    """Long-polls getUpdates and dispatches recognized slash commands.

    `commands` maps a command string (e.g. "/status") to a handler
    `callable(args: str) -> Optional[str]`, where `args` is whatever text
    followed the command and the return value (if truthy) is sent back
    as a reply. Only messages from `chat_id` are processed.
    """

    def __init__(self, token: str, chat_id: str, notifier: TelegramNotifier, commands: dict):
        super().__init__(daemon=True, name="telegram-command-responder")
        self.token = token
        self.chat_id = str(chat_id)
        self.notifier = notifier
        self.commands = commands
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
                    self._handle_update(update)
            except httpx.HTTPError as e:
                logger.warning("Telegram getUpdates error: %s", e)
                time.sleep(3)
            except Exception:
                logger.exception("Unexpected error in Telegram command responder")
                time.sleep(3)

    def _handle_update(self, update):
        message = update.get("message") or update.get("channel_post")
        if not message:
            return
        text = (message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id"))
        if chat_id != self.chat_id or not text.startswith("/"):
            return

        parts = text.split(maxsplit=1)
        command = parts[0].split("@")[0]  # strip a "/cmd@BotName" suffix
        args = parts[1].strip() if len(parts) > 1 else ""

        handler = self.commands.get(command)
        if handler is None:
            return
        try:
            reply = handler(args)
        except Exception:
            logger.exception("Error handling Telegram command %s", command)
            reply = "⚠️ Internal error handling that command; check the logs."
        if reply:
            self.notifier.send(reply)

    def stop(self):
        self._stop_event.set()
