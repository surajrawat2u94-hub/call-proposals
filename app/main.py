from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .database import engine, Base, get_db_session
from .models import CallForProposal
from .schemas import CFPSchema, CFPListResponse, IngestResponse
from .ingestion import run_ingestion
from .maintenance import enforce_unique_index, dedupe_cfps


# Initialize DB tables
Base.metadata.create_all(bind=engine)
# Ensure unique index exists
try:
	enforce_unique_index(engine)
except Exception:
	pass

app = FastAPI(title="CFP Aggregator")


@app.get("/health")
def health():
	return {"status": "ok"}


@app.get("/cfps", response_model=CFPListResponse)
def list_cfps(
		session: Session = Depends(get_db_session),
		q: Optional[str] = Query(None, description="Free text search in title/summary"),
		source: Optional[str] = Query(None),
		sponsor: Optional[str] = Query(None),
		country: Optional[str] = Query(None),
		before: Optional[date] = Query(None, description="Deadline on or before"),
		after: Optional[date] = Query(None, description="Deadline on or after"),
		offset: int = Query(0, ge=0),
		limit: int = Query(20, ge=1, le=100),
	):
	query = select(CallForProposal)
	count_query = select(func.count(CallForProposal.id))

	def apply_filters(stmt):
		if q:
			like = f"%{q}%"
			stmt = stmt.where(
				(CallForProposal.title.ilike(like)) | (CallForProposal.summary.ilike(like))
			)
		if source:
			stmt = stmt.where(CallForProposal.source == source)
		if sponsor:
			stmt = stmt.where(CallForProposal.sponsor == sponsor)
		if country:
			stmt = stmt.where(CallForProposal.country == country)
		if before:
			stmt = stmt.where((CallForProposal.deadline <= before) | (CallForProposal.deadline.is_(None)))
		if after:
			stmt = stmt.where(CallForProposal.deadline >= after)
		return stmt

	query = apply_filters(query).order_by(CallForProposal.deadline.is_(None), CallForProposal.deadline.asc())
	count_query = apply_filters(count_query)

	total = session.execute(count_query).scalar_one()
	rows = session.execute(query.offset(offset).limit(limit)).scalars().all()

	return CFPListResponse(items=[CFPSchema.model_validate(r) for r in rows], total=total)


@app.post("/ingest", response_model=IngestResponse)
def ingest(session: Session = Depends(get_db_session)):
	inserted, updated, sources = run_ingestion(session)
	return IngestResponse(inserted=inserted, updated=updated, sources=sources)


@app.post("/maintenance/dedupe")
def maintenance_dedupe(session: Session = Depends(get_db_session)):
	deleted = dedupe_cfps(session)
	return {"deleted": deleted}