import os
import re
from pathlib import Path
from typing import Iterable, List, Dict, Any, Optional
import yaml


def _intent_to_dict(intent: Any) -> Dict[str, Any]:
  if isinstance(intent, dict):
    return intent
  return {
    "name": getattr(intent, "name", ""),
    "description": getattr(intent, "description", "") or "",
    "examples": list(getattr(intent, "examples", []) or []),
    "responses": list(getattr(intent, "responses", []) or []),
  }


def _ensure_dir(path: Path) -> None:
  path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, lines: List[str]) -> None:
  path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _format_examples(examples: Iterable[str]) -> List[str]:
  unique = []
  seen = set()
  for example in examples:
    text = (example or "").strip()
    if not text:
      continue
    if text.lower() in seen:
      continue
    seen.add(text.lower())
    unique.append(text)
  return unique


def build_rasa_assets(
  project_path: str,
  intents: Iterable[Any],
  profile: Optional[Dict[str, Any]] = None,
  *,
  similarity_threshold: float = 0.4,
  top_k: int = 4
) -> Dict[str, Any]:
  """
  Generate Rasa project training artifacts (NLU, stories, rules, domain, config).
  Returns a summary dict with counts.
  """
  profile = profile or {}
  intents_data = [_intent_to_dict(item) for item in intents]

  project_root = Path(project_path).resolve()
  data_dir = project_root / "data"
  _ensure_dir(data_dir)

  company_name = profile.get("company_name") or "Our company"
  escalation = profile.get("escalation_message") or (
    f"Sorry, I cannot help with that. Please contact {company_name} directly."
  )
  contact = profile.get("contact") or {}
  contact_lines = []
  if contact.get("email"):
    contact_lines.append(f"Email: {contact['email']}")
  if contact.get("phone"):
    contact_lines.append(f"Phone: {contact['phone']}")
  if contact.get("website"):
    contact_lines.append(f"Website: {contact['website']}")
  if contact.get("address"):
    contact_lines.append(f"Address: {contact['address']}")
  if contact_lines:
    escalation = f"{escalation}\n\nContact {company_name}:\n" + "\n".join(contact_lines)

  greet_response = (
    f"Hello! You're speaking with {company_name}. "
    "I'm here to help you with questions about our services."
  )
  goodbye_response = (
    f"Thank you for contacting {company_name}. If you need anything else, just let me know."
  )

  base_intents = [
    {
      "name": "greet",
      "examples": ["hello", "hi", "good morning", "hey there", "good afternoon"],
      "responses": [greet_response],
      "action": "utter_greet",
    },
    {
      "name": "goodbye",
      "examples": ["thanks, bye", "goodbye", "talk soon", "cheers"],
      "responses": [goodbye_response],
      "action": "utter_goodbye",
    },
    {
      "name": "out_of_scope",
      "examples": [
        "Can you help me with my computer?",
        "What's the weather today?",
        "Write me some code.",
        "Book a flight for me.",
      ],
      "responses": [escalation],
      "action": "utter_out_of_scope",
    },
  ]

  knowledge_intents = []
  for item in intents_data:
    name = (item.get("name") or "").strip()
    if not name:
      continue
    examples = _format_examples(item.get("examples") or [])
    responses = [resp.strip() for resp in (item.get("responses") or []) if str(resp).strip()]
    if not examples or not responses:
      continue
    knowledge_intents.append(
      {
        "name": name,
        "examples": examples,
        "responses": responses,
        "description": item.get("description") or "",
      }
    )

  # Build NLU data
  nlu_lines = ['version: "3.1"', "nlu:"]

  for block in base_intents + knowledge_intents:
    nlu_lines.append(f"  - intent: {block['name']}")
    nlu_lines.append("    examples: |")
    for example in block["examples"]:
      nlu_lines.append(f"      - {example}")

  _write_text(data_dir / "nlu.yml", nlu_lines)

  # Build stories (simple happy path)
  stories_lines = ['version: "3.1"', "stories:"]
  stories_lines.extend(
    [
      "  - story: happy_path_services",
      "    steps:",
      "      - intent: greet",
      "      - action: utter_greet",
      "      - intent: {first_intent}".format(
        first_intent=knowledge_intents[0]["name"] if knowledge_intents else "out_of_scope"
      ),
      "      - action: {first_action}".format(
        first_action="utter_" + (
          knowledge_intents[0]["name"] if knowledge_intents else "out_of_scope"
        )
      ),
      "      - intent: goodbye",
      "      - action: utter_goodbye",
    ]
  )
  _write_text(data_dir / "stories.yml", stories_lines)

  # Build rules
  rules_lines = ['version: "3.1"', "rules:"]
  for entry in base_intents:
    rules_lines.append(f"  - rule: respond to {entry['name']}")
    rules_lines.append("    steps:")
    rules_lines.append(f"      - intent: {entry['name']}")
    rules_lines.append(f"      - action: {entry['action']}")

  rules_lines.append("  - rule: handle fallback")
  rules_lines.append("    steps:")
  rules_lines.append("      - intent: nlu_fallback")
  rules_lines.append("      - action: utter_out_of_scope")

  for entry in knowledge_intents:
    rules_lines.append(f"  - rule: respond to {entry['name']}")
    rules_lines.append("    steps:")
    rules_lines.append(f"      - intent: {entry['name']}")
    rules_lines.append(f"      - action: utter_{entry['name']}")

  _write_text(data_dir / "rules.yml", rules_lines)

  # Build domain
  intents_list = [block["name"] for block in base_intents] + [k["name"] for k in knowledge_intents]
  intents_list.extend(["nlu_fallback"])

  responses = {
    "utter_greet": [{"text": resp} for resp in base_intents[0]["responses"]],
    "utter_goodbye": [{"text": resp} for resp in base_intents[1]["responses"]],
    "utter_out_of_scope": [{"text": escalation}],
  }
  for entry in knowledge_intents:
    utter_name = f"utter_{entry['name']}"
    responses[utter_name] = [{"text": text} for text in entry["responses"]]

  domain_payload = {
    "version": "3.1",
    "intents": intents_list,
    "responses": responses,
    "session_config": {
      "session_expiration_time": 60,
      "carry_over_slots_to_new_session": True,
    },
  }

  domain_path = project_root / "domain.yml"
  domain_path.write_text(yaml.safe_dump(domain_payload, sort_keys=False, allow_unicode=True), encoding="utf-8")

  # Build config
  assistant_id = _slugify(company_name, fallback=project_root.name) + "_support"
  config_payload = {
    "recipe": "default.v1",
    "assistant_id": assistant_id,
    "language": "en",
    "pipeline": [
      {"name": "WhitespaceTokenizer"},
      {"name": "RegexFeaturizer"},
      {"name": "LexicalSyntacticFeaturizer"},
      {"name": "CountVectorsFeaturizer"},
      {"name": "CountVectorsFeaturizer", "analyzer": "char_wb", "min_ngram": 1, "max_ngram": 4},
      {"name": "DIETClassifier", "epochs": 150, "constrain_similarities": True},
      {"name": "EntitySynonymMapper"},
      {"name": "ResponseSelector", "epochs": 100, "constrain_similarities": True},
      {"name": "FallbackClassifier", "threshold": 0.4, "ambiguity_threshold": 0.1},
    ],
    "policies": [
      {"name": "MemoizationPolicy"},
      {"name": "RulePolicy", "core_fallback_threshold": 0.3, "core_fallback_action_name": "utter_out_of_scope"},
      {"name": "TEDPolicy", "max_history": 5, "epochs": 100, "constrain_similarities": True},
    ],
  }

  config_path = project_root / "config.yml"
  config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False, allow_unicode=True), encoding="utf-8")

  return {
    "knowledge_intents": len(knowledge_intents),
    "base_intents": len(base_intents),
    "project_path": str(project_root),
    "assistant_id": assistant_id,
    "similarity_threshold": similarity_threshold,
    "top_k": top_k,
  }
def _slugify(value: str, fallback: str = "assistant") -> str:
  if not value:
    return fallback
  slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
  return slug or fallback
