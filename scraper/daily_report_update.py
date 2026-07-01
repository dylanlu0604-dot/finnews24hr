#!/usr/bin/env python3
"""Scrape daily market reports, summarize them with the site's OpenAI model, and export JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local fallback before dependencies are installed.
    def load_dotenv(*_args, **_kwargs):
        return False

import daily_reports


TW = dt.timezone(dt.timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR.parent / "docs"
DAILY_NEWS_PATH = DOCS_DIR / "daily_news.json"
DAILY_LOG_PATH = DOCS_DIR / "daily_scrape_logs.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-nano").strip()
MAX_REPORT_CHARS = 12000
ALLOWED_GENERATION_WEEKDAYS = {1, 2, 3, 4, 5}  # Tue-Sat in Python's Monday=0 convention.


SYSTEM_PROMPT = """你是一位專業的財經研究分析師，負責把當天多個來源的市場報告，彙整成一份精煉的繁體中文「AI 每日財經新聞」。

請嚴格依照以下四個段落輸出，每段以 Markdown 三級標題開頭，順序固定：

你是一位資深的總體經濟分析師。請根據以下來自多家金融機構的市場日報，將其整合並組織成一份簡潔、重點突出的每日市場更新。

【基本寫作規則】
1. 視角與來源：無需說明資料來源，請將資訊自然融入你客觀的分析師觀點中。忽略任何廣告訊息。
2. 焦點：只關注每日實際發生的重大事件與數據，無需過度猜測或對未來進行主觀預測。
3. 標題：請創建一個簡潔的新聞式標題，清楚總結當日最重要的市場變化，標題中不需提及具體日期。
4. 語言：一律使用繁體中文，並符合台灣金融市場的習慣用語與專有名詞。
5. 特殊條件：除非澳洲金融市場發生異常大的波動或澳洲央行有重大決策，否則無需特別提及澳洲市場。

【段落結構與撰寫指引】
請嚴格將內容組織成以下四個部分，每個部分「只能有一段文字」，請保持簡潔扼要：

#### 總體經濟與市場大事
* 撰寫指引：針對「總體經濟與市場大事」的撰寫指引：1)主要：聚焦全球央行政策動態、全球主要國家重要總經數據（尤其是各國GDP、CPI、HICP、PCE通膨、PMI、就業市場等數據）。2)次要：如果需要可以「簡短補充」重大地緣政治、各國政策。若包含明確數字請精準列出。可適度納入對大盤有系統性影響的科技或企業巨頭動態。

#### 主要股市
* 撰寫指引：概述全球主要股市（美、歐、亞）的整體趨勢。必須包含代表性指數（如標普500、那斯達克、道瓊歐洲STOXX 600、台股、日經等）的「漲跌幅百分比」與「絕對指數點位」。需點出領漲或領跌的關鍵產業板塊（如科技、半導體），並簡述背後的市場驅動因素。除非個別公司對大盤有決定性影響，否則不需提及個股。

#### 主要政府債券
* 撰寫指引：聚焦美、德、日、中等主要經濟體的公債殖利率變化（特別是 10 年期與 2 年期）。必須明確寫出殖利率的「變動幅度（百分點或基點）」與「最新的絕對數值」（例如：上漲 0.09 百分點至 4.5%）。並簡短解釋殖利率升降的原因（如通膨數據發布、升息/降息預期變化）。

#### 主要商品
* 撰寫指引：專注於國際原油（如 WTI、布蘭特）與黃金市場的表現。必須包含商品的「漲跌幅百分比」與「收盤絕對價格」（如：每桶 70.1 美元、每盎司 4021.8 美元）。需結合地緣政治、實際利率、美元走勢或供需變化，來解釋價格波動的核心原因。

撰寫要求：
- 全文使用繁體中文，語氣為專業、客觀的財經分析。
- 綜合所有來源、彙整成連貫敘述，不要逐一條列各來源，也不要標注資料來源名稱。
- 具體引用數據（指數點位、漲跌幅、殖利率、油價、金價等），漲跌請明確標示方向（例如 +0.6%、-0.3%、下跌 2 個基點）。
- 各來源若有衝突或不一致，以較具體、較多來源支持者為準。
- 只輸出這四個段落的內容，不要前言、結語、免責聲明或額外標題。
- 每段約一至兩段文字，緊湊扎實，避免空話。


【資料來源優先順序】
- 判斷當日市場主軸、重大新聞、總經數據、央行政策、地緣政治、政策變化與市場行情時，優先參考 wallstreetcn 與 jin10。
- wallstreetcn 與 jin10 提到的事件，應優先納入摘要並作為判斷當日重點的主要依據。
- 其他來源主要用於補充細節、補足 wallstreetcn 與 jin10 未涵蓋的市場或資產類別。
- 若不同來源對同一事件、數據或市場解讀不一致，優先採用 wallstreetcn 與 jin10。
- 輸出時不要標注來源名稱。

數據格式規範（極重要，請嚴格比對）：

[ 規範 A：常規資產（股票、外匯、商品等）]
   - 只要數據為下跌/減少，必須採用「下跌 -X%」的格式。
     * 正確範例：下跌 -2.3%
     * 錯誤範例：下跌 2.3%、減少了 2.3%

[ 規範 B：債券殖利率專用 ]
   - 債券殖利率的漲跌，一律不准使用「%」，必須使用「百分點」。
   - 如果是下跌，同樣要加上負號，格式為「下跌 -X百分點」。
     * 正確範例：下跌 -0.14 百分點、上漲 0.05百分點
     * 錯誤範例：下跌 -0.14%、下跌 0.14 百分點

[ 規範 C：股市數字絕對值不要有小數點 ]
- 股市絕對數值請去除小數點。
* 正確範例：道瓊工業指數下跌 -0.3%至38500點，那斯達克指數下跌 -0.7%至16200點。
* 錯誤範例：道瓊工業指數下跌 -0.3%至38500.2點。

小數點規範：
- 所有文章中提到的百分點、%都是要修正到「小數點後1位」。
- 「收盤行情（價格/指數/殖利率數字）」一律修正並呈現到「小數點後1位」，其中股市數字絕對值則不要有小數點。
- 綜合正確範例：S&P 500 指數收盤在 15230、債券殖利率 4.3%、利率上漲 1.4 個百分點、美元指數 103.2、原油期貨下跌 -3.3%。

注意：百分點與基點不要搞混，債券殖利率的漲跌必須使用「百分點」，而非「%」。例如，如果債券殖利率從 4.5% 上漲到 4.7%，正確的表達應該是「上漲 0.2 百分點」，而不是「上漲 0.2%」或「上漲 20 個基點」。

以下為理想輸出的風格範例（僅供語氣與格式參考，內容請以當日實際資料為準）：

【範例一】
### 總體經濟與市場大事
本日市場焦點集中於中東地緣政治發展與美國經濟數據。美國上週初次申請失業金人數維持在209,000人，顯示勞動市場狀況穩定…（略）

### 主要股市
美國股市普遍收高，道瓊工業指數上漲0.6%至50286點…（略）

### 主要政府債券
美國公債殖利率普遍小幅走低，10年期美債殖利率維持在4.6%…（略）

### 主要商品
國際油價在盤中經歷劇烈震盪後收跌…（略）

【範例二】
### 總體經濟與市場大事
美國總統川普透露與伊朗的談判破局，油價大幅飆高…（略）

### 主要股市
受中東地緣政治緊張緩解及油價下跌激勵，全球主要股市普遍上揚…（略）

### 主要政府債券
隨著中東局勢趨緩及油價回落，主要政府債券市場普遍反彈…（略）

### 主要商品
國際油價因美國與伊朗可能達成協議的樂觀預期而重挫…（略）
"""


def now_tw() -> dt.datetime:
    return dt.datetime.now(TW)


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except Exception as exc:
        print(f"[WARN] {path.name} 讀取失敗：{exc}")
        return default


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = now_tw().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def result_to_log(result: daily_reports.ScrapeResult) -> dict:
    return {
        "source": result.source,
        "ok": result.ok,
        "status": result.status,
        "reason": result.reason,
        "title": result.title,
        "url": result.url,
        "expected_date": result.expected_date.isoformat() if result.expected_date else "",
        "actual_date": result.actual_date.isoformat() if result.actual_date else "",
    }


def build_user_message(target_date: dt.date, results: list[daily_reports.ScrapeResult]) -> str:
    blocks = [f"以下為 {target_date.isoformat()} 各來源的原始報告，請彙整成四段式 AI 每日財經新聞："]
    for result in results:
        if not result.ok:
            continue
        title = f"\n標題：{result.title}" if result.title else ""
        url = f"\nURL：{result.url}" if result.url else ""
        body = daily_reports.require_text(result.content, f"{result.source} 內容為空")
        if len(body) > MAX_REPORT_CHARS:
            body = body[:MAX_REPORT_CHARS] + "\n[內容因長度限制截斷]"
        blocks.append(f"===== 來源：{result.source} ====={title}{url}\n{body}")
    return "\n\n".join(blocks)


def extract_response_text(response: dict) -> str:
    if response.get("output_text"):
        return response["output_text"]
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "".join(parts)


def call_openai_daily_summary(target_date: dt.date, results: list[daily_reports.ScrapeResult]) -> str:
    payload = {
        "model": OPENAI_SUMMARY_MODEL,
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": build_user_message(target_date, results)}]},
        ],
        "max_output_tokens": 8000,
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    text = extract_response_text(response.json()).strip()
    if not text:
        raise RuntimeError("OpenAI 回傳空摘要")
    return text + "\n"


def upsert_by_id(items: list[dict], new_item: dict, *, limit: int) -> list[dict]:
    merged = [item for item in items if item.get("id") != new_item.get("id")]
    merged.insert(0, new_item)
    return merged[:limit]


def update_daily_news(item: dict) -> None:
    data = load_json(DAILY_NEWS_PATH, {"updated": "", "items": []})
    data["items"] = upsert_by_id(data.get("items", []), item, limit=90)
    save_json(DAILY_NEWS_PATH, data)


def update_daily_log(item: dict) -> None:
    data = load_json(DAILY_LOG_PATH, {"updated": "", "items": []})
    data["items"] = upsert_by_id(data.get("items", []), item, limit=270)
    save_json(DAILY_LOG_PATH, data)


def run_daily_update(target_date: dt.date, sources: list[str], run_hour: int | None) -> int:
    if target_date.weekday() not in ALLOWED_GENERATION_WEEKDAYS:
        print(f"[SKIP] {target_date.isoformat()} is Sunday/Monday in Asia/Taipei scope; daily report is not generated.")
        return 0

    started_at = now_tw()
    ctx = daily_reports.build_context(target_date)
    results: list[daily_reports.ScrapeResult] = []
    print(f"[DAILY] Target date: {target_date.isoformat()} | sources: {', '.join(sources)}")

    for source in sources:
        print(f"[DAILY] {source} scraping...")
        result = daily_reports.SCRAPERS[source](ctx)
        results.append(result)
        print(f"[DAILY] {source}: {result.status}" + (f" - {result.reason}" if result.reason else ""))

    finished_at = now_tw()
    success_results = [result for result in results if result.ok]
    failed_results = [result for result in results if not result.ok]
    hour = run_hour if run_hour is not None else started_at.hour
    run_id = f"{target_date.strftime('%Y%m%d')}{hour:02d}"
    status = "SUCCESS" if success_results and not failed_results else "PARTIAL" if success_results else "FAILED"

    log_item = {
        "id": run_id,
        "date": target_date.isoformat(),
        "run_hour": hour,
        "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "total_sources": len(results),
        "success_count": len(success_results),
        "failure_count": len(failed_results),
        "success_sources": [result.source for result in success_results],
        "failed_sources": [result.source for result in failed_results],
        "sources": [result_to_log(result) for result in results],
    }
    update_daily_log(log_item)

    summary_text = ""
    summary_error = ""
    if len(success_results) < 2:
        summary_error = f"成功來源不足，僅 {len(success_results)} 個來源"
    elif not OPENAI_API_KEY:
        summary_error = "未設定 OPENAI_API_KEY"
    else:
        try:
            summary_text = call_openai_daily_summary(target_date, success_results)
        except Exception as exc:
            summary_error = str(exc)
            print(f"[ERROR] Daily summary failed: {summary_error}")

    if summary_text:
        news_item = {
            "id": run_id,
            "date": target_date.isoformat(),
            "run_hour": hour,
            "generated_at": now_tw().strftime("%Y-%m-%d %H:%M:%S"),
            "model": OPENAI_SUMMARY_MODEL,
            "status": "SUMMARY_READY",
            "summary_error": "",
            "source_count": len(results),
            "success_count": len(success_results),
            "failure_count": len(failed_results),
            "success_sources": [result.source for result in success_results],
            "failed_sources": [result.source for result in failed_results],
            "content": summary_text,
        }
        update_daily_news(news_item)
    print(f"[DAILY] Log saved. success={len(success_results)} failure={len(failed_results)}")
    if summary_text:
        print(f"[DAILY] Summary saved with model {OPENAI_SUMMARY_MODEL}")
    else:
        print(f"[DAILY] Summary skipped: {summary_error}")
    return 0 if success_results else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update daily market report summary and scrape log JSON.")
    parser.add_argument("--date", default=now_tw().date().isoformat(), help="Asia/Taipei target date YYYY-MM-DD.")
    parser.add_argument("--sources", default=os.getenv("DAILY_REPORT_SOURCES", "all"), help="Comma-separated source list, or all.")
    parser.add_argument("--run-hour", type=int, default=None, help="Taiwan-hour label to store in JSON, e.g. 7, 8, 9.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global OPENAI_API_KEY, OPENAI_SUMMARY_MODEL
    load_dotenv(BASE_DIR.parent / ".env")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.4-nano").strip()
    args = parse_args(argv or sys.argv[1:])
    try:
        target_date = dt.date.fromisoformat(args.date)
    except ValueError:
        print("日期格式錯誤：--date 必須是 YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        sources = daily_reports.parse_sources(args.sources)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return run_daily_update(target_date, sources, args.run_hour)


if __name__ == "__main__":
    raise SystemExit(main())
