import os
import tempfile
from fastapi import FastAPI, Query, HTTPException
from urllib.parse import urlparse
import pdfplumber
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = FastAPI(title="VectorCheck API", version="1.0.0")

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit

def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ['http', 'https']

def create_session_with_retries():
    """Create a requests session with retry strategy"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

@app.get("/vector-check")
def vector_check(
    pdf_url: str = Query(..., description="URL to the PDF file"),
    original_page_number: int = Query(None, description="Original page number from source document")
):
    """
    Controleert per pagina of er vectorinformatie aanwezig is.
    
    Vector detectie op basis van:
    - Tekst characters (vector tekst)
    - Lijnen en curves (geometrische elementen)
    - Rectangles (vector shapes)
    
    Output = lijst met per pagina:
    - page_number (uses original_page_number if provided)
    - is_vector (True/False)
    - vector_elements (details)
    """
    
    if not is_valid_url(pdf_url):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    tmp_path = None
    
    try:
        # Create session with retries
        session = create_session_with_retries()
        
        # Download PDF with better error handling
        print(f"Attempting to download PDF from: {pdf_url}")
        print(f"Original page number: {original_page_number}")
        
        response = session.get(
            pdf_url, 
            stream=True, 
            timeout=(10, 60),  # Connect timeout: 10s, Read timeout: 60s
            headers={
                'User-Agent': 'VectorCheck-API/1.0.0',
                'Accept': 'application/pdf,*/*'
            }
        )
        
        # Check response status
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="PDF URL access forbidden - URL may be expired")
        elif response.status_code == 404:
            raise HTTPException(status_code=404, detail="PDF not found at the provided URL")
        
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type and 'application/octet-stream' not in content_type:
            print(f"Warning: Content-Type is {content_type}, expected application/pdf")
        
        # Check file size
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="PDF file too large")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            total_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # filter out keep-alive chunks
                    tmp_file.write(chunk)
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise HTTPException(status_code=413, detail="PDF file too large")
            tmp_path = tmp_file.name
        
        print(f"PDF downloaded successfully. Size: {total_size} bytes")
        
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
                    
                    # Use original_page_number if provided, otherwise use PDF page number
                    page_number = original_page_number if original_page_number is not None else page_num + 1
                    
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_number,
                        "is_vector": is_vector,
                        "vector_elements": vector_elements,
                        "original_page_number": original_page_number  # For debugging
                    })
                    
                except Exception as e:
                    print(f"Error processing page {page_num + 1}: {str(e)}")
                    page_number = original_page_number if original_page_number is not None else page_num + 1
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_number,
                        "is_vector": False,
                        "vector_elements": {"error": str(e)},
                        "original_page_number": original_page_number  # For debugging
                    })
        
        return {
            "success": True,
            "page_count": len(result),
            "pages": result
        }
        
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=408, detail="Request timeout - PDF download took too long")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail="Connection error - Could not connect to PDF URL")
    except requests.RequestException as e:
        error_msg = f"Failed to download PDF: {str(e)}"
        print(error_msg)
        
        # More specific error messages
        if "403" in str(e):
            raise HTTPException(status_code=403, detail="PDF URL access forbidden - URL may be expired or invalid")
        elif "404" in str(e):
            raise HTTPException(status_code=404, detail="PDF not found at the provided URL")
        else:
            raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    
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
