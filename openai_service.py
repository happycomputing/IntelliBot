import os
import json
from openai import OpenAI

# Reference: blueprint:python_openai
# the newest OpenAI model is "gpt-5" which was released August 7, 2025.
# do not change this unless explicitly requested by the user

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

def detect_intent(message):
    """
    Detect the intent of the user's message.
    Returns: greeting, factual_question, chitchat, or out_of_scope
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an intent classifier. Analyze the user's message and classify it into one of these categories:\n"
                    "- greeting: Simple greetings like 'hi', 'hello', 'hey', 'good morning'\n"
                    "- factual_question: Questions seeking specific information, facts, or knowledge\n"
                    "- chitchat: Casual conversation, small talk, personal questions about the bot\n"
                    "- out_of_scope: Requests for actions the bot cannot perform (write code, perform tasks, etc.)\n\n"
                    "Respond with JSON in this format: {\"intent\": \"category\", \"confidence\": 0.95}"
                },
                {"role": "user", "content": message}
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=100
        )
        
        result = json.loads(response.choices[0].message.content)
        return result.get("intent", "factual_question"), result.get("confidence", 0.5)
    except Exception as e:
        print(f"Intent detection error: {e}")
        return "factual_question", 0.5


def generate_greeting(stats=None):
    """
    Generate a friendly greeting with information about the bot's capabilities.
    """
    try:
        stats_info = ""
        if stats:
            stats_info = f"\n\nCurrent knowledge base: {stats.get('raw_docs', 0)} documents indexed from {stats.get('url', 'configured website')}."
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a friendly AI assistant for a knowledge-base chatbot. "
                    "Generate a warm, professional greeting that:\n"
                    "1. Welcomes the user\n"
                    "2. Explains that you answer questions ONLY based on the knowledge available to you\n"
                    "3. Mentions you don't make up information\n"
                    "4. Invites them to ask questions\n\n"
                    f"Keep it brief (2-3 sentences). Never use technical terms like 'crawling' or 'indexing'.{stats_info}"
                },
                {"role": "user", "content": "Generate a greeting"}
            ],
            max_completion_tokens=150
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"Greeting generation error: {e}")
        return "Hello! I'm here to answer questions based only on the knowledge available to me. I don't make up information. What would you like to know?"


def get_company_name_from_url(url):
    """Extract a friendly company name from the configured URL."""
    if not url:
        return "our company"
    
    # Extract domain and make it friendly
    domain = url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
    
    # Try to make it readable (e.g., "officems.co.za" -> "OfficeMS")
    name_part = domain.split('.')[0]
    if name_part:
        # Capitalize first letter or use as-is if it looks like a brand name
        return name_part.title() if name_part.islower() else name_part
    
    return "our company"


def get_contact_details(retrieval_engine):
    """
    Retrieve contact information from the indexed documents.
    Returns formatted contact details or empty string if not found.
    """
    try:
        # Use simple query that matches well with FAISS
        contact_query = "contact us"
        result = retrieval_engine.get_answer(contact_query)
        
        if result.get('answer') and 'couldn\'t find' not in result.get('answer', ''):
            # Extract just the contact info, clean it up
            answer = result.get('answer', '')
            
            # Format nicely - just the essential contact info
            contact_text = f"\n\nYou can reach us:\n{answer}"
            return contact_text
        
        return ""
    except Exception as e:
        print(f"Error retrieving contact details: {e}")
        return ""


def generate_fallback_response(company_name, contact_details=""):
    """
    Generate a static, friendly out-of-scope response.
    No GPT - strictly static to prevent hallucination.
    """
    response = f"Sorry, I cannot help you with that. Anything else related to {company_name}?"
    
    if contact_details:
        response += contact_details
    
    return response
