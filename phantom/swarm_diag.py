"""
Phantom Swarm Diagnostic — Step-by-step test of the entire swarm chain.

Usage:
    python -m phantom.swarm_diag <ghost_file_or_hash>

Tests each phase of swarm formation and reports exactly where it breaks.
"""

import os
import sys
import time
import threading
import umsgpack
import RNS

from phantom import config
from phantom.ghost_file import GhostFile
from phantom.network import PhantomNetwork
from phantom.identity import PhantomIdentity


def header(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def ok(msg):
    print(f"  ✅ {msg}")


def fail(msg):
    print(f"  ❌ {msg}")


def info(msg):
    print(f"  ℹ️  {msg}")


def warn(msg):
    print(f"  ⚠️  {msg}")


def run_diagnostic(target):
    """Run full swarm diagnostic."""

    # ── Phase 1: Ghost File ──────────────────────────────────────
    header("PHASE 1: Ghost File Analysis")

    ghost = None
    ghost_hash = None

    if os.path.isfile(target):
        ghost = GhostFile.load(target)
        if ghost:
            ghost_hash = ghost.ghost_hash
            ok(f"Ghost loaded: {ghost.name}")
            info(f"Ghost hash: {ghost_hash}")
            info(f"File size: {ghost.file_size:,} bytes")
            info(f"Chunks: {ghost.chunk_count}")
            info(f"seeder_dest: {ghost.seeder_dest or 'NONE'}")
            info(f"seeder_dests ({len(ghost.seeder_dests)}):")
            for i, d in enumerate(ghost.seeder_dests):
                info(f"  [{i}] {d}")
            if not ghost.seeder_dests:
                fail("No seeder dests in ghost file — leecher has nothing to connect to!")
        else:
            fail(f"Could not load ghost file: {target}")
            return
    else:
        ghost_hash = target
        info(f"Using raw hash: {ghost_hash}")
        info("No ghost file — will test announce-based discovery only")

    # ── Phase 2: RNS Network ────────────────────────────────────
    header("PHASE 2: RNS Network")

    network = PhantomNetwork()
    network.start()
    ok("Reticulum started")

    pid = PhantomIdentity()
    pid.load()
    if not pid.is_loaded:
        pid.create_new()
    ok(f"Identity: {pid.hash_pretty}")

    # Check interfaces
    time.sleep(1)
    try:
        interfaces = RNS.Reticulum.get_instance().get_interface_stats()
        for iface in interfaces:
            name = iface.get("name", "?")
            status = iface.get("status", False)
            rxb = iface.get("rxb", 0)
            txb = iface.get("txb", 0)
            if status:
                ok(f"Interface: {name} (rx:{rxb} tx:{txb})")
            else:
                fail(f"Interface: {name} — DOWN")
    except Exception as e:
        warn(f"Could not get interface stats: {e}")

    # ── Phase 3: Path Resolution ────────────────────────────────
    header("PHASE 3: Path Resolution (are seeders reachable?)")

    if ghost and ghost.seeder_dests:
        for dest_hex in ghost.seeder_dests:
            try:
                dest_bytes = bytes.fromhex(dest_hex)
                # Check if path is cached
                already_cached = RNS.Transport.has_path(dest_bytes)
                if already_cached:
                    warn(f"{dest_hex[:16]}... — path CACHED (may be stale!)")

                # Request fresh path
                if already_cached:
                    # Expire the cached path first
                    try:
                        with RNS.Transport.path_table_lock:
                            if dest_bytes in RNS.Transport.path_table:
                                del RNS.Transport.path_table[dest_bytes]
                    except Exception:
                        pass

                info(f"{dest_hex[:16]}... — requesting fresh path (5s timeout)...")
                start = time.time()
                found = RNS.Transport.await_path(dest_bytes, timeout=5)
                elapsed = time.time() - start

                if found:
                    hops = RNS.Transport.hops_to(dest_bytes)
                    ok(f"{dest_hex[:16]}... — REACHABLE ({elapsed:.1f}s, {hops} hops)")
                else:
                    fail(f"{dest_hex[:16]}... — UNREACHABLE (no response in 5s)")

            except Exception as e:
                fail(f"{dest_hex[:16]}... — error: {e}")
    else:
        warn("No seeder dests to test")

    # ── Phase 4: Link Establishment ─────────────────────────────
    header("PHASE 4: Link Establishment (can we connect?)")

    connected_links = []

    if ghost and ghost.seeder_dests:
        for dest_hex in ghost.seeder_dests:
            try:
                dest_bytes = bytes.fromhex(dest_hex)
                if not RNS.Transport.has_path(dest_bytes):
                    info(f"{dest_hex[:16]}... — skipping (no path)")
                    continue

                # Recall identity
                seeder_identity = RNS.Identity.recall(dest_bytes)
                if not seeder_identity:
                    time.sleep(1)
                    seeder_identity = RNS.Identity.recall(dest_bytes)
                if not seeder_identity:
                    fail(f"{dest_hex[:16]}... — cannot recall identity")
                    continue

                # Build destination
                seeder_dest = RNS.Destination(
                    seeder_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    config.RNS_APP_NAME,
                    "swarm",
                    ghost_hash
                )

                # Verify hash
                if seeder_dest.hash != dest_bytes:
                    fail(f"{dest_hex[:16]}... — dest hash mismatch!")
                    info(f"  Expected: {dest_hex}")
                    info(f"  Built:    {seeder_dest.hash.hex()}")
                    continue

                # Try link
                link_established = threading.Event()
                link_failed = threading.Event()

                def on_est(link):
                    link_established.set()
                def on_closed(link):
                    if not link_established.is_set():
                        link_failed.set()

                info(f"{dest_hex[:16]}... — initiating link...")
                link = RNS.Link(
                    seeder_dest,
                    established_callback=on_est,
                    closed_callback=on_closed
                )

                start = time.time()
                while time.time() - start < config.DEFAULT_LINK_TIMEOUT:
                    if link_established.is_set():
                        elapsed = time.time() - start
                        ok(f"{dest_hex[:16]}... — CONNECTED ({elapsed:.1f}s)")
                        connected_links.append((dest_bytes, link))
                        break
                    if link_failed.is_set():
                        fail(f"{dest_hex[:16]}... — link REJECTED/DROPPED")
                        break
                    time.sleep(0.5)
                else:
                    fail(f"{dest_hex[:16]}... — link TIMEOUT ({config.DEFAULT_LINK_TIMEOUT}s)")

            except Exception as e:
                fail(f"{dest_hex[:16]}... — error: {e}")
    else:
        warn("No seeder dests to test")

    # ── Phase 5: Manifest Fetch ─────────────────────────────────
    header("PHASE 5: Manifest Fetch (does seeder respond?)")

    for dest_bytes, link in connected_links:
        dest_hex = dest_bytes.hex()
        response_event = threading.Event()
        response_data = [None]

        def on_response(receipt):
            try:
                response_data[0] = receipt.response
            except Exception:
                pass
            response_event.set()

        def on_failed(receipt):
            response_event.set()

        try:
            info(f"{dest_hex[:16]}... — requesting manifest...")
            request = link.request(
                "manifest",
                data=None,
                response_callback=on_response,
                failed_callback=on_failed,
                timeout=15
            )

            response_event.wait(timeout=20)

            if response_data[0]:
                manifest = umsgpack.unpackb(response_data[0])
                ok(f"{dest_hex[:16]}... — manifest received!")
                info(f"  Name: {manifest.get('name', '?')}")
                info(f"  Chunks: {manifest.get('chunk_count', '?')}")
                manifest_dests = manifest.get("seeder_dests", [])
                info(f"  Seeder dests in manifest ({len(manifest_dests)}):")
                for md in manifest_dests:
                    already_known = md in (ghost.seeder_dests if ghost else [])
                    status = "KNOWN" if already_known else "NEW ←"
                    info(f"    {md[:16]}... [{status}]")
            else:
                fail(f"{dest_hex[:16]}... — no manifest response!")

        except Exception as e:
            fail(f"{dest_hex[:16]}... — manifest error: {e}")

    # ── Phase 6: Want Announce Test ─────────────────────────────
    header("PHASE 6: Want Announce (do seeders hear us?)")

    if ghost_hash:
        found_seeders = []
        found_lock = threading.Lock()

        class TestHandler:
            def __init__(self):
                self.aspect_filter = None
            def received_announce(self, destination_hash, announced_identity, app_data):
                if app_data:
                    try:
                        metadata = umsgpack.unpackb(app_data)
                        if isinstance(metadata, dict):
                            if metadata.get("type") == "seeder":
                                gh = metadata.get("ghost_hash", "")
                                if gh == ghost_hash:
                                    with found_lock:
                                        dh = destination_hash.hex()
                                        if dh not in found_seeders:
                                            found_seeders.append(dh)
                                            ok(f"Seeder responded: {dh[:16]}...")
                    except Exception:
                        pass

        handler = TestHandler()
        RNS.Transport.register_announce_handler(handler)

        want_dest = RNS.Destination(
            pid.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            config.RNS_APP_NAME,
            "diag",
            ghost_hash
        )

        announce_data = umsgpack.packb({
            "ghost_hash": ghost_hash,
            "type": "want",
            "t": int(time.time()),
        })

        info("Sending want announce...")
        want_dest.announce(app_data=announce_data)
        info("Waiting 15s for seeder responses...")

        for i in range(15):
            time.sleep(1)
            if found_seeders:
                break

        if found_seeders:
            ok(f"{len(found_seeders)} seeder(s) responded to want announce")
        else:
            fail("No seeders responded — announces may be rate-limited by Hub")

    # ── Phase 7: Cleanup ────────────────────────────────────────
    header("CLEANUP")

    for _, link in connected_links:
        try:
            link.teardown()
        except Exception:
            pass
    ok("All test links closed")

    # ── Summary ─────────────────────────────────────────────────
    header("SUMMARY")

    if ghost:
        info(f"Ghost file has {len(ghost.seeder_dests)} seeder dest(s)")
    if connected_links:
        ok(f"Successfully connected to {len(connected_links)} seeder(s)")
    else:
        fail("Could not connect to any seeders")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m phantom.swarm_diag <ghost_file_or_hash>")
        sys.exit(1)

    target = sys.argv[1]
    run_diagnostic(target)
