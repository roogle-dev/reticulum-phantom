"""
Reticulum Phantom — Seeder Node

The seeder announces a file's availability on the Reticulum mesh
and serves chunk requests from leechers over encrypted RNS.Link
connections.

Flow:
  1. Load identity + parse .ghost file
  2. Create RNS.Destination for the ghost hash
  3. Register request handlers (manifest, chunk, status)
  4. Announce on the mesh
  5. Serve incoming chunk requests via RNS.Resource
"""

import os
import time
import threading

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config
from .identity import PhantomIdentity
from .ghost_file import GhostFile
from .chunker import Chunker
from .network import PhantomNetwork


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

    def start(self):
        """
        Start seeding the file.

        Creates the RNS destination, registers handlers,
        and announces on the mesh.
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

        # Announce on the mesh — only ghost_hash for privacy
        # File name/size details are shared only over encrypted links
        announce_data = umsgpack.packb({
            "ghost_hash": self.ghost.ghost_hash,
        })
        self._destination.announce(app_data=announce_data)

        self._running = True
        self.start_time = time.time()

        # Save destination hash into ghost file for fast leecher discovery
        dest_hex = self._destination.hash.hex()
        self.ghost.seeder_dest = dest_hex
        # Re-save ghost to library so the .ghost file includes the dest hash
        config.ensure_directories()
        library_path = os.path.join(
            config.GHOSTS_DIR,
            self.ghost.name + config.GHOST_EXTENSION
        )
        self.ghost.save(library_path)
        # Also update the source-adjacent ghost if it exists
        if self.ghost.source_path:
            src_ghost = self.ghost.source_path + config.GHOST_EXTENSION
            if os.path.isfile(src_ghost):
                self.ghost.save(src_ghost)

        RNS.log(
            f"Seeding: {self.ghost.name} | "
            f"Hash: {self.ghost.ghost_hash} | "
            f"Destination: {RNS.prettyhexrep(self._destination.hash)}",
            RNS.LOG_INFO
        )

        # Start periodic re-announce thread
        settings = config.load_settings()
        interval = settings.get("announce_interval",
                                config.DEFAULT_ANNOUNCE_INTERVAL)
        self._announce_thread = threading.Thread(
            target=self._announce_loop,
            args=(interval,),
            daemon=True
        )
        self._announce_thread.start()

    def stop(self):
        """Stop seeding and tear down all links."""
        self._running = False

        for link in self._active_links:
            try:
                link.teardown()
            except Exception:
                pass

        self._active_links.clear()
        RNS.log(f"Stopped seeding: {self.ghost.name}", RNS.LOG_INFO)

    def _announce_loop(self, interval):
        """Periodically re-announce on the mesh."""
        while self._running:
            time.sleep(interval)
            if self._running and self._destination:
                try:
                    announce_data = umsgpack.packb({
                        "ghost_hash": self.ghost.ghost_hash,
                    })
                    self._destination.announce(app_data=announce_data)
                    RNS.log(
                        f"Re-announced: {self.ghost.name}",
                        RNS.LOG_DEBUG
                    )
                except Exception as e:
                    RNS.log(
                        f"Re-announce failed: {e}",
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
        """
        RNS.log("Serving manifest request", RNS.LOG_DEBUG)

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
        }

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
