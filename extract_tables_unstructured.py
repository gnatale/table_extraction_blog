#!/usr/bin/env python3
"""Extract tables from a PDF using the Unstructured Transform API and save them as CSV files."""

import argparse
import csv
import json
import mimetypes
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from unstructured_client import UnstructuredClient
from unstructured_client.models.operations import (
    CreateJobRequest,
    DownloadJobOutputRequest,
)
from unstructured_client.models.shared import BodyCreateJob, InputFiles

from env_utils import DEFAULT_ENV_FILE, load_env_file

# SDK paths include /api/v1/...; do not append /api/v1 here (curl docs use the full prefix).
DEFAULT_API_URL = "https://platform-api.transform.unstructured.io"
DEFAULT_OUTPUT_DIR = "unstructured_output"
POLL_INTERVAL_SECONDS = 10

PARTITION_JOB_NODES = [
    {
        "name": "Partitioner",
        "type": "partition",
        "subtype": "vlm",
        "settings": {
            "is_dynamic": True,
            "allow_fast": True,
        },
    }
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract tables from a PDF with the Unstructured Transform API "
            "and write CSV files."
        ),
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
    return parser.parse_args()


def require_api_key() -> str:
    load_env_file()
    api_key = os.getenv("UNSTRUCTURED_API_KEY")
    if not api_key:
        raise RuntimeError(
            "UNSTRUCTURED_API_KEY is not set. Add it to "
            f"{DEFAULT_ENV_FILE} or export it in your shell."
        )
    return api_key


def make_client() -> UnstructuredClient:
    return UnstructuredClient(
        api_key_auth=require_api_key(),
        server_url=DEFAULT_API_URL,
    )


class TableHTMLParser(HTMLParser):
    """Parse a simple HTML table into rows of cell text."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            cell_text = "".join(self._current_cell).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._in_cell = False
            self._current_cell = []
        elif tag == "tr" and self._current_row is not None:
            if any(cell.strip() for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def strip_html_fence(html: str) -> str:
    """Remove optional markdown code fences around HTML from enrich output."""
    trimmed = html.strip()
    match = re.match(r"^```(?:html)?\s*(.*?)```\s*$", trimmed, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return trimmed


def html_table_to_rows(html: str) -> list[list[str]]:
    parser = TableHTMLParser()
    parser.feed(strip_html_fence(html))
    return parser.rows


def table_element_to_rows(element: dict[str, Any]) -> list[list[str]]:
    metadata = element.get("metadata") or {}
    html = metadata.get("text_as_html")
    if isinstance(html, str) and html.strip():
        rows = html_table_to_rows(html)
        if rows:
            return rows

    text = element.get("text")
    if isinstance(text, str) and text.strip():
        return [[text.strip()]]

    return []


def normalize_elements(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("elements", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def job_status_value(status: Any) -> str:
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


def create_partition_job(client: UnstructuredClient, pdf_path: Path) -> str:
    content_type, _ = mimetypes.guess_type(pdf_path)
    with pdf_path.open("rb") as handle:
        response = client.jobs.create_job(
            request=CreateJobRequest(
                body_create_job=BodyCreateJob(
                    request_data=json.dumps({"job_nodes": PARTITION_JOB_NODES}),
                    input_files=[
                        InputFiles(
                            content=handle,
                            file_name=pdf_path.name,
                            content_type=content_type or "application/pdf",
                        )
                    ],
                )
            )
        )
    job_id = response.job_information.id
    if not job_id:
        raise RuntimeError("Unstructured did not return a job ID.")
    return job_id


def wait_for_job(client: UnstructuredClient, job_id: str) -> list[str]:
    while True:
        response = client.jobs.get_job(request={"job_id": job_id})
        job_info = response.job_information
        status = job_status_value(job_info.status)
        print(f"Job status: {status}")

        if status == "COMPLETED":
            output_files = job_info.output_node_files or []
            file_ids = [f.file_id for f in output_files if f.file_id]
            if not file_ids:
                raise RuntimeError("Job completed but returned no output files.")
            return file_ids

        if status in ("FAILED", "STOPPED"):
            raise RuntimeError(f"Job did not complete successfully: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)


def download_job_outputs(
    client: UnstructuredClient,
    job_id: str,
    file_ids: list[str],
) -> list[Any]:
    outputs: list[Any] = []
    for file_id in file_ids:
        response = client.jobs.download_job_output(
            request=DownloadJobOutputRequest(job_id=job_id, file_id=file_id)
        )
        outputs.append(response.any)
    return outputs


def write_table_csv(rows: list[list[str]], csv_path: Path) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)


def export_tables(pdf_path: Path, output_dir: Path) -> list[Path]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    table_dir = output_dir / pdf_path.stem
    table_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = table_dir / "raw_output"
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = make_client()
    job_id = create_partition_job(client, pdf_path)
    print(f"Job ID: {job_id}")

    file_ids = wait_for_job(client, job_id)
    outputs = download_job_outputs(client, job_id, file_ids)

    (raw_dir / "unstructured_response.json").write_text(
        json.dumps(outputs, indent=2),
        encoding="utf-8",
    )

    elements: list[dict[str, Any]] = []
    for output in outputs:
        elements.extend(normalize_elements(output))

    csv_paths: list[Path] = []
    table_index = 0
    for element in elements:
        if element.get("type") != "Table":
            continue
        rows = table_element_to_rows(element)
        if not rows:
            continue
        table_index += 1
        csv_path = table_dir / f"table_{table_index}.csv"
        write_table_csv(rows, csv_path)
        csv_paths.append(csv_path)

    if not csv_paths:
        raise RuntimeError("Unstructured found no tables in the document.")

    return csv_paths


def main() -> int:
    args = parse_args()
    try:
        csv_paths = export_tables(pdf_path=args.pdf, output_dir=args.output_dir)
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
