from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional


@dataclass
class CFPItem:
	source: str
	external_id: str
	title: str
	summary: Optional[str]
	url: str
	sponsor: Optional[str]
	country: Optional[str]
	deadline: Optional[date]
	categories: Optional[str]
	currency: Optional[str]
	amount_min: Optional[int]
	amount_max: Optional[int]


class BaseAdapter:
	"""Interface for CFP source adapters."""

	name: str = "base"

	def fetch(self) -> Iterable[CFPItem]:
		"""Yield CFPItem from the source."""
		raise NotImplementedError