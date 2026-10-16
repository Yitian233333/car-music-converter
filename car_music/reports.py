from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from .convert import Result


def csv_safe(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export_results(path: Path, results: list[Result]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["源文件", "结果", "输出文件", "源格式", "说明", "耗时秒"])
        for result in results:
            writer.writerow(csv_safe(v) for v in asdict(result).values())
