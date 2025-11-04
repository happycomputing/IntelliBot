import os
import hashlib
import json
from PyPDF2 import PdfReader
from werkzeug.utils import secure_filename

UPLOAD_DIR = "kb/uploads"

def process_uploaded_documents(files, raw_dir="kb/raw"):
    """
    Extract text from uploaded documents (markdown or PDF) and save as raw documents.
    Returns list of processed document info.
    """
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    processed = []
    
    for file in files:
        if isinstance(file, dict):
            filename = file['filename']
            file_content = file['content']
        else:
            filename = file.filename
            file_content = file.read() if hasattr(file, 'read') else None
        
        if not filename:
            continue
        
        file_ext = os.path.splitext(filename)[1].lower()
        
        try:
            original_bytes = file_content if isinstance(file_content, (bytes, bytearray)) else None
            text_content = ""
            
            if file_ext == '.md':
                if isinstance(file_content, bytes):
                    text_content = file_content.decode('utf-8')
                    original_bytes = file_content
                else:
                    text_content = file_content
                    original_bytes = (file_content or "").encode('utf-8')
            elif file_ext == '.pdf':
                from io import BytesIO
                pdf_reader = PdfReader(BytesIO(file_content))
                text_content = ""
                for page in pdf_reader.pages:
                    text_content += page.extract_text() + "\n"
                original_bytes = file_content
            else:
                print(f"Unsupported file type: {filename}")
                continue
            
            if text_content.strip():
                if not isinstance(original_bytes, (bytes, bytearray)):
                    original_bytes = (text_content or "").encode('utf-8')
                
                doc_hash = hashlib.sha1(original_bytes).hexdigest()
                safe_name = secure_filename(filename) or f"document_{doc_hash}"
                stored_filename = f"{doc_hash}_{safe_name}"
                stored_path = os.path.join(UPLOAD_DIR, stored_filename)
                
                with open(stored_path, 'wb') as upload_file:
                    upload_file.write(original_bytes)
                
                doc_file = os.path.join(raw_dir, f"{doc_hash}.json")
                
                doc_data = {
                    "url": f"/uploads/{stored_filename}",
                    "label": filename,
                    "text": text_content.strip()
                }
                
                with open(doc_file, 'w', encoding='utf-8') as f:
                    json.dump(doc_data, f, ensure_ascii=False, indent=2)
                
                processed.append({
                    "filename": filename,
                    "stored_filename": stored_filename,
                    "hash": doc_hash,
                    "chars": len(text_content)
                })
                print(f"Processed document: {filename} ({len(text_content)} chars, stored as {stored_filename})")
        
        except Exception as e:
            print(f"Error processing {filename}: {e}")
    
    return processed
