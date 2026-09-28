# File Hash Baseline for Windows

`create_baseline.py` scans a selected folder and writes normal-file SHA-256 values, symbolic links and Windows junctions themselves, and scan metadata to `baseline.db`.

The program uses only the Python standard library. It has no third-party dependencies, network connection, or upload behavior. Source files are opened only in binary read-only mode and are never modified.

## Requirements

- Windows
- Python 3.12 or newer (`Path.is_junction()` is required)

## Usage

Run the program without an argument to enter the absolute source-folder path interactively:

```powershell
python create_baseline.py
```

```text
Enter the absolute path of the folder to scan: D:/My Folder
```

You may instead provide the path as a command-line argument:

```powershell
python create_baseline.py "D:/My Folder"
```

Python accepts forward slashes in Windows paths. These examples use forward slashes to avoid the special way some Japanese fonts display the traditional Windows path separator. Enclose command-line paths containing spaces in double quotes. In interactive mode, the program also removes surrounding double quotes from pasted paths.

When the scan completes, the program creates `baseline.db` next to `create_baseline.py`.

## How scanning works

- `os.scandir()` enumerates and processes entries one at a time instead of storing a complete path list in memory.
- Normal files are read in 1 MiB chunks and hashed with SHA-256.
- File size and modification time are compared before and after hashing. If either changes, the file changed while being read and the entire build fails.
- The active SQLite DB, journal, WAL, and SHM files are explicitly excluded.
- `files.sha256` has an index for faster future content-based searches.

`walk_entries()` is a generator. Each `yield path` passes only the current item to `main()` and pauses. After `main()` records the link or finishes the file's SHA-256 calculation and DB insert, it requests the next item. This enumerates and processes entries one at a time without storing the complete path list in memory.

All database changes occur in one transaction. The program commits only after every step succeeds. Any failure triggers a rollback, so a new partial baseline is never retained. If a complete DB already exists, its previous data remains intact after a failed rebuild.

## Symbolic links and junctions

A symbolic link is a filesystem link object that can point to another file or folder. When software accesses a symbolic link, the operating system normally redirects the operation to its target. If the target is moved or deleted, the link itself may remain but become broken.

A junction is a Windows directory link that redirects one folder path to another folder. Junctions are often used for path compatibility, moving folders, or keeping an old path connected to a new storage location. A junction links folders, not normal files.

Neither type is a copy of the target's data. Removing the link itself normally does not remove the file or folder that it targets. Copy and backup software may either preserve the link or follow it and copy the target, so this distinction matters.

This program records only the link itself. It does not enter or follow the link, and it does not hash the target. The `links` table stores:

1. The link's path relative to the source folder.
2. Its kind: `symlink` or `junction`.
3. The target string returned by `readlink()`.

This tool considers a link unchanged when all three values match, even if the target is missing or its contents differ.

A Windows `.lnk` shortcut is not a symbolic link or junction. It is a normal file interpreted by programs such as File Explorer, so this program hashes it like any other normal file.

## Database schema

`files` table:

| Column | Contents |
|---|---|
| `path` | Normal-file path relative to the source folder |
| `sha256` | SHA-256 of the file contents |

`links` table:

| Column | Contents |
|---|---|
| `path` | Link path relative to the source folder |
| `kind` | `symlink` or `junction` |
| `target` | Target string returned by `readlink()` |

`metadata` table:

| `key` | `value` |
|---|---|
| `root_path` | Absolute source path supplied by the user |
| `scan_completed_at_utc` | ISO 8601 UTC time when the DB completed successfully |

File size and modification time are used only for change detection during scanning and are not stored permanently in the DB.
