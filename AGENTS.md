# IntelliBot Ops Notes *(local only — do not commit)*

- **Container**: `IntelliBot` (LXC), IPv4 `10.130.0.134`
- **Project path**: `/workspace/IntelliBot` (host repo is bind-mounted via `code` device with `shift=true`)
- **App port**: Gunicorn listens on `80` inside the container (access via container IP/port 80)
- **Python env**: `/workspace/IntelliBot/.venv`
- **Service env vars**: `/etc/intellibot.env` (update with real secrets, then restart service)

## Container Access

```bash
# Open a shell inside the container
lxc exec IntelliBot -- bash

# Run a single command
lxc exec IntelliBot -- bash -lc "cd /workspace/IntelliBot && <command>"
```

## App Service (systemd)

```bash
# Service lifecycle
lxc exec IntelliBot -- bash -lc "systemctl status intellibot.service"
lxc exec IntelliBot -- bash -lc "systemctl restart intellibot.service"
lxc exec IntelliBot -- bash -lc "systemctl stop intellibot.service"

# Follow logs
lxc exec IntelliBot -- bash -lc "journalctl -u intellibot -f"
```

Gunicorn command: `/workspace/IntelliBot/.venv/bin/gunicorn --worker-class eventlet -w 1 --worker-connections 1000 app:app --bind 0.0.0.0:80`

## Package/Env Management

```bash
lxc exec IntelliBot -- bash -lc "cd /workspace/IntelliBot && source .venv/bin/activate && <pip/python command>"
```

`requirements.txt` is already installed in the venv; re-run `pip install -r requirements.txt` after dependency updates.

## Manual Chat Tests

```bash
lxc exec IntelliBot -- bash -lc "cd /workspace/IntelliBot && source .venv/bin/activate && python - <<'PY'
import socketio, threading, pprint

sio = socketio.Client()
event = threading.Event()
payload = {}

@sio.event
def connect():
    print('connected')

@sio.on('chat_response')
def on_chat_response(data):
    payload.update(data)
    print('chat_response received')
    event.set()
    sio.disconnect()

sio.connect('http://127.0.0.1:80')
sio.emit('chat_message', {'message': 'Test message from ops notes'})

if event.wait(timeout=10):
    pprint.pprint(payload)
else:
    print('No response within timeout')
    sio.disconnect()
PY"
```

## Conversation Storage & Feedback

- Database: `sqlite:////workspace/IntelliBot/fallback.db`
- Inspect recent conversations (requires venv for Python):

  ```bash
  lxc exec IntelliBot -- bash -lc "cd /workspace/IntelliBot && source .venv/bin/activate && python - <<'PY'
import sqlite3

conn = sqlite3.connect('fallback.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT id, question, substr(answer, 1, 80) AS answer_snippet, timestamp, feedback FROM conversations ORDER BY id DESC LIMIT 5')
for row in cur.fetchall():
    print(dict(row))
conn.close()
PY"
  ```

- Add feedback to a conversation:

  ```bash
  # Replace <ID> with conversation_id and adjust message/feedback as needed
  lxc exec IntelliBot -- bash -lc "curl -s -X POST http://127.0.0.1:80/api/conversations/<ID>/feedback \\
    -H 'Content-Type: application/json' \\
    -d '{\"feedback\": \"Helpful for onboarding\"}'"
  ```

## Quick Health Checks

```bash
# From the container host (directly hitting container IP)
curl -I http://10.130.0.134/health

# Inside the container
lxc exec IntelliBot -- bash -lc "curl -I http://127.0.0.1:80/health"
```

## Notes

- Update `/etc/intellibot.env` whenever secrets or DB connection change, then `systemctl restart intellibot.service`.
- If the bind mount breaks permissions, reapply shift and restart:
  `lxc config device set IntelliBot code shift true && lxc restart IntelliBot`.
- Access the app via `http://10.130.0.134/` (or container name if DNS is configured); open firewall routes as needed.
- Application behaviour and deployment expectations live in `README.md` and `replit.md`; keep both documents current whenever we change features or ops steps.
- Git discipline: Do not commit to the repository unless asked.
