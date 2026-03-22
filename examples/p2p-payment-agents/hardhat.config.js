/** @type {import('hardhat/config').HardhatUserConfig} */
export default {
  solidity: "0.8.20",
  networks: {
    local: {
      type: "edr-simulated",
      chainId: 31337,
    },
  },
};
