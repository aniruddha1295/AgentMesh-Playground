# Architecture — P2P Agent Payment Coordination with py-libp2p

## Overview

This example demonstrates AI agents negotiating and completing payments over a
peer-to-peer network using py-libp2p. It simulates Google's AP2 (Agent Payments
Protocol) concepts — but replaces centralized HTTP with decentralized P2P networking.

```
 AP2 Today (centralized)              Our Demo (decentralized)
 ========================              ========================

 Agent A                               Agent A
   |                                     |
   | HTTP POST                           | libp2p stream
   v                                     v
 [Central Server]                      Agent B  (direct connection)
   |                                     |
   | HTTP POST                           | GossipSub broadcast
   v                                     v
 Agent B                               Agent C  (mesh network)
```

---

## Network Topology

```
                    +-------------------+
                    |  Bootstrap Node   |
                    |    (port 8000)    |
                    |  Peer Discovery   |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
     +--------+----+  +-----+-------+  +---+----------+
     | Merchant A  |  | Merchant B  |  |   Buyer      |
     | QuickShoot  |  | Premium     |  |   Agent      |
     | $350        |  | Films $450  |  |   Budget $400|
     | (port 8001) |  | (port 8002) |  |  (port 8003) |
     +-------------+  +-------------+  +--------------+

     All peers connect to Bootstrap first, then discover
     each other via GossipSub mesh formation.
```

---

## py-libp2p Components Used

```
+------------------------------------------------------------------+
|                        py-libp2p Stack                            |
|                                                                  |
|  +------------------+  +------------------+  +-----------------+ |
|  |    GossipSub     |  |   Direct Streams |  |   Bootstrap     | |
|  |                  |  |                  |  |                 | |
|  | - Broadcast      |  | - Cart offers    |  | - Peer registry | |
|  |   intents        |  | - Payments       |  | - Mesh summary  | |
|  | - Announce       |  | - Escrow msgs    |  | - Discovery     | |
|  |   presence       |  | - Receipts       |  |                 | |
|  +------------------+  +------------------+  +-----------------+ |
|                                                                  |
|  +------------------+  +------------------+  +-----------------+ |
|  |     Mplex        |  |     Noise        |  |   new_host()    | |
|  |  (multiplexer)   |  |  (encryption)    |  |  (peer identity)| |
|  +------------------+  +------------------+  +-----------------+ |
+------------------------------------------------------------------+
```

---

## Message Types (AP2 Simulation)

```
+------------------+     +------------------+     +------------------+
|  IntentMessage   |     |   CartMessage    |     | PaymentMessage   |
|  (AP2: Intent    |     |  (AP2: Cart      |     | (AP2: Payment    |
|   Mandate)       |     |   Mandate)       |     |  Mandate)        |
|                  |     |                  |     |                  |
| - description    |     | - merchant_name  |     | - cart_hash      |
| - max_budget     |     | - items[]        |     | - amount         |
| - currency       |     | - total          |     | - authorization  |
| - multiaddr      |     | - cart_expiry    |     | - currency       |
+------------------+     +------------------+     +------------------+

+------------------+     +------------------+     +------------------+
| EscrowHoldMsg    |     | EscrowReleaseMsg |     | ReceiptMessage   |
|                  |     |                  |     | (AP2: Payment    |
| - payment_id     |     | - payment_id     |     |  Receipt)        |
| - amount         |     | - amount         |     |                  |
| - hold_expiry    |     | - status:        |     | - payment_id     |
| - status: HELD   |     |   RELEASED       |     | - amount         |
| - release_cond   |     |                  |     | - status:        |
+------------------+     +------------------+     |   COMPLETED      |
                                                  | - merchant_name  |
+------------------+     +------------------+     +------------------+
| RejectMessage    |     | PaymentFailedMsg |
|                  |     |                  |
| - reason         |     | - reason         |
+------------------+     +------------------+
```

---

## Complete Payment Flow

```
  Buyer                    GossipSub               Merchant A           Merchant B
    |                       (topic)                (QuickShoot)        (Premium Films)
    |                         |                       |                     |
    |  1. ANNOUNCE            |                       |                     |
    |------------------------>|                       |                     |
    |                         |                       |                     |
    |  2. INTENT              |                       |                     |
    |  "Book videographer"    |                       |                     |
    |  budget: $400           |                       |                     |
    |------------------------>|                       |                     |
    |                         |  Intent heard         |                     |
    |                         |---------------------->|                     |
    |                         |---------------------->|-------------------->|
    |                         |                       |                     |
    |                         |           3. Check:   |        Check:       |
    |                         |           $350 <= $400|        $450 > $400  |
    |                         |           = SEND OFFER|        = SKIP       |
    |                         |                       |                     |
    |  4. CART (direct stream) |                      |                     |
    |<------------------------------------------------|                     |
    |  "Event Videography"    |                       |                     |
    |  total: $350            |                       |                     |
    |                         |                       |                     |
    |  5. SELECT best offer   |                       |                     |
    |  (cheapest in budget)   |                       |                     |
    |                         |                       |                     |
    |  6. PAYMENT (direct stream)                     |                     |
    |------------------------------------------------>|                     |
    |  amount: $350           |                       |                     |
    |  auth: sig_xxxxx        |                       |                     |
    |                         |                       |                     |
    |                         |           7. VERIFY   |                     |
    |                         |              payment  |                     |
    |                         |                       |                     |
    |  8. ESCROW_HOLD         |                       |                     |
    |<------------------------------------------------|                     |
    |  $350 held              |                       |                     |
    |  txn_xxxxx              |                       |                     |
    |                         |                       |                     |
    |                         |           9. SERVICE  |                     |
    |                         |              DELIVERY |                     |
    |                         |              (2s sim) |                     |
    |                         |                       |                     |
    |  10. ESCROW_RELEASE     |                       |                     |
    |<------------------------------------------------|                     |
    |  funds released         |                       |                     |
    |                         |                       |                     |
    |  11. RECEIPT            |                       |                     |
    |<------------------------------------------------|                     |
    |  txn_xxxxx              |                       |                     |
    |  $350 - COMPLETED       |                       |                     |
    |                         |                       |                     |
```

---

## Communication Channels

```
+------------------------------------------------------------------+
|                     Communication Map                            |
|                                                                  |
|  GossipSub (broadcast, topic: "ap2-marketplace")                 |
|  ============================================                    |
|  Used for:                                                       |
|    - Agent announcements (role, name, multiaddr)                 |
|    - Buyer intent broadcasting                                   |
|    - Mesh summary flooding (bootstrap)                           |
|                                                                  |
|  Direction: One-to-many (all subscribers see it)                 |
|                                                                  |
|  Direct Streams (protocol: "/ap2-sim/1.0.0")                    |
|  ============================================                    |
|  Used for:                                                       |
|    - Merchant -> Buyer: Cart offer                               |
|    - Buyer -> Merchant: Payment authorization                    |
|    - Merchant -> Buyer: Escrow hold / release / receipt          |
|    - Buyer -> Merchant: Rejection                                |
|                                                                  |
|  Direction: One-to-one (private, encrypted via Noise)            |
+------------------------------------------------------------------+
```

---

## Message Framing (Wire Protocol)

```
  Length-prefix framing over libp2p streams:

  +--------+------------------------------------------+
  | Header |              Payload                     |
  | 4 bytes|              N bytes                     |
  +--------+------------------------------------------+
  | uint32 |          JSON (UTF-8)                    |
  | (big   |                                          |
  | endian)|  {"type":"payment","sender":"12D3..."    |
  |        |   "amount":350.0,"cart_hash":"abc..."}   |
  +--------+------------------------------------------+

  This allows multiple messages on a single stream:

  [len1][msg1][len2][msg2][len3][msg3]...

  Example: Merchant sends 3 messages on one stream:
  [EscrowHold] -> [EscrowRelease] -> [Receipt]
```

---

## Escrow Lifecycle

```
                     +------------------+
                     |    PAYMENT       |
                     |    RECEIVED      |
                     +--------+---------+
                              |
                        Verify payment
                              |
                    +---------+---------+
                    |                   |
                    v                   v
           +-------+-------+   +-------+-------+
           |  ESCROW HELD  |   | PAYMENT FAILED |
           |  (funds held) |   | (invalid auth) |
           +-------+-------+   +---------------+
                   |
          Service delivered?
                   |
          +--------+--------+
          |                 |
          v                 v
  +-------+-------+  +-----+---------+
  |ESCROW RELEASED|  |ESCROW REFUNDED|
  |(funds to      |  |(funds back to |
  | merchant)     |  | buyer - timeout|
  +-------+-------+  +---------------+
          |
          v
  +-------+-------+
  |    RECEIPT     |
  |   COMPLETED   |
  +---------------+
```

---

## File Structure

```
ap2_payment_agents/
|
|-- protocol.py            Shared foundation
|   |-- Message types      (Intent, Cart, Payment, Escrow, Receipt, Reject)
|   |-- Enums              (MessageType, AgentRole, PaymentStatus)
|   |-- Framing            (length-prefix encode/decode)
|   |-- Serialization      (JSON serialize/deserialize)
|   |-- Utilities          (select_best_offer, generate_payment_id)
|
|-- payment.py             Business logic
|   |-- MockPaymentVerifier  (simulated signature check)
|   |-- EscrowManager        (hold / release / refund / check_expired)
|   |-- simulate_service_delivery (async delay)
|
|-- bootstrap_node.py      Network infrastructure
|   |-- Peer registry      (track connected agents + roles)
|   |-- Mesh flooding      (broadcast peer list periodically)
|   |-- GossipSub listener (process announcements)
|
|-- merchant_agent.py      Service provider
|   |-- Listen for intents (GossipSub)
|   |-- Send cart offers   (direct stream to buyer)
|   |-- Handle payments    (verify -> escrow -> deliver -> receipt)
|   |-- Handle rejections  (graceful logging)
|
|-- buyer_agent.py         Client
|   |-- Publish intent     (GossipSub broadcast)
|   |-- Collect offers     (stream handler for incoming carts)
|   |-- Select best offer  (cheapest within budget)
|   |-- Send payment       (direct stream to merchant)
|   |-- Receive receipt    (escrow hold -> release -> receipt)
|
|-- integration_test.py    Verification (5 tests)
|-- quick_start.py         Cross-platform launcher
|-- requirements.txt       Frozen dependencies
|-- README.md              Setup and usage guide
```

---

## How AP2 Concepts Map to py-libp2p

```
+------------------------+------------------------+
|     AP2 (HTTP)         |   Our Demo (py-libp2p) |
+========================+========================+
| Agent discovery via    | GossipSub announce +   |
| HTTP AgentCards at     | Bootstrap peer registry|
| known URLs             |                        |
+------------------------+------------------------+
| Intent Mandate sent    | IntentMessage broadcast|
| via HTTP POST to       | via GossipSub to ALL   |
| specific merchant URL  | subscribed merchants   |
+------------------------+------------------------+
| Cart Mandate returned  | CartMessage sent via   |
| as HTTP response       | direct libp2p stream   |
+------------------------+------------------------+
| Payment Mandate sent   | PaymentMessage sent    |
| via HTTP with signed   | via direct stream with |
| JWT credentials        | simulated signature    |
+------------------------+------------------------+
| Settlement via payment | Mock verification +    |
| network (Visa, USDC)   | EscrowManager          |
+------------------------+------------------------+
| PaymentReceipt as      | ReceiptMessage via     |
| HTTP response          | direct stream           |
+------------------------+------------------------+
| Central server needed  | NO server needed       |
| for each agent         | All peer-to-peer       |
+------------------------+------------------------+
```

---

## Key Takeaways

1. **GossipSub replaces HTTP broadcast** -- buyers publish intents to ALL merchants at once, not one-by-one HTTP calls
2. **Direct streams replace HTTP request/response** -- payment negotiation happens on encrypted P2P streams
3. **Bootstrap replaces DNS/URLs** -- agents discover each other via a known peer, not domain names
4. **Noise encryption is automatic** -- all communication is encrypted without configuring TLS certificates
5. **No single point of failure** -- if bootstrap goes down after mesh forms, agents can still communicate
6. **Escrow pattern works P2P** -- financial guarantees don't require a centralized escrow service
