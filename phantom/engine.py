"""
Reticulum Phantom — Engine

Background engine that manages the Reticulum stack, seeders,
and leechers. Provides a unified API for the TUI to start/stop
transfers, query state, and receive events.

The engine runs RNS in a background thread and is fully
decoupled from any UI framework.
"""

import os
import time
import threading
from collections import deque
from datetime import datetime

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config
from .identity import PhantomIdentity
from .ghost_file import GhostFile
from .network import PhantomNetwork
from .seeder import Seeder
from .leecher import Leecher


class TransferInfo:
    """Tracks the state of a single transfer (seed or download)."""

    def __init__(self, transfer_id, direction, name, ghost_hash=None):
        self.id = transfer_id
        self.direction = direction      # "upload" or "download"
        self.name = name
        self.ghost_hash = ghost_hash
        self.state = "starting"
        self.progress = 0.0             # 0.0 → 1.0
        self.chunks_done = 0
        self.total_chunks = 0
        self.bytes_transferred = 0
        self.speed = 0.0
        self.peers = 0
        self.error = None
        self.started_at = time.time()
        self.destination_hash = ""
        self._seeder = None
        self._leecher = None


class PhantomEngine:
    """
    Central engine managing all Phantom operations.

    Thread-safe — all state mutations go through locks.
    The TUI polls or subscribes to state changes.
    """

    def __init__(self, rns_config_path=None):
        self._rns_config = rns_config_path
        self._network = None
        self._identity = None
        self._running = False
        self._lock = threading.Lock()

        # Active transfers
        self._transfers = {}        # id → TransferInfo
        self._transfer_counter = 0

        # Event log (ring buffer)
        self._log = deque(maxlen=200)

        # Known peers on the mesh
        self._peers = {}  # identity_hex → {name, dest, last_seen, files[]}

        # Callbacks for TUI
        self.on_log = None           # Called with (timestamp, level, message)
        self.on_transfer_update = None  # Called with (transfer_id)

    @property
    def is_running(self):
        return self._running

    @property
    def identity_hash(self):
        if self._identity and self._identity.is_loaded:
            return self._identity.hash_pretty
        return "Not loaded"

    @property
    def identity_hex(self):
        if self._identity and self._identity.is_loaded:
            return self._identity.hash_hex
        return ""

    def start(self):
        """Start the engine: initialize RNS and load identity."""
        if self._running:
            return

        self._add_log("info", "Starting Reticulum Network Stack...")

        self._network = PhantomNetwork(self._rns_config)
        self._network.start()

        self._identity = PhantomIdentity()
        self._identity.load()

        if not self._identity.is_loaded:
            self._identity.create_new()
            self._add_log("info", "Created new Phantom identity")

        self._running = True
        self._add_log("info",
                       f"Engine started | Identity: {self._identity.hash_pretty}")

        # Register announce handler to track peers on the mesh
        self._register_peer_handler()

    def stop(self):
        """Stop all transfers and shut down."""
        self._running = False

        with self._lock:
            for tid, transfer in list(self._transfers.items()):
                if transfer._seeder:
                    try:
                        transfer._seeder.stop()
                    except Exception:
                        pass
                if transfer._leecher:
                    try:
                        transfer._leecher.cancel()
                    except Exception:
                        pass

        self._add_log("info", "Engine stopped")

    def seed_file(self, file_path):
        """
        Start seeding a file.

        Args:
            file_path: Path to the file (or .ghost file).

        Returns:
            Transfer ID string, or None on error.
        """
        if not self._running:
            return None

        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            self._add_log("error", f"File not found: {file_path}")
            return None

        # Determine ghost vs regular file
        if file_path.endswith(config.GHOST_EXTENSION):
            ghost = GhostFile.load(file_path)
            if not ghost:
                self._add_log("error", "Invalid ghost file")
                return None
            source_dir = os.path.dirname(file_path)
            source_path = os.path.join(source_dir, ghost.name)
            if not os.path.isfile(source_path):
                base_path = file_path[:-len(config.GHOST_EXTENSION)]
                if os.path.isfile(base_path):
                    source_path = base_path
                else:
                    self._add_log("error",
                                  f"Source file not found: {ghost.name}")
                    return None
        else:
            source_path = file_path
            ghost_path = file_path + config.GHOST_EXTENSION

            if os.path.isfile(ghost_path):
                ghost = GhostFile.load(ghost_path)
                if not ghost:
                    ghost = None

            if not os.path.isfile(ghost_path) or ghost is None:
                self._add_log("info",
                              f"Hashing: {os.path.basename(file_path)}...")
                ghost = GhostFile.create(file_path)
                if not ghost:
                    self._add_log("error", "Failed to create ghost")
                    return None
                ghost.save()

        # Create transfer
        with self._lock:
            self._transfer_counter += 1
            tid = f"seed_{self._transfer_counter}"

        transfer = TransferInfo(
            tid, "upload", ghost.name, ghost.ghost_hash
        )
        transfer.total_chunks = ghost.chunk_count
        transfer.state = "seeding"

        # Create and start seeder
        seeder = Seeder(ghost, source_path, self._network, self._identity)
        seeder.start()
        transfer._seeder = seeder
        transfer.destination_hash = seeder.destination_hash_hex or ""

        with self._lock:
            self._transfers[tid] = transfer

        self._add_log("info",
                       f"↑ Seeding: {ghost.name} | "
                       f"Dest: {transfer.destination_hash}")

        # Start stats poller
        threading.Thread(
            target=self._poll_seeder, args=(tid,), daemon=True
        ).start()

        return tid

    def download_file(self, destination_hash):
        """
        Start downloading from a destination hash.

        Args:
            destination_hash: Hex string of the seeder's destination.

        Returns:
            Transfer ID string, or None on error.
        """
        if not self._running:
            return None

        with self._lock:
            self._transfer_counter += 1
            tid = f"dl_{self._transfer_counter}"

        transfer = TransferInfo(tid, "download", "Discovering...", "")
        transfer.state = "discovering"

        leecher = Leecher(self._network, self._identity)
        transfer._leecher = leecher

        def on_state_change(state, info):
            transfer.state = state
            if state == "fetching_manifest" and leecher.ghost:
                transfer.name = leecher.ghost.name
                transfer.total_chunks = leecher.total_chunks
                transfer.ghost_hash = leecher.ghost_hash
            elif state == "complete":
                transfer.progress = 1.0
                self._add_log("info",
                              f"✓ Download complete: {transfer.name}")
            elif state == "failed":
                transfer.error = info
                self._add_log("error",
                              f"✗ Download failed: {info}")
            self._notify_transfer(tid)

        def on_progress(chunks_done, total, bytes_received):
            transfer.chunks_done = chunks_done
            transfer.total_chunks = total
            transfer.bytes_transferred = bytes_received
            transfer.progress = chunks_done / total if total > 0 else 0
            elapsed = time.time() - transfer.started_at
            transfer.speed = bytes_received / elapsed if elapsed > 0 else 0
            self._notify_transfer(tid)

        leecher.on_state_change = on_state_change
        leecher.on_progress = on_progress

        with self._lock:
            self._transfers[tid] = transfer

        # Detect if input is a .ghost file path
        if os.path.isfile(destination_hash) or destination_hash.endswith(config.GHOST_EXTENSION):
            ghost_path = os.path.abspath(destination_hash)
            if os.path.isfile(ghost_path):
                ghost = GhostFile.load(ghost_path)
                if ghost:
                    transfer.name = ghost.name
                    transfer.ghost_hash = ghost.ghost_hash
                    transfer.total_chunks = ghost.chunk_count
                    self._add_log("info",
                                  f"↓ Downloading: {ghost.name} via .ghost file")
                    leecher.download_from_ghost(ghost_path)
                    return tid
                else:
                    self._add_log("error", f"Invalid ghost file: {ghost_path}")
                    return None
            else:
                self._add_log("error", f"Ghost file not found: {ghost_path}")
                return None

        self._add_log("info",
                       f"↓ Downloading: {destination_hash}")

        leecher.download_from_hash(destination_hash)
        return tid

    def stop_transfer(self, transfer_id):
        """Stop a specific transfer."""
        with self._lock:
            transfer = self._transfers.get(transfer_id)

        if not transfer:
            return

        if transfer._seeder:
            transfer._seeder.stop()
            transfer.state = "stopped"
        if transfer._leecher:
            transfer._leecher.cancel()
            transfer.state = "cancelled"

        self._add_log("info", f"Transfer stopped: {transfer.name}")

    def get_transfers(self):
        """Get list of all transfers for display."""
        with self._lock:
            return list(self._transfers.values())

    def get_ghost_files(self):
        """List all .ghost files in the data directory."""
        config.ensure_directories()
        ghosts = []

        # Check ghosts dir
        for fname in os.listdir(config.GHOSTS_DIR):
            if fname.endswith(config.GHOST_EXTENSION):
                fpath = os.path.join(config.GHOSTS_DIR, fname)
                ghost = GhostFile.load(fpath)
                if ghost:
                    ghosts.append({
                        "path": fpath,
                        "name": ghost.name,
                        "size": ghost.file_size,
                        "size_human": GhostFile._human_size(ghost.file_size),
                        "chunks": ghost.chunk_count,
                        "ghost_hash": ghost.ghost_hash,
                        "seeder_dest": ghost.seeder_dest or "",
                        "created_at": ghost.created_at,
                    })

        return ghosts

    def get_log_entries(self):
        """Get recent log entries."""
        with self._lock:
            return list(self._log)

    def get_peers(self):
        """Get list of known peers on the mesh."""
        with self._lock:
            return list(self._peers.values())

    def get_network_stats(self):
        """Get network stats for display."""
        total_up = sum(
            t.bytes_transferred for t in self._transfers.values()
            if t.direction == "upload"
        )
        total_down = sum(
            t.bytes_transferred for t in self._transfers.values()
            if t.direction == "download"
        )
        active = sum(
            1 for t in self._transfers.values()
            if t.state in ("seeding", "downloading", "discovering",
                           "connecting", "fetching_manifest")
        )

        return {
            "online": self._running,
            "total_uploaded": total_up,
            "total_downloaded": total_down,
            "active_transfers": active,
            "total_transfers": len(self._transfers),
            "total_uploaded_human": GhostFile._human_size(total_up),
            "total_downloaded_human": GhostFile._human_size(total_down),
        }

    # ─── Internal ──────────────────────────────────────────────────────────

    def _register_peer_handler(self):
        """Register an announce handler to track Phantom peers on the mesh."""
        engine = self

        class PeerAnnounceHandler:
            def __init__(self):
                self.aspect_filter = config.RNS_APP_NAME + ".swarm"

            def received_announce(self, destination_hash, announced_identity, app_data):
                if app_data:
                    try:
                        metadata = umsgpack.unpackb(app_data)
                        ghost_hash = metadata.get("ghost_hash", "")
                        file_name = metadata.get("name", "Unknown")
                        file_size = metadata.get("size", 0)

                        identity_hex = announced_identity.hash.hex()
                        dest_hex = destination_hash.hex()

                        with engine._lock:
                            if identity_hex not in engine._peers:
                                engine._peers[identity_hex] = {
                                    "identity": identity_hex,
                                    "identity_short": identity_hex[:16] + "...",
                                    "files": {},
                                    "last_seen": time.time(),
                                    "dest_hashes": [],
                                }
                                engine._add_log(
                                    "info",
                                    f"New peer discovered: {identity_hex[:16]}..."
                                )

                            peer = engine._peers[identity_hex]
                            peer["last_seen"] = time.time()
                            peer["files"][ghost_hash] = {
                                "name": file_name,
                                "size": file_size,
                                "ghost_hash": ghost_hash,
                                "dest_hash": dest_hex,
                            }
                            if dest_hex not in peer["dest_hashes"]:
                                peer["dest_hashes"].append(dest_hex)

                    except Exception:
                        pass

        handler = PeerAnnounceHandler()
        RNS.Transport.register_announce_handler(handler)
        self._add_log("info", "Listening for peers on mesh...")

    def _poll_seeder(self, transfer_id):
        """Poll seeder stats periodically."""
        while self._running:
            time.sleep(2)
            with self._lock:
                transfer = self._transfers.get(transfer_id)

            if not transfer or not transfer._seeder:
                break
            if transfer.state == "stopped":
                break

            stats = transfer._seeder.get_stats()
            transfer.peers = stats["active_peers"]
            transfer.chunks_done = stats["chunks_served"]
            transfer.bytes_transferred = stats["total_uploaded"]
            self._notify_transfer(transfer_id)

    def _add_log(self, level, message):
        """Add a log entry."""
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        with self._lock:
            self._log.append(entry)

        if self.on_log:
            try:
                self.on_log(entry)
            except Exception:
                pass

    def _notify_transfer(self, transfer_id):
        """Notify that a transfer has updated."""
        if self.on_transfer_update:
            try:
                self.on_transfer_update(transfer_id)
            except Exception:
                pass
