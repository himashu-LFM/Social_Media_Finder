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

## Deploy (Render + Vercel)

**1) Deploy backend to Render**

- Create a new **Web Service** from this repo.
- Root directory: repo root (`/`)
- Build command:
  - `pip install -r requirements.txt`
- Start command:
  - `uvicorn api_server:app --host 0.0.0.0 --port $PORT`
- Add environment variables in Render:
  - `SERPER_API_KEY=...`
  - `OPENAI_API_KEY=...`
  - `CORS_ORIGINS=https://<your-vercel-app>.vercel.app`

After deploy, note your backend URL, e.g. `https://your-api.onrender.com`.

**2) Deploy frontend to Vercel**

- Import the same repo in Vercel.
- Set **Root Directory** to `curator-ai`.
- Add environment variable:
  - `NEXT_PUBLIC_PYTHON_API_URL=https://your-api.onrender.com`
- Deploy.

If you use a custom domain on Vercel, add that domain to Render `CORS_ORIGINS` too (comma-separated).
