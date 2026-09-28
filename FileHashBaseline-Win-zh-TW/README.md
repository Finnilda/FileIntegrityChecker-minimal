# Windows 檔案雜湊基準

`create_baseline.py` 會掃描指定資料夾，將普通檔案的 SHA-256、symbolic link / Windows junction 本身，以及掃描資訊寫入 `baseline.db`。

程式只使用 Python 標準函式庫，沒有第三方套件、網路連線或上傳功能。來源檔案只會以二進位唯讀模式開啟，不會被修改。

## 需求

- Windows
- Python 3.12 或更新版本（程式使用 `Path.is_junction()`）

## 使用方式

直接執行程式但不提供參數時，程式會提示輸入來源資料夾的絕對路徑：

```powershell
python create_baseline.py
```

```text
請輸入要掃描的資料夾絕對路徑：D:/My Folder
```

也可以直接將路徑當成命令列參數：

```powershell
python create_baseline.py "D:/My Folder"
```

Python 在 Windows 上可以接受路徑中的正斜線 `/`。這裡刻意使用正斜線，避免反斜線 `\` 在部分日文字型中被顯示成 `¥`。路徑包含空格時，命令列參數必須放在雙引號內；互動輸入時即使貼上的路徑帶有外層雙引號，程式也會自動移除。

完成後，程式會在 `create_baseline.py` 旁建立 `baseline.db`。

## 掃描方式

- 使用 `os.scandir()` 逐一列舉並處理項目，不先把完整路徑清單保存在記憶體中。
- 普通檔案以每次 1 MiB 的方式分段讀取並計算 SHA-256。
- 雜湊前後會比較檔案大小與修改時間；若不同，代表讀取期間檔案被更動，整次建立會失敗。
- 明確排除正在建立的 SQLite DB、journal、WAL 與 SHM 檔案。
- `files.sha256` 有索引，方便未來依 SHA-256 尋找內容相同的檔案。

`walk_entries()` 是一個 generator。每次執行 `yield path` 時，它只把目前這個項目交給 `main()`，然後暫停；`main()` 記錄 link，或完成檔案的 SHA-256 與 DB 寫入後，才會要求下一個項目。如此會逐筆列舉、逐筆處理，不需要先將完整路徑清單保存在記憶體中。

所有資料庫修改都在同一個 transaction 中。全部成功才會 commit；任何步驟失敗都會 rollback，因此不會留下只有部分掃描結果的新基準。若原本已有完整 DB，失敗時會保留原本的資料。

## Symbolic link 與 junction

Symbolic link（符號連結）是檔案系統中的連結物件，可以指向另一個檔案或資料夾。程式存取 symbolic link 時，作業系統通常會把操作導向它所指向的目標；如果目標被移動或刪除，link 本身仍可能存在，但會變成失效的連結。

Junction（目錄連接點）是 Windows 的資料夾連結，會將一個資料夾路徑導向另一個資料夾。它常用於路徑相容、搬移資料夾，或讓舊路徑繼續指向新的儲存位置。Junction 只能作為資料夾連結，不會指向普通檔案。

兩者都不是資料內容的副本。刪除 link 本身通常不會刪除它所指向的檔案或資料夾；複製與備份軟體則可能選擇保留 link，或跟隨 link 複製目標內容，因此需要明確區分。

程式只記錄 link 本身，不會進入、不會跟隨，也不會雜湊它指向的內容。`links` 表保存：

1. Link 相對於來源資料夾的路徑。
2. 類型是 `symlink` 或 `junction`。
3. `readlink()` 取得的目標字串。

即使目標不存在或內容不同，只要以上三項相同，本工具就視為同一個 link。

Windows `.lnk` 捷徑不是 symbolic link 或 junction，而是一種由檔案總管等程式解讀的普通檔案。因此 `.lnk` 會像其他普通檔案一樣計算 SHA-256。

## DB 格式

`files` 表：

| 欄位 | 內容 |
|---|---|
| `path` | 普通檔案相對於來源資料夾的路徑 |
| `sha256` | 檔案內容的 SHA-256 |

`links` 表：

| 欄位 | 內容 |
|---|---|
| `path` | Link 相對於來源資料夾的路徑 |
| `kind` | `symlink` 或 `junction` |
| `target` | `readlink()` 取得的目標字串 |

`metadata` 表：

| `key` | `value` |
|---|---|
| `root_path` | 使用者指定的來源絕對路徑 |
| `scan_completed_at_utc` | DB 成功建立完成的 ISO 8601 UTC 時間 |

檔案大小與修改時間只在掃描期間用於變動偵測，不會永久寫入 DB。
