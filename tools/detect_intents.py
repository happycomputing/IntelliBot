import os
import json
import glob
from openai import OpenAI

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def auto_detect_intents():
    """
    Analyze indexed documents to automatically detect potential intents
    based on content structure and topics.
    Returns a list of suggested intents with descriptions and example patterns.
    """
    raw_docs = glob.glob("kb/raw/*.json")
    
    if not raw_docs:
        return {"error": "No documents indexed yet"}
    
    doc_samples = []
    for doc_path in raw_docs[:10]:
        with open(doc_path, 'r') as f:
            doc = json.load(f)
            snippet = doc['content'][:500]
            doc_samples.append({
                'url': doc['url'],
                'snippet': snippet
            })
    
    sample_text = "\n\n".join([f"URL: {d['url']}\nContent: {d['snippet']}" for d in doc_samples])
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are an intent detection expert. Analyze the provided website content samples and identify common user intents.

For each intent, provide:
1. name: Short identifier (e.g., 'company_info', 'services', 'contact_us')
2. description: What the intent covers
3. patterns: 3-5 keyword patterns that indicate this intent
4. examples: 3-5 example questions users might ask

Focus on detecting intents like:
- Company/organization information
- Products/services
- Contact information  
- Team/about information
- Pricing/costs
- Technical documentation
- FAQ topics

Return JSON array of intent objects."""
                },
                {
                    "role": "user", 
                    "content": f"Analyze these website content samples and suggest intents:\n\n{sample_text}"
                }
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=2000
        )
        
        result = json.loads(response.choices[0].message.content)
        intents = result.get("intents", [])
        
        return {
            "status": "success",
            "intents": intents,
            "analyzed_docs": len(doc_samples)
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

def match_intent_pattern(question, intent_patterns):
    """
    Check if a question matches any intent patterns (simple keyword matching).
    Returns True if any pattern keyword is found in the question.
    """
    question_lower = question.lower()
    
    for pattern in intent_patterns:
        pattern_lower = pattern.lower()
        if pattern_lower in question_lower:
            return True
    
    return False
