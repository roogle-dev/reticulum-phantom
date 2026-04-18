"""
Reticulum Phantom — Identity Management

Handles creation, loading, and display of RNS cryptographic identities.
Each Phantom node has a single persistent identity (X25519 keypair)
that is used for all network operations.
"""

import os
import RNS

from . import config


class PhantomIdentity:
    """
    Manages the Reticulum cryptographic identity for this Phantom node.

    The identity is a persistent X25519 keypair stored on disk.
    It is used to:
      - Create destinations (endpoints) on the mesh
      - Encrypt/decrypt all communications
      - Sign announcements
      - Identify this node to peers
    """

    def __init__(self):
        self._identity = None
        self._identity_path = config.IDENTITY_FILE
        config.ensure_directories()

    @property
    def identity(self):
        """Get the loaded RNS.Identity instance."""
        return self._identity

    @property
    def hash_hex(self):
        """Get the identity hash as a hex string (your node ID)."""
        if self._identity:
            return RNS.hexrep(self._identity.hash, delimit=False)
        return None

    @property
    def hash_pretty(self):
        """Get the identity hash in Reticulum's pretty format."""
        if self._identity:
            return RNS.prettyhexrep(self._identity.hash)
        return None

    @property
    def is_loaded(self):
        """Check if an identity is currently loaded."""
        return self._identity is not None

    def exists(self):
        """Check if an identity file exists on disk."""
        return os.path.exists(self._identity_path)

    def create_new(self):
        """
        Create a brand new identity (keypair) and save it to disk.
        WARNING: This will overwrite any existing identity!

        Returns:
            The new RNS.Identity instance.
        """
        self._identity = RNS.Identity()
        self._identity.to_file(self._identity_path)

        RNS.log(
            f"Created new Phantom identity: {self.hash_pretty}",
            RNS.LOG_INFO
        )
        return self._identity

    def load(self):
        """
        Load an existing identity from disk, or create one if none exists.

        Returns:
            The loaded RNS.Identity instance.
        """
        if os.path.exists(self._identity_path):
            self._identity = RNS.Identity.from_file(self._identity_path)
            if self._identity:
                RNS.log(
                    f"Loaded Phantom identity: {self.hash_pretty}",
                    RNS.LOG_INFO
                )
                return self._identity
            else:
                RNS.log(
                    "Identity file corrupted, creating new identity",
                    RNS.LOG_WARNING
                )
                return self.create_new()
        else:
            RNS.log(
                "No existing identity found, creating new one",
                RNS.LOG_INFO
            )
            return self.create_new()

    def load_from_file(self, path):
        """
        Load an identity from a specific file path (for import).

        Args:
            path: Path to the identity file.

        Returns:
            The loaded RNS.Identity instance, or None on failure.
        """
        if not os.path.exists(path):
            RNS.log(f"Identity file not found: {path}", RNS.LOG_ERROR)
            return None

        self._identity = RNS.Identity.from_file(path)
        if self._identity:
            # Save as our active identity
            self._identity.to_file(self._identity_path)
            RNS.log(
                f"Imported identity: {self.hash_pretty}",
                RNS.LOG_INFO
            )
            return self._identity
        else:
            RNS.log("Failed to load identity from file", RNS.LOG_ERROR)
            return None

    def export(self, path):
        """
        Export the current identity to a file (for backup/transfer).

        Args:
            path: Destination path for the exported identity.

        Returns:
            True if successful, False otherwise.
        """
        if not self._identity:
            RNS.log("No identity loaded to export", RNS.LOG_ERROR)
            return False

        try:
            self._identity.to_file(path)
            RNS.log(f"Identity exported to: {path}", RNS.LOG_INFO)
            return True
        except Exception as e:
            RNS.log(f"Failed to export identity: {e}", RNS.LOG_ERROR)
            return False

    def get_info(self):
        """
        Get a dictionary of identity information for display.

        Returns:
            Dict with identity details, or None if no identity loaded.
        """
        if not self._identity:
            return None

        return {
            "hash": self.hash_hex,
            "hash_pretty": self.hash_pretty,
            "public_key_size": len(self._identity.get_public_key()) * 8,
            "identity_path": self._identity_path,
            "curve": "X25519/Ed25519",
        }
