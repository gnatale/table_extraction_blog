#!/usr/bin/env python3
"""Extract tables from a PDF using Amazon Bedrock Data Automation and save Markdown and CSV files."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3

from env_utils import load_env_file, require_env

load_env_file()

DEFAULT_AWS_PROFILE = require_env("AWS_PROFILE")
DEFAULT_AWS_REGION = require_env("AWS_REGION")
DEFAULT_S3_UPLOAD_PATH = require_env("BDA_S3_UPLOAD_PATH")
DEFAULT_OUTPUT_DIR = "bda_output"
DEFAULT_DATA_AUTOMATION_PROJECT_ARN = require_env("BDA_DATA_AUTOMATION_PROJECT_ARN")
POLL_INTERVAL_SECONDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract tables from a PDF with Bedrock Data Automation "
            "and write Markdown and CSV files."
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
        help=f"AWS region for Bedrock Data Automation (default: {DEFAULT_AWS_REGION})",
    )
    parser.add_argument(
        "--project-arn",
        default=DEFAULT_DATA_AUTOMATION_PROJECT_ARN,
        help=(
            "Data Automation project ARN (default: custom table-extraction project "
            "in eu-central-1)"
        ),
    )
    return parser.parse_args()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Return bucket name and object key prefix from an s3:// URI."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not a valid S3 URI: {uri}")
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return parsed.netloc, prefix


def data_automation_profile_suffix(region: str) -> str:
    """Map an AWS region to the BDA data-automation-profile resource suffix.

    Profile ARNs use geography-specific suffixes (not one global name):
    - ``us.data-automation-v1`` for US commercial regions
    - ``eu.data-automation-v1`` for Europe commercial regions
    - ``apac.data-automation-v1`` for Asia Pacific commercial regions
    - ``us-gov.data-automation-v1`` for AWS GovCloud
    """
    if region.startswith("us-gov-"):
        return "us-gov.data-automation-v1"
    if region.startswith("us-"):
        return "us.data-automation-v1"
    if region.startswith("eu-"):
        return "eu.data-automation-v1"
    if region.startswith("ap-"):
        return "apac.data-automation-v1"
    raise ValueError(
        f"No Bedrock Data Automation profile mapping for region {region!r}. "
        "Use a supported US, EU, APAC, or GovCloud region."
    )


def data_automation_profile_arn(region: str, account_id: str) -> str:
    suffix = data_automation_profile_suffix(region)
    return (
        f"arn:aws:bedrock:{region}:{account_id}:data-automation-profile/{suffix}"
    )


def upload_file(s3_client: Any, bucket: str, key: str, local_path: Path) -> None:
    s3_client.upload_file(str(local_path.resolve()), bucket, key)


def download_bytes(s3_client: Any, s3_uri: str) -> bytes:
    bucket, key = parse_s3_object_uri(s3_uri)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def parse_s3_object_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not a valid S3 object URI: {s3_uri}")
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"S3 URI must include an object key: {s3_uri}")
    return parsed.netloc, key


def s3_object_parent_prefix(s3_uri: str) -> str:
    """Return the S3 key prefix (with trailing slash) for the object’s parent “folder”."""
    _, key = parse_s3_object_uri(s3_uri)
    if "/" not in key:
        return ""
    return f"{key.rsplit('/', 1)[0]}/"


def load_json_from_s3(s3_client: Any, s3_uri: str) -> dict[str, Any]:
    return json.loads(download_bytes(s3_client, s3_uri).decode("utf-8"))


def delete_s3_object(s3_client: Any, bucket: str, key: str) -> None:
    s3_client.delete_object(Bucket=bucket, Key=key)


def delete_s3_prefix(s3_client: Any, bucket: str, prefix: str) -> None:
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents", [])
        if not contents:
            continue
        s3_client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": obj["Key"]} for obj in contents]},
        )


def download_s3_prefix(
    s3_client: Any,
    bucket: str,
    prefix: str,
    local_dir: Path,
) -> None:
    if not prefix:
        return
    local_root = local_dir.resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if key.startswith(prefix):
                relative_key = key[len(prefix) :]
            else:
                relative_key = key
            relative_key = relative_key.lstrip("/")
            if not relative_key or ".." in Path(relative_key).parts:
                continue
            local_path = (local_root / relative_key).resolve()
            try:
                local_path.relative_to(local_root)
            except ValueError:
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(local_path))


def wait_for_job(
    bda_runtime: Any,
    invocation_arn: str,
) -> dict[str, Any]:
    while True:
        status_response = bda_runtime.get_data_automation_status(
            invocationArn=invocation_arn,
        )
        status = status_response.get("status")
        if status in ("Success", "ServiceError", "ClientError"):
            return status_response
        if status not in ("Created", "InProgress", None):
            raise RuntimeError(f"Unexpected BDA job status: {status!r}")
        time.sleep(POLL_INTERVAL_SECONDS)


def iter_standard_output_paths(job_metadata: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for segment in job_metadata.get("output_metadata", []):
        for segment_metadata in segment.get("segment_metadata", []):
            path = segment_metadata.get("standard_output_path")
            if path:
                paths.append(path)
    return paths


def collect_table_entities(standard_output: dict[str, Any]) -> list[dict[str, Any]]:
    tables = [
        element
        for element in standard_output.get("elements", [])
        if element.get("type") == "TABLE"
    ]
    tables.sort(key=lambda table: table.get("reading_order", 0))
    return tables


def write_table_csv(
    s3_client: Any,
    table: dict[str, Any],
    csv_path: Path,
) -> None:
    representation = table.get("representation") or {}
    csv_text = representation.get("csv")
    if csv_text:
        csv_path.write_text(csv_text, encoding="utf-8")
        return

    csv_s3_uri = table.get("csv_s3_uri")
    if csv_s3_uri:
        csv_path.write_bytes(download_bytes(s3_client, csv_s3_uri))
        return

    raise RuntimeError(
        "Table entity has no representation.csv or csv_s3_uri in BDA output."
    )


def write_table_markdown(table: dict[str, Any], md_path: Path) -> None:
    representation = table.get("representation") or {}
    markdown = representation.get("markdown")
    if markdown:
        md_path.write_text(markdown, encoding="utf-8")
        return

    plain_text = representation.get("text")
    if plain_text:
        md_path.write_text(plain_text, encoding="utf-8")
        return

    raise RuntimeError(
        "Table entity has no representation.markdown or representation.text "
        "in BDA output."
    )


def export_tables(
    pdf_path: Path,
    output_dir: Path,
    s3_upload_path: str,
    profile: str,
    region: str,
    project_arn: str,
) -> tuple[list[Path], list[Path]]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf_stem = pdf_path.stem
    table_dir = output_dir / pdf_stem
    table_dir.mkdir(parents=True, exist_ok=True)

    session = boto3.Session(profile_name=profile, region_name=region)
    s3_client = session.client("s3")
    sts_client = session.client("sts")
    bda_runtime = session.client("bedrock-data-automation-runtime")

    account_id = sts_client.get_caller_identity()["Account"]
    bucket, upload_prefix = parse_s3_uri(s3_upload_path)
    job_id = str(uuid.uuid4())
    input_key = f"{upload_prefix}{job_id}.pdf"
    output_prefix = f"bda-output/{job_id}/"
    input_s3_uri = f"s3://{bucket}/{input_key}"
    output_s3_uri = f"s3://{bucket}/{output_prefix}"

    upload_file(s3_client, bucket, input_key, pdf_path)

    output_cleanup_prefix = output_prefix
    try:
        invoke_response = bda_runtime.invoke_data_automation_async(
            inputConfiguration={"s3Uri": input_s3_uri},
            outputConfiguration={"s3Uri": output_s3_uri},
            dataAutomationConfiguration={
                "dataAutomationProjectArn": project_arn,
                "stage": "LIVE",
            },
            dataAutomationProfileArn=data_automation_profile_arn(region, account_id),
        )

        status_response = wait_for_job(
            bda_runtime,
            invoke_response["invocationArn"],
        )
        if status_response.get("status") != "Success":
            error_message = status_response.get("errorMessage", "unknown error")
            error_type = status_response.get("errorType", status_response["status"])
            raise RuntimeError(f"BDA job failed ({error_type}): {error_message}")

        job_metadata_uri = status_response["outputConfiguration"]["s3Uri"]
        output_cleanup_prefix = s3_object_parent_prefix(job_metadata_uri)
        job_metadata = load_json_from_s3(s3_client, job_metadata_uri)

        all_tables: list[dict[str, Any]] = []
        for standard_output_path in iter_standard_output_paths(job_metadata):
            standard_output = load_json_from_s3(s3_client, standard_output_path)
            all_tables.extend(collect_table_entities(standard_output))

        if not all_tables:
            raise RuntimeError(
                "BDA completed but no tables were found in the document."
            )

        md_paths: list[Path] = []
        csv_paths: list[Path] = []
        for index, table in enumerate(all_tables, start=1):
            md_path = table_dir / f"table_{index}.md"
            csv_path = table_dir / f"table_{index}.csv"
            write_table_markdown(table, md_path)
            write_table_csv(s3_client, table, csv_path)
            md_paths.append(md_path)
            csv_paths.append(csv_path)

        return md_paths, csv_paths
    finally:
        delete_s3_object(s3_client, bucket, input_key)
        download_s3_prefix(
            s3_client, bucket, output_cleanup_prefix, table_dir / "raw_output"
        )
        delete_s3_prefix(s3_client, bucket, output_cleanup_prefix)
        if output_cleanup_prefix != output_prefix:
            delete_s3_prefix(s3_client, bucket, output_prefix)


def main() -> int:
    args = parse_args()
    try:
        md_paths, csv_paths = export_tables(
            pdf_path=args.pdf,
            output_dir=args.output_dir,
            s3_upload_path=args.s3_upload_path,
            profile=args.profile,
            region=args.region,
            project_arn=args.project_arn,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {len(md_paths)} table(s) as Markdown:")
    for path in md_paths:
        print(path)
    print(f"Extracted {len(csv_paths)} table(s) as CSV:")
    for path in csv_paths:
        print(path)
    return 0


if __name__ == "__main__":
    start = time.perf_counter()
    exit_code = main()
    print(f"Elapsed: {time.perf_counter() - start:.2f}s")
    sys.exit(exit_code)
