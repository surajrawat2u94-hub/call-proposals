from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Text, Date, Integer, DateTime, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class CallForProposal(Base):
	__tablename__ = "cfp"

	id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
	source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
	external_id: Mapped[str] = mapped_column(String(200), nullable=False)
	title: Mapped[str] = mapped_column(String(500), nullable=False)
	summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
	url: Mapped[str] = mapped_column(String(1000), nullable=False)
	sponsor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
	country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
	deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
	categories: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
	currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
	amount_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
	amount_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
	updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
	hash_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

	__table_args__ = (
		UniqueConstraint("source", "external_id", name="uq_source_external_id"),
		Index("ix_cfp_text_search", "title", "summary"),
	)