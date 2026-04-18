"""
Reticulum Phantom — .ghost File Format

The .ghost file is the Phantom equivalent of a .torrent file.
It contains all metadata needed to identify, locate, and verify
a shared file on the Reticulum mesh.

Format: msgpack-encoded binary
Extension: .ghost
"""

import os
import time
import hashlib
import struct

import RNS
import RNS.vendor.umsgpack as umsgpack

from . import config


class GhostFile:
    """
    Represents a .ghost file — the metadata descriptor for a shared file.

    A .ghost file contains:
      - File name, size, and SHA-256 hash
      - Chunk size and per-chunk SHA-256 hashes
      - Creator identity hash
      - Timestamp and optional comment
      - RNS destination aspects for mesh discovery
    """

    def __init__(self):
        self.version = config.GHOST_FORMAT_VERSION
        self.name = ""
        self.file_size = 0
        self.chunk_size = config.DEFAULT_CHUNK_SIZE
        self.chunk_count = 0
        self.file_hash = ""
        self.chunk_hashes = []
        self.created_at = 0
        self.created_by = ""
        self.comment = ""
        self.source_path = ""     # Original file path for seeding
        self.seeder_dest = ""    # Seeder's destination hash (for fast discovery)

    @property
    def ghost_hash(self):
        """
        The unique identifier for this ghost on the mesh.
        First 16 bytes of the file's SHA-256, hex-encoded.
        This becomes the RNS destination aspect.
        """
        if self.file_hash:
            return self.file_hash[:32]  # 16 bytes = 32 hex chars
        return None

    @property
    def destination_aspects(self):
        """RNS destination aspects for mesh announcement."""
        return ["swarm", self.ghost_hash]

    @staticmethod
    def create(filepath, identity_hash="", comment="",
               chunk_size=None):
        """
        Create a .ghost file from an actual file on disk.

        This reads the entire file to compute:
          1. The full-file SHA-256 hash
          2. Per-chunk SHA-256 hashes

        Args:
            filepath: Path to the source file.
            identity_hash: Hex string of the creator's identity hash.
            comment: Optional description.
            chunk_size: Override default chunk size.

        Returns:
            A GhostFile instance, or None on failure.
        """
        if not os.path.isfile(filepath):
            RNS.log(f"File not found: {filepath}", RNS.LOG_ERROR)
            return None

        ghost = GhostFile()
        ghost.name = os.path.basename(filepath)
        ghost.file_size = os.path.getsize(filepath)
        ghost.chunk_size = chunk_size or config.DEFAULT_CHUNK_SIZE
        ghost.created_at = int(time.time())
        ghost.created_by = identity_hash
        ghost.comment = comment
        ghost.source_path = os.path.abspath(filepath)

        # Validate chunk size
        ghost.chunk_size = max(config.MIN_CHUNK_SIZE,
                               min(ghost.chunk_size, config.MAX_CHUNK_SIZE))

        # Calculate chunk count
        ghost.chunk_count = (ghost.file_size + ghost.chunk_size - 1) // ghost.chunk_size
        if ghost.file_size == 0:
            ghost.chunk_count = 0

        # Hash the file — full hash + per-chunk hashes
        RNS.log(f"Hashing file: {ghost.name} ({ghost.file_size} bytes)", RNS.LOG_INFO)

        file_hasher = hashlib.sha256()
        ghost.chunk_hashes = []

        try:
            with open(filepath, "rb") as f:
                chunk_index = 0
                while True:
                    chunk = f.read(ghost.chunk_size)
                    if not chunk:
                        break

                    # Update full-file hash
                    file_hasher.update(chunk)

                    # Compute per-chunk hash
                    chunk_hash = hashlib.sha256(chunk).hexdigest()
                    ghost.chunk_hashes.append(chunk_hash)
                    chunk_index += 1

            ghost.file_hash = file_hasher.hexdigest()

            RNS.log(
                f"Ghost created: {ghost.name} | "
                f"{ghost.chunk_count} chunks | "
                f"Hash: {ghost.ghost_hash}",
                RNS.LOG_INFO
            )
            return ghost

        except IOError as e:
            RNS.log(f"Error reading file: {e}", RNS.LOG_ERROR)
            return None

    def save(self, output_path=None):
        """
        Save the .ghost file to disk.

        Args:
            output_path: Where to save. If None, saves next to the
                         original file with .ghost extension.

        Returns:
            The path where the file was saved, or None on failure.
        """
        if output_path is None:
            output_path = os.path.join(config.GHOSTS_DIR,
                                       self.name + config.GHOST_EXTENSION)

        data = {
            "ghost_version": self.version,
            "name": self.name,
            "file_size": self.file_size,
            "chunk_size": self.chunk_size,
            "chunk_count": self.chunk_count,
            "file_hash": self.file_hash,
            "chunk_hashes": self.chunk_hashes,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "comment": self.comment,
            # NOTE: source_path intentionally excluded — privacy
            # It would expose the creator's filesystem paths
            "seeder_dest": self.seeder_dest,
            "app_name": config.RNS_APP_NAME,
        }

        try:
            packed = umsgpack.packb(data)
            with open(output_path, "wb") as f:
                f.write(packed)

            RNS.log(f"Ghost file saved: {output_path}", RNS.LOG_INFO)
            return output_path

        except Exception as e:
            RNS.log(f"Failed to save ghost file: {e}", RNS.LOG_ERROR)
            return None

    @staticmethod
    def load(ghost_path):
        """
        Load and parse a .ghost file from disk.

        Args:
            ghost_path: Path to the .ghost file.

        Returns:
            A GhostFile instance, or None if invalid.
        """
        if not os.path.isfile(ghost_path):
            RNS.log(f"Ghost file not found: {ghost_path}", RNS.LOG_ERROR)
            return None

        try:
            with open(ghost_path, "rb") as f:
                raw = f.read()

            data = umsgpack.unpackb(raw)

            ghost = GhostFile()
            ghost.version = data.get("ghost_version", 1)
            ghost.name = data.get("name", "unknown")
            ghost.file_size = data.get("file_size", 0)
            ghost.chunk_size = data.get("chunk_size", config.DEFAULT_CHUNK_SIZE)
            ghost.chunk_count = data.get("chunk_count", 0)
            ghost.file_hash = data.get("file_hash", "")
            ghost.chunk_hashes = data.get("chunk_hashes", [])
            ghost.created_at = data.get("created_at", 0)
            ghost.created_by = data.get("created_by", "")
            ghost.comment = data.get("comment", "")
            ghost.source_path = data.get("source_path", "")
            ghost.seeder_dest = data.get("seeder_dest", "")

            # Validate
            if not ghost.file_hash:
                RNS.log("Invalid ghost file: missing file_hash", RNS.LOG_ERROR)
                return None

            if len(ghost.chunk_hashes) != ghost.chunk_count:
                RNS.log(
                    f"Invalid ghost file: chunk hash count mismatch "
                    f"({len(ghost.chunk_hashes)} vs {ghost.chunk_count})",
                    RNS.LOG_ERROR
                )
                return None

            RNS.log(f"Loaded ghost: {ghost.name} [{ghost.ghost_hash}]", RNS.LOG_INFO)
            return ghost

        except Exception as e:
            RNS.log(f"Failed to parse ghost file: {e}", RNS.LOG_ERROR)
            return None

    def get_info_dict(self):
        """Get a dictionary of ghost file information for display."""
        from datetime import datetime

        created = datetime.fromtimestamp(self.created_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if self.created_at else "Unknown"

        return {
            "name": self.name,
            "file_size": self.file_size,
            "file_size_human": self._human_size(self.file_size),
            "chunk_size": self.chunk_size,
            "chunk_size_human": self._human_size(self.chunk_size),
            "chunk_count": self.chunk_count,
            "file_hash": self.file_hash,
            "ghost_hash": self.ghost_hash,
            "created_at": created,
            "created_by": self.created_by or "Anonymous",
            "comment": self.comment or "(none)",
            "version": self.version,
        }

    @staticmethod
    def _human_size(num_bytes):
        """Convert bytes to human-readable size."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(num_bytes) < 1024.0:
                return f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} PB"
