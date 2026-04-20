# 🧪 Testing Reticulum Phantom — Multi-PC Guide

A step-by-step guide to test P2P file sharing across the internet or LAN using the Reticulum mesh.

> **v0.8.0** — Phantom respects your Reticulum configuration. Configure your own interfaces for mesh connectivity.

---

## Prerequisites (All PCs)

```bash
# Install Python 3.8+ then:
pip install rns rich

# Clone the repo (or download the zip)
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd reticulum-phantom
pip install -r requirements.txt
```

### Configure Reticulum Interfaces

Before using Phantom, you need at least one Reticulum interface configured. Phantom never modifies your Reticulum config — you are in control.

Find interface definitions at:
- **[Reticulum Getting Started](https://markqvist.github.io/Reticulum/manual/gettingstartedfast.html)** — Official configuration guide
- **[directory.rns.recipes](https://directory.rns.recipes)** — Community interface directory
- **[rmap.world](https://rmap.world)** — Network map of active nodes

Edit `~/.reticulum/config` (or `%USERPROFILE%\.reticulum\config` on Windows) to add your interfaces.

---

## Quick Test: 2 PCs Over the Internet

### PC-A (Seeder)

```bash
# Seed a file — ghost file is auto-created next to the original
python phantom.py seed movie.mkv

# Or seed an entire directory
python phantom.py seed-all /path/to/movies/
```

Output:
```
✓ Seeding 1 files  Press Ctrl+C to stop all

  ↑ movie.mkv | ghost:a1b2c3d4e5f6... | dest:138e6b9ca155dd6f...
```

The `.ghost` file is saved right next to `movie.mkv` — share it with your friend.

### PC-B (Leecher)

Copy the `.ghost` file from PC-A (email, USB, Discord, etc.), then:

```bash
python phantom.py download movie.mkv.ghost
```

Output:
```
ℹ Announcing want — waiting for seeders...
ℹ Requesting path to seeder: 138e6b9ca155...
ℹ Encrypted link established with seeder ✓
ℹ Connected to 1 peer(s)
⠋ Downloading movie.mkv [150/705] ━━━━━━━━╺━━━━━━━━━━━━━━  21%  1.5 MB/s  0:06:12
```

**That's it.** No IPs, no ports, no firewall rules. The mesh handles everything.

---

## Multi-Seeder Test: 3 PCs

This tests the swarm — multiple seeders serving the same file simultaneously.

### PC-A: Original Seeder

```bash
python phantom.py seed-all /path/to/files/
# → Ghost files created next to each file
# → Share the .ghost files with PC-B and PC-C
```

### PC-B: Download + Re-seed

```bash
# Download the file
python phantom.py download movie.mkv.ghost -o ~/Downloads

# After download completes, re-seed it
python phantom.py seed ~/Downloads/movie.mkv
# → YOUR dest is added to the ghost file's seeder_dests list
# → Share YOUR .ghost file — it now contains BOTH seeders!
```

### PC-C: Download from Swarm

```bash
# Use PC-B's .ghost file (has both PC-A and PC-B as seeders)
python phantom.py download movie.mkv.ghost
# → Tries PC-A dest, then PC-B dest
# → Downloads chunks from ALL reachable seeders simultaneously
# → If PC-A goes offline, PC-B picks up the remaining chunks
```

---

## Verifying Mesh Connectivity

### Check Your Interfaces

```bash
# Linux/Mac
cat ~/.reticulum/config

# Windows (PowerShell)
type $env:USERPROFILE\.reticulum\config
```

You should see at least one enabled interface, for example:
```ini
[[My Transport Node]]
    type = TCPClientInterface
    enabled = Yes
    target_host = your.transport.node
    target_port = 4242
```

> If no interfaces are configured, Phantom will display a setup guide pointing to the Reticulum documentation and interface directories.

### Test Path Resolution

```bash
# Check if a seeder destination is reachable on the mesh
rnpath <destination_hash> -w 10
```

Expected output:
```
Path found, destination <138e6b9ca155dd6f...> is 2 hops away via <521c87a8...>
```

---

## LAN Testing (Optional)

If you want to test on a local network:

### PC-A: Add a TCP Server

Edit `~/.reticulum/config` (or `%USERPROFILE%\.reticulum\config` on Windows):

```ini
  [[Phantom TCP Server]]
    type = TCPServerInterface
    listen_ip = 0.0.0.0
    listen_port = 4242
    enabled = yes
```

### PC-B: Connect to PC-A

```ini
  [[Phantom TCP Client]]
    type = TCPClientInterface
    target_host = 192.168.1.100  # PC-A's IP
    target_port = 4242
    enabled = yes
```

> ⚠️ Replace `192.168.1.100` with PC-A's actual IP address!

### Firewall (Windows)

```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="Reticulum Phantom" dir=in action=allow protocol=tcp localport=4242
```

---

## Same-PC Testing (Two Terminals)

You can test locally without multiple PCs. Reticulum's shared transport instance handles it.

**Terminal 1:**
```bash
python phantom.py seed testfile.txt
```

**Terminal 2:**
```bash
python phantom.py download testfile.txt.ghost
```

---

## Debug Mode

If something doesn't connect, use debug mode to see all RNS activity:

```bash
python phantom.py debug
```

This shows interface discovery, announce propagation, path requests, link establishment, and packet flow.

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Link establishment timed out` | No enabled interfaces or no path | Check `~/.reticulum/config` — ensure at least one interface is enabled |
| Seeder found instantly but link fails | Stale cached path | Restart the seeder, wait 10s, retry download |
| `Destination hash mismatch` | Leecher found another leecher, not a seeder | Update to v0.6.2+ (type filtering fix) |
| Progress shows 1% with 600+ chunks done | Stale progress bar code | Update to v0.6.3 (progress fix) |
| Speed shows 500+ MB/s | Cached chunks counted as transfer | Update to v0.6.3 (speed fix) |
| `WinError 10038` socket error | TCP connection dropped | Usually auto-reconnects. Restart if persistent |
| No seeders found | Ghost file missing `seeder_dest` | Re-generate ghost file from seeder, or wait for announce discovery (~1-2 min) |

---

## Architecture Diagram

```
  PC-A (Seeder)                   Transport Nodes                   PC-B (Leecher)
 ┌──────────────┐                ┌──────────────┐                 ┌──────────────┐
 │ phantom seed │   TCP/LoRa     │  reticulum   │   TCP/LoRa      │   phantom    │
 │  movie.mkv   │◄──────────────►│  transport   │◄──────────────►│  download    │
 │              │                │  relays      │                 │  movie.ghost │
 │  announces:  │                └──────────────┘                 │              │
 │  type=seeder │                                                 │  discovers:  │
 │  ghost_hash  │ ◄──── E2E encrypted link (X25519) ───────────► │  await_path  │
 └──────────────┘       chunks + verify + assemble                └──────────────┘
       ↕                                                                 ↕
 ┌──────────────┐                                                 ┌──────────────┐
 │  .ghost file │  ──── shared via email/USB/Discord ──────────►  │  .ghost file │
 │  seeder_dests│                                                 │  hint_dests  │
 └──────────────┘                                                 └──────────────┘
```

### Key Points

- **Transport nodes** are community-run Reticulum relays — they route packets but **cannot read content** (E2E encrypted)
- **Ghost files** contain seeder destinations for fast discovery (like multi-tracker torrents)
- **Announce type filtering** ensures leechers only connect to actual seeders, not other leechers
- **Auto-failover** redistributes chunks when a seeder drops mid-transfer

---

## Quick Reference

| Action | Command |
|--------|---------|
| Seed a file | `python phantom.py seed movie.mkv` |
| Seed a directory | `python phantom.py seed-all /path/to/files/` |
| Download | `python phantom.py download movie.mkv.ghost` |
| Download to folder | `python phantom.py download movie.mkv.ghost -o ~/Desktop` |
| Check identity | `python phantom.py identity` |
| Create identity | `python phantom.py identity --new` |
| Debug mode | `python phantom.py debug` |
| Clean downloads | `python phantom.py clean` |
| Clean everything | `python phantom.py clean --all` |
