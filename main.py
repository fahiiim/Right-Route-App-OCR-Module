import os
import json
import sys
import boto3
import base64
from pathlib import Path
from dotenv import load_dotenv
from botocore.config import Config
from openai import OpenAI
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# Load environment variables
load_dotenv()

# Lazy initialization - clients created on first use
_textract_client = None
_s3_client = None
_openai_client = None

# Boto3 config with timeouts to prevent hanging
_boto_config = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={'max_attempts': 2}
)


def get_textract_client():
    """Lazy initialization of Textract client"""
    global _textract_client
    if _textract_client is None:
        _textract_client = boto3.client(
            'textract',
            region_name=os.getenv('AWS_REGION'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            config=_boto_config
        )
    return _textract_client


def get_s3_client():
    """Lazy initialization of S3 client"""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            's3',
            region_name=os.getenv('AWS_REGION'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            config=_boto_config
        )
    return _s3_client


def get_openai_client():
    """Lazy initialization of OpenAI client"""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
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
    
    import re
    
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


def extract_text_from_document(file_path, document_name):
    """Extract text from document using AWS Textract"""
    try:
        # Handle PDF files - extract text directly from PDF
        if file_path.lower().endswith('.pdf'):
            if not PDF_SUPPORT:
                raise Exception("PDF support requires 'PyMuPDF' library. Install with: pip install pymupdf")
            
            print("  Extracting text from PDF...")
            try:
                # Try PyMuPDF extraction first (handles encrypted PDFs better)
                pdf_doc = fitz.open(file_path)
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
            
            # Fallback to AWS Textract for PDFs
            with open(file_path, 'rb') as document:
                document_bytes = document.read()
        else:
            # For images, read directly
            with open(file_path, 'rb') as document:
                document_bytes = document.read()
        
        # Use synchronous detection with Textract
        print("  Calling AWS Textract detect_document_text...")
        response = get_textract_client().detect_document_text(Document={'Bytes': document_bytes})
        
        # Extract text from response
        extracted_text = '\n'.join([block['Text'] for block in response['Blocks'] if block['BlockType'] == 'LINE'])
        
        if not extracted_text:
            raise Exception("No text could be extracted from the document")
        
        return extracted_text, None
            
    except Exception as e:
        raise Exception(f"Error extracting text from document: {str(e)}")


def detect_permit_type(extracted_text):
    """Detect the type of permit document"""
    text_lower = extracted_text.lower()
    
    # Check for Overdimension/Oversize/Overweight permits
    if any(keyword in text_lower for keyword in ['overdimension', 'superload', 'oversize', 'overweight', 'department of transportation']):
        return 'overdimension'
    
    # Default to generic permit type
    return 'generic'


def extract_route_information(extracted_text):
    """Use OpenAI to extract route information from OCR text"""
    try:
        # Detect permit type
        permit_type = detect_permit_type(extracted_text)
        
        # Use specialized prompt based on permit type
        if permit_type == 'overdimension':
            prompt = f"""
You are an expert at extracting route information from Overdimension Superload, Oversize, and Overweight permits.

From the provided permit document text, extract the following information in JSON format:

1. start_point: An object containing:
   - raw_text: The exact text from the document describing the start point
   - parsed: An object with:
     * road: The road/highway identifier (e.g., "IA-9", "US-75", "I-29")
     * direction: Direction abbreviation (e.g., "EB", "WB", "NB", "SB")
     * intersection: Cross street or intersection identifier if mentioned
     * county: County name if mentioned
     * state: State abbreviation (e.g., "IA", "SD")
     * city: City name if mentioned

2. end_point: An object with the same structure as start_point (raw_text and parsed fields)

3. route_steps: A sequential array where each step contains:
   - step_id: Sequential number starting from 1
   - raw_text: The exact text from the document for this route segment
   - parsed: An object with fields (include only fields that are present):
     * road: Road/highway identifier
     * direction: Direction abbreviation
     * action: Action type (e.g., "start", "merge", "continue", "turn", "exit")
     * city: City name if mentioned
     * county: County name if mentioned
     * state: State abbreviation if mentioned
     * intersection: Cross street or intersection if mentioned
     * mile_post: Mile post marker if mentioned

4. permit_type: The type of permit (e.g., "Overdimension Superload", "Oversize/Overweight Single Trip")

PARSING RULES:
- Extract the EXACT raw text as it appears in the document
- For parsed fields, extract individual components:
  * Roads: "IA-9", "US-75", "I-29", "SD-11", etc.
  * Directions: "EB" (Eastbound), "WB" (Westbound), "NB" (Northbound), "SB" (Southbound)
  * Actions: Infer from context (start, merge, continue, turn, exit)
  * Intersections: Cross streets, exits, or reference points
  * Cities: Town or city names
  * Counties: County names (in parentheses usually)
  * States: Two-letter state codes

EXAMPLE FORMAT:
{{
  "start_point": {{
    "raw_text": "IA-9 EB AT A10 INTERSECTION (LYON)(STATE BORDER OF SOUTH DAKOTA)",
    "parsed": {{
      "road": "IA-9",
      "direction": "EB",
      "intersection": "A10",
      "county": "Lyon",
      "state": "IA"
    }}
  }},
  "end_point": {{
    "raw_text": "US-18 WB AT MN-60 INTERSECTION (LYON)",
    "parsed": {{
      "road": "US-18",
      "direction": "WB",
      "intersection": "MN-60",
      "county": "Lyon",
      "state": "IA"
    }}
  }},
  "route_steps": [
    {{
      "step_id": 1,
      "raw_text": "START ON IA-9 EB AT A10 INTERSECTION",
      "parsed": {{
        "road": "IA-9",
        "direction": "EB",
        "action": "start",
        "intersection": "A10"
      }}
    }},
    {{
      "step_id": 2,
      "raw_text": "US-75 SB",
      "parsed": {{
        "road": "US-75",
        "direction": "SB",
        "action": "merge"
      }}
    }},
    {{
      "step_id": 3,
      "raw_text": "IA-9 EB (IN ROCK RAPIDS AT N UNION ST)",
      "parsed": {{
        "road": "IA-9",
        "direction": "EB",
        "city": "Rock Rapids",
        "intersection": "N Union St"
      }}
    }}
  ],
  "permit_type": "Overdimension Superload"
}}

Document text:
{extracted_text}

Return ONLY a valid JSON object with these fields. Use null for fields not found in the document.
"""
        else:
            # Generic prompt for other permit types
            prompt = f"""
You are an expert at extracting route information from permit documents and travel documents.

From the provided document text, extract the following information in JSON format:

1. start_point: An object containing:
   - raw_text: The exact text from the document describing the start point
   - parsed: An object with:
     * road: The road/highway identifier (e.g., "IA-9", "US-75")
     * direction: Direction abbreviation (e.g., "EB", "WB", "NB", "SB")
     * intersection: Cross street if mentioned
     * county: County name if mentioned
     * state: State abbreviation (e.g., "IA", "SD")
     * city: City name if mentioned

2. end_point: An object with the same structure as start_point

3. route_steps: A sequential array where each step contains:
   - step_id: Sequential number starting from 1
   - raw_text: The exact text from the document for this route segment
   - parsed: An object with fields (include only fields present):
     * road: Road/highway identifier
     * direction: Direction abbreviation
     * action: Action type (e.g., "start", "merge", "continue", "turn", "exit")
     * city: City name if mentioned
     * county: County name if mentioned
     * state: State abbreviation if mentioned
     * intersection: Cross street or intersection if mentioned
     * mile_post: Mile post marker if mentioned

4. permit_type: The type of permit if identifiable

PARSING RULES:
- Extract EXACT raw text from the document
- Parse individual components into separate fields
- Infer action types from context
- Only include fields that are actually present

EXAMPLE FORMAT:
{{
  "start_point": {{
    "raw_text": "IA-9 EB AT A10 INTERSECTION (LYON)",
    "parsed": {{
      "road": "IA-9",
      "direction": "EB",
      "intersection": "A10",
      "county": "Lyon",
      "state": "IA"
    }}
  }},
  "end_point": {{
    "raw_text": "US-18 WB AT MN-60",
    "parsed": {{
      "road": "US-18",
      "direction": "WB",
      "intersection": "MN-60"
    }}
  }},
  "route_steps": [
    {{
      "step_id": 1,
      "raw_text": "START ON IA-9 EB",
      "parsed": {{
        "road": "IA-9",
        "direction": "EB",
        "action": "start"
      }}
    }}
  ],
  "permit_type": "Single Trip"
}}

Document text:
{extracted_text}

Return ONLY a valid JSON object with these fields. Use null for any fields not found.
"""
        
        response = get_openai_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an OCR data extraction specialist. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2000
        )
        
        # Parse the response
        response_text = response.choices[0].message.content.strip()
        
        # Try to extract JSON from response
        try:
            route_info = json.loads(response_text)
        except json.JSONDecodeError:
            # If response contains JSON but with extra text, try to extract it
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                route_info = json.loads(json_match.group())
            else:
                route_info = {
                    "start_location": None,
                    "end_location": None,
                    "route_segments": [],
                    "raw_response": response_text
                }
        
        # Expand abbreviations in the entire extracted route information (recursive)
        route_info = expand_abbreviations_in_dict(route_info)
        
        return route_info
        
    except Exception as e:
        raise Exception(f"Error extracting route information: {str(e)}")


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
        print(json.dumps(route_info, indent=2))
        print("-" * 60)
        
        return {
            "filename": filename,
            "extracted_text": extracted_text,
            "route_information": route_info
        }
        
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
