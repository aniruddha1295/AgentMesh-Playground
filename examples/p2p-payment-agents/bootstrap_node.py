"""
bootstrap_node.py — Bootstrap/registry node for the AP2 P2P agent payment demo.

Acts as a well-known meeting point for agent discovery. Maintains a registry
of connected peers and their roles, and periodically broadcasts the mesh
summary so all agents know who is online.

Usage:
    python bootstrap_node.py --port 8000
"""

import argparse
import json
import logging
import os
import time
from typing import Dict

import trio
import multiaddr
from libp2p import new_host
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.stream_muxer.mplex.mplex import Mplex, MPLEX_PROTOCOL_ID
from libp2p.tools.async_service.trio_service import background_trio_service
from libp2p.custom_types import TProtocol
from libp2p.peer.peerinfo import info_from_p2p_addr

from protocol import (
    MARKETPLACE_TOPIC, GOSSIPSUB_PROTOCOL_ID, PAYMENT_PROTOCOL_ID,
    MessageType, AgentRole, AnnounceMessage,
    serialize_message, deserialize_message,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bootstrap")

TAG = "[BOOTSTRAP]"

# ---------------------------------------------------------------------------
# Peer Registry
# ---------------------------------------------------------------------------

# peer_id (str) → {role, name, multiaddr, last_seen}
peer_registry: Dict[str, dict] = {}

MESH_BROADCAST_INTERVAL = 10  # seconds between mesh summary broadcasts


def register_peer(peer_id: str, role: str, name: str, addr: str) -> None:
    """Add or update a peer in the registry."""
    is_new = peer_id not in peer_registry
    peer_registry[peer_id] = {
        "role": role,
        "name": name,
        "multiaddr": addr,
        "last_seen": time.time(),
    }
    if is_new:
        log.info(f"{TAG} Peer registered: {name} ({role}) — {peer_id[:16]}...")
    else:
        log.info(f"{TAG} Peer updated: {name} ({role}) — {peer_id[:16]}...")


def remove_peer(peer_id: str) -> None:
    """Remove a peer from the registry."""
    info = peer_registry.pop(peer_id, None)
    if info:
        log.info(f"{TAG} Peer removed: {info['name']} ({info['role']}) — {peer_id[:16]}...")


def mesh_summary() -> dict:
    """Build a JSON-serializable summary of the current mesh."""
    return {
        "type": "mesh_summary",
        "peers": {
            pid: {"role": p["role"], "name": p["name"], "multiaddr": p["multiaddr"]}
            for pid, p in peer_registry.items()
        },
        "count": len(peer_registry),
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# GossipSub Message Handler
# ---------------------------------------------------------------------------

async def handle_topic_messages(subscription) -> None:
    """Listen for messages on the marketplace topic and process them."""
    while True:
        msg = await subscription.get()
        try:
            parsed = deserialize_message(msg.data)
            sender_short = parsed.sender[:16] if parsed.sender else "?"

            # Process announcements to update the peer registry
            if parsed.type == MessageType.ANNOUNCE and isinstance(parsed, AnnounceMessage):
                register_peer(parsed.sender, parsed.role.value, parsed.name, parsed.multiaddr)
            else:
                log.info(f"{TAG} Message on topic: type={parsed.type.value} from={sender_short}...")
        except Exception:
            pass  # Ignore non-protocol messages (e.g. mesh summaries)


# ---------------------------------------------------------------------------
# Mesh Broadcaster
# ---------------------------------------------------------------------------

async def broadcast_mesh(pubsub, topic) -> None:
    """Periodically publish the mesh summary to the marketplace topic."""
    while True:
        await trio.sleep(MESH_BROADCAST_INTERVAL)
        summary = mesh_summary()
        data = json.dumps(summary).encode("utf-8")
        await pubsub.publish(topic, data)
        log.info(f"{TAG} Broadcast mesh summary — {summary['count']} peer(s) online")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(port: int) -> None:
    """Start the bootstrap node."""
    listen_addr = multiaddr.Multiaddr(f"/ip4/0.0.0.0/tcp/{port}")

    # Create host with Mplex muxer
    host = new_host(muxer_opt={MPLEX_PROTOCOL_ID: Mplex})

    # Set up GossipSub and Pubsub
    gossipsub = GossipSub(
        protocols=[TProtocol(GOSSIPSUB_PROTOCOL_ID)],
        degree=3,
        degree_low=2,
        degree_high=4,
        time_to_live=60,
        gossip_window=2,
        gossip_history=5,
        heartbeat_initial_delay=0.5,
        heartbeat_interval=2,
    )
    pubsub = Pubsub(host=host, router=gossipsub)

    # Start services
    async with host.run(listen_addrs=[listen_addr]):
        async with background_trio_service(pubsub), background_trio_service(gossipsub):
            await pubsub.wait_until_ready()

            # Subscribe to the marketplace topic
            subscription = await pubsub.subscribe(MARKETPLACE_TOPIC)

            # Print listening address so other agents can connect
            peer_id = host.get_id().pretty()
            full_addr = f"/ip4/127.0.0.1/tcp/{port}/p2p/{peer_id}"
            log.info(f"{TAG} Bootstrap node started")
            log.info(f"{TAG} Peer ID: {peer_id}")
            log.info(f"{TAG} Listening on: {full_addr}")
            log.info(f"{TAG} Topic: {MARKETPLACE_TOPIC}")
            log.info(f"{TAG} Copy this address for other agents:")
            print(f"\n  {full_addr}\n")

            # Announce ourselves on the topic
            announce = AnnounceMessage(
                sender=peer_id,
                role=AgentRole.BOOTSTRAP,
                name="Bootstrap Node",
                multiaddr=full_addr,
            )
            await pubsub.publish(MARKETPLACE_TOPIC, serialize_message(announce))
            log.info(f"{TAG} Published announce message")

            # Run message listener and mesh broadcaster concurrently
            async with trio.open_nursery() as nursery:
                nursery.start_soon(handle_topic_messages, subscription)
                nursery.start_soon(broadcast_mesh, pubsub, MARKETPLACE_TOPIC)


def main() -> None:
    parser = argparse.ArgumentParser(description="AP2 Bootstrap Node — P2P peer discovery")
    parser.add_argument("--port", type=int, default=8000, help="TCP port to listen on (default: 8000)")
    args = parser.parse_args()

    log.info(f"{TAG} Starting bootstrap node on port {args.port}...")
    try:
        trio.run(run, args.port)
    except KeyboardInterrupt:
        log.info(f"{TAG} Shutting down gracefully...")


if __name__ == "__main__":
    main()
