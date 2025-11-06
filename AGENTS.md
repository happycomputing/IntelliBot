# IntelliBot Ops Notes *(local only — do not commit)*

Use two spaces for indentation.

- **Container**: `IntelliBot` (LXC), IPv4 `10.130.0.134`
- **Project path**: `/workspace/IntelliBot` (host repo is bind-mounted via `code` device with `shift=true`)
- **App port**: Gunicorn listens on `80` inside the container (access via container IP/port 80)
- **Python env**: `/workspace/IntelliBot/.venv`
- **Service env vars**: `/etc/intellibot.env` (update with real secrets, then restart service)
- **Core secrets**: `OPENAI_API_KEY`, `SESSION_SECRET` (SQLite handled locally via `intellibot.db`)

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

- Database file: `sqlite:////workspace/IntelliBot/intellibot.db`
- Inspect recent conversations (requires venv for Python):

  ```bash
  lxc exec IntelliBot -- bash -lc "cd /workspace/IntelliBot && source .venv/bin/activate && python - <<'PY'
import sqlite3

conn = sqlite3.connect('intellibot.db')
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
- SQLite lives at `/workspace/IntelliBot/intellibot.db`; back up before destructive operations.
- If the bind mount breaks permissions, reapply shift and restart:
  `lxc config device set IntelliBot code shift true && lxc restart IntelliBot`.
- Access the app via `http://10.130.0.134/` (or container name if DNS is configured); open firewall routes as needed.
- Git discipline: Do not commit to the repository unless asked.


## Current App Snapshot (Nov 2025)

- Rasa only: every chatbot runs from a dedicated Rasa project under `bots_store/<slug>`; the legacy project now lives in `bots_legacy/` for reference.
- Multi-bot aware: `intellibot.db` stores bots, conversations, and intents per bot; most API calls expect a `bot_id`.
- Knowledge base per bot: indexed data, uploads, and config live under `kb/<slug>/...`; the web UI posts to `/api/config`, `/api/index`, `/api/crawl` with the active bot attached.
- Runtime flow: Gunicorn + Eventlet hosts Flask/Socket.IO; chat turns call `run_rasa_turn`, which shells into `.venv-rasa` to query each bot's trained model.
- Frontend UX: users pick/create bots via the header selector; stats/config panels update when a bot is ready; chat history (with feedback controls) reloads on connect.
- Ops checklist after changes: re-run `.venv/bin/pip install -r requirements.txt` if dependencies change, then `systemctl restart intellibot.service`; hit `/health` for a quick verify.

## Container Permissions

- Persistent user inside container: `pieter` (UID/GID 1001). Systemd runs `intellibot.service` as this user so that everything under `/workspace/IntelliBot` stays writable from the host.
- Use the helper: `./scripts/container-dev.sh` …
  - With a command: `./scripts/container-dev.sh "pip install -r requirements.txt"`
  - Without args, you get an interactive shell as `pieter` inside the container.
- When root privileges are required (e.g. `apt`, `chown`, service tweaks), run: `lxc exec IntelliBot -- sudo ...` and afterwards ensure ownership stays on UID/GID 1001 (rerun `chown -R 1001:1001 /workspace/IntelliBot` if needed).
- Keep `lxc config device set IntelliBot code shift true` enabled so the host/container UID mapping stays aligned; after applying it, restart the container.

