"""
Reticulum Phantom — Configuration & Path Management

Handles all application paths, default settings, and configuration
persistence. Platform-aware for Windows, macOS, and Linux.
"""

import os
import sys
import json
import platform


# ─── App Constants ─────────────────────────────────────────────────────────────

APP_NAME = "phantom"
APP_VERSION = "0.7.0"
GHOST_EXTENSION = ".ghost"
GHOST_FORMAT_VERSION = 1

# RNS app namespace — all destinations use this prefix
RNS_APP_NAME = "phantom"

# ─── Default Settings ─────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 1_048_576    # 1 MB
MIN_CHUNK_SIZE = 65_536           # 64 KB
MAX_CHUNK_SIZE = 16_777_216       # 16 MB

DEFAULT_ANNOUNCE_INTERVAL = 600      # 10 min — seeder heartbeat (path keepalive only, PEX handles discovery)
ANNOUNCE_STAGGER_PER_FILE = 2     # seconds between each file's initial announce in seed-all
ANNOUNCE_STAGGER_MAX = 5          # max seconds between announces (for large libraries)
DEFAULT_TRANSFER_TIMEOUT = 120    # seconds (for data transfers)
DEFAULT_LINK_TIMEOUT = 10         # seconds (for link establishment — fast failover)
DEFAULT_PATH_TIMEOUT = 30         # seconds
DEFAULT_DISCOVERY_WINDOW = 5      # seconds to collect seeders (more join during download)
MAX_SWARM_PEERS = 5               # max simultaneous peer connections

# Reverse discovery: leecher announces "I want X", seeders respond
# With PEX handling primary peer discovery, this is only a fallback.
# Must stay above Hub's announce_rate_target (typically 60-120s).
WANT_ANNOUNCE_INTERVAL = 120       # seconds between want re-announces

# TCP/IP defaults for initial transport
DEFAULT_TCP_HOST = "0.0.0.0"
DEFAULT_TCP_PORT = 7777


# ─── Platform-Aware Paths ─────────────────────────────────────────────────────

def get_data_dir():
    """Get the platform-appropriate data directory for Phantom."""
    system = platform.system()

    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "ReticulumPhantom")
    elif system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "ReticulumPhantom")
    else:
        # Linux / BSD / other Unix
        xdg = os.environ.get("XDG_DATA_HOME",
                             os.path.join(os.path.expanduser("~"), ".local", "share"))
        return os.path.join(xdg, "reticulum-phantom")


def get_config_dir():
    """Get the platform-appropriate config directory."""
    system = platform.system()

    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "ReticulumPhantom")
    elif system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Preferences", "ReticulumPhantom")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME",
                             os.path.join(os.path.expanduser("~"), ".config"))
        return os.path.join(xdg, "reticulum-phantom")


# ─── Directory Structure ──────────────────────────────────────────────────────

DATA_DIR = get_data_dir()
CONFIG_DIR = get_config_dir()

IDENTITY_FILE = os.path.join(DATA_DIR, "identity")
DATABASE_FILE = os.path.join(DATA_DIR, "phantom.db")
DOWNLOADS_DIR = os.path.join(DATA_DIR, "downloads")
CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
GHOSTS_DIR = os.path.join(DATA_DIR, "ghosts")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "phantom.log")


def ensure_directories():
    """Create all required directories if they don't exist."""
    dirs = [DATA_DIR, CONFIG_DIR, DOWNLOADS_DIR, CHUNKS_DIR, GHOSTS_DIR]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


# ─── Settings Management ──────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "chunk_size": DEFAULT_CHUNK_SIZE,
    "announce_interval": DEFAULT_ANNOUNCE_INTERVAL,
    "transfer_timeout": DEFAULT_TRANSFER_TIMEOUT,
    "auto_seed_after_download": True,
    "tcp_enabled": True,
    "tcp_host": DEFAULT_TCP_HOST,
    "tcp_port": DEFAULT_TCP_PORT,
    "max_concurrent_transfers": 3,
    "download_directory": DOWNLOADS_DIR,
    "log_level": "info",
}


def load_settings():
    """Load settings from disk, falling back to defaults."""
    ensure_directories()

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                user_settings = json.load(f)
            # Merge with defaults (user settings override)
            merged = {**DEFAULT_SETTINGS, **user_settings}
            return merged
        except (json.JSONDecodeError, IOError):
            pass

    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """Persist settings to disk."""
    ensure_directories()

    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def update_setting(key, value):
    """Update a single setting and save."""
    settings = load_settings()
    if key not in DEFAULT_SETTINGS:
        raise KeyError(f"Unknown setting: {key}")
    settings[key] = value
    save_settings(settings)
    return settings


# ─── RNS Network Auto-Configuration ──────────────────────────────────────────

# Default mesh entry point — the Sideband Hub is the most widely
# available public Reticulum transport node.
SIDEBAND_HUB_HOST = "sideband.connect.reticulum.network"
SIDEBAND_HUB_PORT = 7822

def ensure_rns_connectivity():
    """
    Ensure the Reticulum config has at least one enabled network interface.
    
    If the user's ~/.reticulum/config has all interfaces disabled (or none
    configured), this function auto-enables the Sideband Hub TCP interface
    so Phantom can reach the global Reticulum mesh out of the box.
    
    Without this, Phantom only works locally on the same machine.
    """
    import re

    # Find the RNS config file
    rns_config_paths = [
        os.path.expanduser("~/.reticulum/config"),
        os.path.expanduser("~/.config/reticulum/config"),
    ]
    if platform.system() == "Windows":
        rns_config_paths.insert(0, os.path.join(
            os.environ.get("USERPROFILE", os.path.expanduser("~")),
            ".reticulum", "config"
        ))

    config_path = None
    for p in rns_config_paths:
        if os.path.isfile(p):
            config_path = p
            break

    if not config_path:
        # No RNS config exists yet — RNS will create one on first run.
        # We'll create a minimal one with Sideband Hub enabled.
        config_path = rns_config_paths[0]
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(_minimal_rns_config())
        print(f"ℹ Created Reticulum config with Sideband Hub: {config_path}")
        return True

    # Read existing config
    with open(config_path, "r") as f:
        content = f.read()

    # Check if ANY interface is enabled
    # Look for patterns like "enabled = yes" or "enabled = true" or "enabled = Yes"
    # that appear AFTER an interface header [[...]]
    has_enabled_interface = False
    in_interfaces = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "[interfaces]":
            in_interfaces = True
            continue
        if in_interfaces and stripped.startswith("[") and not stripped.startswith("[["):
            break  # Left interfaces section
        if in_interfaces:
            if re.match(r'^\s*(interface_)?enabled\s*=\s*(yes|true|Yes|True|1)\s*$', stripped):
                has_enabled_interface = True
                break

    if has_enabled_interface:
        return False  # Already has connectivity

    # No enabled interfaces — enable or add Sideband Hub
    if "Sideband Hub" in content or "sideband.connect.reticulum.network" in content:
        # Sideband Hub exists but is disabled — enable it
        content = re.sub(
            r'(\[\[Sideband Hub\]\].*?enabled\s*=\s*)(No|False|no|false|0)',
            r'\g<1>Yes',
            content,
            count=1,
            flags=re.DOTALL
        )
        with open(config_path, "w") as f:
            f.write(content)
        print("ℹ Auto-enabled Sideband Hub for mesh connectivity")
    else:
        # No Sideband Hub at all — append it
        hub_config = f"""
  # Auto-added by Phantom for mesh connectivity
  [[Sideband Hub]]
    type = TCPClientInterface
    enabled = Yes
    target_host = {SIDEBAND_HUB_HOST}
    target_port = {SIDEBAND_HUB_PORT}
"""
        with open(config_path, "a") as f:
            f.write(hub_config)
        print("ℹ Added Sideband Hub interface for mesh connectivity")

    return True


def _minimal_rns_config():
    """Generate a minimal RNS config with Sideband Hub enabled."""
    return f"""[reticulum]
  enable_transport = False
  share_instance = Yes

[logging]
  loglevel = 4

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = No

  # Sideband Hub — global Reticulum mesh entry point
  [[Sideband Hub]]
    type = TCPClientInterface
    enabled = Yes
    target_host = {SIDEBAND_HUB_HOST}
    target_port = {SIDEBAND_HUB_PORT}
"""

