# Watchdog — BSC Wallet Monitoring Bot

Read-only monitoring bot for BNB Smart Chain (BSC), with optional Solana
add-on monitoring. It watches one source wallet and sends a Telegram
alert the moment that wallet sends native BNB or any BEP-20 token to
another address. It then keeps an eye on every recipient it has ever
funded, and sends a second alert if one of those recipients later
deploys what looks like a new token and/or launches it on PancakeSwap V2.

Separately, it can also watch an operator-supplied list of **Solana**
wallets and alert when one of them launches a token on **pump.fun** — see
[Solana (pump.fun) monitoring](#solana-pumpfun-monitoring-optional) below
for why that list has to be supplied manually rather than discovered
automatically.

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
> description said funded BSC recipient wallets "launching a memecoin on
> Solana" should trigger the second alert, but every concrete technical
> detail it gave (the PancakeSwap V2 Factory address, `PairCreated`
> topic, EVM contract-creation semantics, ERC-20
> `name/symbol/decimals/totalSupply` checks) is BSC/EVM-specific. That
> BSC-side detection is what's described above and is what actually
> watches the funded recipients automatically. Solana monitoring is
> implemented too (below), but as a **separate, manually-linked**
> watchlist — see why in the next section.

## Solana (pump.fun) monitoring (optional)

A BSC address and a Solana address live in entirely different key
spaces — there is no cryptographic or on-chain link that lets the bot
automatically figure out "this BSC recipient also controls this Solana
wallet." So unlike the BSC watchlist (which fills itself in from
observed transfers), the Solana watchlist has to be told which wallets
to watch, by you, once you've identified them through your own means
(e.g. the recipient publicly links both wallets, a CEX withdrawal trail,
OSINT, etc.).

Once a Solana wallet is on that watchlist, the bot watches it for
**pump.fun token creation** — pump.fun is the dominant Solana memecoin
launchpad, so a wallet calling its `create` instruction is a strong,
low-noise "this wallet just launched a memecoin" signal (the same role
`PairCreated` plays on the BSC side).

**Enabling it:**

1. Set `SOLANA_RPC_HTTP` in `.env` (e.g.
   `https://api.mainnet-beta.solana.com`, or a provider like Helius/
   QuickNode/Ankr for production use). Leaving it unset disables Solana
   monitoring entirely — the bot runs BSC-only, exactly as before.
2. Seed the watchlist via `SOLANA_WATCHLIST` (comma-separated addresses)
   in `.env`, and/or add wallets at runtime from Telegram:
   - `/watch_sol <address>` — add a Solana wallet to the watchlist
   - `/unwatch_sol <address>` — remove one
   - `/status` also reports how many Solana wallets are being watched
3. When a watched wallet's transaction both invokes the pump.fun program
   and logs `Instruction: Create`, the bot sends a
   **MEMECOIN LAUNCH (Solana / pump.fun)** alert with the new token
   mint, the deployer, the tx signature, name/symbol (best-effort — see
   below), and Solscan links. Deduped by mint address, same as the BSC
   side is deduped by token address.

**How detection works, precisely** (`solana_watcher.py`):

- Each watched wallet is polled independently via `getSignaturesForAddress`
  (paginated backward to avoid missing transactions if a burst happens
  between polls), tracking a per-wallet last-seen-signature cursor in
  SQLite so restarts resume cleanly, exactly like the BSC block cursor.
  A newly added wallet starts from its current latest signature — no
  history backfill, matching the BSC watcher's default behavior.
- A transaction counts as a pump.fun launch if its log messages show
  both the pump.fun program being invoked *and* an `Instruction: Create`
  line (Anchor's standard per-instruction log).
- The **new token mint** is found by diffing `preTokenBalances` vs
  `postTokenBalances` (robust to pump.fun changing its instruction's
  account ordering), falling back to the first account of the pump.fun
  instruction if that diff finds nothing.
- **Name/symbol** are a best-effort Borsh decode of the create
  instruction's raw data (skip the 8-byte Anchor discriminator, read two
  length-prefixed UTF-8 strings). This is inherently fragile — if
  pump.fun changes its instruction layout, parsing may fail. It's wrapped
  so a parse failure **never blocks the alert**: you still get the mint
  address, deployer, signature, and links, just with name/symbol shown
  as "unknown" instead of guessed wrong.

## Project layout

```
main.py             entry point — wires everything together, runs the loop(s)
config.py           env var loading and validation
constants.py        contract/program addresses, event topics, minimal ABIs
chain_utils.py       web3 connection setup + generic RPC retry wrapper
state.py             SQLite persistence (BSC + Solana state, dedupe, watchlists)
watcher.py           BSC detection logic (transfers + launch detection)
solana_watcher.py     Solana detection logic (pump.fun launches)
solana_utils.py       base58 decode, address sanity check, Borsh string reader
telegram_notify.py   Telegram alert sending + slash-command responder
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

`SOLANA_RPC_HTTP` / `SOLANA_WATCHLIST` are optional — leave them unset for
BSC-only monitoring. See
[Solana (pump.fun) monitoring](#solana-pumpfun-monitoring-optional) to
enable the add-on.

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
processed block, watchlist sizes, and uptime. Send `/help` for the full
command list.

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
- `solana_watchlist` — Solana wallets to watch for pump.fun launches
  (operator-supplied; see above), and whether each was added via
  `SOLANA_WATCHLIST` or `/watch_sol`.
- `solana_wallet_cursor` — last-seen transaction signature per watched
  Solana wallet, for incremental polling.
- `solana_launched_tokens` — every Solana token mint already alerted,
  deduped the same way as `launched_tokens`.

On restart the bot reads `last_block` from `meta` and resumes from there
— it does not miss blocks and does not reprocess/duplicate ones it
already handled. On the very first run (no state yet), it starts from
the current chain head by default (no history backfill) unless
`START_BLOCK` is set. Each Solana wallet behaves the same way
independently via its own cursor.

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
| `SOLANA_RPC_HTTP` | Enables Solana/pump.fun monitoring when set; leave unset to disable it entirely. |
| `SOLANA_WATCHLIST` | Comma-separated Solana wallets to watch from startup (more can be added later via `/watch_sol`). |
| `SOLANA_POLL_INTERVAL_SECONDS` | How often each watched Solana wallet is polled (default 10s). |

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
- **Solana wallet linkage is manual, not automatic.** The bot cannot
  discover on its own that a funded BSC recipient also controls a given
  Solana wallet — see
  [Solana (pump.fun) monitoring](#solana-pumpfun-monitoring-optional).
  If you never add a Solana wallet to the watchlist, it is never
  checked, no matter what the corresponding BSC wallet does.
- **What Solana "memecoin launch" can miss:**
  - Only **pump.fun** creation is detected. A token minted directly via
    the SPL Token program and listed straight on Raydium/Orca without
    ever touching pump.fun will not trigger an alert.
  - Name/symbol extraction is a best-effort Borsh parse of pump.fun's
    current instruction layout; if pump.fun changes it, name/symbol may
    come back as "unknown" (the alert itself — mint, deployer, tx,
    links — still fires correctly, since that part depends only on the
    pump.fun program ID and log lines, not the byte layout).
  - Each wallet is polled independently with a safety cap of ~500
    signatures per poll cycle; an extremely high-throughput wallet that
    exceeds that between polls could have older transactions skipped in
    that cycle (rare in practice for a monitored recipient wallet).

## Development notes

- BSC addresses are normalized to lowercase for internal
  comparisons/storage and displayed as EIP-55 checksum addresses in
  alerts. Solana addresses are **case-sensitive** base58 and are always
  stored/compared verbatim — never lowercased.
- BEP-20 transfers and PancakeSwap pair creations are found via
  `eth_getLogs` with topic filters (efficient — no full-chain scanning);
  native transfers and contract-creation transactions require iterating
  each block's transactions, since there's no log event for a plain
  value transfer or a plain `CREATE`.
- `geth_poa_middleware` is injected because BSC block headers carry
  extra validator data (like Clique PoA chains), which trips web3.py's
  default header validation otherwise.
- The Solana watcher runs in its own background thread on its own poll
  interval, independent of the BSC block loop, since Solana's
  transaction model (signature-based, per-account history) doesn't map
  onto BSC's block-number polling. It talks to the Solana RPC directly
  over JSON-RPC via `httpx` rather than pulling in `solana-py`/`solders`,
  to keep the dependency footprint small — the only decoding needed
  (base58, a couple of Borsh strings) is ~50 lines in `solana_utils.py`,
  cross-checked against the reference `base58` package during
  development.

