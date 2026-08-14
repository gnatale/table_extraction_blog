#!/usr/bin/env python3
"""Extract tables from a PDF using Docling and save them as CSV and HTML files."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
    TableStructureV2Options,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

DEFAULT_OUTPUT_DIR = "docling_output"
TABLE_MODES = ("accurate", "fast", "v2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract tables from a PDF with Docling and write CSV and HTML files.",
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
        "--mode",
        choices=TABLE_MODES,
        default="accurate",
        help=(
            "TableFormer extraction mode: accurate (default), fast, or v2 "
            "(v2 ignores PDF cell boundaries when they span merged columns)"
        ),
    )
    return parser.parse_args()


def build_pipeline_options(mode: str) -> PdfPipelineOptions:
    if mode == "fast":
        table_options = TableStructureOptions(mode=TableFormerMode.FAST)
    elif mode == "v2":
        table_options = TableStructureV2Options(do_cell_matching=False)
    else:
        table_options = TableStructureOptions(mode=TableFormerMode.ACCURATE)

    return PdfPipelineOptions(
        do_table_structure=True,
        table_structure_options=table_options,
    )


def build_document_converter(mode: str) -> DocumentConverter:
    pipeline_options = build_pipeline_options(mode)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        },
    )


def table_metadata(table: Any, *, table_index: int, mode: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "table_index": table_index,
        "mode": mode,
        "page": None,
        "bbox": None,
    }

    if not getattr(table, "prov", None):
        return metadata

    provenance = table.prov[0]
    metadata["page"] = getattr(provenance, "page_no", None)

    bbox = getattr(provenance, "bbox", None)
    if bbox is not None:
        if hasattr(bbox, "model_dump"):
            metadata["bbox"] = bbox.model_dump()
        elif hasattr(bbox, "as_tuple"):
            left, top, right, bottom = bbox.as_tuple()
            metadata["bbox"] = {
                "l": left,
                "t": top,
                "r": right,
                "b": bottom,
            }
        else:
            metadata["bbox"] = str(bbox)

    return metadata


def export_tables(
    pdf_path: Path,
    output_dir: Path,
    *,
    mode: str = "accurate",
) -> list[Path]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    table_dir = output_dir / pdf_path.stem
    table_dir.mkdir(parents=True, exist_ok=True)

    converter = build_document_converter(mode)
    conversion_result = converter.convert(pdf_path)
    document = conversion_result.document
    tables = document.tables

    if not tables:
        raise RuntimeError("Docling found no tables in the document.")

    csv_paths: list[Path] = []
    raw_tables: list[dict[str, Any]] = []

    for table_index, table in enumerate(tables, start=1):
        csv_path = table_dir / f"table_{table_index}.csv"
        html_path = table_dir / f"table_{table_index}.html"

        table_df = table.export_to_dataframe(doc=document)
        table_df.to_csv(csv_path, index=False)
        html_path.write_text(table.export_to_html(doc=document), encoding="utf-8")

        csv_paths.append(csv_path)
        raw_tables.append(table_metadata(table, table_index=table_index, mode=mode))

    raw_dir = table_dir / "raw_output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "docling_tables.json").write_text(
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
            mode=args.mode,
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
