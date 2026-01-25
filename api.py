import os
import json
import tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from main import (
    process_document,
    ALLOWED_EXTENSIONS,
    allowed_file
)

# Load environment variables
load_dotenv()

app = FastAPI(
    title="Social WiFi OCR Module API",
    description="Extract route information from permit documents",
    version="1.0.0"
)

# In-memory storage for extracted OCR results
extracted_data = {}


@app.post("/api/ocr/extract")
async def extract_route_information(file: UploadFile = File(...)):
    """
    Upload a document (PDF, JPG, PNG, etc.) and extract route information.
    
    - **file**: Document file to process
    - **Returns**: Extracted text and route information with segments
    """
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        
        if not allowed_file(file.filename):
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Supported: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # Process the document
            result = process_document(temp_file_path)
            
            if result is None:
                raise HTTPException(status_code=500, detail="Failed to process document")
            
            # Store the extracted data using filename as key
            extracted_data[result["filename"]] = {
                "filename": result["filename"],
                "route_information": result["route_information"],
                "extracted_text": result.get("extracted_text", "")
            }
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "filename": result["filename"],
                    "route_information": result["route_information"]
                }
            )
        
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing document: {str(e)}")
    


@app.get("/api/ocr/extract/{filename}")
async def get_extracted_data(filename: str):
    """
    Retrieve previously extracted OCR data by filename.
    
    - **filename**: The filename of the document to retrieve
    - **Returns**: The stored route information and extracted text
    """
    if filename not in extracted_data:
        raise HTTPException(
            status_code=404,
            detail=f"No extracted data found for filename: {filename}"
        )
    
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": extracted_data[filename]
        }
    )


@app.get("/api/ocr/list")
async def list_extracted_documents():
    """
    List all extracted documents stored in memory.
    
    - **Returns**: List of all filenames with their extracted data
    """
    if not extracted_data:
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "count": 0,
                "documents": []
            }
        )
    
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "count": len(extracted_data),
            "documents": list(extracted_data.keys()),
            "data": extracted_data
        }
    )


@app.get("/api/ocr/clear")
async def clear_extracted_data():
    """
    Clear all stored extracted data from memory.
    
    - **Returns**: Confirmation message
    """
    count = len(extracted_data)
    extracted_data.clear()
    
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": f"Cleared {count} documents from storage"
        }
    )


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "Social WiFi OCR Module API",
        "version": "1.0.0"
    }


@app.get("/api/supported-formats")
async def get_supported_formats():
    """Get list of supported file formats"""
    return {
        "supported_formats": list(ALLOWED_EXTENSIONS)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="10.10.7.64", port=8001)

