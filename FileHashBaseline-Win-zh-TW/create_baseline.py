"""建立檔案 SHA-256 基準資料庫，並記錄 symbolic link / junction 本身。"""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import os
import sqlite3
import sys


# DB 放在本程式旁邊；resolve() 讓後面的排除比對使用統一的絕對路徑。
DB_PATH = Path(__file__).with_name("baseline.db").resolve()

CHUNK_SIZE = 1024 * 1024  # 每次只讀 1 MiB，避免大型檔案占用大量記憶體。


def hash_file(path: Path) -> str:
    """計算 SHA-256；若讀取前後的大小或修改時間不同，代表檔案在讀取期間被更動，就拒絕結果。"""
    digest = sha256()  # 建立一個全新的 SHA-256 累積器。

    with path.open("rb") as source:  # 用二進位唯讀模式開檔，離開區塊時自動關閉。
        before = os.fstat(source.fileno())  # 記錄雜湊前的大小與修改時間。

        while chunk := source.read(CHUNK_SIZE):  # 每次讀 1 MiB
            digest.update(chunk)  # 將目前區塊加入同一份 SHA-256 累積器。

        after = os.fstat(source.fileno())  # 雜湊完成後、同一個已開啟檔案的狀態。

    # 大小或修改時間不同，代表計算期間檔案曾改變；停止並回報錯誤，避免回傳不可靠的雜湊值。
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"File changed while hashing: {path}")

    return digest.hexdigest()  # 回傳十六進位的 SHA-256 值。


def walk_entries(root: Path):
    """走訪 root；深入普通資料夾，但不進入 symbolic link 或 junction。"""
    pending = [root]  # 待掃描資料夾堆疊；一開始只有使用者指定的來源資料夾。

    while pending:
        folder = pending.pop()

        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)  # 轉成 Path，讓 main() 能使用 relative_to() 和 readlink()。

                # 只有非 link 的普通資料夾需要深入；link 和其他項目則交給 main()。
                if not (entry.is_symlink() or entry.is_junction()) and entry.is_dir():
                    pending.append(path)  # 稍後列舉資料夾內容；不會使用遞迴呼叫堆疊。
                else:
                    yield path  # 一次交出一個項目；main() 處理完成後，才繼續列舉下一個。


def get_root() -> Path:
    """優先讀取命令列參數；沒有參數時，提示使用者輸入來源資料夾。"""
    if len(sys.argv) > 2:
        raise SystemExit(
            "Error: too many arguments.\n"
            'Usage example: python create_baseline.py "D:/My Folder"'
        )

    if len(sys.argv) == 2:
        return Path(sys.argv[1])

    user_input = input("請輸入要掃描的資料夾絕對路徑：").strip().strip('"')
    return Path(user_input)


def main(root: Path) -> None:
    """串流掃描指定資料夾，並用單一 transaction 重新建立完整基準資料庫。"""
    if not root.is_absolute():  # 確認使用者輸入的是絕對路徑。
        raise SystemExit(f"Root must be an absolute path: {root}")

    if not root.is_dir():  # 確認路徑存在，而且確實是資料夾。
        raise SystemExit(f"Folder not found: {root}")

    # SQLite 可能在 DB 旁產生三種附屬檔；四個路徑都不能成為掃描資料。
    ignored = {
        DB_PATH,  # 正式 SQLite DB。
        Path(f"{DB_PATH}-journal").resolve(),  # 預設 rollback journal。
        Path(f"{DB_PATH}-wal").resolve(),  # WAL 模式的 write-ahead log。
        Path(f"{DB_PATH}-shm").resolve(),  # WAL 模式的 shared-memory 檔。
    }

    file_count = 0  # 已成功寫入 files 表的普通檔案數量。
    link_count = 0  # 已成功寫入 links 表的 symlink / junction 數量。

    db = sqlite3.connect(DB_PATH)  # 開啟 DB；不存在時由 SQLite 建立新檔案。

    try:
        db.execute("BEGIN")
        db.execute("DROP TABLE IF EXISTS files")  # 如果有的話，刪除上一次的普通檔案基準。
        db.execute("DROP TABLE IF EXISTS links")  # 如果有的話，刪除上一次的連結基準。
        db.execute("DROP TABLE IF EXISTS metadata")  # 如果有的話，刪除上一次的掃描 metadata。

        # 普通檔案保存相對路徑和內容雜湊。
        db.execute(
            """
            CREATE TABLE files (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL
            )
            """
        )

        # 連結只保存 link 本身；不檢查、不跟隨，也不雜湊它指向的內容。
        db.execute(
            """
            CREATE TABLE links (
                path TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('symlink', 'junction')),
                target TEXT NOT NULL
            )
            """
        )

        # metadata 使用 key/value 結構，未來可新增欄位而不必改變資料表 schema。
        db.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        # 自訂列舉器會回報 link 本身，但不會進入 symlink 或 junction 的目標。
        for path in walk_entries(root):
            relative_path = str(path.relative_to(root))  # DB 只保存相對來源資料夾的路徑。

            if path.is_junction():
                db.execute(
                    "INSERT INTO links VALUES (?, ?, ?)",
                    (relative_path, "junction", str(path.readlink())),  # 保存相對路徑與 readlink() 取得的目標字串。
                )
                link_count += 1
                continue

            if path.is_symlink():
                db.execute(
                    "INSERT INTO links VALUES (?, ?, ?)",  # 和 junction 共用同一份簡單 schema。
                    (relative_path, "symlink", str(path.readlink())),  # 保存相對路徑與 readlink() 取得的目標字串。
                )
                link_count += 1
                continue

            if not path.is_file():  # 其他非普通檔案項目不需要寫入 DB。
                continue

            if path in ignored:  # 排除本程式正在寫入的 SQLite DB 與附屬檔。
                continue

            file_hash = hash_file(path)  # 普通檔案開啟並計算 SHA-256。
            db.execute(
                "INSERT INTO files VALUES (?, ?)",
                (relative_path, file_hash),  # 寫入相對路徑與十六進位 SHA-256。
            )

            file_count += 1
            print(f"\rHashed {file_count:,} files; recorded {link_count:,} links...", end="", flush=True)

        # 為了加速後續查詢，建立 files 表的 SHA-256 索引。
        db.execute("CREATE INDEX files_sha256_idx ON files (sha256)")

        # 保存使用者指定的來源絕對路徑。
        db.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("root_path", str(root)),
        )

        # 只有全部檔案與連結都處理成功後，才記錄這份 DB 的完成時間。
        db.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("scan_completed_at_utc", datetime.now(UTC).isoformat(timespec="seconds")),
        )

        db.commit()
    except Exception:
        db.rollback()  # 任一步驟失敗，就撤銷這次 transaction 的所有修改。
        raise
    finally:
        db.close()  # 無論成功或失敗都關閉 DB，避免 Windows 持續鎖住檔案。

    # 只有 commit 成功且連線已關閉後，程式才會顯示完成訊息。
    print(f"\nCreated {DB_PATH} with {file_count:,} files and {link_count:,} links.")


if __name__ == "__main__":
    main(get_root())
