"""
settlement.py — On-chain settlement for EIP-3009 transferWithAuthorization.

Submits a buyer's signed payment authorization to the USDC contract on Base Sepolia.
The settler (merchant) pays gas. This is the ONLY HTTP/RPC call in the payment flow.
"""

import logging
from typing import Optional

from eth_account import Account
from web3 import Web3

from config import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_RPC_URL,
    USDC_CONTRACT_ADDRESS,
    USDC_ABI,
    explorer_tx_url,
    atomic_to_usdc,
)

log = logging.getLogger("settlement")
TAG = "[SETTLEMENT]"


class OnChainSettler:
    """Submits signed EIP-3009 authorizations to the USDC contract on-chain.

    The settler pays gas for the transaction. In our case, the merchant
    is the settler — they submit the buyer's signed authorization.
    """

    def __init__(self, private_key: str, rpc_url: str = BASE_SEPOLIA_RPC_URL):
        """Initialize with a gas-paying wallet.

        Args:
            private_key: Hex private key of the gas payer (merchant)
            rpc_url: Base Sepolia RPC endpoint
        """
        self.account = Account.from_key(private_key)
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.usdc_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
            abi=USDC_ABI,
        )
        log.info(f"{TAG} Settler initialized: {self.account.address}")

    def settle(self, authorization: dict) -> dict:
        """Submit transferWithAuthorization to USDC contract on Base Sepolia.

        Args:
            authorization: dict with from, to, value, validAfter, validBefore, nonce, signature

        Returns:
            dict with: success, tx_hash, block_number, gas_used, explorer_url, error (if failed)
        """
        try:
            # Parse authorization fields
            from_addr = Web3.to_checksum_address(authorization["from"])
            to_addr = Web3.to_checksum_address(authorization["to"])
            value = int(authorization["value"])
            valid_after = int(authorization["validAfter"])
            valid_before = int(authorization["validBefore"])

            # Nonce as bytes32
            nonce_hex = authorization["nonce"]
            if nonce_hex.startswith("0x"):
                nonce_hex = nonce_hex[2:]
            nonce_bytes = bytes.fromhex(nonce_hex)

            # Signature as bytes
            sig_hex = authorization["signature"]
            if sig_hex.startswith("0x"):
                sig_hex = sig_hex[2:]
            signature_bytes = bytes.fromhex(sig_hex)

            log.info(f"{TAG} Settling: {atomic_to_usdc(value)} USDC from {from_addr[:10]}... to {to_addr[:10]}...")

            # Build transaction
            tx = self.usdc_contract.functions.transferWithAuthorization(
                from_addr,
                to_addr,
                value,
                valid_after,
                valid_before,
                nonce_bytes,
                signature_bytes,
            ).build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": 200000,
                "gasPrice": self.w3.eth.gas_price * 2,  # Auto gas price with buffer
                "chainId": self.w3.eth.chain_id,
            })

            # Sign with settler's key (pays gas)
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)

            # Send and wait for receipt
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()
            if not tx_hash_hex.startswith("0x"):
                tx_hash_hex = "0x" + tx_hash_hex

            log.info(f"{TAG} Transaction sent: {tx_hash_hex}")
            log.info(f"{TAG} Waiting for confirmation...")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt["status"] == 1:
                result = {
                    "success": True,
                    "tx_hash": tx_hash_hex,
                    "block_number": receipt["blockNumber"],
                    "gas_used": receipt["gasUsed"],
                    "explorer_url": explorer_tx_url(tx_hash_hex),
                }
                log.info(f"{TAG} Settlement SUCCESS: {result['explorer_url']}")
                return result
            else:
                result = {
                    "success": False,
                    "tx_hash": tx_hash_hex,
                    "block_number": receipt["blockNumber"],
                    "gas_used": receipt["gasUsed"],
                    "explorer_url": explorer_tx_url(tx_hash_hex),
                    "error": "Transaction reverted",
                }
                log.error(f"{TAG} Settlement REVERTED: {result['explorer_url']}")
                return result

        except Exception as e:
            log.error(f"{TAG} Settlement FAILED: {e}")
            return {
                "success": False,
                "tx_hash": "",
                "block_number": 0,
                "gas_used": 0,
                "explorer_url": "",
                "error": str(e),
            }

    def check_balance(self, address: str) -> float:
        """Check USDC balance of an address. Returns human-readable USDC amount."""
        raw = self.usdc_contract.functions.balanceOf(
            Web3.to_checksum_address(address)
        ).call()
        return atomic_to_usdc(raw)

    def check_eth_balance(self, address: str) -> float:
        """Check ETH balance (for gas). Returns ETH amount."""
        wei = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(self.w3.from_wei(wei, "ether"))
