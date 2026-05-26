#!/usr/bin/env python3
"""
Daily market report scraper.

Usage:
    python3 scraper.py
    python3 scraper.py --date 2026-05-22 --sources cnyes,taishin
    python3 scraper.py --strict

Output is always written to scraped_reports/<date>/ — exactly one folder per day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import traceback
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover - exercised when dependencies are missing.
    requests = None

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

try:
    import scrapetube
except ImportError:  # pragma: no cover
    scrapetube = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.formatters import TextFormatter
    try:
        from youtube_transcript_api import CouldNotRetrieveTranscript as _CouldNotRetrieveTranscript
    except ImportError:
        _CouldNotRetrieveTranscript = None
except ImportError:  # pragma: no cover
    YouTubeTranscriptApi = None
    TextFormatter = None
    _CouldNotRetrieveTranscript = None

try:
    from youtube_transcript_api.proxies import GenericProxyConfig
except ImportError:  # pragma: no cover
    GenericProxyConfig = None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 30


class ScrapeError(Exception):
    """An expected scraper failure with a user-readable category."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        expected_date: dt.date | None = None,
        actual_date: dt.date | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.expected_date = expected_date
        self.actual_date = actual_date


@dataclass(frozen=True)
class ReportContext:
    target_date: dt.date
    asia_date: dt.date
    west_date: dt.date
    sydney_date: dt.date


@dataclass
class ScrapeResult:
    source: str
    status: str
    reason: str = ""
    content: str = ""
    title: str = ""
    url: str = ""
    expected_date: dt.date | None = None
    actual_date: dt.date | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS"


def today_in_taipei() -> dt.date:
    taipei_tz = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.now(taipei_tz).date()


def previous_business_day(day: dt.date) -> dt.date:
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def build_context(target_date: dt.date) -> ReportContext:
    return ReportContext(
        target_date=target_date,
        asia_date=target_date,
        west_date=previous_business_day(target_date - dt.timedelta(days=1)),
        sydney_date=target_date,
    )


def require_http_parser() -> None:
    missing = []
    if requests is None:
        missing.append("requests")
    if BeautifulSoup is None:
        missing.append("beautifulsoup4")
    if missing:
        raise ScrapeError(
            "DEPENDENCY_ERROR",
            "缺少必要套件：" + ", ".join(missing) + "。請先執行 pip install -r requirements.txt",
        )


def require_pdf() -> None:
    if pdfplumber is None:
        raise ScrapeError(
            "DEPENDENCY_ERROR",
            "缺少 PDF 解析套件：pdfplumber。請先執行 pip install -r requirements.txt",
        )


def require_factset_dependencies() -> None:
    missing = []
    if requests is None:
        missing.append("requests")
    if YouTubeTranscriptApi is None or TextFormatter is None:
        missing.append("youtube-transcript-api")
    if missing:
        raise ScrapeError(
            "DEPENDENCY_ERROR",
            "缺少 YouTube 相關套件：" + ", ".join(missing) + "。請先執行 pip install -r requirements.txt",
        )


def _search_youtube_dict(partial, search_key: str):
    """Breadth-first search for every value under ``search_key`` in nested JSON."""
    stack = [partial]
    while stack:
        current = stack.pop(0)
        if isinstance(current, dict):
            for key, value in current.items():
                if key == search_key:
                    yield value
                else:
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def fetch_factset_channel_videos(limit: int = 15) -> list[dict]:
    """Return [{"title", "videoId"}, ...] for the FactSet YouTube channel.

    scrapetube 2.6.0 cannot parse YouTube's current ``lockupViewModel`` layout
    (it only looks for the now-removed ``videoRenderer``), so we fetch the
    channel page and parse ytInitialData directly, with a legacy fallback.
    """
    assert requests is not None
    url = "https://www.youtube.com/@FactSetSolutions/videos"
    session = requests.Session()
    session.headers["User-Agent"] = HEADERS["User-Agent"]
    session.headers["Accept-Language"] = "en"
    session.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
    try:
        resp = session.get(url, params={"ucbcb": 1}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError("NETWORK_ERROR", f"網路或 HTTP 請求失敗：{exc}") from exc
    finally:
        session.close()

    html = resp.text
    marker = "var ytInitialData = "
    start = html.find(marker)
    if start == -1:
        raise ScrapeError("PARSE_ERROR", "FactSet 頻道頁找不到 ytInitialData")
    start += len(marker)
    end = html.find("};", start)
    if end == -1:
        raise ScrapeError("PARSE_ERROR", "FactSet ytInitialData 格式異常")
    try:
        data = json.loads(html[start:end] + "}")
    except json.JSONDecodeError as exc:
        raise ScrapeError("PARSE_ERROR", f"FactSet ytInitialData 解析失敗：{exc}") from exc

    videos: list[dict] = []

    # Current layout: lockupViewModel.
    for lv in _search_youtube_dict(data, "lockupViewModel"):
        if not isinstance(lv, dict):
            continue
        video_id = lv.get("contentId")
        title = (
            lv.get("metadata", {})
            .get("lockupMetadataViewModel", {})
            .get("title", {})
            .get("content")
        )
        if video_id and title:
            videos.append({"title": title, "videoId": video_id})
            if len(videos) >= limit:
                return videos
    if videos:
        return videos

    # Legacy fallback: videoRenderer.
    for vr in _search_youtube_dict(data, "videoRenderer"):
        if not isinstance(vr, dict):
            continue
        video_id = vr.get("videoId")
        runs = vr.get("title", {}).get("runs", [])
        title = "".join(run.get("text", "") for run in runs)
        if video_id and title:
            videos.append({"title": title, "videoId": video_id})
            if len(videos) >= limit:
                break
    return videos


_RELAXED_SSL_SESSION = None


def _relaxed_ssl_session():
    """Session that keeps CA + hostname verification but relaxes the
    VERIFY_X509_STRICT structural check, which newer OpenSSL enables by
    default and which rejects some banks' otherwise-valid certs (e.g. those
    missing a Subject Key Identifier extension)."""
    global _RELAXED_SSL_SESSION
    if _RELAXED_SSL_SESSION is not None:
        return _RELAXED_SSL_SESSION

    import ssl

    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context

    class _RelaxedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = create_urllib3_context()
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            kwargs["ssl_context"] = ctx
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _RelaxedAdapter())
    _RELAXED_SSL_SESSION = session
    return session


def _requester(relaxed_ssl: bool):
    return _relaxed_ssl_session() if relaxed_ssl else requests


def fetch_text(url: str, *, verify: bool = True, encoding: str | None = None, relaxed_ssl: bool = False) -> str:
    require_http_parser()
    assert requests is not None
    try:
        response = _requester(relaxed_ssl).get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=verify)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError("NETWORK_ERROR", f"網路或 HTTP 請求失敗：{exc}") from exc

    if encoding:
        response.encoding = encoding
    return response.text


def fetch_bytes(url: str, *, verify: bool = True, relaxed_ssl: bool = False) -> bytes:
    require_http_parser()
    assert requests is not None
    try:
        response = _requester(relaxed_ssl).get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=verify)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError("NETWORK_ERROR", f"網路或 HTTP 請求失敗：{exc}") from exc
    return response.content


def parse_date(value: str, formats: list[str] | None = None) -> dt.date:
    cleaned = re.sub(r"\s+", " ", value.strip())
    cleaned = cleaned.replace("上午", "").replace("下午", "").strip()
    cleaned = re.sub(r"\s+(a\.m\.|p\.m\.).*$", "", cleaned, flags=re.IGNORECASE)
    formats = formats or []
    fallback_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y%m%d",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y",
        "%Y年%m月%d日",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats + fallback_formats:
        try:
            return dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass

    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", cleaned)
    if iso_match:
        return dt.datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()

    raise ScrapeError("PARSE_ERROR", f"無法解析日期：{value!r}")


def parse_month_day_with_year(value: str, year: int) -> dt.date:
    cleaned = re.sub(r"\s+", " ", value.strip())
    candidates = [
        (f"{cleaned}, {year}", "%B %d, %Y"),   # May 21, 2026
        (f"{cleaned}, {year}", "%b %d, %Y"),   # May 21, 2026 (abbrev month)
        (f"{cleaned}/{year}", "%m/%d/%Y"),     # 05/21/2026
        (f"{cleaned}-{year}", "%d-%b-%Y"),     # 21-May (YouTube lockup titles)
        (f"{cleaned}-{year}", "%d-%B-%Y"),     # 21-May (full month)
        (f"{cleaned} {year}", "%d %b %Y"),     # 21 May
        (f"{cleaned} {year}", "%d %B %Y"),     # 21 May (full month)
    ]
    for text, fmt in candidates:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ScrapeError("PARSE_ERROR", f"無法解析月日格式：{value!r}")


def require_date(source: str, actual: dt.date, expected: dt.date) -> None:
    if actual != expected:
        raise ScrapeError(
            "DATE_MISMATCH",
            f"{source} 日期不符：頁面最新日期是 {actual.isoformat()}，預期日期是 {expected.isoformat()}",
            expected_date=expected,
            actual_date=actual,
        )


def require_text(value: str | None, description: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ScrapeError("NO_DATA", f"沒有爬到資料：{description}")
    return text


def soup_from_url(url: str, *, verify: bool = True, encoding: str | None = None, relaxed_ssl: bool = False):
    html = fetch_text(url, verify=verify, encoding=encoding, relaxed_ssl=relaxed_ssl)
    assert BeautifulSoup is not None
    return BeautifulSoup(html, "html.parser")


def extract_pdf_text(pdf_bytes: bytes, *, page_indexes: list[int] | None = None, skip_last_pages: int = 0) -> str:
    require_pdf()
    require_text(pdf_bytes[:8].decode("latin1", errors="ignore"), "PDF 回傳內容是空的")
    if not pdf_bytes.lstrip().startswith(b"%PDF"):
        raise ScrapeError("NO_DATA", "PDF 下載失敗：回傳內容不是 PDF")

    assert pdfplumber is not None
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_indexes is None:
                pages = pdf.pages
                if skip_last_pages:
                    pages = pages[:-skip_last_pages]
            else:
                pages = []
                for index in page_indexes:
                    if index >= len(pdf.pages):
                        raise ScrapeError("NO_DATA", f"PDF 頁數不足，找不到第 {index + 1} 頁")
                    pages.append(pdf.pages[index])

            text = "\n".join((page.extract_text() or "") for page in pages).strip()
    except ScrapeError:
        raise
    except Exception as exc:
        raise ScrapeError("PARSE_ERROR", f"PDF 解析失敗：{exc}") from exc

    return require_text(text, "PDF 中沒有可抽取文字")


def success(source: str, content: str, *, title: str = "", url: str = "", expected_date: dt.date | None = None, actual_date: dt.date | None = None) -> ScrapeResult:
    return ScrapeResult(
        source=source,
        status="SUCCESS",
        content=require_text(content, f"{source} 內容為空"),
        title=title.strip(),
        url=url,
        expected_date=expected_date,
        actual_date=actual_date,
    )


def failure(source: str, error: Exception, *, expected_date: dt.date | None = None, actual_date: dt.date | None = None) -> ScrapeResult:
    if isinstance(error, ScrapeError):
        return ScrapeResult(
            source=source,
            status=error.kind,
            reason=error.message,
            expected_date=expected_date or error.expected_date,
            actual_date=actual_date or error.actual_date,
        )
    # Collapse multi-line exception messages (e.g. youtube-transcript-api) to one line.
    reason_text = " ".join(str(error).split())
    return ScrapeResult(
        source=source,
        status="UNKNOWN_ERROR",
        reason=f"未預期錯誤：{reason_text}",
        expected_date=expected_date,
        actual_date=actual_date,
        detail=traceback.format_exc(),
    )


def scrape_cnyes(ctx: ReportContext) -> ScrapeResult:
    source = "cnyes"
    expected = ctx.asia_date
    try:
        url = "https://news.cnyes.com/tag/%E7%BE%8E%E8%82%A1%E7%9B%A4%E5%BE%8C"
        soup = soup_from_url(url)
        first_li = soup.find("ul").find("li") if soup.find("ul") else None
        if first_li is None:
            raise ScrapeError("NO_DATA", "找不到鉅亨網新聞列表第一筆")

        time_tag = first_li.find("time")
        if time_tag is None:
            raise ScrapeError("NO_DATA", "找不到鉅亨網新聞日期")

        date_text = time_tag.get_text(strip=True)
        actual = parse_date(f"{expected.year}-{date_text}", ["%Y-%m-%d", "%Y-%m/%d", "%Y-%m.%d"])
        if actual > expected:
            actual = actual.replace(year=actual.year - 1)
        require_date(source, actual, expected)

        link_tag = first_li.find("a", href=True)
        if link_tag is None:
            raise ScrapeError("NO_DATA", "找不到鉅亨網文章連結")

        article_url = urljoin(url, str(link_tag["href"]))
        article_soup = soup_from_url(article_url)
        og_title = article_soup.find("meta", property="og:title")
        title = str(og_title["content"]).strip() if og_title and og_title.get("content") else ""
        if not title:
            title_tag = article_soup.find("h1")
            title = title_tag.get_text(strip=True) if title_tag else ""

        article_main = article_soup.find("main", id="article-container")
        if article_main is None:
            raise ScrapeError("NO_DATA", "找不到鉅亨網文章正文區塊")
        paragraphs = [p.get_text(strip=True) for p in article_main.find_all("p")]
        content = "\n\n".join(p for p in paragraphs if p)
        return success(source, content, title=title, url=article_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_taishin(ctx: ReportContext) -> ScrapeResult:
    source = "taishin"
    expected = ctx.asia_date
    try:
        url = "https://www.taishinbank.com.tw/eServiceA/invst/servlet/servlet.invst.IReportServlet?action=list&cat=522&rows=5"
        raw = fetch_text(url, relaxed_ssl=True)
        match = re.search(r"callback\d+\((.*)\)\s*$", raw.strip(), re.DOTALL)
        if match is None:
            raise ScrapeError("PARSE_ERROR", "台新回傳內容不是預期的 JSONP 格式")

        payload = json.loads(match.group(1))
        rows = payload.get("data") or []
        if not rows:
            raise ScrapeError("NO_DATA", "台新列表沒有任何報告")

        first = rows[0]
        actual = parse_date(str(first.get("BEGINDATE", "")))
        require_date(source, actual, expected)

        report_id = first.get("KMSEQNO")
        if not report_id:
            raise ScrapeError("NO_DATA", "台新列表缺少 KMSEQNO")

        pdf_url = f"https://www.taishinbank.com.tw/eServiceA/invst/servlet/servlet.invst.IReportServlet?action=file&no={report_id}"
        text = extract_pdf_text(fetch_bytes(pdf_url, relaxed_ssl=True), page_indexes=[2])
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_cathay(ctx: ReportContext) -> ScrapeResult:
    source = "cathay"
    expected = ctx.asia_date
    try:
        url = "https://www.cathaybk.com.tw/cathaybk/personal/wealth/market/report/"
        soup = soup_from_url(url, relaxed_ssl=True)
        target_h3 = next((h3 for h3 in soup.find_all("h3") if "財富管理日報" in h3.get_text()), None)
        if target_h3 is None:
            raise ScrapeError("NO_DATA", "找不到國泰財富管理日報")

        date_div = target_h3.find_next("div", class_="cubinvest-l-dateList__date")
        if date_div is None:
            raise ScrapeError("NO_DATA", "找不到國泰報告日期")
        actual = parse_date(date_div.get_text(strip=True))
        require_date(source, actual, expected)

        a_tag = target_h3.find_next("a", href=True)
        if a_tag is None:
            raise ScrapeError("NO_DATA", "找不到國泰 PDF 連結")
        pdf_url = urljoin("https://www.cathaybk.com.tw", a_tag["href"])
        text = extract_pdf_text(fetch_bytes(pdf_url, relaxed_ssl=True), page_indexes=[0])
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_edwardjones(ctx: ReportContext) -> ScrapeResult:
    source = "edwardjones"
    expected = ctx.west_date
    try:
        url = "https://www.edwardjones.com/us-en/market-news-insights/stock-market-news/daily-market-recap"
        soup = soup_from_url(url)
        article = soup.find("div", class_="rich-text relative")
        if article is None:
            raise ScrapeError("NO_DATA", "找不到 Edward Jones 文章正文")

        first_p = article.find("p")
        date_text = first_p.get_text(" ", strip=True) if first_p else ""
        date_match = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", date_text)
        if date_match:
            actual = parse_date(date_match.group(0), ["%m/%d/%Y"])
        else:
            month_match = re.search(r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b", date_text)
            if not month_match:
                raise ScrapeError("PARSE_ERROR", f"找不到 Edward Jones 日期：{date_text!r}")
            actual = parse_date(month_match.group(0), ["%B %d, %Y"])
        require_date(source, actual, expected)

        text = article.get_text("\n", strip=True)
        return success(source, text, url=url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_nomura(ctx: ReportContext) -> ScrapeResult:
    source = "nomura"
    expected = ctx.asia_date
    try:
        url = "https://www.nomura-am.co.jp/market/marketcomment/"
        soup = soup_from_url(url, encoding="utf-8")
        tab = soup.find("div", {"id": "tab-1"})
        if tab is None:
            raise ScrapeError("NO_DATA", "找不到野村 market comment 列表")

        target = next((a for a in tab.find_all("a") if "石黒英之のMarket Navi" in a.get_text()), None)
        if target is None:
            raise ScrapeError("NO_DATA", "找不到石黒英之のMarket Navi")

        date_span = target.find_previous("span", class_="archive_date")
        if date_span is None or not date_span.get("postdate"):
            raise ScrapeError("NO_DATA", "找不到野村報告日期")
        actual = parse_date(str(date_span.get("postdate")))
        require_date(source, actual, expected)

        target_href = target.get("href") or ""
        # Fast path: the listing link points directly to the PDF (current layout).
        if target_href.lower().split("?")[0].endswith(".pdf"):
            pdf_url = urljoin("https://www.nomura-am.co.jp", target_href)
        else:
            # Legacy path: link is an article HTML page that contains the PDF.
            article_url = urljoin("https://www.nomura-am.co.jp", target_href)
            article = soup_from_url(article_url, encoding="utf-8")
            pdf_link = next(
                (a for a in article.find_all("a")
                 if (a.get("href") or "").lower().split("?")[0].endswith(".pdf")
                 and "/market/marketcomment/" in (a.get("href") or "")),
                None,
            )
            if pdf_link is None:
                raise ScrapeError("NO_DATA", "找不到野村報告 PDF 連結")
            pdf_url = urljoin("https://www.nomura-am.co.jp", pdf_link["href"])
        text = extract_pdf_text(fetch_bytes(pdf_url), page_indexes=[0])
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_factset(ctx: ReportContext) -> ScrapeResult:
    source = "factset"
    expected = ctx.west_date
    try:
        require_factset_dependencies()
        videos = fetch_factset_channel_videos(limit=15)

        # Collect all "Evening/Weekly Market Recap" candidates with parseable dates.
        candidates: list[tuple[dict, str, dt.date]] = []
        for video in videos:
            title = video.get("title", "")
            if not re.search(r"(Evening|Weekly)\s+Market\s+Recap", title, re.IGNORECASE):
                continue
            date_part = title.split(",")[-1].strip()
            try:
                actual = parse_month_day_with_year(date_part, expected.year)
            except ScrapeError:
                continue
            if actual > expected:
                actual = actual.replace(year=actual.year - 1)
            candidates.append((video, title, actual))

        if not candidates:
            raise ScrapeError("NO_DATA", "FactSet YouTube 頻道找不到市場回顧影片")

        # Date-check against the most recent candidate.
        require_date(source, candidates[0][2], expected)

        proxy_url = os.environ.get("YOUTUBE_PROXY_URL")
        api_kwargs = {}
        if proxy_url and GenericProxyConfig is not None:
            api_kwargs["proxy_config"] = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
        assert YouTubeTranscriptApi is not None and TextFormatter is not None
        api = YouTubeTranscriptApi(**api_kwargs)

        # Try every candidate whose date matches — there is usually one, but a
        # channel may re-upload; try all before giving up.
        date_matches = [(v, t, d) for v, t, d in candidates if d == expected]
        last_transcript_exc: Exception | None = None
        for vid, ttl, dat in date_matches:
            video_id = vid.get("videoId")
            if not video_id:
                continue
            try:
                transcript = api.fetch(video_id)
                text = TextFormatter().format_transcript(transcript).strip()
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                return success(source, text, title=ttl, url=video_url, expected_date=expected, actual_date=dat)
            except Exception as exc:  # noqa: BLE001
                last_transcript_exc = exc

        if last_transcript_exc is not None:
            reason = " ".join(str(last_transcript_exc).split())
            raise ScrapeError("TRANSCRIPT_ERROR", f"無法取得 FactSet 字幕：{reason}") from last_transcript_exc
        raise ScrapeError("NO_DATA", "FactSet 日期符合的影片缺少 videoId")
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_jin10(ctx: ReportContext) -> ScrapeResult:
    source = "jin10"
    expected = ctx.asia_date
    try:
        url = "https://xnews.jin10.com/30"
        soup = soup_from_url(url)
        list_box = soup.find("div", {"class": "jin10-news-list"})
        if list_box is None:
            raise ScrapeError("NO_DATA", "找不到金十數據新聞列表")

        item = list_box.find("div", {"class": "jin10-news-list-item-info"})
        if item is None:
            raise ScrapeError("NO_DATA", "金十數據新聞列表沒有項目")

        a_tag = item.find("a", href=True)
        if a_tag is None:
            raise ScrapeError("NO_DATA", "找不到金十數據文章連結")

        article_url = urljoin(url, a_tag["href"])
        article_soup = soup_from_url(article_url)
        title_div = article_soup.find("div", {"class": "news-app_title"})
        if title_div is None:
            raise ScrapeError("NO_DATA", "找不到金十數據文章日期")
        date_text = title_div.get_text(" ", strip=True).split("|")[-1].strip()
        actual = parse_date(date_text, ["%Y年%m月%d日"])
        require_date(source, actual, expected)

        content_div = article_soup.find("div", class_="jin10-news-cdetails-content")
        if content_div is None:
            raise ScrapeError("NO_DATA", "找不到金十數據文章正文")
        text = content_div.get_text("\n", strip=True)
        return success(source, text, url=article_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_fx678(ctx: ReportContext) -> ScrapeResult:
    source = "fx678"
    expected = ctx.asia_date
    try:
        url = "https://news.fx678.com/column/cjzc"
        soup = soup_from_url(url)
        top_list = soup.find("ul", class_="top1 list")
        if top_list is None:
            raise ScrapeError("NO_DATA", "找不到匯通財經早餐列表")

        a_tag = top_list.find("a", href=True)
        h3_tag = top_list.find("h3")
        if a_tag is None or h3_tag is None:
            raise ScrapeError("NO_DATA", "匯通財經早餐缺少文章連結或標題")

        date_text = h3_tag.get_text(strip=True).split("：")[0]
        actual = parse_date(f"{expected.year}年{date_text}", ["%Y年%m月%d日财经早餐"])
        if actual > expected:
            actual = actual.replace(year=actual.year - 1)
        require_date(source, actual, expected)

        article_url = urljoin("https://news.fx678.com", a_tag["href"])
        article_soup = soup_from_url(article_url)
        content = article_soup.find("div", class_="content")
        if content is None:
            raise ScrapeError("NO_DATA", "找不到匯通財經早餐正文")
        text = content.get_text("\n", strip=True)
        return success(source, text, url=article_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_investrade(ctx: ReportContext) -> ScrapeResult:
    source = "investrade"
    expected = ctx.west_date
    try:
        url = "https://www.investrade.com/category/daily-market-report/"
        soup = soup_from_url(url, verify=False)

        target = None
        actual = None
        for a_tag in soup.find_all("a", href=True):
            link_text = a_tag.get_text(" ", strip=True)
            # Article links look like "Market Review: May 21, 2026"; the
            # category nav link ("Daily Market Report") has no colon date.
            if not re.match(r"Market Review\s*:", link_text, re.IGNORECASE):
                continue
            date_text = link_text.split(":", 1)[-1].strip()
            try:
                actual = parse_date(date_text, ["%B %d, %Y"])
            except Exception:
                continue
            target = a_tag
            break
        if target is None or actual is None:
            raise ScrapeError("NO_DATA", "找不到 Investrade Market Review")
        require_date(source, actual, expected)

        article_url = target["href"]
        article_soup = soup_from_url(article_url, verify=False)
        content = (
            article_soup.find("div", class_="entry-content content")
            or article_soup.find("div", class_=re.compile(r"theme-post-content"))
            or article_soup.find("div", class_=re.compile(r"entry-content"))
        )
        if content is None:
            raise ScrapeError("NO_DATA", "找不到 Investrade 文章正文")
        text = content.get_text("\n", strip=True)
        return success(source, text, url=article_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_westpac(ctx: ReportContext) -> ScrapeResult:
    source = "westpac"
    expected = ctx.sydney_date
    try:
        require_http_parser()
        require_pdf()
        assert requests is not None
        base_url = "https://www.westpaciq.com.au"
        lib_url = "https://library.westpaciq.com.au"
        url = f"{base_url}/topic.morningreport"

        # The topic page is ~230 KB; give it extra time on slow connections.
        try:
            response = requests.get(url, headers=HEADERS, timeout=60, verify=True)
            response.raise_for_status()
            html = response.text
        except requests.RequestException as exc:
            raise ScrapeError("NETWORK_ERROR", f"網路或 HTTP 請求失敗：{exc}") from exc

        match = re.search(r"var\s+JSONobject\s*=\s*'(.+?)';", html, re.DOTALL)
        if match is None:
            raise ScrapeError("PARSE_ERROR", "Westpac landing page 找不到 JSONobject")

        payload = json.loads(match.group(1))
        articles = payload.get("articleAggregator") or []
        if not articles:
            raise ScrapeError("NO_DATA", "Westpac JSONobject 沒有文章")

        first = articles[0]
        actual = parse_date(str(first.get("publishdate", "")))
        require_date(source, actual, expected)

        # Fast path: PDF URL is often embedded in the executiveSummary HTML,
        # saving a round-trip to the article page.
        pdf_url: str | None = None
        exec_summary = first.get("executiveSummary", "")
        if exec_summary:
            pdf_match = re.search(r"""href=['"]([^'"]+\.pdf[^'"]*)['"]""", exec_summary, re.IGNORECASE)
            if pdf_match:
                href = pdf_match.group(1)
                pdf_url = href if href.startswith("http") else urljoin(lib_url, href)

        # Slow path fallback: scrape the article page for a PDF link.
        if pdf_url is None:
            article_path = first.get("articlePath")
            if not article_path:
                raise ScrapeError("NO_DATA", "Westpac 文章缺少 articlePath 且 executiveSummary 無 PDF 連結")
            article_url = urljoin(base_url, article_path)
            article_soup = soup_from_url(article_url)
            a_tag = article_soup.find("a", href=re.compile(r"\.pdf(?:\?|$)", re.IGNORECASE))
            if a_tag is None:
                raise ScrapeError("NO_DATA", "Westpac 文章頁找不到 PDF 連結")
            href = a_tag["href"]
            pdf_url = href if href.startswith("http") else urljoin(base_url, href)

        text = extract_pdf_text(fetch_bytes(pdf_url), skip_last_pages=3)
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_uob(ctx: ReportContext) -> ScrapeResult:
    """UOB Morning Overview — direct date-substituted PDF URL.

    URL pattern: https://www.uobgroup.com/assets/web-resources/research/pdf/MO_YYMMDD.pdf
    where YYMMDD is the 2-digit-year publish date. Always drops the last 6 pages.
    """
    source = "uob"
    expected = ctx.asia_date
    try:
        require_http_parser()
        require_pdf()
        ymd = expected.strftime("%y%m%d")
        pdf_url = f"https://www.uobgroup.com/assets/web-resources/research/pdf/MO_{ymd}.pdf"
        text = extract_pdf_text(fetch_bytes(pdf_url), skip_last_pages=6)
        # The URL itself encodes the date; if fetch_bytes succeeds we trust it.
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=expected)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


def scrape_mizuho(ctx: ReportContext) -> ScrapeResult:
    """Mizuho Daily (Singapore Macro) — find newest PDF link, drop last 3 pages."""
    source = "mizuho"
    expected = ctx.asia_date
    try:
        require_http_parser()
        require_pdf()
        url = "https://www.mizuhogroup.com/asia-pacific/singapore/macro/daily"
        soup = soup_from_url(url)

        target = None
        actual = None
        link_re = re.compile(r"Mizuho\s+Daily\s+(\d{1,2}\s+\w+\s+\d{4})", re.IGNORECASE)
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if ".pdf" not in href.lower():
                continue
            text_match = link_re.search(a_tag.get_text(" ", strip=True))
            if not text_match:
                continue
            try:
                actual = dt.datetime.strptime(text_match.group(1), "%d %B %Y").date()
            except ValueError:
                continue
            target = a_tag
            break

        if target is None or actual is None:
            raise ScrapeError("NO_DATA", "找不到 Mizuho Daily PDF 連結")
        require_date(source, actual, expected)

        pdf_url = target["href"]
        text = extract_pdf_text(fetch_bytes(pdf_url), skip_last_pages=3)
        return success(source, text, url=pdf_url, expected_date=expected, actual_date=actual)
    except Exception as exc:
        return failure(source, exc, expected_date=expected)


SCRAPERS: dict[str, Callable[[ReportContext], ScrapeResult]] = {
    "cnyes": scrape_cnyes,
    "taishin": scrape_taishin,
    "cathay": scrape_cathay,
    "edwardjones": scrape_edwardjones,
    "nomura": scrape_nomura,
    "factset": scrape_factset,
    "jin10": scrape_jin10,
    "fx678": scrape_fx678,
    "investrade": scrape_investrade,
    "westpac": scrape_westpac,
    "uob": scrape_uob,
    "mizuho": scrape_mizuho,
}


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "report"


def format_report_file(result: ScrapeResult) -> str:
    header = [
        f"Source: {result.source}",
        f"Status: {result.status}",
    ]
    if result.title:
        header.append(f"Title: {result.title}")
    if result.url:
        header.append(f"URL: {result.url}")
    if result.expected_date:
        header.append(f"Expected date: {result.expected_date.isoformat()}")
    if result.actual_date:
        header.append(f"Actual date: {result.actual_date.isoformat()}")
    header.append("")
    return "\n".join(header) + result.content.strip() + "\n"


def format_summary(results: list[ScrapeResult], output_dir: Path) -> str:
    ok_count = sum(1 for result in results if result.ok)
    lines = [
        "Scrape summary",
        f"Output directory: {output_dir}",
        f"Successful reports: {ok_count}/{len(results)}",
        "",
    ]
    for result in results:
        expected = result.expected_date.isoformat() if result.expected_date else "-"
        actual = result.actual_date.isoformat() if result.actual_date else "-"
        lines.append(f"- {result.source}: {result.status} (expected={expected}, actual={actual})")
        if result.reason:
            lines.append(f"  Reason: {result.reason}")
    lines.append("")
    return "\n".join(lines)


def format_errors(results: list[ScrapeResult]) -> str:
    failed = [result for result in results if not result.ok]
    if not failed:
        return "No errors.\n"

    lines = ["Scrape errors", ""]
    for result in failed:
        lines.extend(
            [
                f"Source: {result.source}",
                f"Status: {result.status}",
                f"Reason: {result.reason or 'No reason provided'}",
            ]
        )
        if result.expected_date:
            lines.append(f"Expected date: {result.expected_date.isoformat()}")
        if result.actual_date:
            lines.append(f"Actual date: {result.actual_date.isoformat()}")
        if result.detail:
            lines.extend(["Detail:", result.detail.strip()])
        lines.append("")
    return "\n".join(lines)


OUTPUT_BASE = Path("scraped_reports")


def write_outputs(results: list[ScrapeResult], target_date: dt.date) -> Path:
    """Write all reports for ``target_date`` to ``scraped_reports/<date>/``.

    There is exactly one output folder per day. To avoid stale files from a
    previous (partial) run sticking around, all existing .txt files in the
    target folder are cleared before this run's files are written.
    """
    run_dir = OUTPUT_BASE / target_date.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Clear stale files from previous runs of the same date.
    for stale in run_dir.glob("*.txt"):
        try:
            stale.unlink()
        except OSError:
            pass

    for result in results:
        if not result.ok:
            continue
        report_path = run_dir / f"{safe_filename(result.source)}.txt"
        report_path.write_text(format_report_file(result), encoding="utf-8")

    (run_dir / "summary.txt").write_text(format_summary(results, run_dir), encoding="utf-8")
    (run_dir / "errors.txt").write_text(format_errors(results), encoding="utf-8")
    return run_dir


def parse_sources(raw_sources: str) -> list[str]:
    if raw_sources.lower() == "all":
        return list(SCRAPERS.keys())
    sources = [source.strip().lower() for source in raw_sources.split(",") if source.strip()]
    unknown = sorted(set(sources) - set(SCRAPERS))
    if unknown:
        raise ValueError(f"Unknown source(s): {', '.join(unknown)}. Available: {', '.join(SCRAPERS)}")
    return sources


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape daily market reports and save each report as an independent txt file.")
    parser.add_argument("--date", default=today_in_taipei().isoformat(), help="Target Asia/Taipei date, format YYYY-MM-DD. Default: today.")
    parser.add_argument("--sources", default="all", help="Comma-separated source names, or all. Default: all")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if any source fails. Default only fails when no source succeeds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        target_date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print("日期格式錯誤：--date 必須是 YYYY-MM-DD，例如 2026-05-22", file=sys.stderr)
        return 2

    try:
        sources = parse_sources(args.sources)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ctx = build_context(target_date)
    results: list[ScrapeResult] = []

    print(f"Target date: {ctx.target_date.isoformat()}")
    print(f"Expected west-market report date: {ctx.west_date.isoformat()}")
    print(f"Sources: {', '.join(sources)}")
    print("")

    for source in sources:
        print(f"[{source}] scraping...")
        result = SCRAPERS[source](ctx)
        results.append(result)
        if result.ok:
            print(f"[{source}] SUCCESS")
        else:
            print(f"[{source}] {result.status}: {result.reason}")

    output_dir = write_outputs(results, target_date)
    print("")
    print(f"Output written to: {output_dir}")
    print(f"Summary: {output_dir / 'summary.txt'}")
    print(f"Errors: {output_dir / 'errors.txt'}")

    successful = sum(1 for result in results if result.ok)
    if args.strict and successful != len(results):
        return 1
    if successful == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
