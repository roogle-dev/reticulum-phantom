"""
Reticulum Phantom — Interactive TUI Dashboard

A full-screen interactive terminal interface built with Textual.
Provides real-time monitoring of transfers, ghost file management,
and network status in a premium cyberpunk-styled dashboard.

Launch with: phantom tui
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Static, Button, Input, DataTable,
    Label, ProgressBar, TabbedContent, TabPane, RichLog, Rule
)
from textual.binding import Binding
from textual.timer import Timer
from textual import events

from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich import box

from . import config
from .engine import PhantomEngine
from .ghost_file import GhostFile

import os
import time


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Widgets
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkPanel(Static):
    """Displays network status with a pulsing indicator."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._online = False
        self._stats = {}

    def update_stats(self, online, stats):
        self._online = online
        self._stats = stats
        self.refresh()

    def render(self):
        dot = "[bold green]●[/bold green]" if self._online else "[bold red]●[/bold red]"
        status_text = "ONLINE" if self._online else "OFFLINE"
        status_color = "green" if self._online else "red"

        up = self._stats.get("total_uploaded_human", "0 B")
        down = self._stats.get("total_downloaded_human", "0 B")
        active = self._stats.get("active_transfers", 0)
        total = self._stats.get("total_transfers", 0)

        content = (
            f" {dot} [{status_color}]{status_text}[/{status_color}]\n"
            f" [bold cyan]↑[/bold cyan] {up}  [bold blue]↓[/bold blue] {down}\n"
            f" [dim]Active: {active} / {total} transfers[/dim]"
        )
        return Text.from_markup(content)


class IdentityPanel(Static):
    """Displays current identity info."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._hash = "Not loaded"
        self._hex = ""

    def update_identity(self, hash_pretty, hash_hex):
        self._hash = hash_pretty
        self._hex = hash_hex
        self.refresh()

    def render(self):
        content = (
            f" [bold]🔑[/bold] {self._hash}\n"
            f" [dim]Identity active on mesh[/dim]"
        )
        return Text.from_markup(content)


class TransferRow(Static):
    """A single transfer progress display with stop/remove controls."""

    def __init__(self, transfer, **kwargs):
        super().__init__(**kwargs)
        self._transfer = transfer

    def update_transfer(self, transfer):
        self._transfer = transfer
        self.refresh()

    def render(self):
        t = self._transfer

        if t.direction == "upload":
            arrow = "[bold cyan]↑ SEED[/bold cyan]"
        else:
            arrow = "[bold blue]↓ DOWN[/bold blue]"

        # Progress bar
        pct = t.progress * 100
        bar_len = 30
        filled = int(bar_len * t.progress)
        bar = "━" * filled + "[dim]━[/dim]" * (bar_len - filled)

        # State styling
        if t.state == "complete":
            state_str = "[bold green]✓ COMPLETE[/bold green]"
        elif t.state in ("failed", "cancelled", "stopped"):
            err = t.error or t.state
            state_str = f"[bold red]✗ {err[:40]}[/bold red]"
        elif t.state == "seeding":
            state_str = f"[green]SEEDING[/green] [dim]Peers:[/dim] {t.peers}"
        elif t.state == "discovering":
            state_str = "[yellow]⏳ DISCOVERING...[/yellow]"
        elif t.state == "connecting":
            state_str = "[yellow]🔗 CONNECTING...[/yellow]"
        elif t.state == "fetching_manifest":
            state_str = "[yellow]📋 FETCHING MANIFEST...[/yellow]"
        elif t.state == "downloading":
            speed = GhostFile._human_size(t.speed) + "/s"
            state_str = f"[cyan]⚡ {speed}[/cyan]"
        elif t.state == "assembling":
            state_str = "[yellow]🔧 ASSEMBLING...[/yellow]"
        else:
            state_str = f"[yellow]{t.state.upper()}[/yellow]"

        # Size info
        size_str = GhostFile._human_size(t.bytes_transferred)
        chunks_str = ""
        if t.total_chunks:
            chunks_str = f"[dim]Chunks:[/dim] {t.chunks_done}/{t.total_chunks}"

        # Destination hash
        dest_str = ""
        if t.destination_hash:
            dest_str = "\n   [dim]Dest: " + t.destination_hash + "[/dim]"

        # Ghost hash
        ghost_str = ""
        if t.ghost_hash:
            ghost_str = "  [dim]Ghost: " + t.ghost_hash[:16] + "...[/dim]"

        # Full name
        name = t.name or "Unknown"

        # Action buttons based on state
        if t.state in ("complete",):
            actions = "  [dim][[/dim][bold red]✕ Remove[/bold red][dim]][/dim]"
        elif t.state in ("failed", "cancelled", "stopped"):
            actions = ("  [dim][[/dim][bold green]▶ Resume[/bold green][dim]][/dim]"
                      "  [dim][[/dim][bold red]✕ Remove[/bold red][dim]][/dim]")
        else:
            actions = "  [dim][[/dim][bold yellow]⏹ Stop[/bold yellow][dim]][/dim]  [dim][[/dim][bold red]✕ Remove[/bold red][dim]][/dim]"

        line1 = f" {arrow}  [bold]{name}[/bold]{ghost_str}{actions}"
        line2 = f"   [cyan]{bar}[/cyan] {pct:5.1f}%  {size_str}  {chunks_str}  {state_str}"

        content = line1 + "\n" + line2 + dest_str
        return Text.from_markup(content)

    def on_click(self, event):
        """Handle clicks on the transfer row."""
        t = self._transfer
        self.app.action_transfer_click(t.id, t.state)


# ═══════════════════════════════════════════════════════════════════════════════
# Main TUI App
# ═══════════════════════════════════════════════════════════════════════════════

class PhantomTUI(App):
    """Reticulum Phantom — Interactive Terminal Dashboard."""

    TITLE = "👻 Reticulum Phantom"
    SUB_TITLE = f"v{config.APP_VERSION} | Decentralized Encrypted File Sharing"

    CSS = """
    Screen {
        background: $surface;
    }

    #status-bar {
        layout: horizontal;
        height: 5;
        margin: 0 1 0 1;
    }

    #network-panel {
        width: 1fr;
        border: solid $accent;
        padding: 0 1;
    }

    #identity-panel {
        width: 2fr;
        border: solid cyan;
        padding: 0 1;
    }

    #transfers-label {
        margin: 1 1 0 1;
        padding: 0;
    }

    #transfers-container {
        height: 1fr;
        min-height: 8;
        margin: 0 1;
        border: solid $primary;
        padding: 1;
        overflow-y: auto;
    }

    .transfer-row {
        height: auto;
        min-height: 3;
        max-height: 5;
        margin: 0 0 1 0;
    }

    #input-bar {
        layout: horizontal;
        height: 3;
        margin: 0 1 0 1;
        padding: 0;
    }

    #input-bar Input {
        width: 1fr;
    }

    #input-bar Button {
        width: auto;
        min-width: 12;
        margin: 0 0 0 1;
    }

    #log-panel {
        height: 10;
        margin: 0 1 0 1;
        border: solid $accent-darken-2;
    }

    #ghost-table {
        height: 1fr;
        margin: 1;
    }

    #peers-table {
        height: 1fr;
        margin: 1;
    }

    #network-table {
        height: 1fr;
        margin: 1;
    }

    #settings-container {
        height: 1fr;
        margin: 1;
        overflow-y: auto;
    }

    TabPane {
        padding: 0;
    }

    #no-transfers {
        text-align: center;
        color: $text-muted;
        margin: 2;
    }

    #settings-edit-bar {
        layout: horizontal;
        height: 3;
        margin: 1 1 0 1;
        padding: 0;
    }

    #settings-edit-bar Input {
        width: 1fr;
        margin: 0 1 0 0;
    }

    #settings-edit-bar Button {
        width: auto;
        min-width: 10;
    }

    #interfaces-label {
        margin: 1 1 0 1;
    }

    #interfaces-table {
        height: auto;
        max-height: 12;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("s", "seed", "Seed File"),
        Binding("d", "download", "Download"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, engine, **kwargs):
        super().__init__(**kwargs)
        self.engine = engine
        self._transfer_widgets = {}
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()

        with TabbedContent():
            # ─── Dashboard Tab ───────────────────────────────────────
            with TabPane("Dashboard", id="tab-dashboard"):

                with Horizontal(id="status-bar"):
                    yield NetworkPanel(id="network-panel")
                    yield IdentityPanel(id="identity-panel")

                yield Static(
                    " [bold]Active Transfers[/bold]  "
                    "[dim](S=Seed  D=Download  Q=Quit)[/dim]",
                    id="transfers-label",
                )

                yield ScrollableContainer(
                    Static(
                        "[dim]No active transfers. "
                        "Press [bold]S[/bold] to seed or "
                        "[bold]D[/bold] to download.[/dim]",
                        id="no-transfers",
                    ),
                    id="transfers-container",
                )

                with Horizontal(id="input-bar"):
                    yield Input(
                        placeholder="File path to seed, or destination hash / .ghost path to download...",
                        id="action-input",
                    )
                    yield Button("Seed", id="btn-seed", variant="success")
                    yield Button("Download", id="btn-download", variant="primary")

                yield RichLog(
                    highlight=True,
                    markup=True,
                    min_width=40,
                    id="log-panel",
                )

            # ─── Ghost Files Tab ─────────────────────────────────────
            with TabPane("Ghost Files", id="tab-ghosts"):
                yield Static(
                    " [bold]👻 Ghost File Library[/bold]  "
                    "[dim]Your .ghost metadata files[/dim]"
                )
                yield DataTable(id="ghost-table", zebra_stripes=True)

            # ─── Peers Tab ────────────────────────────────────────
            with TabPane("Peers", id="tab-peers"):
                yield Static(
                    " [bold]🌐 Phantom Peers[/bold]  "
                    "[dim]Phantom nodes seeding files[/dim]"
                )
                yield DataTable(id="peers-table", zebra_stripes=True)

            # ─── Network Tab ────────────────────────────────────────
            with TabPane("Network", id="tab-network"):
                yield Static(
                    " [bold]📡 Mesh Network[/bold]  "
                    "[dim]ALL nodes visible on the Reticulum mesh[/dim]"
                )
                yield Input(
                    placeholder="Filter by identity or destination hash...",
                    id="network-filter"
                )
                yield DataTable(id="network-table", zebra_stripes=True)

            # ─── Settings Tab ────────────────────────────────────────
            with TabPane("Settings", id="tab-settings"):
                yield Static(
                    " [bold]⚙ Phantom Settings[/bold]"
                )
                yield DataTable(id="settings-table", zebra_stripes=True)

                with Horizontal(id="settings-edit-bar"):
                    yield Input(
                        placeholder="Setting name (e.g. download_directory)",
                        id="settings-key-input",
                    )
                    yield Input(
                        placeholder="New value",
                        id="settings-value-input",
                    )
                    yield Button("Save", id="btn-save-setting", variant="success")

                yield Static(
                    " [bold]📡 Reticulum Interfaces[/bold]  "
                    "[dim]Active network interfaces[/dim]",
                    id="interfaces-label",
                )
                yield DataTable(id="interfaces-table", zebra_stripes=True)

        yield Footer()

    def on_mount(self) -> None:
        """Set up refresh timer and log callbacks."""
        # Hook engine callbacks
        self.engine.on_log = self._on_engine_log
        self.engine.on_transfer_update = self._on_transfer_update

        # Set up periodic refresh
        self._refresh_timer = self.set_interval(1.0, self._refresh_ui)

        # Initialize tables
        self._init_ghost_table()
        self._init_peers_table()
        self._init_network_table()
        self._init_settings_table()
        self._init_interfaces_table()

        # Load log history from previous sessions
        self._load_log_history()

        # Auto-seed files from ghost library
        self._auto_seed_library()

        # Auto-resume interrupted downloads
        self._auto_resume_downloads()

        # Initial refresh
        self._refresh_ui()

    def _auto_seed_library(self):
        """Auto-seed all files from the ghost library on startup."""
        try:
            config.ensure_directories()
            ghost_dir = config.GHOSTS_DIR
            if not os.path.isdir(ghost_dir):
                return

            seeded = 0
            for entry in os.listdir(ghost_dir):
                if entry.endswith(config.GHOST_EXTENSION):
                    ghost_path = os.path.join(ghost_dir, entry)
                    try:
                        ghost = GhostFile.load(ghost_path)
                        if not ghost:
                            continue

                        # Look for the source file
                        source_dir = os.path.dirname(ghost_path)
                        source_path = os.path.join(source_dir, ghost.name)

                        # Also check downloads dir
                        if not os.path.isfile(source_path):
                            dl_path = os.path.join(
                                config.DOWNLOADS_DIR, ghost.name
                            )
                            if os.path.isfile(dl_path):
                                source_path = dl_path

                        # Also check path without .ghost extension
                        if not os.path.isfile(source_path):
                            base = ghost_path[:-len(config.GHOST_EXTENSION)]
                            if os.path.isfile(base):
                                source_path = base

                        if os.path.isfile(source_path):
                            tid = self.engine.seed_file(source_path)
                            if tid:
                                seeded += 1
                    except Exception:
                        pass

            if seeded > 0:
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f"Auto-seeding {seeded} files from library"
                })
        except Exception:
            pass

    def _load_log_history(self):
        """Load log entries from previous sessions."""
        try:
            entries = self.engine.load_log_history(max_lines=100)
            if entries:
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f"─── Previous session logs ({len(entries)} entries) ───"
                })
                for entry in entries[-50:]:
                    date = entry.get("date", "")
                    time_str = entry.get("time", "")
                    prefix = f"{date} " if date else ""
                    entry_copy = dict(entry)
                    entry_copy["time"] = prefix + time_str
                    self._append_log(entry_copy)
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": "─── Current session ───"
                })
        except Exception:
            pass

    def _auto_resume_downloads(self):
        """Resume any interrupted downloads from previous sessions."""
        try:
            resumable = self.engine.get_resumable_downloads()
            for dl in resumable:
                ghost_path = dl.get("ghost_path", "")
                name = dl.get("name", "Unknown")
                have = dl.get("chunks_have", 0)
                total = dl.get("total_chunks", 0)
                pct = (have / total * 100) if total > 0 else 0

                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f"🔄 Resuming: {name} ({pct:.0f}% — {have}/{total} chunks)"
                })

                self.run_worker(
                    lambda gp=ghost_path: self.engine.download_file(gp),
                    thread=True,
                )

            if resumable:
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f"Resumed {len(resumable)} interrupted download(s)"
                })
        except Exception:
            pass

    def _on_engine_log(self, entry):
        """Handle log events from the engine."""
        try:
            self.call_from_thread(self._append_log, entry)
        except Exception:
            pass

    def _on_transfer_update(self, transfer_id):
        """Handle transfer update events."""
        try:
            self.call_from_thread(self._refresh_transfers)
        except Exception:
            pass

    def _append_log(self, entry):
        """Append a log entry to the log panel."""
        try:
            log_widget = self.query_one("#log-panel", RichLog)
            level = entry.get("level", "info")
            time_str = entry.get("time", "")
            message = entry.get("message", "")

            level_colors = {
                "info": "cyan",
                "warning": "yellow",
                "error": "red",
                "debug": "dim",
            }
            color = level_colors.get(level, "white")

            log_widget.write(
                f"[dim]{time_str}[/dim] [{color}]{message}[/{color}]"
            )
        except Exception:
            pass

    def _refresh_ui(self):
        """Periodic UI refresh."""
        try:
            self._refresh_network()
            self._refresh_identity()
            self._refresh_transfers()
            self._refresh_peers_table()
            self._refresh_network_table()
            self._refresh_interfaces_table()
        except Exception:
            pass

    def _refresh_network(self):
        """Update network status panel."""
        try:
            panel = self.query_one("#network-panel", NetworkPanel)
            stats = self.engine.get_network_stats()
            panel.update_stats(self.engine.is_running, stats)
        except Exception:
            pass

    def _refresh_identity(self):
        """Update identity panel."""
        try:
            panel = self.query_one("#identity-panel", IdentityPanel)
            panel.update_identity(
                self.engine.identity_hash,
                self.engine.identity_hex
            )
        except Exception:
            pass

    def _refresh_transfers(self):
        """Update the transfers display."""
        try:
            container = self.query_one("#transfers-container")
            transfers = self.engine.get_transfers()

            # Show/hide the "no transfers" message
            no_transfers = self.query_one("#no-transfers", Static)

            if not transfers:
                no_transfers.display = True
                # Remove old transfer widgets
                for widget_id in list(self._transfer_widgets.keys()):
                    try:
                        self._transfer_widgets[widget_id].remove()
                    except Exception:
                        pass
                self._transfer_widgets.clear()
                return

            no_transfers.display = False

            # Update/create transfer widgets
            current_ids = set()
            for transfer in transfers:
                current_ids.add(transfer.id)

                if transfer.id in self._transfer_widgets:
                    self._transfer_widgets[transfer.id].update_transfer(transfer)
                else:
                    widget = TransferRow(
                        transfer,
                        id=f"tr-{transfer.id}",
                        classes="transfer-row",
                    )
                    self._transfer_widgets[transfer.id] = widget
                    container.mount(widget, before=no_transfers)

            # Remove widgets for transfers no longer active
            for widget_id in list(self._transfer_widgets.keys()):
                if widget_id not in current_ids:
                    try:
                        self._transfer_widgets[widget_id].remove()
                    except Exception:
                        pass
                    del self._transfer_widgets[widget_id]
        except Exception:
            pass

    def _init_ghost_table(self):
        """Initialize the ghost files table."""
        try:
            table = self.query_one("#ghost-table", DataTable)
            table.add_columns(
                "File Name", "Size", "Chunks", "Ghost Hash",
                "Seeder Dest", "Created"
            )
            self._refresh_ghost_table()
        except Exception:
            pass

    def _refresh_ghost_table(self):
        """Refresh ghost files table data."""
        try:
            table = self.query_one("#ghost-table", DataTable)
            table.clear()

            ghosts = self.engine.get_ghost_files()
            for g in ghosts:
                from datetime import datetime
                try:
                    created = datetime.fromtimestamp(
                        g["created_at"]
                    ).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    created = "Unknown"

                seeder_dest = g.get("seeder_dest", "")
                if seeder_dest:
                    seeder_dest = seeder_dest[:16] + "..."
                else:
                    seeder_dest = "[not set]"

                table.add_row(
                    g["name"],
                    g["size_human"],
                    str(g["chunks"]),
                    g["ghost_hash"],
                    seeder_dest,
                    created,
                )
        except Exception:
            pass

    def _init_peers_table(self):
        """Initialize the peers table."""
        try:
            table = self.query_one("#peers-table", DataTable)
            table.add_columns(
                "Identity", "Files Seeding", "File Names", "Last Seen"
            )
        except Exception:
            pass

    def _refresh_peers_table(self):
        """Refresh peers table data."""
        try:
            table = self.query_one("#peers-table", DataTable)
            table.clear()

            peers = self.engine.get_peers()
            for peer in peers:
                identity = peer.get("identity_short", "Unknown")
                files = peer.get("files", {})
                file_count = str(len(files))

                # Build file names list
                names = []
                for gh, info in files.items():
                    name = info.get("name", "Unknown")
                    size = GhostFile._human_size(info.get("size", 0))
                    names.append(f"{name} ({size})")
                names_str = ", ".join(names) if names else "None"

                # Last seen
                from datetime import datetime
                try:
                    last_seen = datetime.fromtimestamp(
                        peer["last_seen"]
                    ).strftime("%H:%M:%S")
                except Exception:
                    last_seen = "Unknown"

                table.add_row(
                    identity,
                    file_count,
                    names_str,
                    last_seen,
                )
        except Exception:
            pass

    def _init_network_table(self):
        """Initialize the network table showing ALL mesh nodes."""
        try:
            table = self.query_one("#network-table", DataTable)
            table.add_columns(
                "Destination", "Identity", "Hops", "App Data", "Last Seen"
            )
        except Exception:
            pass

    def _refresh_network_table(self):
        """Refresh network table with all mesh nodes."""
        try:
            table = self.query_one("#network-table", DataTable)
            table.clear()

            # Get filter text
            filter_text = ""
            try:
                filter_input = self.query_one("#network-filter", Input)
                filter_text = filter_input.value.strip().lower()
            except Exception:
                pass

            nodes = self.engine.get_mesh_nodes()
            matched = 0
            total = len(nodes)

            for node in sorted(nodes, key=lambda n: n.get("last_seen", 0), reverse=True):
                dest = node.get("dest_short", "Unknown")
                identity = node.get("identity_short", "Unknown")
                hops = str(node.get("hops", "?"))
                app_data = node.get("app_data", "")

                # Apply filter
                if filter_text:
                    searchable = f"{dest} {identity} {app_data}".lower()
                    if filter_text not in searchable:
                        continue

                matched += 1

                from datetime import datetime
                try:
                    last_seen = datetime.fromtimestamp(
                        node["last_seen"]
                    ).strftime("%H:%M:%S")
                except Exception:
                    last_seen = "Unknown"

                table.add_row(dest, identity, hops, app_data, last_seen)

            # Update header with count
            if filter_text:
                try:
                    header = self.query_one("#tab-network Static")
                    header.update(
                        f" [bold]📡 Mesh Network[/bold]  "
                        f"[dim]Showing {matched}/{total} nodes matching '{filter_text}'[/dim]"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _init_settings_table(self):
        """Initialize settings table."""
        try:
            table = self.query_one("#settings-table", DataTable)
            table.add_columns("Setting", "Value", "Default")
            self._refresh_settings_table()
        except Exception:
            pass

    def _refresh_settings_table(self):
        """Refresh settings table data."""
        try:
            table = self.query_one("#settings-table", DataTable)
            table.clear()

            settings = config.load_settings()
            defaults = config.DEFAULT_SETTINGS

            for key, value in sorted(settings.items()):
                default = defaults.get(key, "")
                table.add_row(key, str(value), str(default))
        except Exception:
            pass

    def _init_interfaces_table(self):
        """Initialize the Reticulum interfaces table."""
        try:
            table = self.query_one("#interfaces-table", DataTable)
            table.add_columns("Name", "Type", "Status", "Details", "RX", "TX")
        except Exception:
            pass

    def _refresh_interfaces_table(self):
        """Refresh interfaces table with live data."""
        try:
            table = self.query_one("#interfaces-table", DataTable)
            table.clear()

            interfaces = self.engine.get_interfaces()
            for iface in interfaces:
                status = "[green]● Online[/green]" if iface.get("online") else "[red]● Offline[/red]"
                rx = GhostFile._human_size(iface.get("rxb", 0))
                tx = GhostFile._human_size(iface.get("txb", 0))

                table.add_row(
                    iface.get("name", "Unknown"),
                    iface.get("type", "Unknown"),
                    status,
                    iface.get("details", ""),
                    rx,
                    tx,
                )
        except Exception:
            pass

    # ─── Actions ───────────────────────────────────────────────────────────

    def action_seed(self):
        """Focus the input for seeding."""
        inp = self.query_one("#action-input", Input)
        inp.placeholder = "Enter file path to seed..."
        inp.focus()
        inp.value = ""
        # Tag so we know what to do on submit
        inp._phantom_action = "seed"

    def action_download(self):
        """Focus the input for downloading."""
        inp = self.query_one("#action-input", Input)
        inp.placeholder = "Enter destination hash or .ghost file path to download..."
        inp.focus()
        inp.value = ""
        inp._phantom_action = "download"

    def action_refresh(self):
        """Manual refresh."""
        self._refresh_ui()
        self._refresh_ghost_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        # Handle settings save button separately
        if event.button.id == "btn-save-setting":
            self._save_setting()
            return

        inp = self.query_one("#action-input", Input)
        value = inp.value.strip()

        if not value:
            return

        if event.button.id == "btn-seed":
            self.run_worker(
                lambda: self.engine.seed_file(value),
                thread=True,
            )
            inp.value = ""
        elif event.button.id == "btn-download":
            self.run_worker(
                lambda: self.engine.download_file(value),
                thread=True,
            )
            inp.value = ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input."""
        value = event.value.strip()
        if not value:
            return

        action = getattr(event.input, "_phantom_action", None)

        if action == "seed":
            self.run_worker(
                lambda: self.engine.seed_file(value),
                thread=True,
            )
        elif action == "download":
            self.run_worker(
                lambda: self.engine.download_file(value),
                thread=True,
            )
        else:
            # Auto-detect: if it looks like a hex hash, download; else seed
            if len(value) == 32 and all(c in '0123456789abcdef' for c in value.lower()):
                self.run_worker(
                    lambda: self.engine.download_file(value),
                    thread=True,
                )
            elif value.endswith(config.GHOST_EXTENSION):
                self.run_worker(
                    lambda: self.engine.download_file(value),
                    thread=True,
                )
            else:
                self.run_worker(
                    lambda: self.engine.seed_file(value),
                    thread=True,
                )

        event.input.value = ""

    def action_transfer_click(self, transfer_id, state):
        """Handle click on a transfer row — stop, resume, or remove."""
        if state in ("complete",):
            # Remove completed transfer
            self.run_worker(
                lambda: self.engine.remove_transfer(transfer_id),
                thread=True,
            )
        elif state in ("failed", "cancelled", "stopped"):
            # Resume the transfer (works for both seeds and downloads)
            self.run_worker(
                lambda: self.engine.resume_transfer(transfer_id),
                thread=True,
            )
        else:
            # Stop the transfer
            self.run_worker(
                lambda: self.engine.stop_transfer(transfer_id),
                thread=True,
            )

    def _save_setting(self):
        """Save a setting from the settings edit bar."""
        try:
            key_input = self.query_one("#settings-key-input", Input)
            val_input = self.query_one("#settings-value-input", Input)

            key = key_input.value.strip()
            value = val_input.value.strip()

            if not key or not value:
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "warning",
                    "message": "Both setting name and value are required"
                })
                return

            # Parse value (int, float, bool, or string)
            parsed = value
            if value.lower() == "true":
                parsed = True
            elif value.lower() == "false":
                parsed = False
            else:
                try:
                    parsed = int(value)
                except ValueError:
                    try:
                        parsed = float(value)
                    except ValueError:
                        pass

            try:
                config.update_setting(key, parsed)
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f"✓ Setting '{key}' updated to: {parsed}"
                })
                self._refresh_settings_table()
                key_input.value = ""
                val_input.value = ""
            except KeyError as e:
                self._append_log({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "error",
                    "message": f"Unknown setting: {key}"
                })
        except Exception:
            pass

    def on_unmount(self) -> None:
        """Clean up on exit."""
        self.engine.stop()


def run_tui(rns_config=None):
    """Launch the TUI. Starts RNS in main thread first."""
    import sys
    from rich.console import Console
    console = Console()

    console.print("\n[bold cyan]Starting Reticulum Phantom TUI...[/bold cyan]")
    console.print("[dim]Initializing network stack...[/dim]")

    # Start engine in main thread (RNS needs main thread for signals)
    engine = PhantomEngine(rns_config)
    engine.start()

    console.print("[bold green]✓ Engine ready. Launching dashboard...[/bold green]\n")

    app = PhantomTUI(engine=engine)
    app.run()

    # Clean up
    engine.stop()
