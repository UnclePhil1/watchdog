"""Small formatting and conversion helpers."""

from decimal import Decimal

BSCSCAN_TX_URL = "https://bscscan.com/tx/{}"
BSCSCAN_ADDRESS_URL = "https://bscscan.com/address/{}"
SOLSCAN_TX_URL = "https://solscan.io/tx/{}"
SOLSCAN_TOKEN_URL = "https://solscan.io/token/{}"
SOLSCAN_ACCOUNT_URL = "https://solscan.io/account/{}"


def shorten(address: str) -> str:
    if not address:
        return "?"
    return f"{address[:6]}…{address[-4:]}"


def escape_html(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bscscan_tx_url(tx_hash: str) -> str:
    return BSCSCAN_TX_URL.format(tx_hash)


def bscscan_address_url(address: str) -> str:
    return BSCSCAN_ADDRESS_URL.format(address)


def solscan_tx_url(signature: str) -> str:
    return SOLSCAN_TX_URL.format(signature)


def solscan_token_url(mint: str) -> str:
    return SOLSCAN_TOKEN_URL.format(mint)


def solscan_account_url(address: str) -> str:
    return SOLSCAN_ACCOUNT_URL.format(address)


def to_hex_str(value) -> str:
    """Normalize HexBytes/bytes/str into a '0x...' hex string."""
    if isinstance(value, (bytes, bytearray)):
        h = value.hex()
        return h if h.startswith("0x") else "0x" + h
    return value


def format_amount(raw_value: int, decimals) -> str:
    """Convert a raw integer token amount into a trimmed human-readable string."""
    if decimals is None:
        decimals = 18
    value = Decimal(int(raw_value)) / (Decimal(10) ** int(decimals))
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
