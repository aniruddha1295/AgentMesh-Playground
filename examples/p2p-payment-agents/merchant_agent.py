"""
merchant_agent.py — Merchant agent for the AP2 P2P agent payment demo.

Connects to the bootstrap node, announces itself as a merchant, listens for
buyer intents on GossipSub, sends cart offers via direct libp2p streams,
and handles the full payment/escrow/receipt flow.

Usage:
    python merchant_agent.py --port 8001 --name "QuickShoot Studios" \
        --price 350 --bootstrap /ip4/127.0.0.1/tcp/8000/p2p/Qm...
"""

import argparse
import json
import logging
import os

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

from protocol import (
    MARKETPLACE_TOPIC, GOSSIPSUB_PROTOCOL_ID, PAYMENT_PROTOCOL_ID,
    OFFER_WINDOW_SECONDS, DEFAULT_ESCROW_HOLD_SECONDS,
    MessageType, AgentRole, PaymentStatus,
    AnnounceMessage, IntentMessage, CartMessage, CartItem,
    PaymentMessage, PaymentFailedMessage, RejectMessage,
    EscrowHoldMessage, EscrowReleaseMessage, ReceiptMessage,
    serialize_message, deserialize_message,
    read_framed_message, write_framed_message,
    generate_payment_id,
)
from payment import MockPaymentVerifier, RealPaymentVerifier, EscrowManager, simulate_service_delivery

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("merchant")


# ---------------------------------------------------------------------------
# Merchant Agent
# ---------------------------------------------------------------------------

class MerchantAgent:
    """Merchant agent that responds to buyer intents with cart offers."""

    def __init__(self, host, pubsub, name: str, price: float, service: str, port: int,
                 wallet=None, settler=None):
        self.host = host
        self.pubsub = pubsub
        self.name = name
        self.price = price
        self.service = service
        self.port = port
        self.tag = f"[MERCHANT:{name}]"
        self.wallet = wallet
        self.settler = settler
        self.real_mode = wallet is not None and settler is not None
        self.verifier = RealPaymentVerifier() if self.real_mode else MockPaymentVerifier()
        self.escrow = EscrowManager()

    @property
    def peer_id(self) -> str:
        return self.host.get_id().pretty()

    @property
    def my_multiaddr(self) -> str:
        return f"/ip4/127.0.0.1/tcp/{self.port}/p2p/{self.peer_id}"

    # -- Announce on startup ------------------------------------------------

    async def announce(self) -> None:
        """Publish an AnnounceMessage on the marketplace topic."""
        wallet_addr = self.wallet.get_address() if self.wallet else ""
        msg = AnnounceMessage(
            sender=self.peer_id,
            role=AgentRole.MERCHANT,
            name=self.name,
            multiaddr=self.my_multiaddr,
            wallet_address=wallet_addr,
        )
        await self.pubsub.publish(MARKETPLACE_TOPIC, serialize_message(msg))
        log.info(f"{self.tag} Announced on marketplace — {self.peer_id[:16]}...")

    # -- Send cart offer to buyer -------------------------------------------

    async def send_cart_offer(self, buyer_peer_id_str: str, buyer_multiaddr: str = "") -> None:
        """Open a direct stream to the buyer and send a CartMessage."""
        from libp2p.peer.id import ID

        # Connect to buyer if we have their multiaddr and aren't connected yet
        if buyer_multiaddr:
            try:
                buyer_info = info_from_p2p_addr(multiaddr.Multiaddr(buyer_multiaddr))
                await self.host.connect(buyer_info)
                log.info(f"{self.tag} Connected to buyer at {buyer_multiaddr[:40]}...")
            except Exception:
                pass  # May already be connected

        buyer_id = ID.from_base58(buyer_peer_id_str)

        wallet_addr = self.wallet.get_address() if self.wallet else ""
        cart = CartMessage(
            sender=self.peer_id,
            merchant_name=self.name,
            items=[CartItem(name=self.service, price=self.price)],
            total=self.price,
            wallet_address=wallet_addr,
        )

        try:
            stream = await self.host.new_stream(
                buyer_id, [TProtocol(PAYMENT_PROTOCOL_ID)]
            )
            await write_framed_message(stream, cart)
            log.info(
                f"{self.tag} Sent cart offer to {buyer_peer_id_str[:16]}... "
                f"-- {self.service} @ ${self.price:.2f}"
            )
            await stream.close()
        except Exception as exc:
            log.error(f"{self.tag} Failed to send cart to {buyer_peer_id_str[:16]}...: {exc}")

    # -- Handle incoming payment stream -------------------------------------

    async def handle_payment_stream(self, stream) -> None:
        """Stream handler for PAYMENT_PROTOCOL_ID (incoming from buyer)."""
        remote = stream.muxed_conn.peer_id.pretty() if hasattr(stream, "muxed_conn") else "?"
        log.info(f"{self.tag} Incoming stream from {remote[:16]}...")

        try:
            msg = await read_framed_message(stream)
            if msg is None:
                log.warning(f"{self.tag} Empty stream from {remote[:16]}...")
                return

            if msg.type == MessageType.PAYMENT:
                await self._process_payment(stream, msg)
            elif msg.type == MessageType.REJECT:
                log.info(
                    f"{self.tag} Offer rejected by {remote[:16]}... "
                    f"— reason: {msg.reason}"
                )
            else:
                log.warning(f"{self.tag} Unexpected message type: {msg.type.value}")
        except Exception as exc:
            log.error(f"{self.tag} Error handling stream: {exc}")

    async def _process_payment(self, stream, payment: PaymentMessage) -> None:
        """Verify payment, hold escrow, deliver, release, send receipt."""
        buyer_short = payment.sender[:16]
        log.info(
            f"{self.tag} Payment received from {buyer_short}... "
            f"— ${payment.amount:.2f} (auth: {payment.authorization[:12]}...)"
        )

        # Step 1: Verify payment
        is_valid = self.verifier.verify_payment(payment)

        if not is_valid:
            fail_msg = PaymentFailedMessage(
                sender=self.peer_id,
                reason="Payment verification failed — invalid authorization",
            )
            await write_framed_message(stream, fail_msg)
            log.warning(f"{self.tag} Payment FAILED for {buyer_short}...")
            return

        payment_id = generate_payment_id()

        # Step 2: Escrow hold
        self.escrow.hold(
            payment_id, payment.amount, payment.currency,
            payment.sender, self.peer_id,
            DEFAULT_ESCROW_HOLD_SECONDS,
        )
        hold_msg = EscrowHoldMessage(
            sender=self.peer_id,
            payment_id=payment_id,
            amount=payment.amount,
        )
        await write_framed_message(stream, hold_msg)
        log.info(f"{self.tag} Escrow HELD — {payment_id} — ${payment.amount:.2f}")

        # Step 3: Simulate service delivery
        log.info(f"{self.tag} Delivering service: {self.service}...")
        await simulate_service_delivery(payment_id, delay_seconds=2.0)

        # Step 4: Release escrow + settle on-chain (if real mode)
        tx_hash = ""
        explorer_url = ""
        block_number = 0

        if self.real_mode and payment.authorization and not payment.authorization.startswith("sig_"):
            log.info(f"{self.tag} Settling on-chain...")
            result = self.settler.settle(json.loads(payment.authorization))
            if result["success"]:
                tx_hash = result["tx_hash"]
                explorer_url = result["explorer_url"]
                block_number = result["block_number"]
                log.info(f"{self.tag} Settlement SUCCESS: {explorer_url}")
                self.wallet.invalidate_nonce_cache()
            else:
                log.error(f"{self.tag} Settlement FAILED: {result.get('error', 'unknown')}")

        self.escrow.release(payment_id)
        release_msg = EscrowReleaseMessage(
            sender=self.peer_id,
            payment_id=payment_id,
            amount=payment.amount,
        )
        await write_framed_message(stream, release_msg)
        log.info(f"{self.tag} Escrow RELEASED — {payment_id}")

        # Step 5: Send receipt
        receipt = ReceiptMessage(
            sender=self.peer_id,
            payment_id=payment_id,
            amount=payment.amount,
            merchant_name=self.name,
            tx_hash=tx_hash,
            explorer_url=explorer_url,
            block_number=block_number,
        )
        await write_framed_message(stream, receipt)
        log.info(f"{self.tag} Receipt sent — {payment_id} — COMPLETED")

    # -- GossipSub listener -------------------------------------------------

    async def listen_for_intents(self, subscription) -> None:
        """Process messages from the marketplace GossipSub topic."""
        log.info(f"{self.tag} Listening for buyer intents...")
        while True:
            msg = await subscription.get()
            try:
                parsed = deserialize_message(msg.data)
                if parsed.type == MessageType.INTENT and isinstance(parsed, IntentMessage):
                    log.info(
                        f"{self.tag} Intent from {parsed.sender[:16]}... "
                        f"— \"{parsed.description}\" (budget: ${parsed.max_budget:.2f})"
                    )
                    if self.price <= parsed.max_budget:
                        buyer_maddr = getattr(parsed, "multiaddr", "")
                        await self.send_cart_offer(parsed.sender, buyer_maddr)
                    else:
                        log.info(
                            f"{self.tag} Skipping — our price ${self.price:.2f} "
                            f"exceeds budget ${parsed.max_budget:.2f}"
                        )
                elif parsed.type == MessageType.ANNOUNCE and hasattr(parsed, "multiaddr"):
                    log.info(
                        f"{self.tag} Peer announced: {parsed.name} ({parsed.role.value})"
                    )
                    # Connect to announced peers for GossipSub mesh
                    try:
                        peer_info = info_from_p2p_addr(multiaddr.Multiaddr(parsed.multiaddr))
                        await self.host.connect(peer_info)
                    except Exception:
                        pass  # May already be connected
            except Exception:
                pass  # Ignore non-protocol messages (e.g. mesh summaries)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(port: int, name: str, price: float, service: str, bootstrap_addr: str) -> None:
    """Start the merchant agent."""
    # Load wallet if private key is available (real mode)
    load_dotenv()
    merchant_key = os.getenv("MERCHANT_PRIVATE_KEY")
    wallet = None
    settler = None

    if merchant_key:
        from wallet import Wallet
        from settlement import OnChainSettler
        rpc_url = os.getenv("RPC_URL", "https://sepolia.base.org")
        wallet = Wallet(merchant_key, rpc_url)
        settler = OnChainSettler(merchant_key, rpc_url)
        tag = f"[MERCHANT:{name}]"
        log.info(f"{tag} REAL MODE — Wallet: {wallet.get_address()}")
        try:
            balance = settler.check_balance(wallet.get_address())
            eth_bal = settler.check_eth_balance(wallet.get_address())
            log.info(f"{tag} USDC Balance: {balance:.2f} | ETH for gas: {eth_bal:.6f}")
        except Exception as e:
            log.warning(f"{tag} Could not read balances: {e}")
    else:
        tag = f"[MERCHANT:{name}]"
        log.info(f"{tag} MOCK MODE — No MERCHANT_PRIVATE_KEY in env")

    listen_addr = multiaddr.Multiaddr(f"/ip4/0.0.0.0/tcp/{port}")

    host = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})

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

    agent = MerchantAgent(host, pubsub, name, price, service, port, wallet=wallet, settler=settler)

    # Register stream handler for incoming payment streams
    host.set_stream_handler(
        TProtocol(PAYMENT_PROTOCOL_ID), agent.handle_payment_stream
    )

    async with host.run(listen_addrs=[listen_addr]):
        async with background_trio_service(pubsub), background_trio_service(gossipsub):
            await pubsub.wait_until_ready()

            # Connect to bootstrap node
            bootstrap_ma = multiaddr.Multiaddr(bootstrap_addr)
            peer_info = info_from_p2p_addr(bootstrap_ma)
            await host.connect(peer_info)
            log.info(f"{agent.tag} Connected to bootstrap node")

            # Subscribe and announce
            subscription = await pubsub.subscribe(MARKETPLACE_TOPIC)
            await trio.sleep(1)  # brief settle before announcing
            await agent.announce()

            log.info(f"{agent.tag} Merchant agent ready — {service} @ ${price:.2f}")
            log.info(f"{agent.tag} Peer ID: {agent.peer_id}")
            log.info(f"{agent.tag} Listening on: {agent.my_multiaddr}")

            # Run the intent listener
            await agent.listen_for_intents(subscription)


def main() -> None:
    parser = argparse.ArgumentParser(description="AP2 Merchant Agent — P2P payment demo")
    parser.add_argument("--port", type=int, default=8001, help="TCP port (default: 8001)")
    parser.add_argument("--name", type=str, default="QuickShoot Studios", help="Merchant name")
    parser.add_argument("--price", type=float, default=350.0, help="Service price in USD")
    parser.add_argument(
        "--service", type=str, default="Event Videography - 4 hours",
        help="Service name",
    )
    parser.add_argument("--bootstrap", type=str, required=True, help="Bootstrap node multiaddr")
    args = parser.parse_args()

    tag = f"[MERCHANT:{args.name}]"
    log.info(f"{tag} Starting on port {args.port}...")
    try:
        trio.run(run, args.port, args.name, args.price, args.service, args.bootstrap)
    except KeyboardInterrupt:
        log.info(f"{tag} Shutting down gracefully...")


if __name__ == "__main__":
    main()
