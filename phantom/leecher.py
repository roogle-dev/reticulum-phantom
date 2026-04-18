"""
Reticulum Phantom — Leecher / Downloader Node

The leecher discovers seeders on the mesh, establishes encrypted
links, and downloads file chunks. After completing a download,
it can automatically transition to seeding.

Flow:
  1. Resolve ghost hash → find seeder on mesh
  2. Establish encrypted RNS.Link
  3. Request manifest (if only have hash, not .ghost file)
  4. Request missing chunks sequentially
  5. Verify each chunk hash
  6. Assemble final file when all chunks received
"""

import os
import time
import threading

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config
from .ghost_file import GhostFile
from .chunker import Chunker


class Leecher:
    """
    Downloads a file from the Reticulum mesh.

    Can be initialized with either:
      - A .ghost file path (full metadata available)
      - A ghost hash string (will fetch manifest from seeder)
    """

    # Download states
    STATE_IDLE = "idle"
    STATE_DISCOVERING = "discovering"
    STATE_CONNECTING = "connecting"
    STATE_FETCHING_MANIFEST = "fetching_manifest"
    STATE_DOWNLOADING = "downloading"
    STATE_ASSEMBLING = "assembling"
    STATE_COMPLETE = "complete"
    STATE_FAILED = "failed"

    def __init__(self, network, identity):
        """
        Initialize the leecher.

        Args:
            network: A PhantomNetwork instance.
            identity: A PhantomIdentity instance.
        """
        self.network = network
        self.identity = identity

        self.ghost = None
        self.chunker = None
        self.ghost_hash = None

        self._link = None
        self._state = self.STATE_IDLE
        self._error = None
        self._running = False

        # Progress tracking
        self.chunks_received = 0
        self.total_chunks = 0
        self.bytes_received = 0
        self.start_time = 0
        self.end_time = 0

        # Callbacks for UI updates
        self.on_progress = None      # Called with (chunks_done, total, bytes)
        self.on_state_change = None  # Called with (new_state, info)
        self.on_complete = None      # Called with (output_path)
        self.on_error = None         # Called with (error_message)
        self.output_dir = None       # Custom output directory

    @property
    def state(self):
        return self._state

    @property
    def progress(self):
        """Get download progress as 0.0 to 1.0."""
        if self.total_chunks == 0:
            return 0.0
        return self.chunks_received / self.total_chunks

    @property
    def speed(self):
        """Get current download speed in bytes/second."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        if elapsed > 0:
            return self.bytes_received / elapsed
        return 0.0

    def _set_state(self, state, info=None):
        """Update state and notify callback."""
        self._state = state
        RNS.log(f"Download state: {state}" + (f" ({info})" if info else ""),
                RNS.LOG_DEBUG)
        if self.on_state_change:
            try:
                self.on_state_change(state, info)
            except Exception:
                pass

    def download_from_ghost(self, ghost_path):
        """
        Start download using a .ghost file.

        Args:
            ghost_path: Path to the .ghost file.
        """
        self.ghost = GhostFile.load(ghost_path)
        if not self.ghost:
            self._set_state(self.STATE_FAILED, "Invalid ghost file")
            return False

        self.ghost_hash = self.ghost.ghost_hash
        self.total_chunks = self.ghost.chunk_count
        self.chunker = Chunker(self.ghost)

        return self._start_download()

    def download_from_hash(self, ghost_hash):
        """
        Start download using only a ghost hash.
        Will fetch the manifest from the seeder.

        Args:
            ghost_hash: The ghost hash string (hex).
        """
        self.ghost_hash = ghost_hash
        return self._start_download()

    def _start_download(self):
        """Begin the download process in a background thread."""
        self._running = True
        self.start_time = time.time()

        thread = threading.Thread(
            target=self._download_worker,
            daemon=True
        )
        thread.start()
        return True

    def _download_worker(self):
        """Main download worker thread with seeder failover."""
        failed_dests = set()  # Track failed seeder dest hashes
        max_retries = 10

        for attempt in range(max_retries):
            if not self._running:
                return

            try:
                # Step 1: Discover a seeder (skipping failed ones)
                self._set_state(self.STATE_DISCOVERING)
                destination_hash = self._discover_seeder(
                    failed_dests=failed_dests
                )
                if not destination_hash:
                    return

                # Step 2: Connect to the seeder
                self._set_state(self.STATE_CONNECTING)
                link = self._connect_to_seeder(destination_hash)
                if not link:
                    # Connection failed — blacklist this seeder and retry
                    failed_dests.add(destination_hash)
                    dest_hex = destination_hash.hex()
                    RNS.log(
                        f"Seeder {dest_hex[:16]}... failed, "
                        f"searching for another seeder...",
                        RNS.LOG_WARNING
                    )
                    # Clean up failed link
                    if self._link:
                        try:
                            self._link.teardown()
                        except Exception:
                            pass
                        self._link = None
                    time.sleep(2)
                    continue  # Retry with another seeder

                # Step 3: Get manifest if we don't have it
                if not self.ghost:
                    self._set_state(self.STATE_FETCHING_MANIFEST)
                    if not self._fetch_manifest(link):
                        failed_dests.add(destination_hash)
                        if self._link:
                            try:
                                self._link.teardown()
                            except Exception:
                                pass
                            self._link = None
                        time.sleep(2)
                        continue

                # Step 4: Download missing chunks
                self._set_state(self.STATE_DOWNLOADING)
                if not self._download_chunks(link):
                    # Download interrupted — try another seeder
                    if self._running and self.chunks_received < self.total_chunks:
                        failed_dests.add(destination_hash)
                        RNS.log(
                            f"Download interrupted at chunk "
                            f"{self.chunks_received}/{self.total_chunks}, "
                            f"trying another seeder...",
                            RNS.LOG_WARNING
                        )
                        if self._link:
                            try:
                                self._link.teardown()
                            except Exception:
                                pass
                            self._link = None
                        time.sleep(2)
                        continue
                    return

                # Step 5: Assemble the file
                self._set_state(self.STATE_ASSEMBLING)
                output = None
                if self.output_dir:
                    os.makedirs(self.output_dir, exist_ok=True)
                    output = os.path.join(self.output_dir, self.ghost.name)
                output_path = self.chunker.assemble(output)

                if output_path:
                    self.end_time = time.time()
                    self._set_state(self.STATE_COMPLETE, output_path)
                    self.chunker.cleanup()

                    if self.on_complete:
                        self.on_complete(output_path)

                    RNS.log(
                        f"Download complete: {output_path}",
                        RNS.LOG_INFO
                    )
                else:
                    self._fail("File assembly failed — hash mismatch")
                return  # Success or assembly failure — done

            except Exception as e:
                RNS.log(f"Download attempt {attempt+1} error: {e}",
                        RNS.LOG_ERROR)
                if self._link:
                    try:
                        self._link.teardown()
                    except Exception:
                        pass
                    self._link = None
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                self._fail(f"Download error after {max_retries} attempts: {e}")
            finally:
                if self._state not in (self.STATE_COMPLETE, self.STATE_FAILED,
                                       self.STATE_DISCOVERING, self.STATE_CONNECTING):
                    if self._link:
                        try:
                            self._link.teardown()
                        except Exception:
                            pass

    def _discover_seeder(self, failed_dests=None):
        """
        Find a seeder for the requested ghost_hash.
        Skips any seeders in failed_dests set.

        Uses three strategies:
        1. Use seeder_dest from ghost file (fastest)
        2. If input looks like a destination hash, try direct path request
        3. Listen for announces with matching ghost_hash in app_data

        Returns:
            Destination hash as bytes, or None if not found.
        """
        if failed_dests is None:
            failed_dests = set()

        input_hash = self.ghost_hash
        found_event = threading.Event()
        found_dest = [None]  # [destination_hash_bytes]

        class GhostAnnounceHandler:
            """RNS-compatible announce handler that filters by ghost_hash."""
            def __init__(self, target_ghost_hash):
                self.aspect_filter = config.RNS_APP_NAME + ".swarm"
                self._target = target_ghost_hash

            def received_announce(self, destination_hash, announced_identity, app_data):
                if app_data:
                    try:
                        metadata = umsgpack.unpackb(app_data)
                        gh = metadata.get("ghost_hash", "")
                        if gh == self._target:
                            # Skip failed seeders
                            if destination_hash in failed_dests:
                                RNS.log(
                                    f"Skipping failed seeder: "
                                    f"{RNS.prettyhexrep(destination_hash)}",
                                    RNS.LOG_DEBUG
                                )
                                return
                            RNS.log(
                                f"Found seeder via announce: {RNS.prettyhexrep(destination_hash)}",
                                RNS.LOG_INFO
                            )
                            found_dest[0] = destination_hash
                            found_event.set()
                    except Exception:
                        pass

        # Register announce handler
        handler = GhostAnnounceHandler(input_hash)
        RNS.Transport.register_announce_handler(handler)

        if failed_dests:
            RNS.log(
                f"Searching mesh for alternative seeder "
                f"({len(failed_dests)} failed)...",
                RNS.LOG_INFO
            )
        else:
            RNS.log(
                f"Searching mesh for ghost {input_hash[:16]}...",
                RNS.LOG_INFO
            )

        # Strategy 1: Use seeder_dest from ghost file (fastest)
        dest_hash = None
        if self.ghost and self.ghost.seeder_dest:
            try:
                candidate = bytes.fromhex(self.ghost.seeder_dest)
                if len(candidate) == 16 and candidate not in failed_dests:
                    RNS.log(
                        f"Using seeder dest from ghost file: {self.ghost.seeder_dest}",
                        RNS.LOG_INFO
                    )
                    RNS.Transport.request_path(candidate)
                    dest_hash = candidate
                elif candidate in failed_dests:
                    RNS.log(
                        f"Ghost file seeder {self.ghost.seeder_dest[:16]}... "
                        f"already failed, waiting for announce...",
                        RNS.LOG_INFO
                    )
            except (ValueError, Exception):
                pass

        # Strategy 2: If input_hash looks like a destination hash, try direct
        if not dest_hash:
            try:
                candidate = bytes.fromhex(input_hash)
                if len(candidate) == 16 and candidate not in failed_dests:
                    RNS.log(
                        f"Input looks like destination hash, requesting path...",
                        RNS.LOG_INFO
                    )
                    RNS.Transport.request_path(candidate)
                    dest_hash = candidate

                    # Try to recover ghost_hash from cached app_data
                    app_data = RNS.Identity.recall_app_data(candidate)
                    if app_data:
                        try:
                            metadata = umsgpack.unpackb(app_data)
                            real_ghost = metadata.get("ghost_hash")
                            if real_ghost:
                                RNS.log(
                                    f"Recovered ghost_hash from cache: {real_ghost}",
                                    RNS.LOG_INFO
                                )
                                self.ghost_hash = real_ghost
                                # Update announce handler target
                                handler._target = real_ghost
                        except Exception:
                            pass
                elif candidate in failed_dests:
                    RNS.log(
                        f"Direct hash already failed, waiting for announce...",
                        RNS.LOG_INFO
                    )
            except (ValueError, Exception):
                pass

        # Wait for discovery — patient retry loop
        retry_interval = 15
        last_request = time.time()
        attempt = 1

        RNS.log("Waiting for seeder (will keep retrying)...", RNS.LOG_INFO)

        while not found_event.is_set():
            # Check direct path (only if not in failed list)
            if dest_hash and dest_hash not in failed_dests:
                if RNS.Transport.has_path(dest_hash):
                    hops = RNS.Transport.hops_to(dest_hash)
                    RNS.log(
                        f"Path found via direct request! {hops} hops to seeder",
                        RNS.LOG_INFO
                    )
                    return dest_hash

            # Re-request path periodically
            if dest_hash and dest_hash not in failed_dests:
                if time.time() - last_request > retry_interval:
                    attempt += 1
                    RNS.log(
                        f"Re-requesting path (attempt {attempt})...",
                        RNS.LOG_INFO
                    )
                    RNS.Transport.request_path(dest_hash)
                    last_request = time.time()

            if not self._running:
                return None
            time.sleep(1)

        if found_event.is_set() and found_dest[0]:
            dest_hash = found_dest[0]
            # Request path to the announced destination
            if not RNS.Transport.has_path(dest_hash):
                RNS.Transport.request_path(dest_hash)
                path_start = time.time()
                while not RNS.Transport.has_path(dest_hash):
                    if time.time() - path_start > 60:
                        self._fail("Path request failed after announce")
                        return None
                    time.sleep(0.5)

            hops = RNS.Transport.hops_to(dest_hash)
            RNS.log(
                f"Path found via announce! {hops} hops to seeder",
                RNS.LOG_INFO
            )
            return dest_hash

        self._fail("Seeder not found on mesh")
        return None

    def _connect_to_seeder(self, destination_hash):
        """
        Establish an encrypted link to the seeder.

        Args:
            destination_hash: The seeder's destination hash as bytes.

        Returns:
            An RNS.Link instance, or None on failure.
        """
        # Recall the seeder's identity from the path we discovered
        seeder_identity = RNS.Identity.recall(destination_hash)

        if not seeder_identity:
            # Wait a moment and retry — announce may still be propagating
            RNS.log("Waiting for seeder identity to propagate...", RNS.LOG_INFO)
            time.sleep(3)
            seeder_identity = RNS.Identity.recall(destination_hash)

        if not seeder_identity:
            self._fail("Cannot recall seeder identity — the seeder may need to re-announce")
            return None

        RNS.log(f"Recalled seeder identity: {RNS.prettyhexrep(seeder_identity.hash)}", RNS.LOG_DEBUG)

        # Build the OUT destination matching the seeder's IN destination.
        # ghost_hash is used as an aspect so each file has a unique destination.
        seeder_destination = RNS.Destination(
            seeder_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            config.RNS_APP_NAME,
            "swarm",
            self.ghost_hash
        )

        # Verify the destination hash matches what we discovered
        if seeder_destination.hash != destination_hash:
            RNS.log(
                f"Destination hash mismatch: "
                f"expected {RNS.prettyhexrep(destination_hash)}, "
                f"built {RNS.prettyhexrep(seeder_destination.hash)}",
                RNS.LOG_WARNING
            )

        # Establish the encrypted link
        link_established = threading.Event()
        link_failed = threading.Event()

        def on_established(link):
            RNS.log("Link established callback fired", RNS.LOG_DEBUG)
            link_established.set()

        def on_closed(link):
            if not link_established.is_set():
                RNS.log("Link closed before establishment", RNS.LOG_WARNING)
                link_failed.set()

        RNS.log(
            f"Initiating link to {RNS.prettyhexrep(seeder_destination.hash)}...",
            RNS.LOG_INFO
        )

        self._link = RNS.Link(
            seeder_destination,
            established_callback=on_established,
            closed_callback=on_closed
        )

        # Wait for link establishment — check both events
        timeout = config.DEFAULT_LINK_TIMEOUT
        start = time.time()
        while time.time() - start < timeout:
            if link_established.is_set():
                RNS.log("Encrypted link established with seeder ✓", RNS.LOG_INFO)
                return self._link
            if link_failed.is_set():
                RNS.log(
                    "Link to seeder failed — connection rejected or dropped",
                    RNS.LOG_WARNING
                )
                return None
            time.sleep(0.5)

        # Timeout
        RNS.log(
            "Link establishment timed out — seeder unreachable",
            RNS.LOG_WARNING
        )
        return None

    def _fetch_manifest(self, link):
        """
        Request the full manifest from the seeder.

        Args:
            link: The established RNS.Link.

        Returns:
            True if manifest received and parsed successfully.
        """
        response_event = threading.Event()
        response_data = [None]  # Mutable container for callback
        response_failed = [False]

        def on_response(receipt):
            try:
                response_data[0] = receipt.response
                RNS.log(
                    f"Manifest response received ({len(response_data[0])} bytes)",
                    RNS.LOG_INFO
                )
            except Exception as e:
                RNS.log(f"Error reading manifest response: {e}", RNS.LOG_ERROR)
                response_failed[0] = True
            response_event.set()

        def on_failed(receipt):
            RNS.log(
                f"Manifest request failed (status: {receipt.status})",
                RNS.LOG_ERROR
            )
            response_failed[0] = True
            response_event.set()

        RNS.log("Sending manifest request...", RNS.LOG_INFO)

        receipt = link.request(
            "manifest",
            data=None,
            response_callback=on_response,
            failed_callback=on_failed,
            timeout=config.DEFAULT_TRANSFER_TIMEOUT
        )

        if not receipt:
            self._fail("Failed to send manifest request")
            return False

        RNS.log(
            f"Manifest request sent (status: {receipt.status})",
            RNS.LOG_DEBUG
        )

        # Wait for response via callback
        if not response_event.wait(config.DEFAULT_TRANSFER_TIMEOUT):
            self._fail("Manifest request timed out")
            return False

        if response_failed[0] or response_data[0] is None:
            self._fail("Manifest request failed — seeder did not respond")
            return False

        try:
            manifest = umsgpack.unpackb(response_data[0])

            self.ghost = GhostFile()
            self.ghost.version = manifest.get("ghost_version", 1)
            self.ghost.name = manifest.get("name", "unknown")
            self.ghost.file_size = manifest.get("file_size", 0)
            self.ghost.chunk_size = manifest.get("chunk_size",
                                                  config.DEFAULT_CHUNK_SIZE)
            self.ghost.chunk_count = manifest.get("chunk_count", 0)
            self.ghost.file_hash = manifest.get("file_hash", "")
            self.ghost.chunk_hashes = manifest.get("chunk_hashes", [])
            self.ghost.created_at = manifest.get("created_at", 0)
            self.ghost.created_by = manifest.get("created_by", "")
            self.ghost.comment = manifest.get("comment", "")

            self.total_chunks = self.ghost.chunk_count
            self.chunker = Chunker(self.ghost)

            # Save the ghost file locally
            self.ghost.save()

            RNS.log(
                f"Manifest received: {self.ghost.name} "
                f"({self.ghost.chunk_count} chunks, "
                f"{GhostFile._human_size(self.ghost.file_size)})",
                RNS.LOG_INFO
            )
            return True

        except Exception as e:
            self._fail(f"Failed to parse manifest: {e}")
            return False

    def _download_chunks(self, link):
        """
        Download all missing chunks sequentially.

        Args:
            link: The established RNS.Link.

        Returns:
            True if all chunks downloaded successfully.
        """
        missing = self.chunker.get_missing_chunks()

        if not missing:
            RNS.log("All chunks already available!", RNS.LOG_INFO)
            self.chunks_received = self.total_chunks
            return True

        RNS.log(
            f"Downloading {len(missing)} missing chunks "
            f"(have {self.total_chunks - len(missing)}/{self.total_chunks})",
            RNS.LOG_INFO
        )

        self.chunks_received = self.total_chunks - len(missing)

        for chunk_index in missing:
            if not self._running:
                return False

            if not self._download_single_chunk(link, chunk_index):
                # Retry once
                RNS.log(
                    f"Retrying chunk {chunk_index}...",
                    RNS.LOG_WARNING
                )
                time.sleep(1)
                if not self._download_single_chunk(link, chunk_index):
                    self._fail(f"Failed to download chunk {chunk_index}")
                    return False

        return True

    def _download_single_chunk(self, link, chunk_index):
        """
        Download a single chunk from the seeder.

        Args:
            link: The established RNS.Link.
            chunk_index: Index of the chunk to download.

        Returns:
            True if chunk downloaded and verified successfully.
        """
        response_event = threading.Event()
        response_data = [None]
        response_failed = [False]

        def on_response(receipt):
            try:
                response_data[0] = receipt.response
            except Exception as e:
                RNS.log(f"Error reading chunk response: {e}", RNS.LOG_ERROR)
                response_failed[0] = True
            response_event.set()

        def on_failed(receipt):
            RNS.log(f"Chunk {chunk_index} request failed", RNS.LOG_ERROR)
            response_failed[0] = True
            response_event.set()

        # Request the chunk
        request_data = umsgpack.packb(chunk_index)

        receipt = link.request(
            "chunk",
            data=request_data,
            response_callback=on_response,
            failed_callback=on_failed,
            timeout=config.DEFAULT_TRANSFER_TIMEOUT
        )

        if not receipt:
            RNS.log(f"Failed to send chunk request {chunk_index}",
                    RNS.LOG_ERROR)
            return False

        # Wait for response via callback
        if not response_event.wait(config.DEFAULT_TRANSFER_TIMEOUT):
            RNS.log(f"Chunk {chunk_index} request timed out", RNS.LOG_ERROR)
            return False

        if response_failed[0] or response_data[0] is None:
            RNS.log(f"Chunk {chunk_index} response failed", RNS.LOG_ERROR)
            return False

        try:
            chunk_response = umsgpack.unpackb(response_data[0])
            index = chunk_response["index"]
            data = chunk_response["data"]

            if index != chunk_index:
                RNS.log(
                    f"Chunk index mismatch: requested {chunk_index}, "
                    f"got {index}",
                    RNS.LOG_ERROR
                )
                return False

            # Save and verify
            if self.chunker.save_chunk(chunk_index, data):
                self.chunks_received += 1
                self.bytes_received += len(data)

                RNS.log(
                    f"Chunk {chunk_index + 1}/{self.total_chunks} "
                    f"received ({len(data)} bytes) "
                    f"[{self.progress * 100:.1f}%]",
                    RNS.LOG_DEBUG
                )

                # Notify progress callback
                if self.on_progress:
                    try:
                        self.on_progress(
                            self.chunks_received,
                            self.total_chunks,
                            self.bytes_received
                        )
                    except Exception:
                        pass

                return True
            else:
                return False

        except Exception as e:
            RNS.log(f"Failed to parse chunk response: {e}",
                    RNS.LOG_ERROR)
            return False

    def cancel(self):
        """Cancel the current download."""
        self._running = False
        self._set_state(self.STATE_FAILED, "Cancelled by user")

        if self._link:
            try:
                self._link.teardown()
            except Exception:
                pass

    def _fail(self, message):
        """Handle a download failure."""
        self._error = message
        self._running = False
        self._set_state(self.STATE_FAILED, message)
        RNS.log(f"Download failed: {message}", RNS.LOG_ERROR)

        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass

    def get_stats(self):
        """Get download statistics for display."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        if self.end_time:
            elapsed = self.end_time - self.start_time

        return {
            "state": self._state,
            "ghost_hash": self.ghost_hash,
            "name": self.ghost.name if self.ghost else "Unknown",
            "progress": self.progress,
            "progress_pct": f"{self.progress * 100:.1f}%",
            "chunks_received": self.chunks_received,
            "total_chunks": self.total_chunks,
            "bytes_received": self.bytes_received,
            "bytes_received_human": GhostFile._human_size(
                self.bytes_received) if self.ghost else "0 B",
            "speed": self.speed,
            "speed_human": f"{GhostFile._human_size(self.speed)}/s" if self.ghost else "0 B/s",
            "elapsed": elapsed,
            "error": self._error,
        }
