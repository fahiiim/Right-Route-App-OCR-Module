import os
import json
import sys
import time
import uuid
import re
import boto3
import base64
from typing import Any, Dict, List, Tuple
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


def _get_env_int(name, default):
    """Read an integer environment variable with fallback."""
    value = _get_env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_env_bool(name, default=False):
    """Read a boolean environment variable with common truthy values."""
    value = _get_env_value(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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

# Internal extraction controls. These do not change the API response contract.
EXTRACTION_PIPELINE_MODE = (_get_env_value('EXTRACTION_PIPELINE_MODE', 'enhanced') or 'enhanced').lower()
EXTRACTION_SHADOW_COMPARE = _get_env_bool('EXTRACTION_SHADOW_COMPARE', False)
EXTRACTION_MAX_MODEL_ATTEMPTS = max(1, _get_env_int('EXTRACTION_MAX_MODEL_ATTEMPTS', 3))
EXTRACTION_MAX_PROMPT_VARIANTS = max(1, _get_env_int('EXTRACTION_MAX_PROMPT_VARIANTS', 2))
EXTRACTION_WEAK_SEGMENT_THRESHOLD = max(1, _get_env_int('EXTRACTION_WEAK_SEGMENT_THRESHOLD', 2))
EXTRACTION_ENABLE_FALLBACK_PARSER = _get_env_bool('EXTRACTION_ENABLE_FALLBACK_PARSER', True)
EXTRACTION_STAGE_LOGGING = _get_env_bool('EXTRACTION_STAGE_LOGGING', True)

# OCR controls for multi-page resilience.
PDF_TEXT_MAX_PAGES = _get_env_int('PDF_TEXT_MAX_PAGES', 0)  # 0 means all pages.
TEXTRACT_PDF_PNG_MAX_PAGES = max(1, _get_env_int('TEXTRACT_PDF_PNG_MAX_PAGES', 3))

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


ROUTE_JSON_SCHEMA = {
    "name": "route_information",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start_location": {"type": ["string", "null"]},
            "end_location": {"type": ["string", "null"]},
            "route_segments": {
                "type": "array",
                "items": {"type": "string"}
            },
            "intersection": {
                "type": "array",
                "items": {"type": "string"}
            },
            "permit_type": {"type": ["string", "null"]}
        },
        "required": [
            "start_location",
            "end_location",
            "route_segments",
            "intersection",
            "permit_type"
        ]
    }
}


PROMPT_PROFILE_HINTS = {
    "generic": (
        "General permit format. Prioritize complete segment order and infer locations"
        " only when clearly supported by context."
    ),
    "milepost-heavy": (
        "Milepost-heavy permit. Treat MP/MILEPOST tokens as route anchors and use"
        " nearby route IDs, county names, and directional travel clues to keep exact"
        " order from start to end."
    ),
    "table-heavy": (
        "Table-heavy permit. Reconstruct route order row-by-row, left-to-right, then"
        " top-to-bottom. Ignore headers/footers and avoid dropping intermediate rows."
    ),
    "county-abbreviation-heavy": (
        "County-abbreviation-heavy permit. Expand county abbreviations (CO/CNTY) and"
        " state shorthand carefully before building route steps."
    )
}


PROMPT_VARIANT_HINTS = [
    "Primary pass: extract all route steps in exact travel order with no omissions.",
    "Retry pass: cross-check OCR text line-by-line to avoid start/end-only output."
]


PERMIT_TYPE_HINTS = {
    "oversize": "Oversize",
    "overweight": "Overweight",
    "single trip": "Single Trip",
    "superload": "Superload",
    "overdimension": "Overdimension"
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


def _extract_city_state(location_text):
    """Extract a normalized city/state pair from location text when available."""
    text = _safe_string(location_text)
    if not text:
        return None

    parts = [part.strip() for part in text.split(',') if part.strip()]
    if len(parts) < 2:
        return None

    city = parts[0]
    state = parts[-1]
    if not city or not state:
        return None
    return f"{city}, {state}"


def _extract_segment_city_state(segment):
    """Extract city/state pair from a normalized route segment."""
    return _extract_city_state(_extract_segment_location(segment))


def _select_intersection_city_state(current_segment, next_segment):
    """Choose a city/state hint for an intersection between adjacent segments."""
    next_city_state = _extract_segment_city_state(next_segment)
    if next_city_state:
        return next_city_state
    return _extract_segment_city_state(current_segment)


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
    """Normalize a model-provided intersection into pair-plus-city/state format."""
    text = _safe_string(entry)
    if not text:
        return None

    text = re.sub(r'\s+near\s+', ', ', text, flags=re.IGNORECASE)
    text = text.replace('&', ' and ')
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    if ',' in text:
        roads_part, location_part = text.split(',', 1)
        location_part = _extract_city_state(location_part)
    else:
        roads_part, location_part = text, None

    roads_part = roads_part.strip()
    roads_part = re.sub(
        r'^\s*(?:intersection(?:\s+of)?|junction(?:\s+of)?)\s+',
        '',
        roads_part,
        flags=re.IGNORECASE
    )

    match = re.match(r'^\s*(.*?)\s+and\s+(.*?)\s*$', roads_part, flags=re.IGNORECASE)
    if not match:
        return None

    road_a = _strip_direction_suffix(match.group(1).strip()) or match.group(1).strip()
    road_b = _strip_direction_suffix(match.group(2).strip()) or match.group(2).strip()
    if not road_a or not road_b:
        return None

    road_pair = f"{road_a} and {road_b}"
    if location_part:
        return f"{road_pair}, {location_part}"
    return road_pair


def _build_intersections_from_segments(route_segments):
    """Create pairwise intersections from adjacent route segments in order."""
    intersections = []

    for idx in range(len(route_segments) - 1):
        current_segment = route_segments[idx]
        next_segment = route_segments[idx + 1]

        current_route = _extract_route_label(current_segment) or current_segment
        next_route = _extract_route_label(next_segment) or next_segment
        city_state = _select_intersection_city_state(current_segment, next_segment)
        if city_state:
            intersections.append(f"{current_route} and {next_route}, {city_state}")
        else:
            intersections.append(f"{current_route} and {next_route}")

    return intersections


def _normalize_intersections(intersection_data, route_segments):
    """Normalize model intersections and enforce adjacent waypoint intersection order."""
    expected_count = max(len(route_segments) - 1, 0)
    if expected_count == 0:
        return []

    generated = _build_intersections_from_segments(route_segments)

    normalized = []
    for item in _safe_string_list(intersection_data):
        normalized_item = _normalize_intersection_entry(item)
        if normalized_item and not _contains_ramp(normalized_item):
            normalized.append(normalized_item)

    if len(normalized) != expected_count:
        return generated

    for idx, expected in enumerate(generated):
        if normalized[idx].lower() != expected.lower():
            return generated

    return normalized


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


def classify_route_quality(route_info):
    """Classify extraction quality for baseline/benchmark reporting."""
    if not isinstance(route_info, dict):
        return 'empty-segments'

    segments = _safe_string_list(route_info.get('route_segments'))
    has_start = bool(_safe_string(route_info.get('start_location')))
    has_end = bool(_safe_string(route_info.get('end_location')))

    if not segments:
        return 'empty-segments'
    if len(segments) < EXTRACTION_WEAK_SEGMENT_THRESHOLD and has_start and has_end:
        return 'start-end-only'
    if len(segments) < EXTRACTION_WEAK_SEGMENT_THRESHOLD:
        return 'partial-route'
    return 'complete-route'


def _extract_sorted_textract_lines(blocks):
    """Sort Textract LINE blocks by page and geometry for stable reading order."""
    sortable_lines = []
    for block_index, block in enumerate(blocks or []):
        if block.get('BlockType') != 'LINE':
            continue

        text = _safe_string(block.get('Text'))
        if not text:
            continue

        geometry = block.get('Geometry', {}).get('BoundingBox', {})
        top = geometry.get('Top', 0.0)
        left = geometry.get('Left', 0.0)
        page = block.get('Page', 1)
        sortable_lines.append((page, top, left, block_index, text))

    sortable_lines.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [item[4] for item in sortable_lines]


def _normalize_ocr_text(raw_text):
    """Normalize OCR text by trimming noise while preserving order."""
    lines = []
    for raw_line in (raw_text or '').splitlines():
        cleaned = re.sub(r'\s+', ' ', raw_line).strip()
        if not cleaned:
            continue
        if lines and cleaned == lines[-1]:
            continue
        lines.append(cleaned)
    return '\n'.join(lines)


def detect_permit_profile(extracted_text):
    """Detect permit layout profile to route prompt tuning."""
    text = extracted_text or ''
    upper_text = text.upper()
    lines = text.splitlines()

    milepost_hits = len(re.findall(r'\b(?:MP|MILEPOST|MILE POST)\b', upper_text))
    table_hits = sum(
        1
        for line in lines
        if line.count('|') >= 2 or '\t' in line or bool(re.search(r'\s{3,}', line))
    )
    county_abbrev_hits = len(re.findall(r'\b[A-Z]{2,4}\s*(?:CO|CNTY|COUNTY)\b', upper_text))

    signals = {
        'milepost_hits': milepost_hits,
        'table_hits': table_hits,
        'county_abbrev_hits': county_abbrev_hits
    }

    scores = {
        'milepost-heavy': milepost_hits * 2,
        'table-heavy': table_hits * 2,
        'county-abbreviation-heavy': county_abbrev_hits * 2,
        'generic': 1
    }

    top_profile = max(scores, key=scores.get)
    total_signal = max(milepost_hits + table_hits + county_abbrev_hits, 1)
    raw_confidence = scores[top_profile] / float(total_signal + 1)
    confidence = max(0.0, min(raw_confidence, 0.99))

    if top_profile == 'generic' or scores[top_profile] < 2:
        top_profile = 'generic'
        confidence = 0.0

    return {
        'profile': top_profile,
        'confidence': round(confidence, 3),
        'signals': signals
    }


def _build_route_extraction_prompt(extracted_text, profile_name, variant_index):
    """Build a profile-aware extraction prompt."""
    profile_hint = PROMPT_PROFILE_HINTS.get(profile_name, PROMPT_PROFILE_HINTS['generic'])
    variant_hint = PROMPT_VARIANT_HINTS[min(variant_index, len(PROMPT_VARIANT_HINTS) - 1)]

    return f"""
You are an expert at extracting and formatting route information from permit documents spanning all US states.

Profile guidance:
- {profile_hint}

Attempt guidance:
- {variant_hint}

Use geographic knowledge to infer full city/county/state names from abbreviations, road names, mileposts, and county references.

Extract route information and return ONLY a valid JSON object in this exact structure:

{{
  "start_location": "[City], [County] County, [State]",
  "end_location": "[City], [County] County, [State]",
  "route_segments": [
    "[Road/Highway], [City], [State]"
  ],
  "intersection": [
        "[Road from segment i] and [Road from segment i+1], [City], [State]"
  ],
  "permit_type": "[Permit type, e.g., Oversize / Overweight Single Trip]"
}}

Rules:
- Return valid JSON only. No markdown.
- Expand state abbreviations to full names.
- Keep route segments in exact travel order.
- Do not include ramps in route_segments or intersection.
- Build intersections from consecutive route segments in order.
- Each intersection entry must contain adjacent route labels joined by "and" plus ", [City], [State]".
- Use the best city/state inferred from adjacent segments; do not use vague area-only labels.
- If route_segments has fewer than 2 items, intersection must be [].
- If a field cannot be determined, use null for strings and [] for arrays.

Document text:
{extracted_text}
"""


def _parse_model_json_payload(response_text):
    """Parse model output and recover JSON object when wrapped in extra text."""
    text = (response_text or '').strip()
    if not text:
        return None, 'empty_model_response'

    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            return None, 'json_not_found'

        try:
            return json.loads(json_match.group()), None
        except json.JSONDecodeError:
            return None, 'json_decode_error'


def _validate_route_payload(route_info):
    """Validate the model payload shape before normalization."""
    issues = []
    if not isinstance(route_info, dict):
        return None, ['payload_not_object']

    if 'start_location' not in route_info:
        issues.append('missing_start_location')
    if 'end_location' not in route_info:
        issues.append('missing_end_location')
    if 'route_segments' not in route_info:
        issues.append('missing_route_segments')
    if 'intersection' not in route_info:
        issues.append('missing_intersection')
    if 'permit_type' not in route_info:
        issues.append('missing_permit_type')

    if not isinstance(route_info.get('route_segments', []), list):
        issues.append('route_segments_not_list')
    if not isinstance(route_info.get('intersection', []), list):
        issues.append('intersection_not_list')

    validated = {
        'start_location': _safe_string(route_info.get('start_location')),
        'end_location': _safe_string(route_info.get('end_location')),
        'route_segments': _safe_string_list(route_info.get('route_segments')),
        'intersection': _safe_string_list(route_info.get('intersection')),
        'permit_type': _safe_string(route_info.get('permit_type')) or 'Unknown'
    }

    if not validated['route_segments'] and not validated['start_location'] and not validated['end_location']:
        issues.append('empty_route_payload')

    return validated, issues


def _is_weak_route_payload(route_info):
    """Determine if a payload is technically valid but too weak for reliable output."""
    quality = classify_route_quality(route_info)
    return quality in {'empty-segments', 'start-end-only', 'partial-route'}, quality


def _extract_permit_type_deterministic(extracted_text):
    """Infer permit type deterministically from keyword matches."""
    lower_text = (extracted_text or '').lower()
    found_tokens = []
    for keyword, label in PERMIT_TYPE_HINTS.items():
        if keyword in lower_text:
            found_tokens.append(label)

    if not found_tokens:
        return 'Unknown'

    unique_labels = []
    for label in found_tokens:
        if label not in unique_labels:
            unique_labels.append(label)

    return ' / '.join(unique_labels)


def _clean_location_candidate(text):
    """Clean a location candidate extracted from OCR lines."""
    candidate = _safe_string(text)
    if not candidate:
        return None

    candidate = re.sub(r'^\s*(?:AT|IN|NEAR|ON)\s+', '', candidate, flags=re.IGNORECASE)
    candidate = re.sub(r'\s+', ' ', candidate).strip(' ,.;:-')
    if len(candidate) > 120:
        return None
    return candidate or None


def _extract_start_end_locations_deterministic(extracted_text):
    """Extract start/end location hints using keyword-based patterns."""
    lines = [line.strip() for line in (extracted_text or '').splitlines() if line.strip()]

    start_patterns = [
        re.compile(r'\b(?:START|BEGIN|ORIGIN)\b\s*[:\-]?\s*(.+)$', re.IGNORECASE),
        re.compile(r'\bFROM\b\s*[:\-]?\s*(.+)$', re.IGNORECASE)
    ]
    end_patterns = [
        re.compile(r'\b(?:END|DESTINATION)\b\s*[:\-]?\s*(.+)$', re.IGNORECASE),
        re.compile(r'\bTO\b\s*[:\-]?\s*(.+)$', re.IGNORECASE)
    ]

    start_location = None
    end_location = None

    for line in lines:
        for pattern in start_patterns:
            match = pattern.search(line)
            if match and not start_location:
                start_location = _clean_location_candidate(match.group(1))
        for pattern in end_patterns:
            match = pattern.search(line)
            if match and not end_location:
                end_location = _clean_location_candidate(match.group(1))
        if start_location and end_location:
            break

    return start_location, end_location


def _normalize_route_token(raw_token):
    """Normalize route tokens into a stable road format."""
    if not raw_token:
        return None

    token = raw_token.upper().strip()
    token = token.replace('INTERSTATE', 'I').replace('U.S.', 'US').replace('STATE ROUTE', 'SR')
    token = re.sub(r'\s+', '', token)
    token = re.sub(r'(NORTHBOUND|SOUTHBOUND|EASTBOUND|WESTBOUND|NB|SB|EB|WB)$', '', token)

    match = re.match(r'^([A-Z]{1,3})[-]?(\d{1,4})$', token)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def _extract_location_hint_from_line(line):
    """Extract a compact location hint from a route line when possible."""
    patterns = [
        re.compile(r'\b(?:AT|IN|NEAR)\b\s+([^.;]+)$', re.IGNORECASE),
        re.compile(r'\b(?:THROUGH|VIA)\b\s+([^.;]+)$', re.IGNORECASE)
    ]

    for pattern in patterns:
        match = pattern.search(line)
        if match:
            location = _clean_location_candidate(match.group(1))
            if location:
                return location
    return None


def _dedupe_consecutive(items):
    """Remove only consecutive duplicates while preserving travel order."""
    deduped = []
    for item in items:
        if not deduped or item != deduped[-1]:
            deduped.append(item)
    return deduped


def _extract_route_segments_deterministic(extracted_text):
    """Extract route segments without LLM using regex and ordering heuristics."""
    route_pattern = re.compile(
        r'\b(?:INTERSTATE\s*\d{1,3}|I[-\s]?\d{1,3}|US[-\s]?\d{1,3}|SR[-\s]?\d{1,3}|SH[-\s]?\d{1,3}|[A-Z]{2,3}[-\s]?\d{1,3})\b',
        re.IGNORECASE
    )

    lines = [line.strip() for line in (extracted_text or '').splitlines() if line.strip()]
    segments = []

    for line in lines:
        if _contains_ramp(line):
            continue

        location_hint = _extract_location_hint_from_line(line)
        token_matches = route_pattern.findall(line)
        for raw_token in token_matches:
            normalized_token = _normalize_route_token(raw_token)
            if not normalized_token:
                continue
            if location_hint:
                segments.append(f"{normalized_token}, {location_hint}")
            else:
                segments.append(normalized_token)

    if not segments:
        all_tokens = route_pattern.findall(extracted_text or '')
        segments = [
            _normalize_route_token(token)
            for token in all_tokens
            if _normalize_route_token(token)
        ]

    segments = _dedupe_consecutive([segment for segment in segments if segment])
    return segments


def extract_route_information_deterministic(extracted_text):
    """Deterministic non-LLM route extraction fallback."""
    normalized_text = _normalize_ocr_text(extracted_text)
    start_location, end_location = _extract_start_end_locations_deterministic(normalized_text)
    route_segments = _extract_route_segments_deterministic(normalized_text)

    route_info = {
        'start_location': start_location,
        'end_location': end_location,
        'route_segments': route_segments,
        'intersection': _build_intersections_from_segments(route_segments),
        'permit_type': _extract_permit_type_deterministic(normalized_text)
    }

    # If explicit start/end fields are missing, derive rough placeholders from first/last segment.
    if not route_info['start_location'] and route_segments:
        route_info['start_location'] = route_segments[0]
    if not route_info['end_location'] and route_segments:
        route_info['end_location'] = route_segments[-1]

    return normalize_route_information(route_info)


def _build_attempt_plan(profile_name):
    """Build bounded model attempts across prompt variants and fallback model."""
    attempts = []
    models = [OPENAI_BEST_MODEL]
    if OPENAI_FALLBACK_MODEL != OPENAI_BEST_MODEL:
        models.append(OPENAI_FALLBACK_MODEL)

    variant_count = min(EXTRACTION_MAX_PROMPT_VARIANTS, len(PROMPT_VARIANT_HINTS))
    for model_name in models:
        for variant_index in range(variant_count):
            attempts.append((model_name, variant_index, profile_name))
            if len(attempts) >= EXTRACTION_MAX_MODEL_ATTEMPTS:
                return attempts
    return attempts


def _raise_openai_route_error(error):
    """Map OpenAI SDK errors to stable HTTP-friendly route extraction errors."""
    if isinstance(error, RateLimitError):
        raise RouteExtractionError(
            f"OpenAI quota/rate limit error: {str(error)}",
            status_code=429,
            code='openai_rate_limit_or_quota'
        )
    if isinstance(error, AuthenticationError):
        raise RouteExtractionError(
            f"OpenAI authentication error: {str(error)}",
            status_code=401,
            code='openai_authentication_error'
        )
    if isinstance(error, APIConnectionError):
        raise RouteExtractionError(
            f"OpenAI connection error: {str(error)}",
            status_code=503,
            code='openai_connection_error'
        )
    if isinstance(error, APIError):
        raise RouteExtractionError(
            f"OpenAI API error: {str(error)}",
            status_code=502,
            code='openai_api_error'
        )

    raise RouteExtractionError(
        f"Error extracting route information: {str(error)}",
        status_code=500,
        code='route_extraction_error'
    )


def _compare_shadow_results(new_route_info, old_route_info):
    """Summarize extraction delta between enhanced and legacy paths."""
    new_segments = len(_safe_string_list((new_route_info or {}).get('route_segments')))
    old_segments = len(_safe_string_list((old_route_info or {}).get('route_segments')))

    return {
        'new_quality': classify_route_quality(new_route_info or {}),
        'legacy_quality': classify_route_quality(old_route_info or {}),
        'new_segment_count': new_segments,
        'legacy_segment_count': old_segments
    }


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
                all_blocks = []
                next_token = None

                while True:
                    params = {'JobId': job_id}
                    if next_token:
                        params['NextToken'] = next_token

                    page_response = get_textract_client().get_document_text_detection(**params)
                    all_blocks.extend(page_response.get('Blocks', []))

                    next_token = page_response.get('NextToken')
                    if not next_token:
                        break

                sorted_lines = _extract_sorted_textract_lines(all_blocks)
                extracted_text = _normalize_ocr_text('\n'.join(sorted_lines))
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
                page_count = len(pdf_doc)
                max_pages = page_count if PDF_TEXT_MAX_PAGES <= 0 else min(PDF_TEXT_MAX_PAGES, page_count)
                pdf_chunks = []

                for page_num in range(max_pages):
                    page = pdf_doc[page_num]
                    pdf_chunks.append(page.get_text("text", sort=True))

                pdf_doc.close()
                pdf_text = _normalize_ocr_text('\n'.join(pdf_chunks))

                if pdf_text.strip():
                    print(f"  ✅ PDF text extracted with PyMuPDF ({max_pages}/{page_count} pages)")
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
                print(
                    f"  ⚠️  Textract rejected PDF bytes; retrying with rendered PNG pages "
                    f"(max {TEXTRACT_PDF_PNG_MAX_PAGES})..."
                )
                try:
                    pdf_doc = fitz_module.open(file_path)
                    if len(pdf_doc) == 0:
                        pdf_doc.close()
                        raise RouteExtractionError(
                            "PDF has no pages.",
                            status_code=400,
                            code="empty_pdf"
                        )

                    combined_blocks = []
                    png_page_count = min(TEXTRACT_PDF_PNG_MAX_PAGES, len(pdf_doc))

                    for page_index in range(png_page_count):
                        page = pdf_doc[page_index]
                        pix = page.get_pixmap(dpi=200, alpha=False)
                        image_bytes = pix.tobytes("png")
                        page_response = get_textract_client().detect_document_text(Document={'Bytes': image_bytes})
                        for block in page_response.get('Blocks', []):
                            block_with_page = dict(block)
                            block_with_page.setdefault('Page', page_index + 1)
                            combined_blocks.append(block_with_page)

                    pdf_doc.close()
                    response = {'Blocks': combined_blocks}
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
        
        # Extract text from response in stable geometric order.
        extracted_lines = _extract_sorted_textract_lines(response.get('Blocks', []))
        extracted_text = _normalize_ocr_text('\n'.join(extracted_lines))
        
        if not extracted_text:
            raise RouteExtractionError(
                "No text could be extracted from the document.",
                status_code=400,
                code="empty_extracted_text"
            )
        
        return extracted_text, None
            
    except RouteExtractionError:
        raise
    except Exception as e:
        raise Exception(f"Error extracting text from document: {str(e)}")


def _create_completion_with_optional_schema(messages, model_name):
    """Create a chat completion with schema constraints when supported."""
    try:
        response = get_openai_client().chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            max_tokens=10000,
            response_format={
                'type': 'json_schema',
                'json_schema': ROUTE_JSON_SCHEMA
            }
        )
        return response, True
    except TypeError:
        # Older OpenAI SDK/model combinations may not support response_format here.
        response = get_openai_client().chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            max_tokens=10000
        )
        return response, False


def _extract_route_information_legacy(extracted_text):
    """Original extraction path kept for rollback and shadow comparisons."""
    try:
        prompt = _build_route_extraction_prompt(extracted_text, 'generic', 0)
        messages = [
            {
                'role': 'system',
                'content': (
                    'You are an OCR and geospatial extraction specialist. '
                    'Always respond with valid JSON only.'
                )
            },
            {'role': 'user', 'content': prompt}
        ]

        model_to_use = OPENAI_BEST_MODEL
        print(f"  Using OpenAI model: {model_to_use} (legacy pipeline)")

        def _create_completion(model_name):
            return get_openai_client().chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0,
                max_tokens=10000
            )

        try:
            response = _create_completion(model_to_use)
        except APIError as model_error:
            error_text = str(model_error).lower()
            model_unavailable = (
                'not found' in error_text
                or 'does not exist' in error_text
                or 'access' in error_text
                or 'permission' in error_text
                or 'not supported' in error_text
                or 'incompatible' in error_text
                or 'chat.completions' in error_text
            )

            if model_unavailable and OPENAI_FALLBACK_MODEL != model_to_use:
                print(
                    f"  Falling back to model: {OPENAI_FALLBACK_MODEL} "
                    f"(requested model unavailable)"
                )
                response = _create_completion(OPENAI_FALLBACK_MODEL)
            else:
                raise

        response_text = (response.choices[0].message.content or '').strip()
        route_info, parse_issue = _parse_model_json_payload(response_text)

        if parse_issue or not isinstance(route_info, dict):
            route_info = {
                'start_location': None,
                'end_location': None,
                'route_segments': [],
                'intersection': [],
                'permit_type': 'Unknown',
                'raw_response': response_text
            }

        return normalize_route_information(route_info)
    except Exception as error:
        _raise_openai_route_error(error)


def extract_route_information_with_diagnostics(extracted_text):
    """Enhanced extraction orchestration with retries, validation, and fallback."""
    normalized_text = _normalize_ocr_text(extracted_text)
    profile = detect_permit_profile(normalized_text)

    diagnostics = {
        'pipeline_mode': EXTRACTION_PIPELINE_MODE,
        'ocr_characters': len(normalized_text),
        'profile': profile,
        'model_attempts': [],
        'final_source': None
    }

    if EXTRACTION_PIPELINE_MODE == 'legacy':
        legacy_result = _extract_route_information_legacy(normalized_text)
        diagnostics['final_source'] = 'legacy-llm'
        diagnostics['quality_classification'] = classify_route_quality(legacy_result)
        return legacy_result, diagnostics

    attempt_plan = _build_attempt_plan(profile['profile'])
    accepted_result = None
    weak_candidate = None
    weak_candidate_count = -1
    weak_candidate_source = None
    first_model_error = None

    for attempt_number, (model_name, variant_index, profile_name) in enumerate(attempt_plan, start=1):
        prompt = _build_route_extraction_prompt(normalized_text, profile_name, variant_index)
        messages = [
            {
                'role': 'system',
                'content': (
                    'You are an OCR and geospatial extraction specialist. '
                    'Always respond with valid JSON only.'
                )
            },
            {'role': 'user', 'content': prompt}
        ]

        attempt_record = {
            'attempt': attempt_number,
            'model': model_name,
            'variant': variant_index + 1,
            'profile': profile_name,
            'status': 'failed'
        }

        try:
            response, schema_enforced = _create_completion_with_optional_schema(messages, model_name)
            attempt_record['schema_enforced'] = schema_enforced
        except (RateLimitError, AuthenticationError, APIConnectionError, APIError) as model_error:
            if first_model_error is None:
                first_model_error = model_error
            attempt_record['status'] = 'model_error'
            attempt_record['error_type'] = type(model_error).__name__
            diagnostics['model_attempts'].append(attempt_record)
            continue
        except Exception as model_error:
            if first_model_error is None:
                first_model_error = model_error
            attempt_record['status'] = 'unexpected_error'
            attempt_record['error_type'] = type(model_error).__name__
            diagnostics['model_attempts'].append(attempt_record)
            continue

        response_text = (response.choices[0].message.content or '').strip()
        payload, parse_issue = _parse_model_json_payload(response_text)
        if parse_issue:
            attempt_record['status'] = 'parse_error'
            attempt_record['parse_issue'] = parse_issue
            diagnostics['model_attempts'].append(attempt_record)
            continue

        validated_payload, validation_issues = _validate_route_payload(payload)
        if validation_issues:
            attempt_record['status'] = 'validation_error'
            attempt_record['validation_issues'] = validation_issues
            attempt_record['segment_count'] = len(_safe_string_list((validated_payload or {}).get('route_segments')))
            diagnostics['model_attempts'].append(attempt_record)
            continue

        normalized_candidate = normalize_route_information(validated_payload)
        is_weak, quality = _is_weak_route_payload(normalized_candidate)
        attempt_record['quality'] = quality
        attempt_record['segment_count'] = len(_safe_string_list(normalized_candidate.get('route_segments')))

        if is_weak:
            attempt_record['status'] = 'weak_candidate'
            if attempt_record['segment_count'] > weak_candidate_count:
                weak_candidate = normalized_candidate
                weak_candidate_count = attempt_record['segment_count']
                weak_candidate_source = f"llm:{model_name}:variant{variant_index + 1}"
            diagnostics['model_attempts'].append(attempt_record)
            continue

        attempt_record['status'] = 'accepted'
        diagnostics['model_attempts'].append(attempt_record)
        accepted_result = normalized_candidate
        diagnostics['final_source'] = f"llm:{model_name}:variant{variant_index + 1}"
        break

    if accepted_result is None and EXTRACTION_ENABLE_FALLBACK_PARSER:
        fallback_result = extract_route_information_deterministic(normalized_text)
        fallback_quality = classify_route_quality(fallback_result)
        fallback_segment_count = len(_safe_string_list(fallback_result.get('route_segments')))
        diagnostics['fallback_quality'] = fallback_quality
        diagnostics['fallback_segment_count'] = fallback_segment_count

        if weak_candidate is None or fallback_segment_count >= weak_candidate_count:
            accepted_result = fallback_result
            diagnostics['final_source'] = 'deterministic-fallback'
        else:
            accepted_result = weak_candidate
            diagnostics['final_source'] = weak_candidate_source or 'llm-weak-candidate'

    if accepted_result is None and weak_candidate is not None:
        accepted_result = weak_candidate
        diagnostics['final_source'] = weak_candidate_source or 'llm-weak-candidate'

    if accepted_result is None:
        if first_model_error is not None:
            _raise_openai_route_error(first_model_error)
        accepted_result = normalize_route_information(
            {
                'start_location': None,
                'end_location': None,
                'route_segments': [],
                'intersection': [],
                'permit_type': 'Unknown'
            }
        )
        diagnostics['final_source'] = 'empty-default'

    if EXTRACTION_SHADOW_COMPARE:
        try:
            legacy_result = _extract_route_information_legacy(normalized_text)
            diagnostics['shadow_comparison'] = _compare_shadow_results(accepted_result, legacy_result)
        except Exception as shadow_error:
            diagnostics['shadow_comparison'] = {
                'error_type': type(shadow_error).__name__,
                'message': str(shadow_error)
            }

    diagnostics['quality_classification'] = classify_route_quality(accepted_result)
    return accepted_result, diagnostics


def extract_route_information(extracted_text):
    """Public extraction API returning route information only."""
    route_info, _diagnostics = extract_route_information_with_diagnostics(extracted_text)
    return route_info


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
        
        # Extract route information using resilient pipeline
        print("\n Extracting route information using resilient pipeline...")
        route_info, diagnostics = extract_route_information_with_diagnostics(extracted_text)

        if EXTRACTION_STAGE_LOGGING:
            stage_summary = {
                'pipeline_mode': diagnostics.get('pipeline_mode'),
                'profile': diagnostics.get('profile', {}).get('profile'),
                'quality': diagnostics.get('quality_classification'),
                'final_source': diagnostics.get('final_source'),
                'model_attempts': len(diagnostics.get('model_attempts', [])),
                'fallback_quality': diagnostics.get('fallback_quality')
            }
            print("\n Extraction diagnostics:")
            print(json.dumps(stage_summary, indent=2))
        
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
