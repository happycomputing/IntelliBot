import os
import io
import json
import hashlib
import datetime
from typing import Iterable, Dict, Any, List, Union
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader


UPLOAD_DIR_DEFAULT = "kb/uploads"


def _normalize_text(text: str) -> str:
  return " ".join(text.split())


def _save_payload(raw_dir: str, payload: Dict[str, Any]) -> None:
  os.makedirs(raw_dir, exist_ok=True)
  doc_hash = payload["content_hash"]
  path = os.path.join(raw_dir, f"{doc_hash}.json")
  with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)


def _store_upload(upload_dir: str, stored_filename: str, content: bytes) -> str:
  os.makedirs(upload_dir, exist_ok=True)
  stored_path = os.path.join(upload_dir, stored_filename)
  with open(stored_path, "wb") as upload_file:
    upload_file.write(content)
  return stored_path


def _extract_pdf_text(data: bytes) -> str:
  reader = PdfReader(io.BytesIO(data))
  parts: List[str] = []
  for page in reader.pages:
    extracted = page.extract_text() or ""
    parts.append(extracted)
  combined = "\n".join(parts)
  return combined


def process_uploaded_documents(
  files: Iterable[Union[Dict[str, Any], Any]],
  raw_dir: str = "kb/raw",
  upload_dir: str = UPLOAD_DIR_DEFAULT,
  url_prefix: str = "/uploads"
) -> List[Dict[str, Any]]:
  """
  Extract text from uploaded files (PDF or Markdown) and persist structured payloads.
  """
  processed: List[Dict[str, Any]] = []
  timestamp = datetime.datetime.utcnow().isoformat() + "Z"

  for file_obj in files:
    if isinstance(file_obj, dict):
      filename = file_obj.get("filename")
      file_content = file_obj.get("content")
    else:
      filename = getattr(file_obj, "filename", None)
      file_content = file_obj.read() if hasattr(file_obj, "read") else None

    if not filename or not file_content:
      continue

    extension = os.path.splitext(filename)[1].lower()
    content_type = "application/octet-stream"
    text_content = ""

    try:
      if extension == ".md":
        content_type = "text/markdown"
        if isinstance(file_content, bytes):
          text_content = file_content.decode("utf-8", errors="ignore")
        else:
          text_content = str(file_content)
          file_content = text_content.encode("utf-8")
      elif extension == ".pdf":
        content_type = "application/pdf"
        if isinstance(file_content, str):
          file_content = file_content.encode("utf-8")
        text_content = _extract_pdf_text(file_content)
      else:
        print(f"Unsupported file type: {filename}")
        continue
    except Exception as exc:
      print(f"Error processing {filename}: {exc}")
      continue

    clean_text = text_content.strip()
    if not clean_text:
      print(f"Skipped {filename}: no extractable text")
      continue

    content_bytes = file_content if isinstance(file_content, (bytes, bytearray)) else clean_text.encode("utf-8")
    content_hash = hashlib.sha1(content_bytes).hexdigest()
    safe_name = secure_filename(filename) or f"document_{content_hash}"
    stored_filename = f"{content_hash}_{safe_name}"
    _store_upload(upload_dir, stored_filename, content_bytes)

    preview = " ".join(clean_text.split())
    meta_description = preview[:280]

    payload = {
      "url": f"{url_prefix}/{stored_filename}",
      "label": filename,
      "title": os.path.splitext(filename)[0],
      "meta_description": meta_description,
      "headings": {},
      "text": clean_text,
      "content_hash": content_hash,
      "source_type": "upload",
      "content_type": content_type,
      "extracted_at": timestamp,
      "status_code": 200
    }
    _save_payload(raw_dir, payload)

    processed.append({
      "filename": filename,
      "stored_filename": stored_filename,
      "hash": content_hash,
      "chars": len(clean_text)
    })
    print(f"Processed document: {filename} ({len(clean_text)} chars, stored as {stored_filename})")

  return processed
