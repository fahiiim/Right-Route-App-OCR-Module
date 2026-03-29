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
  "permit_type": "[Permit type, e.g., Oversize / Overweight Single Trip]"
}}

Rules:
- Return ONLY valid JSON, nothing else. No markdown formatting.
- Expand all state abbreviations to full state names (e.g., "SD" to "South Dakota", "TX" to "Texas").
- Ensure road names are properly formatted (e.g., "I-29", "US-75", "SD-11").
- Keep route segments in the exact order of travel.
- Format locations nicely, guessing the representative city if only a county or highway is provided but you are confident about the general area the route crosses or starts/ends at.
- Use explicit and clean formatting. For `permit_type`, infer it from the text (e.g. "Oversize / Overweight Single Trip", "Overdimension Superload").
- If a field cannot be determined, use null for strings and [] for segments.

Document text:
{extracted_text}
"""
        
        response = get_openai_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an OCR data extraction specialist. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
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
                    "permit_type": "Unknown",
                    "raw_response": response_text
                }
        
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
