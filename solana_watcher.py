"""Solana-side monitoring: watches an operator-supplied list of Solana
wallets (see README) for pump.fun token launches.

There is no cryptographic link between a BSC address and a Solana
address, so this does not automatically discover which Solana wallets
belong to a funded BSC recipient — wallets are added via SOLANA_WATCHLIST
or the /watch_sol Telegram command once the operator has identified them
through their own means.

Detection: a wallet's transaction that both (a) invokes the pump.fun
program and (b) logs "Instruction: Create" (Anchor's standard per-
instruction log line) is treated as a pump.fun token creation. The new
mint is identified by diffing pre/post token balances (robust to
pump.fun account-ordering changes); the token name/symbol are a
best-effort Borsh parse of the instruction data and are allowed to fail
gracefully — a parse failure never blocks the core alert (mint,
deployer, signature, links), it just leaves name/symbol as "unknown".
"""

import logging
import time

import httpx

from chain_utils import call_with_retry
from constants import PUMP_FUN_PROGRAM_ID
from solana_utils import b58decode, read_borsh_string
from utils import escape_html, shorten, solscan_token_url, solscan_tx_url

logger = logging.getLogger("watchdog.solana")

_ANCHOR_DISCRIMINATOR_LEN = 8
_SIGNATURES_PAGE_LIMIT = 25
_MAX_PAGES_PER_POLL = 20  # safety cap: at most 500 signatures/wallet/poll
_TX_FETCH_RETRIES = 3
_TX_FETCH_RETRY_DELAY = 2.0


class SolanaWatcher:
    def __init__(self, cfg, state, notifier):
        self.cfg = cfg
        self.state = state
        self.notifier = notifier
        self.rpc_url = cfg.solana_rpc_http
        self._client = httpx.Client(timeout=20)

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    def run(self):
        logger.info("Solana watcher started (poll interval %ss)", self.cfg.solana_poll_interval_seconds)
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Error in Solana watch loop; will retry next cycle")
            time.sleep(self.cfg.solana_poll_interval_seconds)

    def poll_once(self):
        for address in self.state.get_solana_watchlist():
            try:
                self._poll_wallet(address)
            except Exception:
                logger.exception("Error polling Solana wallet %s", address)

    def _poll_wallet(self, address: str):
        cursor = self.state.get_solana_cursor(address)
        if cursor is None:
            # First time seeing this wallet: initialize the cursor at the
            # current latest signature. No history backfill / alerting,
            # mirroring the BSC watcher's "start from head" default.
            latest_page = self._rpc("getSignaturesForAddress", [address, {"limit": 1}])
            if latest_page:
                self.state.set_solana_cursor(address, latest_page[0]["signature"])
                logger.info("Solana wallet %s: cursor initialized at %s (no backfill)", address, latest_page[0]["signature"])
            else:
                logger.info("Solana wallet %s: no transaction history yet", address)
            return

        new_sigs = self._fetch_new_signatures(address, cursor)
        if not new_sigs:
            return

        for entry in reversed(new_sigs):  # oldest -> newest
            if entry.get("err") is not None:
                continue
            try:
                self._process_signature(address, entry["signature"])
            except Exception:
                logger.exception("Error processing Solana tx %s for wallet %s", entry["signature"], address)

        self.state.set_solana_cursor(address, new_sigs[0]["signature"])

    def _fetch_new_signatures(self, address: str, until_signature: str):
        """Paginate backward from the current head until we hit
        until_signature (or run out of new signatures / the safety cap),
        so a burst of activity between polls isn't silently truncated."""
        pages = []
        before = None
        for _ in range(_MAX_PAGES_PER_POLL):
            params = [address, {"limit": _SIGNATURES_PAGE_LIMIT, "until": until_signature}]
            if before:
                params[1]["before"] = before
            page = self._rpc("getSignaturesForAddress", params)
            if not page:
                break
            pages.extend(page)
            if len(page) < _SIGNATURES_PAGE_LIMIT:
                break
            before = page[-1]["signature"]
        return pages

    # ------------------------------------------------------------------ #
    # Per-transaction detection
    # ------------------------------------------------------------------ #

    def _process_signature(self, wallet_address: str, signature: str):
        alert_id = f"{signature}:-1:sol_launch"
        if self.state.is_alerted(alert_id):
            return

        tx = self._get_transaction_with_wait(signature)
        if tx is None:
            logger.warning("Solana tx %s never became available; skipping", signature)
            return

        if not self._invokes_pump_fun_create(tx):
            return

        mint = self._extract_new_mint(tx)
        if mint and self.state.is_solana_token_launched(mint):
            return

        name, symbol = self._extract_name_symbol(tx)
        self._send_launch_alert(wallet_address, mint, signature, name, symbol)

        if mint:
            self.state.mark_solana_token_launched(mint, wallet_address, signature)
        self.state.mark_alerted(alert_id, "sol_launch", signature)

    def _get_transaction_with_wait(self, signature: str):
        for attempt in range(_TX_FETCH_RETRIES):
            tx = self._rpc(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            if tx is not None:
                return tx
            if attempt < _TX_FETCH_RETRIES - 1:
                time.sleep(_TX_FETCH_RETRY_DELAY)
        return None

    def _invokes_pump_fun_create(self, tx) -> bool:
        log_messages = ((tx.get("meta") or {}).get("logMessages")) or []
        invoked_pump_fun = any(PUMP_FUN_PROGRAM_ID in line for line in log_messages)
        if not invoked_pump_fun:
            return False
        return any("Instruction: Create" in line for line in log_messages)

    def _extract_new_mint(self, tx):
        meta = tx.get("meta") or {}
        pre = {b["mint"] for b in (meta.get("preTokenBalances") or [])}
        post = {b["mint"] for b in (meta.get("postTokenBalances") or [])}
        new_mints = post - pre
        if len(new_mints) == 1:
            return next(iter(new_mints))
        if len(new_mints) > 1:
            logger.warning("Multiple candidate new mints in tx %s; picking one arbitrarily", tx.get("transaction", {}).get("signatures", ["?"])[0])
            return sorted(new_mints)[0]

        # Fallback: pump.fun's publicly documented 'create' instruction
        # accounts list has the new mint as the first account. Only used
        # if the balance-diff approach above found nothing (e.g. a bare
        # create with no accompanying first buy in the same transaction).
        try:
            instructions = tx["transaction"]["message"]["instructions"]
            for ix in instructions:
                if ix.get("programId") == PUMP_FUN_PROGRAM_ID:
                    accounts = ix.get("accounts") or []
                    if accounts:
                        return accounts[0]
        except Exception:
            pass
        return None

    def _extract_name_symbol(self, tx):
        """Best-effort Borsh parse of the pump.fun create instruction's
        (name, symbol, uri, ...) payload. Any failure here is swallowed —
        the caller falls back to 'unknown' and still sends the alert with
        the data that matters (mint, deployer, signature)."""
        try:
            instructions = tx["transaction"]["message"]["instructions"]
            for ix in instructions:
                if ix.get("programId") != PUMP_FUN_PROGRAM_ID:
                    continue
                data_b58 = ix.get("data")
                if not data_b58:
                    continue
                raw = b58decode(data_b58)
                offset = _ANCHOR_DISCRIMINATOR_LEN
                name, offset = read_borsh_string(raw, offset)
                symbol, offset = read_borsh_string(raw, offset)
                return name, symbol
        except Exception as e:
            logger.debug("Could not parse pump.fun create instruction data: %s", e)
        return None, None

    # ------------------------------------------------------------------ #
    # RPC + alerting
    # ------------------------------------------------------------------ #

    def _rpc(self, method: str, params):
        def _do():
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            resp = self._client.post(self.rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Solana RPC error for {method}: {data['error']}")
            return data["result"]

        return call_with_retry(_do, what=f"solana {method}")

    def _send_launch_alert(self, wallet_address, mint, signature, name, symbol):
        lines = [
            "\U0001F9EA <b>MEMECOIN LAUNCH (Solana / pump.fun)</b>",
            "",
            "Detection: Pump.fun token creation by a watched Solana wallet",
            f"Deployer (watched wallet): <code>{shorten(wallet_address)}</code>",
            f"Full: <code>{wallet_address}</code>",
            "",
        ]
        if mint:
            lines += [
                f"Token mint: <code>{shorten(mint)}</code>",
                f"Full: <code>{mint}</code>",
            ]
        else:
            lines.append("Token mint: <i>could not be determined from this transaction</i>")

        name_display = escape_html(name) if name else "unknown"
        symbol_display = escape_html(symbol) if symbol else "unknown"
        lines.append(f"Name / Symbol: {name_display} / {symbol_display}")

        lines += [
            "",
            f"Tx signature: <code>{signature}</code>",
            f"\U0001F517 Tx: {solscan_tx_url(signature)}",
        ]
        if mint:
            lines.append(f"\U0001F517 Token: {solscan_token_url(mint)}")

        if not self.notifier.send("\n".join(lines)):
            logger.error("Failed to deliver Solana launch alert for tx %s after retries", signature)
