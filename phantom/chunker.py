"""
Reticulum Phantom — File Chunking & Reassembly

Handles splitting files into fixed-size chunks for transfer
and reassembling received chunks into complete files.
Each chunk is independently verifiable via SHA-256.
"""

import os
import hashlib

import RNS

from . import config


class Chunker:
    """
    Manages file chunking for Phantom transfers.

    Files are split into fixed-size chunks (default 1MB).
    Each chunk is independently hashed for integrity verification.
    Partial downloads are stored in a per-ghost chunk directory.
    """

    def __init__(self, ghost_file):
        """
        Initialize chunker for a specific ghost file.

        Args:
            ghost_file: A GhostFile instance with metadata.
        """
        self.ghost = ghost_file
        self.chunk_dir = os.path.join(
            config.CHUNKS_DIR, self.ghost.ghost_hash
        )
        os.makedirs(self.chunk_dir, exist_ok=True)

    def get_chunk_path(self, index):
        """Get the filesystem path for a specific chunk."""
        return os.path.join(self.chunk_dir, f"chunk_{index:06d}")

    def has_chunk(self, index):
        """Check if a specific chunk exists on disk."""
        return os.path.isfile(self.get_chunk_path(index))

    def get_available_chunks(self):
        """
        Get a list of chunk indices we have locally.

        Returns:
            List of integers (chunk indices).
        """
        available = []
        for i in range(self.ghost.chunk_count):
            if self.has_chunk(i):
                available.append(i)
        return available

    def get_missing_chunks(self):
        """
        Get a list of chunk indices we still need.

        Returns:
            List of integers (chunk indices).
        """
        missing = []
        for i in range(self.ghost.chunk_count):
            if not self.has_chunk(i):
                missing.append(i)
        return missing

    def get_bitfield(self):
        """
        Get a compact bitfield of available chunks.

        Returns:
            Bytes representing which chunks we have (1=have, 0=missing).
        """
        bitfield = bytearray((self.ghost.chunk_count + 7) // 8)
        for i in range(self.ghost.chunk_count):
            if self.has_chunk(i):
                byte_index = i // 8
                bit_index = 7 - (i % 8)
                bitfield[byte_index] |= (1 << bit_index)
        return bytes(bitfield)

    def read_chunk(self, index, source_path=None):
        """
        Read a chunk — from source file (seeding) or from chunk store.

        Args:
            index: Chunk index.
            source_path: Path to the original file (for seeders).

        Returns:
            Chunk data as bytes, or None if unavailable.
        """
        if source_path and os.path.isfile(source_path):
            # Read directly from source file
            try:
                with open(source_path, "rb") as f:
                    f.seek(index * self.ghost.chunk_size)
                    chunk = f.read(self.ghost.chunk_size)
                    if chunk:
                        return chunk
            except IOError as e:
                RNS.log(f"Error reading chunk {index} from source: {e}",
                        RNS.LOG_ERROR)
                return None

        # Read from chunk store
        chunk_path = self.get_chunk_path(index)
        if os.path.isfile(chunk_path):
            try:
                with open(chunk_path, "rb") as f:
                    return f.read()
            except IOError as e:
                RNS.log(f"Error reading chunk {index}: {e}", RNS.LOG_ERROR)
                return None

        return None

    def save_chunk(self, index, data):
        """
        Save a received chunk to the chunk store after verifying its hash.

        Args:
            index: Chunk index.
            data: Chunk data as bytes.

        Returns:
            True if saved and verified, False if hash mismatch.
        """
        # Verify chunk hash
        expected_hash = self.ghost.chunk_hashes[index]
        actual_hash = hashlib.sha256(data).hexdigest()

        if actual_hash != expected_hash:
            RNS.log(
                f"Chunk {index} hash mismatch! "
                f"Expected {expected_hash[:16]}..., "
                f"got {actual_hash[:16]}...",
                RNS.LOG_ERROR
            )
            return False

        # Save to disk
        chunk_path = self.get_chunk_path(index)
        try:
            with open(chunk_path, "wb") as f:
                f.write(data)
            return True
        except IOError as e:
            RNS.log(f"Error saving chunk {index}: {e}", RNS.LOG_ERROR)
            return False

    def assemble(self, output_path=None):
        """
        Reassemble all chunks into the final file.

        Args:
            output_path: Where to write the assembled file.
                         Defaults to downloads directory.

        Returns:
            The output path if successful, None if incomplete or failed.
        """
        # Check we have all chunks
        missing = self.get_missing_chunks()
        if missing:
            RNS.log(
                f"Cannot assemble: missing {len(missing)} chunks",
                RNS.LOG_ERROR
            )
            return None

        if output_path is None:
            output_path = os.path.join(config.DOWNLOADS_DIR, self.ghost.name)

        # Handle filename conflicts
        base_path = output_path
        counter = 1
        while os.path.exists(output_path):
            name, ext = os.path.splitext(base_path)
            output_path = f"{name}_{counter}{ext}"
            counter += 1

        RNS.log(f"Assembling {self.ghost.chunk_count} chunks → {output_path}",
                RNS.LOG_INFO)

        try:
            file_hasher = hashlib.sha256()

            with open(output_path, "wb") as out:
                for i in range(self.ghost.chunk_count):
                    chunk = self.read_chunk(i)
                    if chunk is None:
                        RNS.log(f"Failed to read chunk {i} during assembly",
                                RNS.LOG_ERROR)
                        # Clean up partial file
                        os.remove(output_path)
                        return None

                    out.write(chunk)
                    file_hasher.update(chunk)

            # Verify final file hash
            final_hash = file_hasher.hexdigest()
            if final_hash != self.ghost.file_hash:
                RNS.log(
                    f"File hash mismatch after assembly! "
                    f"Expected {self.ghost.file_hash[:16]}..., "
                    f"got {final_hash[:16]}...",
                    RNS.LOG_ERROR
                )
                os.remove(output_path)
                return None

            file_size = os.path.getsize(output_path)
            RNS.log(
                f"Assembly complete: {output_path} "
                f"({file_size} bytes, hash verified ✓)",
                RNS.LOG_INFO
            )
            return output_path

        except IOError as e:
            RNS.log(f"Error during assembly: {e}", RNS.LOG_ERROR)
            return None

    def cleanup(self):
        """Remove the chunk directory (after successful assembly)."""
        import shutil
        if os.path.isdir(self.chunk_dir):
            shutil.rmtree(self.chunk_dir)
            RNS.log(f"Cleaned up chunks for {self.ghost.ghost_hash}",
                    RNS.LOG_DEBUG)

    def get_progress(self):
        """
        Get download progress as a float between 0.0 and 1.0.

        Returns:
            Progress value, or 1.0 if chunk_count is 0.
        """
        if self.ghost.chunk_count == 0:
            return 1.0
        return len(self.get_available_chunks()) / self.ghost.chunk_count
