"""
buyer_agent.py — Buyer agent for the AP2 P2P agent payment demo.

Connects to the network, publishes a purchase intent, collects competing
merchant offers, selects the best one, sends payment, and receives escrow
confirmation and final receipt.

Usage:
    python buyer_agent.py --port 8003 --budget 400 --bootstrap /ip4/127.0.0.1/tcp/8000/p2p/Qm...
"""

import argparse
import json
import logging
import os
import time
from typing import Dict, Optional

import trio
import multiaddr
from dotenv import load_dotenv
from libp2p import new_host
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.stream_muxer.mplex.mplex import Mplex, MPLEX_PROTOCOL_ID
from libp2p.tools.async_service.trio_service import background_trio_service
from libp2p.custom_types import TProtocol
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.peer.id import ID as PeerID

from protocol import (
    MARKETPLACE_TOPIC, GOSSIPSUB_PROTOCOL_ID, PAYMENT_PROTOCOL_ID,
    OFFER_WINDOW_SECONDS,
    MessageType, AgentRole,
    AnnounceMessage, IntentMessage, CartMessage,
    PaymentMessage, RejectMessage,
    EscrowHoldMessage, EscrowReleaseMessage, EscrowRefundMessage, ReceiptMessage,
    PaymentFailedMessage,
    serialize_message, deserialize_message,
    read_framed_message, write_framed_message,
    select_best_offer,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("buyer")

TAG = "[BUYER]"

# ---------------------------------------------------------------------------
# Offer collection state
# ---------------------------------------------------------------------------

# Maps merchant peer_id (str) -> CartMessage
received_offers: Dict[str, CartMessage] = {}

# Maps merchant peer_id (str) -> wallet address (for real payments)
merchant_wallets: Dict[str, str] = {}

# Wallet instance (None in mock mode)
buyer_wallet: Optional[object] = None


# ---------------------------------------------------------------------------
# Stream handler — merchants open streams to us with their offers
# ---------------------------------------------------------------------------

async def offer_stream_handler(stream) -> None:
    """Handle an incoming stream from a merchant sending a CartMessage."""
    try:
        msg = await read_framed_message(stream)
        if msg is None:
            return
        if isinstance(msg, CartMessage):
            peer_id = msg.sender
            received_offers[peer_id] = msg
            log.info(f"{TAG} Received offer from {msg.merchant_name}: ${msg.total:.2f}")
        else:
            log.warning(f"{TAG} Unexpected message type on offer stream: {msg.type}")
    except Exception as exc:
        log.warning(f"{TAG} Error reading offer stream: {exc}")
    finally:
        await stream.close()


# ---------------------------------------------------------------------------
# Post-selection: send payment or rejection to merchants
# ---------------------------------------------------------------------------

async def send_rejection(host, peer_id_str: str, my_peer_id: str) -> None:
    """Open a stream to a merchant and send a RejectMessage."""
    try:
        peer_id = PeerID.from_base58(peer_id_str)
        stream = await host.new_stream(peer_id, [TProtocol(PAYMENT_PROTOCOL_ID)])
        reject = RejectMessage(sender=my_peer_id, reason="Selected another offer")
        await write_framed_message(stream, reject)
        merchant_name = received_offers[peer_id_str].merchant_name
        log.info(f"{TAG} Sent rejection to {merchant_name}")
        await stream.close()
    except Exception as exc:
        log.warning(f"{TAG} Failed to send rejection to {peer_id_str[:16]}...: {exc}")


async def send_payment_and_await_receipt(host, best: CartMessage, my_peer_id: str) -> None:
    """Open a stream to the selected merchant, send payment, and listen for escrow + receipt."""
    peer_id = PeerID.from_base58(best.sender)
    stream = await host.new_stream(peer_id, [TProtocol(PAYMENT_PROTOCOL_ID)])

    # Build payment message
    # Get merchant wallet from CartMessage or from announcements
    merchant_eth_addr = getattr(best, "wallet_address", "") or merchant_wallets.get(best.sender, "")

    if buyer_wallet and merchant_eth_addr:
        # REAL MODE — sign EIP-3009 authorization
        from config import USDC_CONTRACT_ADDRESS, BASE_SEPOLIA_CHAIN_ID, usdc_to_atomic
        amount_atomic = usdc_to_atomic(best.total)

        log.info(f"{TAG} Signing EIP-3009 authorization (local, no network)...")
        authorization = buyer_wallet.sign_transfer_authorization(
            to=merchant_eth_addr,
            amount_atomic=amount_atomic,
        )

        payment = PaymentMessage(
            sender=my_peer_id,
            cart_hash=best.cart_hash,
            amount=best.total,
            currency=best.currency,
            from_address=buyer_wallet.get_address(),
            to_address=merchant_eth_addr,
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            token_contract=USDC_CONTRACT_ADDRESS,
            authorization=json.dumps(authorization),
        )
        log.info(f"{TAG} Sent REAL payment of ${best.total:.2f} USDC to {best.merchant_name}")
    else:
        # MOCK MODE — fake signature
        payment = PaymentMessage(
            sender=my_peer_id,
            cart_hash=best.cart_hash,
            amount=best.total,
            currency=best.currency,
        )
        log.info(f"{TAG} Sent MOCK payment of ${best.total:.2f} to {best.merchant_name}")

    await write_framed_message(stream, payment)

    # Listen for escrow hold, escrow release, and receipt on the same stream
    while True:
        msg = await read_framed_message(stream)
        if msg is None:
            log.warning(f"{TAG} Stream closed before receipt was received")
            break

        if isinstance(msg, EscrowHoldMessage):
            log.info(f"{TAG} Escrow confirmed: ${msg.amount:.2f} held for {msg.payment_id}")
        elif isinstance(msg, EscrowReleaseMessage):
            log.info(f"{TAG} Service delivered. Escrow released for {msg.payment_id}")
        elif isinstance(msg, ReceiptMessage):
            log.info(f"{TAG} Receipt received! Transaction: {msg.payment_id} "
                     f"- ${msg.amount:.2f} - {msg.status.value.upper()}")
            if msg.tx_hash:
                log.info(f"{TAG} On-chain tx: {msg.tx_hash}")
                log.info(f"{TAG} Verify: {msg.explorer_url}")
            log.info(f"{TAG} Payment to {msg.merchant_name} completed successfully!")
            break
        elif isinstance(msg, PaymentFailedMessage):
            log.error(f"{TAG} Payment failed: {msg.reason}")
            break
        elif isinstance(msg, EscrowRefundMessage):
            log.info(f"{TAG} Escrow refunded: ${msg.amount:.2f} — {msg.reason}")
            break
        else:
            log.warning(f"{TAG} Unexpected message type: {msg.type}")

    await stream.close()


# ---------------------------------------------------------------------------
# GossipSub message listener (for mesh summaries / other broadcasts)
# ---------------------------------------------------------------------------

async def handle_topic_messages(subscription, host) -> None:
    """Listen on the marketplace topic and connect to announced peers."""
    while True:
        msg = await subscription.get()
        try:
            parsed = deserialize_message(msg.data)
            if parsed.type == MessageType.ANNOUNCE and hasattr(parsed, "multiaddr"):
                log.info(f"{TAG} Peer announced: {parsed.name} ({parsed.role.value}) at {parsed.multiaddr}")
                # Store merchant wallet address for real payments
                if hasattr(parsed, "wallet_address") and parsed.wallet_address:
                    merchant_wallets[parsed.sender] = parsed.wallet_address
                    log.info(f"{TAG} Stored wallet for {parsed.name}: {parsed.wallet_address[:10]}...")
                # Connect to announced peers for GossipSub mesh formation
                try:
                    peer_info = info_from_p2p_addr(multiaddr.Multiaddr(parsed.multiaddr))
                    await host.connect(peer_info)
                except Exception:
                    pass  # May already be connected or unreachable
        except Exception:
            pass  # Ignore unparseable messages (e.g. mesh_summary from bootstrap)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(port: int, budget: float, description: str, bootstrap_addr: str, delay: int = 10) -> None:
    """Run the buyer agent."""
    global buyer_wallet

    # Load wallet if private key is available (real mode)
    load_dotenv()
    buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    real_mode = bool(buyer_key)

    if real_mode:
        from wallet import Wallet
        from config import USDC_CONTRACT_ADDRESS, BASE_SEPOLIA_CHAIN_ID, usdc_to_atomic
        rpc_url = os.getenv("RPC_URL", "https://sepolia.base.org")
        buyer_wallet = Wallet(buyer_key, rpc_url)
        log.info(f"{TAG} REAL MODE — Wallet: {buyer_wallet.get_address()}")
        try:
            balance = buyer_wallet.get_balance()
            log.info(f"{TAG} USDC Balance: {balance:.2f}")
        except Exception as e:
            log.warning(f"{TAG} Could not read balance: {e}")
    else:
        log.info(f"{TAG} MOCK MODE — No BUYER_PRIVATE_KEY in env")

    listen_addr = multiaddr.Multiaddr(f"/ip4/0.0.0.0/tcp/{port}")

    # Create host with Mplex muxer
    host = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})

    # Set up GossipSub and Pubsub
    gossipsub = GossipSub(
        protocols=[TProtocol(GOSSIPSUB_PROTOCOL_ID)],
        degree=3,
        degree_low=2,
        degree_high=4,
        time_to_live=60,
        gossip_window=2,
        gossip_history=5,
        heartbeat_initial_delay=0.5,
        heartbeat_interval=2,
    )
    pubsub = Pubsub(host=host, router=gossipsub)

    async with host.run(listen_addrs=[listen_addr]):
        async with background_trio_service(pubsub), background_trio_service(gossipsub):
            await pubsub.wait_until_ready()

            peer_id = host.get_id().pretty()
            full_addr = f"/ip4/127.0.0.1/tcp/{port}/p2p/{peer_id}"
            log.info(f"{TAG} Buyer agent started — Peer ID: {peer_id[:16]}...")

            # Register stream handler for incoming merchant offers
            host.set_stream_handler(TProtocol(PAYMENT_PROTOCOL_ID), offer_stream_handler)

            # Connect to bootstrap node
            bootstrap_ma = multiaddr.Multiaddr(bootstrap_addr)
            bootstrap_info = info_from_p2p_addr(bootstrap_ma)
            await host.connect(bootstrap_info)
            log.info(f"{TAG} Connected to bootstrap node")

            # Subscribe to marketplace topic
            subscription = await pubsub.subscribe(MARKETPLACE_TOPIC)

            # Announce presence
            announce = AnnounceMessage(
                sender=peer_id,
                role=AgentRole.BUYER,
                name="Buyer Agent",
                multiaddr=full_addr,
            )
            await pubsub.publish(MARKETPLACE_TOPIC, serialize_message(announce))
            log.info(f"{TAG} Published announce message")

            # Wait for GossipSub mesh to settle before publishing intent
            log.info(f"{TAG} Waiting {delay}s for GossipSub mesh to settle...")
            await trio.sleep(delay)

            # Publish purchase intent
            intent = IntentMessage(
                sender=peer_id,
                description=description,
                max_budget=budget,
                currency="USD",
                multiaddr=full_addr,
            )
            await pubsub.publish(MARKETPLACE_TOPIC, serialize_message(intent))
            log.info(f"{TAG} Published intent: \"{description}\" (budget: ${budget:.2f})")

            # Collect offers for the offer window period
            log.info(f"{TAG} Waiting {OFFER_WINDOW_SECONDS}s for merchant offers...")

            async with trio.open_nursery() as nursery:
                nursery.start_soon(handle_topic_messages, subscription, host)

                # Wait for offers to arrive
                await trio.sleep(OFFER_WINDOW_SECONDS)

                # Evaluate offers
                offers = list(received_offers.values())
                if not offers:
                    log.info(f"{TAG} No offers received within window. Exiting.")
                    nursery.cancel_scope.cancel()
                    return

                log.info(f"{TAG} Received {len(offers)} offer(s). Selecting best...")
                best = select_best_offer(offers, budget)

                if best is None:
                    log.info(f"{TAG} No offers within budget (${budget:.2f}). Exiting.")
                    for pid in received_offers:
                        await send_rejection(host, pid, peer_id)
                    nursery.cancel_scope.cancel()
                    return

                log.info(f"{TAG} Selected {best.merchant_name} at ${best.total:.2f}")

                # Reject non-selected merchants
                for pid, cart in received_offers.items():
                    if pid != best.sender:
                        await send_rejection(host, pid, peer_id)

                # Send payment to selected merchant and await receipt
                await send_payment_and_await_receipt(host, best, peer_id)

                # Done — cancel the topic listener
                nursery.cancel_scope.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="AP2 Buyer Agent — P2P payment demo")
    parser.add_argument("--port", type=int, default=8003, help="TCP port to listen on (default: 8003)")
    parser.add_argument("--budget", type=float, default=400.0, help="Maximum budget in USD (default: 400)")
    parser.add_argument("--description", type=str, default="Book a videographer for an event",
                        help="Purchase intent description")
    parser.add_argument("--bootstrap", type=str, required=True, help="Bootstrap node multiaddr")
    parser.add_argument("--delay", type=int, default=10, help="Seconds to wait for mesh formation (default: 10)")
    args = parser.parse_args()

    log.info(f"{TAG} Starting buyer agent on port {args.port}...")
    try:
        trio.run(run, args.port, args.budget, args.description, args.bootstrap, args.delay)
    except KeyboardInterrupt:
        log.info(f"{TAG} Shutting down gracefully...")


if __name__ == "__main__":
    main()
