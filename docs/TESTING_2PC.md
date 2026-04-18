# 🧪 Testing Reticulum Phantom Between 2 PCs

A step-by-step guide to test P2P file sharing between two machines over your LAN.

---

## Prerequisites (Both PCs)

```bash
# Install Python 3.8+ then:
pip install rns rich

# Clone the repo (or copy the folder)
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd reticulum-phantom
```

---

## Step 1: Find Your IP Addresses

On each PC, find the local IP:

**Windows:**
```powershell
ipconfig | Select-String "IPv4"
```

**Linux/Mac:**
```bash
ip addr show | grep "inet "
# or
ifconfig | grep "inet "
```

Example:
- **PC-A (Seeder):** `192.168.1.100`
- **PC-B (Leecher):** `192.168.1.200`

---

## Step 2: Configure Reticulum for TCP/IP

Reticulum needs to know how to reach the other PC. We configure this in the Reticulum config file.

### PC-A (Seeder) — Acts as the TCP Server

Edit the Reticulum config:
- **Windows:** `%USERPROFILE%\.reticulum\config`
- **Linux/Mac:** `~/.reticulum/config`

If the file doesn't exist yet, run this once to auto-generate it:
```bash
python -c "import RNS; RNS.Reticulum()"
```

Now edit the config and add this at the bottom under `[interfaces]`:

```ini
  [[Phantom TCP Server]]
    type = TCPServerInterface
    listen_ip = 0.0.0.0
    listen_port = 4242
    enabled = yes
```

### PC-B (Leecher) — Connects to PC-A

Edit the same Reticulum config on PC-B, add:

```ini
  [[Phantom TCP Client]]
    type = TCPClientInterface
    target_host = 192.168.1.100
    target_port = 4242
    enabled = yes
```

> ⚠️ Replace `192.168.1.100` with PC-A's actual IP address!

---

## Step 3: Create Identity (Both PCs)

On **both** PCs:

```bash
python phantom.py identity --new
```

You'll see something like:
```
✓ New identity created!
🔑 Identity Hash  <a1b2c3d4e5f6...>
```

---

## Step 4: Seed a File (PC-A)

Create a test file and start seeding:

```bash
# Create a dummy test file (or use any file you want)
echo "Hello from Reticulum Phantom!" > testfile.txt

# Or create something bigger:
# python -c "open('testfile.bin','wb').write(b'X'*5000000)"  # 5MB file

# Start seeding
python phantom.py seed testfile.txt
```

You'll see output like:
```
👻 Phantom Seeder
↑ SEEDING  testfile.txt

Ghost Hash:   a1b2c3d4e5f6789012345678
Destination:  <f0e1d2c3b4a5...>

Share the ghost hash with peers so they can download.
Press Ctrl+C to stop seeding.
```

**Copy the Destination hash** — you'll need it on PC-B.

---

## Step 5: Download the File (PC-B)

On PC-B, use the **destination hash** from PC-A:

```bash
python phantom.py download <paste_the_destination_hash_here>
```

For example:
```bash
python phantom.py download f0e1d2c3b4a596877463524130
```

You should see:
```
ℹ Searching mesh for seeder...
ℹ Establishing encrypted link...
ℹ Fetching manifest from seeder...
Downloading testfile.txt  ████████████████  100%  2.1 KB/s  0:00:02

✓ DOWNLOAD COMPLETE
File:       testfile.txt
Size:       30 B
Time:       2s
```

---

## Alternative: Test on Same PC (Two Terminals)

You can also test locally without 2 PCs! Reticulum's `AutoInterface` auto-discovers peers on localhost.

**Terminal 1:**
```bash
cd reticulum-phantom
python phantom.py seed testfile.txt
```

**Terminal 2:**
```bash
cd reticulum-phantom
python phantom.py download <destination_hash_from_terminal_1>
```

> This works because RNS automatically creates a shared transport instance on the same machine.

---

## Step 6: Debug Mode

If something doesn't connect, use debug mode to see all RNS activity:

```bash
python phantom.py debug
```

This shows:
- Interface discovery
- Announce propagation
- Path requests
- Link establishment
- Packet flow

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Seeder not found on mesh` | Check firewall — port 4242 must be open on PC-A |
| `Link establishment timed out` | Verify the IP address in PC-B's Reticulum config |
| `Cannot recall seeder identity` | The seeder needs to announce first — wait ~10 seconds |
| No output on `debug` | Make sure Reticulum config has the TCP interface enabled |

### Firewall Rules (Windows)

If you're on Windows, you may need to allow the port:

```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="Reticulum Phantom" dir=in action=allow protocol=tcp localport=4242
```

### Verify Connectivity

Test basic TCP connectivity between the PCs:

```powershell
# On PC-B, test if you can reach PC-A
Test-NetConnection -ComputerName 192.168.1.100 -Port 4242
```

---

## Quick Reference

| Command | PC-A (Seeder) | PC-B (Leecher) |
|---------|--------------|-----------------|
| Setup | `python phantom.py identity --new` | `python phantom.py identity --new` |
| Share | `python phantom.py seed myfile.zip` | — |
| Get | — | `python phantom.py download <hash>` |
| Debug | `python phantom.py debug` | `python phantom.py debug` |

---

## Network Diagram

```
  PC-A (Seeder)                          PC-B (Leecher)
 ┌──────────────┐                      ┌──────────────┐
 │ phantom seed │                      │   phantom    │
 │  myfile.zip  │                      │  download    │
 │              │    TCP/IP :4242      │   <hash>     │
 │  RNS Server  │◄────────────────────►│  RNS Client  │
 │  0.0.0.0     │   E2E Encrypted     │  → PC-A IP   │
 └──────────────┘                      └──────────────┘
       ↑                                      ↓
   announce()                          request_path()
   serve chunks                        download chunks
                                       verify hashes
                                       assemble file ✓
```
