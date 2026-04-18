"""
Reticulum Phantom — Seeder Node

The seeder registers its file availability and passively listens
for leecher 'want' announcements on the Reticulum mesh. When a
matching want is detected, the seeder re-announces itself so the
leecher can discover and connect to it.

Flow:
  1. Load identity + parse .ghost file
  2. Create RNS.Destination for the ghost hash
  3. Register request handlers (manifest, chunk, status)
  4. Announce ONCE on the mesh (for routing)
  5. Register with WantAnnounceHandler (passive listening)
  6. When a want matches, re-announce so leecher connects to us
  7. Serve incoming chunk requests via RNS.Resource
"""

import os
import time
import random
import threading

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config
from .identity import PhantomIdentity
from .ghost_file import GhostFile
from .chunker import Chunker
from .network import PhantomNetwork


class WantAnnounceHandler:
    """
    Global singleton that listens for leecher 'want' announcements.

    All seeders register with this handler. When a leecher announces
    "I want ghost_hash X", the handler dispatches to the matching
    seeder which then connects back to the leecher.
    """

    _instance = None
    _seeders = {}    # ghost_hash → Seeder instance
    _lock = threading.Lock()
    _registered = False

    @classmethod
    def register_seeder(cls, ghost_hash, seeder):
        """Register a seeder to respond to wants for a ghost_hash."""
        with cls._lock:
            cls._seeders[ghost_hash] = seeder

        # Register the handler singleton with RNS Transport (once)
        if not cls._registered:
            cls._instance = cls()
            RNS.Transport.register_announce_handler(cls._instance)
            cls._registered = True
            RNS.log(
                "WantAnnounceHandler registered — listening for wants",
                RNS.LOG_INFO
            )

    @classmethod
    def unregister_seeder(cls, ghost_hash):
        """Remove a seeder from the handler."""
        with cls._lock:
            cls._seeders.pop(ghost_hash, None)

    def __init__(self):
        # Catch ALL announces and filter manually by app_data.
        # Using aspect_filter = None ensures we don't miss announces
        # due to RNS name-hash mismatch with dynamic aspects.
        self.aspect_filter = None
        self.receive_path_responses = True  # Also catch path responses

    def received_announce(self, destination_hash, announced_identity, app_data):
        """Called by RNS when ANY announcement is received."""
        if not app_data:
            return

        try:
            metadata = umsgpack.unpackb(app_data)

            # Only process dict payloads (Phantom announces)
            # Other RNS apps send int/list/str — ignore gracefully
            if not isinstance(metadata, dict):
                return

            announce_type = metadata.get("type", "")
            ghost_hash = metadata.get("ghost_hash", "")

            # Handle "want" announces — respond to leechers
            if announce_type == "want":
                with self._lock:
                    seeder = self._seeders.get(ghost_hash)
                if seeder:
                    RNS.log(
                        f"Leecher wants {ghost_hash[:16]}... — "
                        f"we have it! Responding...",
                        RNS.LOG_INFO
                    )
                    threading.Thread(
                        target=seeder._respond_to_want,
                        args=(destination_hash,),
                        daemon=True
                    ).start()
                return

            # Handle "seeder" announces — discover peer seeders
            if announce_type == "seeder" and ghost_hash:
                with self._lock:
                    seeder = self._seeders.get(ghost_hash)
                if seeder:
                    new_dest = destination_hash.hex()
                    own_dest = seeder._destination.hash.hex() if seeder._destination else ""
                    # Don't add ourselves
                    if new_dest != own_dest and new_dest not in seeder.ghost.seeder_dests:
                        seeder.ghost.seeder_dests.append(new_dest)
                        seeder.ghost.save()
                        RNS.log(
                            f"Discovered peer seeder for {ghost_hash[:16]}... "
                            f"→ {new_dest[:16]}... "
                            f"(now {len(seeder.ghost.seeder_dests)} known)",
                            RNS.LOG_INFO
                        )
                        # Re-announce so the new peer discovers us back
                        if seeder._destination and seeder._running:
                            try:
                                seeder._destination.announce(
                                    app_data=seeder._make_announce_data()
                                )
                                RNS.log(
                                    f"Re-announced to notify peer {new_dest[:16]}...",
                                    RNS.LOG_DEBUG
                                )
                            except Exception:
                                pass
        except Exception as e:
            RNS.log(
                f"Error processing want announce: {e}",
                RNS.LOG_WARNING
            )


class Seeder:
    """
    Seeds a file on the Reticulum mesh.

    Creates an RNS destination for the file's ghost hash,
    announces its availability, and serves chunks to peers.
    """

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
        self.chunker = Chunker(ghost)

        self._destination = None
        self._active_links = []
        self._running = False
        self._announce_thread = None

        # Stats
        self.total_uploaded = 0
        self.chunks_served = 0
        self.peers_connected = 0
        self.start_time = 0

    @property
    def destination_hash(self):
        """Get the destination hash for this seeder."""
        if self._destination:
            return self._destination.hash
        return None

    @property
    def destination_hash_hex(self):
        """Get the destination hash as a hex string."""
        if self._destination:
            return RNS.hexrep(self._destination.hash, delimit=False)
        return None

    def start(self, announce_delay=0):
        """
        Start seeding the file.

        Creates the RNS destination, registers handlers,
        and registers with the WantAnnounceHandler to
        passively respond to leecher "want" announcements.

        Args:
            announce_delay: Seconds delay (legacy, kept for API compat).
        """
        if self._running:
            RNS.log("Seeder already running", RNS.LOG_WARNING)
            return

        # Create the destination for this ghost
        self._destination = RNS.Destination(
            self.identity.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            config.RNS_APP_NAME,
            "swarm",
            self.ghost.ghost_hash
        )

        # Register request handlers
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

        # Set up link callbacks
        self._destination.set_link_established_callback(
            self._on_link_established
        )

        self._running = True
        self.start_time = time.time()

        # Save destination hash into ghost file for fast leecher discovery
        dest_hex = self._destination.hash.hex()
        self.ghost.seeder_dest = dest_hex

        # Add to seeder list (accumulates across re-seeds)
        if dest_hex not in self.ghost.seeder_dests:
            self.ghost.seeder_dests.append(dest_hex)

        # Always save ghost file NEXT TO the source file (primary location)
        # This is where users expect to find it for sharing
        if self.ghost.source_path:
            src_ghost = self.ghost.source_path + config.GHOST_EXTENSION
            self.ghost.save(src_ghost)
            RNS.log(
                f"Ghost file saved: {src_ghost}",
                RNS.LOG_INFO
            )

        # Also save to the library (backup / catalog)
        config.ensure_directories()
        library_path = os.path.join(
            config.GHOSTS_DIR,
            self.ghost.name + config.GHOST_EXTENSION
        )
        self.ghost.save(library_path)

        RNS.log(
            f"Seeding: {self.ghost.name} | "
            f"Hash: {self.ghost.ghost_hash} | "
            f"Destination: {RNS.prettyhexrep(self._destination.hash)}",
            RNS.LOG_INFO
        )

        # Initial announce so the mesh can route link requests to us
        if announce_delay > 0:
            time.sleep(announce_delay)

        self._destination.announce(app_data=self._make_announce_data())

        # Register with the global want-announce handler
        # (works on platforms with rnsd, e.g. Ubuntu)
        WantAnnounceHandler.register_seeder(
            self.ghost.ghost_hash, self
        )

        # Start infrequent re-announce loop (every 3h default)
        # Needed because announce handlers don't fire on all platforms
        self._announce_thread = threading.Thread(
            target=self._announce_loop, daemon=True
        )
        self._announce_thread.start()

    def _announce_loop(self):
        """Infrequent re-announce loop so leechers can discover us.

        The initial announce happens in start(). This loop handles
        periodic heartbeat re-announces (every 3h by default).
        """
        interval = config.DEFAULT_ANNOUNCE_INTERVAL
        # Sleep in 1s increments so we can stop quickly
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
                        RNS.log(
                            f"Re-announced: {self.ghost.name}",
                            RNS.LOG_DEBUG
                        )
                    except Exception:
                        pass

    def stop(self):
        """Stop seeding and tear down all links."""
        self._running = False

        # Unregister from want handler
        WantAnnounceHandler.unregister_seeder(self.ghost.ghost_hash)

        for link in self._active_links:
            try:
                link.teardown()
            except Exception:
                pass

        self._active_links.clear()

        # Deregister destination from RNS Transport so it can be re-registered
        if self._destination:
            try:
                RNS.Transport.deregister_destination(self._destination)
            except Exception:
                # Fallback: manually remove from destinations list
                try:
                    with RNS.Transport.destinations_lock:
                        RNS.Transport.destinations = [
                            d for d in RNS.Transport.destinations
                            if d.hash != self._destination.hash
                        ]
                except Exception:
                    pass
            self._destination = None

        RNS.log(f"Stopped seeding: {self.ghost.name}", RNS.LOG_INFO)

    def _make_announce_data(self):
        """Build announce app_data with a nonce to prevent deduplication.

        RNS silently drops re-announces with identical app_data from
        the same destination. Adding a timestamp ensures each announce
        is unique and propagates to all peers.
        """
        return umsgpack.packb({
            "ghost_hash": self.ghost.ghost_hash,
            "type": "seeder",
            "t": int(time.time()),  # nonce — prevents dedup
        })

    def _respond_to_want(self, leecher_dest_hash):
        """
        Respond to a leecher's 'want' announcement.

        Strategy:
        1. Request path to the leecher (triggers PATH_RESPONSE, not rate-limited)
        2. Then re-announce our destination (may be rate-limited, but path
           request ensures the leecher can discover our path regardless)
        """
        if not self._running or not self._destination:
            return

        try:
            # Step 1: Request path to leecher — this triggers a PATH_RESPONSE
            # on the mesh, which is NOT subject to announce rate limiting.
            # The leecher will see our path appear in the path table.
            if not RNS.Transport.has_path(leecher_dest_hash):
                RNS.Transport.request_path(leecher_dest_hash)

            # Step 2: Re-announce (may be rate-limited by Hub, but worth trying)
            self._destination.announce(app_data=self._make_announce_data())

            RNS.log(
                f"Responded to want for {self.ghost.name} — "
                f"re-announced dest {self._destination.hash.hex()[:16]}...",
                RNS.LOG_INFO
            )

        except Exception as e:
            RNS.log(
                f"Failed to respond to want: {e}",
                RNS.LOG_WARNING
            )

    def _on_link_established(self, link):
        """Called when a leecher connects."""
        self._active_links.append(link)
        self.peers_connected += 1

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

    def _handle_manifest_request(self, path, data, request_id,
                                  link_id, remote_identity, requested_at):
        """
        Handle a manifest request — return the full ghost metadata.
        This lets a leecher who only has the hash get the full .ghost info.
        Includes ALL known seeder dests (from ghost file + live seeders).
        """
        RNS.log("Serving manifest request", RNS.LOG_DEBUG)

        # Collect all known seeder dests — from ghost file + all live seeders
        all_dests = set(self.ghost.seeder_dests)
        # Add our own dest
        if self._destination:
            all_dests.add(self._destination.hash.hex())
        # Add dests from all other live seeders for this ghost hash
        with WantAnnounceHandler._lock:
            for gh, seeder in WantAnnounceHandler._seeders.items():
                if seeder._destination:
                    all_dests.add(seeder._destination.hash.hex())

        manifest = {
            "ghost_version": self.ghost.version,
            "name": self.ghost.name,
            "file_size": self.ghost.file_size,
            "chunk_size": self.ghost.chunk_size,
            "chunk_count": self.ghost.chunk_count,
            "file_hash": self.ghost.file_hash,
            "chunk_hashes": self.ghost.chunk_hashes,
            "created_at": self.ghost.created_at,
            "created_by": self.ghost.created_by,
            "comment": self.ghost.comment,
            # Share ALL known seeder dests so leecher can connect to swarm
            "seeder_dest": self.ghost.seeder_dest,
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

        The request data contains the chunk index as a msgpack int.
        """
        try:
            chunk_index = umsgpack.unpackb(data)

            if not isinstance(chunk_index, int):
                RNS.log("Invalid chunk request: non-integer index",
                        RNS.LOG_WARNING)
                return None

            if chunk_index < 0 or chunk_index >= self.ghost.chunk_count:
                RNS.log(
                    f"Invalid chunk request: index {chunk_index} "
                    f"out of range (0-{self.ghost.chunk_count - 1})",
                    RNS.LOG_WARNING
                )
                return None

            # Read the chunk
            chunk_data = self.chunker.read_chunk(chunk_index, self.source_path)

            if chunk_data:
                self.chunks_served += 1
                self.total_uploaded += len(chunk_data)

                RNS.log(
                    f"Serving chunk {chunk_index}/{self.ghost.chunk_count - 1} "
                    f"({len(chunk_data)} bytes)",
                    RNS.LOG_DEBUG
                )

                # Pack with index for verification on the other end
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
        """
        uptime = time.time() - self.start_time if self.start_time else 0

        status = {
            "name": self.ghost.name,
            "ghost_hash": self.ghost.ghost_hash,
            "file_size": self.ghost.file_size,
            "chunk_count": self.ghost.chunk_count,
            "chunks_available": self.ghost.chunk_count,  # We have all chunks
            "peers": len(self._active_links),
            "total_uploaded": self.total_uploaded,
            "chunks_served": self.chunks_served,
            "uptime": int(uptime),
        }

        return umsgpack.packb(status)

    def get_stats(self):
        """Get seeder statistics for display."""
        uptime = time.time() - self.start_time if self.start_time else 0

        return {
            "name": self.ghost.name,
            "ghost_hash": self.ghost.ghost_hash,
            "destination": RNS.prettyhexrep(self._destination.hash) if self._destination else "N/A",
            "active_peers": len(self._active_links),
            "total_peers_connected": self.peers_connected,
            "chunks_served": self.chunks_served,
            "total_uploaded": self.total_uploaded,
            "total_uploaded_human": GhostFile._human_size(self.total_uploaded),
            "uptime_seconds": int(uptime),
            "running": self._running,
        }
