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


def generate_fallback_response(message, context=""):
    """
    Generate a helpful response for out-of-scope or chitchat messages.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful AI assistant for a knowledge-base chatbot. "
                    "The user's message is outside the scope of factual questions. "
                    "Respond politely and:\n"
                    "1. If it's chitchat or casual conversation, engage briefly but remind them of your primary purpose\n"
                    "2. If it's a request you can't fulfill (write code, perform actions), politely decline and explain your limitations\n"
                    "3. Always redirect them back to asking questions about the knowledge available to you\n\n"
                    f"Never use technical terms like 'crawling', 'indexing', or 'database'. Context: {context if context else 'No knowledge available yet.'}"
                },
                {"role": "user", "content": message}
            ],
            max_completion_tokens=200
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"Fallback response error: {e}")
        return "I can only answer questions based on the indexed website content. Please ask me something about the knowledge base!"
