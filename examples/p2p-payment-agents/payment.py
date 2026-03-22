"""
payment.py — Payment verification and escrow management for the
AP2-sim P2P agent payment demo.

Supports two modes:
- Mock mode: simulated verification (default when no wallet keys)
- Real mode: EIP-3009 signature verification (local math, no RPC)

The escrow manager tracks hold / release / refund lifecycle.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import trio

from protocol import PaymentMessage, PaymentStatus, generate_payment_id

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock Payment Verifier
# ---------------------------------------------------------------------------

class MockPaymentVerifier:
    """Simulates payment authorization verification.

    Always succeeds unless the authorization string starts with
    ``"invalid_"``, which lets us exercise failure flows in the demo.
    """

    @staticmethod
    def verify_payment(payment: PaymentMessage) -> bool:
        """Return True if the payment authorization looks valid."""
        if payment.authorization.startswith("invalid_"):
            log.warning("[PAYMENT] Verification FAILED for auth=%s", payment.authorization)
            return False
        log.info(
            "[PAYMENT] Verified payment: amount=%.2f %s auth=%s",
            payment.amount, payment.currency, payment.authorization,
        )
        return True

    @staticmethod
    def generate_payment_id() -> str:
        """Return a unique transaction ID (delegates to protocol helper)."""
        pid = generate_payment_id()
        log.info("[PAYMENT] Generated payment_id=%s", pid)
        return pid


class RealPaymentVerifier:
    """Verifies EIP-3009 signatures locally — pure math, no RPC.

    Uses Wallet.verify_authorization() to recover the signer from
    the signature and check it matches the claimed sender.
    """

    @staticmethod
    def verify_payment(payment: PaymentMessage) -> bool:
        """Verify the EIP-3009 signature in the payment authorization."""
        # Fall back to mock if authorization looks like a mock signature
        if not payment.authorization or payment.authorization.startswith("sig_"):
            log.warning("[PAYMENT] No real authorization — falling back to mock")
            return MockPaymentVerifier.verify_payment(payment)

        if not payment.from_address:
            log.warning("[PAYMENT] No from_address — cannot verify real payment")
            return False

        try:
            from wallet import Wallet
            authorization = json.loads(payment.authorization)
            is_valid = Wallet.verify_authorization(authorization, payment.from_address)
            if is_valid:
                log.info("[PAYMENT] Real signature VERIFIED for %s", payment.from_address[:10])
            else:
                log.warning("[PAYMENT] Real signature INVALID for %s", payment.from_address[:10])
            return is_valid
        except Exception as e:
            log.error("[PAYMENT] Verification error: %s", e)
            return False


# ---------------------------------------------------------------------------
# Escrow Record
# ---------------------------------------------------------------------------

@dataclass
class EscrowRecord:
    """Represents a single escrow hold."""
    payment_id: str
    amount: float
    currency: str
    buyer_id: str
    merchant_id: str
    status: PaymentStatus
    hold_expiry: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    authorization: str = ""        # Stored signed auth JSON (for real mode settlement)


# ---------------------------------------------------------------------------
# Escrow Manager
# ---------------------------------------------------------------------------

class EscrowManager:
    """In-memory escrow ledger: hold, release, refund, and expiry checks."""

    def __init__(self) -> None:
        self._escrows: dict[str, EscrowRecord] = {}

    # -- core operations ----------------------------------------------------

    def hold(
        self,
        payment_id: str,
        amount: float,
        currency: str,
        buyer_id: str,
        merchant_id: str,
        hold_seconds: int,
    ) -> EscrowRecord:
        """Create a new escrow hold for *payment_id*."""
        now = datetime.now(timezone.utc)
        record = EscrowRecord(
            payment_id=payment_id,
            amount=amount,
            currency=currency,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            status=PaymentStatus.HELD,
            hold_expiry=now + timedelta(seconds=hold_seconds),
            created_at=now,
        )
        self._escrows[payment_id] = record
        log.info(
            "[ESCROW] HOLD created: id=%s amount=%.2f %s buyer=%s merchant=%s expires=%s",
            payment_id, amount, currency, buyer_id, merchant_id,
            record.hold_expiry.isoformat(),
        )
        return record

    def release(self, payment_id: str) -> EscrowRecord:
        """Release escrow funds to the merchant.

        Raises ``ValueError`` if the escrow is not in HELD status.
        """
        record = self._require(payment_id, PaymentStatus.HELD, "release")
        record.status = PaymentStatus.RELEASED
        log.info("[ESCROW] RELEASED: id=%s amount=%.2f %s", payment_id, record.amount, record.currency)
        return record

    def refund(self, payment_id: str, reason: str = "hold_expired") -> EscrowRecord:
        """Refund escrow funds back to the buyer.

        Raises ``ValueError`` if the escrow is not in HELD status.
        """
        record = self._require(payment_id, PaymentStatus.HELD, "refund")
        record.status = PaymentStatus.REFUNDED
        log.info(
            "[ESCROW] REFUNDED: id=%s amount=%.2f %s reason=%s",
            payment_id, record.amount, record.currency, reason,
        )
        return record

    # -- queries ------------------------------------------------------------

    def check_expired(self) -> list[EscrowRecord]:
        """Return escrows that are still HELD but past their expiry time."""
        now = datetime.now(timezone.utc)
        return [
            r for r in self._escrows.values()
            if r.status == PaymentStatus.HELD and r.hold_expiry < now
        ]

    def get(self, payment_id: str) -> Optional[EscrowRecord]:
        """Look up an escrow record by *payment_id*."""
        return self._escrows.get(payment_id)

    # -- internal helpers ---------------------------------------------------

    def _require(self, payment_id: str, expected: PaymentStatus, action: str) -> EscrowRecord:
        record = self._escrows.get(payment_id)
        if record is None:
            raise ValueError(f"Cannot {action}: no escrow found for {payment_id}")
        if record.status != expected:
            raise ValueError(
                f"Cannot {action} escrow {payment_id}: status is {record.status.value}, expected {expected.value}"
            )
        return record


# ---------------------------------------------------------------------------
# Simulated Service Delivery
# ---------------------------------------------------------------------------

async def simulate_service_delivery(payment_id: str, delay_seconds: float) -> bool:
    """Simulate service delivery by waiting *delay_seconds*, then returning True.

    In the real world this would be an external confirmation; here it just
    models time passing before the merchant fulfils the order.
    """
    log.info("[PAYMENT] Service delivery started for %s (%.1fs delay)…", payment_id, delay_seconds)
    await trio.sleep(delay_seconds)
    log.info("[PAYMENT] Service delivered for %s", payment_id)
    return True
