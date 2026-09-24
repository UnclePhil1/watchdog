"""Environment configuration loading."""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

from solana_utils import is_probably_solana_address

load_dotenv()

logger = logging.getLogger("watchdog.config")

DEFAULT_SOURCE_WALLET = "0xa81697d866bFCfbDecd44134c5c02c7032c07777"


@dataclass
class Config:
    rpc_wss: Optional[str]
    rpc_http: Optional[str]
    telegram_token: str
    telegram_chat_id: str
    source_wallet: str
    poll_interval_seconds: float
    confirmations: int
    launch_lookback_blocks: int
    max_block_batch: int
    start_block: Optional[int]
    db_path: str
    log_level: str
    # Solana (pump.fun) launch monitoring — optional. Disabled entirely
    # unless SOLANA_RPC_HTTP is set, since there is no on-chain way to
    # automatically link a BSC recipient wallet to a Solana wallet; the
    # watchlist is operator-supplied (env var and/or the /watch_sol
    # Telegram command).
    solana_rpc_http: Optional[str] = None
    solana_watchlist: List[str] = field(default_factory=list)
    solana_poll_interval_seconds: float = 10.0


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    rpc_wss = os.environ.get("BSC_RPC_WSS", "").strip() or None
    rpc_http = os.environ.get("BSC_RPC_HTTP", "").strip() or None
    if not rpc_wss and not rpc_http:
        raise RuntimeError("Set BSC_RPC_WSS and/or BSC_RPC_HTTP in the environment")

    start_block_env = os.environ.get("START_BLOCK", "").strip()

    solana_rpc_http = os.environ.get("SOLANA_RPC_HTTP", "").strip() or None
    solana_watchlist_env = os.environ.get("SOLANA_WATCHLIST", "").strip()
    solana_watchlist = []
    if solana_watchlist_env:
        for addr in solana_watchlist_env.split(","):
            addr = addr.strip()
            if not addr:
                continue
            if not is_probably_solana_address(addr):
                logger.warning("Ignoring SOLANA_WATCHLIST entry that doesn't look like a Solana address: %s", addr)
                continue
            solana_watchlist.append(addr)

    return Config(
        rpc_wss=rpc_wss,
        rpc_http=rpc_http,
        telegram_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
        source_wallet=os.environ.get("SOURCE_WALLET", "").strip() or DEFAULT_SOURCE_WALLET,
        poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", "4")),
        confirmations=int(os.environ.get("CONFIRMATIONS", "3")),
        launch_lookback_blocks=int(os.environ.get("LAUNCH_LOOKBACK_BLOCKS", "200")),
        max_block_batch=int(os.environ.get("MAX_BLOCK_BATCH", "200")),
        start_block=int(start_block_env) if start_block_env else None,
        db_path=os.environ.get("DB_PATH", "watchdog.db"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        solana_rpc_http=solana_rpc_http,
        solana_watchlist=solana_watchlist,
        solana_poll_interval_seconds=float(os.environ.get("SOLANA_POLL_INTERVAL_SECONDS", "10")),
    )
