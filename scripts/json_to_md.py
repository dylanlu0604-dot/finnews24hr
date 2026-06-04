"""Convert docs/data.json into 5 markdown files under notebooklm/.

Reads docs/data.json (produced by scraper/fetch_sina.py) and splits the
items into exactly five markdown files (new01.md ... new05.md) such that
the total text volume is balanced across files. Items remain in their
original chronological order; only the cut points differ.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs" / "data.json"
OUT_DIR = ROOT / "notebooklm"
NUM_FILES = 5
FILE_TEMPLATE = "new{idx:02d}.md"


def item_weight(item: dict) -> int:
    text = item.get("text") or ""
    return len(text)


def item_to_md(item: dict) -> str:
    time = item.get("time", "")
    tags = item.get("tags") or []
    text = (item.get("text") or "").strip()
    tag_str = " ".join(f"#{t}" for t in tags) if tags else ""
    header = f"### {time}"
    if tag_str:
        header += f"  {tag_str}"
    return f"{header}\n\n{text}\n"


def split_indices(weights: list[int], n: int) -> list[int]:
    """Return n+1 boundary indices that split weights into n contiguous
    buckets of as-equal-as-possible total weight."""
    total = sum(weights)
    target = total / n
    boundaries = [0]
    acc = 0
    bucket = 1
    for i, w in enumerate(weights):
        acc += w
        remaining_items = len(weights) - (i + 1)
        remaining_buckets = n - bucket
        # Cut here if we've reached the running target AND leave at least
        # one item per remaining bucket.
        if bucket < n and acc >= target * bucket and remaining_items >= remaining_buckets:
            boundaries.append(i + 1)
            bucket += 1
    while len(boundaries) < n:
        boundaries.append(len(weights))
    boundaries.append(len(weights))
    return boundaries


def main() -> None:
    with DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items") or []
    updated = data.get("updated", "")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not items:
        # Still emit five files so workflow output is deterministic.
        for i in range(1, NUM_FILES + 1):
            (OUT_DIR / FILE_TEMPLATE.format(idx=i)).write_text(
                f"# 24HR 財經快訊 - 第 {i} 部分\n\n_更新時間：{updated}_\n\n_本批次無資料。_\n",
                encoding="utf-8",
            )
        return

    weights = [item_weight(it) for it in items]
    boundaries = split_indices(weights, NUM_FILES)

    for i in range(NUM_FILES):
        start, end = boundaries[i], boundaries[i + 1]
        chunk = items[start:end]
        chunk_chars = sum(weights[start:end])
        body_parts = [
            f"# 24HR 財經快訊 - 第 {i + 1} 部分（共 {NUM_FILES} 部分）",
            "",
            f"_更新時間：{updated}_",
            f"_本檔包含 {len(chunk)} 則快訊，約 {chunk_chars} 字_",
            "",
            "---",
            "",
        ]
        for item in chunk:
            body_parts.append(item_to_md(item))
            body_parts.append("---")
            body_parts.append("")
        out_path = OUT_DIR / FILE_TEMPLATE.format(idx=i + 1)
        out_path.write_text("\n".join(body_parts).rstrip() + "\n", encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}: {len(chunk)} items, {chunk_chars} chars")


if __name__ == "__main__":
    main()
