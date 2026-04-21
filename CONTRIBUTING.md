# Contributing to Reticulum Phantom

Thank you for your interest in contributing to Reticulum Phantom! This project aims to be the first decentralized file-sharing application built on the Reticulum Network Stack.

## Getting Started

### Prerequisites
- Python 3.8 or higher
- A working Reticulum installation (`pip install rns`)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/roogle-dev/reticulum-phantom.git
cd phantom

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

# Install in development mode
pip install -e ".[dev]"
```

### Running Tests

```bash
python -m pytest tests/ -v
```

## How to Contribute

### Reporting Bugs
- Use the GitHub Issues tab
- Include your OS, Python version, and RNS version
- Provide steps to reproduce the issue

### Submitting Changes
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Code Style
- Follow PEP 8
- Use type hints where practical
- Add docstrings to public functions and classes
- Keep functions focused and small

### Architecture Guidelines
- All network I/O goes through the RNS API — never raw sockets
- File operations use the `chunker` module
- UI output uses the `rich` library via `ui.py`
- Configuration is centralized in `config.py`

### Mesh Etiquette (Critical)

Reticulum is a shared mesh network. Transport nodes enforce **announce rate-limiting** — if you announce too frequently, your packets get silently dropped and your destination may be temporarily blocked.

**Rules for contributors:**

1. **One destination per instance** — All seeded files share a single `RNS.Destination("phantom", "swarm")`. Never create per-file destinations.
2. **Never use announces for peer discovery** — Use PEX (Peer Exchange) over encrypted Links instead. Links are never rate-limited.
3. **Minimum announce interval: 120 seconds** per destination hash. Our heartbeat is 3 hours.
4. **Use Links for control-plane traffic** — Manifest, chunk, PEX, catalog, and status requests all happen over Links, not announces.
5. **Event-driven, not polling** — Only poll periodically for PEX (every 120s during download). Everything else should be event-driven.
6. **ghost_hash in request payloads** — File identity is carried in the request data dict, not in destination aspects.

**Discovery hierarchy:**
1. **Direct path resolution** (ghost file dests) — fastest, no network cost
2. **PEX over Links** — primary discovery, not rate-limited
3. **Announce-based** — fallback only, subject to rate-limiting

### Peer Exchange (PEX) Protocol

When adding new features that involve peer communication:
- Register new request handlers on the shared SeedManager destination (like `"peers"`, `"manifest"`, `"chunk"`, `"catalog"`)
- All request handlers receive `{ghost_hash: "..."}` in the request data to identify which file
- Use `link.request()` / `response_callback` pattern
- Keep responses compact (msgpack-encoded)
- Handle timeouts gracefully (10-15s)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
