# Table extraction blog data and scripts

Companion repository for blog posts on PDF table extraction. It includes sample PDFs, ground-truth spreadsheets, and scripts to reproduce the results discussed in the articles.

**Blog posts:**

Why PDF table extraction fails - part I: https://gionatale.substack.com/p/why-pdf-table-extraction-fails-part

## Repository layout

```
test_pdfs/                       # test PDFs used in the blogs
original_csvs/                   # ground-truth Numbers files and CSV
extract_tables_*.py              # table extraction scripts
env_utils.py                     # shared .env loader
example.env                      # environment variable template
{tool}_output/                   # tool output directories (gitignored)
blog_output/                     # tool output files shown in the blog  
```

## Requirements

- Python 3.12 (the version used to prepare the blog entries; other versions may work)

## Installation

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration for external services

Cloud and API scripts read credentials from a `.env` file in the repository root.

1. Copy the template: `cp example.env .env`
2. Fill in the values for the services you plan to use.

Note that pre-existing environmental variables override values in `.env`. 

**AWS Textract and Bedrock Data Automation** require an AWS account, an S3 bucket/prefix for uploads, and IAM permissions for the relevant APIs. 
BDA also needs a pre-configured [Data Automation project](https://docs.aws.amazon.com/bedrock/latest/userguide/bda.html) ARN.

**Unstructured** requires a [Transform API](https://docs.unstructured.io/) account and API key.

## Running the scripts

All scripts take a PDF path as the first argument and write output under a tool-specific directory. Run `python <script> --help` for full options.

Example:

```bash
python extract_tables_pdfplumber.py \
  "test_pdfs/frostbite_financial_statement/frostbite_creamery_annual_financial_statement (full grid).pdf"
```

## Disclaimer

This repository is provided as-is for educational and research purposes. The authors make no warranties and accept no liability for use of these scripts or sample data. Use at your own risk.

## License

Licensed under the MIT License — see [LICENSE](LICENSE).
