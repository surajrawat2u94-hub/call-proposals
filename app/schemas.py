from __future__ import annotations

from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, HttpUrl


class CFPSchema(BaseModel):
	id: int
	source: str
	external_id: str
	title: str
	summary: Optional[str] = None
	url: HttpUrl
	sponsor: Optional[str] = None
	country: Optional[str] = None
	deadline: Optional[date] = None
	categories: Optional[str] = None
	currency: Optional[str] = None
	amount_min: Optional[int] = None
	amount_max: Optional[int] = None
	created_at: datetime
	updated_at: datetime

	class Config:
		from_attributes = True


class CFPListResponse(BaseModel):
	items: List[CFPSchema]
	total: int


class IngestResponse(BaseModel):
	inserted: int
	updated: int
	sources: List[str]