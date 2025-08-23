# CFP Aggregator Backend

A FastAPI backend to aggregate Calls for Proposals (CFPs) from multiple sources with a pluggable adapter architecture.

## Quick start

```bash
# From /workspace
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Project structure

- `app/main.py`: FastAPI app, routes
- `app/database.py`: Database engine, session
- `app/models.py`: SQLAlchemy models
- `app/schemas.py`: Pydantic models
- `app/adapters/`: Source adapters
- `app/ingestion.py`: Ingestion orchestration

## Endpoints

- `GET /health`
- `GET /cfps`: list/search CFPs
- `POST /ingest`: trigger ingestion (sync)

## Notes

- Default DB: SQLite at `/workspace/data/cfps.db`
- Add adapters in `app/adapters/` and register them in `app/ingestion.py`.