import os
import json
import glob
import datetime
from typing import Optional, Callable, Dict, Any
import yaml
from openai import OpenAI


ProgressCallback = Optional[Callable[[str, str], None]]


def _notify(callback: ProgressCallback, msg_type: str, message: str) -> None:
  if callback:
    callback(msg_type, message)


def _collect_samples(raw_dir: str, limit: int = 8) -> str:
  doc_paths = sorted(
    glob.glob(os.path.join(raw_dir, "*.json")),
    key=lambda path: os.path.getsize(path),
    reverse=True
  )[:limit]
  snippets = []
  for path in doc_paths:
    try:
      with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
      text = (data.get("text") or "")[:1200]
      if not text.strip():
        continue
      source = data.get("url") or data.get("label") or os.path.basename(path)
      snippets.append(f"Source: {source}\n{text.strip()}")
    except Exception:
      continue
  return "\n\n---\n\n".join(snippets)


def build_company_profile(
  raw_dir: str,
  output_path: str,
  brand_voice: str = "professional",
  model: str = "gpt-4o-mini",
  progress_callback: ProgressCallback = None
) -> Dict[str, Any]:
  """
  Generate or refresh the company profile YAML from crawled knowledge.
  """
  api_key = os.environ.get("OPENAI_API_KEY")
  if not api_key:
    raise RuntimeError("OPENAI_API_KEY not configured")

  sample_text = _collect_samples(raw_dir)
  if not sample_text:
    raise RuntimeError("No knowledge documents available to derive a profile")

  client = OpenAI(api_key=api_key)
  _notify(progress_callback, "info", "Deriving company profile from knowledge sources...")

  system_prompt = (
    "You are an operations specialist preparing briefing notes for a customer support assistant. "
    "Distill the provided company knowledge into structured facts. "
    "Keep tone recommendations aligned with a professional brand voice."
  )

  user_prompt = (
    "Create a JSON object with the following top-level keys:\n"
    "company_name (string),\n"
    "brand_voice (string),\n"
    "summary (string, 3-4 sentences),\n"
    "values (list of short strings),\n"
    "contact (object with phone, email, website, address),\n"
    "escalation_message (string to be used when the bot cannot answer),\n"
    "knowledge_cutoff (ISO timestamp of now),\n"
    "data_sources (list of source descriptors).\n\n"
    "All information must be grounded in the supplied snippets; leave fields empty if not available. "
    "Use the brand voice '{brand_voice}' when crafting summary and escalation message.\n\n"
    "Snippets:\n{snippets}"
  ).format(brand_voice=brand_voice, snippets=sample_text)

  response = client.chat.completions.create(
    model=model,
    response_format={"type": "json_object"},
    messages=[
      {"role": "system", "content": system_prompt},
      {"role": "user", "content": user_prompt}
    ],
    max_tokens=1200
  )

  content = response.choices[0].message.content
  if not content:
    raise RuntimeError("OpenAI returned an empty profile response")

  data = json.loads(content)
  now_iso = datetime.datetime.utcnow().isoformat() + "Z"
  data.setdefault("brand_voice", brand_voice)
  data.setdefault("knowledge_cutoff", now_iso)

  os.makedirs(os.path.dirname(output_path), exist_ok=True)
  with open(output_path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, allow_unicode=False, sort_keys=False, indent=2)

  _notify(progress_callback, "success", f"Profile saved to {output_path}")
  return data
