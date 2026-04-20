# 👻 Reticulum Phantom

**Decentralized, end-to-end encrypted peer-to-peer file sharing over the [Reticulum](https://reticulum.network/) mesh network.**

> The first file-sharing application built natively on Reticulum. No central servers. No trackers. No cleartext. Just the mesh.

[![License: MIT](https://img.shields.io/badge/License-MIT-cyan.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![Reticulum](https://img.shields.io/badge/Reticulum-Mesh_Network-purple.svg)](https://reticulum.network/)
[![Version](https://img.shields.io/badge/v0.8.0-Stable-green.svg)](#roadmap)

---

## What is Phantom?

Phantom lets you share files over [Reticulum](https://reticulum.network/) — a cryptographic mesh networking stack designed for resilient, long-range, low-bandwidth communications. Think of it as a torrent client, but:

- **🔐 Encrypted by default** — All transfers use Reticulum's E2E encryption (X25519/Ed25519)
- **🌐 Fully decentralized** — No trackers, no central servers, no DNS
- **📡 Mesh-native** — Works over TCP/IP, LoRa, packet radio, serial links, or any Reticulum interface
- **👻 .ghost files** — Like `.torrent` files, but for the mesh
- **🧩 Chunked transfers** — Files are split into verified chunks for reliable delivery
- **🆔 Cryptographic identity** — Your node ID is a persistent keypair, not an IP address
- **📂 Multi-file seeding** — Seed your entire library simultaneously
- **🖥️ Interactive TUI** — Full-screen terminal dashboard (optional)
- **🐝 Multi-peer swarm** — Download from multiple seeders simultaneously
- **🔄 Auto-failover** — If a seeder goes offline, others pick up instantly
- **🌍 Respects your config** — Never modifies your Reticulum configuration; you control your own interfaces
- **📋 Multi-seeder ghost files** — Like multi-tracker torrents, ghost files store all known seeders for resilience
- **⏸️ Resume support** — Downloads pick up where they left off, with accurate progress tracking
- **🔀 PEX (Peer Exchange)** — Seeders share their peer lists over encrypted Links, bypassing announce rate-limits
- **🌱 Auto-seed** — After downloading, automatically start seeding to strengthen the swarm
- **🛡️ Mesh-friendly** — Adaptive announce stagger prevents network spam, even with 1000+ files

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd reticulum-phantom

# Install dependencies
pip install -r requirements.txt

# Or install as a package
pip install -e .

# Optional: install TUI dashboard support
pip install textual
```

### Share a File (2 steps)

```bash
# 1. Seed a file (auto-creates .ghost file next to the original)
python phantom.py seed movie.mkv
#   → movie.mkv.ghost created (share THIS with friends)
#   → Seeding on the mesh...

# 2. Share the .ghost file with your friend (email, USB, Discord, etc.)
```

That's it. `seed` auto-creates your identity and the ghost file. Configure your Reticulum interfaces to connect to the mesh.

### Download a File

```bash
# Download using a .ghost file (like opening a .torrent)
python phantom.py download movie.mkv.ghost

# Download to a specific folder
python phantom.py download movie.mkv.ghost -o ~/Downloads
```

---

## The .ghost Workflow

The `.ghost` file is the Phantom equivalent of a `.torrent` file. Share it with anyone — they use it to find seeders on the mesh and download the file.

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   1. Seed:     phantom seed movie.mkv                           │
│                → creates movie.mkv.ghost next to the file       │
│                → announces on mesh, serves chunks               │
│                                                                 │
│   2. Share:    Send movie.mkv.ghost to your friend              │
│                (email, USB, Discord, whatever)                  │
│                                                                 │
│   3. Download: phantom download movie.mkv.ghost                 │
│                → auto-discovers ALL seeders, downloads in swarm │
│                → automatically starts seeding after completion  │
│                                                                 │
│   4. Swarm grows organically — every downloader becomes a       │
│      seeder, new peers discovered via PEX in real-time          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Multi-Seeder Resilience

Ghost files accumulate seeder destinations over time, like torrents with multiple trackers:

```
movie.mkv.ghost
├── seeder_dests: [
│   "8543cc3d..."  ← Original seeder (you)
│   "a1b2c3d4..."  ← Friend A (downloaded + re-seeded)
│   "e5f6a7b8..."  ← Friend B (downloaded + re-seeded)
│ ]
```

When downloading, Phantom tries **all** known seeders. If the original goes offline, any re-seeder works.

---

## Commands

| Command | Description |
|---------|-------------|
| `phantom seed <file>` | Start seeding a file (auto-creates .ghost) |
| `phantom seed-all [dir]` | Seed all files in a directory or ghost library |
| `phantom download <target>` | Download by .ghost file or destination hash |
| `phantom create <file>` | Create a .ghost file without seeding |
| `phantom info <ghost_file>` | Display `.ghost` file metadata |
| `phantom identity` | Show your node identity |
| `phantom identity --new` | Create a new identity |
| `phantom clean` | Remove temporary chunks and downloads |
| `phantom settings` | View/edit settings |
| `phantom debug` | Live Reticulum debug log |
| `phantom tui` | Launch the interactive TUI dashboard |

### Command Details

#### `phantom seed` (recommended)
```bash
# Seed a file — auto-creates .ghost, connects to mesh, starts serving
python phantom.py seed movie.mkv

# Seed from an existing .ghost file
python phantom.py seed movie.mkv.ghost

# Seed all files in a directory
python phantom.py seed-all /path/to/movies/

# Output:
#   ↑ Movie1.mkv | ghost:a1b2c3d4... | dest:138e6b9c...
#   ↑ Movie2.mkv | ghost:e5f6a7b8... | dest:9cab3d21...
#   ✓ Seeding 2 files  Press Ctrl+C to stop all
```

#### `phantom download`
```bash
# Download using a .ghost file (recommended — like opening a .torrent)
python phantom.py download movie.mkv.ghost

# Download by destination hash
python phantom.py download 138e6b9ca155dd6f592dd8507601c5c5

# Download to a specific directory
python phantom.py download movie.mkv.ghost -o ~/Desktop
```

#### `phantom create`
```bash
# Create a .ghost file without seeding
python phantom.py create myfile.zip
python phantom.py create myfile.zip -c "My awesome file"
python phantom.py create myfile.zip -o /path/to/output.ghost
python phantom.py create myfile.zip --chunk-size 262144  # 256KB chunks
```

#### `phantom clean`
```bash
# Remove chunk cache and downloads
python phantom.py clean

# Remove everything including ghost library
python phantom.py clean --all

# Only remove ghost files
python phantom.py clean --ghosts
```

#### `phantom identity`
```bash
# Show current identity
python phantom.py identity

# Create new identity
python phantom.py identity --new

# Export identity (for backup)
python phantom.py identity --export-file ~/phantom_backup.key

# Import identity (from backup)
python phantom.py identity --import-file ~/phantom_backup.key
```

#### `phantom tui`
```bash
# Launch the interactive TUI dashboard
python phantom.py tui
```

The TUI provides a full-screen dashboard with:
- Real-time transfer monitoring (seed/download)
- Ghost file browser with auto-seeding
- Network status with node filtering
- Mesh peer discovery
- Settings panel

> **Note:** The TUI requires `textual`. Install with `pip install textual`. All other commands work without it.

---

## The .ghost File Format

A `.ghost` file is a compact [msgpack](https://msgpack.org/)-encoded binary containing:

```
┌──────────────────────────────────────┐
│ ghost_version: 1                     │
│ name: "ubuntu-24.04-desktop.iso"     │
│ file_size: 4800000000                │
│ chunk_size: 1048576  (1MB)           │
│ chunk_count: 4578                    │
│ file_hash: "sha256..."              │
│ chunk_hashes: ["sha256...", ...]     │
│ created_at: 1713420000               │
│ created_by: "identity_hash"          │
│ comment: "Official Ubuntu ISO"       │
│ seeder_dest: "8543cc3d..."           │  ← Primary seeder
│ seeder_dests: ["8543cc3d...", ...]    │  ← ALL known seeders
│ app_name: "phantom"                  │
└──────────────────────────────────────┘
```

The **ghost hash** (first 16 bytes of the file's SHA-256) is the unique identifier used for mesh discovery.

> **Privacy:** `source_path` is intentionally excluded from the ghost file to avoid exposing filesystem paths.

---

## How It Works

### Discovery

Phantom uses a **three-layer discovery strategy**:

1. **Direct path resolution (fast)** — The .ghost file contains known seeder destinations. The leecher uses `await_path()` to resolve the path through the mesh — typically connects in 1-2 seconds
2. **PEX — Peer Exchange (primary)** — After connecting to ANY seeder, the leecher asks `"who else seeds this?"` over the encrypted Link. The seeder returns all known peers. This bypasses announce rate-limiting entirely because Links are never throttled
3. **Announce-based discovery (fallback)** — Leecher broadcasts "I want ghost_hash X" as an RNS announce. Seeders listening for wants re-announce themselves. Used when no seeder dests are known
4. **Continuous discovery** — During download, PEX runs every 30 seconds to find new peers joining the swarm
5. **Auto-failover** — If a seeder dies mid-transfer, its chunks redistribute to remaining peers

### PEX (Peer Exchange)

```
 Leecher connects to any seeder it knows (encrypted Link)
     │
     ├── link.request("peers")
     │   └── Seeder responds: ["fd1bbf73...", "ab12cd34...", ...]
     │
     ├── Leecher connects to each peer directly
     │   (no announces needed — Links are never rate-limited)
     │
     └── Every 30s during download: ask peers for MORE peers
         (swarm grows organically)
```

### Multi-Peer Swarm

```
 Seeder A                                                    Leecher
┌────────┐  announce(type="seeder", ghost_hash=...)          ┌────────┐
│ seed   ├──────────────────────────────────────────────────►│ listen │
│ movie  │                    Reticulum Mesh                │ for    │
└────────┘                   ┌──────────────┐              │ type=  │
                             │   Transport  │              │ seeder │
 Seeder B                    │    Nodes     │              └───┬────┘
┌────────┐  announce         │   Transport  │                  │
│ seed   ├──────────────────►│              │  PEX + 5s        │
│ movie  │                   └──────────────┘  discovery       │
└────────┘                                                     │
                                                               │
     ┌─────────────────────────────────────────────────────────┤
     │              Connect to ALL seeders                     │
     ▼                                                         ▼
 ┌────────┐  chunks 0-350                               ┌────────┐
 │Seeder A├────────────────────────────────────────────►│        │
 └────────┘                                             │ Merge  │
                                                        │ chunks │
 ┌────────┐  chunks 351-702                             │ verify │
 │Seeder B├────────────────────────────────────────────►│ hash   │
 └────────┘                                             └────────┘
```

**More seeders = faster downloads.** Chunks are distributed across all peers automatically.

### Architecture

1. **Identity** — Each node has a persistent X25519/Ed25519 keypair
2. **Ghost File** — Metadata descriptor with per-chunk SHA-256 hashes + multi-seeder destinations
3. **Seeder** — Creates a unique RNS destination per file, announces with `type: "seeder"`, serves chunks + PEX peer lists
4. **Leecher** — Discovers seeders via direct path + PEX + announce handler, downloads chunks in parallel
5. **Engine** — Thread-safe background manager for multiple concurrent seeders/leechers
6. **TUI** — Interactive terminal dashboard built with Textual (optional)
7. **Network** — Read-only connectivity check with guidance to official Reticulum docs; never modifies user config

---

## Network Configuration

Phantom **respects your Reticulum configuration** and never modifies it. You are in full control of your network interfaces, following [Reticulum's decentralized design philosophy](https://markqvist.github.io/Reticulum/manual/gettingstartedfast.html).

To connect to the mesh, configure one or more interfaces in your Reticulum config (`~/.reticulum/config`):

```ini
# Example: connect to a community transport node
[[My Transport Node]]
    type = TCPClientInterface
    enabled = yes
    target_host = your.transport.node
    target_port = 4242
```

### Finding Interfaces

- **[Reticulum Getting Started](https://markqvist.github.io/Reticulum/manual/gettingstartedfast.html)** — Official configuration guide
- **[directory.rns.recipes](https://directory.rns.recipes)** — Community interface directory
- **[rmap.world](https://rmap.world)** — Network map of active nodes

> **Tip:** It's best to have several redundant connections configured. Enable interface discovery options so your nodes can continuously discover peering opportunities as the network evolves.

If no interfaces are configured, Phantom will display a helpful setup guide pointing to these resources.

---

## Settings

View and modify settings:

```bash
# View all settings
python phantom.py settings

# Update a setting
python phantom.py settings chunk_size 262144
python phantom.py settings auto_seed_after_download false
python phantom.py settings tcp_port 8888
```

| Setting | Default | Description |
|---------|---------|-------------|
| `chunk_size` | 1048576 (1MB) | File chunk size in bytes |
| `announce_interval` | 1800 (30min) | Re-announce interval in seconds |
| `transfer_timeout` | 120 (2min) | Transfer timeout in seconds |
| `auto_seed_after_download` | true | Auto-seed after downloading |
| `tcp_enabled` | true | Enable TCP/IP transport |
| `tcp_host` | 0.0.0.0 | TCP listen address |
| `tcp_port` | 7777 | TCP listen port |
| `max_concurrent_transfers` | 3 | Max simultaneous transfers |
| `download_directory` | (platform) | Where to save downloads |
| `log_level` | info | Logging verbosity |

---

## Multi-PC Testing

### PC-A (Seeder)
```bash
# Seed a directory of files (ghost files created automatically beside each file)
python phantom.py seed-all /path/to/files/
```

### PC-B (Second Seeder)
```bash
# Copy the .ghost file from PC-A and the original file, then:
python phantom.py seed movie.mkv
# → Your dest is ADDED to seeder_dests list
```

### PC-C (Leecher — Downloads from BOTH)
```bash
# Copy the .ghost file from PC-B (has both A and B as seeders), then:
python phantom.py download movie.mkv.ghost -o ~/Desktop
# → Tries both PC-A and PC-B destinations
# → Downloads chunks from all reachable seeders simultaneously!
```

---

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| Windows  | ✅ Tested | Field-tested (Windows ↔ Linux cross-mesh) |
| Linux    | ✅ Tested | Field-tested (Ubuntu <→ Windows cross-mesh) |
| macOS    | ✅ Supported | Untested, should work |

Data is stored in platform-appropriate locations:
- **Windows**: `%APPDATA%\ReticulumPhantom\`
- **macOS**: `~/Library/Application Support/ReticulumPhantom/`
- **Linux**: `~/.local/share/reticulum-phantom/`

Ghost files are **also saved next to the source file** for easy access and sharing.

---

## Development

```bash
# Clone and setup
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd reticulum-phantom
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e .

# Run tests
python -m pytest tests/ -v

# Run in debug mode
python phantom.py debug
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Roadmap

- [x] **v0.1** — Core: `.ghost` files, identity, single-peer seed/download, CLI
- [x] **v0.2** — Multi-file seeding: `seed-all`, ghost library, source path tracking
- [x] **v0.3** — TUI dashboard: interactive terminal interface with Textual
- [x] **v0.4** — Patient discovery: announce-based + direct path, auto-failover
- [x] **v0.5** — Multi-peer swarm: parallel downloads from multiple seeders, continuous discovery
- [x] **v0.6** — Global mesh: multi-seeder ghost files, announce type filtering, resume-aware progress, cross-platform field tested
- [x] **v0.7** — PEX (Peer Exchange): seeders share peer lists over Links, bypassing announce rate-limits, bidirectional seeder discovery, stale path invalidation
- [x] **v0.8** — Decentralization fix: removed all config file modification, removed hardcoded entrypoints, connectivity guidance via official Reticulum docs *(current)*
- [ ] **v0.9** — LXMF integration: offline chunk caching via propagation nodes
- [ ] **v1.0** — DHT-like peer discovery and reputation system

---

## License

[MIT](LICENSE) — Free and open source.

---

## Acknowledgments

- [Reticulum Network Stack](https://reticulum.network/) by Mark Qvist
- Built on the principles of decentralization, privacy, and resilience

---

## Community

- 💬 **Matrix**: [#roogle-reticulum:matrix.org](https://matrix.to/#/#roogle-reticulum:matrix.org) — Roogle community chat
- 🌐 **Roogle**: [roogle.us](https://roogle.us) — More free Reticulum services

---

<p align="center">
  <b>👻 Share files. Not metadata.</b>
</p>
