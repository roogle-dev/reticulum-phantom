"""
Reticulum Phantom — Network Initialization

Handles RNS (Reticulum Network Stack) initialization and
transport configuration. Phantom NEVER modifies the user's
Reticulum configuration — interface setup is the user's
responsibility, following Reticulum's decentralized design.
"""

import os
import re

import RNS

from . import config


class ConnectivityStatus:
    """Result of a connectivity check — never modifies anything."""

    def __init__(self, has_interfaces=False, interface_count=0, config_exists=False):
        self.has_interfaces = has_interfaces
        self.interface_count = interface_count
        self.config_exists = config_exists

    @property
    def is_ok(self):
        return self.has_interfaces

    def get_guidance_message(self):
        """
        Return a helpful message for users with no connectivity.
        Points to official Reticulum resources per Mark Qvist's guidance.
        """
        if self.is_ok:
            return None

        lines = [
            "No Reticulum network interfaces configured.",
            "",
            "Phantom requires at least one active Reticulum interface to",
            "communicate with the mesh. To configure your interfaces:",
            "",
            f"  1. Reticulum docs:  {config.RETICULUM_DOCS_URL}",
            f"  2. Interface list:  {config.INTERFACE_DIRECTORY_URL}",
            f"  3. Network map:     {config.RMAP_URL}",
            "",
            "Edit your Reticulum config file (~/.reticulum/config) to add",
            "one or more interfaces, then restart Phantom.",
        ]
        return "\n".join(lines)


class PhantomNetwork:
    """
    Manages the Reticulum network stack for Phantom.

    Initializes RNS with appropriate configuration and
    provides utility methods for network operations.

    IMPORTANT: Phantom never modifies the user's Reticulum config.
    Users configure their own interfaces following Reticulum's
    decentralized design philosophy.
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
          1. Check for configured interfaces (read-only)
          2. Initialize RNS with the user's existing config
          3. Start the transport engine

        Phantom never modifies the Reticulum configuration.
        If no interfaces are configured, a guidance message is logged.

        Returns:
            The RNS.Reticulum instance.
        """
        if self._started:
            return self._reticulum

        try:
            RNS.log("Starting Reticulum Network Stack...", RNS.LOG_INFO)

            # Check connectivity status (read-only — never modifies config)
            status = self.check_connectivity()
            if not status.is_ok:
                guidance = status.get_guidance_message()
                if guidance:
                    RNS.log(
                        "No network interfaces detected. "
                        "See Reticulum docs for interface configuration.",
                        RNS.LOG_WARNING
                    )

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

    def check_connectivity(self):
        """
        Check if the user's Reticulum config has any enabled interfaces.

        This is a READ-ONLY check — it never modifies any files.

        Returns:
            ConnectivityStatus with interface info and guidance.
        """
        try:
            if self._configpath:
                config_dir = self._configpath
            else:
                config_dir = os.path.join(
                    os.path.expanduser("~"), ".reticulum"
                )

            config_file = os.path.join(config_dir, "config")

            if not os.path.isfile(config_file):
                return ConnectivityStatus(
                    has_interfaces=False,
                    interface_count=0,
                    config_exists=False,
                )

            with open(config_file, "r") as f:
                content = f.read()

            # Count enabled interfaces (read-only scan)
            enabled_count = 0
            in_interfaces = False
            current_iface = None
            current_enabled = None

            for line in content.split("\n"):
                stripped = line.strip()

                if stripped == "[interfaces]":
                    in_interfaces = True
                    continue

                if in_interfaces and stripped.startswith("[") and not stripped.startswith("[["):
                    # Left [interfaces] section — save last interface
                    if current_iface and current_enabled:
                        enabled_count += 1
                    break

                if in_interfaces:
                    if stripped.startswith("[[") and stripped.endswith("]]"):
                        # Save previous interface
                        if current_iface and current_enabled:
                            enabled_count += 1
                        current_iface = stripped[2:-2].strip()
                        current_enabled = None
                        continue

                    if current_iface and re.match(
                        r'^\s*(interface_)?enabled\s*=', stripped
                    ):
                        if re.search(
                            r'=\s*(yes|true|1)\s*$', stripped, re.IGNORECASE
                        ):
                            current_enabled = True
                        else:
                            current_enabled = False

            # Don't forget the last interface
            if current_iface and current_enabled:
                enabled_count += 1

            return ConnectivityStatus(
                has_interfaces=enabled_count > 0,
                interface_count=enabled_count,
                config_exists=True,
            )

        except Exception:
            # Can't read config — RNS will handle this on its own
            return ConnectivityStatus(
                has_interfaces=True,  # Assume OK if we can't check
                interface_count=0,
                config_exists=False,
            )

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
        # (shows interfaces that the shared instance manages)
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
