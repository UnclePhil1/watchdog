"""Entry point: wires up config, state, chain connection, Telegram, and
the watch loop. Run with: python main.py
"""

import logging
import sys
from datetime import datetime, timezone

from chain_utils import get_web3
from config import load_config
from state import State
from telegram_notify import StatusResponder, TelegramNotifier
from watcher import Watcher


def main():
    cfg = load_config()

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("watchdog.main")

    state = State(cfg.db_path)
    w3 = get_web3(cfg)
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    start_time = datetime.now(timezone.utc)

    watcher = Watcher(w3=w3, cfg=cfg, state=state, notifier=notifier)

    def status_text() -> str:
        uptime = datetime.now(timezone.utc) - start_time
        return (
            "\U0001F4CA <b>Watchdog status</b>\n\n"
            f"Source wallet: <code>{watcher.source_checksum}</code>\n"
            f"Last processed block: <code>{state.get_last_block()}</code>\n"
            f"Watched recipient wallets: <b>{state.get_watchlist_count()}</b>\n"
            f"Uptime: {str(uptime).split('.')[0]}"
        )

    responder = StatusResponder(cfg.telegram_token, cfg.telegram_chat_id, status_text, notifier)
    responder.start()

    logger.info("Watchdog started. Watching source wallet %s", watcher.source_checksum)
    try:
        watcher.run()
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
