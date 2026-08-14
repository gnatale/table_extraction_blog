#!/usr/bin/env python3
"""Extract tables from a PDF using AWS Textract and save them as CSV files."""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from PIL import Image
from textractor import Textractor
from textractor.data.constants import TextractFeatures
from textractor.utils.s3_utils import s3_path_to_bucket_and_prefix, upload_to_s3
from textractor.visualizers.entitylist import EntityList

from env_utils import load_env_file, require_env

load_env_file()

DEFAULT_AWS_PROFILE = require_env("AWS_PROFILE")
DEFAULT_AWS_REGION = require_env("AWS_REGION")
DEFAULT_S3_UPLOAD_PATH = require_env("TEXTRACT_S3_UPLOAD_PATH")
DEFAULT_OUTPUT_DIR = "textract_output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract tables from a PDF with AWS Textract and write CSV files.",
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
        "--s3-upload-path",
        default=DEFAULT_S3_UPLOAD_PATH,
        help="S3 URI prefix for uploading the PDF before async analysis",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_AWS_PROFILE,
        help=f"AWS credentials profile (default: {DEFAULT_AWS_PROFILE})",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_AWS_REGION,
        help=f"AWS region for Textract (default: {DEFAULT_AWS_REGION})",
    )
    return parser.parse_args()


def save_table_page_images(document, table_dir: Path) -> list[Path]:
    """Write PNG overlays (table + cell boxes) for each page that contains a table."""
    tables_by_page: dict[int, list] = {}
    for table in document.tables:
        tables_by_page.setdefault(table.page, []).append(table)

    written: list[Path] = []
    for page_num in sorted(tables_by_page):
        overlay = EntityList(tables_by_page[page_num]).visualize(
            with_text=False,
            with_words=False,
        )
        if isinstance(overlay, list):
            if len(overlay) != 1:
                raise RuntimeError(
                    f"Expected one overlay image for page {page_num}, got {len(overlay)}"
                )
            overlay = overlay[0]
        if not isinstance(overlay, Image.Image):
            raise RuntimeError(f"Unexpected visualize() return type: {type(overlay)}")

        out_path = table_dir / f"page_{page_num}_tables.png"
        overlay.save(out_path)
        written.append(out_path)

    return written


def export_tables(
    pdf_path: Path,
    output_dir: Path,
    s3_upload_path: str,
    profile: str,
    region: str,
) -> tuple[list[Path], list[Path]]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf_stem = pdf_path.stem
    table_dir = output_dir / pdf_stem
    table_dir.mkdir(parents=True, exist_ok=True)

    extractor = Textractor(profile_name=profile, region_name=region)
    # Upload ourselves so we know the exact S3 key and can delete it afterward.
    # Textractor's start_document_analysis uploads under a UUID but never cleans up.
    # Include .pdf so Textractor rasterizes via pdf2image instead of opening as an image.
    s3_file_path = os.path.join(s3_upload_path, f"{uuid.uuid4()}.pdf")
    upload_to_s3(extractor.s3_client, s3_file_path, str(pdf_path.resolve()))

    try:
        document = extractor.start_document_analysis(
            file_source=s3_file_path,
            features=[TextractFeatures.TABLES],
            save_image=True,
        )

        tables = document.tables
        if not tables:
            raise RuntimeError(
                "Textract completed but no tables were found in the document."
            )

        raw_dir = table_dir / "raw_output"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "textract_response.json").write_text(
            json.dumps(document.response, indent=2),
            encoding="utf-8",
        )

        csv_paths: list[Path] = []
        for index, table in enumerate(tables, start=1):
            csv_path = table_dir / f"table_{index}.csv"
            df = table.to_pandas(use_columns=False)
            df.to_csv(csv_path, index=False, header=False)
            csv_paths.append(csv_path)

        image_paths = save_table_page_images(document, table_dir)
        return csv_paths, image_paths
    finally:
        bucket, key = s3_path_to_bucket_and_prefix(s3_file_path)
        extractor.s3_client.delete_object(Bucket=bucket, Key=key)


def main() -> int:
    args = parse_args()
    try:
        csv_paths, image_paths = export_tables(
            pdf_path=args.pdf,
            output_dir=args.output_dir,
            s3_upload_path=args.s3_upload_path,
            profile=args.profile,
            region=args.region,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {len(csv_paths)} table(s):")
    for path in csv_paths:
        print(path)
    print(f"Wrote {len(image_paths)} page overlay image(s):")
    for path in image_paths:
        print(path)
    return 0


if __name__ == "__main__":
    start = time.perf_counter()
    exit_code = main()
    print(f"Elapsed: {time.perf_counter() - start:.2f}s")
    sys.exit(exit_code)
