"""
Reticulum Phantom — Rich Terminal UI

Premium terminal output using the Rich library.
Handles all visual presentation: tables, panels, progress bars,
live displays, and debug logging.
"""

import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress, BarColumn, TextColumn, TimeRemainingColumn,
    SpinnerColumn, TaskProgressColumn
)
from rich.text import Text
from rich import box

from . import config


# Global console instance
console = Console()

# ─── ASCII Art Banner ──────────────────────────────────────────────────────────

BANNER = r"""
[bold cyan]
  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝
[/bold cyan]
[dim]  Reticulum Phantom — Decentralized Encrypted File Sharing[/dim]
[dim]  v{version} | .ghost files | Mesh-native P2P[/dim]
""".replace("{version}", config.APP_VERSION)


def print_banner():
    """Print the Phantom banner."""
    console.print(BANNER)


# ─── Identity Display ─────────────────────────────────────────────────────────

def print_identity(info):
    """
    Display identity information in a styled panel.

    Args:
        info: Dict from PhantomIdentity.get_info()
    """
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("🔑 Identity Hash", info["hash_pretty"])
    table.add_row("📋 Full Hash", info["hash"])
    table.add_row("🔐 Key Size", f"{info['public_key_size']} bits")
    table.add_row("📐 Curve", info["curve"])
    table.add_row("📁 Stored At", info["identity_path"])

    panel = Panel(
        table,
        title="[bold white]👻 Phantom Identity[/bold white]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def print_identity_created(info):
    """Display a new identity creation message."""
    console.print()
    console.print("[bold green]✓ New identity created![/bold green]")
    print_identity(info)
    console.print(
        "[dim]This identity is your permanent node ID on the Reticulum mesh.[/dim]"
    )
    console.print(
        "[dim yellow]⚠ Back up your identity file — losing it means losing "
        "your node identity.[/dim yellow]"
    )


# ─── Ghost File Display ───────────────────────────────────────────────────────

def print_ghost_info(info):
    """
    Display .ghost file information.

    Args:
        info: Dict from GhostFile.get_info_dict()
    """
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_column("Key", style="bold magenta")
    table.add_column("Value", style="white")

    table.add_row("📄 File Name", info["name"])
    table.add_row("📦 File Size", info["file_size_human"])
    table.add_row("🧩 Chunks", f"{info['chunk_count']} × {info['chunk_size_human']}")
    table.add_row("🔒 File Hash", info["file_hash"][:32] + "...")
    table.add_row("👻 Ghost Hash", info["ghost_hash"])
    table.add_row("📅 Created", info["created_at"])
    table.add_row("👤 Creator", info["created_by"])
    table.add_row("💬 Comment", info["comment"])
    table.add_row("📋 Version", str(info["version"]))

    panel = Panel(
        table,
        title="[bold white]👻 Ghost File[/bold white]",
        border_style="magenta",
        padding=(1, 2),
    )
    console.print(panel)


def print_ghost_created(ghost_path, info):
    """Display ghost file creation success."""
    console.print()
    console.print(f"[bold green]✓ Ghost file created: {ghost_path}[/bold green]")
    print_ghost_info(info)
    console.print()
    console.print(
        f"[bold]Share this ghost hash with peers:[/bold] "
        f"[bold cyan]{info['ghost_hash']}[/bold cyan]"
    )


# ─── Seeding Display ──────────────────────────────────────────────────────────

def print_seeding_started(stats):
    """Display seeding start message."""
    console.print()
    console.print(
        Panel(
            f"[bold green]↑ SEEDING[/bold green]  {stats['name']}\n\n"
            f"[bold]Ghost Hash:[/bold]   {stats['ghost_hash']}\n"
            f"[bold]Destination:[/bold]  {stats['destination']}\n\n"
            f"[dim]Share the ghost hash with peers so they can download.\n"
            f"Press Ctrl+C to stop seeding.[/dim]",
            title="[bold white]👻 Phantom Seeder[/bold white]",
            border_style="green",
            padding=(1, 2),
        )
    )


def print_seeding_status(stats):
    """Print a one-line seeding status update."""
    console.print(
        f"  [green]↑[/green] Peers: {stats['active_peers']} | "
        f"Chunks served: {stats['chunks_served']} | "
        f"Uploaded: {stats['total_uploaded_human']} | "
        f"Uptime: {_format_duration(stats['uptime_seconds'])}",
        end="\r"
    )


# ─── Download Display ─────────────────────────────────────────────────────────

def create_download_progress():
    """Create a Rich Progress bar for downloads."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("{task.fields[speed]}"),
        TimeRemainingColumn(),
        console=console,
    )


def print_download_started(ghost_hash):
    """Display download start message."""
    console.print()
    console.print(
        f"[bold blue]↓ DOWNLOADING[/bold blue] ghost:{ghost_hash[:16]}..."
    )


def print_download_complete(stats):
    """Display download completion message."""
    elapsed = stats.get("elapsed", 0)

    console.print()
    console.print(
        Panel(
            f"[bold green]✓ DOWNLOAD COMPLETE[/bold green]\n\n"
            f"[bold]File:[/bold]          {stats.get('name', 'Unknown')}\n"
            f"[bold]Size:[/bold]          {stats.get('bytes_received_human', '0 B')}\n"
            f"[bold]Chunks:[/bold]        {stats.get('chunks_received', 0)}/{stats.get('total_chunks', 0)}\n"
            f"[bold]Time:[/bold]          {_format_duration(elapsed)}\n"
            f"[bold]Avg Speed:[/bold]     {stats.get('speed_human', '0 B/s')}",
            title="[bold white]👻 Download Complete[/bold white]",
            border_style="green",
            padding=(1, 2),
        )
    )


def print_download_failed(error):
    """Display download failure."""
    console.print()
    console.print(
        f"[bold red]✗ Download failed:[/bold red] {error}"
    )


# ─── Settings Display ─────────────────────────────────────────────────────────

def print_settings(settings):
    """Display current settings."""
    table = Table(show_header=True, box=box.ROUNDED, padding=(0, 2))
    table.add_column("Setting", style="bold yellow")
    table.add_column("Value", style="white")
    table.add_column("Default", style="dim")

    defaults = config.DEFAULT_SETTINGS

    for key, value in sorted(settings.items()):
        default = defaults.get(key, "")
        style = "" if value == default else "bold green"
        table.add_row(key, str(value), str(default), style=style)

    panel = Panel(
        table,
        title="[bold white]⚙ Phantom Settings[/bold white]",
        border_style="yellow",
        padding=(1, 2),
    )
    console.print(panel)


# ─── Debug / Log Display ──────────────────────────────────────────────────────

def print_debug_header():
    """Print debug mode header."""
    console.print()
    console.print(
        Panel(
            "[bold]Live RNS debug log — all Reticulum network activity\n"
            "[dim]Press Ctrl+C to exit[/dim]",
            title="[bold white]🔍 Phantom Debug Mode[/bold white]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()


# ─── Utility ──────────────────────────────────────────────────────────────────

def print_error(message):
    """Display an error message."""
    console.print(f"[bold red]✗ Error:[/bold red] {message}")


def print_success(message):
    """Display a success message."""
    console.print(f"[bold green]✓[/bold green] {message}")


def print_info(message):
    """Display an info message."""
    console.print(f"[bold blue]ℹ[/bold blue] {message}")


def print_warning(message):
    """Display a warning message."""
    console.print(f"[bold yellow]⚠[/bold yellow] {message}")


def _format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h)}h {int(m)}m {int(s)}s"
