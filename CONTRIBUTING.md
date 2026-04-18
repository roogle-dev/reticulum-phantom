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

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
