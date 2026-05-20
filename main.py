import os
import json
import sys
import time
import uuid
import re
import boto3
import base64
from pathlib import Path
from dotenv import load_dotenv
from botocore.config import Config
from botocore.exceptions import ClientError
from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, APIError
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# Load environment variables
load_dotenv()

# Disable __pycache__ generation for this process.
sys.dont_write_bytecode = True

# Lazy initialization - clients created on first use
_textract_client = None
_s3_client = None
_openai_client = None
_fitz_import_error = None

# Boto3 config with timeouts to prevent hanging
_boto_config = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={'max_attempts': 2}
)


class RouteExtractionError(Exception):
    """Raised when upstream OCR/LLM extraction fails with actionable status."""

    def __init__(self, message, status_code=500, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _get_env_value(name, default=None):
    """Read an environment variable and trim surrounding whitespace."""
    value = os.getenv(name)
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
    if value == "":
        return default
    return value


def get_textract_client():
    """Lazy initialization of Textract client"""
    global _textract_client
    if _textract_client is None:
        _textract_client = boto3.client(
            'textract',
            region_name=_get_env_value('AWS_REGION'),
            aws_access_key_id=_get_env_value('AWS_ACCESS_KEY'),
            aws_secret_access_key=_get_env_value('AWS_SECRET_ACCESS_KEY'),
            config=_boto_config
        )
    return _textract_client


def get_s3_client():
    """Lazy initialization of S3 client"""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            's3',
            region_name=_get_env_value('AWS_REGION'),
            aws_access_key_id=_get_env_value('AWS_ACCESS_KEY'),
            aws_secret_access_key=_get_env_value('AWS_SECRET_ACCESS_KEY'),
            config=_boto_config
        )
    return _s3_client


def get_openai_client():
    """Lazy initialization of OpenAI client"""
    global _openai_client
    if _openai_client is None:
        api_key = (os.getenv('OPENAI_API_KEY') or '').strip()
        if not api_key:
            raise RouteExtractionError(
                "OPENAI_API_KEY is missing. Set it in your environment file.",
                status_code=500,
                code="missing_openai_api_key"
            )
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


# Backward compatibility aliases
def __getattr__(name):
    if name == 'textract_client':
        return get_textract_client()
    if name == 's3_client':
        return get_s3_client()
    if name == 'openai_client':
        return get_openai_client()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'txt', 'jpeg', 'png', 'gif', 'webp'}

# Prefer the strongest model by default; fall back gracefully if unavailable.
OPENAI_BEST_MODEL = (os.getenv('OPENAI_MODEL') or 'gpt-4.1').strip() or 'gpt-4.1'
OPENAI_FALLBACK_MODEL = (os.getenv('OPENAI_FALLBACK_MODEL') or 'gpt-4o').strip() or 'gpt-4o'

# State abbreviations mapping
STATE_ABBREVIATIONS = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri',
    'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey',
    'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont',
    'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming'
}

# Direction abbreviations mapping
DIRECTION_ABBREVIATIONS = {
    'N': 'North',
    'S': 'South',
    'E': 'East',
    'W': 'West',
    'NB': 'Northbound',
    'SB': 'Southbound',
    'EB': 'Eastbound',
    'WB': 'Westbound'
}


def expand_abbreviations(text):
    """Expand state, direction, and route abbreviations to full names"""
    if not text:
        return text
    
    result = text
    
    # Expand state abbreviations (match word boundaries for state codes)
    # IMPORTANT: Only match uppercase state abbreviations to avoid matching lowercase words like "in"
    for abbr, full_name in STATE_ABBREVIATIONS.items():
        # Match state abbreviations ONLY when uppercase (to avoid matching "in" as "IN")
        # This handles patterns like ", IN", " IN ", "(IN)", "IN " etc. but NOT lowercase "in"
        patterns = [
            (rf'\b{abbr}\b(?=[,\s\)]|$)', full_name),  # For standalone state codes
            (rf', {abbr}(?=[,\s\)]|$)', f', {full_name}'),  # After comma
        ]
        for pattern, replacement in patterns:
            # Do NOT use IGNORECASE flag - match only uppercase abbreviations
            result = re.sub(pattern, replacement, result)
    
    # Expand direction abbreviations (match word boundaries, only uppercase to avoid false positives)
    for abbr, full_name in DIRECTION_ABBREVIATIONS.items():
        pattern = rf'\b{abbr}\b'
        # Do NOT use IGNORECASE flag for direction abbreviations either
        result = re.sub(pattern, full_name, result)
    
    return result


def expand_abbreviations_in_dict(obj):
    """Recursively expand abbreviations in dictionary/list structures"""
    if isinstance(obj, dict):
        return {k: expand_abbreviations_in_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [expand_abbreviations_in_dict(item) for item in obj]
    elif isinstance(obj, str):
        return expand_abbreviations(obj)
    else:
        return obj


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _safe_string(value):
    """Convert a value to a trimmed string, or None when empty."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


def _safe_string_list(value):
    """Convert a value to a clean list of non-empty strings."""
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = _safe_string(item)
        if text:
            items.append(text)
    return items


def _contains_ramp(text):
    """Return True when a segment/intersection mentions a ramp."""
    if not text:
        return False
    return bool(re.search(r'\bramp\b', text, flags=re.IGNORECASE))


def _remove_ramp_segments(route_segments):
    """Remove ramp-related route segments while preserving route order."""
    return [segment for segment in route_segments if not _contains_ramp(segment)]


def _extract_route_label(segment):
    """Extract a clean road/highway label from a route segment string."""
    if not segment:
        return None

    route_label = segment.split(',', 1)[0].strip()
    route_label = re.sub(
        r'\s+(Northbound|Southbound|Eastbound|Westbound|NB|SB|EB|WB|N|S|E|W)\b',
        '',
        route_label,
        flags=re.IGNORECASE
    )
    route_label = re.sub(r'\s+', ' ', route_label).strip()
    return route_label or None


def _strip_direction_suffix(text):
    """Remove trailing direction tokens from a road label."""
    if not text:
        return text

    cleaned = text
    while True:
        updated = re.sub(
            r'\s+(Northbound|Southbound|Eastbound|Westbound|NB|SB|EB|WB|N|S|E|W)\b$',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        if updated == cleaned:
            break
        cleaned = updated

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or None


def _extract_segment_location(segment):
    """Extract city/state-style location text from a route segment string."""
    if not segment:
        return None

    parts = [part.strip() for part in segment.split(',')]
    if len(parts) < 2:
        return None

    location = ', '.join([part for part in parts[1:] if part])
    return location or None


def _normalize_route_segment(segment):
    """Normalize a route segment by removing direction words from the road label."""
    text = _safe_string(segment)
    if not text:
        return None

    if ',' in text:
        road_part, location_part = text.split(',', 1)
        road_part = _strip_direction_suffix(road_part.strip())
        location_part = location_part.strip()
        if road_part and location_part:
            return f"{road_part}, {location_part}"
        return road_part or location_part or None

    return _strip_direction_suffix(text)


def _normalize_intersection_entry(entry):
    """Normalize intersection text formatting to a consistent style."""
    text = _safe_string(entry)
    if not text:
        return None

    text = re.sub(r'\s+near\s+', ', ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    if ',' in text:
        roads_part, location_part = text.split(',', 1)
        location_part = location_part.strip()
    else:
        roads_part, location_part = text, None

    match = re.match(r'^\s*(.*?)\s+and\s+(.*?)\s*$', roads_part, flags=re.IGNORECASE)
    if match:
        road_a = _strip_direction_suffix(match.group(1).strip()) or match.group(1).strip()
        road_b = _strip_direction_suffix(match.group(2).strip()) or match.group(2).strip()
        roads_part = f"{road_a} and {road_b}"
    else:
        roads_part = _strip_direction_suffix(roads_part) or roads_part

    if location_part:
        return f"{roads_part}, {location_part}"
    return roads_part or None


def _build_intersections_from_segments(route_segments):
    """Create pairwise intersections from adjacent route segments in order."""
    intersections = []

    for idx in range(len(route_segments) - 1):
        current_segment = route_segments[idx]
        next_segment = route_segments[idx + 1]

        current_route = _extract_route_label(current_segment) or current_segment
        next_route = _extract_route_label(next_segment) or next_segment
        location = _extract_segment_location(current_segment) or _extract_segment_location(next_segment)

        if location:
            intersections.append(f"{current_route} and {next_route}, {location}")
        else:
            intersections.append(f"{current_route} and {next_route}")

    return intersections


def _normalize_intersections(intersection_data, route_segments):
    """Normalize model intersections and guarantee pairwise count/order."""
    expected_count = max(len(route_segments) - 1, 0)
    if expected_count == 0:
        return []

    normalized = []
    for item in _safe_string_list(intersection_data):
        normalized_item = _normalize_intersection_entry(item)
        if normalized_item and not _contains_ramp(normalized_item):
            normalized.append(normalized_item)
    generated = _build_intersections_from_segments(route_segments)

    if len(normalized) == expected_count:
        return normalized

    if not normalized:
        return generated

    adjusted = normalized[:expected_count]
    if len(adjusted) < expected_count:
        adjusted.extend(generated[len(adjusted):expected_count])

    return adjusted


def normalize_route_information(route_info):
    """Normalize model output to a stable JSON structure expected by downstream users."""
    if not isinstance(route_info, dict):
        route_info = {}

    route_info = expand_abbreviations_in_dict(route_info)

    start_location = _safe_string(route_info.get('start_location'))
    end_location = _safe_string(route_info.get('end_location'))
    raw_segments = _safe_string_list(route_info.get('route_segments'))
    normalized_segments = []
    for segment in raw_segments:
        cleaned_segment = _normalize_route_segment(segment)
        if cleaned_segment:
            normalized_segments.append(cleaned_segment)
    route_segments = _remove_ramp_segments(normalized_segments)
    intersection = _normalize_intersections(route_info.get('intersection'), route_segments)
    permit_type = _safe_string(route_info.get('permit_type')) or 'Unknown'

    normalized = {
        'start_location': start_location,
        'end_location': end_location,
        'route_segments': route_segments,
        'intersection': intersection,
        'permit_type': permit_type
    }

    if route_info.get('raw_response'):
        normalized['raw_response'] = route_info['raw_response']

    return normalized


def get_fitz_module():
    """Try to import PyMuPDF at runtime so fresh installs are picked up after restart."""
    global PDF_SUPPORT
    global _fitz_import_error
    if 'fitz' in globals():
        PDF_SUPPORT = True
        return globals()['fitz']

    try:
        import fitz as runtime_fitz  # PyMuPDF
        globals()['fitz'] = runtime_fitz
        PDF_SUPPORT = True
        _fitz_import_error = None
        return runtime_fitz
    except Exception as e:
        PDF_SUPPORT = False
        _fitz_import_error = str(e)
        return None


def extract_text_from_pdf_via_textract_async(file_path, document_name):
    """Use async Textract flow via S3 for PDFs that fail bytes-based sync OCR."""
    bucket_name = _get_env_value('AWS_S3_BUCKET')
    if not bucket_name:
        raise RouteExtractionError(
            "AWS_S3_BUCKET is required for async PDF processing.",
            status_code=500,
            code="missing_s3_bucket"
        )

    s3_key = f"ocr-temp/{Path(document_name).stem}-{uuid.uuid4()}.pdf"

    try:
        get_s3_client().upload_file(file_path, bucket_name, s3_key)

        start_response = get_textract_client().start_document_text_detection(
            DocumentLocation={
                'S3Object': {
                    'Bucket': bucket_name,
                    'Name': s3_key
                }
            }
        )
        job_id = start_response['JobId']

        timeout_seconds_raw = _get_env_value('TEXTRACT_PDF_TIMEOUT_SECONDS', '120')
        try:
            timeout_seconds = int(timeout_seconds_raw)
        except (TypeError, ValueError):
            timeout_seconds = 120
        elapsed = 0
        poll_interval = 2

        while True:
            status_response = get_textract_client().get_document_text_detection(JobId=job_id)
            status = status_response.get('JobStatus')

            if status == 'SUCCEEDED':
                lines = []
                next_token = None

                while True:
                    params = {'JobId': job_id}
                    if next_token:
                        params['NextToken'] = next_token

                    page_response = get_textract_client().get_document_text_detection(**params)
                    for block in page_response.get('Blocks', []):
                        if block.get('BlockType') == 'LINE' and block.get('Text'):
                            lines.append(block['Text'])

                    next_token = page_response.get('NextToken')
                    if not next_token:
                        break

                extracted_text = '\n'.join(lines).strip()
                if not extracted_text:
                    raise RouteExtractionError(
                        "No text could be extracted from the PDF.",
                        status_code=400,
                        code="empty_extracted_text"
                    )
                return extracted_text

            if status == 'FAILED':
                status_message = status_response.get('StatusMessage', 'Textract async PDF job failed.')
                raise RouteExtractionError(
                    f"Textract async PDF processing failed: {status_message}",
                    status_code=400,
                    code="textract_pdf_failed"
                )

            if elapsed >= timeout_seconds:
                raise RouteExtractionError(
                    "Textract async PDF processing timed out.",
                    status_code=503,
                    code="textract_pdf_timeout"
                )

            time.sleep(poll_interval)
            elapsed += poll_interval

    except ClientError as e:
        error = e.response.get('Error', {})
        error_code = error.get('Code', 'Unknown')
        error_message = error.get('Message') or str(e)

        status_code = 502
        custom_code = "aws_pdf_processing_error"
        if error_code in {'NoSuchBucket', 'InvalidS3ObjectException', 'InvalidParameterException'}:
            status_code = 400
            custom_code = "aws_invalid_s3_or_pdf_source"
        elif error_code in {
            'AccessDenied',
            'AccessDeniedException',
            'InvalidAccessKeyId',
            'SignatureDoesNotMatch',
            'UnrecognizedClientException'
        }:
            status_code = 403
            custom_code = "aws_auth_or_permission_error"

        raise RouteExtractionError(
            f"AWS Textract/S3 error during async PDF processing ({error_code}): {error_message}",
            status_code=status_code,
            code=custom_code
        )
    finally:
        try:
            get_s3_client().delete_object(Bucket=bucket_name, Key=s3_key)
        except Exception:
            pass


def extract_text_from_document(file_path, document_name):
    """Extract text from document using AWS Textract"""
    try:
        is_pdf = file_path.lower().endswith('.pdf')
        fitz_module = get_fitz_module() if is_pdf else None

        # Handle PDF files - try direct text extraction first for best speed/quality
        if is_pdf and fitz_module is not None:
            print("  Extracting text from PDF...")
            try:
                pdf_doc = fitz_module.open(file_path)
                pdf_text = ""
                for page_num in range(min(1, len(pdf_doc))):  # Get first page only
                    page = pdf_doc[page_num]
                    pdf_text += page.get_text()
                pdf_doc.close()

                if pdf_text.strip():
                    print("  ✅ PDF text extracted with PyMuPDF")
                    return pdf_text, None
            except Exception as e:
                print(f"  ⚠️  PyMuPDF extraction failed: {str(e)}")
                print("  Falling back to AWS Textract...")
        elif is_pdf:
            reason = _fitz_import_error or "PyMuPDF not installed"
            print(f"  ⚠️  PyMuPDF unavailable ({reason}); falling back to AWS Textract for PDF")

        with open(file_path, 'rb') as document:
            document_bytes = document.read()

        print("  Calling AWS Textract detect_document_text...")
        try:
            response = get_textract_client().detect_document_text(Document={'Bytes': document_bytes})
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')

            # Some PDFs are rejected in raw PDF form; retry as PNG when PyMuPDF is available.
            if is_pdf and error_code == 'UnsupportedDocumentException' and fitz_module is not None:
                print("  ⚠️  Textract rejected PDF bytes; retrying with first-page PNG...")
                try:
                    pdf_doc = fitz_module.open(file_path)
                    if len(pdf_doc) == 0:
                        pdf_doc.close()
                        raise RouteExtractionError(
                            "PDF has no pages.",
                            status_code=400,
                            code="empty_pdf"
                        )

                    page = pdf_doc[0]
                    pix = page.get_pixmap(dpi=200, alpha=False)
                    image_bytes = pix.tobytes("png")
                    pdf_doc.close()
                    response = get_textract_client().detect_document_text(Document={'Bytes': image_bytes})
                except RouteExtractionError:
                    raise
                except Exception as conversion_error:
                    raise RouteExtractionError(
                        f"Unsupported PDF format for Textract and PNG fallback failed: {str(conversion_error)}",
                        status_code=400,
                        code="unsupported_pdf_document"
                    )
            elif is_pdf and error_code == 'UnsupportedDocumentException':
                print("  ⚠️  Textract rejected PDF bytes; retrying with async S3 PDF flow...")
                return extract_text_from_pdf_via_textract_async(file_path, document_name), None
            else:
                raise
        
        # Extract text from response
        extracted_text = '\n'.join([block['Text'] for block in response['Blocks'] if block['BlockType'] == 'LINE'])
        
        if not extracted_text:
            raise Exception("No text could be extracted from the document")
        
        return extracted_text, None
            
    except RouteExtractionError:
        raise
    except Exception as e:
        raise Exception(f"Error extracting text from document: {str(e)}")


def extract_route_information(extracted_text):
    """Use OpenAI to extract and intelligently format route information from OCR text for all US states"""
    try:
        prompt = f"""
You are an expert at extracting and formatting route information from permit documents spanning all US states.

Use your geographic knowledge to infer full city, county, and state names based on abbreviations, road names, partial county names, or mileposts provided in the text. For example, if the text mentions "MP ROBERTS" and "SD-42", you should infer Roberts County, South Dakota, and identify primary cities like Sisseton. If you see "I-29 SB AT MP ROBERTS 252.65, I-90 EB, SD-11 SB, SD-42 EB, END ON SD-42 AT MP MINNEHAHA", you should figure out the start and end cities and map out each step accurately.

From the provided document text, extract the route information and return ONLY a valid JSON object matching the following exact structure:

{{
  "start_location": "[City], [County] County, [State]",
  "end_location": "[City], [County] County, [State]",
  "route_segments": [
    "[Road/Highway], [City], [State]",
    ...
  ],
    "intersection": [
        "[Road from segment i] and [Road from segment i+1] near [City], [State]",
        ...
    ],
  "permit_type": "[Permit type, e.g., Oversize / Overweight Single Trip]"
}}

Rules:
- Return ONLY valid JSON, nothing else. No markdown formatting.
- Expand all state abbreviations to full state names (e.g., "SD" to "South Dakota", "TX" to "Texas").
- Ensure road names are properly formatted (e.g., "I-29", "US-75", "SD-11").
- Keep route segments in the exact order of travel.
- Do NOT include ramps in `route_segments` (e.g., "Ramp", "On Ramp", "Off Ramp"). Skip ramp entries and keep only primary roads/highways.
- Add `intersection` entries by pairing each consecutive route segment in order:
    1) intersection[0] = route_segments[0] with route_segments[1]
    2) intersection[1] = route_segments[1] with route_segments[2]
    3) continue this pattern to the end
- Keep intersection order exactly aligned with the route segment order.
- Format each intersection as: "[Road A] and [Road B], [City], [State]".
- Do NOT include ramps in `intersection` entries.
- Use the city/state where those two roads connect. If city is unknown but state is known, still include the state.
- If `route_segments` has fewer than 2 items, `intersection` must be [].
- Format locations nicely, guessing the representative city if only a county or highway is provided but you are confident about the general area the route crosses or starts/ends at.
- Use explicit and clean formatting. For `permit_type`, infer it from the text (e.g. "Oversize / Overweight Single Trip", "Overdimension Superload").
- If a field cannot be determined, use null for strings and [] for `route_segments` and `intersection`.

Document text:
{extracted_text}
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an OCR and geospatial extraction specialist. "
                    "Always respond with valid JSON only."
                )
            },
            {"role": "user", "content": prompt}
        ]

        model_to_use = OPENAI_BEST_MODEL
        print(f"  Using OpenAI model: {model_to_use}")

        def _create_completion(model_name):
            return get_openai_client().chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0,
                max_tokens=2500
            )

        try:
            response = _create_completion(model_to_use)
        except APIError as model_error:
            error_text = str(model_error).lower()
            model_unavailable = (
                "not found" in error_text
                or "does not exist" in error_text
                or "access" in error_text
                or "permission" in error_text
                or "not supported" in error_text
                or "incompatible" in error_text
                or "chat.completions" in error_text
            )

            if model_unavailable and OPENAI_FALLBACK_MODEL != model_to_use:
                print(
                    f"  Falling back to model: {OPENAI_FALLBACK_MODEL} "
                    f"(requested model unavailable)"
                )
                response = _create_completion(OPENAI_FALLBACK_MODEL)
            else:
                raise

        response_text = (response.choices[0].message.content or "").strip()

        try:
            route_info = json.loads(response_text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                route_info = json.loads(json_match.group())
            else:
                route_info = {
                    "start_location": None,
                    "end_location": None,
                    "route_segments": [],
                    "intersection": [],
                    "permit_type": "Unknown",
                    "raw_response": response_text
                }

        return normalize_route_information(route_info)
        
    except RateLimitError as e:
        raise RouteExtractionError(
            f"OpenAI quota/rate limit error: {str(e)}",
            status_code=429,
            code="openai_rate_limit_or_quota"
        )
    except AuthenticationError as e:
        raise RouteExtractionError(
            f"OpenAI authentication error: {str(e)}",
            status_code=401,
            code="openai_authentication_error"
        )
    except APIConnectionError as e:
        raise RouteExtractionError(
            f"OpenAI connection error: {str(e)}",
            status_code=503,
            code="openai_connection_error"
        )
    except APIError as e:
        raise RouteExtractionError(
            f"OpenAI API error: {str(e)}",
            status_code=502,
            code="openai_api_error"
        )
    except Exception as e:
        raise RouteExtractionError(
            f"Error extracting route information: {str(e)}",
            status_code=500,
            code="route_extraction_error"
        )


def process_document(file_path):
    """Process a document and extract route information"""
    try:
        # Check if file exists
        if not os.path.exists(file_path):
            print(f" Error: File not found: {file_path}")
            return None
        
        # Check if file is allowed
        if not allowed_file(file_path):
            print(f" Error: File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
            return None
        
        filename = os.path.basename(file_path)
        print(f"\n Processing document: {filename}")
        print("=" * 60)
        
        # Extract text using Textract
        print(" Extracting text from document using AWS Textract...")
        extracted_text, job_id = extract_text_from_document(file_path, filename)
        
        if extracted_text is None:
            print(f" PDF processing started asynchronously. Job ID: {job_id}")
            return None
        
        print(f" Text extraction successful")
        print(f"\n Extracted Text Preview:\n{'-' * 60}")
        print(extracted_text[:500])
        if len(extracted_text) > 500:
            print(f"\n... (total {len(extracted_text)} characters)")
        print("-" * 60)
        
        # Extract route information using OpenAI
        print("\n Extracting route information using OpenAI...")
        route_info = extract_route_information(extracted_text)
        
        print(" Route extraction successful")
        print(f"\n  Route Information:\n{'-' * 60}")
        response_payload = {
            "success": True,
            "route_information": route_info
        }
        print(json.dumps(response_payload, indent=2))
        print("-" * 60)
        
        return {
            "filename": filename,
            "extracted_text": extracted_text,
            "route_information": route_info
        }
        
    except RouteExtractionError:
        raise
    except Exception as e:
        print(f" Error: {str(e)}")
        return None


def main():
    """Main entry point for the CLI"""
    print("\n OCR Module - Route Information Extractor")
    print("=" * 60)
    
    if len(sys.argv) < 2:
        print("\n Usage: python main.py <file_path>")
        print(f"\n Supported formats: {', '.join(ALLOWED_EXTENSIONS)}")
        print("\n Example:")
        print("   python main.py permit.pdf")
        print("   python main.py document.jpg")
        sys.exit(1)
    
    file_path = sys.argv[1]
    process_document(file_path)


if __name__ == '__main__':
    main()
