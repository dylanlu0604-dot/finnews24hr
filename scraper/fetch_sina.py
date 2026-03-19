"""
- 爬取最新快訊（簡體）→ 轉換繁體（臺灣正體）
- 輸出至 docs/data.json，供 GitHub Pages 讀取
"""

import requests
import json
import os
from datetime import datetime, timezone, timedelta

try:
    from opencc import OpenCC
    cc = OpenCC("s2twp")   # 簡體 → 臺灣繁體 + 詞彙替換
    HAS_OPENCC = True
except ImportError:
    HAS_OPENCC = False
    print("[WARN] opencc-python-reimplemented 未安裝，將保留簡體輸出")

TW = timezone(timedelta(hours=8))


def to_tw(text: str) -> str:
    if HAS_OPENCC and text:
        return cc.convert(text)
    return text


def fetch_sina_7x24(pages: int = 20, page_size: int = 50) -> list[dict]:
    url = "https://zhibo.sina.com.cn/api/zhibo/feed"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/7x24/?tag=7",
    }

    all_items = []
    seen_ids = set()

    for page in range(1, pages + 1):
        params = {
            "zhibo_id": 152,
            "tag": 0,
            "page": page,
            "page_size": page_size,
            "dire": "f",
            "dpc": 1,
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            news_list = r.json()["result"]["data"]["feed"]["list"]
        except Exception as e:
            print(f"[ERROR] page {page}: {e}")
            break

        if not news_list:
            break

        for item in news_list:
            item_id = str(item.get("id", ""))
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)


            tags = [to_tw(t["name"]) for t in item.get("tag", []) if t.get("name")]
            text = to_tw(item.get("rich_text", "").strip())
            
            # 央行分類：標籤或內文含關鍵字自動歸類
            CB_KEYWORDS = ["央行", "聯儲"]
            if any(kw in tag for tag in tags for kw in CB_KEYWORDS) or \
               any(kw in text for kw in CB_KEYWORDS):
                if "央行" not in tags:
                    tags.insert(0, "央行")

            

            all_items.append({
                "id": item_id,
                "time": item.get("create_time", ""),
                "tags": tags,
                "text": text,
            })

    return all_items


def main():
    print(f"[{datetime.now(TW).strftime('%Y-%m-%d %H:%M:%S')}] 開始爬取...")
    items = fetch_sina_7x24(pages=20, page_size=50)

    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "updated": now_str,
        "count": len(items),
        "items": items,
    }

    # 確保 docs/ 目錄存在
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "data.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] 共 {len(items)} 筆 → {out_path}（更新時間：{now_str}）")


if __name__ == "__main__":
    main()
