# 👻 Reticulum Phantom

**Decentralized, end-to-end encrypted peer-to-peer file sharing over the [Reticulum](https://reticulum.network/) mesh network.**

> The first file-sharing application built natively on Reticulum. No central servers. No trackers. No cleartext. Just the mesh.

[![License: MIT](https://img.shields.io/badge/License-MIT-cyan.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![Reticulum](https://img.shields.io/badge/Reticulum-Mesh_Network-purple.svg)](https://reticulum.network/)

---

## What is Phantom?

Phantom lets you share files over [Reticulum](https://reticulum.network/) — a cryptographic mesh networking stack designed for resilient, long-range, low-bandwidth communications. Think of it as a torrent client, but:

- **🔐 Encrypted by default** — All transfers use Reticulum's E2E encryption (X25519/Ed25519)
- **🌐 Fully decentralized** — No trackers, no central servers, no DNS
- **📡 Mesh-native** — Works over TCP/IP, LoRa, packet radio, serial links, or any Reticulum interface
- **👻 .ghost files** — Like `.torrent` files, but for the mesh
- **🧩 Chunked transfers** — Files are split into verified chunks for reliable delivery
- **🆔 Cryptographic identity** — Your node ID is a persistent keypair, not an IP address

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd phantom

# Install dependencies
pip install -r requirements.txt

# Or install as a package
pip install -e .
```

### Create Your Identity

```bash
# Create a new Phantom identity (first time only)
python phantom.py identity --new
```

### Share a File

```bash
# Convert any file to .ghost format
python phantom.py create myfile.zip

# Start seeding it on the mesh
python phantom.py seed myfile.zip
# → Outputs a ghost hash like: a1b2c3d4e5f6...
```

### Download a File

```bash
# Download using a ghost hash
python phantom.py download a1b2c3d4e5f6...

# Or download using a .ghost file
python phantom.py download myfile.zip.ghost
```

---

## Commands

| Command | Description |
|---------|-------------|
| `phantom create <file>` | Convert a file to `.ghost` format |
| `phantom info <ghost_file>` | Display `.ghost` file metadata |
| `phantom identity` | Show your node identity |
| `phantom identity --new` | Create a new identity |
| `phantom seed <file>` | Start seeding a file on the mesh |
| `phantom download <hash>` | Download by ghost hash |
| `phantom settings` | View/edit settings |
| `phantom debug` | Live Reticulum debug log |

### Command Details

#### `phantom create`
```bash
python phantom.py create myfile.zip
python phantom.py create myfile.zip -c "My awesome file"
python phantom.py create myfile.zip -o /path/to/output.ghost
python phantom.py create myfile.zip --chunk-size 262144  # 256KB chunks
```

#### `phantom seed`
```bash
# Seed a regular file (auto-creates .ghost)
python phantom.py seed myfile.zip

# Seed using an existing .ghost file
python phantom.py seed myfile.zip.ghost
```

#### `phantom download`
```bash
# Download by ghost hash (fetches manifest from seeder)
python phantom.py download a1b2c3d4e5f6789012345678

# Download using a .ghost file
python phantom.py download myfile.zip.ghost
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

---

## The .ghost File Format

A `.ghost` file is the Phantom equivalent of a `.torrent` file. It's a compact [msgpack](https://msgpack.org/)-encoded binary containing:

```
┌─────────────────────────────────────┐
│ ghost_version: 1                    │
│ name: "ubuntu-24.04-desktop.iso"    │
│ file_size: 4800000000               │
│ chunk_size: 1048576  (1MB)          │
│ chunk_count: 4578                   │
│ file_hash: "sha256..."             │
│ chunk_hashes: ["sha256...", ...]    │
│ created_at: 1713420000              │
│ created_by: "identity_hash"         │
│ comment: "Official Ubuntu ISO"      │
│ app_name: "phantom"                 │
└─────────────────────────────────────┘
```

The **ghost hash** (first 16 bytes of the file's SHA-256) is the unique identifier used for mesh discovery.

---

## How It Works

```
 Seeder                              Reticulum Mesh                        Leecher
┌────────┐                          ┌──────────────┐                     ┌────────┐
│ Create │  announce(ghost_hash)    │              │  request_path()     │ Search │
│ .ghost ├─────────────────────────►│   Transport  │◄────────────────────┤  for   │
│  file  │                          │    Nodes     │                     │ seeder │
└───┬────┘                          └──────┬───────┘                     └───┬────┘
    │                                      │                                 │
    │  E2E Encrypted Link (X25519)         │  path_found!                    │
    │◄─────────────────────────────────────┼─────────────────────────────────┤
    │                                      │                                 │
    │  request("manifest")                 │                                 │
    │◄─────────────────────────────────────┼─────────────────────────────────┤
    │  response(ghost_metadata)            │                                 │
    ├──────────────────────────────────────┼────────────────────────────────►│
    │                                      │                                 │
    │  request("chunk", index=0)           │                                 │
    │◄─────────────────────────────────────┼─────────────────────────────────┤
    │  response(chunk_data)                │                                 │
    ├──────────────────────────────────────┼────────────────────────────────►│
    │  ... repeat for all chunks ...       │                                 │
    │                                      │                          ┌─────┴─────┐
    │                                      │                          │ Assemble  │
    │                                      │                          │ & Verify  │
    │                                      │                          └───────────┘
```

### Architecture

1. **Identity** — Each node has a persistent X25519/Ed25519 keypair
2. **Ghost File** — Metadata descriptor with per-chunk SHA-256 hashes
3. **Seeder** — Creates an RNS destination, announces on the mesh, serves chunks
4. **Leecher** — Discovers seeders via mesh routing, downloads verified chunks
5. **Network** — Thin wrapper over `RNS.Reticulum` for transport management

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
| `announce_interval` | 300 (5min) | Re-announce interval in seconds |
| `transfer_timeout` | 120 (2min) | Transfer timeout in seconds |
| `auto_seed_after_download` | true | Auto-seed after downloading |
| `tcp_enabled` | true | Enable TCP/IP transport |
| `tcp_host` | 0.0.0.0 | TCP listen address |
| `tcp_port` | 7777 | TCP listen port |
| `max_concurrent_transfers` | 3 | Max simultaneous transfers |
| `download_directory` | (platform) | Where to save downloads |
| `log_level` | info | Logging verbosity |

---

## Network Configuration

Phantom uses Reticulum for all networking. By default, it uses `AutoInterface` to discover peers on the local network.

For internet-wide sharing, add TCP interfaces to your Reticulum config (`~/.reticulum/config`):

```ini
# Connect to a public Reticulum transport node
[[TCP Client]]
    type = TCPClientInterface
    target_host = <transport_node_ip>
    target_port = 4242
    enabled = true
```

See the [Reticulum documentation](https://markqvist.github.io/Reticulum/manual/) for full networking configuration.

---

## Platform Support

| Platform | Status |
|----------|--------|
| Windows  | ✅ Supported |
| macOS    | ✅ Supported |
| Linux    | ✅ Supported |

Data is stored in platform-appropriate locations:
- **Windows**: `%APPDATA%\ReticulumPhantom\`
- **macOS**: `~/Library/Application Support/ReticulumPhantom/`
- **Linux**: `~/.local/share/reticulum-phantom/`

---

## Development

```bash
# Clone and setup
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd phantom
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
- [ ] **v0.2** — Multi-peer swarm: parallel downloads from multiple seeders
- [ ] **v0.3** — LXMF integration: offline chunk caching via propagation nodes
- [ ] **v0.4** — TUI dashboard: interactive terminal interface with Textual
- [ ] **v0.5** — DHT-like peer discovery and reputation system

---

## License

[MIT](LICENSE) — Free and open source.

---

## Acknowledgments

- [Reticulum Network Stack](https://reticulum.network/) by Mark Qvist
- Built on the principles of decentralization, privacy, and resilience

---

<p align="center">
  <b>👻 Share files. Not metadata.</b>
</p>
