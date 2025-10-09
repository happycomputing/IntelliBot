import os
import hashlib
import json
from PyPDF2 import PdfReader

def process_uploaded_documents(files, raw_dir="kb/raw"):
    """
    Extract text from uploaded documents (markdown or PDF) and save as raw documents.
    Returns list of processed document info.
    """
    os.makedirs(raw_dir, exist_ok=True)
    processed = []
    
    for file in files:
        filename = file.filename
        file_ext = os.path.splitext(filename)[1].lower()
        
        try:
            text_content = ""
            
            if file_ext == '.md':
                text_content = file.read().decode('utf-8')
            elif file_ext == '.pdf':
                file.stream.seek(0)
                pdf_reader = PdfReader(file.stream)
                text_content = ""
                for page in pdf_reader.pages:
                    text_content += page.extract_text() + "\n"
            else:
                print(f"Unsupported file type: {filename}")
                continue
            
            if text_content.strip():
                doc_hash = hashlib.sha1(filename.encode()).hexdigest()
                doc_file = os.path.join(raw_dir, f"{doc_hash}.json")
                
                doc_data = {
                    "url": f"uploaded://{filename}",
                    "text": text_content.strip()
                }
                
                with open(doc_file, 'w', encoding='utf-8') as f:
                    json.dump(doc_data, f, ensure_ascii=False, indent=2)
                
                processed.append({
                    "filename": filename,
                    "hash": doc_hash,
                    "chars": len(text_content)
                })
                print(f"Processed document: {filename} ({len(text_content)} chars)")
        
        except Exception as e:
            print(f"Error processing {filename}: {e}")
    
    return processed
