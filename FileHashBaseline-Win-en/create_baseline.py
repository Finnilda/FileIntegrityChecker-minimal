"""Build a SHA-256 baseline database and record symbolic links and junctions themselves."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import os
import sqlite3
import sys


# Store the DB next to this script; resolve() gives the exclusion checks one absolute path format.
DB_PATH = Path(__file__).with_name("baseline.db").resolve()

CHUNK_SIZE = 1024 * 1024  # Read 1 MiB at a time so large files do not consume excessive memory.


def hash_file(path: Path) -> str:
    """Calculate SHA-256; reject the result if size or modification time changes while reading."""
    digest = sha256()  # Create a new SHA-256 accumulator for this file.

    with path.open("rb") as source:  # Open in binary read-only mode and close automatically.
        before = os.fstat(source.fileno())  # Record size and modification time before hashing.

        while chunk := source.read(CHUNK_SIZE):  # Read 1 MiB at a time.
            digest.update(chunk)  # Add this chunk to the same SHA-256 calculation.

        after = os.fstat(source.fileno())  # Inspect the same open file again after hashing.

    # A changed size or modification time means the file changed while it was being read.
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"File changed while hashing: {path}")

    return digest.hexdigest()  # Return the SHA-256 value in hexadecimal form.


def walk_entries(root: Path):
    """Walk root, entering normal folders but never entering symbolic links or junctions."""
    pending = [root]  # Folder stack, initially containing only the user-selected source folder.

    while pending:
        folder = pending.pop()

        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)  # Convert to Path for relative_to() and readlink().

                # Enter only normal, non-link folders; send links and other entries to main().
                if not (entry.is_symlink() or entry.is_junction()) and entry.is_dir():
                    pending.append(path)  # Visit this folder later without recursive function calls.
                else:
                    yield path  # Yield one item; resume only after main() finishes processing it.


def get_root() -> Path:
    """Use the command-line argument, or prompt for a source folder when none is provided."""
    if len(sys.argv) > 2:
        raise SystemExit(
            "Error: too many arguments.\n"
            'Usage example: python create_baseline.py "D:/My Folder"'
        )

    if len(sys.argv) == 2:
        return Path(sys.argv[1])

    user_input = input("Enter the absolute path of the folder to scan: ").strip().strip('"')
    return Path(user_input)


def main(root: Path) -> None:
    """Stream through the source folder and rebuild one complete baseline transaction."""
    if not root.is_absolute():  # Require an explicit absolute source path.
        raise SystemExit(f"Root must be an absolute path: {root}")

    if not root.is_dir():  # Confirm that the path exists and is a folder.
        raise SystemExit(f"Folder not found: {root}")

    # SQLite may create three sidecar files; none of these four paths may become scan input.
    ignored = {
        DB_PATH,  # Main SQLite database.
        Path(f"{DB_PATH}-journal").resolve(),  # Default rollback journal.
        Path(f"{DB_PATH}-wal").resolve(),  # Write-ahead log used by WAL mode.
        Path(f"{DB_PATH}-shm").resolve(),  # Shared-memory file used by WAL mode.
    }

    file_count = 0  # Normal files successfully written to the files table.
    link_count = 0  # Symbolic links and junctions successfully written to the links table.

    db = sqlite3.connect(DB_PATH)  # Open the DB, creating it if it does not exist.

    try:
        db.execute("BEGIN")
        db.execute("DROP TABLE IF EXISTS files")  # Remove the previous normal-file baseline.
        db.execute("DROP TABLE IF EXISTS links")  # Remove the previous link baseline.
        db.execute("DROP TABLE IF EXISTS metadata")  # Remove the previous scan metadata.

        # Store each normal file's relative path and content hash.
        db.execute(
            """
            CREATE TABLE files (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL
            )
            """
        )

        # Store each link itself without checking, following, or hashing its target.
        db.execute(
            """
            CREATE TABLE links (
                path TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('symlink', 'junction')),
                target TEXT NOT NULL
            )
            """
        )

        # A key/value table allows future metadata without changing the schema.
        db.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        # The custom walker returns links themselves but never enters their targets.
        for path in walk_entries(root):
            relative_path = str(path.relative_to(root))  # Store paths relative to the source folder.

            if path.is_junction():
                db.execute(
                    "INSERT INTO links VALUES (?, ?, ?)",
                    (relative_path, "junction", str(path.readlink())),  # Store path and readlink() target.
                )
                link_count += 1
                continue

            if path.is_symlink():
                db.execute(
                    "INSERT INTO links VALUES (?, ?, ?)",  # Junctions and symlinks share this schema.
                    (relative_path, "symlink", str(path.readlink())),  # Store path and readlink() target.
                )
                link_count += 1
                continue

            if not path.is_file():  # Ignore other special filesystem entries.
                continue

            if path in ignored:  # Never hash the SQLite files that this program is modifying.
                continue

            file_hash = hash_file(path)  # Open and hash a normal file.
            db.execute(
                "INSERT INTO files VALUES (?, ?)",
                (relative_path, file_hash),  # Store the relative path and hexadecimal SHA-256.
            )

            file_count += 1
            print(f"\rHashed {file_count:,} files; recorded {link_count:,} links...", end="", flush=True)

        # Index SHA-256 values to speed up later content-based searches.
        db.execute("CREATE INDEX files_sha256_idx ON files (sha256)")

        # Store the absolute source path supplied by the user.
        db.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("root_path", str(root)),
        )

        # Record completion time only after every file and link has been processed successfully.
        db.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("scan_completed_at_utc", datetime.now(UTC).isoformat(timespec="seconds")),
        )

        db.commit()
    except Exception:
        db.rollback()  # Undo every change in this transaction if any step fails.
        raise
    finally:
        db.close()  # Always release the DB and its Windows file lock.

    # Report success only after commit has succeeded and the DB connection has closed.
    print(f"\nCreated {DB_PATH} with {file_count:,} files and {link_count:,} links.")


if __name__ == "__main__":
    main(get_root())
