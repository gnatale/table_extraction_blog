#!/usr/bin/env python3
"""Extract tables from a PDF using pdfplumber and save them as CSV files."""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import pdfplumber

DEFAULT_OUTPUT_DIR = "pdfplumber_output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract tables from a PDF with pdfplumber and write CSV files.",
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
        "--text-strategy",
        action="store_true",
        help=(
            "Detect tables using pdfplumber's text strategy "
            "(vertical and horizontal) instead of the default lines strategy. "
            "Output files are labeled with '_text'."
        ),
    )
    return parser.parse_args()


def write_table_csv(table: list[list[str | None]], csv_path: Path) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in table:
            writer.writerow(["" if cell is None else cell for cell in row])


def export_tables(
    pdf_path: Path,
    output_dir: Path,
    *,
    text_strategy: bool = False,
) -> list[Path]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    table_dir = output_dir / pdf_path.stem
    table_dir.mkdir(parents=True, exist_ok=True)

    file_label = "_text" if text_strategy else ""
    table_settings = (
        {"vertical_strategy": "text", "horizontal_strategy": "text"}
        if text_strategy
        else None
    )

    csv_paths: list[Path] = []
    raw_tables: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        table_index = 0
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables(table_settings) or []
            for table in tables:
                table_index += 1
                csv_path = table_dir / f"table_{table_index}{file_label}.csv"
                write_table_csv(table, csv_path)
                csv_paths.append(csv_path)
                raw_tables.append(
                    {
                        "table_index": table_index,
                        "page": page_number,
                        "strategy": "text" if text_strategy else "lines",
                        "rows": table,
                    }
                )

    if not csv_paths:
        raise RuntimeError("pdfplumber found no tables in the document.")

    raw_dir = table_dir / "raw_output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"pdfplumber_tables{file_label}.json").write_text(
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
            text_strategy=args.text_strategy,
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
