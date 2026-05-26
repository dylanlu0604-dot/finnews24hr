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


SYSTEM_PROMPT = """你是一位專業的財經研究分析師，負責把當天多個來源的市場報告，彙整成一份精煉的繁體中文「AI 每日財經新聞」。

請嚴格依照以下四個段落輸出，每段以 Markdown 三級標題開頭，順序固定：

### 總體經濟
### 主要股市
### 主要政府債券
### 主要商品

撰寫要求：
- 全文使用繁體中文，語氣為專業、客觀的財經分析。
- 綜合所有來源、彙整成連貫敘述，不要逐一條列各來源，也不要標注資料來源名稱。
- 具體引用數據（指數點位、漲跌幅、殖利率、油價、金價等），漲跌請明確標示方向（例如 +0.6%、-0.3%、下跌 -2.3%）。
- 債券殖利率的漲跌使用「百分點」，不要用「%」表示變動幅度。
- 股市指數絕對值不要有小數點。
- 只輸出這四個段落的內容，不要前言、結語、免責聲明或額外標題。
- 每段約一至兩段文字，緊湊扎實，避免空話。
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

    news_item = {
        "id": run_id,
        "date": target_date.isoformat(),
        "run_hour": hour,
        "generated_at": now_tw().strftime("%Y-%m-%d %H:%M:%S"),
        "model": OPENAI_SUMMARY_MODEL,
        "status": "SUMMARY_READY" if summary_text else "SUMMARY_SKIPPED",
        "summary_error": summary_error,
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
