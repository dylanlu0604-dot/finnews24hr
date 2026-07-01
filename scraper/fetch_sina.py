"""
財經快訊爬蟲 + 市場報價
- 爬取最新快訊（簡體）→ 轉換繁體（臺灣正體）→ docs/data.json
- 抓取市場報價（yfinance）→ docs/market.json
"""

from __future__ import annotations

import requests
import json
import os
import re
import sqlite3
import time
import uuid
from html import unescape
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv # 新增這一行

import fetch_capital_calendar


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
ET = ZoneInfo("America/New_York")
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "news.db")
NEW_DB_PATH = os.path.join(BASE_DIR, "new.db")
DOCS_DIR = os.path.join(BASE_DIR, "..", "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive", "news")
EXPORT_DAYS = 7
try:
    NEWS_DB_RETENTION_DAYS = max(EXPORT_DAYS, int(float(os.getenv("NEWS_DB_RETENTION_DAYS", "14"))))
except ValueError:
    NEWS_DB_RETENTION_DAYS = 14
try:
    NEWS_DB_MAX_MB = max(1, int(float(os.getenv("NEWS_DB_MAX_MB", "90"))))
except ValueError:
    NEWS_DB_MAX_MB = 90
TE_STREAM_URL = "https://tradingeconomics.com/ws/stream.ashx"
TE_STREAM_TYPES = ("markets", "economy")
MKTNEWS_FLASH_URL = "https://api.mktnews.net/api/flash"
MKTNEWS_PAGE_LIMIT = 50
MKTNEWS_DEFAULT_PAGES = 1
EASTMONEY_API = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
EASTMONEY_PAGE_SIZE = 50
try:
    EASTMONEY_TARGET = max(0, int(os.getenv("EM_TARGET", "50")))
except ValueError:
    EASTMONEY_TARGET = 50
# (slug, fastColumn id, 類別 tag, 子標籤)
EASTMONEY_SOURCES = [
    ("qqyh_zgyh",  "118", "央行",     "中國央行"),
    ("qqyh_mlc",   "119", "央行",     "美聯儲"),
    ("qqyh_ozyh",  "120", "央行",     "歐洲央行"),
    ("qqyh_ygyh",  "121", "央行",     "英國央行"),
    ("qqyh_rbyh",  "122", "央行",     "日本央行"),
    ("qqyh_jndyh", "123", "央行",     "加拿大央行"),
    ("qqyh_ozlc",  "124", "央行",     "澳洲聯儲"),
    ("jjsj_zgsj",  "125", "經濟數據", "中國"),
    ("jjsj_mgsj",  "126", "經濟數據", "美國"),
    ("jjsj_oyqsj", "127", "經濟數據", "歐元區"),
    ("jjsj_ygsj",  "128", "經濟數據", "英國"),
    ("jjsj_rbsj",  "129", "經濟數據", "日本"),
    ("jjsj_jndsj", "130", "經濟數據", "加拿大"),
    ("jjsj_ozsj",  "131", "經濟數據", "澳洲"),
]
AI_SUMMARY_PATH = os.path.join(DOCS_DIR, "ai_summaries.json")
CENTRAL_BANK_SUMMARY_PATH = os.path.join(DOCS_DIR, "central_bank_summaries.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-nano").strip()
try:
    CENTRAL_BANK_SUMMARY_HOUR = int(os.getenv("CENTRAL_BANK_SUMMARY_HOUR", "7"))
except ValueError:
    CENTRAL_BANK_SUMMARY_HOUR = 7
try:
    CENTRAL_BANK_AUTO_BACKFILL_DAYS = max(1, int(os.getenv("CENTRAL_BANK_AUTO_BACKFILL_DAYS", "7")))
except ValueError:
    CENTRAL_BANK_AUTO_BACKFILL_DAYS = 7
CENTRAL_BANK_SUMMARY_SCHEMA_VERSION = 7

CENTRAL_BANKS = [
    {"id": "fed", "name": "美國聯準會 Fed", "keywords": ("Fed", "Federal Reserve", "FOMC", "Powell", "Warsh", "Waller", "Bowman", "Jefferson", "Williams", "美聯儲", "聯準會", "鮑威爾", "沃許", "沃什", "聯邦公開市場委員會")},
    {"id": "ecb", "name": "歐洲央行 ECB", "keywords": ("ECB", "European Central Bank", "Lagarde", "Schnabel", "Villeroy", "Lane", "Guindos", "歐洲央行", "歐央行", "拉加德", "歐洲中央銀行")},
    {"id": "boe", "name": "英國央行 BoE", "keywords": ("BoE", "BOE", "Bank of England", "Bailey", "Mann", "Pill", "Lombardelli", "英國央行", "英格蘭銀行", "MPC")},
    {"id": "boj", "name": "日本央行 BoJ", "keywords": ("BoJ", "BOJ", "Bank of Japan", "Ueda", "Uchida", "Himino", "Tamura", "Asada", "Sato", "日本央行", "植田", "日銀", "日本銀行")},
    {"id": "rba", "name": "澳洲聯儲 RBA", "keywords": ("RBA", "Reserve Bank of Australia", "Bullock", "Hauser", "澳洲聯儲", "澳洲央行")},
    {"id": "boc", "name": "加拿大央行 BoC", "keywords": ("BoC", "BOC", "Bank of Canada", "Macklem", "Rogers", "加拿大央行")},
    {"id": "rbi", "name": "印度央行 RBI", "keywords": ("RBI", "Reserve Bank of India", "Das", "Malhotra", "印度央行")},
    {"id": "bcb", "name": "巴西央行 BCB", "keywords": ("BCB", "Banco Central do Brasil", "Galipolo", "Campos Neto", "巴西央行")},
    {"id": "snb", "name": "瑞士央行 SNB", "keywords": ("SNB", "Swiss National Bank", "Schlegel", "瑞士央行")},
]

CENTRAL_BANK_OFFICIAL_ALIASES = {
    "fed": {
        "沃許": ("沃許", "沃什", "Warsh", "Kevin Warsh", "KevinWarsh", "凱文·沃許", "凱文·沃什"),
        "鮑威爾": ("鮑威爾", "Powell", "Jerome Powell"),
        "沃勒": ("沃勒", "Waller", "Christopher Waller"),
        "鮑曼": ("鮑曼", "Bowman", "Michelle Bowman"),
        "傑佛森": ("傑佛森", "Jefferson", "Philip Jefferson"),
        "威廉姆斯": ("威廉姆斯", "Williams", "John Williams"),
        "庫克": ("庫克", "Cook", "Lisa Cook"),
        "庫格勒": ("庫格勒", "Kugler", "Adriana Kugler"),
        "巴爾": ("巴爾", "Barr", "Michael Barr"),
        "施密德": ("施密德", "Schmid", "Jeffrey Schmid"),
        "米蘭": ("米蘭", "Miran"),
        "哈瑪克": ("哈瑪克", "Hammack", "Beth Hammack"),
        "柯林斯": ("柯林斯", "Collins", "Susan Collins"),
        "卡什卡里": ("卡什卡里", "Kashkari", "Neel Kashkari"),
        "古爾斯比": ("古爾斯比", "Goolsbee", "Austan Goolsbee"),
        "戴利": ("戴利", "Daly", "Mary Daly"),
        "博斯蒂克": ("博斯蒂克", "Bostic", "Raphael Bostic"),
        "洛根": ("洛根", "Logan", "Lorie Logan"),
        "巴爾金": ("巴爾金", "Barkin", "Thomas Barkin"),
        "穆薩萊姆": ("穆薩萊姆", "Musalem", "Alberto Musalem"),
    },
    "ecb": {
        "拉加德": ("拉加德", "Lagarde", "Christine Lagarde"),
        "金多斯": ("金多斯", "Guindos", "de Guindos", "Luis de Guindos"),
        "連恩": ("連恩", "Lane", "Philip Lane"),
        "施納貝爾": ("施納貝爾", "Schnabel", "Isabel Schnabel"),
        "維勒魯瓦": ("維勒魯瓦", "Villeroy", "Villeroy de Galhau"),
        "帕內塔": ("帕內塔", "Panetta", "Fabio Panetta"),
        "斯圖納拉斯": ("Stournaras", "斯圖納拉斯"),
        "卡扎克斯": ("Kazaks", "卡扎克斯"),
        "納格爾": ("納格爾", "Nagel", "Joachim Nagel"),
        "雷恩": ("雷恩", "Rehn", "Olli Rehn"),
        "穆勒": ("穆勒", "Muller", "Müller"),
        "埃爾登森": ("埃爾登森", "Elderson", "Frank Elderson"),
        "霍爾茨曼": ("霍爾茨曼", "Holzmann"),
        "諾特": ("諾特", "Knot"),
    },
    "boe": {
        "貝利": ("貝利", "Bailey", "Andrew Bailey"),
        "皮爾": ("Huw Pill", "Pill", "皮爾"),
        "曼恩": ("曼恩", "Mann", "Catherine Mann"),
        "倫巴德利": ("倫巴德利", "Lombardelli", "Clare Lombardelli"),
        "蘭斯登": ("蘭斯登", "Ramsden", "Dave Ramsden"),
        "丁格拉": ("丁格拉", "Dhingra"),
        "格林": ("格林", "Greene", "Megan Greene"),
    },
    "boj": {
        "植田": ("植田", "植田和男", "Ueda", "Kazuo Ueda"),
        "內田": ("內田", "內田真一", "Uchida", "Shinichi Uchida"),
        "冰見野": ("冰見野", "冰見野良三", "Himino", "Ryozo Himino"),
        "田村": ("田村", "田村直樹", "Tamura", "Naoki Tamura"),
        "安達": ("安達", "安達誠司", "Adachi", "Seiji Adachi"),
        "中村": ("中村", "中村豊明", "Nakamura", "Toyoaki Nakamura"),
        "高田": ("高田", "高田創", "Takata", "Hajime Takata"),
        "淺田": ("淺田", "浅田", "浅田統一郎", "Asada", "Toichiro Asada"), 
        "佐藤": ("佐藤", "佐藤綾野", "Sato", "Ayano Sato"), 
        "增一行": ("增一行",),
        "野口": ("野口", "野口旭", "Noguchi", "Asahi Noguchi"), 
        "中川": ("中川", "中川順子", "Nakagawa", "Junko Nakagawa"), 
        "黑田": ("黑田", "黑田東彥", "Kuroda", "Haruhiko Kuroda"), 
    },
    "rba": {
        "布洛克": ("Bullock", "布洛克", "Michele Bullock"),
        "豪瑟": ("Hauser", "豪瑟", "Andrew Hauser"),
        "肯特": ("Kent", "肯特"),
        "杭特": ("Hunter", "杭特"),
    },
    "boc": {
        "麥克勒姆": ("Macklem", "麥克勒姆", "Tiff Macklem"),
        "羅傑斯": ("Rogers", "羅傑斯", "Carolyn Rogers"),
        "格拉維爾": ("Gravelle", "格拉維爾"),
    },
    "rbi": {
        "達斯": ("Das", "達斯", "Shaktikanta Das"),
        "馬爾霍特拉": ("Malhotra", "馬爾霍特拉"),
        "帕特拉": ("Patra", "帕特拉"),
    },
    "bcb": {
        "加里波羅": ("Galipolo", "加里波羅", "Gabriel Galípolo"),
        "坎波斯·內托": ("Campos Neto", "坎波斯·內托", "Roberto Campos Neto", "內托"),
    },
    "snb": {
        "施萊格爾": ("Schlegel", "施萊格爾", "Martin Schlegel"),
        "馬丁": ("Martin", "馬丁", "Antoine Martin"),
        "楚丁": ("Tschudin", "楚丁"),
        "喬丹": ("Thomas Jordan"),
    },
}

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


def fetch_mktnews_flash(pages: int = MKTNEWS_DEFAULT_PAGES, page_size: int = MKTNEWS_PAGE_LIMIT) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://mktnews.com/flash.html",
    }
    page_size = min(max(1, page_size), MKTNEWS_PAGE_LIMIT)
    results = []
    seen_ids = set()
    last_id = None
    for page in range(1, max(1, pages) + 1):
        params = {"limit": page_size}
        if last_id:
            params["last_id"] = last_id
        try:
            r = requests.get(MKTNEWS_FLASH_URL, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            if not isinstance(data, list):
                print(f"[WARN] MKT News：第 {page} 頁非預期回應格式 {type(data).__name__}")
                break
            if not data:
                break
            new_items = []
            for item in data:
                item_id = item.get("id") if isinstance(item, dict) else None
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    new_items.append(item)
            results.extend(new_items)
            last_item = data[-1] if isinstance(data[-1], dict) else {}
            next_last_id = last_item.get("id")
            if not next_last_id or next_last_id == last_id or len(data) < page_size:
                break
            last_id = next_last_id
        except Exception as e:
            print(f"[ERROR] MKT News 第 {page} 頁：{e}")
            break
    return results


def fetch_eastmoney_fastnews(slug: str, column_id: str, target: int) -> list[dict]:
    """東方財富快訊：分頁抓取直到累積 `target` 筆或 API 無更多資料。"""
    if target <= 0:
        return []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://kuaixun.eastmoney.com/{slug}.html",
    }
    collected: list[dict] = []
    seen: set[str] = set()
    sort_end = ""
    while len(collected) < target:
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": column_id,
            "sortEnd": sort_end,
            "pageSize": str(EASTMONEY_PAGE_SIZE),
            "req_trace": uuid.uuid4().hex,
        }
        payload = None
        for attempt in range(4):
            try:
                r = requests.get(EASTMONEY_API, params=params, headers=headers,
                                 timeout=(10, 45))
                r.raise_for_status()
                payload = r.json()
                break
            except (requests.Timeout, requests.ConnectionError, ValueError) as e:
                print(f"[WARN] 東方財富 {slug} 第 {len(collected)//EASTMONEY_PAGE_SIZE+1} 頁第 {attempt+1} 次失敗：{e}")
                time.sleep(1.5 * (attempt + 1))
        if payload is None:
            print(f"[ERROR] 東方財富 {slug}：多次重試失敗，提前結束")
            break
        if payload.get("code") != "1":
            print(f"[ERROR] 東方財富 {slug}：{payload.get('message')}")
            break
        data = payload.get("data") or {}
        items = data.get("fastNewsList") or []
        if not items:
            break
        for it in items:
            code = it.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            collected.append(it)
            if len(collected) >= target:
                break
        sort_end = data.get("sortEnd") or ""
        if not sort_end or len(collected) >= target:
            break
        time.sleep(0.4)
    return collected


def process_eastmoney_item(raw: dict, slug: str, category: str, sublabel: str) -> dict | None:
    code = raw.get("code")
    show_time = raw.get("showTime")
    if not code or not show_time:
        return None
    summary = clean_text(raw.get("summary") or "")
    title = clean_text(raw.get("title") or "")
    body = summary or title
    if not body:
        return None
    # 分類/子標籤已經是繁體標準形式，不再經 to_tw()，
    # 避免 opencc s2twp 把「經濟數據」本地化成「經濟資料」造成前端 chip 對不上。
    return {
        "id": f"em:{slug}:{code}",
        "time": show_time,
        "tags": [category, sublabel],
        "text": to_tw(body),
    }


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


TE_TIME_FIELDS = (
    "date",
    "Date",
    "datetime",
    "DateTime",
    "time",
    "Time",
    "timestamp",
    "Timestamp",
    "createdAt",
    "CreatedAt",
    "created_at",
    "lastUpdate",
    "LastUpdate",
    "last_updated",
)


def parse_te_time(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(TW).strftime("%Y-%m-%d %H:%M:%S")
    value = str(value).strip()
    if not value:
        return None
    if re.fullmatch(r"\d{10,13}", value):
        timestamp = int(value)
        timestamp = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(TW).strftime("%Y-%m-%d %H:%M:%S")
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW).strftime("%Y-%m-%d %H:%M:%S")


def extract_te_time(raw: dict) -> str | None:
    for field in TE_TIME_FIELDS:
        parsed = parse_te_time(raw.get(field))
        if parsed:
            return parsed
    return None


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

    item_time = extract_te_time(raw)
    if not item_time:
        print(f"[WARN] Trading Economics：略過無有效時間欄位的 item {stream_type}:{item_id}")
        return None

    return {
        "id": f"te:{stream_type}:{item_id}",
        "time": item_time,
        "tags": [to_tw(t) for t in tags],
        "text": to_tw(text),
    }


def flatten_mktnews_classification(raw: dict) -> list[str]:
    names = []
    for key in ("classification", "classify"):
        values = raw.get(key) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("name", ""))
            parent = clean_text(item.get("parent", ""))
            if parent and parent not in names:
                names.append(parent)
            if name and name not in names:
                names.append(name)
            child = item.get("child") or []
            if isinstance(child, list):
                for child_item in child:
                    if isinstance(child_item, dict):
                        child_name = clean_text(child_item.get("name", ""))
                        if child_name and child_name not in names:
                            names.append(child_name)
    return names


def format_mktnews_economic_text(data: dict) -> str:
    title = clean_text(data.get("title") or data.get("name") or "")
    country = clean_text(data.get("country") or "")
    actual = data.get("actual")
    previous = data.get("previous")
    consensus = data.get("consensus")
    unit = clean_text(data.get("unit") or "")
    pieces = []
    if country:
        pieces.append(country)
    if title:
        pieces.append(title)
    if actual not in (None, ""):
        pieces.append(f"actual {actual}{unit}")
    if consensus not in (None, ""):
        pieces.append(f"consensus {consensus}{unit}")
    if previous not in (None, ""):
        pieces.append(f"previous {previous}{unit}")
    return " | ".join(pieces)


def process_mktnews_flash_item(raw: dict) -> dict | None:
    item_id = raw.get("id")
    data = raw.get("data") or {}
    if not item_id or not isinstance(data, dict):
        return None

    item_time = parse_te_time(raw.get("time"))
    if not item_time:
        print(f"[WARN] MKT News：略過無有效時間欄位的 item {item_id}")
        return None

    title = clean_text(data.get("title") or "")
    content = clean_text(data.get("content") or "")
    if raw.get("type") == 1:
        text = format_mktnews_economic_text(data)
    else:
        text = title if not content or content == title else f"{title} — {content}" if title else content
    if not text:
        return None

    tags = ["MKT News"]
    if raw.get("type") == 1:
        tags.append("Economic Calendar")
    if raw.get("important"):
        tags.append("Important")
    if raw.get("hot"):
        tags.append("Hot")

    for name in flatten_mktnews_classification(raw):
        if name not in tags:
            tags.append(name)

    impacts = raw.get("impact") or []
    if isinstance(impacts, list):
        for impact in impacts:
            if not isinstance(impact, dict):
                continue
            symbol = clean_text(impact.get("symbol") or "")
            direction = clean_text(impact.get("impact") or "")
            if symbol and symbol not in tags:
                tags.append(symbol)
            if direction and direction != "none":
                impact_tag = f"Impact {direction}"
                if impact_tag not in tags:
                    tags.append(impact_tag)

    return {
        "id": f"mkt:{item_id}",
        "time": item_time,
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


def normalize_export_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    item_id = str(item.get("id", "")).strip()
    item_time = str(item.get("time", "")).strip()
    text = str(item.get("text", "")).strip()
    tags = item.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not item_id or not item_time or not text or not isinstance(tags, list):
        return None
    return {"id": item_id, "time": item_time, "tags": tags, "text": text}


def load_export_items(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"[WARN] {os.path.relpath(path, os.path.dirname(DOCS_DIR))} 讀取失敗，略過：{e}")
        return []

    items = []
    for item in payload.get("items", []):
        normalized = normalize_export_item(item)
        if normalized:
            items.append(normalized)
    return items


def archive_path_for_date(date_text: str) -> str:
    year, month = date_text[:4], date_text[5:7]
    return os.path.join(ARCHIVE_DIR, year, month, f"{date_text}.json")


def archive_dates_to_seed(days: int) -> list[str]:
    today = datetime.now(TW).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(max(0, days))]


def seed_news_db_from_export(conn) -> int:
    items = load_export_items(os.path.join(DOCS_DIR, "data.json"))
    for date_text in archive_dates_to_seed(requested_news_retention_days()):
        items.extend(load_export_items(archive_path_for_date(date_text)))

    imported = upsert_items(conn, items)
    if imported:
        print(f"[DB] 從 docs/data.json / archive 補入 {imported} 筆既有快訊")
    return imported


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


def news_item_date(item: dict) -> str | None:
    time_text = str(item.get("time", "")).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", time_text):
        return time_text[:10]
    return None


def sorted_news_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (str(item.get("time", "")), str(item.get("id", ""))),
        reverse=True,
    )


def save_json_if_changed(path: str, payload: dict) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == text:
                    return False
        except Exception:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def export_daily_news_archives(conn) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT id, time, tags, text FROM news ORDER BY time DESC",
    ).fetchall()
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        item = {"id": row[0], "time": row[1], "tags": json.loads(row[2]), "text": row[3]}
        date_text = news_item_date(item)
        if not date_text:
            continue
        grouped.setdefault(date_text, {})[item["id"]] = item

    written = 0
    for date_text, by_id in grouped.items():
        path = archive_path_for_date(date_text)
        for item in load_export_items(path):
            by_id.setdefault(item["id"], item)
        items = sorted_news_items(list(by_id.values()))
        payload = {"date": date_text, "count": len(items), "items": items}
        if save_json_if_changed(path, payload):
            written += 1

    if grouped:
        print(f"[ARCHIVE] 每日快訊封存 {len(grouped)} 天，更新 {written} 個檔案")
    return len(grouped), written


def requested_news_retention_days() -> int:
    days = NEWS_DB_RETENTION_DAYS
    for env_name in ("AI_SCORE_BACKFILL_DAYS", "CENTRAL_BANK_BACKFILL_DAYS"):
        raw = os.getenv(env_name, "").strip()
        if not raw:
            continue
        try:
            days = max(days, int(float(raw)))
        except ValueError:
            pass
    return max(EXPORT_DAYS, days)


def database_path(conn) -> str:
    rows = conn.execute("PRAGMA database_list").fetchall()
    for _, name, path in rows:
        if name == "main" and path:
            return path
    return DB_PATH


def compact_news_db(conn, retention_days: int) -> tuple[int, int, int]:
    cutoff = (datetime.now(TW) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("DELETE FROM news WHERE time < ?", (cutoff,))
    removed = max(0, cur.rowcount)
    conn.commit()
    path = database_path(conn)
    before_size = os.path.getsize(path) if os.path.exists(path) else 0
    conn.execute("VACUUM")
    conn.commit()
    after_size = os.path.getsize(path) if os.path.exists(path) else 0
    return removed, before_size, after_size


def maintain_news_db(conn) -> None:
    retention_days = requested_news_retention_days()
    removed, before_size, after_size = compact_news_db(conn, retention_days)
    print(
        f"[DB] news.db 保留 {retention_days} 天；刪除 {removed} 筆；"
        f"VACUUM {before_size / 1024 / 1024:.1f}MB -> {after_size / 1024 / 1024:.1f}MB"
    )

    max_bytes = NEWS_DB_MAX_MB * 1024 * 1024
    if after_size <= max_bytes or retention_days <= EXPORT_DAYS:
        return

    removed, before_size, after_size = compact_news_db(conn, EXPORT_DAYS)
    print(
        f"[DB] news.db 仍超過 {NEWS_DB_MAX_MB}MB，改保留 {EXPORT_DAYS} 天；"
        f"刪除 {removed} 筆；VACUUM {before_size / 1024 / 1024:.1f}MB -> {after_size / 1024 / 1024:.1f}MB"
    )


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


def prune_ai_summaries_since(cutoff_text: str) -> int:
    data = load_ai_summaries()
    before = len(data.get("items", []))
    data["items"] = [
        item for item in data.get("items", [])
        if (item.get("slot_end") or item.get("slot_start") or "") < cutoff_text
    ]
    removed = before - len(data["items"])
    if removed:
        save_ai_summaries(data)
    return removed


def clear_recent_ai_summaries_if_requested() -> None:
    raw_days = os.getenv("CLEAR_RECENT_SUMMARY_DAYS", "").strip()
    if not raw_days:
        return

    try:
        clear_days = float(raw_days)
    except ValueError:
        print(f"[ERROR] CLEAR_RECENT_SUMMARY_DAYS 格式錯誤（需為正數）：{raw_days}，略過清理")
        return

    if clear_days <= 0:
        print(f"[ERROR] CLEAR_RECENT_SUMMARY_DAYS 必須大於 0：{raw_days}，略過清理")
        return

    cutoff = datetime.now(TW) - timedelta(days=clear_days)
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    summary_removed = prune_ai_summaries_since(cutoff_text)
    print(
        f"[CLEANUP] 已清除最近 {clear_days:g} 天 AI 摘要 "
        f"{summary_removed} 筆（slot_end >= {cutoff_text}）"
    )


SUMMARY_PRIORITY_KEYWORDS = (
    # S: macro data
    "CPI", "PCE", "PMI", "GDP", "nonfarm", "payroll", "JOLTS", "retail sales",
    "inflation", "unemployment", "jobless", "employment", "trade deficit",
    "trade surplus", "exports", "imports", "consumer confidence",
    "通膨", "通脹", "通貨膨脹", "非農", "就業", "失業", "零售銷售",
    "貿易逆差", "貿易順差", "出口", "進口", "消費者信心",
    # A: policy
    "Fed", "Federal Reserve", "ECB", "BOJ", "BOE", "PBOC", "central bank",
    "interest rate", "rate decision", "tariff", "sanction", "fiscal",
    "央行", "美聯儲", "聯準會", "日本央行", "歐洲央行", "英國央行",
    "利率", "降準", "關稅", "制裁", "財政",
    # B: globally relevant companies
    "Nvidia", "NVDA", "Apple", "Microsoft", "Alphabet", "Google", "Amazon",
    "Meta", "Tesla", "TSMC", "ASML", "AMD", "Arm", "OpenAI", "earnings",
    "revenue", "guidance", "財報", "營收", "指引", "獲利",
)

SUMMARY_LOW_VALUE_KEYWORDS = (
    "券商", "策略", "配置", "研報", "建議投資者關注", "主力合約", "夜盤",
    "酒價", "註冊資本", "成立新", "股份註銷",
)


def summary_priority_score(row) -> int:
    item_id, item_time, tags_json, text = row
    tags = json.loads(tags_json)
    tag_text = " ".join(tags)
    full_text = f"{tag_text} {text}"
    score = 0

    if any(keyword.lower() in full_text.lower() for keyword in SUMMARY_PRIORITY_KEYWORDS):
        score += 100
    if "Importance 3" in tags:
        score += 80
    elif "Importance 2" in tags:
        score += 45
    elif "Importance 1" in tags:
        score += 15

    if "Economy" in tags:
        score += 35
    if any(tag in tags for tag in ("央行", "宏觀", "國際", "公司")):
        score += 20
    if "Markets" in tags:
        score -= 20
    if item_id.startswith("te:markets:"):
        score -= 35
    if any(keyword in full_text for keyword in SUMMARY_LOW_VALUE_KEYWORDS):
        score -= 60

    return score


def recent_news_for_summary(conn, start: datetime, end: datetime, limit: int = 330) -> list[dict]:
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    te_limit = min(40, limit)
    te_rows = conn.execute(
        """
        SELECT id, time, tags, text
        FROM news
        WHERE time >= ? AND time < ? AND id LIKE 'te:%'
        ORDER BY time DESC
        LIMIT ?
        """,
        (start_text, end_text, te_limit),
    ).fetchall()

    remaining_limit = max(0, limit - len(te_rows))
    other_rows = conn.execute(
        """
        SELECT id, time, tags, text
        FROM news
        WHERE time >= ? AND time < ? AND id NOT LIKE 'te:%'
        ORDER BY time DESC
        LIMIT ?
        """,
        (start_text, end_text, remaining_limit),
    ).fetchall()

    rows = sorted([*te_rows, *other_rows], key=lambda r: (summary_priority_score(r), r[1]), reverse=True)[:limit]
    return [{"id": r[0], "time": r[1], "tags": json.loads(r[2]), "text": r[3]} for r in rows]


def compact_te_market_text(text: str) -> str:
    title, sep, desc = text.partition(" — ")
    if not sep:
        return title[:220]

    first_sentence = re.split(r"(?<=[.!?])\s+", desc, maxsplit=1)[0]
    background_markers = (
        " after ",
        " following ",
        " as ",
        " while ",
        " amid ",
        " due to ",
        " on hopes ",
        " on expectations ",
    )
    background_keywords = (
        "Reserve Bank",
        "central bank",
        "rate",
        "hike",
        "cut",
        "PMI",
        "GDP",
        "inflation",
        "earnings",
        "forecast",
        "expectations",
        "data",
        "report",
    )
    lowered = first_sentence.lower()
    cut_at = None
    for marker in background_markers:
        idx = lowered.find(marker)
        if idx >= 0 and any(keyword.lower() in lowered[idx:] for keyword in background_keywords):
            cut_at = idx if cut_at is None else min(cut_at, idx)
    if cut_at is not None:
        first_sentence = first_sentence[:cut_at].rstrip(" ,;")

    return f"{title} — {first_sentence}"[:220]


def compact_news_input(items: list[dict], max_chars: int = 260) -> str:
    lines = []
    for item in items:
        item_tags = item.get("tags", [])
        tags = ",".join(item_tags[:5])
        text = clean_text(item.get("text", ""))[:max_chars]
        if "Trading Economics" in item_tags and "Markets" in item_tags:
            text = compact_te_market_text(text)
            text = f"【TE市場行情，只保留本時段價格反應；已移除央行/數據/財報背景，禁止推回政策或數據新事件】{text}"
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


def format_market_context_date(value) -> str:
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(TW)
            return value.strftime("%Y-%m-%d")
    except Exception:
        pass
    return str(value)[:10]


def market_index_date(value) -> object:
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(ET)
            return value.date()
    except Exception:
        pass
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def market_context_cutoff_date(reference_time: datetime):
    """Return the latest exchange-date that can be used as context for this slot."""

    ref_et = reference_time.astimezone(ET)
    cutoff = ref_et.date()
    weekday = ref_et.weekday()

    # US equity/futures weekend pause: Friday evening through Sunday 18:00 ET.
    if weekday == 5:
        cutoff -= timedelta(days=1)
    elif weekday == 6 and ref_et.hour <= 18:
        cutoff -= timedelta(days=2)
    elif weekday == 4 and ref_et.hour >= 17:
        # After Friday's futures pause starts, the last usable session is Friday.
        cutoff = ref_et.date()

    return cutoff


def filter_market_history_for_slot(series, reference_time: datetime):
    cutoff = market_context_cutoff_date(reference_time)
    try:
        return series[[market_index_date(idx) is not None and market_index_date(idx) <= cutoff for idx in series.index]]
    except Exception:
        return series


def is_market_context_stale(latest_index, reference_time: datetime) -> bool:
    latest_date = market_index_date(latest_index)
    cutoff = market_context_cutoff_date(reference_time)
    if latest_date is None:
        return True
    return latest_date < cutoff


def is_us_weekend_market_closure(start: datetime, end: datetime) -> bool:
    def in_closure(value: datetime) -> bool:
        value_et = value.astimezone(ET)
        weekday = value_et.weekday()
        return (
            (weekday == 4 and value_et.hour >= 17)
            or weekday == 5
            or (weekday == 6 and value_et.hour < 18)
        )

    probe = start
    while probe < end:
        if not in_closure(probe):
            return False
        probe += timedelta(minutes=30)
    return in_closure(end - timedelta(seconds=1))


def market_session_note(start: datetime, end: datetime) -> str:
    if not is_us_weekend_market_closure(start, end):
        return "- 本摘要區間未完全落在美股週末休市時段，仍須依快訊與市場背景的新鮮度描述。"

    start_et = start.astimezone(ET).strftime("%Y-%m-%d %H:%M")
    end_et = end.astimezone(ET).strftime("%Y-%m-%d %H:%M")
    return (
        f"- 本摘要區間換算美東時間為 {start_et} 至 {end_et}，"
        "完全落在美股與主要美股期貨週末休市期間；不得寫美股/科技股在近 3 小時"
        "維持高檔、上漲、震盪、創高或回落。若引用市場價格背景，只能寫成"
        "前一交易日/最近可得價格仍在高檔或低檔的背景。"
    )


def market_position_note(current: float, closes_6m, closes_long) -> str:
    """回傳市場位置狀態；內部看長短期高低點，輸出文字保持簡潔。"""

    if closes_long is not None and not closes_long.empty:
        high_all = float(closes_long.max())
        all_gap = pct_change(current, high_all)
        if all_gap is not None and all_gap >= -0.25:
            return "，創/接近歷史新高"

        low_all = float(closes_long.min())
        all_low_gap = pct_change(current, low_all)
        if all_low_gap is not None and all_low_gap <= 0.25:
            return "，創/接近歷史新低"

        high_52w = float(closes_long.tail(min(len(closes_long), 252)).max())
        high_52w_gap = pct_change(current, high_52w)
        if high_52w_gap is not None and high_52w_gap >= -0.5:
            return "，高檔震盪"

        low_52w = float(closes_long.tail(min(len(closes_long), 252)).min())
        low_52w_gap = pct_change(current, low_52w)
        if low_52w_gap is not None and low_52w_gap <= 0.5:
            return "，低檔震盪"

    high_3m = float(closes_6m.tail(min(len(closes_6m), 64)).max())
    high_3m_gap = pct_change(current, high_3m)
    if high_3m_gap is not None and high_3m_gap >= -1:
        return "，維持近期高點"

    low_3m = float(closes_6m.tail(min(len(closes_6m), 64)).min())
    low_3m_gap = pct_change(current, low_3m)
    if low_3m_gap is not None and low_3m_gap <= 1:
        return "，維持近期低點"

    return ""


def fetch_ai_market_context(reference_time: datetime | None = None) -> str:
    """抓 AI 摘要專用市場背景，避免標題只被新聞風險詞帶偏。"""
    if not HAS_YF:
        return ""

    reference_time = reference_time or datetime.now(TW)
    lines = []
    for item in AI_MARKET_CONTEXT_SYMBOLS:
        symbol = item["symbol"]
        name = item["name"]
        kind = item["kind"]
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="6mo", interval="1d", auto_adjust=True)
            if hist.empty or "Close" not in hist:
                continue

            closes = filter_market_history_for_slot(hist["Close"].dropna(), reference_time)
            if closes.empty:
                continue

            long_hist = ticker.history(period="max", interval="1d", auto_adjust=True)
            closes_long = (
                filter_market_history_for_slot(long_hist["Close"].dropna(), reference_time)
                if not long_hist.empty and "Close" in long_hist
                else None
            )

            latest_index = closes.index[-1]
            latest_date = format_market_context_date(latest_index)
            stale = is_market_context_stale(latest_index, reference_time)
            current = float(closes.iloc[-1])
            prev_1m = float(closes.iloc[-22]) if len(closes) >= 22 else None
            prev_3m = float(closes.iloc[-64]) if len(closes) >= 64 else None

            one_month = pct_change(current, prev_1m)
            three_month = pct_change(current, prev_3m)
            position_note = market_position_note(current, closes, closes_long)

            if kind == "yield":
                one_month_text = f"{(current - prev_1m) * 100:+.0f}bp" if prev_1m is not None else "n/a"
                three_month_text = f"{(current - prev_3m) * 100:+.0f}bp" if prev_3m is not None else "n/a"
            else:
                one_month_text = format_pct(one_month)
                three_month_text = format_pct(three_month)

            freshness_note = (
                f"最近可得收盤/結算價（資料日期 {latest_date}，不代表摘要時段內交易）"
                if stale
                else f"盤中/最新價（資料日期 {latest_date}）"
            )
            lines.append(
                f"- {name}：{freshness_note} {format_market_price(current, kind)}，"
                f"近 1 個月 {one_month_text}，近 3 個月 {three_month_text}{position_note}"
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
    session_note = market_session_note(start, end)
    prompt = f"""
你是財經新聞編輯。請根據以下 {len(items)} 則近 3 小時快訊，產生繁體中文（台灣）AI 摘要分析。
時間區間：{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')}（台灣時間）

【交易時段檢查】
{session_note}

【市場價格背景（只用於校準情緒，不是事件來源）】
以下為主要資產價格與中期表現，只能用來校準市場情緒，不是近 3 小時新聞事件來源。
若標示「最近可得收盤/結算價」，代表該標的在摘要時段內可能休市或資料未更新，只能寫成前一交易日/最近可得水準，絕對不可寫成「近 3 小時上漲、盤中攀升、最新大漲、持續創高」。
近 1 個月、近 3 個月漲跌幅是中期表現，絕對不可改寫成日內漲跌、近 3 小時漲跌或盤中走勢。
不得把市場價格背景拆成獨立 events；events 必須來自下方快訊。
{market_context or "- 本次未取得市場價格背景，請只根據快訊中明確價格資料判斷。"}

規則：
- 若 S&P 500 / Nasdaq 維持高檔或近 1~3 個月上漲，summary_title 不得只寫「風險升溫」「地緣緊張」「避險升溫」等單邊負面標題。
- 標題必須同時反映「風險資產價格狀態」與「新聞風險事件」。例如：股市高檔震盪、風險資產守高、油金波動牽動盤面。
- 若新聞風險很多，但股市、信用或其他風險資產仍強，請寫成「市場消化風險」或「高檔震盪」，不要寫成全面 risk-off。
- 若快訊或標示「盤中/最新價」的市場價格背景明確顯示主要股指「創新高 / 歷史新高」，必須使用「創新高」或「歷史高點」等相同強度描述，不得降格改寫成「接近三個月高點」。
- 若只有標示「最近可得收盤/結算價」的市場價格背景顯示高點，只能描述成「最近可得價格仍在高檔」，不得寫成摘要時段內創高或上漲。
- 若快訊或標示「盤中/最新價」的市場價格背景明確顯示主要股指「創新高 / 歷史新高」，摘要內容要偏向非常樂觀的情緒去寫，不要強調太多風險。
- 若市場價格背景寫「高檔震盪」，可描述為股市高檔震盪或風險資產守高，不要寫出「52 週」。
- 若市場價格背景寫「維持近期高點」，可描述為股市維持近期高點，不要寫出「三個月」或「52 週」。
- 若快訊或標示「盤中/最新價」的市場價格背景明確顯示主要股指「創新低 / 歷史新低」，必須使用「創新低」或「歷史低點」等相同強度描述。
- 若市場價格背景寫「低檔震盪」，可描述為低檔震盪，不要寫出「52 週」。
- 若市場價格背景寫「維持近期低點」，可描述為維持近期低點，不要寫出「三個月」或「52 週」。
- C 級市場行情事件只能作為收尾補充，最多 1~2 則；若已選出 5 則以上 S/A/B 事件，C 級不得超過 1 則。

【篩選與排序原則（內部使用，請勿輸出等級標示）】
請依下列重要程度由高到低，從快訊中挑出最具市場參考價值的事件（5~10 則），順序由最重要排到次重要：
- 本 prompt 所稱「主要經濟體」與「重要經濟體」一律指以下：阿根廷、澳洲、加拿大、中國、法國、德國、印度、印尼、越南、義大利、日本、墨西哥、俄羅斯、沙烏地阿拉伯、南韓、土耳其、英國、美國、歐盟、台灣。請用此範圍判斷資料重要性。
- 排除中國券商所有研報、策略、配置建議與市場評論，例如中信證券、中國銀河證券、銀河證券、中信建投、中信建投證券、華泰證券、天風證券、東方證券、廣發證券、國盛證券、東吳證券、開源證券、華西證券、招商證券、興業證券、國海證券、國金證券、中泰證券、信達證券、申萬宏源證券、海通證券、國泰君安、光大證券、中金公司等提出的市場策略，不得納入摘要事件。
- 只排除中國券商的觀點型內容；不要因此排除 SEC、倫敦證券交易所、東京證券交易所、印度證券交易委員會等海外監管機構或交易所公告。
- 央行官員發言屬於重要政策/數據訊號，尤其是 Fed、ECB、BOJ、BOE、PBOC 及上述主要經濟體央行官員對利率、通膨、就業、匯率與金融穩定的表態。
- 事件必須以本時間區間內實際發生、公布或更新的內容為主；若快訊提到「前一日或更早」的政策/數據/財報，只能當作背景，不得把舊事件寫成該時段新事件。
- Trading Economics 的市場行情文章若只是「引用」先前央行決議、經濟數據或財報作為價格變動背景，event title 必須聚焦「本時段的價格/市場反應」，不可改寫成「央行宣布升息」或「數據公布」。
- Trading Economics 的 Economy 文章若是當下公布的 GDP、PMI、CPI、貿易、就業、央行紀要或利率訊號，應依 S/A 級優先處理，不要被 Markets 行情文蓋掉。
- 不得把兩個不同事件硬合併成同一個 event。例：澳洲燃料儲備政策與澳元走勢可各自成為獨立事件；若澳元走勢文中引用 RBA 先前升息，只能寫「澳元因升息背景與風險偏好走強」，不得寫成「澳洲央行宣布升息與燃料儲備政策」。
- 若同一時段有大量重要經濟數據，摘要與 events 可高度集中在經濟數據；即使約 70% 內容都是經濟數據也可以。
- 市場行情類事件請整合股市、匯市、債市、原物料與加密貨幣表現，必須合併成 1~2 則，不要拆成零散多則；不得讓行情事件佔 events 過半。



S 級（最優先）：主要經濟體最新數據公佈
   例：美國 4 月 CPI 創一年新高、非農就業、歐元區 GDP、中國 PMI、PCE、零售銷售、JOLTS。

A 級：主要經濟體政策公布
   例：川普宣布對歐洲關稅、澳洲央行升息、Fed 利率決議、財政部新政、人行降準。

B 級：全球重要公司消息（限具全球影響力之大型企業）
   例：ASML Q1 營收高於預期、NVDA 財報前瞻、OpenAI 延後 IPO、Apple、TSMC、Microsoft。

C 級：全球市場行情摘要整理
    全球股市、匯市、債市、原物料、加密貨幣等主要資產行情整理。只作為背景補充，不可壓過 S/A/B。不要分開寫很多則，必須合併寫在 1~2 則。

D 級：地緣政治
   例：美伊停火談判延後


【請過濾掉以下垃圾級內容，不得納入 10 則事件】
- 中國、香港中小型公司個股消息（如：長和註銷 Vodafone Three 股份、江西銅業 (00358.HK) 跌超 4.5%）。
- 中國期貨市場價格變動（如：滬鎳主力合約跌、鐵礦石期貨夜盤收紅）。

【summary 寫法參考 — 請改寫成近 3 小時，不是週報】
- summary 要像全球行情總評，先用一句話交代近 3 小時市場主線，例如：科技財報、央行決策、重要經濟數據、地緣局勢、油價/金價/美元/美債殖利率變化如何牽動風險偏好。
- 接著用同一段串起股、匯、債、原物料與主要區域市場表現：美股/科技股、歐股、亞股、美元指數、美債 10 年期殖利率、黃金、WTI/Brent 原油；有明確數字就寫數字，沒有就簡短描述方向。
- 寫法要接近：「近 3 小時市場聚焦 ___，美股/科技股 ___，歐亞股 ___；債匯方面，美元 ___、10 年期美債殖利率 ___；原物料方面，油價 ___、金價 ___，反映 ___。」
- 若主要股指創新高或維持高檔，summary 要把股市高檔與風險偏好放在前面，再補充油金、美元、美債或地緣風險，不要把風險事件寫成主旋律。
- 若近 3 小時缺少某類資產資料，不要硬湊；市場價格背景只能補充風險偏好狀態，不得改寫成摘要時段內行情。
- 不要寫成 events 的條列縮寫，也不要逐句重複「某某宣布、某某表示」。summary 應該是一段流暢的總評。

輸出要求：
1. summary_title：一句 15 字以內標題。
2. summary：150~250 字以內總摘要。
3. risk_on_score：0~10 分數，10 代表市場完全 risk on，0 代表市場完全 risk off。
4. fundamental_surprise_score：0~10 分數，10 代表經濟數據與財報全面優於預期，0 代表全面低於預期。
5. score_commentary：一句 60 字以內說明兩個分數的主要依據。
6. events：5~10 個重點事件，依該時段實際重要事件多寡決定數量，依重要程度由高到低排序（S→A→B→C→D）。若快訊中存在任何 S/A/B 候選，前 3 則 events 必須優先選 S/A/B，不得用 C 級行情開場。
7. 每個事件包含 title、explanation、impact。
8. 每個 explanation 約 300~450 字，必須包含「發生了什麼」、「為何重要」、「對市場情緒、資產價格、經濟基本面或企業獲利的含意」。不要只用一兩句話轉述價格。
9. **請勿在任何欄位輸出「S級 / A 級 / B 級 / C 級 / D 級」等分級標示**，分級僅作為內部篩選依據。
10. 不要編造事實；若訊息不足，寧可寫得簡短或減少事件數量，也不要硬湊。
11. 清淡時段寧可只給 5 則高質量事件，也不要為了湊數納入垃圾級內容。
12. 如果 S、A、B 級的經濟數據很多，70%以上的 events 都是 S/A/B 也沒關係；相反地，C 級行情不得超過 2 則。
13. 若快訊或標示「盤中/最新價」的市場價格背景明確顯示主要股指「創新高 / 歷史新高」，摘要內容要偏向非常樂觀的情緒去寫，不要強調太多風險；若只是「最近可得收盤/結算價」，只能當成中期背景。

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
- 只有同一主體、同一事件鏈的多個時點可以整合；不同政策、不同資產、不同發布者不得為了省 events 數量而合併。
- 每個 events.explanation 要寫成完整分析段落：先交代核心事實，再說明它代表的市場/政策/基本面訊號，最後補上對相關資產或風險偏好的影響。不可只寫成短版新聞摘要。
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
        "max_output_tokens": 5200,
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

    # ── Backfill 模式 ──
    # 平常排程會往回看最近幾個 3 小時時段，已存在的摘要會跳過；
    # 這樣手動刪除雲端某一筆摘要後，下一輪排程可自動補回。
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
            target_slots = 3
    else:
        try:
            target_slots = max(1, int(os.getenv("AI_LOOKBACK_SLOTS", "3")))
        except ValueError:
            target_slots = 3

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
                    market_context = fetch_ai_market_context(slot_end)
                    if market_context:
                        print(f"[AI] {slot_id} 已取得摘要用市場價格背景（截至 {slot_end.strftime('%Y-%m-%d %H:%M')}）")
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
# 央行摘要
# ════════════════════════════
def load_central_bank_summaries() -> dict:
    if not os.path.exists(CENTRAL_BANK_SUMMARY_PATH):
        return {"updated": "", "items": []}
    try:
        with open(CENTRAL_BANK_SUMMARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data
    except Exception as e:
        print(f"[WARN] central_bank_summaries.json 讀取失敗：{e}")
        return {"updated": "", "items": []}


def save_central_bank_summaries(data: dict) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    data["items"] = sorted(data.get("items", []), key=lambda x: x.get("date", ""), reverse=True)[:21]
    data["updated"] = datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")
    with open(CENTRAL_BANK_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def central_bank_keywords() -> list[str]:
    keywords = []
    seen = set()
    for bank in CENTRAL_BANKS:
        for keyword in bank["keywords"]:
            if keyword not in seen:
                keywords.append(keyword)
                seen.add(keyword)
    return keywords


def central_bank_news_for_day(conn, start: datetime, end: datetime, limit: int = 320) -> list[dict]:
    start_text = start.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end.strftime("%Y-%m-%d %H:%M:%S")
    keywords = central_bank_keywords()
    clauses = " OR ".join(["text LIKE ?"] * len(keywords))
    params = [start_text, end_text, *[f"%{keyword}%" for keyword in keywords], limit]
    rows = conn.execute(
        f"""
        SELECT id, time, tags, text
        FROM news
        WHERE time >= ? AND time < ? AND ({clauses})
        ORDER BY time DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [{"id": r[0], "time": r[1], "tags": json.loads(r[2]), "text": r[3]} for r in rows]


def central_bank_alias_matches(alias: str, text: str) -> bool:
    if not alias or not text:
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", alias):
        pattern = rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return alias in text


def central_bank_official_has_direct_attribution(text: str, aliases: tuple[str, ...]) -> bool:
    """Return true only when the item attributes a remark/event to the official."""
    direct_verbs = (
        "表示", "稱", "指出", "認為", "強調", "警告", "重申", "補充", "提到",
        "談及", "發言", "講話", "演講", "作證", "接受採訪", "致詞", "宣誓就任",
    )
    upcoming_markers = ("將", "將於", "預計", "料", "可能", "等待", "關注", "提醒")
    for alias in aliases:
        if not central_bank_alias_matches(alias, text):
            continue
        if re.search(rf"{re.escape(alias)}\s*[:：]", text, flags=re.IGNORECASE):
            return True
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", alias):
            if re.search(
                rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])[^.\n]{{0,80}}\b"
                r"(says|said|remarks|speaks|testifies|warns|stresses|notes)\b",
                text,
                flags=re.IGNORECASE,
            ):
                return True
        alias_pos = text.find(alias)
        if alias_pos < 0:
            continue
        context = text[alias_pos:alias_pos + 80]
        if any(marker in context[:12] for marker in upcoming_markers) and not any(verb in context for verb in direct_verbs):
            continue
        if any(verb in context for verb in direct_verbs):
            return True
    return False


def central_bank_required_officials(items: list[dict]) -> dict[str, list[str]]:
    required: dict[str, list[str]] = {}
    for item in items:
        text = clean_text(item.get("text", ""))
        if not text:
            continue
        for bank_id, aliases_by_name in CENTRAL_BANK_OFFICIAL_ALIASES.items():
            for official, aliases in aliases_by_name.items():
                if central_bank_official_has_direct_attribution(text, aliases):
                    required.setdefault(bank_id, [])
                    if official not in required[bank_id]:
                        required[bank_id].append(official)
    return required


def format_required_officials(required: dict[str, list[str]]) -> str:
    if not required:
        return "- 未辨識到明確官員名單；仍需依資料逐位整理。"
    lines = []
    bank_names = {bank["id"]: bank["name"] for bank in CENTRAL_BANKS}
    for bank_id, officials in required.items():
        lines.append(f"- {bank_names.get(bank_id, bank_id)}：{'、'.join(officials)}")
    return "\n".join(lines)


def official_item_text(item) -> str:
    if isinstance(item, dict):
        details = " ".join(
            f"{detail.get('title', '')} {detail.get('text', '')}"
            for detail in item.get("details", [])
            if isinstance(detail, dict)
        )
        return " ".join(str(item.get(key, "")) for key in ("official", "venue", "analysis")) + " " + details
    return str(item or "")


def central_bank_official_owner(official: str) -> str | None:
    official = str(official or "").strip()
    if not official:
        return None
    for bank_id, aliases_by_name in CENTRAL_BANK_OFFICIAL_ALIASES.items():
        for canonical, aliases in aliases_by_name.items():
            if official == canonical or any(central_bank_alias_matches(alias, official) for alias in aliases):
                return bank_id
    return None


def sanitize_central_bank_officials(result: dict) -> dict:
    for bank in result.get("central_banks", []):
        bank_id = str(bank.get("id", "")).lower()
        officials = []
        for item in bank.get("officials") or []:
            official = item.get("official", "") if isinstance(item, dict) else ""
            owner = central_bank_official_owner(official)
            if owner and owner != bank_id:
                print(f"[WARN] 移除錯放央行官員：{bank.get('name', bank_id)} -> {official}")
                continue
            officials.append(item)
        bank["officials"] = officials
    return result


def central_bank_official_issues(
    result: dict,
    required: dict[str, list[str]],
    min_chars: int = 300,
    min_detail_points: int = 4,
    include_missing: bool = True,
) -> list[str]:
    issues = []
    for bank in result.get("central_banks", []):
        release = bank.get("policy_release") or {}
        if release.get("has_release"):
            continue
        bank_id = str(bank.get("id", "")).lower()
        for idx, item in enumerate(bank.get("officials") or [], start=1):
            if isinstance(item, dict):
                official = str(item.get("official", "")).strip()
                details = item.get("details") or []
                detail_text = " ".join(
                    f"{detail.get('title', '')} {detail.get('text', '')}"
                    for detail in details
                    if isinstance(detail, dict)
                )
                text = f"{item.get('analysis', '')} {detail_text}"
                if any(marker in official for marker in ("、", "/", "與", "和", ",")):
                    issues.append(f"{bank.get('name', bank.get('id', '央行'))} officials 第 {idx} 點混入多位官員：{official}")
                if len(details) < min_detail_points:
                    issues.append(f"{bank.get('name', bank.get('id', '央行'))} officials 第 {idx} 點只有 {len(details)} 個細項")
                for detail_idx, detail in enumerate(details, start=1):
                    if not isinstance(detail, dict):
                        issues.append(f"{bank.get('name', bank.get('id', '央行'))} officials 第 {idx} 點第 {detail_idx} 個細項格式錯誤")
                        continue
                    detail_compact = re.sub(r"\s+", "", f"{detail.get('title', '')}{detail.get('text', '')}")
                    if len(detail_compact) < 20:
                        issues.append(f"{bank.get('name', bank.get('id', '央行'))} officials 第 {idx} 點第 {detail_idx} 個細項太短")
            else:
                text = item
            compact = re.sub(r"\s+", "", str(text or ""))
            if compact and len(compact) < min_chars:
                issues.append(f"{bank.get('name', bank.get('id', '央行'))} officials 第 {idx} 點只有 {len(compact)} 字")

        if include_missing:
            rendered_officials = "\n".join(official_item_text(item) for item in bank.get("officials") or [])
            for official in required.get(bank_id, []):
                aliases = CENTRAL_BANK_OFFICIAL_ALIASES.get(bank_id, {}).get(official, (official,))
                if not any(central_bank_alias_matches(alias, rendered_officials) for alias in aliases):
                    issues.append(f"{bank.get('name', bank.get('id', '央行'))} 缺少官員：{official}")
    return issues


def call_openai_central_bank_summary(items: list[dict], start: datetime, end: datetime) -> dict:
    required_officials = central_bank_required_officials(items)
    prompt = f"""
你是央行政策與利率市場分析師。請只根據下方資料庫快訊，整理繁體中文（台灣）「央行摘要」。
摘要時間：GMT+8 每日上午 07:00。
資料範圍：{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')}（台灣時間，24 小時窗口）

必須覆蓋以下央行，順序不可改：
1. 美國聯準會 Fed
2. 歐洲央行 ECB
3. 英國央行 BoE
4. 日本央行 BoJ
5. 澳洲聯儲 RBA
6. 加拿大央行 BoC
7. 印度央行 RBI
8. 巴西央行 BCB
9. 瑞士央行 SNB

可辨識到「本人直接發言或正式出席」的官員名單：
{format_required_officials(required_officials)}

上述名單只包含資料中有直接歸因的官員。每一位若屬於當日發言、受訪、演講、作證、政策評論、致詞或人事交接，才放入該央行 officials，且各自成為獨立物件；不得省略，不得合併成同一點。若資料只是由總統、財長、白宮顧問、分析師、媒體或市場行情文章「提到」某位央行官員，不得把該官員寫成本人發言。

═══════════════════════════════
事實核對與反幻覺規則
═══════════════════════════════
- 嚴禁杜撰日期、場合、會議名稱、演講題目或主辦單位。資料庫快訊的 time 是入庫/發布時間，不等於事件發生日期；只有原文明確寫出「當地時間」「於某日」「在某會議/場合」時，才能寫成發言日期與場合。
- officials.venue 必須可由原文直接核對。若原文沒有場合，只能寫「來源未提供具體場合」或使用原文已有的簡短場景（例如「白宮宣誓儀式」「接受採訪」）；禁止自行補成「公開金融研討會」「主題演講」「政策論壇」等看似合理但來源沒有的場合。
- 只有原文確認官員本人說話、受訪、演講、作證、致詞或宣誓就任，才能放入 officials。若只是市場文章、研究報告、利率期貨、總統/官員評論、分析師預測、或「市場關注某人首次講話」，請放入 expectations/reports 或直接略過，不得轉寫成該官員本人發言。
- 不得把發布日、轉載日、摘要日或社群討論日當作官員發言日。若同一事件由多篇快訊重複提及，請以原文事件日期為準；若日期不明，寫「原文未交代確切日期」。
- 原文未提供的政策立場不得補齊。若原文沒有說「反對降息」「支持升息」「核心通膨仍高」等內容，不得為了湊滿 details 自行生成；只能寫可推導的市場含意，並明確標成「市場解讀」或「政策含意」，不可冒充本人主張。
- 目標是「高密度但可追溯」：只要原文提供足夠資訊，每位官員盡量輸出 7-10 個 details；若原文資訊較少，仍可用「原文可驗證重點」「已知政策背景」「政策含意」「市場解讀」「資料限制」等角度展開到 4-6 點，但每一點都必須標清是原文事實、背景脈絡或分析推論。
- 若某位官員只有一句可驗證內容，不要只寫一句短摘要；請把同一句拆成可核對的事實、政策背景、反應函數含意、可能市場解讀與資料限制。禁止創造不存在的十點細節；完整性要求不得高於真實性要求。

═══════════════════════════════
判斷模式：每家央行先判斷當日是否有「貨幣政策發布」
═══════════════════════════════
「貨幣政策發布」的認定標準（任一項成立即視為有發布）：
- 利率決議 / 利率決定 / 政策利率調整（含維持不變的官方公告）
- FOMC 聲明、SEP 點陣圖、ECB Governing Council 決議、BoE MPC 決議、BoJ MPM 決議、RBA Cash Rate 決議等
- 央行貨幣政策聲明（Monetary Policy Statement）
- 央行行長 / 總裁 / 主席於決議後召開的記者會
- 季度貨幣政策報告（如日銀展望報告、英銀 Monetary Policy Report、ECB 經濟公報主軸發布日）

═══════════════════════════════
模式 A：當日有貨幣政策發布 → 必填 policy_release，officials/expectations/reports 全部留空陣列
═══════════════════════════════
請對該央行做「深度政策分析」，不要套用平日的三段架構。policy_release 包含下列子欄位（皆為條列字串陣列，除 summary 外）：

- has_release：true
- summary：3-5 句總結今日政策動向與市場含意（120-260 字），需明確點出利率水準、政策立場（鷹/鴿/中性）、與市場預期落差。
- decision：利率決議結果與政策工具變動明細。涵蓋政策利率、存款利率、貸款利率、量化緊縮/寬鬆規模、資產負債表方向、準備金率、前次水準對比、決議票數分歧（若有）。每點 100-220 字。最少 3 點。
- statement：政策聲明關鍵段落逐點拆解。涵蓋通膨敘事變化、就業/成長語氣、金融穩定段落、措辭新增或刪除（hawkish/dovish wording changes）。每點 120-260 字。最少 3 點。
- economic_view：央行對經濟、通膨、就業、金融條件的最新評估，含 SEP / 員工預測 / 通膨路徑 / GDP 預測修訂。若有預測表格內容請逐項列出新舊值對比。每點 120-260 字。最少 2 點。
- forward_guidance：前瞻指引、路徑訊號、官員對下一次會議的暗示、條件式承諾、QT 或 QE 路徑、終端利率（terminal rate）線索。每點 120-260 字。最少 2 點。
- press_conference：記者會 / 行長談話重點。逐題或逐主題整理（通膨、勞動市場、財政、匯率、地緣、市場波動等），引用具體用語（如 "data-dependent"、"meeting by meeting"、"sufficiently restrictive"）。每點 120-280 字。最少 4 點。若當日確認有決議但記者會內容資料有限，至少根據可得資訊整理 2 點並註明「資料有限」。
- market_reaction：市場對該決議與記者會的即時反應與後續定價變化。涵蓋短端利率期貨、OIS / 掉期定價、殖利率曲線變化、匯率、股市、信用利差。每點 100-220 字。最少 2 點。

═══════════════════════════════
模式 B：當日無貨幣政策發布 → 維持三段，has_release=false，policy_release 各子陣列輸出空陣列、summary 為空字串
═══════════════════════════════
- officials：當天該央行官員本人直接發言、受訪、演講、作證、致詞或正式人事交接整理。這不是字串陣列，而是「官員物件陣列」。每一個 officials 物件只能對應一位官員，欄位為 official、venue、analysis、details。venue 必須使用原文可核對的場合；若沒有場合，寫「來源未提供具體場合」，不得編造活動名稱。嚴禁把威廉姆斯、巴爾、米蘭等多位官員合併在同一個 official、analysis 或 details。若同一位官員有多則本人發言快訊，才合併成同一個官員物件。analysis 是 120-220 字總覽；details 優先輸出 7-10 個細項，來源有限時至少 4 個；每個細項都有 title 與 text，格式要像「發言時間與場合」「可驗證原文重點」「利率立場」「政策背景」「政策含義」「市場解讀」「資料限制」這種可掃讀標題。每個 details.text 需 55-150 字，必須完整交代原始發言重點、背景、利率立場、通膨判斷、就業或成長看法、金融穩定/資產負債表觀點、政策時點與市場含意；若原文沒有某項內容，不得補寫成本人主張，可改寫為「原文未提及」或「市場解讀」。最少 0 位官員；若只有間接提及央行官員但無本人發言，officials 必須留空陣列。最多 18 位官員。
- expectations：市場對央行政策預期。每點 90-200 字，需具體寫出 CME FedWatch / OIS / 路透調查 / 經濟學家中位數預測 / 利率期貨機率變化等可量化定價，並對比前一日或前一週基準；若沒有對比基準，說明目前定價代表的政策含意。最少 2 點。最多 12 點。
- reports：當天央行非決議類報告、研究、會議紀要、金融穩定、貼現窗、SOMA 操作、貨幣供給數據、外匯存底等。每點 90-200 字，需點出報告名稱、核心數字、政策意涵。最少 2 點（若有資料）。最多 12 點。
- 沒有任何資料的段才允許空陣列。

═══════════════════════════════
全央行共用嚴格規則
═══════════════════════════════
- 只整理與該央行直接相關的內容。Fed 只能放 Fed / FOMC / 聯準會；ECB 只能放 ECB / 歐洲央行；依此類推。其他央行的訊息嚴禁混入。
- 絕對禁止把澳幣、英鎊、股市、黃金、原油、加密、一般債券行情等市場新聞塞進官員發言、聲明或報告。
- 若只是市場行情文章引用央行作為背景，不要納入 reports / policy_release；除非文章有明確的利率期貨、調查或政策機率，才可放入 expectations。
- 不要寫「快訊顯示」「根據快訊」「資料指出」等元敘事；直接寫重點。
- 官員發言必須「有據可查」。嚴禁輸出「某某強調央行獨立性重要性」「某某稱經濟有韌性」這類來源沒有細節的套話；每位官員都要寫成可獨立閱讀的多點 details，包含他/她實際說了什麼、為什麼重要、對利率路徑與市場定價代表什麼。沒有原文依據的細節不得補。
- details 要盡量多，但不能平鋪重複。可把同一則原文拆成不同層次：1）原文直接事實；2）明確日期/場合或「來源未提供」；3）利率或政策工具是否被提及；4）通膨/就業/成長是否被提及；5）與央行反應函數的關係；6）市場定價或預期的影響；7）限制與不確定性。每一層都要避免冒充原文未說過的內容。
- 若同一位官員在同一天有多個主題，合併在同一個官員物件的 analysis；不同官員必須拆成不同官員物件，不可合併。
- 若原始資料對某位官員只有一兩句，請在不杜撰新事實的前提下，完整展開該句話的政策背景、央行反應函數含意、與市場可能解讀。details 至少要包含「發言時間與場合」（若未知就寫「來源未提供具體場合」）、「可驗證原文重點」、「政策含義」；若原文沒有利率、通膨、就業、資產負債表、金融穩定、銀行監管、支付系統等內容，不得自行新增。
- 數字、百分比、機率、票數請完整保留並標明單位（bps、%、人）。
- 中文用詞採台灣財經媒體慣例：聯準會、歐洲央行、英國央行、日本央行、澳洲聯儲、加拿大央行、印度央行、巴西央行、瑞士央行、升息、降息、利率期貨、殖利率、量化緊縮、貼現窗、政策利率、存款便利、再融資。
- status 欄位：模式 A 寫「政策決議日」「會議紀要日」「政策報告日」等具體事件；模式 B 寫「追蹤中」「官員密集發言」「無重大事件」等。

═══════════════════════════════
輸出要求
═══════════════════════════════
- headline：一句總結全球央行主線，25 字以內。若當日有任一主要央行決議，須在 headline 點名該央行與方向。
- central_banks 陣列必須剛好 9 個，順序與上方清單一致。
- 每家央行都必須提供完整的 policy_release 物件結構（即使是模式 B，也要回傳 has_release=false + 所有子欄位的空值）。

資料庫快訊：
{compact_news_input(items, max_chars=1200)}
""".strip()

    bullet_array = {
        "type": "array",
        "items": {"type": "string"},
    }
    official_detail_array = {
        "type": "array",
        "minItems": 4,
        "maxItems": 12,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string", "minLength": 20},
            },
            "required": ["title", "text"],
        },
    }
    official_array = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "official": {"type": "string"},
                "venue": {"type": "string"},
                "analysis": {"type": "string", "minLength": 120},
                "details": official_detail_array,
            },
            "required": ["official", "venue", "analysis", "details"],
        },
    }
    policy_release_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "has_release": {"type": "boolean"},
            "summary": {"type": "string"},
            "decision": bullet_array,
            "statement": bullet_array,
            "economic_view": bullet_array,
            "forward_guidance": bullet_array,
            "press_conference": bullet_array,
            "market_reaction": bullet_array,
        },
        "required": [
            "has_release",
            "summary",
            "decision",
            "statement",
            "economic_view",
            "forward_guidance",
            "press_conference",
            "market_reaction",
        ],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline": {"type": "string"},
            "central_banks": {
                "type": "array",
                "minItems": 9,
                "maxItems": 9,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "status": {"type": "string"},
                        "officials": official_array,
                        "expectations": bullet_array,
                        "reports": bullet_array,
                        "policy_release": policy_release_schema,
                    },
                    "required": [
                        "id",
                        "name",
                        "status",
                        "officials",
                        "expectations",
                        "reports",
                        "policy_release",
                    ],
                },
            },
        },
        "required": ["headline", "central_banks"],
    }
    def request_summary(user_prompt: str, max_output_tokens: int = 32000) -> dict:
        payload = {
            "model": OPENAI_SUMMARY_MODEL,
            "input": [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "你只輸出符合 JSON schema 的繁體中文央行政策摘要。"}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "daily_central_bank_summary",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
        r.raise_for_status()
        return json.loads(extract_response_text(r.json()))

    result = sanitize_central_bank_officials(request_summary(prompt))
    official_issues = central_bank_official_issues(result, required_officials)
    for attempt in range(2):
        if not official_issues:
            break
        retry_prompt = f"""
{prompt}

═══════════════════════════════
上一版輸出不合格（第 {attempt + 1} 次），以下官員發言有漏人、混寫或段落太短：
{chr(10).join(f"- {item}" for item in official_issues[:30])}

請重新輸出完整 JSON。這次 officials 必須是一位官員一個物件，不可把多位官員合併成同一點。
只覆蓋資料中有本人直接發言、受訪、演講、作證、致詞或正式人事交接的官員；被總統、財長、白宮顧問、分析師、媒體或市場行情文章提到的官員，不得寫成 officials。
每位官員優先輸出 7-10 個 details 細項，來源有限時至少 4 個；每個細項必須有可掃讀 title 與完整 text，但所有內容都必須能從原文或明確政策背景推導，不得創造原文沒有的日期、場合、會議、演講題目、利率立場或通膨判斷。
details 可拆成「發言時間與場合」「可驗證原文重點」「政策背景」「利率立場」「通膨/就業是否提及」「反應函數含意」「政策含義」「市場解讀」「資料限制」等；若原文沒有場合，venue 與該細項請寫「來源未提供具體場合」，不要補成公開研討會、論壇或主題演講。
analysis 只做總覽，真正細節放在 details；analysis + details 合計至少 300 個中文字，越完整越好，但每個新增細項都必須標清是原文事實、背景脈絡或分析推論，真實性優先於字數。
絕對不要再輸出短句、新聞標題式摘要、多位官員混寫，或把間接新聞改寫成官員本人發言。
""".strip()
        result = sanitize_central_bank_officials(request_summary(retry_prompt))
        official_issues = central_bank_official_issues(result, required_officials)
    hard_issues = central_bank_official_issues(result, required_officials, include_missing=False)
    if hard_issues:
        print("[WARN] 央行官員摘要仍有格式疑慮（已輸出詳細版，避免回落舊短版）：" + "; ".join(hard_issues[:30]))
    missing_issues = [issue for issue in official_issues if "缺少官員" in issue]
    if missing_issues:
        print("[WARN] 央行官員名單疑似缺漏（已輸出詳細版，保留警告）：" + "; ".join(missing_issues[:20]))
    return result


def empty_central_bank_summary(summary_date: str, start: datetime, end: datetime, count: int) -> dict:
    return {
        "id": summary_date.replace("-", ""),
        "date": summary_date,
        "range_start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "range_end": end.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S"),
        "schema_version": CENTRAL_BANK_SUMMARY_SCHEMA_VERSION,
        "count": count,
        "headline": "今日央行資訊有限",
        "central_banks": [
            {
                "id": bank["id"],
                "name": bank["name"],
                "status": "今日資料不足",
                "officials": [],
                "expectations": [],
                "reports": [],
                "policy_release": {
                    "has_release": False,
                    "summary": "",
                    "decision": [],
                    "statement": [],
                    "economic_view": [],
                    "forward_guidance": [],
                    "press_conference": [],
                    "market_reaction": [],
                },
            }
            for bank in CENTRAL_BANKS
        ],
    }


def central_bank_window_end(now: datetime) -> datetime:
    end = now.replace(hour=CENTRAL_BANK_SUMMARY_HOUR, minute=0, second=0, microsecond=0)
    if now < end:
        end -= timedelta(days=1)
    return end


def central_bank_window_for_date(day: datetime.date) -> tuple[datetime, datetime]:
    end = datetime.combine(day, datetime.min.time(), tzinfo=TW).replace(hour=CENTRAL_BANK_SUMMARY_HOUR)
    start = end - timedelta(days=1)
    return start, end


def central_bank_summary_needs_refresh(item: dict | None) -> bool:
    if not item:
        return True
    try:
        version = int(item.get("schema_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    return version < CENTRAL_BANK_SUMMARY_SCHEMA_VERSION


def central_bank_target_days(now: datetime, data: dict) -> list[datetime.date]:
    latest_end = central_bank_window_end(now)
    raw_backfill = os.getenv("CENTRAL_BANK_BACKFILL_DAYS", "").strip()
    if raw_backfill:
        try:
            days = max(1, int(float(raw_backfill)))
            return [(latest_end - timedelta(days=i)).date() for i in range(days)]
        except ValueError:
            print(f"[ERROR] CENTRAL_BANK_BACKFILL_DAYS 格式錯誤（需為數字）：{raw_backfill}，改用今日")

    if not data.get("items"):
        return [(latest_end - timedelta(days=i)).date() for i in range(CENTRAL_BANK_AUTO_BACKFILL_DAYS)]

    stale_days = []
    for item in data.get("items", []):
        date_text = item.get("date", "")
        if not date_text or not central_bank_summary_needs_refresh(item):
            continue
        try:
            stale_days.append(datetime.fromisoformat(date_text).date())
        except ValueError:
            continue
    if stale_days:
        return sorted(set(stale_days), reverse=True)[:CENTRAL_BANK_AUTO_BACKFILL_DAYS]

    existing_dates = {str(item.get("date", "")) for item in data.get("items", [])}
    missing_recent_days = [
        (latest_end - timedelta(days=i)).date()
        for i in range(CENTRAL_BANK_AUTO_BACKFILL_DAYS)
        if (latest_end - timedelta(days=i)).date().isoformat() not in existing_dates
    ]
    if missing_recent_days:
        return missing_recent_days

    return [latest_end.date()]


def update_central_bank_summaries(conn) -> None:
    now = datetime.now(TW)
    force_update = os.getenv("CENTRAL_BANK_FORCE_UPDATE", "").strip()
    backfill_days = os.getenv("CENTRAL_BANK_BACKFILL_DAYS", "").strip()
    force_update = force_update or backfill_days
    data = load_central_bank_summaries()
    target_days = central_bank_target_days(now, data)

    latest_end = central_bank_window_end(now)
    if target_days == [latest_end.date()] and now < latest_end and not force_update:
        print(f"[CB] 尚未到每日央行摘要更新時間（GMT+8 {CENTRAL_BANK_SUMMARY_HOUR:02d}:00）")
        return

    if not OPENAI_API_KEY:
        print("[SKIP] 央行摘要：未設定 OPENAI_API_KEY")
        return

    existing_by_date = {item.get("date"): item for item in data.get("items", [])}
    changed = False
    for day in target_days:
        summary_date = day.isoformat()
        if summary_date in existing_by_date and not force_update and not central_bank_summary_needs_refresh(existing_by_date.get(summary_date)):
            print(f"[CB] {summary_date} 已有央行摘要，略過")
            continue

        day_start, day_end = central_bank_window_for_date(day)
        items = central_bank_news_for_day(conn, day_start, day_end)

        try:
            if len(items) < 3:
                summary_item = empty_central_bank_summary(summary_date, day_start, day_end, len(items))
                print(f"[CB] {summary_date} 央行相關快訊不足 ({len(items)} 筆)，寫入空摘要")
            else:
                result = call_openai_central_bank_summary(items, day_start, day_end)
                summary_item = {
                    "id": summary_date.replace("-", ""),
                    "date": summary_date,
                    "range_start": day_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "range_end": day_end.strftime("%Y-%m-%d %H:%M:%S"),
                    "generated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S"),
                    "schema_version": CENTRAL_BANK_SUMMARY_SCHEMA_VERSION,
                    "count": len(items),
                    **result,
                }
                print(f"[OK] 央行摘要：新增 {summary_date} ({len(items)} 則快訊)")

            data["items"] = [item for item in data.get("items", []) if item.get("date") != summary_date]
            data.setdefault("items", []).insert(0, summary_item)
            existing_by_date[summary_date] = summary_item
            changed = True
        except Exception as e:
            print(f"[ERROR] 央行摘要 {summary_date} 失敗：{e}")

    if changed:
        save_central_bank_summaries(data)


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
    try:
        mktnews_pages = max(1, int(os.getenv("MKTNEWS_PAGES", str(MKTNEWS_DEFAULT_PAGES))))
    except ValueError:
        mktnews_pages = MKTNEWS_DEFAULT_PAGES

    # 1. 快訊
    db_path = NEW_DB_PATH if os.path.exists(NEW_DB_PATH) else DB_PATH
    conn = sqlite3.connect(db_path)
    init_db(conn)
    seed_news_db_from_export(conn)
    raw_items = fetch_pages(pages=5, page_size=50)
    processed = [process_item(r) for r in raw_items]
    sina_new_count = upsert_items(conn, processed)

    te_new_counts = {}
    for stream_type in TE_STREAM_TYPES:
        te_raw_items = fetch_tradingeconomics_stream_news(stream_type, limit=50)
        te_processed = [item for item in (process_tradingeconomics_item(r) for r in te_raw_items) if item]
        te_new_counts[stream_type] = upsert_items(conn, te_processed)

    mktnews_raw_items = fetch_mktnews_flash(pages=mktnews_pages, page_size=MKTNEWS_PAGE_LIMIT)
    mktnews_processed = [item for item in (process_mktnews_flash_item(r) for r in mktnews_raw_items) if item]
    mktnews_new_count = upsert_items(conn, mktnews_processed)

    em_new_counts: dict[str, int] = {}
    if EASTMONEY_TARGET > 0:
        for slug, column_id, category, sublabel in EASTMONEY_SOURCES:
            em_raw_items = fetch_eastmoney_fastnews(slug, column_id, EASTMONEY_TARGET)
            em_processed = [
                item for item in (
                    process_eastmoney_item(r, slug, category, sublabel)
                    for r in em_raw_items
                ) if item
            ]
            em_new_counts[slug] = upsert_items(conn, em_processed)
            time.sleep(0.5)

    clear_recent_ai_summaries_if_requested()
    update_ai_summaries(conn)
    update_central_bank_summaries(conn)
    maintain_news_db(conn)
    export_daily_news_archives(conn)
    updated, total = export_news_json(conn)
    conn.close()
    te_summary = "｜".join(f"TE {name} 新增 {count} 筆" for name, count in te_new_counts.items())
    em_total = sum(em_new_counts.values())
    em_summary = f"東方財富 {len(em_new_counts)} 欄目新增 {em_total} 筆 (target={EASTMONEY_TARGET})"
    print(f"[OK] 快訊：新浪新增 {sina_new_count} 筆｜{te_summary}｜MKT News {mktnews_pages} 頁新增 {mktnews_new_count} 筆｜{em_summary}｜累計 {total} 筆")

    try:
        calendar_count = fetch_capital_calendar.update_calendar_json()
        print(f"[OK] 財經日曆更新 {calendar_count} 筆")
    except Exception as e:
        print(f"[WARN] 財經日曆更新失敗：{e}")

    # 2. 市場報價
    if HAS_YF:
        print("[市場] 開始抓取報價...")
        market_data = fetch_all_market()
        export_market_json(market_data)
    else:
        print("[SKIP] yfinance 未安裝，跳過市場資料")


if __name__ == "__main__":
    main()
