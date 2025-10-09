import random

class ActionHandler:
    """
    Rasa-style action handler for executing intent-based responses.
    Supports multiple action types: static, retrieval, hybrid.
    """
    
    @staticmethod
    def execute_action(intent, query, user_message, retrieval_engine):
        """
        Execute an action based on intent configuration.
        
        Args:
            intent: Intent object with action_type and responses
            query: User's original query
            user_message: User's message text
            retrieval_engine: RetrievalEngine instance for FAISS queries
        
        Returns:
            dict with answer, sources, confidence, intent info
        """
        action_type = intent.action_type or 'retrieval'
        responses = intent.responses or []
        
        if action_type == 'static':
            return ActionHandler._execute_static(intent, responses)
        
        elif action_type == 'retrieval':
            return ActionHandler._execute_retrieval(intent, query, retrieval_engine)
        
        elif action_type == 'hybrid':
            return ActionHandler._execute_hybrid(intent, query, responses, retrieval_engine)
        
        else:
            return ActionHandler._execute_retrieval(intent, query, retrieval_engine)
    
    @staticmethod
    def _execute_static(intent, responses):
        """
        Return a static predefined response (Rasa utter_* style).
        Randomly selects from multiple responses if available.
        """
        if not responses:
            return {
                'answer': f"I understand you're asking about {intent.name}, but I don't have a configured response yet.",
                'sources': [],
                'confidence': 0.8,
                'intent': intent.name,
                'action_type': 'static'
            }
        
        answer = random.choice(responses) if isinstance(responses, list) else responses
        
        return {
            'answer': answer,
            'sources': [],
            'confidence': 1.0,
            'intent': intent.name,
            'action_type': 'static'
        }
    
    @staticmethod
    def _execute_retrieval(intent, query, retrieval_engine):
        """
        Use FAISS retrieval to answer from indexed documents.
        Standard retrieval action for factual questions.
        """
        result = retrieval_engine.get_answer(query)
        result['intent'] = intent.name
        result['action_type'] = 'retrieval'
        return result
    
    @staticmethod
    def _execute_hybrid(intent, query, responses, retrieval_engine):
        """
        Hybrid: Use retrieval but format answer with custom template.
        Combines FAISS context with predefined response structure.
        """
        retrieval_result = retrieval_engine.get_answer(query)
        
        if not responses:
            return ActionHandler._execute_retrieval(intent, query, retrieval_engine)
        
        template = random.choice(responses) if isinstance(responses, list) else responses
        
        context = retrieval_result.get('answer', '')
        sources = retrieval_result.get('sources', [])
        
        answer = template.replace('{context}', context)
        
        answer = answer.replace('{sources_count}', str(len(sources)))
        
        return {
            'answer': answer,
            'sources': sources,
            'confidence': retrieval_result.get('confidence', 0.5),
            'intent': intent.name,
            'action_type': 'hybrid'
        }
    
    @staticmethod
    def get_default_responses_for_intent(intent_name, description):
        """
        Generate smart default responses based on intent name and description.
        """
        intent_lower = intent_name.lower()
        
        if 'contact' in intent_lower:
            return {
                'action_type': 'hybrid',
                'responses': [
                    "Here's how to contact us: {context}",
                    "You can reach us through: {context}"
                ]
            }
        
        elif 'service' in intent_lower or 'product' in intent_lower:
            return {
                'action_type': 'hybrid', 
                'responses': [
                    "We offer the following: {context}",
                    "Our services include: {context}"
                ]
            }
        
        elif 'price' in intent_lower or 'cost' in intent_lower:
            return {
                'action_type': 'hybrid',
                'responses': [
                    "Here's our pricing information: {context}",
                    "Regarding costs: {context}"
                ]
            }
        
        elif 'about' in intent_lower or 'who' in intent_lower:
            return {
                'action_type': 'hybrid',
                'responses': [
                    "About us: {context}",
                    "Here's information about our company: {context}"
                ]
            }
        
        elif 'hours' in intent_lower or 'when' in intent_lower:
            return {
                'action_type': 'hybrid',
                'responses': [
                    "Our hours: {context}",
                    "We're available: {context}"
                ]
            }
        
        else:
            return {
                'action_type': 'retrieval',
                'responses': []
            }
