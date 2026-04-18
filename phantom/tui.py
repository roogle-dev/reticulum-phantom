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


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Widgets
# ═══════════════════════════════════════════════════════════════════════════════

class PhantomBanner(Static):
    """The ASCII art banner widget."""

    BANNER = """[bold cyan]  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝[/bold cyan]"""

    def render(self):
        return Text.from_markup(self.BANNER)


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

        content = (
            f" {dot} [{status_color}]{status_text}[/{status_color}]\n"
            f" [bold cyan]↑[/bold cyan] {up}  [bold blue]↓[/bold blue] {down}\n"
            f" [dim]Active: {active} transfers[/dim]"
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
    """A single transfer progress display."""

    def __init__(self, transfer, **kwargs):
        super().__init__(**kwargs)
        self._transfer = transfer

    def update_transfer(self, transfer):
        self._transfer = transfer
        self.refresh()

    def render(self):
        t = self._transfer
        arrow = "[bold cyan]↑[/bold cyan]" if t.direction == "upload" else "[bold blue]↓[/bold blue]"

        # Progress bar
        pct = t.progress * 100
        bar_len = 20
        filled = int(bar_len * t.progress)
        bar = "█" * filled + "░" * (bar_len - filled)

        # State styling
        if t.state == "complete":
            state_str = "[bold green]COMPLETE ✓[/bold green]"
        elif t.state in ("failed", "cancelled", "stopped"):
            state_str = f"[bold red]{t.state.upper()}[/bold red]"
        elif t.state == "seeding":
            state_str = f"[green]SEEDING[/green] P:{t.peers}"
        elif t.state == "downloading":
            speed = GhostFile._human_size(t.speed) + "/s"
            state_str = f"[cyan]{speed}[/cyan]"
        else:
            state_str = f"[yellow]{t.state.upper()}[/yellow]"

        # Size
        size_str = GhostFile._human_size(t.bytes_transferred)
        chunks_str = f"{t.chunks_done}/{t.total_chunks}" if t.total_chunks else ""

        name = t.name if len(t.name) <= 40 else t.name[:37] + "..."

        content = (
            f" {arrow} {name}\n"
            f"   [cyan]{bar}[/cyan] {pct:5.1f}%  {size_str}  {chunks_str}  {state_str}"
        )
        return Text.from_markup(content)


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

    #banner {
        height: 7;
        content-align: center middle;
        margin: 0 0 1 0;
    }

    #status-bar {
        layout: horizontal;
        height: 5;
        margin: 0 1 1 1;
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

    #transfers-container {
        height: 1fr;
        margin: 0 1;
        border: solid $primary;
        padding: 1;
        overflow-y: auto;
    }

    #log-panel {
        height: 12;
        margin: 0 1 1 1;
        border: solid $accent-darken-2;
    }

    .transfer-row {
        height: 3;
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

    #ghost-table {
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
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("s", "seed", "Seed File"),
        Binding("d", "download", "Download"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, rns_config=None, **kwargs):
        super().__init__(**kwargs)
        self.engine = PhantomEngine(rns_config)
        self._transfer_widgets = {}
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()

        with TabbedContent():
            # ─── Dashboard Tab ───────────────────────────────────────
            with TabPane("Dashboard", id="tab-dashboard"):
                yield PhantomBanner(id="banner")

                with Horizontal(id="status-bar"):
                    yield NetworkPanel(id="network-panel")
                    yield IdentityPanel(id="identity-panel")

                yield Static(
                    " [bold]Active Transfers[/bold]  "
                    "[dim](S=Seed  D=Download  Q=Quit)[/dim]",
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
                        placeholder="File path to seed, or destination hash to download...",
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

            # ─── Settings Tab ────────────────────────────────────────
            with TabPane("Settings", id="tab-settings"):
                yield Static(
                    " [bold]⚙ Phantom Settings[/bold]"
                )
                yield DataTable(id="settings-table", zebra_stripes=True)

        yield Footer()

    def on_mount(self) -> None:
        """Start the engine and set up refresh timer."""
        # Start engine in background
        self.run_worker(self._start_engine, thread=True)

        # Set up periodic refresh
        self._refresh_timer = self.set_interval(2.0, self._refresh_ui)

        # Initialize tables
        self._init_ghost_table()
        self._init_settings_table()

    async def _start_engine(self) -> None:
        """Start the Phantom engine (runs in worker thread)."""
        self.engine.on_log = self._on_engine_log
        self.engine.on_transfer_update = self._on_transfer_update
        self.engine.start()

        # Initial UI update
        self.call_from_thread(self._refresh_ui)

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
                "File Name", "Size", "Chunks", "Ghost Hash", "Created"
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

                table.add_row(
                    g["name"],
                    g["size_human"],
                    str(g["chunks"]),
                    g["ghost_hash"][:16] + "...",
                    created,
                )
        except Exception:
            pass

    def _init_settings_table(self):
        """Initialize settings table."""
        try:
            table = self.query_one("#settings-table", DataTable)
            table.add_columns("Setting", "Value", "Default")

            settings = config.load_settings()
            defaults = config.DEFAULT_SETTINGS

            for key, value in sorted(settings.items()):
                default = defaults.get(key, "")
                table.add_row(key, str(value), str(default))
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
        inp.placeholder = "Enter destination hash to download..."
        inp.focus()
        inp.value = ""
        inp._phantom_action = "download"

    def action_refresh(self):
        """Manual refresh."""
        self._refresh_ui()
        self._refresh_ghost_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
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
            else:
                self.run_worker(
                    lambda: self.engine.seed_file(value),
                    thread=True,
                )

        event.input.value = ""

    def on_unmount(self) -> None:
        """Clean up on exit."""
        self.engine.stop()


def run_tui(rns_config=None):
    """Launch the TUI."""
    app = PhantomTUI(rns_config=rns_config)
    app.run()
