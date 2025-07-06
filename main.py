import os
import tempfile
from fastapi import FastAPI, Query, HTTPException
from urllib.parse import urlparse
import fitz  # PyMuPDF
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
    Dit gebeurt op basis van:
    - get_drawings(): geometrische vectorlijnen (zoals muren, lijnen, vormen)
    - get_text(): vector-gebaseerde tekst

    Output = lijst met per pagina:
    - page_number
    - is_vector (True/False)
    """
    
    if not is_valid_url(pdf_url):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    tmp_path = None
    doc = None
    
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
        
        # Process PDF
        doc = fitz.open(tmp_path)
        result = []
        
        for page in doc:
            drawings = page.get_drawings()
            text = page.get_text().strip()
            
            is_vector = bool(drawings or text)
            result.append({
                "page_url": pdf_url,
                "page_number": page.number + 1,
                "is_vector": is_vector
            })
        
        return {
            "success": True,
            "page_count": len(doc),
            "pages": result
        }
        
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to download PDF: {str(e)}")
    except fitz.FileDataError as e:
        raise HTTPException(status_code=400, detail=f"Invalid PDF file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    
    finally:
        # Always cleanup
        if doc:
            doc.close()
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

@app.get("/health")
def health_check():
    return {"status": "healthy", "message": "VectorCheck API is running"}

@app.get("/")
def root():
    return {"message": "VectorCheck API - Send PDF URLs to /vector-check endpoint"}
