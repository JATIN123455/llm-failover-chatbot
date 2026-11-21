# 1. Live URLs

- Frontend: add the deployed public frontend URL here
- Backend: add the deployed public backend URL here

# 2. Run locally: with Docker and without

## Windows setup

1. Install Docker Desktop: https://www.docker.com/products/docker-desktop/
2. Open Docker Desktop and wait until Docker Engine is running.
3. Install VS Code (optional): https://code.visualstudio.com/
4. Install Git for Windows: https://git-scm.com/download/win
5. Open PowerShell and run:
   ```powershell
   cd C:\Users\91999\llm-failover-chatbot
   ```

## Docker

```powershell
docker compose up --build -d
```

Open http://localhost:3000. The API is at http://localhost:8000.

```powershell
docker compose down
```

To use real providers, copy `.env.example` to `.env` and replace the placeholder keys.

Windows 11 does not include `make`. To enable `make up` and `make down`:

```powershell
winget install GnuWin32.Make
```

Restart PowerShell, then run `make up` or `make down`.

## Run without Docker

Backend:

```powershell
cd C:\Users\91999\llm-failover-chatbot\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="sqlite:///C:/temp/llm-chatbot.db"
$env:LLM_API_KEYS="demo_key_1,demo_key_2"
$env:ENABLE_FAILURE_SIMULATION="true"
$env:ALLOWED_ORIGINS="http://localhost:5173"
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Frontend in a second PowerShell window:

```powershell
cd C:\Users\91999\llm-failover-chatbot\frontend
npm install
$env:VITE_API_URL="http://localhost:8000"
npm run dev -- --port 5173
```

# 3. Design decisions: key fallback, caching, SQLite session storage

- LiteLLM handles provider calls and has an enabled local cache. The application also uses an exact in-memory conversation cache to expose deterministic cache hits.
- Keys are named `key_1`, `key_2`, and so on. Requests retry after simulated or real 429, quota, 500, 502, or 503 failures.
- Cache identity is the model plus the complete system/history/new-message list.
- SQLite stores sessions and ordered messages. The named Docker volume mounted at `/data` preserves `app.db` across restarts.
- `/chat` writes history only after provider success or a cache hit.

# 4. Verification: how to see failover, cache hits and per-message token usage

```powershell
Invoke-RestMethod http://localhost:8000/health

$body = @{session_id="s-demo-1"; message="What is the capital of France?"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/chat -ContentType "application/json" -Body $body

$body = @{session_id="s-demo-2"; message="What is the capital of France?"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/chat -ContentType "application/json" -Body $body

$body = @{session_id="s-failover"; message="Hello"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/chat -ContentType "application/json" -Body $body -Headers @{"X-Simulate-Failure"="key_1=429"}

$body = @{session_id="s-failed"; message="Hello"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/chat -ContentType "application/json" -Body $body -Headers @{"X-Simulate-Failure"="*=429"}

Invoke-RestMethod http://localhost:8000/sessions/s-demo-1/messages
```

# 5. Setup Used: your editor and AI tools

- Docker Desktop
- VS Code or PowerShell
- Git
- React and Vite
- FastAPI, Uvicorn, LiteLLM
- SQLite
- Copilot SDK

# 6. Bonus features (if any): what you added beyond the requirements and why

- Optional per-session system prompt
- Responsive chat interface
- Docker health check
- Demo provider mode for local verification without real API keys