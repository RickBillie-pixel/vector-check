import os
import tempfile
from fastapi import FastAPI, Query, HTTPException
from urllib.parse import urlparse
import pdfplumber
import requests

app = FastAPI(title="VectorCheck API", version="1.0.0")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit

def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ['http', 'https']

@app.get("/vector-check")
def vector_check(pdf_url: str = Query(..., description="URL to the PDF file")):
    """
    Controleert per pagina of er vectorinformatie aanwezig is.
    
    Vector detectie op basis van:
    - Tekst characters (vector tekst)
    - Lijnen en curves (geometrische elementen)
    - Rectangles (vector shapes)
    
    Output = lijst met per pagina:
    - page_number
    - is_vector (True/False)
    - vector_elements (details)
    """
    
    if not is_valid_url(pdf_url):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    tmp_path = None
    
    try:
        # Download PDF
        response = requests.get(pdf_url, stream=True, timeout=30)
        response.raise_for_status()
        
        # Check file size
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="PDF file too large")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            for chunk in response.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_path = tmp_file.name
        
        # Process PDF with pdfplumber
        result = []
        
        with pdfplumber.open(tmp_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                try:
                    # Extract text
                    text = page.extract_text()
                    text_content = text.strip() if text else ""
                    
                    # Extract vector elements
                    lines = page.lines if hasattr(page, 'lines') else []
                    curves = page.curves if hasattr(page, 'curves') else []
                    rects = page.rects if hasattr(page, 'rects') else []
                    chars = page.chars if hasattr(page, 'chars') else []
                    
                    # Determine if page contains vector content
                    has_text = len(text_content) > 10
                    has_vector_graphics = len(lines) > 0 or len(curves) > 0 or len(rects) > 0
                    has_vector_text = len(chars) > 0
                    
                    is_vector = has_text or has_vector_graphics or has_vector_text
                    
                    vector_elements = {
                        "text_chars": len(chars),
                        "lines": len(lines),
                        "curves": len(curves),
                        "rectangles": len(rects),
                        "text_length": len(text_content)
                    }
                    
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_num + 1,
                        "is_vector": is_vector,
                        "vector_elements": vector_elements
                    })
                    
                except Exception as e:
                    # If processing fails, assume it's not vector
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_num + 1,
                        "is_vector": False,
                        "vector_elements": {"error": str(e)}
                    })
        
        return {
            "success": True,
            "page_count": len(result),
            "pages": result
        }
        
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to download PDF: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    
    finally:
        # Always cleanup
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@app.get("/health")
def health_check():
    return {"status": "healthy", "message": "VectorCheck API is running"}

@app.get("/")
def root():
    return {"message": "VectorCheck API - Send PDF URLs to /vector-check endpoint"}