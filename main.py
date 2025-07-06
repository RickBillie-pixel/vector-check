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

def analyze_vector_content(page):
    """
    Verbeterde analyse om echte vector illustraties te detecteren
    en normale tekst-PDF's uit te filteren
    """
    try:
        # Extract verschillende elementen
        text = page.extract_text()
        text_content = text.strip() if text else ""
        
        lines = page.lines if hasattr(page, 'lines') else []
        curves = page.curves if hasattr(page, 'curves') else []
        rects = page.rects if hasattr(page, 'rects') else []
        chars = page.chars if hasattr(page, 'chars') else []
        
        # Basis counts
        line_count = len(lines)
        curve_count = len(curves)
        rect_count = len(rects)
        char_count = len(chars)
        text_length = len(text_content)
        
        # 1. Filter uit: Pagina's met alleen tekst (geen graphics)
        has_graphics = line_count > 0 or curve_count > 0 or rect_count > 0
        
        if not has_graphics:
            return {
                "is_vector": False,
                "reason": "No vector graphics found - text only",
                "vector_elements": {
                    "text_chars": char_count,
                    "lines": line_count,
                    "curves": curve_count,
                    "rectangles": rect_count,
                    "text_length": text_length
                }
            }
        
        # 2. Check for minimal vector content thresholds
        # Veel PDF's hebben een paar lijntjes voor layout, dat is niet echt vector content
        
        # Minimale drempels voor echte vector content
        MIN_VECTOR_ELEMENTS = 5  # Minimaal 5 vector elementen
        MIN_COMPLEX_SHAPES = 2   # Minimaal 2 complexe shapes (curves/rects)
        
        total_vector_elements = line_count + curve_count + rect_count
        complex_shapes = curve_count + rect_count
        
        # 3. Ratio analyse: Is er veel meer vector content dan tekst?
        # Echte vector illustraties hebben meestal een hoge graphics-to-text ratio
        
        if text_length > 0:
            graphics_to_text_ratio = total_vector_elements / (text_length / 100)  # per 100 characters
        else:
            graphics_to_text_ratio = total_vector_elements  # Als er geen tekst is
        
        # 4. Detectie van verschillende types vector content
        
        # Type 1: Illustraties/diagrammen (veel vector elementen, weinig tekst)
        is_illustration = (
            total_vector_elements >= MIN_VECTOR_ELEMENTS and
            (graphics_to_text_ratio > 0.5 or text_length < 200)
        )
        
        # Type 2: Technische tekeningen (veel lijnen en shapes)
        is_technical_drawing = (
            line_count >= 10 and
            complex_shapes >= MIN_COMPLEX_SHAPES
        )
        
        # Type 3: Complexe graphics (veel curves)
        is_complex_graphics = (
            curve_count >= 5 or
            (curve_count >= 2 and line_count >= 5)
        )
        
        # Type 4: Flowcharts/diagrammen (veel rectangles + lijnen)
        is_diagram = (
            rect_count >= 3 and
            line_count >= 3 and
            total_vector_elements >= 8
        )
        
        # 5. Exclusie van layout elements
        # Veel PDF's hebben basic layout rectangles/lines die geen echte content zijn
        
        # Check of dit waarschijnlijk layout is (weinig elementen, veel tekst)
        likely_layout_only = (
            total_vector_elements < 5 and
            text_length > 500 and
            graphics_to_text_ratio < 0.1
        )
        
        if likely_layout_only:
            return {
                "is_vector": False,
                "reason": "Likely layout elements only, not vector illustrations",
                "vector_elements": {
                    "text_chars": char_count,
                    "lines": line_count,
                    "curves": curve_count,
                    "rectangles": rect_count,
                    "text_length": text_length,
                    "graphics_to_text_ratio": round(graphics_to_text_ratio, 2)
                }
            }
        
        # 6. Finale beslissing
        is_vector = (
            is_illustration or
            is_technical_drawing or
            is_complex_graphics or
            is_diagram
        )
        
        # Bepaal het type vector content
        vector_type = []
        if is_illustration:
            vector_type.append("illustration")
        if is_technical_drawing:
            vector_type.append("technical_drawing")
        if is_complex_graphics:
            vector_type.append("complex_graphics")
        if is_diagram:
            vector_type.append("diagram")
        
        return {
            "is_vector": is_vector,
            "vector_type": vector_type if is_vector else None,
            "reason": f"Vector content detected: {', '.join(vector_type)}" if is_vector else "No significant vector content",
            "vector_elements": {
                "text_chars": char_count,
                "lines": line_count,
                "curves": curve_count,
                "rectangles": rect_count,
                "text_length": text_length,
                "total_vector_elements": total_vector_elements,
                "graphics_to_text_ratio": round(graphics_to_text_ratio, 2)
            }
        }
        
    except Exception as e:
        return {
            "is_vector": False,
            "reason": f"Error analyzing content: {str(e)}",
            "vector_elements": {"error": str(e)}
        }

@app.get("/vector-check")
def vector_check(
    pdf_url: str = Query(..., description="URL to the PDF file"),
    original_page_number: int = Query(None, description="Original page number from source document")
):
    """
    Controleert per pagina of er vectorinformatie aanwezig is.
    
    Verbeterde vector detectie die filtert op:
    - Echte vector illustraties (hoge graphics-to-text ratio)
    - Technische tekeningen (veel lijnen en shapes)
    - Complexe graphics (curves en shapes)
    - Diagrammen en flowcharts
    
    Filtert UIT:
    - Alleen-tekst pagina's
    - Layout-elementen (basic rectangles/lines)
    - Pagina's met minimale vector content
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
                    # Gebruik de verbeterde analyse functie
                    analysis = analyze_vector_content(page)
                    
                    # Use original_page_number if provided, otherwise use PDF page number
                    page_number = original_page_number if original_page_number is not None else page_num + 1
                    
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_number,
                        "is_vector": analysis["is_vector"],
                        "vector_type": analysis.get("vector_type"),
                        "reason": analysis["reason"],
                        "vector_elements": analysis["vector_elements"],
                        "original_page_number": original_page_number  # For debugging
                    })
                    
                except Exception as e:
                    print(f"Error processing page {page_num + 1}: {str(e)}")
                    page_number = original_page_number if original_page_number is not None else page_num + 1
                    result.append({
                        "page_url": pdf_url,
                        "page_number": page_number,
                        "is_vector": False,
                        "reason": f"Error processing page: {str(e)}",
                        "vector_elements": {"error": str(e)},
                        "original_page_number": original_page_number  # For debugging
                    })
        
        # Summary statistics
        vector_pages = [p for p in result if p["is_vector"]]
        
        return {
            "success": True,
            "page_count": len(result),
            "vector_pages_count": len(vector_pages),
            "vector_pages": [p["page_number"] for p in vector_pages],
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
