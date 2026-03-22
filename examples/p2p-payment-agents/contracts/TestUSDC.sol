// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title TestUSDC
 * @notice Minimal ERC20 with EIP-3009 transferWithAuthorization for testing.
 * @dev Implements only what we need: balanceOf, transfer, transferWithAuthorization, nonces.
 */
contract TestUSDC {
    string public name = "USD Coin";
    string public version = "2";
    uint8 public decimals = 6;
    string public symbol = "USDC";

    mapping(address => uint256) public balanceOf;
    mapping(address => uint256) public nonces;
    uint256 public totalSupply;

    // EIP-712 domain separator
    bytes32 public DOMAIN_SEPARATOR;

    // EIP-3009 typehash
    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH =
        keccak256("TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)");

    // Track used authorization nonces (separate from sequential nonces)
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    event Transfer(address indexed from, address indexed to, uint256 value);

    constructor() {
        DOMAIN_SEPARATOR = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256(bytes(name)),
            keccak256(bytes(version)),
            block.chainid,
            address(this)
        ));
    }

    /// @notice Mint tokens to an address (only for testing)
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
        emit Transfer(address(0), to, amount);
    }

    /// @notice Standard ERC20 transfer
    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    /// @notice EIP-3009: Execute a transfer with a signed authorization
    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        bytes memory signature
    ) external {
        require(block.timestamp > validAfter, "Authorization not yet valid");
        require(block.timestamp < validBefore, "Authorization expired");
        require(!authorizationState[from][nonce], "Authorization already used");

        // Build EIP-712 digest
        bytes32 structHash = keccak256(abi.encode(
            TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
            from, to, value, validAfter, validBefore, nonce
        ));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));

        // Recover signer
        address signer = _recover(digest, signature);
        require(signer == from, "Invalid signature");

        // Mark nonce as used
        authorizationState[from][nonce] = true;

        // Execute transfer
        require(balanceOf[from] >= value, "Insufficient balance");
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }

    /// @dev Recover signer from signature
    function _recover(bytes32 digest, bytes memory signature) internal pure returns (address) {
        require(signature.length == 65, "Invalid signature length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(signature, 32))
            s := mload(add(signature, 64))
            v := byte(0, mload(add(signature, 96)))
        }
        if (v < 27) v += 27;
        return ecrecover(digest, v, r, s);
    }
}
