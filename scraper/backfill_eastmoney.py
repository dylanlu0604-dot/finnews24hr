"""
一次性回補：對 EASTMONEY_SOURCES 每個欄目抓 EM_TARGET（預設 2000）筆寫入 news.db，
然後更新 docs/data.json。
- 重用 fetch_sina.py 的 fetch_eastmoney_fastnews / process_eastmoney_item / upsert_items / export_news_json
- 跳過 sina / TE / MKT / AI 摘要 / market 報價（這些由排程主流程負責）
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

# 預設回補量；可被環境變數覆蓋
os.environ.setdefault("EM_TARGET", "2000")

sys.path.insert(0, os.path.dirname(__file__))
import fetch_sina as fs  # noqa: E402


def main() -> int:
    db_path = fs.NEW_DB_PATH if os.path.exists(fs.NEW_DB_PATH) else fs.DB_PATH
    print(f"[INFO] DB = {db_path}")
    print(f"[INFO] EM_TARGET = {fs.EASTMONEY_TARGET}")
    print(f"[INFO] sources = {len(fs.EASTMONEY_SOURCES)}")

    conn = sqlite3.connect(db_path)
    fs.init_db(conn)

    total_inserted = 0
    for slug, column_id, category, sublabel in fs.EASTMONEY_SOURCES:
        before = conn.execute(
            "SELECT COUNT(*) FROM news WHERE id LIKE ?", (f"em:{slug}:%",)
        ).fetchone()[0]
        print(f"\n[{slug}] === {category}/{sublabel} (fastColumn={column_id}) ===")
        raw_items = fs.fetch_eastmoney_fastnews(slug, column_id, fs.EASTMONEY_TARGET)
        print(f"[{slug}] fetched {len(raw_items)} items from API")
        processed = [
            item for item in (
                fs.process_eastmoney_item(r, slug, category, sublabel)
                for r in raw_items
            ) if item
        ]
        new_count = fs.upsert_items(conn, processed)
        after = conn.execute(
            "SELECT COUNT(*) FROM news WHERE id LIKE ?", (f"em:{slug}:%",)
        ).fetchone()[0]
        total_inserted += new_count
        print(f"[{slug}] inserted={new_count} (db rows: {before} → {after})")
        time.sleep(0.5)

    print(f"\n[INFO] 寫 docs/data.json ...")
    updated, total = fs.export_news_json(conn)
    print(f"[OK] data.json updated={updated} count={total}")
    conn.close()
    print(f"\n[GRAND TOTAL] inserted={total_inserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
