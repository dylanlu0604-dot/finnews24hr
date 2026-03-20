"""
財經快訊爬蟲 + 市場報價
- 爬取最新快訊（簡體）→ 轉換繁體（臺灣正體）→ docs/data.json
- 抓取市場報價（yfinance）→ docs/market.json
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

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False
    print("[WARN] yfinance 未安裝，跳過市場資料")

TW = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "news.db")
DOCS_DIR = os.path.join(BASE_DIR, "..", "docs")

# ── 市場 symbol 設定 ──
MARKET_SYMBOLS = {
    "equity": {
        "main":  {"symbol": "ES=F",     "name": "S&P 500 期貨"},
        "minis": [
            {"symbol": "NQ=F",       "name": "Nasdaq 期貨"},
            {"symbol": "YM=F",       "name": "Dow 期貨"},
            {"symbol": "NKD=F",      "name": "Nikkei 期貨"},
            {"symbol": "RTY=F",      "name": "Russell 2000 期貨"},
            {"symbol": "^GDAXI",     "name": "DAX"},
            {"symbol": "^VIX",       "name": "VIX 恐慌指數"},
        ]
    },
    "fx": {
        "main":  {"symbol": "DX-Y.NYB", "name": "DXY 美元指數"},
        "minis": [
            {"symbol": "EURUSD=X", "name": "EUR/USD"},
            {"symbol": "JPY=X",    "name": "USD/JPY"},
            {"symbol": "GBPUSD=X", "name": "GBP/USD"},
            {"symbol": "AUDUSD=X", "name": "AUD/USD"},
            {"symbol": "CNY=X",    "name": "USD/CNY"},
            {"symbol": "TWD=X",    "name": "USD/TWD"},
        ]
    },
    "bond": {
        "main":  {"symbol": "^TNX",      "name": "US 10Y 殖利率"},
        "minis": [
            {"symbol": "^IRX",      "name": "US 3M"},
            {"symbol": "^FVX",      "name": "US 5Y"},
            {"symbol": "^TNX",      "name": "US 10Y"},
            {"symbol": "^TYX",      "name": "US 30Y"},
            {"symbol": "HYG",       "name": "高收益債 ETF"},
            {"symbol": "LQD",       "name": "投資級債 ETF"},
        ]
    },
    "commodity": {
        "main":  {"symbol": "CL=F",     "name": "WTI 原油"},
        "minis": [
            {"symbol": "NG=F",     "name": "天然氣"},
            {"symbol": "GC=F",     "name": "黃金"},
            {"symbol": "SI=F",     "name": "白銀"},
            {"symbol": "HG=F",     "name": "銅"},
            {"symbol": "BZ=F",     "name": "Brent 原油"},
            {"symbol": "ZW=F",     "name": "小麥"},
        ]
    },
}


def to_tw(text: str) -> str:
    if HAS_OPENCC and text:
        return cc.convert(text)
    return text


# ════════════════════════════
# 新聞爬蟲
# ════════════════════════════
def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id   TEXT PRIMARY KEY,
            time TEXT,
            tags TEXT,
            text TEXT
        )
    """)
    conn.commit()


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
        params = {"zhibo_id": 152, "tag": 0, "page": page,
                  "page_size": page_size, "dire": "f", "dpc": 1}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            results.extend(r.json()["result"]["data"]["feed"]["list"])
        except Exception as e:
            print(f"[ERROR] page {page}: {e}")
            break
    return results


def process_item(raw: dict) -> dict:
    tags = [to_tw(t["name"]) for t in raw.get("tag", []) if t.get("name")]
    text = to_tw(raw.get("rich_text", "").strip())
    CB_KEYWORDS = ["央行", "聯儲"]
    if any(kw in tag for tag in tags for kw in CB_KEYWORDS) or \
       any(kw in text for kw in CB_KEYWORDS):
        if "央行" not in tags:
            tags.insert(0, "央行")
    return {"id": str(raw.get("id", "")), "time": raw.get("create_time", ""),
            "tags": tags, "text": text}


def upsert_items(conn, items: list[dict]) -> int:
    new_count = 0
    for item in items:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO news (id, time, tags, text) VALUES (?,?,?,?)",
                (item["id"], item["time"],
                 json.dumps(item["tags"], ensure_ascii=False), item["text"])
            )
            if conn.total_changes > 0:
                new_count += 1
        except Exception as e:
            print(f"[DB ERROR] {e}")
    conn.commit()
    return new_count


def export_news_json(conn) -> tuple[str, int]:
    rows = conn.execute(
        "SELECT id, time, tags, text FROM news ORDER BY time DESC"
    ).fetchall()
    items = [{"id": r[0], "time": r[1], "tags": json.loads(r[2]), "text": r[3]} for r in rows]
    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now_str, "count": len(items), "items": items},
                  f, ensure_ascii=False, indent=2)
    return now_str, len(items)


# ════════════════════════════
# 市場報價
# ════════════════════════════
def fetch_symbol(symbol: str) -> dict | None:
    """抓單一 symbol 的報價 + 5分鐘 K 線（最近 2 天）"""
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="2d", interval="5m", auto_adjust=True)
        if hist.empty:
            return None
        info = t.fast_info
        price = float(info.last_price) if info.last_price else None
        prev  = float(info.previous_close) if info.previous_close else None
        # 轉成 {time(unix), open, high, low, close} 陣列
        bars = []
        for ts, row in hist.iterrows():
            unix = int(ts.timestamp())
            bars.append({
                "t": unix,
                "o": round(float(row["Open"]),  6),
                "h": round(float(row["High"]),  6),
                "l": round(float(row["Low"]),   6),
                "c": round(float(row["Close"]), 6),
            })
        return {"price": price, "prev_close": prev, "bars": bars}
    except Exception as e:
        print(f"[YF ERROR] {symbol}: {e}")
        return None


def fetch_all_market() -> dict:
    """抓所有 symbol，回傳扁平化結構"""
    data = {}
    seen = set()
    for panel in MARKET_SYMBOLS.values():
        all_sym = [panel["main"]] + panel["minis"]
        for entry in all_sym:
            sym = entry["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            print(f"  fetching {sym} ({entry['name']})...", end=" ", flush=True)
            result = fetch_symbol(sym)
            if result:
                data[sym] = {**result, "name": entry["name"]}
                print(f"✓ {result['price']:.4f}")
            else:
                print("✗ failed")
    return data


def export_market_json(market_data: dict) -> None:
    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DOCS_DIR, exist_ok=True)
    payload = {
        "updated": now_str,
        "panels": MARKET_SYMBOLS,
        "data":   market_data,
    }
    with open(os.path.join(DOCS_DIR, "market.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[OK] market.json 更新 ({len(market_data)} symbols)")


# ════════════════════════════
# 主程式
# ════════════════════════════
def main():
    now = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 開始執行...")

    # 1. 快訊
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    raw_items = fetch_pages(pages=5, page_size=50)
    processed = [process_item(r) for r in raw_items]
    new_count = upsert_items(conn, processed)
    updated, total = export_news_json(conn)
    conn.close()
    print(f"[OK] 快訊：新增 {new_count} 筆｜累計 {total} 筆")

    # 2. 市場報價
    if HAS_YF:
        print("[市場] 開始抓取報價...")
        market_data = fetch_all_market()
        export_market_json(market_data)
    else:
        print("[SKIP] yfinance 未安裝，跳過市場資料")


if __name__ == "__main__":
    main()
