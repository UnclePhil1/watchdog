# Watchdog — BSC Wallet Monitoring Bot

Read-only monitoring bot for BNB Smart Chain (BSC). It watches one source
wallet and sends a Telegram alert the moment that wallet sends native BNB
or any BEP-20 token to another address. It then keeps an eye on every
recipient it has ever funded, and sends a second alert if one of those
recipients later deploys what looks like a new token and/or launches it
on PancakeSwap V2.

No private keys, no signing, no trading — alerts only.

## What it watches

**1. Outgoing transfers from the source wallet**
(default `0xa81697d866bFCfbDecd44134c5c02c7032c07777`, override with
`SOURCE_WALLET`):

- Native BNB transfers (`tx.value > 0`, transaction confirmed/successful).
- BEP-20 `Transfer` events where `from` is the source wallet.

Zero-value and failed transactions are ignored. Every recipient is added
to a persistent watchlist together with the tx/block that first funded it.

**2. Token launches by any watchlisted wallet**, checked in this priority
order:

1. The wallet sends a contract-creation transaction (`to == null`) and the
   new contract answers `name()`, `symbol()`, `decimals()`, `totalSupply()`
   like an ERC-20/BEP-20 token. All four succeed → alert as a confirmed
   launch. Only some succeed → still alerted, but labeled **"possible
   token deploy"** rather than silently dropped.
2. The PancakeSwap V2 Factory (`0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73`)
   emits `PairCreated` for a token that a watchlisted wallet deployed
   within the last `LAUNCH_LOOKBACK_BLOCKS` blocks (default 200). This
   catches launches the direct-creation probe missed or under-labeled.
3. A contract with **zero** working ERC-20 view functions is never
   alerted directly (to avoid noise on every random contract a wallet
   happens to deploy), but it is still recorded, so a later PancakeSwap
   pair for it can still trigger signal 2.

Each token is alerted at most once, deduped by contract address.

> **Note on the "Solana" wording in the original spec:** the task
> description said funded wallets "launching a memecoin on Solana" should
> trigger the second alert, but every concrete technical detail it gave
> (the PancakeSwap V2 Factory address, `PairCreated` topic, EVM
> contract-creation semantics, ERC-20 `name/symbol/decimals/totalSupply`
> checks) is BSC/EVM-specific — Solana uses a completely different
> address format and has no PancakeSwap V2 factory at that address. This
> is treated as a wording slip for "launches a memecoin **on BSC**", and
> the bot implements BSC-side launch detection as specified by the
> PancakeSwap V2 factory and ERC-20 checks. If Solana monitoring is
> genuinely needed too, it would require a separate Solana RPC/indexer
> (e.g. tracking Pump.fun / Raydium program logs for a Solana address
> controlled by the same operator) and is out of scope for this BSC-only
> implementation.

## Project layout

```
main.py             entry point — wires everything together, runs the loop
config.py           env var loading and validation
constants.py        contract addresses, event topics, minimal ABIs
chain_utils.py       web3 connection setup + generic RPC retry wrapper
state.py             SQLite persistence (last block, dedupe, watchlist)
watcher.py           core detection logic (transfers + launch detection)
telegram_notify.py   Telegram alert sending + /status command responder
utils.py              formatting helpers (amounts, addresses, HTML escaping)
requirements.txt
.env.example
```

## Setup

### 1. Create a Telegram bot and get your chat id

1. Open Telegram, message **@BotFather**, send `/newbot`, follow the
   prompts. You'll get a token like `123456789:AAExampleTokenHere`.
2. Decide where alerts should go — your own DM with the bot, or a group:
   - **DM:** open a chat with your new bot and send it any message (e.g.
     `/start`) so it's allowed to message you back.
   - **Group:** add the bot to the group.
3. Get the numeric chat id:
   - Easiest: message **@userinfobot** (for your own user id) or add
     **@RawDataBot** to the group briefly and read the `chat.id` field it
     reports, or
   - Send a message to your bot, then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
     read `"chat":{"id": ...}` from the JSON response.
   - Group chat ids are negative numbers (e.g. `-1001234567890`) — keep
     the minus sign.

### 2. Get a BSC RPC endpoint

Any of these work — free tiers are fine for testing, a paid tier is
recommended for production reliability:

- Official: `https://bsc-dataseed.binance.org` (HTTP only, rate-limited)
- [Ankr](https://www.ankr.com/rpc/bsc/), [QuickNode](https://www.quicknode.com/),
  [Blast API](https://blastapi.io/), [NodeReal](https://nodereal.io/) —
  all offer free BSC HTTP + WebSocket endpoints.

### 3. Configure

```bash
cp .env.example .env
# edit .env: fill in BSC_RPC_WSS / BSC_RPC_HTTP, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

`SOURCE_WALLET` already defaults to
`0xa81697d866bFCfbDecd44134c5c02c7032c07777`; only set it if you want to
watch a different address.

### 4. Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

You should see log lines like:

```
2026-09-24 12:00:00 INFO [watchdog.chain] Connected to BSC via WebSocket RPC
2026-09-24 12:00:00 INFO [watchdog.main] Watchdog started. Watching source wallet 0xa816...07777
2026-09-24 12:00:04 INFO [watchdog.watcher] Processing blocks 41234567..41234568 (head=41234571)
```

Send `/status` to the bot in Telegram at any time to get the last
processed block, number of watched recipient wallets, and uptime.

## State and restarts

All state lives in a local SQLite file (`DB_PATH`, default
`watchdog.db`):

- `meta` — last processed block number.
- `alerts` — every alert already sent, keyed by `tx_hash:log_index:type`,
  so the same on-chain event is never alerted twice even across restarts.
- `watchlist` — every recipient wallet the source has ever funded, and
  when.
- `created_contracts` — every contract creation seen from a watchlisted
  wallet (used to correlate later PancakeSwap pairs back to their
  deployer).
- `launched_tokens` — every token already alerted as a launch, keyed by
  token address, so it's never alerted twice even if both detection
  signals fire for it.

On restart the bot reads `last_block` from `meta` and resumes from there
— it does not miss blocks and does not reprocess/duplicate ones it
already handled. On the very first run (no state yet), it starts from
the current chain head by default (no history backfill) unless
`START_BLOCK` is set.

## Configuration reference

See `.env.example` for the full list with defaults. Key ones:

| Variable | Purpose |
|---|---|
| `BSC_RPC_WSS` / `BSC_RPC_HTTP` | RPC endpoint(s). WSS is tried first if set, falls back to HTTP if the WSS connection fails at startup. At least one is required. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Where alerts go. |
| `SOURCE_WALLET` | Wallet to monitor. |
| `POLL_INTERVAL_SECONDS` | How often to check for new blocks (default 4s). |
| `CONFIRMATIONS` | Blocks to stay behind chain head before processing, as reorg protection (default 3). |
| `LAUNCH_LOOKBACK_BLOCKS` | Max age (in blocks) between a wallet's contract deployment and a later PancakeSwap pair for it to still count as the same launch (default 200). |
| `MAX_BLOCK_BATCH` | Caps how many blocks are scanned per loop iteration / `eth_getLogs` call — lower this if your RPC provider rejects large block ranges. |
| `START_BLOCK` | Only used on a fresh DB with no prior state — backfill from this block instead of starting at chain head. |

## Limitations (read before relying on this)

- **Reorgs.** `CONFIRMATIONS` (default 3 blocks) reduces but does not
  eliminate the risk of alerting on a transaction that a shallow reorg
  later drops. BSC reorgs are rare but not impossible; there is no
  automatic "un-alert" if one happens.
- **RPC quality.** Detection is only as good as the RPC provider: missed
  blocks, dropped `eth_getLogs` results, or aggressive rate limiting on
  free tiers can cause a gap. The retry/backoff logic handles transient
  failures but can't invent data an RPC never returned. Use a reputable
  provider for anything you actually care about.
- **What "memecoin launch" can miss:**
  - Tokens deployed via a factory/proxy pattern where the *watchlisted
    wallet* is not the `tx.from` of the raw contract-creation transaction
    (e.g. it calls a deployer contract instead of deploying directly) —
    signal 1 won't catch this. Signal 2 (PancakeSwap pair) still can, but
    only if the token contract itself was ever directly created by the
    watched wallet and the correlation lookback window hasn't expired.
  - Tokens that never get a PancakeSwap **V2** pair (e.g. only listed on
    a V3-style pool or a different DEX) — only V2 `PairCreated` is
    watched, per the spec.
  - Non-standard tokens that don't implement `name/symbol/decimals/
    totalSupply` at all are still recorded but never alerted unless they
    later get a PancakeSwap V2 pair.
  - The optional third signal from the spec ("wallet adds first liquidity
    to a token it deployed") is **not implemented** — it was marked
    optional in the spec and the first two signals already give solid
    coverage; add it by watching `Mint` events on pairs owned by
    watchlisted wallets if needed.
- **Telegram delivery.** Sends retry with exponential backoff (4
  attempts). If Telegram is unreachable for the whole retry window, that
  specific alert is logged as an error and not retried later — the bot
  intentionally does not get stuck reprocessing old blocks waiting for
  Telegram to come back.
- **Single process, single instance.** Running two copies against the
  same `DB_PATH` will race; don't do that. Running two copies against
  different DB files will each alert independently (duplicate alerts).

## Development notes

- Addresses are normalized to lowercase for internal comparisons/storage
  and displayed as EIP-55 checksum addresses in alerts.
- BEP-20 transfers and PancakeSwap pair creations are found via
  `eth_getLogs` with topic filters (efficient — no full-chain scanning);
  native transfers and contract-creation transactions require iterating
  each block's transactions, since there's no log event for a plain
  value transfer or a plain `CREATE`.
- `geth_poa_middleware` is injected because BSC block headers carry
  extra validator data (like Clique PoA chains), which trips web3.py's
  default header validation otherwise.

