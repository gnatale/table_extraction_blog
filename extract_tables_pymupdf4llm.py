#!/usr/bin/env python3
"""Extract tables from a PDF using pymupdf4llm and save them as CSV files."""

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Literal

import pymupdf4llm

DEFAULT_OUTPUT_DIR = "pymupdf4llm_output"
STRATEGIES = ("lines", "lines_strict", "text")
Strategy = Literal["lines", "lines_strict", "text"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract tables from a PDF with pymupdf4llm and write CSV files.",
    )
    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to the input PDF file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f"Output root directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        default="lines",
        help=(
            "Table border detection strategy: lines (default vector graphics), "
            "lines_strict (drawn lines only), or text (word clustering)."
        ),
    )
    return parser.parse_args()


def write_table_csv(table: list[list[str | None]], csv_path: Path) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in table:
            writer.writerow(["" if cell is None else cell for cell in row])


def parse_to_json_result(result: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(result, str):
        return json.loads(result)
    if isinstance(result, list):
        return {"pages": result}
    return result


def export_tables(
    pdf_path: Path,
    output_dir: Path,
    *,
    strategy: Strategy = "lines",
) -> list[Path]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    table_dir = output_dir / pdf_path.stem
    table_dir.mkdir(parents=True, exist_ok=True)

    csv_paths: list[Path] = []
    raw_tables: list[dict] = []

    json_result = pymupdf4llm.to_json(str(pdf_path), table_strategy=strategy)
    data = parse_to_json_result(json_result)

    table_index = 0
    for page_number, page in enumerate(data.get("pages", []), start=1):
        for block in page.get("boxes", []):
            if block.get("boxclass") != "table":
                continue
            table_payload = block.get("table") or {}
            rows = table_payload.get("extract")
            if not rows:
                continue
            table_index += 1
            csv_path = table_dir / f"table_{table_index}_{strategy}.csv"
            write_table_csv(rows, csv_path)
            csv_paths.append(csv_path)
            raw_tables.append(
                {
                    "table_index": table_index,
                    "page": page_number,
                    "strategy": strategy,
                    "bbox": table_payload.get("bbox"),
                    "rows": rows,
                }
            )

    if not csv_paths:
        raise RuntimeError("pymupdf4llm found no tables in the document.")

    raw_dir = table_dir / "raw_output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"pymupdf4llm_tables_{strategy}.json").write_text(
        json.dumps(raw_tables, indent=2),
        encoding="utf-8",
    )

    return csv_paths


def main() -> int:
    args = parse_args()
    try:
        csv_paths = export_tables(
            pdf_path=args.pdf,
            output_dir=args.output_dir,
            strategy=args.strategy,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {len(csv_paths)} table(s):")
    for path in csv_paths:
        print(path)
    return 0


if __name__ == "__main__":
    start = time.perf_counter()
    exit_code = main()
    print(f"Elapsed: {time.perf_counter() - start:.2f}s")
    sys.exit(exit_code)
