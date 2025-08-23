from __future__ import annotations

from typing import Tuple

from sqlalchemy import text, select, func, delete, and_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import CallForProposal


def enforce_unique_index(engine: Engine) -> bool:
	"""Ensure a unique index exists on (source, external_id). Returns True if created now."""
	# SQLite supports CREATE UNIQUE INDEX IF NOT EXISTS
	with engine.begin() as conn:
		conn.execute(text(
			"CREATE UNIQUE INDEX IF NOT EXISTS uq_cfp_source_external_id ON cfp(source, external_id)"
		))
	# We cannot easily tell if it existed before; return False to indicate unknown/assumed existing
	return True


def dedupe_cfps(session: Session) -> int:
	"""Remove duplicate CFP rows keeping the lowest id for each (source, external_id). Returns rows deleted."""
	duplicates = session.execute(
		select(
			CallForProposal.source,
			CallForProposal.external_id,
			func.min(CallForProposal.id).label("keep_id"),
			(func.count(CallForProposal.id) - 1).label("extra")
		).group_by(CallForProposal.source, CallForProposal.external_id)
		 .having(func.count(CallForProposal.id) > 1)
	).all()

	deleted_total = 0
	for source, external_id, keep_id, extra in duplicates:
		stmt = delete(CallForProposal).where(
			and_(
				CallForProposal.source == source,
				CallForProposal.external_id == external_id,
				CallForProposal.id != keep_id,
			)
		)
		result = session.execute(stmt)
		deleted_total += result.rowcount or 0

	session.commit()
	return deleted_total