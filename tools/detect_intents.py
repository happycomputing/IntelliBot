import os
import json
import glob
from typing import Dict, Any, Optional, Iterable, List
import yaml
from openai import OpenAI


def _load_profile(profile_path: Optional[str]) -> Dict[str, Any]:
  if not profile_path or not os.path.exists(profile_path):
    return {}
  with open(profile_path, "r", encoding="utf-8") as handle:
    return yaml.safe_load(handle) or {}


def _sample_documents(raw_dir: str, limit: int = 12) -> List[Dict[str, str]]:
  paths = sorted(
    glob.glob(os.path.join(raw_dir, "*.json")),
    key=lambda p: os.path.getsize(p),
    reverse=True
  )[:limit]
  samples = []
  for path in paths:
    try:
      with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
      text = (data.get("text") or "")[:1500]
      if not text.strip():
        continue
      samples.append({
        "source": data.get("url") or data.get("label") or os.path.basename(path),
        "snippet": text.strip()
      })
    except Exception:
      continue
  return samples


def auto_detect_intents(
  raw_dir: str = "kb/raw",
  profile_path: Optional[str] = None,
  brand_voice: str = "professional",
  model: str = "gpt-4o-mini"
) -> Dict[str, Any]:
  """
  Generate suggested intents and responses grounded in uploaded knowledge.
  """
  api_key = os.environ.get("OPENAI_API_KEY")
  if not api_key:
    raise RuntimeError("OPENAI_API_KEY not configured")

  samples = _sample_documents(raw_dir)
  if not samples:
    return {"status": "error", "error": "No documents indexed yet"}

  profile = _load_profile(profile_path)
  company_name = profile.get("company_name") or "the company"
  contact = profile.get("contact") or {}

  contact_block = "\n".join([
    f"Phone: {contact.get('phone')}" if contact.get("phone") else "",
    f"Email: {contact.get('email')}" if contact.get("email") else "",
    f"Website: {contact.get('website')}" if contact.get("website") else "",
    f"Address: {contact.get('address')}" if contact.get("address") else ""
  ]).strip()

  joined_samples = "\n\n---\n\n".join(
    f"Source: {sample['source']}\n{sample['snippet']}" for sample in samples
  )

  system_prompt = (
    "You are a Rasa conversation designer for customer support. "
    "Analyse the supplied knowledge snippets and produce a concise set of intents. "
    "Each intent must be fully grounded in the knowledge; disregard unrelated content."
  )

  user_prompt = (
    "Return a JSON object with an 'intents' array. Each element must include:\n"
    "  name: snake_case intent identifier\n"
    "  description: short summary of the customer need\n"
    "  examples: 3 example user messages\n"
    "  canonical_response: professional agent reply grounded in knowledge, include citations if possible\n"
    "  required_context: optional notes (e.g. policies, prerequisites)\n"
    "  source_urls: list of URLs supporting the answer\n\n"
    "Use brand voice '{brand_voice}'. When content is missing, omit the intent instead of guessing.\n"
    "For questions outside scope, do not fabricate knowledge—instead ensure the fall-back response "
    f"references these contact details:\n{contact_block or 'No contact details available.'}\n\n"
    "Knowledge snippets:\n"
    f"{joined_samples}"
  )

  client = OpenAI(api_key=api_key)
  response = client.chat.completions.create(
    model=model,
    response_format={"type": "json_object"},
    messages=[
      {"role": "system", "content": system_prompt},
      {"role": "user", "content": user_prompt}
    ],
    max_tokens=2600
  )

  content = response.choices[0].message.content
  if not content:
    return {"status": "error", "error": "Empty response from OpenAI"}

  payload = json.loads(content)
  intents = payload.get("intents", [])
  if not isinstance(intents, list):
    intents = []

  summary = profile.get("summary") or ""
  overview_exists = any(
    isinstance(item, dict)
    and isinstance(item.get("name"), str)
    and any(keyword in item["name"].lower() for keyword in ("overview", "company", "about"))
    for item in intents
  )
  if summary and not overview_exists:
    values = profile.get("values") or []
    response_parts = [summary.strip()]
    if values:
      response_parts.append("Core values: " + ", ".join(values))
    if contact_block:
      response_parts.append(contact_block)
    canonical = "\n\n".join(part for part in response_parts if part).strip()
    data_sources = profile.get("data_sources") or []
    if isinstance(data_sources, str):
      data_sources = [data_sources]
    if not data_sources:
      if contact.get("website"):
        data_sources = [contact["website"]]
      elif samples:
        data_sources = [samples[0]["source"]]
    overview_intent = {
      "name": "company_overview",
      "description": f"Provide a general overview of {company_name}.",
      "examples": [
        f"Tell me about {company_name}",
        f"What does {company_name} do?",
        "Give me an overview of your company.",
        "Who are you?",
        "What services does your company provide?"
      ],
      "canonical_response": canonical,
      "required_context": "",
      "source_urls": data_sources
    }
    intents.insert(0, overview_intent)

  return {
    "status": "success",
    "intents": intents,
    "analyzed_docs": len(samples),
    "profile_used": bool(profile)
  }


def match_intent_pattern(question: str, intent_patterns: Iterable[str]) -> bool:
  """
  Check if a question matches any intent patterns via regex or substring matching.
  """
  import re
  question_lower = question.lower()
  for pattern in intent_patterns:
    pattern_str = str(pattern).strip()
    if not pattern_str:
      continue
    if pattern_str.startswith("^") or pattern_str.endswith("$") or any(
      symbol in pattern_str for symbol in ["*", "?", "[", "]", "(", ")", "|", "\\"]
    ):
      try:
        if re.search(pattern_str, question, re.IGNORECASE):
          return True
      except re.error:
        pass
    if pattern_str.lower() in question_lower:
      return True
  return False
