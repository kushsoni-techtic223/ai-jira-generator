# Daily Time Logger

Upload a `.pdf` or `.docx` requirement document → backend extracts text → Ollama generates **STRICT JSON** → frontend renders a Jira-style board.

## Backend (FastAPI)

### Install

```bash
cd ai-jira-generator/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
cd ai-jira-generator/backend
uvicorn main:app --reload
```

Backend runs at `http://localhost:8000`.

## Ollama

1. Install Ollama
2. Run the model:

```bash
ollama run llama3
```

## Frontend (Next.js)

### Install

```bash
cd ai-jira-generator/frontend
npm install
```

### Run

```bash
cd ai-jira-generator/frontend
npm run dev
```

Frontend runs at `http://localhost:3000` and calls the backend at `http://localhost:8000/generate-jira`.

## Live Jira board (OAuth + timers)

Any Jira Cloud user can connect with **their own** Atlassian account. You do **not** add them as app Collaborators.

### 1. Atlassian Developer Console

1. Create an OAuth 2.0 (3LO) app at https://developer.atlassian.com/console/myapps/
2. **Authorization** → Callback URL: `http://localhost:8000/callback`
3. **Permissions** → Jira platform REST API: `read:jira-work`, `write:jira-work`, plus `offline_access`  
   - Also **User identity API** → `read:me` (so the app can call `/me` for your profile)
4. **Distribution** → enable **Sharing**  
   - This is what lets any teammate click Connect — **not** Collaborators  
   - Collaborators are only for people who edit the app in the developer console

### 2. Backend `.env`

```bash
cp backend/.env.example backend/.env
```

Set at least:

```
JIRA_CLIENT_ID=...
JIRA_CLIENT_SECRET=...
JIRA_REDIRECT_URI=http://localhost:8000/callback
FRONTEND_URL=http://localhost:3000
JIRA_OAUTH_SCOPES=read:jira-work write:jira-work offline_access read:me
```

If something is missing, the Live board shows a checklist (or open `GET http://localhost:8000/auth/jira/setup`).

### 3. Flow
1. Open **Today's work / Live board**
2. Click **Connect with Jira** → approve → redirect back
3. Pick a **project** → **Load tickets**
4. On a card: **Start work** → when done **Stop & log**
5. Time is saved locally (`backend/data/worklogs.json`) and pushed to the Jira issue worklog

Each browser keeps its own session (`X-Jira-Session`), so multiple people can use the same app instance without sharing one login.

### OAuth / worklog API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth/jira/setup` | Config checklist (what’s missing) |
| `GET` | `/auth/jira/login` | Start OAuth |
| `GET` | `/callback` | OAuth redirect |
| `GET` | `/auth/jira/status` | Connection status (+ setup) |
| `POST` | `/auth/jira/logout` | Clear this browser’s session |
| `GET` | `/jira/projects` | List projects |
| `GET` | `/jira/project/{key}/issues` | Fetch project tickets |
| `POST` | `/jira/worklog` | Stop timer → local + Jira worklog |
| `GET` | `/jira/worklogs` | Local time records |

