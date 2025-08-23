from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Iterable, Optional

import httpx

from .base import BaseAdapter, CFPItem


class GrantsGovAdapter(BaseAdapter):
	name = "grants_gov"

	BASE_URL = "https://www.grants.gov/grantsws/rest/opportunities/search/"

	def fetch(self) -> Iterable[CFPItem]:
		params = {
			"startRecordNum": 1,
			"sortBy": "openDate|desc",
			"oppStatuses": "forecasted|posted",
			"rows": 50,
		}
		with httpx.Client(timeout=30) as client:
			resp = client.get(self.BASE_URL, params=params)
			resp.raise_for_status()
			data = resp.json()
			for opp in data.get("opportunitySearchResult", []):
				opp_number = opp.get("opportunityNumber") or hashlib.sha1(opp.get("title", "").encode()).hexdigest()
				yield CFPItem(
					source=self.name,
					external_id=str(opp_number),
					title=opp.get("title") or "Untitled",
					summary=opp.get("description") or None,
					url=opp.get("opportunitySynopsisUrl") or "https://www.grants.gov/",
					sponsor=opp.get("agency") or None,
					country="United States",
					deadline=self._parse_date(opp.get("closeDate")),
					categories=",".join([c.get("name") for c in opp.get("cfdaList", []) if c.get("name")]) if opp.get("cfdaList") else None,
					currency="USD",
					amount_min=None,
					amount_max=None,
				)

	def _parse_date(self, raw: Optional[str]):
		if not raw:
			return None
		try:
			return datetime.strptime(raw, "%m/%d/%Y").date()
		except Exception:
			return None