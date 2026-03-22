"""
config.py — Chain configuration, contract addresses, and ABIs for Base Sepolia USDC.
"""

import os

# Chain configuration (reads from .env, falls back to Base Sepolia defaults)
BASE_SEPOLIA_CHAIN_ID = int(os.getenv("CHAIN_ID", "84532"))
BASE_SEPOLIA_RPC_URL = os.getenv("RPC_URL", "https://sepolia.base.org")

# USDC contract address (reads from .env — set by setup_local.py for Hardhat)
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")

# EIP-712 domain constants for USDC
USDC_TOKEN_NAME = "USD Coin"
USDC_TOKEN_VERSION = "2"
USDC_DECIMALS = 6

# Block explorer (use basescan for real Base Sepolia, local for Hardhat)
_chain_id = int(os.getenv("CHAIN_ID", "84532"))
BASE_SEPOLIA_EXPLORER = "https://sepolia.basescan.org" if _chain_id == 84532 else "http://localhost:8545 (local hardhat)"

# --- ABI Definitions ---

# ABI for transferWithAuthorization (EIP-3009)
TRANSFER_WITH_AUTH_ABI = [
    {
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "signature", "type": "bytes"},
        ],
        "name": "transferWithAuthorization",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ABI for reading nonce (needed before signing)
NONCES_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "nonces",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# ABI for reading balance
BALANCE_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Combined ABI for full USDC interaction
USDC_ABI = TRANSFER_WITH_AUTH_ABI + NONCES_ABI + BALANCE_ABI

# --- Helpers ---

def usdc_to_atomic(amount_usd: float) -> int:
    """Convert human-readable USDC amount to atomic units (6 decimals).
    Example: 3.50 -> 3500000
    """
    return int(amount_usd * (10 ** USDC_DECIMALS))

def atomic_to_usdc(amount_atomic: int) -> float:
    """Convert atomic units to human-readable USDC amount.
    Example: 3500000 -> 3.50
    """
    return amount_atomic / (10 ** USDC_DECIMALS)

def explorer_tx_url(tx_hash: str) -> str:
    """Build block explorer URL for a transaction."""
    return f"{BASE_SEPOLIA_EXPLORER}/tx/{tx_hash}"
