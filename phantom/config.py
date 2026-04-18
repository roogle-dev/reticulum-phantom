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
APP_VERSION = "0.5.0"
GHOST_EXTENSION = ".ghost"
GHOST_FORMAT_VERSION = 1

# RNS app namespace — all destinations use this prefix
RNS_APP_NAME = "phantom"

# ─── Default Settings ─────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 1_048_576    # 1 MB
MIN_CHUNK_SIZE = 65_536           # 64 KB
MAX_CHUNK_SIZE = 16_777_216       # 16 MB

DEFAULT_ANNOUNCE_INTERVAL = 1800  # seconds (30 min — mesh-friendly, avoids announce spam)
ANNOUNCE_STAGGER_PER_FILE = 2     # seconds between each file's initial announce in seed-all
DEFAULT_TRANSFER_TIMEOUT = 120    # seconds (for data transfers)
DEFAULT_LINK_TIMEOUT = 10         # seconds (for link establishment — fast failover)
DEFAULT_PATH_TIMEOUT = 30         # seconds
DEFAULT_DISCOVERY_WINDOW = 5      # seconds to collect seeders (more join during download)
MAX_SWARM_PEERS = 5               # max simultaneous peer connections

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
