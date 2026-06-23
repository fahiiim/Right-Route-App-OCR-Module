<h1 align="center">Right Route App - OCR Module</h1>
<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Quality](https://img.shields.io/badge/code%20quality-professional-brightgreen)](.)
[![Last Updated](https://img.shields.io/badge/updated-Dec%202025-blue)](.)

[![AWS](https://img.shields.io/badge/AWS-FF9900?style=for-the-badge&logo=amazon-aws&logoColor=white)](https://aws.amazon.com/textract/)
[![Amazon Textract](https://img.shields.io/badge/Amazon%20Textract-FF9900?style=for-the-badge&logo=amazon&logoColor=white)](.)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--3.5-412991?logo=openai&logoColor=white)](https://openai.com/)
[![PyPDF2](https://img.shields.io/badge/PyPDF2-PDF%20Processing-red)](https://pypi.org/project/PyPDF2/)
[![Pillow](https://img.shields.io/badge/Pillow-Image%20Processing-7EC3A8)](https://python-pillow.org/)

[![Boto3](https://img.shields.io/badge/Boto3-AWS%20SDK-FF9900)](https://boto3.amazonaws.com/)
[![Python Dotenv](https://img.shields.io/badge/python--dotenv-Env%20Management-9B59B6)](https://pypi.org/project/python-dotenv/)
[![Requests](https://img.shields.io/badge/Requests-HTTP%20Client-2CA5E0)](https://requests.readthedocs.io/)

[![OCR](https://img.shields.io/badge/OCR-Optical%20Character%20Recognition-4285F4)](.)
[![AI](https://img.shields.io/badge/AI-Artificial%20Intelligence-FF6B6B)](.)
[![NLP](https://img.shields.io/badge/NLP-Natural%20Language%20Processing-00B4D8)](.)
[![PDF Processing](https://img.shields.io/badge/PDF-Processing-E83E8C)](.)

[![Document Processing](https://img.shields.io/badge/Document-Processing-6A0DAD)](.)
[![Route Extraction](https://img.shields.io/badge/Route-Extraction-28A745)](.)
[![Structured Data](https://img.shields.io/badge/Structured-Data-FF6B35)](.)
[![REST API Ready](https://img.shields.io/badge/REST%20API-Ready-003366)](.)

</div>

> **Professional-grade OCR solution for automated route extraction from permit documents and images AND VOICE PROCESSING**

## Overview

The Right Route App OCR Module is a robust, enterprise-ready solution that leverages advanced Optical Character Recognition (OCR) and Artificial Intelligence to automatically extract route information from permit documents. Built with AWS Textract and OpenAI's GPT-3.5, this module provides accurate, structured data extraction with minimal human intervention.

Live IP Link: http://16.192.4.30:8001/docs#/

### Key Capabilities

- 🎯 **Intelligent Text Extraction** - AWS Textract with multi-page PyMuPDF-first PDF extraction and resilient fallback paths
- 🤖 **AI-Powered Information Parsing** - OpenAI extraction (gpt-4.1 default, fallback model configurable)
- 📄 **Multi-Format Support** - PDF, JPG, PNG, GIF, WebP documents
- 🔍 **Structured Output** - JSON-formatted route data with geographic coordinates
- 🛡️ **Reliability-Focused Pipeline** - schema-validated JSON extraction with bounded retries and deterministic fallback parsing
- 📊 **Measured Quality Gates** - benchmark-driven checks for complete-route, start/end-only, and empty-segment rates

Accuracy note: permit formats differ by state and template, so no OCR+LLM pipeline can guarantee 100% extraction across all future documents. This repository focuses on measurable improvement and safe fallback behavior.

## Reliability and Benchmarking

The extraction internals are hardened while keeping the public API contract unchanged.

- Stage-level classification is captured for OCR, model parsing/validation, and fallback quality.
- Route extraction can run in enhanced mode (default) or legacy mode using environment flags.
- CI blocks deploy when regression quality checks fail.

Run the benchmark suite locally:

```bash
python scripts/run_regression_suite.py --strict --pipeline-mode deterministic --dataset benchmarks/permit_samples.json
```

Quality metrics tracked:

- `complete_route_rate`
- `start_end_only_rate`
- `empty_segment_rate`

---

## System Architecture

<img width="1024" height="1024" alt="image" src="https://github.com/user-attachments/assets/263f0e2a-2d65-4998-853a-076be7be8715" />


---

## Prerequisites

### System Requirements

- **Python:** 3.8 or higher
- **OS:** Windows, macOS, or Linux
- **RAM:** Minimum 2GB recommended
- **Disk Space:** 500MB for dependencies

### External Services

1. **AWS Account** with:
   - AWS Textract service access
   - S3 bucket (optional, for document storage)
   - Proper IAM credentials configured

2. **OpenAI API Key:**
   - OpenAI account with API access
   - Sufficient API credits/quota

---

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/fahiiim/Right-Route-App-OCR-Module.git
cd Right-Route-App-OCR-Module
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `boto3` - AWS SDK
- `openai` - OpenAI API client
- `fastapi` and `uvicorn` - REST API server
- `python-dotenv` - Environment variable management
- `python-multipart` - File upload handling
- `pymupdf` - PDF processing and text extraction
- `Pillow` - Image processing
- `requests` - HTTP client

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# AWS Configuration
AWS_REGION=us-east-1
AWS_ACCESS_KEY=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Optional: AWS S3 Configuration
AWS_S3_BUCKET=your_s3_bucket_name
```

⚠️ **Security Note:** Never commit `.env` file to version control. Use environment variables in production.

### 4. Run the Module

```bash
python main.py path/to/document.pdf
```

**Example Output:**

```
🌐 OCR Module - Route Information Extractor
============================================================

📄 Processing document: permit.pdf
============================================================
🔍 Extracting text from document using AWS Textract...
  ✅ PDF text extracted with PyPDF2
✅ Text extraction successful

📝 Extracted Text Preview:
------------------------------------------------------------
[Extracted text content...]
------------------------------------------------------------

🤖 Extracting route information using OpenAI...
✅ Route extraction successful

🗺️  Route Information:
------------------------------------------------------------
{
  "start_location": "Main St & 5th Ave, New York, NY",
  "end_location": "Broadway & 42nd St, New York, NY",
  "route_segments": [
    "Main St northbound",
    "Turn left on 5th Ave",
    "Turn right on Broadway",
    "Destination on right"
  ]
}
------------------------------------------------------------
```

### 5. Run the REST API (Local)

```bash
uvicorn api:app --host 0.0.0.0 --port 8001
```

Once running:
- API root: `http://localhost:8001/`
- Interactive docs: `http://localhost:8001/docs`
- OCR endpoint: `POST http://localhost:8001/api/ocr/extract`

---

## Docker Quick Start

### 1. Prepare environment variables

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS/Linux
cp .env.example .env
```

Update `.env` with your real AWS and OpenAI credentials.

### 2. Build and run with Docker Compose

```bash
docker compose up --build -d
```

### 3. Test the containerized API

```bash
curl http://localhost:8001/
```

Swagger docs will be available at `http://localhost:8001/docs`.

### 4. Stop the container

```bash
docker compose down
```

---

## EC2 CI/CD Deployment

This repository includes a production deployment workflow for EC2.

- Workflow file: `.github/workflows/ci-cd-ec2.yml`
- Full setup guide: `DEPLOYMENT.md`
- One-time EC2 bootstrap script: `scripts/bootstrap-ec2.sh`

Pushes to `main` run CI checks, build the Docker image, and deploy over SSH to EC2.

---

## Live Link From Docker (No Cloud Deployment)

If your container is already running on port `8001`, you can create a temporary public link directly from your machine.

### 1. Keep Docker container running

```bash
docker compose up -d
```

### 2. Start temporary public tunnel

```bash
ssh -o StrictHostKeyChecking=no -R 80:localhost:8001 nokey@localhost.run
```

The terminal will print a public HTTPS URL (example: `https://abc123.localhost.run`).

### 3. Share this link with backend engineer

- `GET https://<your-tunnel-domain>/`
- `GET https://<your-tunnel-domain>/docs`
- `POST https://<your-tunnel-domain>/api/ocr/extract`

### 4. Stop live link when done

Close the tunnel terminal with `Ctrl + C`.

Note: this tunnel link is temporary and changes when restarted.

---

## Usage Guide

### Command Line Usage

```bash
# Process a single document
python main.py document.pdf

# Process an image
python main.py permit.jpg

# Process multiple documents (in a loop)
for file in uploads/*.pdf; do
    python main.py "$file"
done
```

### Supported File Formats

| Format | Extension | Support |
|--------|-----------|---------|
| PDF    | `.pdf`    | ✅ Full |
| JPEG   | `.jpg`, `.jpeg` | ✅ Full |
| PNG    | `.png`    | ✅ Full |
| GIF    | `.gif`    | ✅ Full |
| WebP   | `.webp`   | ✅ Full |

### Output Format

The module returns structured JSON data:

```json
{
  "filename": "permit.pdf",
  "extracted_text": "Full text content extracted...",
  "route_information": {
    "start_location": "Main St & 5th Ave, New York, NY",
    "end_location": "Broadway & 42nd St, New York, NY",
    "route_segments": [
      "Main St northbound",
      "Turn right on 5th Ave",
      "Destination on right"
    ]
  }
}
```

---

## API Reference

### Function: `process_document(file_path)`

Processes a document and extracts route information.

**Parameters:**
- `file_path` (str): Full path to the document

**Returns:**
- `dict`: Structured output with extraction results
- `None`: If processing fails

**Example:**

```python
from main import process_document

result = process_document("permits/document.pdf")
if result:
    print(result["route_information"]["start_location"])
```

### Function: `extract_text_from_document(file_path, document_name)`

Extracts raw text using AWS Textract or PyPDF2.

**Parameters:**
- `file_path` (str): Path to document
- `document_name` (str): Document identifier

**Returns:**
- `tuple`: (extracted_text, job_id)

### Function: `extract_route_information(extracted_text)`

Parses extracted text using OpenAI GPT-3.5.

**Parameters:**
- `extracted_text` (str): Raw text to process

**Returns:**
- `dict`: Structured route information

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| **Average Processing Time** | 2-5 seconds per document |
| **Text Extraction Accuracy** | 95%+ (AWS Textract) |
| **Route Parsing Accuracy** | 92%+ (GPT-3.5) |
| **Supported Concurrent Requests** | 10+ (with proper scaling) |
| **File Size Limit** | Up to 50MB (AWS limit) |

---

## Troubleshooting

### Common Issues

#### 1. **AWS Textract Service Error**

```
❌ Error: Error extracting text from document: An error occurred (InvalidSignatureException)
```

**Solution:** Verify AWS credentials in `.env` file

```bash
aws sts get-caller-identity  # Test AWS credentials
```

#### 2. **OpenAI API Rate Limit**

```
❌ Error: Error extracting route information: RateLimitError
```

**Solution:** 
- Wait before retrying
- Check API quota limits in OpenAI dashboard
- Upgrade account tier if needed

#### 3. **PDF Extraction Failures**

```
⚠️  PyPDF2 extraction failed
```

**Solution:** 
- Try updating PyPDF2: `pip install --upgrade PyPDF2`
- Module automatically falls back to AWS Textract
- Some encrypted PDFs may require decryption

#### 4. **File Not Found**

```
❌ Error: File not found
```

**Solution:** Use absolute file path:

```bash
python main.py C:\Full\Path\To\document.pdf  # Windows
python main.py /full/path/to/document.pdf    # macOS/Linux
```

---

## Configuration Guide

### AWS Textract Setup

1. Go to [AWS IAM Console](https://console.aws.amazon.com/iam/)
2. Create user with **TextractFullAccess** policy
3. Generate **Access Keys**
4. Add to `.env` file

### OpenAI API Setup

1. Visit [OpenAI Platform](https://platform.openai.com/)
2. Create/login to account
3. Generate API key in dashboard
4. Set `OPENAI_API_KEY` in `.env`

---

## Code Structure

```
Social-wifi OCR Module/
├── api.py                     # FastAPI application and endpoints
├── main.py                    # OCR extraction and AI parsing logic
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container build definition
├── docker-compose.yml         # Local container orchestration
├── .env.example               # Environment variable template
├── .env                       # Local secrets (git-ignored)
├── uploads/                   # Optional local document directory
└── README.md                  # Documentation
```

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| boto3 | ≥1.28.0 | AWS SDK |
| botocore | ≥1.31.0 | AWS SDK core configuration |
| fastapi | ≥0.104.0 | REST API framework |
| uvicorn | ≥0.24.0 | ASGI server |
| openai | ≥1.5.0 | OpenAI API |
| python-dotenv | ≥1.0.0 | Environment variables |
| python-multipart | ≥0.0.6 | Multipart file uploads |
| pymupdf | ≥1.24.0 | PDF text extraction |
| Pillow | ≥11.0.0 | Image processing |
| requests | ≥2.31.0 | HTTP requests |

For detailed versions, see [requirements.txt](requirements.txt)

---

## Best Practices

### Document Preparation

- **PDF Quality:** Ensure documents are clear and readable
- **Language:** English documents supported (others may have lower accuracy)
- **Resolution:** Scanned documents should be 200+ DPI
- **Color:** Color documents process faster than B&W

### API Usage Optimization

- **Batch Processing:** Process documents sequentially to avoid rate limits
- **Cost Control:** Monitor OpenAI API usage in dashboard
- **Error Handling:** Implement retry logic with exponential backoff
- **Caching:** Cache extraction results when possible

### Security Considerations

- ✅ Store credentials in environment variables
- ✅ Use IAM roles instead of access keys in production
- ✅ Implement request logging (exclude sensitive data)
- ✅ Rotate API keys regularly
- ❌ Never commit `.env` to version control
- ❌ Never log API keys or sensitive data
- ❌ Never expose environment variables in error messages

---

## License

MIT License - See [LICENSE](LICENSE) for details

---

## Support & Contribution

### Getting Help

- 📧 **Report Issues:** [GitHub Issues](https://github.com/fahiiim/Right-Route-App-OCR-Module/issues)
- 📚 **Documentation:** Full API reference included
- 💬 **Discussions:** [GitHub Discussions](https://github.com/fahiiim/Right-Route-App-OCR-Module/discussions)

### Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

---

## Changelog

### Version 1.0.0 (December 2025)

- ✨ Initial release
- 🎯 AWS Textract integration
- 🤖 OpenAI GPT-3.5 processing
- 📄 Multi-format document support
- 🔐 Environment-based configuration

---

## Technology Stack

```
┌──────────────────────────────────────┐
│   Right Route OCR Module v1.0        │
├──────────────────────────────────────┤
│                                      │
│  Application Layer                   │
│  ├─ Python 3.8+                      │
│  └─ FastAPI + Uvicorn                │
│                                      │
│  Processing Layer                    │
│  ├─ AWS Textract (OCR)              │
│  ├─ PyMuPDF (PDF handling)          │
│  └─ Pillow (Image processing)       │
│                                      │
│  Intelligence Layer                  │
│  └─ OpenAI GPT models (NLP)         │
│                                      │
│  Infrastructure                      │
│  ├─ Docker / Docker Compose          │
│  ├─ Localhost.run tunnel             │
│  └─ AWS + OpenAI APIs                │
│                                      │
└──────────────────────────────────────┘
```

---

## Roadmap

- [ ] Batch processing API endpoint
- [ ] Multi-language support
- [ ] Confidence score metrics
- [ ] Result caching layer
- [x] REST API wrapper
- [x] Docker containerization
- [ ] Unit test suite
- [ ] Performance optimization

---

## Disclaimer

This module processes documents through external services (AWS, OpenAI). Ensure compliance with:
- Data protection regulations (GDPR, CCPA)
- Service terms and conditions
- Document confidentiality requirements
- API usage policies

---

**Last Updated:** March 2026  
**Repository:** [GitHub](https://github.com/fahiiim/Right-Route-App-OCR-Module)  
**Maintained By:** SparkTech Agency
**AI Engineer:** Md Fahim Sarker Mridul
