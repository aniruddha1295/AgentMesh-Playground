"""Verify a transaction on the local Hardhat blockchain."""
import sys
from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv()

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL", "http://127.0.0.1:8545")))
tx_hash = sys.argv[1] if len(sys.argv) > 1 else input("Paste tx hash: ")

try:
    tx = w3.eth.get_transaction_receipt(tx_hash)
    print(f"\n=== TRANSACTION VERIFIED ===")
    print(f"  Status:      {'SUCCESS' if tx['status'] == 1 else 'FAILED'}")
    print(f"  Block:       {tx['blockNumber']}")
    print(f"  Gas Used:    {tx['gasUsed']}")
    print(f"  From:        {tx['from']}")
    print(f"  To Contract: {tx['to']}")
    print(f"  Tx Hash:     0x{tx['transactionHash'].hex()}")
    print(f"  ========================\n")
except Exception as e:
    print(f"Error: {e}")
