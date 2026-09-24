"""SQLite-backed persistence: last processed block, alert dedupe,
watchlist of funded recipient wallets, and detected contract creations /
launched tokens.

A single connection is shared across the main loop thread and the
Telegram /status responder thread, guarded by a lock (SQLite itself is
not safe for concurrent use from multiple threads on one connection).
"""

import datetime
import sqlite3
import threading

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    address TEXT PRIMARY KEY,
    first_funded_tx TEXT NOT NULL,
    first_funded_block INTEGER NOT NULL,
    first_funded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS created_contracts (
    contract_address TEXT PRIMARY KEY,
    deployer TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS launched_tokens (
    token_address TEXT PRIMARY KEY,
    deployer TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    detection_method TEXT NOT NULL,
    alerted_at TEXT NOT NULL
);

-- Solana side: an operator-supplied watchlist (there is no on-chain way
-- to link a BSC recipient wallet to a Solana wallet automatically), a
-- per-wallet signature cursor for incremental polling, and a dedupe
-- table for already-alerted token mints.
CREATE TABLE IF NOT EXISTS solana_watchlist (
    address TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solana_wallet_cursor (
    address TEXT PRIMARY KEY,
    last_signature TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solana_launched_tokens (
    mint_address TEXT PRIMARY KEY,
    deployer TEXT NOT NULL,
    signature TEXT NOT NULL,
    alerted_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ts_to_iso(ts) -> str:
    return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).isoformat()


class State:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA_SQL)

    # -- last processed block ------------------------------------------------

    def get_last_block(self):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key='last_block'").fetchone()
        return int(row[0]) if row else None

    def set_last_block(self, block_number: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('last_block', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(block_number),),
            )

    # -- alert dedupe ---------------------------------------------------------

    def is_alerted(self, alert_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        return row is not None

    def mark_alerted(self, alert_id: str, alert_type: str, tx_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO alerts(alert_id, alert_type, tx_hash, created_at) VALUES (?,?,?,?)",
                (alert_id, alert_type, tx_hash, _now()),
            )

    # -- watchlist --------------------------------------------------------------

    def add_to_watchlist(self, address: str, tx_hash: str, block_number: int, block_timestamp) -> bool:
        """Returns True if this is a newly added wallet."""
        addr = address.lower()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO watchlist(address, first_funded_tx, first_funded_block, first_funded_at) "
                "VALUES (?,?,?,?)",
                (addr, tx_hash, block_number, _ts_to_iso(block_timestamp)),
            )
        return cur.rowcount > 0

    def get_watchlist_set(self):
        with self._lock:
            rows = self._conn.execute("SELECT address FROM watchlist").fetchall()
        return {r[0] for r in rows}

    def get_watchlist_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
        return row[0]

    # -- created contracts (by watched wallets) ----------------------------------

    def record_created_contract(self, contract_address: str, deployer: str, tx_hash: str, block_number: int, block_timestamp) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO created_contracts"
                "(contract_address, deployer, tx_hash, block_number, created_at) VALUES (?,?,?,?,?)",
                (contract_address.lower(), deployer.lower(), tx_hash, block_number, _ts_to_iso(block_timestamp)),
            )

    def get_created_contract(self, contract_address: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT contract_address, deployer, tx_hash, block_number FROM created_contracts WHERE contract_address=?",
                (contract_address.lower(),),
            ).fetchone()
        if not row:
            return None
        return {"contract_address": row[0], "deployer": row[1], "tx_hash": row[2], "block_number": row[3]}

    # -- launched tokens (deduped memecoin-launch alerts) ------------------------

    def is_token_launched(self, token_address: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM launched_tokens WHERE token_address=?", (token_address.lower(),)
            ).fetchone()
        return row is not None

    def mark_token_launched(self, token_address: str, deployer: str, tx_hash: str, detection_method: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO launched_tokens"
                "(token_address, deployer, tx_hash, detection_method, alerted_at) VALUES (?,?,?,?,?)",
                (token_address.lower(), deployer.lower(), tx_hash, detection_method, _now()),
            )

    # -- Solana watchlist (operator-supplied; see module docstring) -------------
    # NOTE: unlike EVM addresses, Solana base58 addresses are case-sensitive,
    # so these are stored and compared verbatim — never lowercased.

    def add_solana_watchlist(self, address: str, source: str = "env") -> bool:
        """Returns True if this is a newly added wallet."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO solana_watchlist(address, added_at, source) VALUES (?,?,?)",
                (address, _now(), source),
            )
        return cur.rowcount > 0

    def remove_solana_watchlist(self, address: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM solana_watchlist WHERE address=?", (address,))
            self._conn.execute("DELETE FROM solana_wallet_cursor WHERE address=?", (address,))
        return cur.rowcount > 0

    def get_solana_watchlist(self):
        with self._lock:
            rows = self._conn.execute("SELECT address FROM solana_watchlist").fetchall()
        return [r[0] for r in rows]

    def get_solana_watchlist_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM solana_watchlist").fetchone()
        return row[0]

    def get_solana_cursor(self, address: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT last_signature FROM solana_wallet_cursor WHERE address=?", (address,)
            ).fetchone()
        return row[0] if row else None

    def set_solana_cursor(self, address: str, signature: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO solana_wallet_cursor(address, last_signature) VALUES (?,?) "
                "ON CONFLICT(address) DO UPDATE SET last_signature=excluded.last_signature",
                (address, signature),
            )

    # -- Solana launched tokens (deduped by mint) --------------------------------

    def is_solana_token_launched(self, mint_address: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM solana_launched_tokens WHERE mint_address=?", (mint_address,)
            ).fetchone()
        return row is not None

    def mark_solana_token_launched(self, mint_address: str, deployer: str, signature: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO solana_launched_tokens"
                "(mint_address, deployer, signature, alerted_at) VALUES (?,?,?,?)",
                (mint_address, deployer, signature, _now()),
            )
