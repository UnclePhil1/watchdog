"""Entry point: wires up config, state, chain connections, Telegram, and
the watch loop(s). Run with: python main.py
"""

import logging
import sys
import threading
from datetime import datetime, timezone

from chain_utils import get_web3
from config import load_config
from solana_utils import is_probably_solana_address
from solana_watcher import SolanaWatcher
from state import State
from telegram_notify import CommandResponder, TelegramNotifier
from utils import escape_html
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

    solana_watcher = None
    if cfg.solana_rpc_http:
        for address in cfg.solana_watchlist:
            state.add_solana_watchlist(address, source="env")
        solana_watcher = SolanaWatcher(cfg=cfg, state=state, notifier=notifier)
        threading.Thread(target=solana_watcher.run, daemon=True, name="solana-watcher").start()
        logger.info(
            "Solana monitoring enabled, watching %s wallet(s) for pump.fun launches",
            state.get_solana_watchlist_count(),
        )
    else:
        logger.info("Solana monitoring disabled (SOLANA_RPC_HTTP not set)")

    def status_text() -> str:
        uptime = datetime.now(timezone.utc) - start_time
        lines = [
            "\U0001F4CA <b>Watchdog status</b>\n",
            f"Source wallet: <code>{watcher.source_checksum}</code>",
            f"Last processed BSC block: <code>{state.get_last_block()}</code>",
            f"Watched recipient wallets (BSC): <b>{state.get_watchlist_count()}</b>",
        ]
        if cfg.solana_rpc_http:
            lines.append(f"Watched Solana wallets: <b>{state.get_solana_watchlist_count()}</b>")
        else:
            lines.append("Solana monitoring: <i>disabled</i>")
        lines.append(f"Uptime: {str(uptime).split('.')[0]}")
        return "\n".join(lines)

    def help_text(_args: str) -> str:
        lines = [
            "\U0001F916 <b>Watchdog commands</b>\n",
            "/status — last block, watchlist sizes, uptime",
        ]
        if cfg.solana_rpc_http:
            lines += [
                "/watch_sol &lt;address&gt; — add a Solana wallet to the pump.fun launch watchlist",
                "/unwatch_sol &lt;address&gt; — remove a Solana wallet from that watchlist",
            ]
        lines.append("/help — this message")
        return "\n".join(lines)

    def watch_sol_handler(args: str) -> str:
        if not cfg.solana_rpc_http:
            return "Solana monitoring is not configured (set SOLANA_RPC_HTTP) — address not added."
        address = args.strip()
        if not address:
            return "Usage: /watch_sol &lt;solana address&gt;"
        if not is_probably_solana_address(address):
            return f"That doesn't look like a valid Solana address: <code>{escape_html(address)}</code>"
        added = state.add_solana_watchlist(address, source="telegram")
        if added:
            return f"✅ Now watching Solana wallet <code>{address}</code> for pump.fun launches."
        return f"Already watching <code>{address}</code>."

    def unwatch_sol_handler(args: str) -> str:
        if not cfg.solana_rpc_http:
            return "Solana monitoring is not configured (set SOLANA_RPC_HTTP)."
        address = args.strip()
        if not address:
            return "Usage: /unwatch_sol &lt;solana address&gt;"
        removed = state.remove_solana_watchlist(address)
        if removed:
            return f"\U0001F5D1 Stopped watching Solana wallet <code>{address}</code>."
        return f"<code>{escape_html(address)}</code> was not on the Solana watchlist."

    commands = {
        "/status": lambda _args: status_text(),
        "/help": help_text,
        "/start": help_text,
        "/watch_sol": watch_sol_handler,
        "/unwatch_sol": unwatch_sol_handler,
    }
    responder = CommandResponder(cfg.telegram_token, cfg.telegram_chat_id, notifier, commands)
    responder.start()

    logger.info("Watchdog started. Watching source wallet %s", watcher.source_checksum)
    try:
        watcher.run()
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
