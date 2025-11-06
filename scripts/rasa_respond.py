#!/usr/bin/env python3
"""CLI helper to send a single message to a trained Rasa model."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rasa.core.agent import Agent


async def interact(model_path: str, message: str, sender_id: str):
    agent = await Agent.load(model_path)
    try:
        nlu_data = await agent.parse_message_using_nlu_interpreter(message)
        responses = await agent.handle_text(message, sender_id=sender_id)
    finally:
        shutdown = getattr(agent, 'shutdown', None)
        if callable(shutdown):
            await shutdown()

    intent_data = nlu_data.get('intent') if isinstance(nlu_data, dict) else None
    intent_name = None
    confidence = None
    if isinstance(intent_data, dict):
        intent_name = intent_data.get('name')
        confidence = intent_data.get('confidence')

    return {
        'status': 'success',
        'intent': intent_name,
        'confidence': confidence,
        'responses': responses,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='Path to the trained Rasa model (.tar.gz)')
    parser.add_argument('--message', required=True, help='User message to send to the model')
    parser.add_argument('--sender', default='default', help='Conversation sender identifier')

    args = parser.parse_args(argv)

    model_path = Path(args.model)
    if not model_path.exists():
        print(json.dumps({'status': 'error', 'message': f'Model not found: {model_path}'}))
        return 2

    try:
        payload = asyncio.run(interact(str(model_path), args.message, args.sender))
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({'status': 'error', 'message': str(exc)}))
        return 1

    print(json.dumps(payload))
    return 0


if __name__ == '__main__':
    sys.exit(main())
