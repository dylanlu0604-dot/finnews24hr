#!/usr/bin/env python3
"""Fetch Capital Futures economic calendar for this week and next week."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup


TW = dt.timezone(dt.timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR.parent / "docs"
OUTPUT_PATH = DOCS_DIR / "economic_calendar.json"
BASE_URL = "https://www.capitalfutures.com.tw"
CALENDAR_PATH = "/zh-tw/financial/calendar"
DEFAULT_INTERVALS = ("this_week", "next_week")
INTERVAL_LABELS = {
    "this_week": "本週",
    "next_week": "下週",
}
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def now_tw() -> dt.datetime:
    return dt.datetime.now(TW)


def verify_tls() -> bool:
    raw = os.getenv("CAPITAL_CALENDAR_VERIFY_TLS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_metric(span_text: str) -> tuple[str, str] | None:
    text = clean_text(span_text)
    match = re.match(r"^(前值|預測|結果)\s*(.*)$", text)
    if not match:
        return None
    label, value = match.groups()
    return label, value.strip() or "--"


def metric_is_released(value: str) -> bool:
    cleaned = clean_text(value)
    return bool(cleaned) and cleaned not in {"--", "-", "—", "待公布", "待定"}


def parse_date_header(text: str) -> str:
    cleaned = clean_text(text)
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported calendar date header: {cleaned}")


def parse_event_datetime(date_iso: str, time_text: str) -> str:
    cleaned = clean_text(time_text)
    match = re.search(r"(\d{1,2}):(\d{2})", cleaned)
    if not match:
        return f"{date_iso} 00:00:00"
    hour, minute = match.groups()
    return f"{date_iso} {int(hour):02d}:{minute}:00"


def extract_fid(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return (query.get("fid") or [""])[0]


def fallback_event_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"hash:{digest}"


def parse_calendar_html(html: str, interval: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []
    current_date = ""

    for node in soup.select("article h2.title6, article .card8-2"):
        classes = node.get("class") or []
        if node.name == "h2" and "title6" in classes:
            current_date = parse_date_header(node.get_text(" ", strip=True))
            continue
        if "card8-2" not in classes:
            continue

        title_link = node.select_one(".main .title a")
        title = clean_text(title_link.get_text(" ", strip=True) if title_link else "")
        if not title:
            continue

        href = title_link.get("href", "") if title_link else ""
        event_url = urljoin(BASE_URL, href) if href else source_url
        fid = extract_fid(event_url)
        time_text = clean_text(node.select_one(".main .time").get_text(" ", strip=True)) if node.select_one(".main .time") else ""
        country = clean_text(node.select_one(".main .country p").get_text(" ", strip=True)) if node.select_one(".main .country p") else ""
        importance = sum(
            1
            for img in node.select(".main .stars img")
            if "ic_star2" in (img.get("src") or "")
        )
        metrics: dict[str, str] = {}
        for span in node.select(".items span"):
            parsed = parse_metric(span.get_text(" ", strip=True))
            if parsed:
                label, value = parsed
                metrics[label] = value

        event_date = current_date
        if not event_date and time_text:
            month_day = re.search(r"(\d{1,2})/(\d{1,2})", time_text)
            if month_day:
                year = now_tw().year
                event_date = f"{year}-{int(month_day.group(1)):02d}-{int(month_day.group(2)):02d}"
        if not event_date:
            continue

        event_datetime = parse_event_datetime(event_date, time_text)
        previous = metrics.get("前值", "--")
        forecast = metrics.get("預測", "--")
        actual = metrics.get("結果", "--")
        event_id = f"capital:{fid}" if fid else fallback_event_id(interval, event_datetime, country, title)

        events.append(
            {
                "id": event_id,
                "fid": fid,
                "interval": interval,
                "date": event_date,
                "time": event_datetime[11:16],
                "datetime": event_datetime,
                "display_time": time_text,
                "country": country,
                "title": title,
                "importance": importance,
                "previous": previous,
                "forecast": forecast,
                "actual": actual,
                "actual_released": metric_is_released(actual),
                "url": event_url,
                "source_url": source_url,
            }
        )

    return events


def calendar_url(interval: str, important: int) -> str:
    return f"{BASE_URL}{CALENDAR_PATH}?interval={interval}&important={important}"


def fetch_interval(interval: str, important: int = 0) -> tuple[str, list[dict]]:
    url = calendar_url(interval, important)
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=(10, 45), verify=verify_tls())
    except requests.exceptions.SSLError as exc:
        print(f"[CAL] TLS verification failed for {url}; retrying without verification: {exc}", file=sys.stderr)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=(10, 45), verify=False)
    response.raise_for_status()
    return url, parse_calendar_html(response.text, interval, url)


def group_events(interval: str, events: list[dict], source_url: str) -> dict:
    dates: dict[str, list[dict]] = {}
    for event in sorted(events, key=lambda item: (item.get("datetime", ""), item.get("title", ""))):
        dates.setdefault(event["date"], []).append(event)
    return {
        "interval": interval,
        "label": INTERVAL_LABELS.get(interval, interval),
        "source_url": source_url,
        "count": len(events),
        "dates": [
            {"date": date, "count": len(day_events), "events": day_events}
            for date, day_events in sorted(dates.items())
        ],
    }


def build_payload(intervals: list[str], important: int = 0) -> dict:
    weeks = []
    all_events = []
    errors = []

    for interval in intervals:
        try:
            source_url, events = fetch_interval(interval, important=important)
            weeks.append(group_events(interval, events, source_url))
            all_events.extend(events)
            print(f"[CAL] {interval}: {len(events)} events")
        except Exception as exc:
            errors.append({"interval": interval, "error": str(exc)})
            print(f"[CAL] {interval} failed: {exc}", file=sys.stderr)

    all_events = sorted(all_events, key=lambda item: (item.get("datetime", ""), item.get("title", "")))
    return {
        "updated": now_tw().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Taipei",
        "source": "群益期貨 財經日曆",
        "source_home": f"{BASE_URL}{CALENDAR_PATH}",
        "important": important,
        "count": len(all_events),
        "errors": errors,
        "weeks": weeks,
        "items": all_events,
    }


def save_payload(payload: dict, output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_calendar_json(
    intervals: tuple[str, ...] | list[str] = DEFAULT_INTERVALS,
    important: int = 0,
    output_path: Path = OUTPUT_PATH,
) -> int:
    payload = build_payload(list(intervals), important=important)
    if not payload["items"]:
        raise RuntimeError("No economic calendar events were fetched; keeping existing output unchanged.")
    save_payload(payload, output_path)
    return payload["count"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Capital Futures economic calendar JSON.")
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
        help="Comma-separated intervals, e.g. this_week,next_week.",
    )
    parser.add_argument("--important", type=int, default=0, help="Capital Futures important filter, default 0.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Output JSON path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    if not intervals:
        print("No intervals requested.", file=sys.stderr)
        return 2
    try:
        count = update_calendar_json(intervals, important=args.important, output_path=Path(args.output))
    except Exception as exc:
        print(f"[CAL] failed: {exc}", file=sys.stderr)
        return 1
    print(f"[CAL] economic_calendar.json updated with {count} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
