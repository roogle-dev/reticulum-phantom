"""
Reticulum Phantom — Seeder Node (Single-Destination Architecture)

One RNS Destination per Phantom instance, serving all seeded files.
File identity lives in request payloads, not in the destination aspect.

Architecture:
  SeedManager   — Owns ONE RNS.Destination("phantom", "swarm").
                   Registers request handlers that dispatch by ghost_hash.
                   Runs ONE announce loop (heartbeat).
  SeedEntry     — Data container for one seeded file (ghost, chunker, stats).
  WantAnnounceHandler — Singleton listener for leecher "want" broadcasts.

Flow:
  1. SeedManager creates ONE destination on first file added
  2. Request handlers receive {ghost_hash: ...} in payload, dispatch to SeedEntry
  3. ONE announce carries all ghost_hashes this node seeds
  4. WantAnnounceHandler matches wants → SeedManager responds via path request
"""

import os
import time
import threading

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config
from .ghost_file import GhostFile
from .chunker import Chunker


# Maximum ghost_hashes to include in announce app_data.
# RNS has announce data limits; cap to prevent oversized announces.
MAX_ANNOUNCE_HASHES = 50


class SeedEntry:
    """
    Data container for one seeded file. No RNS objects.

    Holds the ghost metadata, source file path, chunker, and
    per-file statistics. The SeedManager dispatches requests
    to the correct SeedEntry by ghost_hash.
    """

    def __init__(self, ghost, source_path):
        self.ghost = ghost
        self.source_path = source_path
        self.chunker = Chunker(ghost)

        # Per-file stats
        self.total_uploaded = 0
        self.chunks_served = 0
        self.peers_connected = 0
        self.start_time = time.time()

    @property
    def ghost_hash(self):
        return self.ghost.ghost_hash

    @property
    def name(self):
        return self.ghost.name

    def get_stats(self):
        """Get per-file statistics for display."""
        uptime = time.time() - self.start_time if self.start_time else 0
        return {
            "name": self.ghost.name,
            "ghost_hash": self.ghost.ghost_hash,
            "file_size": self.ghost.file_size,
            "chunk_count": self.ghost.chunk_count,
            "chunks_available": self.ghost.chunk_count,
            "total_uploaded": self.total_uploaded,
            "total_uploaded_human": GhostFile._human_size(self.total_uploaded),
            "chunks_served": self.chunks_served,
            "uptime_seconds": int(uptime),
        }


class WantAnnounceHandler:
    """
    Global singleton that listens for leecher 'want' announcements.

    Dispatches to the SeedManager when a leecher announces
    "I want ghost_hash X" and the manager has that file.
    """

    _instance = None
    _manager = None       # Reference to the SeedManager
    _lock = threading.Lock()
    _registered = False

    @classmethod
    def register_manager(cls, manager):
        """Register the SeedManager to respond to wants."""
        with cls._lock:
            cls._manager = manager

        # Register the handler singleton with RNS Transport (once)
        if not cls._registered:
            cls._instance = cls()
            RNS.Transport.register_announce_handler(cls._instance)
            cls._registered = True
            RNS.log(
                "WantAnnounceHandler registered — listening for wants",
                RNS.LOG_INFO
            )

    def __init__(self):
        # Catch ALL announces and filter manually by app_data.
        self.aspect_filter = None
        self.receive_path_responses = True

    def received_announce(self, destination_hash, announced_identity, app_data):
        """Called by RNS when ANY announcement is received."""
        if not app_data:
            return

        try:
            metadata = umsgpack.unpackb(app_data)

            # Only process dict payloads (Phantom announces)
            if not isinstance(metadata, dict):
                return

            announce_type = metadata.get("type", "")
            ghost_hash = metadata.get("ghost_hash", "")

            # Handle "want" announces — respond to leechers
            if announce_type == "want" and ghost_hash:
                with self._lock:
                    manager = self._manager
                if manager and manager.has_file(ghost_hash):
                    RNS.log(
                        f"Leecher wants {ghost_hash[:16]}... — "
                        f"we have it! Responding...",
                        RNS.LOG_INFO
                    )
                    threading.Thread(
                        target=manager._respond_to_want,
                        args=(destination_hash, ghost_hash),
                        daemon=True
                    ).start()
                return

            # Handle "seeder" announces — discover peer seeders
            if announce_type == "seeder":
                # New format: ghost_hashes list
                peer_hashes = metadata.get("ghost_hashes", [])
                # Backward compat: single ghost_hash
                if not peer_hashes and ghost_hash:
                    peer_hashes = [ghost_hash]

                with self._lock:
                    manager = self._manager
                if manager:
                    new_dest = destination_hash.hex()
                    own_dest = manager.destination_hash_hex or ""

                    # Don't add ourselves
                    if new_dest != own_dest:
                        for gh in peer_hashes:
                            entry = manager.get_entry(gh)
                            if entry and new_dest not in entry.ghost.seeder_dests:
                                entry.ghost.seeder_dests.append(new_dest)
                                entry.ghost.save()
                                RNS.log(
                                    f"Discovered peer seeder for "
                                    f"{gh[:16]}... → {new_dest[:16]}... "
                                    f"(now {len(entry.ghost.seeder_dests)} known)",
                                    RNS.LOG_INFO
                                )
        except Exception as e:
            RNS.log(
                f"Error processing announce: {e}",
                RNS.LOG_WARNING
            )


class SeedManager:
    """
    Manages all seeded files through ONE RNS Destination.

    Creates a single destination ("phantom.swarm") on first use,
    registers request handlers that dispatch by ghost_hash in
    the request payload, and runs one announce/heartbeat loop.

    Thread-safe — all mutations go through _lock.
    """

    def __init__(self, network, identity):
        self.network = network
        self.identity = identity

        self._destination = None
        self._entries = {}          # ghost_hash → SeedEntry
        self._active_links = []
        self._running = False
        self._announce_thread = None
        self._lock = threading.Lock()

    @property
    def destination_hash(self):
        """Get the destination hash for this seeder node."""
        if self._destination:
            return self._destination.hash
        return None

    @property
    def destination_hash_hex(self):
        """Get the destination hash as a hex string."""
        if self._destination:
            return RNS.hexrep(self._destination.hash, delimit=False)
        return None

    def has_file(self, ghost_hash):
        """Check if we're seeding a file with this ghost_hash."""
        with self._lock:
            return ghost_hash in self._entries

    def get_entry(self, ghost_hash):
        """Get the SeedEntry for a ghost_hash, or None."""
        with self._lock:
            return self._entries.get(ghost_hash)

    def get_all_entries(self):
        """Get all SeedEntry instances."""
        with self._lock:
            return list(self._entries.values())

    def get_ghost_hashes(self):
        """Get list of all ghost_hashes being seeded."""
        with self._lock:
            return list(self._entries.keys())

    def add(self, ghost, source_path):
        """
        Start seeding a file.

        Creates the shared destination on first call.
        Subsequent calls just register the file and re-announce.

        Args:
            ghost: A GhostFile instance.
            source_path: Path to the actual file being seeded.

        Returns:
            A SeedEntry instance.
        """
        # Create the shared destination on first file
        if self._destination is None:
            self._create_destination()

        entry = SeedEntry(ghost, source_path)

        with self._lock:
            self._entries[ghost.ghost_hash] = entry

        # Save destination hash into ghost file
        dest_hex = self._destination.hash.hex()
        ghost.seeder_dest = dest_hex
        if dest_hex not in ghost.seeder_dests:
            ghost.seeder_dests.append(dest_hex)

        # Save ghost file next to source (primary location)
        if ghost.source_path:
            src_ghost = ghost.source_path + config.GHOST_EXTENSION
            ghost.save(src_ghost)
            RNS.log(f"Ghost file saved: {src_ghost}", RNS.LOG_INFO)

        # Also save to the library (backup / catalog)
        config.ensure_directories()
        library_path = os.path.join(
            config.GHOSTS_DIR,
            ghost.name + config.GHOST_EXTENSION
        )
        ghost.save(library_path)

        RNS.log(
            f"Seeding: {ghost.name} | "
            f"Hash: {ghost.ghost_hash} | "
            f"Destination: {RNS.prettyhexrep(self._destination.hash)}",
            RNS.LOG_INFO
        )

        # Re-announce with updated file list
        self._do_announce()

        return entry

    def remove(self, ghost_hash):
        """Stop seeding a specific file."""
        with self._lock:
            entry = self._entries.pop(ghost_hash, None)

        if entry:
            RNS.log(f"Stopped seeding: {entry.name}", RNS.LOG_INFO)

        # If no files left, could tear down destination, but keep it
        # alive for potential future adds without re-announce cost.

    def stop(self):
        """Stop all seeding and tear down the destination."""
        self._running = False

        with self._lock:
            self._entries.clear()

        for link in self._active_links:
            try:
                link.teardown()
            except Exception:
                pass
        self._active_links.clear()

        # Deregister destination from RNS Transport
        if self._destination:
            try:
                RNS.Transport.deregister_destination(self._destination)
            except Exception:
                try:
                    with RNS.Transport.destinations_lock:
                        RNS.Transport.destinations = [
                            d for d in RNS.Transport.destinations
                            if d.hash != self._destination.hash
                        ]
                except Exception:
                    pass
            self._destination = None

        RNS.log("SeedManager stopped", RNS.LOG_INFO)

    def _create_destination(self):
        """Create the single shared RNS destination and register handlers."""
        self._destination = RNS.Destination(
            self.identity.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            config.RNS_APP_NAME,
            "swarm",
        )

        # Register request handlers — all dispatch by ghost_hash in payload
        self._destination.register_request_handler(
            "manifest",
            response_generator=self._handle_manifest_request,
            allow=RNS.Destination.ALLOW_ALL
        )

        self._destination.register_request_handler(
            "chunk",
            response_generator=self._handle_chunk_request,
            allow=RNS.Destination.ALLOW_ALL
        )

        self._destination.register_request_handler(
            "status",
            response_generator=self._handle_status_request,
            allow=RNS.Destination.ALLOW_ALL
        )

        self._destination.register_request_handler(
            "peers",
            response_generator=self._handle_peers_request,
            allow=RNS.Destination.ALLOW_ALL
        )

        self._destination.register_request_handler(
            "catalog",
            response_generator=self._handle_catalog_request,
            allow=RNS.Destination.ALLOW_ALL
        )

        # Set up link callbacks
        self._destination.set_link_established_callback(
            self._on_link_established
        )

        self._running = True

        # Register with the global want-announce handler
        WantAnnounceHandler.register_manager(self)

        # Start the single heartbeat re-announce loop
        self._announce_thread = threading.Thread(
            target=self._announce_loop, daemon=True
        )
        self._announce_thread.start()

        RNS.log(
            f"SeedManager destination created: "
            f"{RNS.prettyhexrep(self._destination.hash)}",
            RNS.LOG_INFO
        )

    def _make_announce_data(self):
        """Build announce app_data with all seeded ghost_hashes.

        RNS silently drops re-announces with identical app_data from
        the same destination. Adding a timestamp ensures each announce
        is unique and propagates to all peers.
        """
        hashes = self.get_ghost_hashes()
        return umsgpack.packb({
            "type": "seeder",
            "ghost_hashes": hashes[:MAX_ANNOUNCE_HASHES],
            "t": int(time.time()),  # nonce — prevents dedup
        })

    def _do_announce(self):
        """Send an announce with current file list."""
        if self._destination and self._running:
            try:
                self._destination.announce(
                    app_data=self._make_announce_data()
                )
            except Exception:
                pass

    def _announce_loop(self):
        """Infrequent re-announce loop (heartbeat).

        The initial announce happens when files are added.
        This loop handles periodic heartbeats (every 3h default).
        """
        interval = config.DEFAULT_ANNOUNCE_INTERVAL
        elapsed = 0
        while self._running:
            time.sleep(1)
            elapsed += 1
            if elapsed >= interval:
                elapsed = 0
                if self._running and self._destination:
                    try:
                        self._destination.announce(
                            app_data=self._make_announce_data()
                        )
                        hashes = self.get_ghost_hashes()
                        RNS.log(
                            f"Heartbeat: re-announced {len(hashes)} file(s)",
                            RNS.LOG_DEBUG
                        )
                    except Exception:
                        pass

    def _respond_to_want(self, leecher_dest_hash, ghost_hash):
        """
        Respond to a leecher's 'want' announcement.

        Strategy: Request path to the leecher — this triggers a PATH_RESPONSE
        on the mesh, which is NOT subject to announce rate limiting.
        The leecher will see our path appear in their path table and can
        connect directly via Link.

        We do NOT re-announce here. Re-announcing per want causes announce
        storms when multiple leechers want the same file simultaneously.
        """
        if not self._running or not self._destination:
            return

        try:
            if not RNS.Transport.has_path(leecher_dest_hash):
                RNS.Transport.request_path(leecher_dest_hash)

            entry = self.get_entry(ghost_hash)
            name = entry.name if entry else ghost_hash[:16]
            RNS.log(
                f"Responded to want for {name} — "
                f"path request sent (no re-announce)",
                RNS.LOG_INFO
            )
        except Exception as e:
            RNS.log(
                f"Failed to respond to want: {e}",
                RNS.LOG_WARNING
            )

    # ─── Link Callbacks ─────────────────────────────────────────────────────

    def _on_link_established(self, link):
        """Called when a leecher connects."""
        self._active_links.append(link)
        link.set_link_closed_callback(self._on_link_closed)
        RNS.log(
            f"Peer connected ({len(self._active_links)} active)",
            RNS.LOG_INFO
        )

    def _on_link_closed(self, link):
        """Called when a peer disconnects."""
        if link in self._active_links:
            self._active_links.remove(link)
        RNS.log(
            f"Peer disconnected ({len(self._active_links)} active)",
            RNS.LOG_INFO
        )

    # ─── Request Handlers ───────────────────────────────────────────────────
    # All handlers receive ghost_hash in the request data payload.

    def _resolve_entry(self, data):
        """Extract ghost_hash from request data and find matching entry."""
        if data is None:
            return None, None

        try:
            payload = umsgpack.unpackb(data)
            if isinstance(payload, dict):
                ghost_hash = payload.get("ghost_hash", "")
            else:
                return None, None
        except Exception:
            return None, None

        entry = self.get_entry(ghost_hash)
        return entry, payload

    def _handle_manifest_request(self, path, data, request_id,
                                  link_id, remote_identity, requested_at):
        """
        Handle a manifest request — return the full ghost metadata.
        Request data: {ghost_hash: "..."}
        """
        entry, payload = self._resolve_entry(data)
        if not entry:
            RNS.log("Manifest request: unknown ghost_hash", RNS.LOG_WARNING)
            return None

        RNS.log(f"Serving manifest for {entry.name}", RNS.LOG_DEBUG)

        # Collect all known seeder dests
        all_dests = set(entry.ghost.seeder_dests)
        if self._destination:
            all_dests.add(self._destination.hash.hex())

        manifest = {
            "ghost_version": entry.ghost.version,
            "name": entry.ghost.name,
            "file_size": entry.ghost.file_size,
            "chunk_size": entry.ghost.chunk_size,
            "chunk_count": entry.ghost.chunk_count,
            "file_hash": entry.ghost.file_hash,
            "chunk_hashes": entry.ghost.chunk_hashes,
            "created_at": entry.ghost.created_at,
            "created_by": entry.ghost.created_by,
            "comment": entry.ghost.comment,
            "seeder_dest": entry.ghost.seeder_dest,
            "seeder_dests": list(all_dests),
        }

        RNS.log(
            f"Manifest response includes {len(all_dests)} seeder dest(s)",
            RNS.LOG_DEBUG
        )
        return umsgpack.packb(manifest)

    def _handle_chunk_request(self, path, data, request_id,
                               link_id, remote_identity, requested_at):
        """
        Handle a chunk request — return the requested chunk data.
        Request data: {ghost_hash: "...", index: <int>}
        """
        entry, payload = self._resolve_entry(data)
        if not entry or not payload:
            RNS.log("Chunk request: unknown ghost_hash", RNS.LOG_WARNING)
            return None

        try:
            chunk_index = payload.get("index")
            if not isinstance(chunk_index, int):
                RNS.log("Invalid chunk request: non-integer index",
                        RNS.LOG_WARNING)
                return None

            if chunk_index < 0 or chunk_index >= entry.ghost.chunk_count:
                RNS.log(
                    f"Invalid chunk request: index {chunk_index} "
                    f"out of range (0-{entry.ghost.chunk_count - 1})",
                    RNS.LOG_WARNING
                )
                return None

            chunk_data = entry.chunker.read_chunk(
                chunk_index, entry.source_path
            )

            if chunk_data:
                entry.chunks_served += 1
                entry.total_uploaded += len(chunk_data)

                RNS.log(
                    f"Serving {entry.name} chunk "
                    f"{chunk_index}/{entry.ghost.chunk_count - 1} "
                    f"({len(chunk_data)} bytes)",
                    RNS.LOG_DEBUG
                )

                return umsgpack.packb({
                    "index": chunk_index,
                    "data": chunk_data,
                })
            else:
                RNS.log(
                    f"Failed to read chunk {chunk_index}",
                    RNS.LOG_ERROR
                )
                return None

        except Exception as e:
            RNS.log(f"Chunk request error: {e}", RNS.LOG_ERROR)
            return None

    def _handle_status_request(self, path, data, request_id,
                                link_id, remote_identity, requested_at):
        """
        Handle a status request — return seeder status info.
        Request data: {ghost_hash: "..."}
        """
        entry, payload = self._resolve_entry(data)
        if not entry:
            RNS.log("Status request: unknown ghost_hash", RNS.LOG_WARNING)
            return None

        uptime = time.time() - entry.start_time if entry.start_time else 0

        status = {
            "name": entry.ghost.name,
            "ghost_hash": entry.ghost.ghost_hash,
            "file_size": entry.ghost.file_size,
            "chunk_count": entry.ghost.chunk_count,
            "chunks_available": entry.ghost.chunk_count,
            "peers": len(self._active_links),
            "total_uploaded": entry.total_uploaded,
            "chunks_served": entry.chunks_served,
            "uptime": int(uptime),
        }
        return umsgpack.packb(status)

    def _handle_peers_request(self, path, data, request_id,
                               link_id, remote_identity, requested_at):
        """
        Handle PEX (Peer Exchange) request — return known seeder dests.
        Request data: {ghost_hash: "..."}
        """
        entry, payload = self._resolve_entry(data)
        if not entry:
            RNS.log("Peers request: unknown ghost_hash", RNS.LOG_WARNING)
            return None

        peers = set(entry.ghost.seeder_dests)
        if self._destination:
            peers.add(self._destination.hash.hex())

        RNS.log(
            f"PEX: sharing {len(peers)} peer(s) for "
            f"{entry.ghost.ghost_hash[:16]}...",
            RNS.LOG_DEBUG
        )
        return umsgpack.packb({"peers": list(peers)})

    def _handle_catalog_request(self, path, data, request_id,
                                 link_id, remote_identity, requested_at):
        """
        Handle catalog request — return all ghost_hashes this node seeds.
        This lets a leecher discover what files are available from this node.
        Request data: None or {}
        """
        hashes = self.get_ghost_hashes()

        catalog = []
        for gh in hashes:
            entry = self.get_entry(gh)
            if entry:
                catalog.append({
                    "ghost_hash": gh,
                    "name": entry.ghost.name,
                    "file_size": entry.ghost.file_size,
                    "chunk_count": entry.ghost.chunk_count,
                })

        RNS.log(
            f"Catalog: sharing {len(catalog)} file(s)",
            RNS.LOG_DEBUG
        )
        return umsgpack.packb({"files": catalog})


class Seeder:
    """
    Backward-compatible wrapper around SeedManager.

    The CLI and engine can continue creating Seeder instances.
    Internally, all Seeders share a single SeedManager (and thus
    a single RNS Destination).
    """

    # Class-level shared manager (one per process)
    _shared_manager = None
    _manager_lock = threading.Lock()

    def __init__(self, ghost, source_path, network, identity):
        """
        Initialize a seeder for a specific file.

        Args:
            ghost: A GhostFile instance.
            source_path: Path to the actual file being seeded.
            network: A PhantomNetwork instance.
            identity: A PhantomIdentity instance.
        """
        self.ghost = ghost
        self.source_path = source_path
        self.network = network
        self.identity = identity

        self._entry = None
        self._running = False

        # Get or create the shared manager
        with Seeder._manager_lock:
            if Seeder._shared_manager is None:
                Seeder._shared_manager = SeedManager(network, identity)
            self._manager = Seeder._shared_manager

    @property
    def _destination(self):
        """Proxy to the shared manager's destination."""
        return self._manager._destination

    @property
    def destination_hash(self):
        """Get the destination hash for this seeder."""
        return self._manager.destination_hash

    @property
    def destination_hash_hex(self):
        """Get the destination hash as a hex string."""
        return self._manager.destination_hash_hex

    def start(self, announce_delay=0):
        """
        Start seeding the file via the shared SeedManager.

        Args:
            announce_delay: Legacy parameter, ignored (single announce handles all).
        """
        if self._running:
            RNS.log("Seeder already running", RNS.LOG_WARNING)
            return

        self._entry = self._manager.add(self.ghost, self.source_path)
        self._running = True

    def stop(self):
        """Stop seeding this file."""
        self._running = False
        if self._entry:
            self._manager.remove(self.ghost.ghost_hash)
            self._entry = None

    def get_stats(self):
        """Get seeder statistics for display."""
        if self._entry:
            stats = self._entry.get_stats()
            stats["destination"] = (
                RNS.prettyhexrep(self._manager.destination_hash)
                if self._manager.destination_hash else "N/A"
            )
            stats["active_peers"] = len(self._manager._active_links)
            stats["total_peers_connected"] = self._entry.peers_connected
            stats["running"] = self._running
            return stats

        return {
            "name": self.ghost.name if self.ghost else "Unknown",
            "ghost_hash": self.ghost.ghost_hash if self.ghost else "",
            "destination": "N/A",
            "active_peers": 0,
            "total_peers_connected": 0,
            "chunks_served": 0,
            "total_uploaded": 0,
            "total_uploaded_human": "0 B",
            "uptime_seconds": 0,
            "running": False,
        }

    @classmethod
    def reset_shared_manager(cls):
        """Reset the shared manager (for testing or shutdown)."""
        with cls._manager_lock:
            if cls._shared_manager:
                cls._shared_manager.stop()
                cls._shared_manager = None
