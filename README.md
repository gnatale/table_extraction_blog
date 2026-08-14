# Table extraction blog data and scripts

This repo contains the example data and table extraction scripts I used when writing these blogs about table data extractions: 

xxx add links xxx

You can use it to reproduce the same results shown in the blogs. 

The table extraction output I obtained by running the script on my pc are in the blog_output folder. 

## Requirements 

- python 3.12 (this is the version I used. The code may work with older/newer versions)

## Installation

MacOS / Linux 

````
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
````

## Executing the scripts 

After installing the python packages, the scripts to extract tables using only local code and models (pdfplumber, Docling, PyMuPDF, PyMuPDF4LLM) should work immediately. 

Those using external services (AWS Textract, AWS Bedrock Data Automation, Unstructured) need an account to be set up in the corresponding services and some configuration. See each script for details. Note that, for each service, some environmental variables should be defined in an .env file (see example.env).    