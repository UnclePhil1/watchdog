"""Minimal, dependency-free Solana helpers: base58 decode, address sanity
check, and a tiny Borsh string reader used to best-effort parse pump.fun's
'create' instruction data.

We deliberately avoid pulling in solana-py/solders/base58 packages here —
this is the only place we need base58, and a ~15-line decoder keeps the
dependency footprint small per the project's "simple, not a framework"
goal.
"""

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_ALPHABET_SET = set(_B58_ALPHABET)
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(s: str) -> bytes:
    if not s:
        return b""
    num = 0
    for char in s:
        num = num * 58 + _B58_INDEX[char]
    num_bytes = (num.bit_length() + 7) // 8
    body = num.to_bytes(num_bytes, "big") if num_bytes else b""
    n_leading_ones = len(s) - len(s.lstrip("1"))
    return b"\x00" * n_leading_ones + body


def is_probably_solana_address(address: str) -> bool:
    """Loose sanity check (length + base58 charset) — not a full ed25519
    pubkey validation, just enough to catch typos and wrong-chain
    addresses (e.g. an 0x-prefixed EVM address)."""
    if not address or not (32 <= len(address) <= 44):
        return False
    return all(c in _B58_ALPHABET_SET for c in address)


def read_borsh_string(raw: bytes, offset: int):
    """Read a Borsh-encoded string (u32 LE length prefix + utf8 bytes)."""
    if offset + 4 > len(raw):
        raise ValueError("truncated Borsh string length")
    length = int.from_bytes(raw[offset:offset + 4], "little")
    offset += 4
    if offset + length > len(raw):
        raise ValueError("truncated Borsh string body")
    value = raw[offset:offset + length].decode("utf-8", errors="replace")
    offset += length
    return value, offset
