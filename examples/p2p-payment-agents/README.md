# P2P Agent Payment Coordination with py-libp2p

A demo where AI agents negotiate and complete **real payments** peer-to-peer using
[py-libp2p](https://github.com/libp2p/py-libp2p), inspired by Google's
[Agent Payments Protocol (AP2)](https://github.com/google-agentic-commerce/AP2).

Buyers broadcast purchase intents over GossipSub. Competing merchants respond
with cart offers via direct libp2p streams. The buyer selects the best offer,
signs a real EIP-3009 payment authorization, and the merchant settles on-chain --
all without a central server.

## Two Modes

| Mode | What Happens | Setup Needed |
|------|-------------|-------------|
| **Mock Mode** | Simulated signatures, no blockchain | Just `pip install` |
| **Real Mode** | Real EIP-3009 signatures, real USDC transfer on-chain | Hardhat + `setup_local.py` |

## Concepts Demonstrated

- **GossipSub** -- publish/subscribe mesh for intent broadcasting and peer announcements
- **Bootstrap discovery** -- well-known node that maintains a peer registry
- **Direct streams** -- point-to-point libp2p streams for cart offers, payments, and receipts
- **Custom protocol handlers** -- `/ap2-sim/1.0.0` protocol with length-prefix framing
- **Escrow** -- hold/release/refund lifecycle with expiry detection
- **EIP-3009 transferWithAuthorization** -- real on-chain USDC payments (real mode)
- **EIP-712 typed data signing** -- cryptographic payment authorization (local, no network)

## Architecture

| Node | Role |
|------|------|
| **Hardhat Node** | Local blockchain with TestUSDC contract (real mode only) |
| **Bootstrap Node** | Peer discovery, mesh summary broadcasts |
| **Merchant Agent(s)** | Listen for intents, send cart offers, verify signatures, settle on-chain |
| **Buyer Agent** | Publish intent, collect offers, select best, sign EIP-3009, receive receipt |

### Payment Flow

```
Buyer --[intent]--> GossipSub --[heard by]--> Merchants
Merchants --[cart offer + wallet address]--> Buyer (direct stream)
Buyer --[EIP-3009 signed payment]--> Selected Merchant (direct stream)
Merchant --[verify signature (local math)]-->
Merchant --[escrow hold]--> Buyer
Merchant --[settle on-chain (1 RPC call)]--> Blockchain
Merchant --[escrow release]--> Buyer
Merchant --[receipt + tx hash]--> Buyer
```

### What's P2P vs What's Not

| Step | Channel | P2P? |
|------|---------|------|
| Agent discovery | GossipSub | Yes |
| Intent broadcast | GossipSub | Yes |
| Cart offer | libp2p stream | Yes |
| EIP-3009 signing | Local (math only) | Yes |
| Payment message | libp2p stream | Yes |
| Signature verification | Local (math only) | Yes |
| Escrow hold | In-memory | Yes |
| **Settlement on-chain** | **RPC to blockchain** | **No (1 call, unavoidable)** |
| Escrow release | libp2p stream | Yes |
| Receipt with tx hash | libp2p stream | Yes |

**9 out of 10 steps are P2P. Only blockchain settlement requires an external call.**

## Prerequisites

- Python 3.10+
- Node.js 18+ (for Hardhat, real mode only)
- pip

## Setup

### Mock Mode (no blockchain needed)

```bash
python -m venv venv
source venv/Scripts/activate    # Windows (Git Bash)
# OR: venv\Scripts\activate     # Windows (CMD)
# OR: source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### Real Mode (real on-chain payments)

```bash
# 1. Install Python dependencies
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt

# 2. Install Hardhat
npm install

# 3. Start local blockchain (keep running in a separate terminal)
npx hardhat node --network local

# 4. Deploy TestUSDC token and fund wallets
python setup_local.py
```

`setup_local.py` will:
- Deploy a TestUSDC contract with EIP-3009 `transferWithAuthorization`
- Mint 100 USDC to the buyer wallet
- Update `.env` with the contract address and chain ID

## Usage

### Real Mode Demo (6 terminals)

**Terminal 1 -- Hardhat blockchain:**
```bash
npx hardhat node --network local
```

**Terminal 2 -- Deploy and fund (run once):**
```bash
python setup_local.py
```

**Terminal 3 -- Bootstrap node:**
```bash
python bootstrap_node.py --port 9900
```
Copy the multiaddr printed to stdout.

**Terminal 4 -- Merchant A:**
```bash
python merchant_agent.py --port 9901 --name "QuickShoot Studios" --price 3.50 \
    --bootstrap /ip4/127.0.0.1/tcp/9900/p2p/<PEER_ID>
```
Should show: `REAL MODE -- Wallet: 0x3C44...`

**Terminal 5 -- Merchant B:**
```bash
python merchant_agent.py --port 9903 --name "Premium Films" --price 4.50 \
    --bootstrap /ip4/127.0.0.1/tcp/9900/p2p/<PEER_ID>
```

**Terminal 6 -- Buyer (wait 10s after merchants):**
```bash
python buyer_agent.py --port 9902 --budget 4.00 \
    --bootstrap /ip4/127.0.0.1/tcp/9900/p2p/<PEER_ID>
```

**Note:** On Git Bash, use `//ip4` (double slash) to prevent path conversion.

### Expected Output (Real Mode)

**Buyer terminal:**
```
[BUYER] REAL MODE -- Wallet: 0x70997970...
[BUYER] USDC Balance: 100.00
[BUYER] Published intent: "Book a videographer for an event" (budget: $4.00)
[BUYER] Received offer from QuickShoot Studios: $3.50
[BUYER] Selected QuickShoot Studios at $3.50
[BUYER] Signing EIP-3009 authorization (local, no network)...
[BUYER] Sent REAL payment of $3.50 USDC to QuickShoot Studios
[BUYER] Escrow confirmed: $3.50 held for txn_xxx
[BUYER] Service delivered. Escrow released for txn_xxx
[BUYER] Receipt received! Transaction: txn_xxx - $3.50 - COMPLETED
[BUYER] On-chain tx: 0x6c75db6c...
[BUYER] Payment to QuickShoot Studios completed successfully!
```

**Merchant terminal:**
```
[MERCHANT] REAL MODE -- Wallet: 0x3C44CdDd...
[MERCHANT] Intent from buyer... (budget: $4.00)
[MERCHANT] Sent cart offer: $3.50
[MERCHANT] Payment received: $3.50 (real EIP-3009 signature)
[MERCHANT] Signature verified (local math)
[MERCHANT] Escrow HELD
[MERCHANT] Delivering service...
[MERCHANT] Settling on-chain...
[MERCHANT] Settlement SUCCESS: tx 0x6c75db6c...
[MERCHANT] Escrow RELEASED
[MERCHANT] Receipt sent -- COMPLETED
```

### Verify On-Chain Transaction

```bash
python verify_tx.py <TX_HASH>
```

```
=== TRANSACTION VERIFIED ===
  Status:      SUCCESS
  Block:       3
  Gas Used:    86509
  From:        0x3C44... (Merchant -- paid gas)
  To Contract: 0x5FbD... (TestUSDC)
  Tx Hash:     0x9803...
```

### Mock Mode Demo (4 terminals, no blockchain)

**Terminal 1 -- Bootstrap:**
```bash
python bootstrap_node.py --port 8000
```

**Terminal 2 -- Merchant A:**
```bash
python merchant_agent.py --port 8001 --name "QuickShoot Studios" --price 350 \
    --bootstrap /ip4/127.0.0.1/tcp/8000/p2p/<PEER_ID>
```

**Terminal 3 -- Merchant B:**
```bash
python merchant_agent.py --port 8002 --name "Premium Films" --price 450 \
    --bootstrap /ip4/127.0.0.1/tcp/8000/p2p/<PEER_ID>
```

**Terminal 4 -- Buyer:**
```bash
python buyer_agent.py --port 8003 --budget 400 \
    --bootstrap /ip4/127.0.0.1/tcp/8000/p2p/<PEER_ID>
```

### Integration Tests

```bash
python integration_test.py
```

## How Real Payments Work

### EIP-3009: transferWithAuthorization

The buyer signs an EIP-712 typed message saying: *"I authorize X USDC to move from my wallet to the merchant's wallet."* This signature is:

1. **Created locally** -- pure cryptographic math, no network call
2. **Sent over libp2p** -- encrypted P2P stream, not HTTP
3. **Verified locally** by the merchant -- recover signer from signature (math only)
4. **Settled on-chain** by the merchant -- one RPC call to submit the signed authorization to the USDC contract

The USDC contract verifies the signature on-chain and transfers the tokens. The merchant pays gas.

### TestUSDC Contract

A minimal ERC20 with EIP-3009 support (`contracts/TestUSDC.sol`), deployed on local Hardhat. It implements:
- `balanceOf` / `transfer` -- standard ERC20
- `transferWithAuthorization` -- EIP-3009 meta-transaction
- `nonces` -- replay protection
- `mint` -- for funding test wallets

The same code works with Circle's real USDC on Base Sepolia (same interface, same EIP-3009 function) -- just change the RPC URL and contract address in `.env`.

## File Descriptions

| File | Purpose |
|------|---------|
| `protocol.py` | Message types (Pydantic), protocol constants, length-prefix framing |
| `payment.py` | Real + mock payment verifier, escrow manager, service delivery simulation |
| `wallet.py` | EIP-3009 signing (local) and verification (local math, no RPC) |
| `settlement.py` | On-chain settlement -- submits signed authorization to USDC contract |
| `config.py` | Chain configuration, contract addresses, ABIs, helpers |
| `bootstrap_node.py` | Peer discovery node with GossipSub mesh summary broadcasts |
| `merchant_agent.py` | Merchant: listens for intents, sends offers, verifies + settles payments |
| `buyer_agent.py` | Buyer: publishes intents, selects best offer, signs + sends real payment |
| `setup_local.py` | Deploys TestUSDC on Hardhat, mints USDC to buyer, updates .env |
| `verify_tx.py` | Verify a transaction on the local Hardhat blockchain |
| `quick_start.py` | One-command launcher for mock mode demo |
| `integration_test.py` | E2E tests: stream framing, payment flow, rejection, escrow lifecycle |
| `contracts/TestUSDC.sol` | Minimal ERC20 + EIP-3009 Solidity contract |
| `contracts/TestUSDC.json` | Pre-compiled ABI + bytecode (no Solidity compiler needed at runtime) |
| `hardhat.config.js` | Hardhat configuration for local blockchain |
| `.env.example` | Template for wallet keys and RPC URL |

## py-libp2p Features Used

| Feature | How it is used |
|---------|---------------|
| `new_host` | Create libp2p hosts for each agent with unique cryptographic identity |
| GossipSub | Broadcast buyer intents and agent announcements on `ap2-marketplace` topic |
| Pubsub `subscribe` / `publish` | Subscribe to topic, publish intents and announces |
| `set_stream_handler` | Register `/ap2-sim/1.0.0` handler for incoming cart offers and payments |
| `new_stream` | Open direct streams to peers for offers, payments, and rejections |
| Noise encryption | All P2P communication encrypted automatically (zero-config) |
| Mplex muxer | Multiplex multiple streams over a single connection |
| `info_from_p2p_addr` | Parse multiaddr strings into peer info for `host.connect` |
| Length-prefix framing | 4-byte big-endian header for reliable message boundaries |

## AP2 Concept Mapping

| AP2 Concept (HTTP) | Our Implementation (py-libp2p) |
|---------------------|-------------------------------|
| Agent discovery via HTTP AgentCards | GossipSub announce + Bootstrap registry |
| Intent Mandate via HTTP POST | IntentMessage broadcast via GossipSub |
| Cart Mandate via HTTP response | CartMessage via direct libp2p stream |
| Payment Mandate via HTTP + signed JWT | PaymentMessage via stream + real EIP-3009 signature |
| Settlement via payment network | On-chain `transferWithAuthorization` (1 RPC call) |
| Payment Receipt via HTTP | ReceiptMessage via stream (includes real tx hash) |
| Central server for each agent | No servers needed -- all peer-to-peer |

## Inspired By

- [AP2 -- Agent Payments Protocol](https://github.com/google-agentic-commerce/AP2) by Google
- [a2a-x402 -- A2A + crypto payments](https://github.com/google-agentic-commerce/a2a-x402)
- [P2P Federated Learning](https://github.com/seetadev/P2P-Federated-Learning) with py-libp2p
- [Filecoin Agents](https://filecoin.cloud/agents) -- RFS-5: P2P agent communication
- [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009) -- transferWithAuthorization
