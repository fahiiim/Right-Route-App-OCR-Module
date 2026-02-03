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

ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp'}

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

1. start_location: The starting point as a GEOCODABLE address.
   - Format: "Nearby City or Town, County, State" OR "Highway Exit Name, City, State"
   - NEVER use mile post markers (MP) alone - convert them to the nearest city/town
   - Example: "Sisseton, Roberts County, SD" instead of "I-29 & MP 252.65, Roberts, SD"

2. end_location: The ending point as a GEOCODABLE address.
   - Same rules as start_location

3. route_segments: A sequential array of GEOCODABLE waypoints. 

   CRITICAL RULES FOR GEOCODABLE LOCATIONS:
   
   ⚠️ MOST IMPORTANT RULE: NEVER PUT TWO HIGHWAYS IN ONE SEGMENT!
   - WRONG: "I-29 and I-90, Sioux Falls, SD" (two highways = confuses map)
   - WRONG: "I-29 & I-90 Interchange, Sioux Falls, SD" (still two highways)
   - CORRECT: "Sioux Falls, SD" (just the city where highways meet)
   
   A) MILE POST (MP) MARKERS - NEVER USE MP IN OUTPUT:
      - MP markers like "MP 252.65" are NOT geocodable
      - Convert to the nearest city/town ONLY
      - Example: "I-29 MP 252.65 in Roberts County" → "Sisseton, SD"
   
   B) HIGHWAY INTERCHANGES (WHERE TWO HIGHWAYS MEET):
      - NEVER combine two highway names in one waypoint
      - Just use the CITY NAME where they intersect
      - Example: "I-29 meets I-90" → "Sioux Falls, SD"
      - Example: "I-90 meets SD-11" → "Brandon, SD" or "Sioux Falls, SD"
   
   C) SINGLE HIGHWAY WITH EXIT OR STREET:
      - Format: "[City], [State]" OR "[Street Name], [City], [State]"
      - Example: "Exit 83, Sioux Falls, SD" or just "Sioux Falls, SD"
      - Example: "26th Street, Sioux Falls, SD"
   
   D) STATE/COUNTY BORDER CROSSINGS:
      - Use the nearest town name ONLY
      - Example: "SD-MN Border on I-29" → "Sisseton, SD"
   
   E) COUNTY REFERENCES:
      - NEVER use county names as locations
      - Always convert to the main city/town in that county
      - Example: "Minnehaha County" → "Sioux Falls, SD"
      - Example: "Roberts County" → "Sisseton, SD"

   EXAMPLE TRANSFORMATION:
   BAD (causes wrong map points):
   - "I-29 & MP 252.65, Roberts, SD" (has MP marker)
   - "I-29 & I-90, Minnehaha, SD" (TWO highways together!)
   - "I-90 & SD-11, Minnehaha, SD" (TWO highways together!)
   - "SD-11 & SD-42, Minnehaha, SD" (TWO highways together!)
   
   GOOD (accurate map points):
   - "Sisseton, SD"
   - "Sioux Falls, SD"
   - "Brandon, SD"
   - "Hartford, SD"

4. permit_type: The type of permit (e.g., "Overdimension Superload", "Oversize/Overweight Single Trip")

IMPORTANT: Every location in route_segments MUST be geocodable by Google Maps or similar services. Use city names, not mile posts or county names alone.

Document text:
{extracted_text}

Return ONLY a valid JSON object with these four fields. Use null for any fields not found in the document.
"""
        else:
            # Generic prompt for other permit types
            prompt = f"""
You are an expert at extracting route information from permit documents and travel documents.

From the provided document text, extract the following information in JSON format:

1. start_location: The starting point as a GEOCODABLE address.
   - Format: "City, State" OR "Street/Highway Intersection, City, State"
   - NEVER use mile post markers (MP) alone

2. end_location: The ending point as a GEOCODABLE address.

3. route_segments: An ordered array of GEOCODABLE waypoints along the route.

   CRITICAL RULES FOR GEOCODABLE LOCATIONS:
   
   ⚠️ MOST IMPORTANT RULE: NEVER PUT TWO HIGHWAYS/ROUTES IN ONE SEGMENT!
   - WRONG: "I-29 and I-90, City, State" (two highways = wrong map point)
   - WRONG: "US-81 & SD-34, City, State" (two routes = wrong map point)
   - CORRECT: "Sioux Falls, SD" (just the city name)
   
   A) MILE POST (MP) MARKERS - NEVER USE:
      - Convert MP markers to nearest city/town ONLY
      - "I-29 MP 252" → "Sisseton, SD"
   
   B) HIGHWAY INTERCHANGES:
      - Just use the CITY NAME, not the highway names
      - "I-29 meets I-90" → "Sioux Falls, SD"
   
   C) LOCATIONS FORMAT:
      - Best: "City, State" (e.g., "Sioux Falls, SD")
      - Also OK: "Street Name, City, State" (e.g., "Main Street, Madison, SD")
      - NEVER: "Highway1 & Highway2, City, State"
   
   D) COUNTY REFERENCES:
      - Convert to main city in that county
      - "Minnehaha County" → "Sioux Falls, SD"

   EXAMPLE:
   BAD: "I-29 & MP 252.65, Roberts, SD"
   GOOD: "Sisseton, SD"
   
   BAD: "I-90 & I-29, Minnehaha, SD"  
   GOOD: "Sioux Falls, SD"

4. permit_type: The type of permit if identifiable

IMPORTANT: Every waypoint MUST be geocodable by map services. Use actual city/town names, not just highway mile markers or county names.

Document text:
{extracted_text}

Return ONLY a valid JSON object with these fields. If any information is not found, use null for that field.
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
            print(f"❌ Error: File not found: {file_path}")
            return None
        
        # Check if file is allowed
        if not allowed_file(file_path):
            print(f"❌ Error: File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}")
            return None
        
        filename = os.path.basename(file_path)
        print(f"\n📄 Processing document: {filename}")
        print("=" * 60)
        
        # Extract text using Textract
        print("🔍 Extracting text from document using AWS Textract...")
        extracted_text, job_id = extract_text_from_document(file_path, filename)
        
        if extracted_text is None:
            print(f"⏳ PDF processing started asynchronously. Job ID: {job_id}")
            return None
        
        print(f"✅ Text extraction successful")
        print(f"\n📝 Extracted Text Preview:\n{'-' * 60}")
        print(extracted_text[:500])
        if len(extracted_text) > 500:
            print(f"\n... (total {len(extracted_text)} characters)")
        print("-" * 60)
        
        # Extract route information using OpenAI
        print("\n🤖 Extracting route information using OpenAI...")
        route_info = extract_route_information(extracted_text)
        
        print("✅ Route extraction successful")
        print(f"\n🗺️  Route Information:\n{'-' * 60}")
        print(json.dumps(route_info, indent=2))
        print("-" * 60)
        
        return {
            "filename": filename,
            "extracted_text": extracted_text,
            "route_information": route_info
        }
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None


def main():
    """Main entry point for the CLI"""
    print("\n🌐 OCR Module - Route Information Extractor")
    print("=" * 60)
    
    if len(sys.argv) < 2:
        print("\n📖 Usage: python main.py <file_path>")
        print(f"\n✅ Supported formats: {', '.join(ALLOWED_EXTENSIONS)}")
        print("\n📝 Example:")
        print("   python main.py permit.pdf")
        print("   python main.py document.jpg")
        sys.exit(1)
    
    file_path = sys.argv[1]
    process_document(file_path)


if __name__ == '__main__':
    main()
