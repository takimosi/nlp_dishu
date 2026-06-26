# Game System

This repository contains a local prototype for the Dishu world demo, including:

- main world entry: `index.html`
- cafe interface: `Boook/book.html`
- game services and templates: `game/`
- diary apartment service: `diary/`

## Local Run

Install the Python dependencies for the services you want to run, then start the local launcher:

```powershell
python start.py
```

The launcher starts multiple local services:

- main static site: `http://localhost:8080`
- diary service: `http://localhost:8000`
- multiplayer game service: `http://localhost:5001`

## Environment Variables

Copy `.env.example` to `.env` for local use and fill in private values there.

Do not commit `.env` or real API keys.

Required for DeepSeek-backed features:

```text
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
FLASK_SECRET_KEY=change-me
```

## GitHub Notes

Large local model files under `game/models/` are intentionally ignored. Runtime caches, room state, and local IDE files are also excluded from Git.
