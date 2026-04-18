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
        Get a list of Reticulum interfaces.

        Combines live interfaces from RNS.Transport with
        configured interfaces from the Reticulum config file
        (needed because shared-instance clients only see
        LocalClientInterface, not the real TCP/LoRa interfaces).

        Returns:
            List of dicts with interface info.
        """
        interfaces = []
        live_names = set()

        # 1. Live interfaces from RNS.Transport
        try:
            for iface in RNS.Transport.interfaces:
                name = getattr(iface, "name", "Unknown")
                live_names.add(name)

                info = {
                    "name": name,
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

        # 2. Configured interfaces from Reticulum config
        # (shows Sideband Hub etc. that the shared instance manages)
        try:
            config_dir = self._configpath or os.path.join(
                os.path.expanduser("~"), ".reticulum"
            )
            config_file = os.path.join(config_dir, "config")

            if os.path.isfile(config_file):
                with open(config_file, "r") as f:
                    lines = f.readlines()

                current_iface = None
                current_props = {}

                for line in lines:
                    stripped = line.strip()

                    # Interface section header: [[Name]]
                    if stripped.startswith("[[") and stripped.endswith("]]"):
                        # Save previous interface
                        if current_iface and current_iface not in live_names:
                            self._add_config_interface(
                                interfaces, current_iface, current_props
                            )
                        current_iface = stripped[2:-2].strip()
                        current_props = {}
                        continue

                    # Properties inside interface block
                    if current_iface and "=" in stripped:
                        key, _, val = stripped.partition("=")
                        current_props[key.strip().lower()] = val.strip()

                # Don't forget the last interface
                if current_iface and current_iface not in live_names:
                    self._add_config_interface(
                        interfaces, current_iface, current_props
                    )
        except Exception:
            pass

        return interfaces

    @staticmethod
    def _add_config_interface(interfaces, name, props):
        """Add a configured (non-live) interface to the list."""
        enabled = props.get("enabled", "yes").lower()
        if enabled == "no":
            return  # Skip disabled interfaces

        iface_type = props.get("type", "Unknown")
        details = ""

        if "target_host" in props:
            port = props.get("target_port", "?")
            details = f"{props['target_host']}:{port}"
        elif "listen_ip" in props:
            port = props.get("listen_port", "?")
            details = f"{props['listen_ip']}:{port}"
        elif "port" in props:
            details = props["port"]

        interfaces.append({
            "name": f"{name} (config)",
            "type": iface_type,
            "online": True,  # Assumed online if enabled
            "details": details,
            "rxb": 0,
            "txb": 0,
        })

    def _ensure_sideband_hub(self):
        """
        Ensure the Sideband Hub interface is enabled in the Reticulum config.
        This gives new users instant global mesh connectivity.
        
        Handles three cases:
          1. No config exists → will be created by RNS, we add hub after
          2. Config exists, no Sideband Hub → append it
          3. Config exists, Sideband Hub disabled → enable it
        """
        import re

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

            has_sideband = (
                "sideband.connect.reticulum.network" in content.lower()
                or "sideband hub" in content.lower()
            )

            if has_sideband:
                # Check if it's enabled
                # Parse the Sideband Hub block to find enabled = No/Yes
                lines = content.splitlines(True)
                in_sideband = False
                sideband_enabled = False
                modified = False

                for i, line in enumerate(lines):
                    stripped = line.strip()

                    if "sideband hub" in stripped.lower() and stripped.startswith("[["):
                        in_sideband = True
                        continue

                    if in_sideband and stripped.startswith("[["):
                        in_sideband = False
                        continue

                    if in_sideband and re.match(r'^\s*(interface_)?enabled\s*=', stripped):
                        if re.search(r'=\s*(yes|true|1)\s*$', stripped, re.IGNORECASE):
                            sideband_enabled = True
                        else:
                            # It's disabled — enable it
                            lines[i] = re.sub(
                                r'(enabled\s*=\s*)(No|False|no|false|0)',
                                r'\g<1>Yes',
                                line
                            )
                            modified = True
                            sideband_enabled = True
                        in_sideband = False

                if modified:
                    with open(config_file, "w") as f:
                        f.write("".join(lines))
                    RNS.log(
                        "Auto-enabled Sideband Hub for mesh connectivity",
                        RNS.LOG_INFO
                    )
                    print("ℹ Auto-enabled Sideband Hub for mesh connectivity")

            else:
                # No Sideband Hub at all — append it
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
                print("ℹ Added Sideband Hub interface for mesh connectivity")

                # Re-read for autointerface check
                with open(config_file, "r") as f:
                    content = f.read()

            # Disable AutoInterface on Windows to prevent IPv6 errors
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
