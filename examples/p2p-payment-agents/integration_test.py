"""
integration_test.py — Simplified E2E test using direct streams (no GossipSub).
Tests the core payment flow: Offer -> Payment -> Escrow -> Receipt.
GossipSub discovery is tested separately by the individual agents.
"""

import trio
import multiaddr as ma
from libp2p import new_host
from libp2p.stream_muxer.mplex.mplex import Mplex, MPLEX_PROTOCOL_ID
from libp2p.tools.async_service.trio_service import background_trio_service
from libp2p.custom_types import TProtocol
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.peer.id import ID as PeerID

from protocol import (
    PAYMENT_PROTOCOL_ID, DEFAULT_ESCROW_HOLD_SECONDS,
    MessageType,
    CartMessage, CartItem, PaymentMessage, PaymentFailedMessage,
    RejectMessage, EscrowHoldMessage, EscrowReleaseMessage, ReceiptMessage,
    read_framed_message, write_framed_message,
    generate_payment_id, select_best_offer,
)
from payment import MockPaymentVerifier, EscrowManager, simulate_service_delivery


async def run_test():
    print("\n=== INTEGRATION TEST: P2P Agent Payment Flow ===\n")
    results = {}

    # ---- Test 1: Stream framing round-trip ----
    print("--- Test 1: Stream framing (payment -> escrow -> receipt) ---")
    h1 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    h2 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    verifier = MockPaymentVerifier()
    escrow = EscrowManager()

    async def merchant_handler(stream):
        msg = await read_framed_message(stream)
        if msg and msg.type == MessageType.PAYMENT:
            if verifier.verify_payment(msg):
                pid = generate_payment_id()
                escrow.hold(pid, msg.amount, "USD", msg.sender, "merchant", DEFAULT_ESCROW_HOLD_SECONDS)
                await write_framed_message(stream, EscrowHoldMessage(sender="merchant", payment_id=pid, amount=msg.amount))
                await simulate_service_delivery(pid, delay_seconds=0.5)
                escrow.release(pid)
                await write_framed_message(stream, EscrowReleaseMessage(sender="merchant", payment_id=pid, amount=msg.amount))
                await write_framed_message(stream, ReceiptMessage(sender="merchant", payment_id=pid, amount=msg.amount, merchant_name="QuickShoot"))
            else:
                await write_framed_message(stream, PaymentFailedMessage(sender="merchant", reason="Invalid"))

    h1.set_stream_handler(TProtocol(PAYMENT_PROTOCOL_ID), merchant_handler)

    async with h1.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9100")]):
        async with h2.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9101")]):
            h1_pid = h1.get_id().pretty()
            info = info_from_p2p_addr(ma.Multiaddr(f"/ip4/127.0.0.1/tcp/9100/p2p/{h1_pid}"))
            await h2.connect(info)

            stream = await h2.new_stream(h1.get_id(), [TProtocol(PAYMENT_PROTOCOL_ID)])
            pay = PaymentMessage(sender="buyer", cart_hash="abc123", amount=350.0)
            await write_framed_message(stream, pay)
            print("  Buyer sent payment: $350.00")

            m1 = await read_framed_message(stream)
            assert isinstance(m1, EscrowHoldMessage), f"Expected EscrowHold, got {type(m1)}"
            print(f"  Buyer received: escrow_hold ({m1.payment_id})")

            m2 = await read_framed_message(stream)
            assert isinstance(m2, EscrowReleaseMessage), f"Expected EscrowRelease, got {type(m2)}"
            print(f"  Buyer received: escrow_release ({m2.payment_id})")

            m3 = await read_framed_message(stream)
            assert isinstance(m3, ReceiptMessage), f"Expected Receipt, got {type(m3)}"
            print(f"  Buyer received: receipt ({m3.payment_id} - ${m3.amount:.2f} - {m3.status.value})")
            await stream.close()

    results["test1_stream_framing"] = True
    print("  PASS\n")

    # ---- Test 2: Payment failure flow ----
    print("--- Test 2: Payment failure (invalid authorization) ---")
    h3 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    h4 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})

    async def merchant_handler_2(stream):
        msg = await read_framed_message(stream)
        if msg and msg.type == MessageType.PAYMENT:
            if verifier.verify_payment(msg):
                await write_framed_message(stream, ReceiptMessage(sender="m", payment_id="x", amount=msg.amount, merchant_name="M"))
            else:
                await write_framed_message(stream, PaymentFailedMessage(sender="m", reason="Invalid authorization"))

    h3.set_stream_handler(TProtocol(PAYMENT_PROTOCOL_ID), merchant_handler_2)

    async with h3.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9102")]):
        async with h4.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9103")]):
            h3_pid = h3.get_id().pretty()
            info = info_from_p2p_addr(ma.Multiaddr(f"/ip4/127.0.0.1/tcp/9102/p2p/{h3_pid}"))
            await h4.connect(info)

            stream = await h4.new_stream(h3.get_id(), [TProtocol(PAYMENT_PROTOCOL_ID)])
            # Authorization starting with "invalid_" triggers failure
            pay = PaymentMessage(sender="buyer", cart_hash="abc", amount=350.0, authorization="invalid_test")
            await write_framed_message(stream, pay)
            print("  Buyer sent invalid payment")

            resp = await read_framed_message(stream)
            assert isinstance(resp, PaymentFailedMessage), f"Expected PaymentFailed, got {type(resp)}"
            print(f"  Buyer received: payment_failed ({resp.reason})")
            await stream.close()

    results["test2_payment_failure"] = True
    print("  PASS\n")

    # ---- Test 3: Offer rejection ----
    print("--- Test 3: Offer rejection ---")
    h5 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    h6 = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    rejection_received = {"value": False}

    async def merchant_handler_3(stream):
        msg = await read_framed_message(stream)
        if msg and msg.type == MessageType.REJECT:
            rejection_received["value"] = True

    h5.set_stream_handler(TProtocol(PAYMENT_PROTOCOL_ID), merchant_handler_3)

    async with h5.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9104")]):
        async with h6.run(listen_addrs=[ma.Multiaddr("/ip4/0.0.0.0/tcp/9105")]):
            h5_pid = h5.get_id().pretty()
            info = info_from_p2p_addr(ma.Multiaddr(f"/ip4/127.0.0.1/tcp/9104/p2p/{h5_pid}"))
            await h6.connect(info)

            stream = await h6.new_stream(h5.get_id(), [TProtocol(PAYMENT_PROTOCOL_ID)])
            await write_framed_message(stream, RejectMessage(sender="buyer", reason="Selected another offer"))
            print("  Buyer sent rejection")
            await stream.close()
            await trio.sleep(0.5)  # Let handler process

    assert rejection_received["value"], "Merchant did not receive rejection"
    results["test3_rejection"] = True
    print("  PASS\n")

    # ---- Test 4: Offer selection logic ----
    print("--- Test 4: Best offer selection ---")
    cart_a = CartMessage(sender="a", merchant_name="QuickShoot", items=[CartItem(name="Video", price=350)], total=350)
    cart_b = CartMessage(sender="b", merchant_name="Premium", items=[CartItem(name="Video", price=450)], total=450)
    cart_c = CartMessage(sender="c", merchant_name="Budget", items=[CartItem(name="Video", price=200)], total=200)

    best = select_best_offer([cart_a, cart_b, cart_c], 400.0)
    assert best is not None and best.merchant_name == "Budget", f"Expected Budget, got {best}"
    print(f"  Budget=$400, offers=[350, 450, 200] -> selected: {best.merchant_name} @ ${best.total}")

    best2 = select_best_offer([cart_b], 400.0)
    assert best2 is None, "Should return None when no offers within budget"
    print(f"  Budget=$400, offers=[450] -> selected: None (over budget)")

    results["test4_selection"] = True
    print("  PASS\n")

    # ---- Test 5: Escrow hold/release/refund ----
    print("--- Test 5: Escrow lifecycle ---")
    em = EscrowManager()
    rec = em.hold("txn_1", 100.0, "USD", "buyer", "merchant", 3600)
    assert rec.status.value == "held"
    print(f"  Hold: {rec.payment_id} — {rec.status.value}")

    rec = em.release("txn_1")
    assert rec.status.value == "released"
    print(f"  Release: {rec.payment_id} — {rec.status.value}")

    rec2 = em.hold("txn_2", 200.0, "USD", "buyer", "merchant", 0)  # Expires immediately
    await trio.sleep(0.1)
    expired = em.check_expired()
    assert len(expired) == 1 and expired[0].payment_id == "txn_2"
    print(f"  Expired: {expired[0].payment_id}")

    rec2 = em.refund("txn_2", "timeout")
    assert rec2.status.value == "refunded"
    print(f"  Refund: {rec2.payment_id} — {rec2.status.value}")

    results["test5_escrow"] = True
    print("  PASS\n")

    # ---- Summary ----
    print("=== TEST RESULTS ===")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}: {name}")
    print(f"\n{'ALL TESTS PASSED!' if all_pass else 'SOME TESTS FAILED!'}")
    print("=== END ===\n")


def main():
    try:
        trio.run(run_test)
    except KeyboardInterrupt:
        print("\nInterrupted.")

if __name__ == "__main__":
    main()
