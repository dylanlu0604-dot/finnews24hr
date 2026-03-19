"""
財經快訊爬蟲
- 爬取最新快訊（簡體）→ 轉換繁體（臺灣正體）
- SQLite 資料庫去重累積，每次只需爬少量頁數
- 輸出至 docs/data.json，供 GitHub Pages 讀取
"""

import requests
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

try:
    from opencc import OpenCC
    cc = OpenCC("s2twp")
    HAS_OPENCC = True
except ImportError:
    HAS_OPENCC = False
    print("[WARN] opencc-python-reimplemented 未安裝，將保留簡體輸出")

TW = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "news.db")
OUT_PATH = os.path.join(BASE_DIR, "..", "docs", "data.json")


def to_tw(text: str) -> str:
    if HAS_OPENCC and text:
        return cc.convert(text)
    return text


# ── 資料庫初始化 ──
def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id        TEXT PRIMARY KEY,
            time      TEXT,
            tags      TEXT,   -- JSON array
            text      TEXT
        )
    """)
    conn.commit()


# ── 爬取原始資料 ──
def fetch_pages(pages: int = 5, page_size: int = 50) -> list[dict]:
    url = "https://zhibo.sina.com.cn/api/zhibo/feed"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/7x24/?tag=7",
    }
    results = []
    for page in range(1, pages + 1):
        params = {
            "zhibo_id": 152, "tag": 0,
            "page": page, "page_size": page_size,
            "dire": "f", "dpc": 1,
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            results.extend(r.json()["result"]["data"]["feed"]["list"])
        except Exception as e:
            print(f"[ERROR] page {page}: {e}")
            break
    return results


# ── 處理單筆快訊 ──
def process_item(raw: dict) -> dict:
    tags = [to_tw(t["name"]) for t in raw.get("tag", []) if t.get("name")]
    text = to_tw(raw.get("rich_text", "").strip())

    # 央行分類
    CB_KEYWORDS = ["央行", "聯儲"]
    if any(kw in tag for tag in tags for kw in CB_KEYWORDS) or \
       any(kw in text for kw in CB_KEYWORDS):
        if "央行" not in tags:
            tags.insert(0, "央行")

    return {
        "id":   str(raw.get("id", "")),
        "time": raw.get("create_time", ""),
        "tags": tags,
        "text": text,
    }


# ── 寫入 DB（跳過已存在的 id）──
def upsert_items(conn, items: list[dict]) -> int:
    new_count = 0
    for item in items:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO news (id, time, tags, text) VALUES (?,?,?,?)",
                (item["id"], item["time"],
                 json.dumps(item["tags"], ensure_ascii=False),
                 item["text"])
            )
            if conn.total_changes > 0:
                new_count += 1
        except Exception as e:
            print(f"[DB ERROR] {e}")
    conn.commit()
    return new_count


# ── 從 DB 讀取全部，輸出 data.json ──
def export_json(conn):
    rows = conn.execute(
        "SELECT id, time, tags, text FROM news ORDER BY time DESC"
    ).fetchall()

    items = [
        {
            "id":   r[0],
            "time": r[1],
            "tags": json.loads(r[2]),
            "text": r[3],
        }
        for r in rows
    ]

    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    payload = {"updated": now_str, "count": len(items), "items": items}

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return now_str, len(items)


# ── 主程式 ──
def main():
    print(f"[{datetime.now(TW).strftime('%Y-%m-%d %H:%M:%S')}] 開始爬取...")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    raw_items  = fetch_pages(pages=5, page_size=50)
    processed  = [process_item(r) for r in raw_items]
    new_count  = upsert_items(conn, processed)

    now_str, total = export_json(conn)
    conn.close()

    print(f"[OK] 本次新增 {new_count} 筆｜資料庫累計 {total} 筆｜更新時間：{now_str}")


if __name__ == "__main__":
    main()
