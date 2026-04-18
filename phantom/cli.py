"""
Reticulum Phantom — CLI Entry Point

The main command-line interface for Phantom.
Handles all user commands: create, seed, download, identity, settings, debug.

Usage:
    phantom create <file>           Convert file → .ghost
    phantom info <ghost_file>       Show .ghost metadata
    phantom identity                Show current identity
    phantom identity --new          Create new identity
    phantom identity --import <f>   Import identity from file
    phantom identity --export <f>   Export identity to file
    phantom seed <file_or_ghost>    Start seeding a file
    phantom download <hash>         Download from mesh by ghost hash
    phantom download <ghost_file>   Download using a .ghost file
    phantom settings                View settings
    phantom settings <key> <value>  Update a setting
    phantom debug                   Live RNS debug log
"""

import os
import sys
import time
import argparse

import RNS

from phantom import config
from phantom.identity import PhantomIdentity
from phantom.ghost_file import GhostFile
from phantom.network import PhantomNetwork
from phantom.seeder import Seeder
from phantom.leecher import Leecher
from phantom import ui


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="phantom",
        description="Reticulum Phantom — Decentralized Encrypted File Sharing",
        epilog="https://github.com/roogle-dev/reticulum-phantom",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ─── phantom create ────────────────────────────────────────────────────
    create_parser = subparsers.add_parser(
        "create",
        help="Convert a file to .ghost format"
    )
    create_parser.add_argument(
        "file",
        help="Path to the file to convert"
    )
    create_parser.add_argument(
        "-o", "--output",
        help="Output path for the .ghost file",
        default=None
    )
    create_parser.add_argument(
        "-c", "--comment",
        help="Optional comment/description",
        default=""
    )
    create_parser.add_argument(
        "--chunk-size",
        help="Chunk size in bytes (default: 1MB)",
        type=int,
        default=None
    )

    # ─── phantom info ──────────────────────────────────────────────────────
    info_parser = subparsers.add_parser(
        "info",
        help="Display .ghost file metadata"
    )
    info_parser.add_argument(
        "ghost_file",
        help="Path to the .ghost file"
    )

    # ─── phantom identity ──────────────────────────────────────────────────
    identity_parser = subparsers.add_parser(
        "identity",
        help="Manage your Phantom identity"
    )
    identity_parser.add_argument(
        "--new",
        action="store_true",
        help="Create a new identity (WARNING: replaces existing)"
    )
    identity_parser.add_argument(
        "--import-file",
        metavar="FILE",
        help="Import identity from a file"
    )
    identity_parser.add_argument(
        "--export-file",
        metavar="FILE",
        help="Export identity to a file"
    )

    # ─── phantom seed ──────────────────────────────────────────────────────
    seed_parser = subparsers.add_parser(
        "seed",
        help="Start seeding a file on the mesh"
    )
    seed_parser.add_argument(
        "file",
        help="Path to the file or .ghost file to seed"
    )
    seed_parser.add_argument(
        "--rns-config",
        help="Path to custom Reticulum config directory",
        default=None
    )

    # ─── phantom seed-all ──────────────────────────────────────────────────
    seedall_parser = subparsers.add_parser(
        "seed-all",
        help="Seed all files in a directory (or all known .ghost files)"
    )
    seedall_parser.add_argument(
        "directory",
        nargs="?",
        help="Directory containing files to seed (default: scans ghosts dir)",
        default=None
    )
    seedall_parser.add_argument(
        "--rns-config",
        help="Path to custom Reticulum config directory",
        default=None
    )

    # ─── phantom download ─────────────────────────────────────────────────
    download_parser = subparsers.add_parser(
        "download",
        help="Download a file from the mesh"
    )
    download_parser.add_argument(
        "target",
        help="Ghost hash (hex) or path to a .ghost file"
    )
    download_parser.add_argument(
        "-o", "--output",
        help="Output directory for the downloaded file",
        default=None
    )
    download_parser.add_argument(
        "--rns-config",
        help="Path to custom Reticulum config directory",
        default=None
    )

    # ─── phantom settings ─────────────────────────────────────────────────
    settings_parser = subparsers.add_parser(
        "settings",
        help="View or update settings"
    )
    settings_parser.add_argument(
        "key",
        nargs="?",
        help="Setting key to update"
    )
    settings_parser.add_argument(
        "value",
        nargs="?",
        help="New value for the setting"
    )

    # ─── phantom clean ────────────────────────────────────────────────────
    clean_parser = subparsers.add_parser(
        "clean",
        help="Remove temporary files (chunks, downloads, ghosts)"
    )
    clean_parser.add_argument(
        "--all",
        action="store_true",
        help="Also remove ghost files from the library"
    )
    clean_parser.add_argument(
        "--ghosts",
        action="store_true",
        help="Only remove ghost files from the library"
    )

    # ─── phantom debug ────────────────────────────────────────────────────
    debug_parser = subparsers.add_parser(
        "debug",
        help="Live debug log of all RNS activity"
    )
    debug_parser.add_argument(
        "--rns-config",
        help="Path to custom Reticulum config directory",
        default=None
    )

    # ─── phantom tui ──────────────────────────────────────────────────────
    tui_parser = subparsers.add_parser(
        "tui",
        help="Launch the interactive TUI dashboard"
    )
    tui_parser.add_argument(
        "--rns-config",
        help="Path to custom Reticulum config directory",
        default=None
    )

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        # No args → launch TUI (lazy import)
        try:
            from phantom.tui import run_tui
            run_tui()
        except ImportError:
            ui.print_banner()
            ui.print_warning(
                "TUI requires 'textual'. Install with: pip install textual"
            )
            parser.print_help()
        return

    # Dispatch to command handler
    try:
        if args.command == "create":
            cmd_create(args)
        elif args.command == "info":
            cmd_info(args)
        elif args.command == "identity":
            cmd_identity(args)
        elif args.command == "seed":
            cmd_seed(args)
        elif args.command == "seed-all":
            cmd_seed_all(args)
        elif args.command == "download":
            cmd_download(args)
        elif args.command == "settings":
            cmd_settings(args)
        elif args.command == "debug":
            cmd_debug(args)
        elif args.command == "clean":
            cmd_clean(args)
        elif args.command == "tui":
            try:
                from phantom.tui import run_tui
                run_tui(getattr(args, 'rns_config', None))
            except ImportError:
                ui.print_error(
                    "TUI requires 'textual'. Install with: pip install textual"
                )
                sys.exit(1)
    except KeyboardInterrupt:
        ui.console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(0)
    except Exception as e:
        ui.print_error(str(e))
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_create(args):
    """Handle: phantom create <file>"""
    ui.print_banner()

    filepath = os.path.abspath(args.file)
    if not os.path.isfile(filepath):
        ui.print_error(f"File not found: {filepath}")
        sys.exit(1)

    # Load identity for the creator hash
    pid = PhantomIdentity()
    pid.load()

    file_size = os.path.getsize(filepath)
    ui.print_info(
        f"Creating ghost for: {os.path.basename(filepath)} "
        f"({GhostFile._human_size(file_size)})"
    )

    # Create the ghost file
    with ui.console.status("[bold cyan]Hashing file...", spinner="dots"):
        ghost = GhostFile.create(
            filepath,
            identity_hash=pid.hash_hex or "",
            comment=args.comment,
            chunk_size=args.chunk_size,
        )

    if ghost is None:
        ui.print_error("Failed to create ghost file")
        sys.exit(1)

    # Save it — next to source file AND to the ghost library
    output_path = args.output
    if output_path is None:
        output_path = filepath + config.GHOST_EXTENSION

    saved_path = ghost.save(output_path)

    # Also save to ghost library (for seed-all)
    config.ensure_directories()
    library_path = os.path.join(
        config.GHOSTS_DIR, ghost.name + config.GHOST_EXTENSION
    )
    if os.path.abspath(output_path) != os.path.abspath(library_path):
        ghost.save(library_path)
    if saved_path:
        ui.print_ghost_created(saved_path, ghost.get_info_dict())
    else:
        ui.print_error("Failed to save ghost file")
        sys.exit(1)


def cmd_info(args):
    """Handle: phantom info <ghost_file>"""
    ui.print_banner()

    ghost_path = os.path.abspath(args.ghost_file)
    if not os.path.isfile(ghost_path):
        ui.print_error(f"Ghost file not found: {ghost_path}")
        sys.exit(1)

    ghost = GhostFile.load(ghost_path)
    if ghost:
        ui.print_ghost_info(ghost.get_info_dict())
    else:
        ui.print_error("Failed to parse ghost file")
        sys.exit(1)


def cmd_identity(args):
    """Handle: phantom identity"""
    ui.print_banner()

    # Initialize RNS (needed for identity operations)
    reticulum = RNS.Reticulum()

    pid = PhantomIdentity()

    if args.new:
        # Create new identity
        if pid.exists():
            ui.print_warning("An identity already exists!")
            ui.console.print(
                "[bold yellow]Creating a new identity will replace it permanently.[/bold yellow]"
            )
            confirm = input("Type 'yes' to confirm: ").strip().lower()
            if confirm != "yes":
                ui.print_info("Cancelled.")
                return

        pid.create_new()
        ui.print_identity_created(pid.get_info())

    elif args.import_file:
        # Import identity
        import_path = os.path.abspath(args.import_file)
        if pid.load_from_file(import_path):
            ui.print_success("Identity imported!")
            ui.print_identity(pid.get_info())
        else:
            ui.print_error("Failed to import identity")

    elif args.export_file:
        # Export identity
        pid.load()
        export_path = os.path.abspath(args.export_file)
        if pid.export(export_path):
            ui.print_success(f"Identity exported to: {export_path}")
        else:
            ui.print_error("Failed to export identity")

    else:
        # Display current identity
        pid.load()
        if pid.is_loaded:
            ui.print_identity(pid.get_info())
        else:
            ui.print_warning("No identity found. Create one with: phantom identity --new")


def cmd_seed(args):
    """Handle: phantom seed <file>"""
    ui.print_banner()

    filepath = os.path.abspath(args.file)
    if not os.path.isfile(filepath):
        ui.print_error(f"File not found: {filepath}")
        sys.exit(1)

    # Determine if this is a .ghost file or a regular file
    if filepath.endswith(config.GHOST_EXTENSION):
        ghost = GhostFile.load(filepath)
        if not ghost:
            ui.print_error("Failed to parse ghost file")
            sys.exit(1)

        # For seeding from .ghost, we need the source file
        # Try to find it in the same directory
        source_dir = os.path.dirname(filepath)
        source_path = os.path.join(source_dir, ghost.name)
        if not os.path.isfile(source_path):
            # Try without the .ghost extension
            base_path = filepath[:-len(config.GHOST_EXTENSION)]
            if os.path.isfile(base_path):
                source_path = base_path
            else:
                ui.print_error(
                    f"Source file not found: {ghost.name}\n"
                    f"Place the original file next to the .ghost file."
                )
                sys.exit(1)
    else:
        # Regular file — check if .ghost already exists
        source_path = filepath
        ghost_path = filepath + config.GHOST_EXTENSION

        if os.path.isfile(ghost_path):
            # Reuse existing .ghost file
            ui.print_info(
                f"Found existing ghost: {os.path.basename(ghost_path)}"
            )
            ghost = GhostFile.load(ghost_path)
            if not ghost:
                ui.print_warning("Existing ghost file is invalid, recreating...")
                ghost = None

        if not os.path.isfile(ghost_path) or ghost is None:
            ui.print_info(f"Creating ghost for: {os.path.basename(filepath)}")
            with ui.console.status("[bold cyan]Hashing file...", spinner="dots"):
                ghost = GhostFile.create(filepath)

            if not ghost:
                ui.print_error("Failed to create ghost")
                sys.exit(1)

            ghost.save()

    # Start the network
    ui.print_info("Starting Reticulum Network Stack...")
    network = PhantomNetwork(args.rns_config)
    network.start()

    # Load identity
    pid = PhantomIdentity()
    pid.load()

    # Create and start the seeder
    seeder = Seeder(ghost, source_path, network, pid)
    seeder.start()

    ui.print_seeding_started(seeder.get_stats())

    # Keep running until Ctrl+C
    try:
        while True:
            time.sleep(5)
            ui.print_seeding_status(seeder.get_stats())
    except KeyboardInterrupt:
        ui.console.print()
        seeder.stop()
        ui.print_info("Seeding stopped.")


def cmd_seed_all(args):
    """Handle: phantom seed-all [directory]"""
    ui.print_banner()

    # Collect files to seed
    files_to_seed = []

    if args.directory:
        # Scan a directory for files
        dirpath = os.path.abspath(args.directory)
        if not os.path.isdir(dirpath):
            ui.print_error(f"Directory not found: {dirpath}")
            sys.exit(1)

        ui.print_info(f"Scanning directory: {dirpath}")
        for entry in os.listdir(dirpath):
            full_path = os.path.join(dirpath, entry)
            if os.path.isfile(full_path) and not entry.endswith(config.GHOST_EXTENSION):
                files_to_seed.append(full_path)
    else:
        # Seed all known .ghost files from ghosts dir
        config.ensure_directories()
        ui.print_info(f"Scanning ghost library: {config.GHOSTS_DIR}")
        for entry in os.listdir(config.GHOSTS_DIR):
            if entry.endswith(config.GHOST_EXTENSION):
                files_to_seed.append(os.path.join(config.GHOSTS_DIR, entry))

    if not files_to_seed:
        ui.print_warning("No files found to seed.")
        sys.exit(0)

    ui.print_info(f"Found {len(files_to_seed)} files to seed")

    # Start the network
    ui.print_info("Starting Reticulum Network Stack...")
    network = PhantomNetwork(args.rns_config)
    network.start()

    # Load identity
    pid = PhantomIdentity()
    pid.load()

    # Create seeders for all files
    seeders = []

    for fpath in files_to_seed:
        try:
            if fpath.endswith(config.GHOST_EXTENSION):
                ghost = GhostFile.load(fpath)
                if not ghost:
                    ui.print_warning(f"Skipping invalid ghost: {os.path.basename(fpath)}")
                    continue
                # Find source file — try stored source_path first
                source_path = None

                if ghost.source_path and os.path.isfile(ghost.source_path):
                    source_path = ghost.source_path
                else:
                    # Fallback: look next to ghost file
                    source_dir = os.path.dirname(fpath)
                    candidate = os.path.join(source_dir, ghost.name)
                    if os.path.isfile(candidate):
                        source_path = candidate
                    else:
                        base_path = fpath[:-len(config.GHOST_EXTENSION)]
                        if os.path.isfile(base_path):
                            source_path = base_path

                if not source_path:
                    ui.print_warning(
                        f"Source file not found for: {ghost.name}"
                    )
                    continue
            else:
                source_path = fpath
                ghost_path = fpath + config.GHOST_EXTENSION

                ghost = None
                if os.path.isfile(ghost_path):
                    ghost = GhostFile.load(ghost_path)

                if ghost is None:
                    ui.print_info(f"Hashing: {os.path.basename(fpath)}...")
                    ghost = GhostFile.create(fpath)
                    if not ghost:
                        ui.print_warning(
                            f"Failed to create ghost: {os.path.basename(fpath)}"
                        )
                        continue
                    ghost.save()

            seeder = Seeder(ghost, source_path, network, pid)
            seeder.start()
            seeders.append(seeder)

            ui.console.print(
                f"  [green]↑[/green] {ghost.name} "
                f"[dim]| ghost:{ghost.ghost_hash[:16]}... "
                f"| dest:{seeder.destination_hash_hex or '?'}[/dim]"
            )

        except Exception as e:
            ui.print_warning(f"Error seeding {os.path.basename(fpath)}: {e}")

    if not seeders:
        ui.print_error("No files could be seeded.")
        sys.exit(1)

    ui.console.print()
    ui.console.print(
        f"[bold green]✓ Seeding {len(seeders)} files[/bold green]  "
        f"[dim]Press Ctrl+C to stop all[/dim]"
    )
    ui.console.print()

    # Keep running — show combined stats
    try:
        while True:
            time.sleep(5)
            total_peers = sum(s.get_stats()["active_peers"] for s in seeders)
            total_chunks = sum(s.get_stats()["chunks_served"] for s in seeders)
            total_uploaded = sum(s.get_stats()["total_uploaded"] for s in seeders)
            uploaded_str = GhostFile._human_size(total_uploaded)

            ui.console.print(
                f"  [green]↑[/green] Files: {len(seeders)} | "
                f"Peers: {total_peers} | "
                f"Chunks served: {total_chunks} | "
                f"Uploaded: {uploaded_str}",
                end="\r"
            )
    except KeyboardInterrupt:
        ui.console.print()
        for s in seeders:
            s.stop()
        ui.print_info(f"Stopped {len(seeders)} seeders.")


def cmd_download(args):
    """Handle: phantom download <hash_or_ghost>"""
    ui.print_banner()

    target = args.target

    # Start the network
    ui.print_info("Starting Reticulum Network Stack...")
    rns_config = getattr(args, 'rns_config', None)
    network = PhantomNetwork(rns_config)
    network.start()

    # Load identity
    pid = PhantomIdentity()
    pid.load()

    # Create leecher
    leecher = Leecher(network, pid)

    # Set output directory if specified
    output_dir = getattr(args, 'output', None)
    if output_dir:
        leecher.output_dir = os.path.abspath(output_dir)
        ui.print_info(f"Download folder: {leecher.output_dir}")

    # Set up progress display
    progress = ui.create_download_progress()
    task_id = None

    def on_progress(chunks_done, total, bytes_received):
        nonlocal task_id
        if task_id is not None:
            progress.update(task_id, completed=bytes_received,
                           description=f"Downloading {leecher.ghost.name if leecher.ghost else 'file'} [{chunks_done}/{total}]")

    def on_state_change(state, info):
        nonlocal task_id
        if state == Leecher.STATE_DISCOVERING:
            ui.print_info("Searching mesh for seeder...")
        elif state == Leecher.STATE_CONNECTING:
            ui.print_info("Establishing encrypted link...")
        elif state == Leecher.STATE_FETCHING_MANIFEST:
            ui.print_info("Fetching manifest from seeder...")
        elif state == Leecher.STATE_DOWNLOADING:
            pass  # Progress bar handles this
        elif state == Leecher.STATE_ASSEMBLING:
            ui.print_info("Assembling file from chunks...")
        elif state == Leecher.STATE_COMPLETE:
            ui.print_download_complete(leecher.get_stats())
        elif state == Leecher.STATE_FAILED:
            ui.print_download_failed(info)

    leecher.on_progress = on_progress
    leecher.on_state_change = on_state_change

    # Determine if target is a .ghost file or a hash
    if target.endswith(config.GHOST_EXTENSION):
        # User is trying to use a .ghost file
        ghost_path = os.path.abspath(target)
        if not os.path.isfile(ghost_path):
            ui.print_error(f"Ghost file not found: {ghost_path}")
            ui.console.print(
                "[dim]Make sure the .ghost file exists at that path.[/dim]"
            )
            sys.exit(1)
        ui.print_info(f"Loading ghost file: {ghost_path}")
        leecher.download_from_ghost(ghost_path)
    elif os.path.isfile(target):
        # Maybe it's a ghost file without the extension check
        ui.print_info(f"Loading ghost file: {target}")
        leecher.download_from_ghost(os.path.abspath(target))
    else:
        # Treat as a destination hash or ghost hash
        ui.print_download_started(target)
        leecher.download_from_hash(target)

    # Wait and show progress
    try:
        # Wait for manifest/connection before showing progress bar
        while leecher.state in (Leecher.STATE_IDLE,
                                 Leecher.STATE_DISCOVERING,
                                 Leecher.STATE_CONNECTING,
                                 Leecher.STATE_FETCHING_MANIFEST):
            time.sleep(0.5)

        if leecher.state == Leecher.STATE_DOWNLOADING:
            with progress:
                file_size = leecher.ghost.file_size if leecher.ghost else leecher.total_chunks * 1024 * 1024
                task_id = progress.add_task(
                    f"Downloading {leecher.ghost.name if leecher.ghost else 'file'} [0/{leecher.total_chunks}]",
                    total=file_size
                )
                while leecher.state == Leecher.STATE_DOWNLOADING:
                    time.sleep(0.3)
                    progress.update(task_id, completed=leecher.bytes_received)

        # Wait for assembling/completion
        while leecher.state == Leecher.STATE_ASSEMBLING:
            time.sleep(0.5)

    except KeyboardInterrupt:
        leecher.cancel()
        ui.print_info("Download cancelled.")


def cmd_settings(args):
    """Handle: phantom settings [key] [value]"""
    ui.print_banner()

    settings = config.load_settings()

    if args.key and args.value:
        # Update a setting
        try:
            # Try to parse value as int, float, or bool
            value = args.value
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            else:
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass  # Keep as string

            settings = config.update_setting(args.key, value)
            ui.print_success(f"Setting '{args.key}' updated to: {value}")
            ui.console.print()
        except KeyError as e:
            ui.print_error(str(e))
            sys.exit(1)

    # Always show current settings
    ui.print_settings(settings)


def cmd_clean(args):
    """Handle: phantom clean [--all] [--ghosts]"""
    import shutil

    ui.print_banner()
    config.ensure_directories()

    cleaned = 0
    total_size = 0

    def dir_size(path):
        total = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total += os.path.getsize(fp)
        return total

    def clean_dir(path, label):
        nonlocal cleaned, total_size
        if os.path.isdir(path):
            size = dir_size(path)
            count = sum(len(files) for _, _, files in os.walk(path))
            if count > 0:
                total_size += size
                cleaned += count
                shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
                ui.print_success(
                    f"Removed {label}: {count} files "
                    f"({GhostFile._human_size(size)})"
                )
            else:
                ui.console.print(f"  [dim]{label}: empty[/dim]")

    if args.ghosts:
        # Only remove ghosts
        clean_dir(config.GHOSTS_DIR, "Ghost library")
    else:
        # Always clean chunks
        clean_dir(config.CHUNKS_DIR, "Chunk cache")

        # Always clean downloads
        clean_dir(config.DOWNLOADS_DIR, "Downloads")

        # Optionally clean ghosts
        if getattr(args, 'all', False):
            clean_dir(config.GHOSTS_DIR, "Ghost library")

    if cleaned > 0:
        ui.console.print()
        ui.print_success(
            f"Cleaned {cleaned} files, "
            f"freed {GhostFile._human_size(total_size)}"
        )
    else:
        ui.print_info("Nothing to clean.")


def cmd_debug(args):
    """Handle: phantom debug"""
    ui.print_banner()
    ui.print_debug_header()

    # Set RNS log level to verbose
    RNS.loglevel = RNS.LOG_VERBOSE

    # Start Reticulum
    ui.print_info("Starting Reticulum in debug mode...")
    network = PhantomNetwork(args.rns_config)
    network.start()

    status = network.get_status()
    ui.print_info(f"Network status: {status}")

    # Load identity
    pid = PhantomIdentity()
    pid.load()
    if pid.is_loaded:
        ui.print_info(f"Identity: {pid.hash_pretty}")

    ui.console.print()
    ui.console.print("[dim]Listening for mesh activity... (Ctrl+C to exit)[/dim]")
    ui.console.print()

    # Keep running — RNS will output debug logs
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ui.console.print()
        ui.print_info("Debug mode ended.")


if __name__ == "__main__":
    main()
