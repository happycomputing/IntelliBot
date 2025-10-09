#!/usr/bin/env python3
"""Comprehensive test script to diagnose bot response issues"""

import json
from actions import ActionHandler
from models import Intent, db
from retrieval_engine import RetrievalEngine
from openai_service import detect_intent

def test_services_query():
    """Test the complete flow for 'tell me about your services'"""
    
    query = "tell me about your services"
    print(f"\n{'='*60}")
    print(f"Testing Query: '{query}'")
    print(f"{'='*60}\n")
    
    # Step 1: Check if intent exists in database
    print("Step 1: Check custom intents in database")
    intents = Intent.query.all()
    print(f"Total custom intents: {len(intents)}")
    for intent in intents:
        print(f"  - {intent.name}: {intent.patterns} (action: {intent.action_type})")
    
    # Step 2: Pattern matching (custom intent)
    print("\nStep 2: Pattern matching")
    matched_intent = None
    for intent in intents:
        if intent.patterns:
            patterns = [p.strip().lower() for p in intent.patterns]
            if any(pattern in query.lower() for pattern in patterns):
                matched_intent = intent.name
                print(f"✅ Matched custom intent: {intent.name}")
                print(f"   Action type: {intent.action_type}")
                print(f"   Responses: {intent.responses}")
                break
    
    if not matched_intent:
        print("❌ No custom intent matched, would use OpenAI fallback")
        intent_result, confidence = detect_intent(query)
        print(f"   OpenAI detected: {intent_result} (confidence: {confidence})")
        matched_intent = intent_result
    
    # Step 3: Execute action
    print("\nStep 3: Execute action")
    with open('config.json') as f:
        config = json.load(f)
    
    retrieval = RetrievalEngine(
        similarity_threshold=config.get('similarity_threshold', 0.45),
        top_k=config.get('top_k', 3)
    )
    
    # Get the actual intent object from database
    intent_obj = Intent.query.filter_by(name=matched_intent).first()
    
    if intent_obj:
        result = ActionHandler.execute_action(intent_obj, query, query, retrieval)
    else:
        result = {'answer': 'Intent not found in database', 'sources': []}
    
    print(f"Action executed: {matched_intent}")
    print(f"Result type: {type(result)}")
    print(f"Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
    
    # Step 4: Show final response
    print("\nStep 4: Final Response")
    print(f"Answer: {result.get('answer', 'NO ANSWER')[:200]}...")
    print(f"Sources: {len(result.get('sources', []))} sources")
    print(f"Scores: {result.get('similarity_scores', [])}")
    
    # Step 5: Check retrieval directly
    print("\nStep 5: Direct FAISS retrieval test")
    retrieval = RetrievalEngine(similarity_threshold=0.45)
    direct_result = retrieval.get_answer("services offered company")
    print(f"Direct query 'services offered company':")
    print(f"  Answer: {direct_result['answer'][:150]}...")
    print(f"  Sources: {len(direct_result['sources'])}")
    print(f"  Scores: {direct_result.get('similarity_scores', [])}")
    
    return result

def test_out_of_scope():
    """Test out-of-scope handling"""
    
    query = "pest control"
    print(f"\n{'='*60}")
    print(f"Testing Out-of-Scope Query: '{query}'")
    print(f"{'='*60}\n")
    
    # Detect intent
    intent_result, confidence = detect_intent(query)
    print(f"Intent detected: {intent_result} (confidence: {confidence})")
    
    # This should not use ActionHandler if out_of_scope
    # Check app.py logic for out_of_scope handling
    with open('config.json') as f:
        config = json.load(f)
    
    retrieval = RetrievalEngine(
        similarity_threshold=config.get('similarity_threshold', 0.45),
        top_k=config.get('top_k', 3)
    )
    
    # Simulate app.py out_of_scope handling
    from openai_service import get_company_name_from_url, get_contact_details, generate_fallback_response
    company_name = get_company_name_from_url(config.get('url', ''))
    contact_details = get_contact_details(retrieval)
    result = {
        'answer': generate_fallback_response(company_name, contact_details),
        'sources': []
    }
    
    print(f"\nFinal Response:")
    print(f"Answer: {result.get('answer', 'NO ANSWER')}")
    
    # Check for hallucination
    if 'pest' in result['answer'].lower() and 'pest control' not in query.lower():
        print("\n❌ HALLUCINATION DETECTED!")
    else:
        print("\n✅ No hallucination - response is safe")

if __name__ == "__main__":
    from app import app
    
    with app.app_context():
        print("\n" + "="*60)
        print("COMPREHENSIVE BOT FLOW TEST")
        print("="*60)
        
        # Test 1: Services query
        test_services_query()
        
        # Test 2: Out of scope
        test_out_of_scope()
        
        print("\n" + "="*60)
        print("TEST COMPLETE")
        print("="*60 + "\n")
