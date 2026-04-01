from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import Settings


class ExternalDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalTicketBatch:
    provider: str
    records: list[dict[str, Any]]
    summary: dict[str, Any]


class ExternalDataService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load_support_tickets(self, source: dict[str, Any]) -> ExternalTicketBatch:
        provider = str(source.get("provider", "")).strip().lower()
        if provider == "github_issues":
            return self._load_github_issues(source)
        if provider == "nyc_311":
            return self._load_nyc_311(source)
        raise ExternalDataError(f"unsupported provider: {provider}")

    def _load_github_issues(self, source: dict[str, Any]) -> ExternalTicketBatch:
        repo = str(source.get("repo", "fastapi/fastapi")).strip()
        state = str(source.get("state", "open")).strip() or "open"
        per_page = max(1, min(int(source.get("per_page", 5)), 20))
        params = urlencode({"state": state, "per_page": per_page})
        url = f"https://api.github.com/repos/{repo}/issues?{params}"
        payload = self._fetch_json(url)
        if not isinstance(payload, list):
            raise ExternalDataError("github issues response is not a list")
        tickets = []
        for item in payload:
            if "pull_request" in item:
                continue
            tickets.append(
                {
                    "customer": repo,
                    "message": item.get("title", ""),
                    "body": item.get("body") or "",
                    "source_id": f"issue#{item.get('number', '')}",
                    "source_url": item.get("html_url", ""),
                    "labels": [label.get("name", "") for label in item.get("labels", []) if isinstance(label, dict)],
                }
            )
        return ExternalTicketBatch(
            provider="github_issues",
            records=tickets,
            summary={
                "repo": repo,
                "state": state,
                "ticket_count": len(tickets),
            },
        )

    def _load_nyc_311(self, source: dict[str, Any]) -> ExternalTicketBatch:
        limit = max(1, min(int(source.get("limit", 6)), 20))
        complaint_type = str(source.get("complaint_type", "")).strip()
        borough = str(source.get("borough", "")).strip().upper()
        filters = ["agency is not null", "descriptor is not null"]
        if complaint_type:
            safe_type = complaint_type.replace("'", "''")
            filters.append(f"complaint_type = '{safe_type}'")
        if borough:
            safe_borough = borough.replace("'", "''")
            filters.append(f"borough = '{safe_borough}'")
        params = urlencode(
            {
                "$select": "unique_key,agency,complaint_type,descriptor,borough,incident_address,created_date,status",
                "$where": " AND ".join(filters),
                "$order": "created_date DESC",
                "$limit": limit,
            }
        )
        url = f"https://data.cityofnewyork.us/resource/erm2-nwe9.json?{params}"
        payload = self._fetch_json(url)
        if not isinstance(payload, list):
            raise ExternalDataError("nyc 311 response is not a list")
        tickets = []
        for item in payload:
            descriptor = item.get("descriptor", "")
            complaint = item.get("complaint_type", "")
            message = f"{complaint} - {descriptor}".strip(" -")
            address = item.get("incident_address", "")
            borough_name = item.get("borough", "")
            tickets.append(
                {
                    "customer": item.get("agency", "NYC 311"),
                    "message": message,
                    "body": f"{borough_name} {address}".strip(),
                    "source_id": item.get("unique_key", ""),
                    "source_url": "https://data.cityofnewyork.us/resource/erm2-nwe9",
                    "status": item.get("status", ""),
                }
            )
        return ExternalTicketBatch(
            provider="nyc_311",
            records=tickets,
            summary={
                "borough": borough,
                "complaint_type": complaint_type,
                "ticket_count": len(tickets),
            },
        )

    def _fetch_json(self, url: str) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "FlowPilot/1.0",
        }
        if self.settings.github_token and "api.github.com" in url:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.settings.http_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ExternalDataError(str(exc)) from exc
