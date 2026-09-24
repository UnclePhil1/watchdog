"""Environment configuration loading."""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

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
    )
