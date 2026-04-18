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
        """Main download worker thread with multi-peer swarm support."""
        failed_dests = set()
        attempt = 0

        while self._running:
            if not self._running:
                return

            try:
                # Step 1: Discover ALL seeders
                self._set_state(self.STATE_DISCOVERING)
                seeder_dests = self._discover_seeders(
                    failed_dests=failed_dests
                )
                if not seeder_dests:
                    return

                RNS.log(
                    f"Found {len(seeder_dests)} seeder(s), connecting...",
                    RNS.LOG_INFO
                )

                # Step 2: Connect to all seeders in parallel
                self._set_state(self.STATE_CONNECTING)
                active_links = self._connect_to_seeders(seeder_dests, failed_dests)

                if not active_links:
                    RNS.log(
                        "No seeders connected, retrying...",
                        RNS.LOG_WARNING
                    )
                    time.sleep(2)
                    continue

                RNS.log(
                    f"Connected to {len(active_links)} peer(s)",
                    RNS.LOG_INFO
                )

                # Step 3: Get manifest if needed (from first peer)
                if not self.ghost:
                    self._set_state(self.STATE_FETCHING_MANIFEST)
                    first_link = active_links[0][1]  # (dest_hash, link)
                    if not self._fetch_manifest(first_link):
                        for dh, lk in active_links:
                            failed_dests.add(dh)
                            try:
                                lk.teardown()
                            except Exception:
                                pass
                        time.sleep(2)
                        continue

                # Step 4: Download chunks from ALL peers
                self._set_state(self.STATE_DOWNLOADING)
                success = self._swarm_download(active_links, failed_dests)

                # Clean up all links
                for _, lk in active_links:
                    try:
                        lk.teardown()
                    except Exception:
                        pass

                if not success:
                    if self._running and self.chunks_received < self.total_chunks:
                        RNS.log(
                            f"Swarm download interrupted at "
                            f"{self.chunks_received}/{self.total_chunks}, "
                            f"retrying...",
                            RNS.LOG_WARNING
                        )
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
                return

            except Exception as e:
                attempt += 1
                RNS.log(f"Download attempt {attempt} error: {e}",
                        RNS.LOG_ERROR)
                time.sleep(3)

    def _connect_to_seeders(self, dest_hashes, failed_dests):
        """
        Connect to multiple seeders in parallel.

        Returns:
            List of (dest_hash_bytes, link) tuples for successful connections.
        """
        results = []
        lock = threading.Lock()
        threads = []

        def connect_one(dest_hash):
            link = self._connect_to_seeder(dest_hash)
            if link:
                with lock:
                    results.append((dest_hash, link))
            else:
                with lock:
                    failed_dests.add(dest_hash)
                RNS.log(
                    f"Peer {dest_hash.hex()[:16]}... connection failed",
                    RNS.LOG_WARNING
                )

        for dh in dest_hashes[:config.MAX_SWARM_PEERS]:
            t = threading.Thread(target=connect_one, args=(dh,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=config.DEFAULT_LINK_TIMEOUT + 5)

        return results

    def _swarm_download(self, active_links, failed_dests):
        """
        Download chunks from multiple peers simultaneously.

        Splits missing chunks into a shared queue. Each peer worker
        pulls chunks from the queue. If a peer dies, its remaining
        chunks go back to the queue for other peers.

        New seeders discovered during download are automatically added.

        Returns:
            True if all chunks downloaded successfully.
        """
        import queue

        missing = self.chunker.get_missing_chunks()
        if not missing:
            RNS.log("All chunks already available!", RNS.LOG_INFO)
            self.chunks_received = self.total_chunks
            return True

        RNS.log(
            f"Downloading {len(missing)} chunks from "
            f"{len(active_links)} peer(s)",
            RNS.LOG_INFO
        )

        self.chunks_received = self.total_chunks - len(missing)

        # Thread-safe chunk queue
        chunk_queue = queue.Queue()
        for idx in missing:
            chunk_queue.put(idx)

        # Track peer stats and active workers
        peer_stats = {}
        stats_lock = threading.Lock()
        all_done = threading.Event()
        active_peer_ids = set()  # Track connected peer dest hashes
        workers = []

        for dh, _ in active_links:
            active_peer_ids.add(dh)

        def peer_worker(dest_hash, link, peer_id):
            """Worker thread for one peer — pulls chunks from shared queue."""
            chunks_done = 0
            peer_hex = dest_hash.hex()[:16]

            while not all_done.is_set() and self._running:
                try:
                    chunk_index = chunk_queue.get(timeout=2)
                except queue.Empty:
                    # No more chunks in queue — check if done
                    if chunk_queue.empty():
                        break
                    continue

                # Download this chunk from our peer
                success = self._download_single_chunk(link, chunk_index)

                if success:
                    chunks_done += 1
                    with stats_lock:
                        peer_stats[peer_hex] = chunks_done

                    if self.chunks_received >= self.total_chunks:
                        all_done.set()
                        break
                else:
                    # Failed — put chunk back for another peer
                    chunk_queue.put(chunk_index)
                    RNS.log(
                        f"Peer {peer_hex}... failed on chunk {chunk_index}, "
                        f"reassigning to another peer",
                        RNS.LOG_WARNING
                    )
                    failed_dests.add(dest_hash)
                    break

            RNS.log(
                f"Peer {peer_hex}... finished: "
                f"{chunks_done} chunks downloaded",
                RNS.LOG_INFO
            )

        def discovery_watcher():
            """Background thread: re-announce wants to attract new seeders."""

            # Announce handler to catch new seeder responses during download
            class LiveSeederHandler:
                def __init__(self, target_hash):
                    self.aspect_filter = None
                    self._target = target_hash

                def received_announce(self, destination_hash, announced_identity, app_data):
                    if all_done.is_set():
                        return
                    if app_data:
                        try:
                            metadata = umsgpack.unpackb(app_data)
                            if not isinstance(metadata, dict):
                                return
                            if metadata.get("type") != "seeder":
                                return
                            gh = metadata.get("ghost_hash", "")
                            if gh == self._target:
                                if destination_hash not in active_peer_ids:
                                    RNS.log(
                                        f"New seeder discovered during download: "
                                        f"{RNS.prettyhexrep(destination_hash)}",
                                        RNS.LOG_INFO
                                    )
                                    # Request path then connect
                                    if not RNS.Transport.has_path(destination_hash):
                                        RNS.Transport.request_path(destination_hash)
                                    deadline = time.time() + 10
                                    while not RNS.Transport.has_path(destination_hash):
                                        if time.time() > deadline or all_done.is_set():
                                            return
                                        time.sleep(0.5)

                                    new_link = self._outer._connect_to_seeder(destination_hash)
                                    if new_link:
                                        active_peer_ids.add(destination_hash)
                                        peer_id = len(workers)
                                        t = threading.Thread(
                                            target=peer_worker,
                                            args=(destination_hash, new_link, peer_id),
                                            daemon=True
                                        )
                                        workers.append(t)
                                        t.start()
                                        RNS.log(
                                            f"New peer joined swarm! "
                                            f"Now {len(active_peer_ids)} peer(s)",
                                            RNS.LOG_INFO
                                        )
                        except Exception:
                            pass

            live_handler = LiveSeederHandler(self.ghost_hash)
            live_handler._outer = self
            RNS.Transport.register_announce_handler(live_handler)

            # Create want-destination for re-announces
            try:
                live_want_dest = RNS.Destination(
                    self.identity.identity,
                    RNS.Destination.IN,
                    RNS.Destination.SINGLE,
                    config.RNS_APP_NAME,
                    "want",
                    self.ghost_hash
                )
            except Exception:
                live_want_dest = None

            # Periodically re-announce want during download
            announce_data = umsgpack.packb({"ghost_hash": self.ghost_hash})
            while not all_done.is_set() and self._running:
                try:
                    if live_want_dest:
                        live_want_dest.announce(app_data=announce_data)
                except Exception:
                    pass
                # Wait before next re-announce
                for _ in range(config.WANT_ANNOUNCE_INTERVAL):
                    if all_done.is_set() or not self._running:
                        break
                    time.sleep(1)

            # Cleanup
            if live_want_dest:
                self._cleanup_want_dest(live_want_dest)

        # Start discovery watcher in background
        watcher = threading.Thread(target=discovery_watcher, daemon=True)
        watcher.start()

        # Start worker threads — one per peer
        for i, (dh, lk) in enumerate(active_links):
            t = threading.Thread(
                target=peer_worker,
                args=(dh, lk, i),
                daemon=True
            )
            workers.append(t)
            t.start()

        # Wait for all workers to finish (including dynamically added ones)
        while not all_done.is_set() and self._running:
            # Check if any workers are still alive
            alive = any(t.is_alive() for t in workers)
            if not alive and not chunk_queue.empty():
                # All workers dead but chunks remain — need more peers
                RNS.log(
                    f"All peers disconnected, {chunk_queue.qsize()} "
                    f"chunks remaining — waiting for new peers...",
                    RNS.LOG_WARNING
                )
                time.sleep(5)
                if not any(t.is_alive() for t in workers):
                    break  # No more workers, give up this round
            elif not alive:
                break  # All done
            time.sleep(1)

        all_done.set()  # Signal discovery watcher to stop

        # Check if we got everything
        if self.chunks_received >= self.total_chunks:
            return True
        elif chunk_queue.empty():
            return True
        else:
            RNS.log(
                f"Swarm incomplete: {chunk_queue.qsize()} chunks remaining",
                RNS.LOG_WARNING
            )
            return False

    def _discover_seeders(self, failed_dests=None):
        """
        Reverse discovery: announce 'I want ghost_hash X' and wait
        for seeders to respond by re-announcing themselves.

        The leecher never searches — it broadcasts demand and waits.
        Seeders passively listen and respond when they have the file.

        Returns:
            List of destination hash bytes, or empty list if none found.
        """
        if failed_dests is None:
            failed_dests = set()

        input_hash = self.ghost_hash
        found_dests = []    # Collected seeder dest hashes
        found_lock = threading.Lock()
        first_found = threading.Event()

        # ── Announce handler to catch seeder response-announces ──
        class SeederResponseHandler:
            """Catches seeder re-announces triggered by our want."""
            def __init__(self, target_ghost_hash):
                self.aspect_filter = None  # Catch ALL announces
                self.receive_path_responses = True  # Also catch path responses
                self._target = target_ghost_hash

            def received_announce(self, destination_hash, announced_identity, app_data):
                if app_data:
                    try:
                        metadata = umsgpack.unpackb(app_data)
                        # Only process dict payloads (Phantom announces)
                        if not isinstance(metadata, dict):
                            return
                        # Only match SEEDER announces, not other leechers' wants
                        if metadata.get("type") != "seeder":
                            return
                        gh = metadata.get("ghost_hash", "")
                        if gh == self._target:
                            if destination_hash not in failed_dests:
                                with found_lock:
                                    if destination_hash not in found_dests:
                                        found_dests.append(destination_hash)
                                        RNS.log(
                                            f"Seeder responded: "
                                            f"{RNS.prettyhexrep(destination_hash)} "
                                            f"({len(found_dests)} total)",
                                            RNS.LOG_INFO
                                        )
                                first_found.set()
                    except Exception:
                        pass

        response_handler = SeederResponseHandler(input_hash)
        RNS.Transport.register_announce_handler(response_handler)

        # ── Create want-destination ──────────────────────────────
        want_dest = RNS.Destination(
            self.identity.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            config.RNS_APP_NAME,
            "want",
            input_hash
        )

        # ── Hint dests from .ghost file (primary discovery) ────────
        # Try ALL known seeder destinations (like multiple trackers)
        hint_dests = []
        if self.ghost:
            dests_to_try = list(self.ghost.seeder_dests) if self.ghost.seeder_dests else []
            # Backward compat: also try seeder_dest if not in list
            if self.ghost.seeder_dest and self.ghost.seeder_dest not in [d for d in dests_to_try]:
                dests_to_try.insert(0, self.ghost.seeder_dest)

            for dest_hex in dests_to_try:
                try:
                    candidate = bytes.fromhex(dest_hex)
                    if len(candidate) == 16 and candidate not in failed_dests:
                        hint_dests.append(candidate)
                except (ValueError, Exception):
                    pass

        # ── Announce "I want X" (for announce-based discovery) ────
        announce_data = umsgpack.packb({
            "ghost_hash": input_hash,
            "type": "want",
            "t": int(time.time()),
        })
        want_dest.announce(app_data=announce_data)

        RNS.log(
            f"📢 Want announced: {input_hash[:16]}... "
            f"— waiting for seeders to respond",
            RNS.LOG_INFO
        )

        # ── Primary: try all known seeder destinations ────────────
        for hint_dest in hint_dests:
            if first_found.is_set():
                break  # Already found one, no need to try more

            RNS.log(
                f"Requesting path to seeder: "
                f"{hint_dest.hex()[:16]}...",
                RNS.LOG_INFO
            )
            # Block up to 15s per dest (shorter since we try multiple)
            path_found = RNS.Transport.await_path(
                hint_dest, timeout=15
            )
            if path_found:
                with found_lock:
                    if hint_dest not in found_dests:
                        found_dests.append(hint_dest)
                        RNS.log(
                            f"Seeder path resolved: "
                            f"{RNS.prettyhexrep(hint_dest)}",
                            RNS.LOG_INFO
                        )
                first_found.set()

        # ── Fallback: wait for announce handler responses ─────────
        last_announce = time.time()

        while not first_found.is_set() and self._running:
            # Re-announce want periodically
            if time.time() - last_announce > config.WANT_ANNOUNCE_INTERVAL:
                announce_data = umsgpack.packb({
                    "ghost_hash": input_hash,
                    "type": "want",
                    "t": int(time.time()),
                })
                want_dest.announce(app_data=announce_data)
                last_announce = time.time()

                # Re-request all hint paths
                for hd in hint_dests:
                    if first_found.is_set():
                        break
                    if hd not in failed_dests:
                        path_found = RNS.Transport.await_path(
                            hd, timeout=10
                        )
                        if path_found:
                            with found_lock:
                                if hd not in found_dests:
                                    found_dests.append(hd)
                                    RNS.log(
                                        f"Seeder path resolved: "
                                        f"{RNS.prettyhexrep(hd)}",
                                        RNS.LOG_INFO
                                    )
                            first_found.set()
                            break

                RNS.log(
                    f"📢 Re-announced want for {input_hash[:16]}...",
                    RNS.LOG_INFO
                )

            time.sleep(1)

        if not found_dests:
            # Only reach here if self._running became False (cancelled)
            self._cleanup_want_dest(want_dest)
            return []

        # ── Discovery window — collect more seeders ──────────────
        discovery_window = config.DEFAULT_DISCOVERY_WINDOW
        RNS.log(
            f"Seeder found! Waiting {discovery_window}s for more...",
            RNS.LOG_INFO
        )
        window_start = time.time()
        while time.time() - window_start < discovery_window:
            if not self._running:
                break
            time.sleep(1)

        # Request paths for all found destinations
        for dh in found_dests:
            if not RNS.Transport.has_path(dh):
                RNS.Transport.request_path(dh)

        # Wait for paths to resolve
        path_deadline = time.time() + 10
        for dh in list(found_dests):
            while not RNS.Transport.has_path(dh):
                if time.time() > path_deadline:
                    found_dests.remove(dh)
                    break
                time.sleep(0.5)

        # Cleanup want-destination
        self._cleanup_want_dest(want_dest)

        RNS.log(
            f"Discovery complete: {len(found_dests)} seeder(s) found",
            RNS.LOG_INFO
        )
        return found_dests

    def _cleanup_want_dest(self, want_dest):
        """Deregister a want-destination from RNS Transport."""
        try:
            RNS.Transport.deregister_destination(want_dest)
        except Exception:
            try:
                with RNS.Transport.destinations_lock:
                    RNS.Transport.destinations = [
                        d for d in RNS.Transport.destinations
                        if d.hash != want_dest.hash
                    ]
            except Exception:
                pass

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

        link = RNS.Link(
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
                return link
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
        """Cancel the current download. Workers exit via _running flag."""
        self._running = False
        self._set_state(self.STATE_FAILED, "Cancelled by user")

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
