"""
wallet.py — Ethereum wallet for EIP-3009 transferWithAuthorization.

Handles:
- EIP-712 typed data signing (LOCAL — no network call)
- Signature verification (LOCAL — pure math)
- Nonce reading from USDC contract (ONE RPC call, cached)
"""

import json
import logging
import time
from typing import Optional

from eth_account import Account
from web3 import Web3

from config import (
    BASE_SEPOLIA_CHAIN_ID,
    USDC_CONTRACT_ADDRESS,
    USDC_TOKEN_NAME,
    USDC_TOKEN_VERSION,
    USDC_ABI,
)

log = logging.getLogger("wallet")
TAG = "[WALLET]"


class Wallet:
    """Ethereum wallet for signing and verifying EIP-3009 payment authorizations."""

    def __init__(self, private_key: str, rpc_url: str):
        """Initialize wallet from private key.

        Args:
            private_key: Hex string starting with 0x
            rpc_url: Base Sepolia RPC endpoint
        """
        self.account = Account.from_key(private_key)
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.usdc_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
            abi=USDC_ABI,
        )
        self._cached_nonce: Optional[bytes] = None
        log.info(f"{TAG} Wallet loaded: {self.get_address()}")

    def get_address(self) -> str:
        """Return wallet's public Ethereum address (checksummed)."""
        return self.account.address

    def get_nonce(self) -> bytes:
        """Generate a unique nonce for EIP-3009 authorization.

        Uses a random 32-byte nonce. Each signed authorization uses a
        unique nonce that gets marked as used on-chain, preventing replay.
        """
        import os as _os
        nonce = _os.urandom(32)
        log.info(f"{TAG} Generated random nonce: 0x{nonce[:4].hex()}...")
        return nonce

    def invalidate_nonce_cache(self):
        """Clear cached nonce (call after a transaction is settled)."""
        self._cached_nonce = None

    def get_balance(self) -> float:
        """Read USDC balance (RPC call). Returns human-readable amount."""
        from config import atomic_to_usdc
        raw = self.usdc_contract.functions.balanceOf(self.get_address()).call()
        return atomic_to_usdc(raw)

    def sign_transfer_authorization(
        self,
        to: str,
        amount_atomic: int,
        valid_after: int = 0,
        valid_before: Optional[int] = None,
    ) -> dict:
        """Sign an EIP-3009 transferWithAuthorization.

        This is LOCAL — only uses cached nonce, no network call needed for signing.

        Args:
            to: Recipient Ethereum address
            amount_atomic: Amount in USDC atomic units (6 decimals)
            valid_after: Unix timestamp after which auth is valid (0 = immediately)
            valid_before: Unix timestamp before which auth is valid (default: +1 hour)

        Returns:
            dict with: from, to, value, validAfter, validBefore, nonce, signature
        """
        if valid_before is None:
            valid_before = int(time.time()) + 3600  # 1 hour from now

        nonce = self.get_nonce()

        # Build EIP-712 typed data
        domain_data = {
            "name": USDC_TOKEN_NAME,
            "version": USDC_TOKEN_VERSION,
            "chainId": BASE_SEPOLIA_CHAIN_ID,
            "verifyingContract": Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
        }

        message_types = {
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        }

        message_data = {
            "from": Web3.to_checksum_address(self.get_address()),
            "to": Web3.to_checksum_address(to),
            "value": amount_atomic,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        }

        # Sign (LOCAL — pure crypto, no network)
        signed = Account.sign_typed_data(
            self.account.key,
            domain_data=domain_data,
            message_types=message_types,
            message_data=message_data,
        )

        # Build signature as hex string
        signature_hex = signed.signature.hex()
        if not signature_hex.startswith("0x"):
            signature_hex = "0x" + signature_hex

        authorization = {
            "from": self.get_address(),
            "to": Web3.to_checksum_address(to),
            "value": str(amount_atomic),
            "validAfter": str(valid_after),
            "validBefore": str(valid_before),
            "nonce": "0x" + nonce.hex(),
            "signature": signature_hex,
        }

        log.info(f"{TAG} Signed authorization: {amount_atomic} atomic USDC to {to[:10]}...")
        return authorization

    @staticmethod
    def verify_authorization(authorization: dict, expected_from: str) -> bool:
        """Verify an EIP-3009 signature — LOCAL, pure math, no RPC.

        Recovers the signer from the signature and checks it matches expected_from.

        Args:
            authorization: dict with from, to, value, validAfter, validBefore, nonce, signature
            expected_from: Expected signer Ethereum address

        Returns:
            True if signature is valid and matches expected_from
        """
        try:
            domain_data = {
                "name": USDC_TOKEN_NAME,
                "version": USDC_TOKEN_VERSION,
                "chainId": BASE_SEPOLIA_CHAIN_ID,
                "verifyingContract": Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
            }

            message_types = {
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            }

            # Reconstruct nonce as bytes
            nonce_hex = authorization["nonce"]
            if nonce_hex.startswith("0x"):
                nonce_hex = nonce_hex[2:]
            nonce_bytes = bytes.fromhex(nonce_hex)

            message_data = {
                "from": Web3.to_checksum_address(authorization["from"]),
                "to": Web3.to_checksum_address(authorization["to"]),
                "value": int(authorization["value"]),
                "validAfter": int(authorization["validAfter"]),
                "validBefore": int(authorization["validBefore"]),
                "nonce": nonce_bytes,
            }

            # Recover signer (LOCAL — pure elliptic curve math)
            from eth_account.messages import encode_typed_data
            signable = encode_typed_data(
                domain_data=domain_data,
                message_types=message_types,
                message_data=message_data,
            )

            signature = bytes.fromhex(authorization["signature"].replace("0x", ""))
            recovered = Account.recover_message(signable, signature=signature)

            is_valid = recovered.lower() == expected_from.lower()

            if is_valid:
                log.info(f"{TAG} Signature verified: signer={recovered[:10]}...")
            else:
                log.warning(f"{TAG} Signature INVALID: expected={expected_from[:10]}... got={recovered[:10]}...")

            return is_valid
        except Exception as e:
            log.error(f"{TAG} Verification error: {e}")
            return False
