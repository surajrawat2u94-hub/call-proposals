from __future__ import annotations

import hashlib
from typing import Iterable, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.base import BaseAdapter, CFPItem
from .adapters.example_grants_gov import GrantsGovAdapter
from .models import CallForProposal


def compute_fingerprint(item: CFPItem) -> str:
	parts = [
		item.title or "",
		item.summary or "",
		item.url or "",
		item.sponsor or "",
		item.country or "",
		str(item.deadline) if item.deadline else "",
		item.categories or "",
		item.currency or "",
		str(item.amount_min) if item.amount_min is not None else "",
		str(item.amount_max) if item.amount_max is not None else "",
	]
	joined = "\x1f".join(parts)
	return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def default_adapters() -> List[BaseAdapter]:
	return [GrantsGovAdapter()]


def upsert_items(session: Session, items: Iterable[CFPItem]) -> Tuple[int, int]:
	inserted = 0
	updated = 0
	for item in items:
		fingerprint = compute_fingerprint(item)

		existing = session.execute(
			select(CallForProposal).where(
				CallForProposal.source == item.source,
				CallForProposal.external_id == item.external_id,
			)
		).scalar_one_or_none()

		if existing is None:
			record = CallForProposal(
				source=item.source,
				external_id=item.external_id,
				title=item.title,
				summary=item.summary,
				url=item.url,
				sponsor=item.sponsor,
				country=item.country,
				deadline=item.deadline,
				categories=item.categories,
				currency=item.currency,
				amount_min=item.amount_min,
				amount_max=item.amount_max,
				hash_fingerprint=fingerprint,
			)
			session.add(record)
			inserted += 1
		else:
			if existing.hash_fingerprint != fingerprint:
				existing.title = item.title
				existing.summary = item.summary
				existing.url = item.url
				existing.sponsor = item.sponsor
				existing.country = item.country
				existing.deadline = item.deadline
				existing.categories = item.categories
				existing.currency = item.currency
				existing.amount_min = item.amount_min
				existing.amount_max = item.amount_max
				existing.hash_fingerprint = fingerprint
				updated += 1

	session.commit()
	return inserted, updated


def run_ingestion(session: Session, adapters: List[BaseAdapter] | None = None) -> Tuple[int, int, List[str]]:
	if adapters is None:
		adapters = default_adapters()

	inserted_total = 0
	updated_total = 0
	source_names: List[str] = []

	for adapter in adapters:
		source_names.append(adapter.name)
		ins, upd = upsert_items(session, adapter.fetch())
		inserted_total += ins
		updated_total += upd

	return inserted_total, updated_total, source_names