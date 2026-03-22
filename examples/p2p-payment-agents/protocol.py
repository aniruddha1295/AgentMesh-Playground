"""
protocol.py — Shared message types, protocol constants, and message framing
for P2P Agent Payment Coordination using py-libp2p.

Simulates AP2 (Agent Payments Protocol) concepts over libp2p:
  Intent → Cart → Payment → Escrow Hold → Escrow Release → Receipt

All agents import this module for consistent message handling.
"""

import json
import struct
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Protocol Constants
# ---------------------------------------------------------------------------

# Custom libp2p protocol ID for direct payment negotiation streams
PAYMENT_PROTOCOL_ID = "/ap2-sim/1.0.0"

# GossipSub topic where buyers broadcast intents and agents announce presence
MARKETPLACE_TOPIC = "ap2-marketplace"

# GossipSub protocol ID
GOSSIPSUB_PROTOCOL_ID = "/meshsub/1.0.0"

# Default escrow hold duration (7 days)
DEFAULT_ESCROW_HOLD_SECONDS = 7 * 24 * 60 * 60

# Offer collection window — how long buyer waits for merchant offers
OFFER_WINDOW_SECONDS = 10


# ---------------------------------------------------------------------------
# Message Types
# ---------------------------------------------------------------------------

class MessageType(str, Enum):
    """All message types in the AP2-sim protocol."""
    ANNOUNCE = "announce"          # Agent joins network, declares role
    INTENT = "intent"              # Buyer broadcasts purchase intent
    CART = "cart"                   # Merchant sends offer/cart
    REJECT = "reject"              # Buyer rejects an offer
    PAYMENT = "payment"            # Buyer sends payment authorization
    PAYMENT_FAILED = "payment_failed"  # Payment verification failed
    ESCROW_HOLD = "escrow_hold"    # Funds held in escrow
    ESCROW_RELEASE = "escrow_release"  # Escrow released after delivery
    ESCROW_REFUND = "escrow_refund"    # Escrow refunded (timeout/dispute)
    RECEIPT = "receipt"            # Final transaction receipt


class AgentRole(str, Enum):
    """Roles an agent can have on the network."""
    BOOTSTRAP = "bootstrap"
    BUYER = "buyer"
    MERCHANT = "merchant"


class PaymentStatus(str, Enum):
    """Payment lifecycle states."""
    PENDING = "pending"
    HELD = "held"
    RELEASED = "released"
    REFUNDED = "refunded"
    FAILED = "failed"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# Message Models (Pydantic)
# ---------------------------------------------------------------------------

class MessageEnvelope(BaseModel):
    """Base envelope for all protocol messages."""
    type: MessageType
    sender: str                    # Peer ID of sender
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])


class AnnounceMessage(MessageEnvelope):
    """Agent announces its presence and role on the network."""
    type: MessageType = MessageType.ANNOUNCE
    role: AgentRole
    name: str                      # Human-readable agent name
    multiaddr: str                 # Agent's listening address
    wallet_address: str = ""       # Ethereum address for receiving payments (merchants)


class IntentMessage(MessageEnvelope):
    """Buyer broadcasts a purchase intent (simulates AP2 IntentMandate)."""
    type: MessageType = MessageType.INTENT
    description: str               # What the buyer wants
    max_budget: float              # Maximum willing to pay
    currency: str = "USD"
    multiaddr: str = ""            # Buyer's listening address (so merchants can connect)

    @field_validator("max_budget")
    @classmethod
    def validate_budget(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_budget must be positive")
        return v


class CartItem(BaseModel):
    """A single item in a merchant's cart offer."""
    name: str
    price: float


class CartMessage(MessageEnvelope):
    """Merchant sends a cart/offer (simulates AP2 CartMandate)."""
    type: MessageType = MessageType.CART
    merchant_name: str
    items: list[CartItem]
    total: float
    currency: str = "USD"
    wallet_address: str = ""       # Merchant's Ethereum address (for real payments)
    cart_expiry: str = Field(
        default_factory=lambda: (
            datetime.now(timezone.utc) + timedelta(minutes=15)
        ).isoformat()
    )

    @property
    def cart_hash(self) -> str:
        """Deterministic hash of cart contents for integrity."""
        data = json.dumps(
            {"items": [i.model_dump() for i in self.items], "total": self.total},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(data.encode()).hexdigest()[:16]


class RejectMessage(MessageEnvelope):
    """Buyer rejects a merchant's offer."""
    type: MessageType = MessageType.REJECT
    reason: str


class PaymentMessage(MessageEnvelope):
    """Buyer sends payment authorization (simulates AP2 PaymentMandate)."""
    type: MessageType = MessageType.PAYMENT
    cart_hash: str                 # Links to the accepted cart
    amount: float
    currency: str = "USD"
    authorization: str = Field(
        default_factory=lambda: f"sig_{uuid.uuid4().hex[:16]}"
    )
    # Real payment fields (used in real mode, empty in mock mode)
    from_address: str = ""         # Buyer's Ethereum address
    to_address: str = ""           # Merchant's Ethereum address
    chain_id: int = 0              # Blockchain chain ID (84532 for Base Sepolia)
    token_contract: str = ""       # USDC contract address


class PaymentFailedMessage(MessageEnvelope):
    """Merchant signals payment verification failure."""
    type: MessageType = MessageType.PAYMENT_FAILED
    reason: str


class EscrowHoldMessage(MessageEnvelope):
    """Funds held in escrow pending service delivery."""
    type: MessageType = MessageType.ESCROW_HOLD
    payment_id: str
    status: PaymentStatus = PaymentStatus.HELD
    amount: float
    currency: str = "USD"
    release_condition: str = "service_delivered"
    hold_expiry: str = Field(
        default_factory=lambda: (
            datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_ESCROW_HOLD_SECONDS)
        ).isoformat()
    )


class EscrowReleaseMessage(MessageEnvelope):
    """Escrow released — funds transferred to merchant."""
    type: MessageType = MessageType.ESCROW_RELEASE
    payment_id: str
    status: PaymentStatus = PaymentStatus.RELEASED
    amount: float
    currency: str = "USD"


class EscrowRefundMessage(MessageEnvelope):
    """Escrow refunded — funds returned to buyer."""
    type: MessageType = MessageType.ESCROW_REFUND
    payment_id: str
    status: PaymentStatus = PaymentStatus.REFUNDED
    amount: float
    currency: str = "USD"
    reason: str = "hold_expired"


class ReceiptMessage(MessageEnvelope):
    """Final transaction receipt (simulates AP2 PaymentReceipt)."""
    type: MessageType = MessageType.RECEIPT
    payment_id: str
    status: PaymentStatus = PaymentStatus.COMPLETED
    amount: float
    currency: str = "USD"
    merchant_name: str
    escrow_released: bool = True
    # Real payment fields (populated in real mode)
    tx_hash: str = ""              # Blockchain transaction hash
    explorer_url: str = ""         # Block explorer URL for verification
    block_number: int = 0          # Block where tx was included


# ---------------------------------------------------------------------------
# Message Serialization Registry
# ---------------------------------------------------------------------------

# Map message type → model class for deserialization
MESSAGE_MODELS: dict[MessageType, type[MessageEnvelope]] = {
    MessageType.ANNOUNCE: AnnounceMessage,
    MessageType.INTENT: IntentMessage,
    MessageType.CART: CartMessage,
    MessageType.REJECT: RejectMessage,
    MessageType.PAYMENT: PaymentMessage,
    MessageType.PAYMENT_FAILED: PaymentFailedMessage,
    MessageType.ESCROW_HOLD: EscrowHoldMessage,
    MessageType.ESCROW_RELEASE: EscrowReleaseMessage,
    MessageType.ESCROW_REFUND: EscrowRefundMessage,
    MessageType.RECEIPT: ReceiptMessage,
}


def serialize_message(msg: MessageEnvelope) -> bytes:
    """Serialize a message to JSON bytes."""
    return msg.model_dump_json().encode("utf-8")


def deserialize_message(data: bytes) -> MessageEnvelope:
    """Deserialize JSON bytes into the correct message type."""
    raw = json.loads(data.decode("utf-8"))
    msg_type = MessageType(raw["type"])
    model_class = MESSAGE_MODELS[msg_type]
    return model_class.model_validate(raw)


# ---------------------------------------------------------------------------
# Length-Prefix Framing (for libp2p streams)
# ---------------------------------------------------------------------------
# Libp2p streams are raw bytes. We use 4-byte big-endian uint32 length
# prefix so the receiver knows where each JSON message ends.
#
# Wire format: [4 bytes: length][N bytes: JSON payload]
# ---------------------------------------------------------------------------

FRAME_HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB max


def frame_message(msg: MessageEnvelope) -> bytes:
    """Encode a message with length-prefix framing for stream transport."""
    payload = serialize_message(msg)
    length = len(payload)
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} bytes (max {MAX_MESSAGE_SIZE})")
    return struct.pack(">I", length) + payload


async def read_framed_message(stream) -> Optional[MessageEnvelope]:
    """Read a single length-prefixed message from a libp2p stream.

    Returns None if the stream is closed or empty.
    """
    # Read 4-byte length header
    header = await stream.read(FRAME_HEADER_SIZE)
    if not header or len(header) < FRAME_HEADER_SIZE:
        return None  # Stream closed cleanly

    length = struct.unpack(">I", header)[0]
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} bytes")

    # Read the payload (may be short if stream closes mid-message)
    payload = await stream.read(length)
    if not payload or len(payload) < length:
        return None  # Partial read — stream closed mid-message, graceful degradation

    return deserialize_message(payload)


async def write_framed_message(stream, msg: MessageEnvelope) -> None:
    """Write a single length-prefixed message to a libp2p stream."""
    data = frame_message(msg)
    await stream.write(data)


# ---------------------------------------------------------------------------
# Utility Helpers
# ---------------------------------------------------------------------------

def generate_payment_id() -> str:
    """Generate a unique payment/transaction ID."""
    return f"txn_{uuid.uuid4().hex[:12]}"


def is_within_budget(offer_total: float, max_budget: float) -> bool:
    """Check if a merchant's offer is within the buyer's budget."""
    return offer_total <= max_budget


def select_best_offer(carts: list[CartMessage], max_budget: float) -> Optional[CartMessage]:
    """Select the cheapest offer within budget from competing merchants."""
    eligible = [c for c in carts if is_within_budget(c.total, max_budget)]
    if not eligible:
        return None
    return min(eligible, key=lambda c: c.total)
