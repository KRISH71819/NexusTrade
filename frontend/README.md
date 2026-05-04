# NexusTrade AI Frontend

React + Vite dashboard for the FastAPI backend.

## Run

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`, so the dashboard only uses backend API data and does not need demo data or browser-side CORS changes.
