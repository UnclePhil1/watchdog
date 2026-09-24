"""Web3 connection setup and a generic retry wrapper for RPC calls."""

import logging
import time

from web3 import Web3
from web3.middleware import geth_poa_middleware

logger = logging.getLogger("watchdog.chain")


def _inject_poa(w3: Web3) -> None:
    # BSC blocks carry extra validator data in extraData (like Clique PoA
    # chains), which trips web3.py's default block header validation
    # without this middleware.
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)


def get_web3(cfg) -> Web3:
    if cfg.rpc_wss:
        try:
            w3 = Web3(Web3.WebsocketProvider(cfg.rpc_wss, websocket_timeout=30))
            if w3.is_connected():
                _inject_poa(w3)
                logger.info("Connected to BSC via WebSocket RPC")
                return w3
            logger.warning("WebSocket RPC did not report connected; falling back to HTTP")
        except Exception as e:
            logger.warning("WebSocket RPC connection failed (%s); falling back to HTTP", e)

    if not cfg.rpc_http:
        raise RuntimeError("WebSocket RPC unavailable and no BSC_RPC_HTTP configured as fallback")

    w3 = Web3(Web3.HTTPProvider(cfg.rpc_http, request_kwargs={"timeout": 20}))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to BSC via HTTP RPC")
    _inject_poa(w3)
    logger.info("Connected to BSC via HTTP RPC")
    return w3


def call_with_retry(fn, *args, retries=5, base_delay=1.5, what="RPC call", **kwargs):
    """Call fn(*args, **kwargs), retrying with exponential backoff on any
    exception (RPC disconnects, rate limits, timeouts, etc)."""
    delay = base_delay
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad: any RPC failure is retryable
            last_exc = e
            logger.warning("%s failed (attempt %s/%s): %s", what, attempt, retries, e)
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 30)
    logger.error("%s permanently failed after %s attempts: %s", what, retries, last_exc)
    raise last_exc
