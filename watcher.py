"""Core watch loop: detects outgoing transfers from the source wallet and
subsequent memecoin-launch signals from any funded recipient wallet.
"""

import logging
import time

from web3 import Web3

from chain_utils import call_with_retry
from constants import ERC20_ABI, PAIR_CREATED_TOPIC, PANCAKE_FACTORY_ABI, PANCAKE_V2_FACTORY, TRANSFER_EVENT_TOPIC
from utils import bscscan_address_url, bscscan_tx_url, escape_html, format_amount, shorten, to_hex_str

logger = logging.getLogger("watchdog.watcher")


class Watcher:
    def __init__(self, w3: Web3, cfg, state, notifier):
        self.w3 = w3
        self.cfg = cfg
        self.state = state
        self.notifier = notifier

        self.source_checksum = Web3.to_checksum_address(cfg.source_wallet)
        self.source_lower = self.source_checksum.lower()
        self._source_topic = "0x" + ("0" * 24) + self.source_lower[2:]

        self.factory_contract = w3.eth.contract(
            address=Web3.to_checksum_address(PANCAKE_V2_FACTORY), abi=PANCAKE_FACTORY_ABI
        )
        self._token_meta_cache = {}

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    def run(self):
        last_processed = self.state.get_last_block()
        if last_processed is None:
            latest = call_with_retry(lambda: self.w3.eth.block_number, what="get block_number (init)")
            # Conservative default: don't backfill chain history on first
            # run unless the operator explicitly set START_BLOCK.
            last_processed = (self.cfg.start_block - 1) if self.cfg.start_block else (latest - 1)
            self.state.set_last_block(last_processed)
            logger.info("No prior state found; starting from block %s", last_processed + 1)
        else:
            logger.info("Resuming from last processed block %s", last_processed)

        while True:
            try:
                latest = call_with_retry(lambda: self.w3.eth.block_number, what="get block_number")
                safe_latest = latest - self.cfg.confirmations

                while safe_latest > last_processed:
                    from_block = last_processed + 1
                    to_block = min(safe_latest, from_block + self.cfg.max_block_batch - 1)
                    logger.info("Processing blocks %s..%s (head=%s)", from_block, to_block, latest)

                    self.handle_pair_created(from_block, to_block)
                    for block_number in range(from_block, to_block + 1):
                        self.process_block(block_number)

                    last_processed = to_block
                    self.state.set_last_block(last_processed)
            except Exception:
                logger.exception("Error in main watch loop; will retry next cycle")

            time.sleep(self.cfg.poll_interval_seconds)

    # ------------------------------------------------------------------ #
    # Per-block scan: native BNB transfers + BEP-20 transfers + contract
    # creation txs
    # ------------------------------------------------------------------ #

    def process_block(self, block_number: int):
        block = call_with_retry(
            self.w3.eth.get_block, block_number, full_transactions=True, what=f"get_block {block_number}"
        )
        watchlist = self.state.get_watchlist_set()
        for tx in block.transactions:
            tx_from = tx["from"]
            tx_to = tx["to"]

            if tx_from and tx_from.lower() == self.source_lower:
                self._handle_source_tx(tx, block)

            if tx_to is None and tx_from and tx_from.lower() in watchlist:
                self._handle_contract_creation(tx, block)

    def _handle_source_tx(self, tx, block):
        """Any transaction sent BY the source wallet: check it for both a
        native value transfer and any BEP-20 Transfer events it emitted.

        This inspects the transaction's own receipt logs rather than
        calling eth_getLogs across the whole block range with no address
        filter — some RPC providers' free/shared tiers (e.g. QuickNode's
        discover plan) reject address-less eth_getLogs outright, and this
        approach works everywhere since it only needs a receipt for a
        transaction we already know the hash of. The trade-off: it only
        catches transfers where the source wallet itself sent the
        transaction, not a third party pulling funds via a pre-approved
        transferFrom() — see the README limitations section.
        """
        tx_hash = to_hex_str(tx["hash"])
        receipt = call_with_retry(self.w3.eth.get_transaction_receipt, tx["hash"], what="get_receipt source tx")
        if receipt.status != 1:
            return

        if tx["to"] is not None and tx["value"] > 0:
            self._alert_native_transfer(tx, block, tx_hash)

        for log in receipt.logs:
            self._maybe_alert_bep20_transfer(log, block, tx_hash)

    def _alert_native_transfer(self, tx, block, tx_hash):
        alert_id = f"{tx_hash}:-1:transfer_native"
        if self.state.is_alerted(alert_id):
            return

        recipient = tx["to"]
        amount = format_amount(tx["value"], 18)
        self._add_to_watchlist(recipient, tx_hash, block.number, block.timestamp)

        text = self._format_transfer_alert(recipient, amount, "BNB", tx_hash)
        if not self.notifier.send(text):
            logger.error("Failed to deliver native transfer alert for tx %s after retries", tx_hash)
        self.state.mark_alerted(alert_id, "transfer_native", tx_hash)

    def _maybe_alert_bep20_transfer(self, log, block, tx_hash):
        topics = log.get("topics") or []
        if len(topics) < 3 or to_hex_str(topics[0]).lower() != TRANSFER_EVENT_TOPIC.lower():
            return
        if to_hex_str(topics[1]).lower() != self._source_topic.lower():
            return  # a Transfer log in this tx, but not one where `from` is the source wallet

        log_index = log["logIndex"]
        alert_id = f"{tx_hash}:{log_index}:transfer_bep20"
        if self.state.is_alerted(alert_id):
            return

        token_address = Web3.to_checksum_address(log["address"])
        try:
            contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
            event = contract.events.Transfer().process_log(log)
        except Exception as e:
            logger.warning("Could not decode Transfer log in tx %s (token %s): %s", tx_hash, token_address, e)
            return

        to_addr = event["args"]["to"]
        raw_value = event["args"]["value"]
        if raw_value == 0:
            return

        token_meta = self._get_token_meta_cached(token_address)
        amount = format_amount(raw_value, token_meta["decimals"])
        self._add_to_watchlist(to_addr, tx_hash, block.number, block.timestamp)

        text = self._format_transfer_alert(to_addr, amount, token_meta["symbol"], tx_hash, token_address=token_address)
        if not self.notifier.send(text):
            logger.error("Failed to deliver BEP-20 transfer alert for tx %s after retries", tx_hash)
        self.state.mark_alerted(alert_id, "transfer_bep20", tx_hash)

    def _handle_contract_creation(self, tx, block):
        tx_hash = to_hex_str(tx["hash"])
        receipt = call_with_retry(self.w3.eth.get_transaction_receipt, tx["hash"], what="get_receipt creation")
        if receipt.status != 1 or not receipt.contractAddress:
            return

        contract_address = receipt.contractAddress
        deployer = tx["from"]
        self.state.record_created_contract(contract_address, deployer, tx_hash, block.number, block.timestamp)

        if self.state.is_token_launched(contract_address):
            return

        token_info = self.probe_token(contract_address)
        if token_info is None:
            logger.info(
                "Watched wallet %s deployed a non-token-like contract %s (tx %s); not alerting",
                deployer, contract_address, tx_hash,
            )
            return

        self._send_launch_alert(contract_address, deployer, tx_hash, token_info, method="contract_creation")
        self.state.mark_token_launched(contract_address, deployer, tx_hash, "contract_creation")

    # ------------------------------------------------------------------ #
    # Log-based scan: PancakeSwap pair creations (address-filtered, so
    # this one is unaffected by free-tier eth_getLogs restrictions)
    # ------------------------------------------------------------------ #

    def handle_pair_created(self, from_block: int, to_block: int):
        logs = call_with_retry(
            self.w3.eth.get_logs,
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": Web3.to_checksum_address(PANCAKE_V2_FACTORY),
                "topics": [PAIR_CREATED_TOPIC],
            },
            what="get_logs PairCreated",
        )
        for log in logs:
            try:
                event = self.factory_contract.events.PairCreated().process_log(log)
            except Exception as e:
                logger.warning("Could not decode PairCreated log in tx %s: %s", to_hex_str(log.get("transactionHash")), e)
                continue

            token0 = event["args"]["token0"]
            token1 = event["args"]["token1"]
            pair = event["args"]["pair"]
            pair_block = log["blockNumber"]
            pair_tx_hash = to_hex_str(log["transactionHash"])

            for token_addr in (token0, token1):
                self._check_pair_token_for_launch(token_addr, pair, pair_block, pair_tx_hash)

    def _check_pair_token_for_launch(self, token_addr, pair_addr, pair_block, pair_tx_hash):
        if self.state.is_token_launched(token_addr):
            return

        created = self.state.get_created_contract(token_addr)
        if created is None:
            return  # not a contract we saw a watched wallet deploy

        watchlist = self.state.get_watchlist_set()
        if created["deployer"] not in watchlist:
            return

        age = pair_block - created["block_number"]
        if age < 0 or age > self.cfg.launch_lookback_blocks:
            return

        token_info = self.probe_token(token_addr) or {
            "name": None, "symbol": None, "decimals": None, "total_supply": None, "confidence": "possible",
        }
        self._send_launch_alert(
            token_addr, created["deployer"], created["tx_hash"], token_info,
            method="pancake_pair", pair_address=pair_addr,
        )
        self.state.mark_token_launched(token_addr, created["deployer"], created["tx_hash"], "pancake_pair")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def probe_token(self, address):
        """Try to read the four standard ERC-20 view functions. Returns
        None if the contract looks nothing like a token at all."""
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
        result = {}
        ok_count = 0
        for field, fn_name in (
            ("name", "name"), ("symbol", "symbol"), ("decimals", "decimals"), ("total_supply", "totalSupply"),
        ):
            try:
                result[field] = getattr(contract.functions, fn_name)().call()
                ok_count += 1
            except Exception:
                result[field] = None
        if ok_count == 0:
            return None
        result["confidence"] = "confirmed" if ok_count == 4 else "possible"
        return result

    def _get_token_meta_cached(self, token_address):
        key = token_address.lower()
        if key in self._token_meta_cache:
            return self._token_meta_cache[key]
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        try:
            symbol = contract.functions.symbol().call()
        except Exception:
            symbol = "TOKEN"
        try:
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18
        meta = {"symbol": symbol, "decimals": decimals}
        self._token_meta_cache[key] = meta
        return meta

    def _add_to_watchlist(self, address, tx_hash, block_number, timestamp):
        if self.state.add_to_watchlist(address, tx_hash, block_number, timestamp):
            logger.info("New recipient wallet watchlisted: %s (funded by tx %s)", address, tx_hash)

    # ------------------------------------------------------------------ #
    # Message formatting
    # ------------------------------------------------------------------ #

    def _format_transfer_alert(self, recipient, amount, symbol, tx_hash, token_address=None):
        recipient_cs = Web3.to_checksum_address(recipient)
        lines = [
            "\U0001F6A8 <b>OUTGOING TRANSFER</b>",
            "",
            f"From (source): <code>{shorten(self.source_checksum)}</code>",
            f"Full: <code>{self.source_checksum}</code>",
            "",
            f"To: <code>{shorten(recipient_cs)}</code>",
            f"Full: <code>{recipient_cs}</code>",
            "",
            f"Amount: <b>{amount} {escape_html(symbol)}</b>",
        ]
        if token_address:
            lines.append(f"Token contract: <code>{token_address}</code>")
        lines += [
            "",
            f"Tx: <code>{tx_hash}</code>",
            f"\U0001F517 Tx: {bscscan_tx_url(tx_hash)}",
            f"\U0001F517 Recipient: {bscscan_address_url(recipient_cs)}",
        ]
        return "\n".join(lines)

    def _send_launch_alert(self, token_address, deployer, tx_hash, token_info, method, pair_address=None):
        confidence = token_info.get("confidence", "possible")
        label = "MEMECOIN LAUNCH" if confidence == "confirmed" else "MEMECOIN LAUNCH (possible token deploy)"
        method_label = {
            "contract_creation": "New contract deployed by a watched wallet, looks like an ERC-20/BEP-20 token",
            "pancake_pair": "PancakeSwap V2 pair created for a token deployed by a watched wallet",
        }[method]

        name = token_info.get("name") or "unknown"
        symbol = token_info.get("symbol") or "unknown"
        decimals = token_info.get("decimals")
        total_supply = token_info.get("total_supply")

        lines = [
            f"\U0001F9EA <b>{label}</b>",
            "",
            f"Detection: {method_label}",
            f"Deployer (watched wallet): <code>{shorten(deployer)}</code>",
            f"Full: <code>{deployer}</code>",
            "",
            f"Token contract: <code>{shorten(token_address)}</code>",
            f"Full: <code>{token_address}</code>",
            f"Name / Symbol: {escape_html(name)} / {escape_html(symbol)}",
        ]
        if decimals is not None and total_supply is not None:
            try:
                lines.append(f"Total supply: {format_amount(total_supply, decimals)}")
            except Exception:
                pass
        lines += [
            "",
            f"Deploy tx: <code>{tx_hash}</code>",
            f"\U0001F517 Deploy tx: {bscscan_tx_url(tx_hash)}",
            f"\U0001F517 Token: {bscscan_address_url(token_address)}",
        ]
        if pair_address:
            lines.append(f"\U0001F517 PancakeSwap pair: {bscscan_address_url(pair_address)}")

        if not self.notifier.send("\n".join(lines)):
            logger.error("Failed to deliver launch alert for token %s after retries", token_address)
