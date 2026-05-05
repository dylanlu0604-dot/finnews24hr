"""
財經快訊爬蟲 + 市場報價
- 爬取最新快訊（簡體）→ 轉換繁體（臺灣正體）→ docs/data.json
- 抓取市場報價（yfinance）→ docs/market.json
"""

import requests
import json
import os
import re
import sqlite3
from html import unescape
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv # 新增這一行


load_dotenv()                 # 新增這一行，它會自動抓取 .env 檔的內容

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
EXPORT_DAYS = 7
TE_STREAM_URL = "https://tradingeconomics.com/ws/stream.ashx"
TE_STREAM_TYPES = ("markets", "economy")
AI_SUMMARY_PATH = os.path.join(DOCS_DIR, "ai_summaries.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-nano").strip()

AI_MARKET_CONTEXT_SYMBOLS = [
    {"symbol": "ES=F", "name": "S&P 500 期貨", "kind": "price"},
    {"symbol": "NQ=F", "name": "Nasdaq 期貨", "kind": "price"},
    {"symbol": "GC=F", "name": "黃金", "kind": "price"},
    {"symbol": "CL=F", "name": "WTI 原油", "kind": "price"},
    {"symbol": "BZ=F", "name": "Brent 原油", "kind": "price"},
    {"symbol": "DX-Y.NYB", "name": "DXY 美元指數", "kind": "price"},
    {"symbol": "^VIX", "name": "VIX", "kind": "price"},
    {"symbol": "^TNX", "name": "美國 10 年期殖利率", "kind": "yield"},
]

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


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


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


def fetch_tradingeconomics_stream_news(stream_type: str, limit: int = 50) -> list[dict]:
    if stream_type not in TE_STREAM_TYPES:
        print(f"[WARN] Trading Economics：未知 stream 類型 {stream_type}")
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://tradingeconomics.com/stream?i={stream_type}",
        "X-Requested-With": "XMLHttpRequest",
    }
    params = {"start": 0, "size": limit, "i": stream_type}
    try:
        r = requests.get(TE_STREAM_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            print(f"[WARN] Trading Economics：非預期回應格式 {type(data).__name__}")
            return []
        for item in data:
            if isinstance(item, dict):
                item["_stream_type"] = stream_type
        return data
    except Exception as e:
        print(f"[ERROR] Trading Economics {stream_type}：{e}")
        return []


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


def parse_te_time(value: str) -> str:
    if not value:
        return datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW).strftime("%Y-%m-%d %H:%M:%S")


def process_tradingeconomics_item(raw: dict) -> dict | None:
    stream_type = raw.get("_stream_type", "markets")
    item_id = raw.get("ID") or raw.get("Id") or raw.get("id")
    title = clean_text(raw.get("Title") or raw.get("title") or "")
    desc = clean_text(raw.get("Description") or raw.get("description") or "")
    url = clean_text(raw.get("Url") or raw.get("URL") or raw.get("url") or "")
    if not item_id or not (title or desc) or not url:
        return None

    text = title if not desc or desc == title else f"{title} — {desc}"
    tags = ["Trading Economics", stream_type.title()]
    for key in ("Country", "Category", "Symbol"):
        value = clean_text(raw.get(key) or raw.get(key.lower()) or "")
        if value and value not in tags:
            tags.append(value)
    importance = raw.get("Importance") or raw.get("importance")
    if importance not in (None, ""):
        tags.append(f"Importance {importance}")

    return {
        "id": f"te:{stream_type}:{item_id}",
        "time": parse_te_time(raw.get("Date") or raw.get("date") or ""),
        "tags": [to_tw(t) for t in tags],
        "text": to_tw(text),
    }


def upsert_items(conn, items: list[dict]) -> int:
    new_count = 0
    for item in items:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO news (id, time, tags, text) VALUES (?,?,?,?)",
                (item["id"], item["time"],
                 json.dumps(item["tags"], ensure_ascii=False), item["text"])
            )
            if cur.rowcount > 0:
                new_count += 1
        except Exception as e:
            print(f"[DB ERROR] {e}")
    conn.commit()
    return new_count


def export_news_json(conn) -> tuple[str, int]:
    cutoff = (datetime.now(TW) - timedelta(days=EXPORT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT id, time, tags, text FROM news WHERE time >= ? ORDER BY time DESC",
        (cutoff,),
    ).fetchall()
    items = [{"id": r[0], "time": r[1], "tags": json.loads(r[2]), "text": r[3]} for r in rows]
    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now_str, "count": len(items), "items": items},
                  f, ensure_ascii=False, indent=2)
    return now_str, len(items)


# ════════════════════════════
# AI 摘要分析
# ════════════════════════════
SLOT_HOURS = 3  # 每個 AI 摘要時段長度（小時）


def time_slot(now: datetime) -> tuple[datetime, datetime, str]:
    """以當前時間切出最近一個完整 SLOT_HOURS 時段。回傳 (slot_start, slot_end, slot_id)。

    slot_id 格式：YYYYMMDDHH（slot_start 的年月日小時）
    例：2026-05-05 03:00 → 06:00 的 slot_id = "2026050503"
    """
    slot_end = now.replace(hour=(now.hour // SLOT_HOURS) * SLOT_HOURS, minute=0, second=0, microsecond=0)
    slot_start = slot_end - timedelta(hours=SLOT_HOURS)
    slot_id = slot_start.strftime("%Y%m%d%H")
    return slot_start, slot_end, slot_id


def load_ai_summaries() -> dict:
    if not os.path.exists(AI_SUMMARY_PATH):
        return {"updated": "", "items": []}
    try:
        with open(AI_SUMMARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data
    except Exception as e:
        print(f"[WARN] ai_summaries.json 讀取失敗：{e}")
        return {"updated": "", "items": []}


def save_ai_summaries(data: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    # 保留最近 80 個時段（3 小時 × 80 ≈ 10 天）
    data["items"] = sorted(data.get("items", []), key=lambda x: x.get("slot_start", ""), reverse=True)[:80]
    data["updated"] = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    with open(AI_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def recent_news_for_summary(conn, start: datetime, end: datetime, limit: int = 330) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, time, tags, text
        FROM news
        WHERE time >= ? AND time < ?
        ORDER BY time DESC
        LIMIT ?
        """,
        (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), limit),
    ).fetchall()
    return [{"id": r[0], "time": r[1], "tags": json.loads(r[2]), "text": r[3]} for r in rows]


def compact_news_input(items: list[dict]) -> str:
    lines = []
    for item in items:
        tags = ",".join(item.get("tags", [])[:5])
        text = clean_text(item.get("text", ""))[:260]
        lines.append(f"- {item.get('time')} [{tags}] {text}")
    return "\n".join(lines)


def pct_change(current: float, previous: float | None) -> float | None:
    if previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def format_market_price(value: float, kind: str) -> str:
    if kind == "yield":
        return f"{value:.2f}%"
    if abs(value) >= 100:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def fetch_ai_market_context() -> str:
    """抓 AI 摘要專用市場背景，避免標題只被新聞風險詞帶偏。"""
    if not HAS_YF:
        return ""

    lines = []
    for item in AI_MARKET_CONTEXT_SYMBOLS:
        symbol = item["symbol"]
        name = item["name"]
        kind = item["kind"]
        try:
            hist = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=True)
            if hist.empty or "Close" not in hist:
                continue

            closes = hist["Close"].dropna()
            if closes.empty:
                continue

            current = float(closes.iloc[-1])
            prev_1m = float(closes.iloc[-22]) if len(closes) >= 22 else None
            prev_3m = float(closes.iloc[-64]) if len(closes) >= 64 else None
            high_3m = float(closes.tail(min(len(closes), 64)).max())

            one_month = pct_change(current, prev_1m)
            three_month = pct_change(current, prev_3m)
            high_gap = pct_change(current, high_3m)
            high_note = "，接近 3 個月高點" if high_gap is not None and high_gap >= -1 else ""

            if kind == "yield":
                one_month_text = f"{(current - prev_1m) * 100:+.0f}bp" if prev_1m is not None else "n/a"
                three_month_text = f"{(current - prev_3m) * 100:+.0f}bp" if prev_3m is not None else "n/a"
            else:
                one_month_text = format_pct(one_month)
                three_month_text = format_pct(three_month)

            lines.append(
                f"- {name}：最新 {format_market_price(current, kind)}，"
                f"近 1 個月 {one_month_text}，近 3 個月 {three_month_text}{high_note}"
            )
        except Exception as e:
            print(f"[YF AI CONTEXT ERROR] {symbol}: {e}")

    return "\n".join(lines)


def extract_response_text(response: dict) -> str:
    if response.get("output_text"):
        return response["output_text"]
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "".join(parts)


def call_openai_summary(items: list[dict], start: datetime, end: datetime, market_context: str = "") -> dict:
    prompt = f"""
你是財經新聞編輯。請根據以下 {len(items)} 則近 3 小時快訊，產生繁體中文（台灣）AI 摘要分析。
時間區間：{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')}（台灣時間）

【市場價格背景（生成標題與摘要前必須先校準）】
以下為主要資產截至摘要生成時的最新價格與中期表現。請把它當成判斷市場實際風險偏好的基準，而不是只根據新聞語氣下標。
{market_context or "- 本次未取得市場價格背景，請只根據快訊中明確價格資料判斷。"}

規則：
- 若 S&P 500 / Nasdaq 維持高檔或近 1~3 個月上漲，summary_title 不得只寫「風險升溫」「地緣緊張」「避險升溫」等單邊負面標題。
- 標題必須同時反映「風險資產價格狀態」與「新聞風險事件」。例如：股市高檔震盪、風險資產守高、油金波動牽動盤面。
- 若新聞風險很多，但股市、信用或其他風險資產仍強，請寫成「市場消化風險」或「高檔震盪」，不要寫成全面 risk-off。
- C 級市場行情事件需優先整合上述 S&P 500、黃金、WTI/Brent、DXY、VIX、美債殖利率的價格與 1 個月/3 個月表現。

【篩選與排序原則（內部使用，請勿輸出等級標示）】
請依下列重要程度由高到低，從快訊中挑出最具市場參考價值的事件（5~10 則），順序由最重要排到次重要：



S 級（最優先）：主要經濟體最新數據公佈
   例：美國 4 月 CPI 創一年新高、非農就業、歐元區 GDP、中國 PMI、PCE、零售銷售、JOLTS。

A 級：主要經濟體政策公布
   例：川普宣布對歐洲關稅、澳洲央行升息、Fed 利率決議、財政部新政、人行降準。

B 級：全球重要公司消息（限具全球影響力之大型企業）
   例：ASML Q1 營收高於預期、NVDA 財報前瞻、OpenAI 延後 IPO、Apple、TSMC、Microsoft。

C 級：全球市場行情摘要整理
    全球股市、匯市、債市、原物料、加密貨幣等主要資產行情整理。不要分開寫很多則，儘量合併寫在1~2則。需要儘量提到數據表現。

D 級：地緣政治
   例：美伊停火談判延後


【請過濾掉以下垃圾級內容，不得納入 10 則事件】
- 中國、香港中小型公司個股消息（如：長和註銷 Vodafone Three 股份、江西銅業 (00358.HK) 跌超 4.5%）。
- 中國期貨市場價格變動（如：滬鎳主力合約跌、鐵礦石期貨夜盤收紅）。

輸出要求：
1. summary_title：一句 15 字以內標題。
2. summary：150~250 字以內總摘要。
3. risk_on_score：0~10 分數，10 代表市場完全 risk on，0 代表市場完全 risk off。
4. fundamental_surprise_score：0~10 分數，10 代表經濟數據與財報全面優於預期，0 代表全面低於預期。
5. score_commentary：一句 60 字以內說明兩個分數的主要依據。
6. events：5~10 個重點事件，依該時段實際重要事件多寡決定數量，依重要程度由高到低排序（S→A→B→C→D）。
7. 每個事件包含 title、explanation、impact。
8. 每個 explanation 約 150~250 字，說明事件。
9. **請勿在任何欄位輸出「S級 / A 級 / B 級 / C 級 / D 級」等分級標示**，分級僅作為內部篩選依據。
10. 不要編造事實；若訊息不足，寧可寫得簡短或減少事件數量，也不要硬湊。
11. 清淡時段寧可只給 5 則高質量事件，也不要為了湊數納入垃圾級內容。

【分數規則 — 請直接根據 summary、events 與市場價格背景評分】
risk_on_score：
- 8~10：股市/信用/科技/高 beta 資產強，VIX低，市場明顯 risk on。
- 5~7：股市仍高檔或漲跌互見，但油金、美元、利率或地緣風險使情緒分歧。
- 3~4：風險資產承壓、股市下跌，避險資產或美元需求升溫。
- 0~2：全面 risk off，股市、信用、週期資產同步轉弱，VIX走強。

fundamental_surprise_score：
- 8~10：主要經濟數據、企業財報或指引大多優於預期。
- 5~7：數據與財報混合偏正面，或缺少足夠明確 surprise。
- 3~4：數據與財報混合偏負面，或成長/需求訊號轉弱。
- 0~2：主要數據與財報普遍低於預期，財報公布後股價大幅下跌。
- 若時段缺少經濟數據與財報訊息，請給 5 分附近，不要硬判極端。

【寫作風格 — 嚴格遵守，違反者視為錯誤輸出】

▸ 嚴禁「轉述快訊」的元敘事語氣 ◂
你是財經分析師，正在直接撰寫市場評論，不是在引用新聞稿。讀者不該看到「快訊」這個概念存在。

❌ 嚴禁出現以下用語（看到任何一個就是錯）：
- 「快訊顯示」「快訊提到」「快訊指出」「快訊表示」「根據快訊」
- 「一則快訊⋯另一則快訊⋯」「多則快訊」「另有快訊」
- 「報價資料顯示」「多次報價出現不同⋯」「報價⋯」
- 「在 6 小時區間內」「6 小時時段中」「本時段」「此區間」
- 「消息面顯示」「市場傳出」（除非快訊原文如此）

✅ 正確寫法（直接陳述市場行為）：
不要寫：「快訊顯示 WTI 原油日內失守 104 美元」
要寫：  「WTI 原油盤中跌破 104 美元，日內跌幅約 2%」

不要寫：「一則快訊提到布倫特突破 114 美元，另一則顯示回落」
要寫：  「布倫特原油盤中觸及 114 美元後回吐，呈衝高拉回走勢」

不要寫：「快訊報導印度盧比續創新低」
要寫：  「印度盧比兌美元貶至 95.4，續創歷史新低」

不要寫：「在 6 小時區間內多次報價出現不同日內跌幅」
要寫：  「日內走勢震盪，由高點 114 美元回落至 113 美元附近」

▸ 風格要點 ◂
- 用財經評論的直述語氣，主詞是「市場標的本身」（油價、美元、台股⋯），不是「快訊」。
- 同一事件若有多個時點數據，整合成一句「先⋯後⋯」「由⋯轉為⋯」，不要分述為「一則⋯另一則⋯」。
- 句子要連貫、流暢，避免「⋯，並⋯，並⋯」這種堆砌式語法。
- 中文用詞要符合台灣財經媒體慣例（殖利率、盤中、日內、創高、回吐、衝高拉回、買盤、賣壓⋯）。
- 不要用簡體用語（漲跌幅 ✅、涨跌幅 ❌；殖利率 ✅、收益率 ❌；盤中 ✅、盘中 ❌）。

【impact 欄位特別規則 — 嚴格遵守】
impact 欄位的目的是記錄「快訊中明確報導的市場實際反應或量化數據」，不是預測。

✅ 正確示範（impact 應該長這樣）：
- 「美元指數升至 99.45，日內 +0.42%」
- 「布倫特原油觸及 114.2 美元，創 18 個月新高」
- 「印度盧比兌美元跌至 95.4，續創歷史新低」
- 「美 30 年期殖利率升破 5.0%」
- 「Palantir 盤後上漲 8%，每股盈餘 0.33 美元優於預期」
- 「澳元/美元跳升 0.6% 至 0.6582」

❌ 嚴禁輸出（這些都是硬寫的廢話，請一律避免）：
- 「可能引發市場波動」「將影響投資情緒」「可能提振相關類股」
- 「投資者需密切關注」「短期內持續波動」「市場觀望情緒濃厚」
- 「可能對全球經濟造成衝擊」「料將推升避險需求」
- 任何含「可能」「恐將」「料將」「預期會」「或將」的推測句。
- 任何沒有具體數字、價格、百分比的通用評論。

規則：
- 若快訊中有明確的價格、漲跌幅、成交量、利率變動、匯率數字 → 寫進 impact。
- 若快訊只是政策宣布 / 數據公布 / 公司新聞，但沒有附帶市場反應 → impact 一律輸出空字串 ""，**寧缺勿濫**。
- 寫 impact 前先自問：「這句話有具體數字或可驗證事實嗎？」沒有就留空。

快訊：
{compact_news_input(items)}
""".strip()

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary_title": {"type": "string"},
            "summary": {"type": "string"},
            "risk_on_score": {"type": "number", "minimum": 0, "maximum": 10},
            "fundamental_surprise_score": {"type": "number", "minimum": 0, "maximum": 10},
            "score_commentary": {"type": "string"},
            "events": {
                "type": "array",
                "minItems": 5,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "explanation": {"type": "string"},
                        "impact": {"type": "string"},
                    },
                    "required": ["title", "explanation", "impact"],
                },
            },
        },
        "required": [
            "summary_title",
            "summary",
            "risk_on_score",
            "fundamental_surprise_score",
            "score_commentary",
            "events",
        ],
    }
    payload = {
        "model": OPENAI_SUMMARY_MODEL,
        "input": [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": "你只輸出符合 JSON schema 的繁體中文財經摘要。"}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "three_hour_market_summary",
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": 3600,
    }
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    text = extract_response_text(r.json())
    return json.loads(text)


def has_ai_scores(item: dict) -> bool:
    return item.get("risk_on_score") is not None and item.get("fundamental_surprise_score") is not None


def update_ai_summaries(conn) -> None:
    now = datetime.now(TW)
    data = load_ai_summaries()
    market_context = fetch_ai_market_context() if OPENAI_API_KEY else ""
    if market_context:
        print("[AI] 已取得摘要用市場價格背景")

    # ── Backfill 模式 ──
    # 平常排程只處理目前這個 3 小時時段；需要一次性補歷史時，才設定環境變數。
    # BACKFILL_FROM=YYYY-MM-DD：回溯該日期之後的所有時段。
    # AI_SCORE_BACKFILL_DAYS=3：只回補最近 N 天缺 score 的既有摘要。
    backfill_from = os.getenv("BACKFILL_FROM", "").strip()
    score_backfill_days = os.getenv("AI_SCORE_BACKFILL_DAYS", "").strip()
    if backfill_from:
        try:
            backfill_dt = datetime.strptime(backfill_from, "%Y-%m-%d").replace(tzinfo=TW)
            hours_back = (now - backfill_dt).total_seconds() / 3600
            target_slots = max(1, int(hours_back // SLOT_HOURS) + 1)
            print(f"[BACKFILL] 回溯模式：從 {backfill_from} 起，預計處理 {target_slots} 個 {SLOT_HOURS} 小時時段")
        except ValueError:
            print(f"[ERROR] BACKFILL_FROM 格式錯誤（需為 YYYY-MM-DD）：{backfill_from}，改用預設值")
            target_slots = 1
    elif score_backfill_days:
        try:
            target_slots = max(1, int(float(score_backfill_days) * 24 / SLOT_HOURS))
            print(f"[BACKFILL] 分數回補模式：最近 {score_backfill_days} 天，預計處理 {target_slots} 個 {SLOT_HOURS} 小時時段")
        except ValueError:
            print(f"[ERROR] AI_SCORE_BACKFILL_DAYS 格式錯誤（需為數字）：{score_backfill_days}，改用預設值")
            target_slots = 1
    else:
        target_slots = 1

    current_time = now
    new_count = 0
    updated_scores = 0
    skipped_existing = 0
    skipped_thin = 0

    for i in range(target_slots):
        slot_start, slot_end, slot_id = time_slot(current_time)
        existing_idx = next((idx for idx, item in enumerate(data.get("items", [])) if item.get("id") == slot_id), None)

        if existing_idx is not None and has_ai_scores(data["items"][existing_idx]):
            skipped_existing += 1
            print(f"[AI] {slot_id} 已有摘要，略過")
        else:
            items = recent_news_for_summary(conn, slot_start, slot_end)
            # 3 小時時段門檻降低為 5 則
            if len(items) < 5:
                skipped_thin += 1
                print(f"[AI] {slot_id} 近 {SLOT_HOURS} 小時快訊不足 ({len(items)} 筆)，略過")
            elif not OPENAI_API_KEY:
                print(f"[SKIP] AI 摘要 {slot_id}：未設定 OPENAI_API_KEY")
            else:
                try:
                    result = call_openai_summary(items, slot_start, slot_end, market_context)
                    summary_item = {
                        "id": slot_id,
                        "slot_start": slot_start.strftime("%Y-%m-%d %H:%M:%S"),
                        "slot_end": slot_end.strftime("%Y-%m-%d %H:%M:%S"),
                        "generated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S"),
                        "count": len(items),
                        **result,
                    }
                    if existing_idx is None:
                        data.setdefault("items", []).insert(0, summary_item)
                        new_count += 1
                        print(f"[OK] AI 摘要：新增 {slot_id} ({len(items)} 則快訊)")
                    else:
                        data["items"][existing_idx] = {**data["items"][existing_idx], **summary_item}
                        updated_scores += 1
                        print(f"[OK] AI 摘要：補分數 {slot_id} ({len(items)} 則快訊)")
                except Exception as e:
                    print(f"[ERROR] AI 摘要 {slot_id} 失敗：{e}")

        # 將時間設為上一個時段的起點，準備下一次迴圈
        current_time = slot_start

    if backfill_from or score_backfill_days:
        print(f"[BACKFILL] 完成：新增 {new_count} 個，補分數 {updated_scores} 個，已存在 {skipped_existing} 個，快訊不足略過 {skipped_thin} 個")

    # 迴圈結束後，統一存檔
    save_ai_summaries(data)


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
    sina_new_count = upsert_items(conn, processed)

    te_new_counts = {}
    for stream_type in TE_STREAM_TYPES:
        te_raw_items = fetch_tradingeconomics_stream_news(stream_type, limit=50)
        te_processed = [item for item in (process_tradingeconomics_item(r) for r in te_raw_items) if item]
        te_new_counts[stream_type] = upsert_items(conn, te_processed)

    update_ai_summaries(conn)
    updated, total = export_news_json(conn)
    conn.close()
    te_summary = "｜".join(f"TE {name} 新增 {count} 筆" for name, count in te_new_counts.items())
    print(f"[OK] 快訊：新浪新增 {sina_new_count} 筆｜{te_summary}｜累計 {total} 筆")

    # 2. 市場報價
    if HAS_YF:
        print("[市場] 開始抓取報價...")
        market_data = fetch_all_market()
        export_market_json(market_data)
    else:
        print("[SKIP] yfinance 未安裝，跳過市場資料")


if __name__ == "__main__":
    main()
