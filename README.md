# Social Media Finder (Curator AI)

Python pipeline (`testing.py`) + FastAPI (`api_server.py`) + Next.js UI (`curator-ai/`).

## Quick start

**Backend** (from this folder):

```bash
pip install -r requirements.txt
copy .env.example .env   # then add keys
uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload
```

**Frontend** (`curator-ai/`):

```bash
cd curator-ai
npm install
copy .env.example .env.local   # set NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787
npm run dev
```

Place input Excel beside `testing.py` or use the UI upload when the API is running.
