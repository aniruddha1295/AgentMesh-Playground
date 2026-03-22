"""
setup_local.py — Deploy TestUSDC and fund wallets on local Hardhat node.
Pure Python — no npx, no Hardhat compilation at runtime.

Usage:
  1. Start Hardhat: npx hardhat node --network local
  2. Run this: python setup_local.py
"""

import json
import sys
import os
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print("ERROR: Cannot connect to Hardhat node at", RPC_URL)
        print("Start it first: npx hardhat node --network local")
        sys.exit(1)

    chain_id = w3.eth.chain_id
    print(f"Connected to Hardhat (chain ID: {chain_id})")

    # Load pre-compiled contract artifact
    artifact_path = os.path.join(os.path.dirname(__file__), "contracts", "TestUSDC.json")
    with open(artifact_path) as f:
        artifact = json.load(f)

    # Deploy TestUSDC
    deployer = Account.from_key(DEPLOYER_KEY)
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])

    print(f"Deploying TestUSDC from {deployer.address}...")
    tx = contract.constructor().build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 3000000,
        "gasPrice": w3.to_wei(1, "gwei"),
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, DEPLOYER_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    usdc_address = receipt["contractAddress"]
    print(f"TestUSDC deployed at: {usdc_address}")

    usdc = w3.eth.contract(address=usdc_address, abi=artifact["abi"])

    # Get wallet addresses
    buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    merchant_key = os.getenv("MERCHANT_PRIVATE_KEY")
    if not buyer_key or not merchant_key:
        print("ERROR: Set BUYER_PRIVATE_KEY and MERCHANT_PRIVATE_KEY in .env")
        sys.exit(1)

    buyer_addr = Account.from_key(buyer_key).address
    merchant_addr = Account.from_key(merchant_key).address

    # Mint 100 USDC to buyer
    print(f"\nMinting 100 USDC to buyer ({buyer_addr[:10]}...)...")
    mint_tx = usdc.functions.mint(buyer_addr, 100_000_000).build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200000,
        "gasPrice": w3.to_wei(1, "gwei"),
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(mint_tx, DEPLOYER_KEY)
    w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(signed.hash)

    # Print balances
    buyer_usdc = usdc.functions.balanceOf(buyer_addr).call() / 1e6
    merchant_usdc = usdc.functions.balanceOf(merchant_addr).call() / 1e6
    buyer_eth = float(w3.from_wei(w3.eth.get_balance(buyer_addr), "ether"))
    merchant_eth = float(w3.from_wei(w3.eth.get_balance(merchant_addr), "ether"))

    print(f"\n=== BALANCES ===")
    print(f"  Buyer:    {buyer_eth:.2f} ETH | {buyer_usdc:.2f} USDC")
    print(f"  Merchant: {merchant_eth:.2f} ETH | {merchant_usdc:.2f} USDC")

    # Update .env with contract address and chain ID
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "r") as f:
        lines = f.readlines()

    new_lines = []
    keys_set = set()
    for line in lines:
        key = line.split("=")[0].strip()
        if key == "USDC_CONTRACT_ADDRESS":
            new_lines.append(f"USDC_CONTRACT_ADDRESS={usdc_address}\n")
            keys_set.add(key)
        elif key == "CHAIN_ID":
            new_lines.append(f"CHAIN_ID={chain_id}\n")
            keys_set.add(key)
        else:
            new_lines.append(line)

    if "USDC_CONTRACT_ADDRESS" not in keys_set:
        new_lines.append(f"USDC_CONTRACT_ADDRESS={usdc_address}\n")
    if "CHAIN_ID" not in keys_set:
        new_lines.append(f"CHAIN_ID={chain_id}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    print(f"\n=== .env UPDATED ===")
    print(f"  USDC_CONTRACT_ADDRESS={usdc_address}")
    print(f"  CHAIN_ID={chain_id}")
    print(f"\n=== SETUP COMPLETE ===")
    print(f"Now run the demo with real payments!")


if __name__ == "__main__":
    main()
