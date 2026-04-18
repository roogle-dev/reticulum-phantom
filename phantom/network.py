"""
Reticulum Phantom — Network Initialization

Handles RNS (Reticulum Network Stack) initialization and
transport configuration. Starts with TCP/IP transport and
can be extended to support LoRa, packet radio, etc.
"""

import os
import RNS

from . import config


class PhantomNetwork:
    """
    Manages the Reticulum network stack for Phantom.

    Initializes RNS with appropriate configuration and
    provides utility methods for network operations.
    """

    def __init__(self, configpath=None):
        """
        Initialize the Phantom network layer.

        Args:
            configpath: Optional custom path to Reticulum config.
                        If None, uses default (~/.reticulum/).
        """
        self._reticulum = None
        self._configpath = configpath
        self._started = False

    @property
    def reticulum(self):
        """Get the RNS.Reticulum instance."""
        return self._reticulum

    @property
    def is_running(self):
        """Check if the network stack is running."""
        return self._started and self._reticulum is not None

    def start(self):
        """
        Start the Reticulum network stack.

        This will:
          1. Load or create Reticulum config
          2. Initialize all configured interfaces (AutoInterface, TCP, etc.)
          3. Start the transport engine

        Returns:
            The RNS.Reticulum instance.
        """
        if self._started:
            return self._reticulum

        try:
            RNS.log("Starting Reticulum Network Stack...", RNS.LOG_INFO)

            # Ensure Sideband Hub is configured for mesh connectivity
            self._ensure_sideband_hub()

            self._reticulum = RNS.Reticulum(self._configpath)
            self._started = True

            # Log network info
            transport_status = "enabled" if RNS.Reticulum.transport_enabled() else "disabled"
            RNS.log(
                f"Reticulum started (Transport: {transport_status})",
                RNS.LOG_INFO
            )

            return self._reticulum

        except Exception as e:
            RNS.log(f"Failed to start Reticulum: {e}", RNS.LOG_ERROR)
            raise

    def has_path(self, destination_hash):
        """
        Check if we know how to reach a destination.

        Args:
            destination_hash: The destination hash as bytes.

        Returns:
            True if a path is known.
        """
        return RNS.Transport.has_path(destination_hash)

    def request_path(self, destination_hash):
        """
        Ask the mesh to find a path to a destination.

        Args:
            destination_hash: The destination hash as bytes.
        """
        RNS.Transport.request_path(destination_hash)

    def hops_to(self, destination_hash):
        """
        Get the number of hops to a destination.

        Args:
            destination_hash: The destination hash as bytes.

        Returns:
            Number of hops, or PATHFINDER_M if unknown.
        """
        return RNS.Transport.hops_to(destination_hash)

    def get_status(self):
        """
        Get current network status information.

        Returns:
            Dict with network status details.
        """
        if not self._started:
            return {"status": "stopped"}

        return {
            "status": "running",
            "transport_enabled": RNS.Reticulum.transport_enabled(),
        }

    def get_interfaces(self):
        """
        Get a list of active Reticulum interfaces.

        Returns:
            List of dicts with interface info.
        """
        interfaces = []
        try:
            for iface in RNS.Transport.interfaces:
                info = {
                    "name": getattr(iface, "name", "Unknown"),
                    "type": type(iface).__name__,
                    "online": getattr(iface, "online", False),
                }

                # Try to get additional details
                if hasattr(iface, "target_host"):
                    info["details"] = f"{iface.target_host}:{getattr(iface, 'target_port', '?')}"
                elif hasattr(iface, "listen_ip"):
                    info["details"] = f"{iface.listen_ip}:{getattr(iface, 'listen_port', '?')}"
                elif hasattr(iface, "port"):
                    info["details"] = str(iface.port)
                else:
                    info["details"] = ""

                # Traffic stats if available
                info["rxb"] = getattr(iface, "rxb", 0)
                info["txb"] = getattr(iface, "txb", 0)

                interfaces.append(info)
        except Exception:
            pass
        return interfaces

    def _ensure_sideband_hub(self):
        """
        Ensure the Sideband Hub interface is in the Reticulum config.
        This gives new users instant global mesh connectivity.
        """
        try:
            # Find the config file
            if self._configpath:
                config_dir = self._configpath
            else:
                config_dir = os.path.join(
                    os.path.expanduser("~"), ".reticulum"
                )

            config_file = os.path.join(config_dir, "config")

            # If no config exists yet, RNS will create one — we'll add to it
            # after first run. For now, check if config exists.
            if not os.path.isfile(config_file):
                return  # RNS will create default config on first run

            with open(config_file, "r") as f:
                content = f.read()

            # Check if Sideband Hub already configured
            if "sideband.connect.reticulum.network" in content.lower():
                # Still check if AutoInterface needs disabling
                self._disable_autointerface(config_file, content)
                return  # Already configured

            if "sideband hub" in content.lower():
                self._disable_autointerface(config_file, content)
                return  # Already has a sideband section

            # Append Sideband Hub interface
            sideband_config = """
  # Phantom Mesh — Global connectivity via Sideband Hub
  [[Sideband Hub]]
    type = TCPClientInterface
    enabled = Yes
    target_host = sideband.connect.reticulum.network
    target_port = 7822
"""
            with open(config_file, "a") as f:
                f.write(sideband_config)

            RNS.log(
                "Added Sideband Hub to Reticulum config for mesh connectivity",
                RNS.LOG_INFO
            )

            # Disable AutoInterface to prevent IPv6 errors
            with open(config_file, "r") as f:
                content = f.read()
            self._disable_autointerface(config_file, content)

        except Exception as e:
            # Non-fatal — user can still configure manually
            RNS.log(
                f"Could not auto-configure Sideband Hub: {e}",
                RNS.LOG_DEBUG
            )

    @staticmethod
    def _disable_autointerface(config_file, content):
        """
        Disable AutoInterface to prevent IPv6 'label too long' errors
        on Windows. Sideband Hub provides global mesh connectivity.
        """
        import sys
        if sys.platform != "win32":
            return  # Only needed on Windows

        try:
            if "[[Default Interface]]" not in content:
                return

            # Line-by-line: find enabled=Yes ONLY inside [[Default Interface]] block
            lines = content.splitlines(True)  # Keep line endings
            in_default_block = False
            modified = False

            for i, line in enumerate(lines):
                stripped = line.strip()

                # Detect entering [[Default Interface]] block
                if stripped == "[[Default Interface]]":
                    in_default_block = True
                    continue

                # Detect leaving block (new [[ section starts)
                if stripped.startswith("[[") and in_default_block:
                    in_default_block = False
                    continue

                # Only modify enabled inside Default Interface block
                if in_default_block and stripped.lower().startswith("enabled"):
                    if "yes" in stripped.lower():
                        lines[i] = line.replace("Yes", "No").replace("yes", "No")
                        modified = True
                        in_default_block = False  # Done

            if modified:
                with open(config_file, "w") as f:
                    f.write("".join(lines))
                RNS.log(
                    "Disabled AutoInterface (using Sideband Hub instead)",
                    RNS.LOG_INFO
                )
        except Exception:
            pass
