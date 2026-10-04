"""Thin, caching, batching client for the OpenAlex API.

Only the fields the rest of the pipeline needs are requested (via `select=`)
to keep payloads small. All GET requests go through a disk cache so reruns of
the pipeline do not re-hit the network for the same query.
"""

from __future__ import annotations

import logging
import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from .cache import DiskCache

logger = logging.getLogger("scholarly_radar.openalex")

BASE_URL = "https://api.openalex.org"

WORK_SELECT = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "cited_by_count",
        "cited_by_percentile_year",
        "referenced_works",
        "related_works",
        "topics",
        "authorships",
        "primary_location",
        "abstract_inverted_index",
        "ids",
    ]
)

USER_AGENT = "scholarly-radar/0.1 (personal research tool; https://github.com/)"


class OpenAlexCacheMiss(RuntimeError):
    """Raised instead of contacting OpenAlex during an offline/cache-only run."""


class OpenAlexRequestBudgetExceeded(RuntimeError):
    """Raised before a real request would exceed the configured run budget."""


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild plain text."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    max_pos = -1
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
            max_pos = max(max_pos, i)
    return " ".join(positions.get(i, "") for i in range(max_pos + 1)).strip() or None


class OpenAlexClient:
    def __init__(
        self,
        cache: DiskCache,
        timeout: float = 20.0,
        max_retries: int = 5,
        min_interval: float = 0.50,
        verbose: bool = False,
        cache_only: bool = False,
        max_network_requests: int | None = None,
        request_journal: str | Path | None = None,
    ):
        self.cache = cache
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.verbose = verbose
        self.cache_only = cache_only
        self.max_network_requests = max_network_requests
        self.request_budget_exhausted = False
        self.request_journal = Path(request_journal) if request_journal else None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.ssh_egress_host = os.environ.get("OPENALEX_SSH_EGRESS_HOST", "").strip()
        self._last_request_ts = 0.0
        self.request_count = 0

    def _journal(self, event: dict) -> None:
        if self.request_journal is None:
            return
        self.request_journal.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), **event}
        with self.request_journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _throttle(self) -> None:
        """Keep this personal research client deliberately conservative.

        The default permits at most about two real HTTP requests per second.
        Cache hits return before this method, so reruns neither contact
        OpenAlex nor pay an artificial delay.
        """
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _get(self, path: str, params: dict) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        cache_key = f"{path}?{urlencode(sorted(params.items()))}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        if self.cache_only:
            raise OpenAlexCacheMiss(
                f"OpenAlex cache miss in cache-only mode: {cache_key}"
            )
        if self.max_network_requests is not None and self.request_count >= self.max_network_requests:
            self.request_budget_exhausted = True
            raise OpenAlexRequestBudgetExceeded(
                f"OpenAlex request budget ({self.max_network_requests}) exhausted before: {cache_key}"
            )

        url = f"{BASE_URL}{path}"
        full_url = f"{url}?{urlencode(sorted(params.items()))}"
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                if self.ssh_egress_host:
                    completed = subprocess.run(
                        [
                            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            self.ssh_egress_host,
                            f"curl -fsS --max-time {int(self.timeout)} {shlex.quote(full_url)}",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout + 15,
                    )
                    self.request_count += 1
                    self._journal({
                        "cache_key": cache_key,
                        "attempt": attempt + 1,
                        "transport": "ssh_curl",
                        "returncode": completed.returncode,
                    })
                    if completed.returncode != 0:
                        if self.verbose:
                            logger.warning(
                                "Remote curl exit %s on attempt %d for %s",
                                completed.returncode, attempt + 1, cache_key,
                            )
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    data = json.loads(completed.stdout)
                    self.cache.set(cache_key, data)
                    return data
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self.request_count += 1
                self._journal({
                    "cache_key": cache_key,
                    "attempt": attempt + 1,
                    "status_code": resp.status_code,
                })
                if resp.status_code == 429 or resp.status_code >= 500:
                    if self.verbose:
                        logger.warning("HTTP %s on attempt %d for %s", resp.status_code, attempt + 1, cache_key)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json()
                self.cache.set(cache_key, data)
                return data
            except requests.RequestException as exc:
                last_exc = exc
                self._journal({
                    "cache_key": cache_key,
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                })
                if self.verbose:
                    logger.warning("Request error on attempt %d for %s: %s", attempt + 1, cache_key, exc)
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError(f"OpenAlex request failed after {self.max_retries} attempts: {cache_key}") from last_exc

    def get_work(self, openalex_id: str) -> dict | None:
        wid = openalex_id.rsplit("/", 1)[-1]
        data = self._get(f"/works/{wid}", {"select": WORK_SELECT})
        return data or None

    def get_work_by_doi(self, doi: str) -> dict | None:
        doi = doi.strip()
        try:
            data = self._get(f"/works/doi:{doi}", {"select": WORK_SELECT})
        except OpenAlexCacheMiss:
            raise
        except RuntimeError:
            return None
        return data or None

    def search_by_title(self, title: str, per_page: int = 5) -> list[dict]:
        data = self._get(
            "/works",
            {"filter": f"title.search:{title}", "select": WORK_SELECT, "per-page": per_page},
        )
        return data.get("results", []) if data else []

    def search_works(
        self,
        query: str,
        per_page: int = 100,
        since_year: int | None = None,
        until_year: int | None = None,
    ) -> list[dict]:
        """Search work title, abstract, and full text with a bounded one-page query."""

        if not 1 <= per_page <= 100:
            raise ValueError("OpenAlex search per_page must be between 1 and 100")
        if since_year is not None and until_year is not None and since_year > until_year:
            raise ValueError("since_year cannot be later than until_year")
        year_filters = []
        if since_year is not None:
            year_filters.append(f"publication_year:>{since_year - 1}")
        if until_year is not None:
            year_filters.append(f"publication_year:<{until_year + 1}")
        data = self._get(
            "/works",
            {
                "search": query,
                "filter": ",".join(year_filters) or None,
                "select": WORK_SELECT,
                "per-page": per_page,
            },
        )
        return data.get("results", []) if data else []

    def get_works_by_ids(
        self, openalex_ids: list[str], batch_size: int = 50, since_year: int | None = None
    ) -> dict[str, dict]:
        """Batch-fetch works by OpenAlex ID using OR'd filters. Returns id -> work.

        Pass `since_year` to push the date filter into the query itself instead
        of fetching every id and discarding old ones client-side -- this is the
        related-works channel's main lever for not burning request/quota budget
        on works that would be filtered out anyway.
        """
        out: dict[str, dict] = {}
        short_ids = sorted({oid.rsplit("/", 1)[-1] for oid in openalex_ids})
        for batch in chunked(short_ids, batch_size):
            filter_val = "openalex_id:" + "|".join(batch)
            if since_year is not None:
                filter_val += f",publication_year:>{since_year - 1}"
            page = 1
            while True:
                data = self._get(
                    "/works",
                    {
                        "filter": filter_val,
                        "select": WORK_SELECT,
                        "per-page": 200,
                        "page": page,
                    },
                )
                results = data.get("results", []) if data else []
                for w in results:
                    out[w["id"]] = w
                if len(results) < 200:
                    break
                page += 1
        return out

    def _fetch_paginated(self, filter_val: str, max_results: int, sort: str) -> list[dict]:
        """Cursor-page through `/works` for one filter until `max_results` is
        collected or results run out. Shared by any channel that needs more
        than one page -- see `get_citing_works` and `get_works_by_authors` for
        why their prior single-page defaults were silently truncating most of
        what was actually available (measured via `meta.count`, not guessed)."""
        out: list[dict] = []
        cursor = "*"
        while cursor and len(out) < max_results:
            data = self._get(
                "/works",
                {
                    "filter": filter_val,
                    "select": WORK_SELECT,
                    "per-page": 200,
                    "cursor": cursor,
                    "sort": sort,
                },
            )
            results = data.get("results", []) if data else []
            out.extend(results)
            meta = data.get("meta", {}) if data else {}
            cursor = meta.get("next_cursor")
            if not results:
                break
        return out

    def get_citing_works(
        self,
        seed_openalex_ids: list[str],
        since_year: int,
        batch_size: int = 50,
        max_results_per_batch: int = 800,
    ) -> list[dict]:
        """Recent works citing any of the given seed works (Channel A).

        800 is not arbitrary: measured against this project's own sample
        library, a 50-seed batch typically has 1,500-3,500 true matches
        (`meta.count`) in a 2-3 year window, and the previous default of 200
        was silently truncating ~90%+ of them after a single page. 800 still
        keeps this bounded (a handful of paginated requests per batch, not an
        unbounded crawl) while recovering a meaningfully larger, still-recent-
        first-sorted slice.
        """
        short_ids = sorted({oid.rsplit("/", 1)[-1] for oid in seed_openalex_ids})
        out: list[dict] = []
        for batch in chunked(short_ids, batch_size):
            filter_val = f"cites:{'|'.join(batch)},publication_year:>{since_year - 1}"
            out.extend(self._fetch_paginated(filter_val, max_results_per_batch, sort="publication_date:desc"))
        return out

    def get_works_by_authors(
        self, author_ids: list[str], since_year: int, batch_size: int = 50, max_results_per_batch: int = 500
    ) -> list[dict]:
        """Recent works by a set of author IDs (Channel C).

        Previously a single per-page=100 fetch with no further pagination --
        measured true availability for this project's sample library was ~830
        works for 32 recurring authors, so that one page was silently dropping
        most of them. 500 recovers the large majority while staying bounded.
        """
        short_ids = sorted({aid.rsplit("/", 1)[-1] for aid in author_ids})
        out: list[dict] = []
        for batch in chunked(short_ids, batch_size):
            filter_val = f"author.id:{'|'.join(batch)},publication_year:>{since_year - 1}"
            out.extend(self._fetch_paginated(filter_val, max_results_per_batch, sort="publication_date:desc"))
        return out

    def get_works_by_topics(
        self, topic_ids: list[str], since_year: int, batch_size: int = 30, per_page: int = 100
    ) -> list[dict]:
        """Recent works in a set of topic IDs (Channel D / F).

        Deliberately single-page and uncapped upward, unlike get_citing_works
        / get_works_by_authors: a topic's true `meta.count` here was measured
        at ~350,000 works for this project's sample library (broad fields like
        "Data Visualization" are enormous), so paginating further would mean
        scanning an effectively unbounded pool for marginal gain -- exactly
        what the design spec warns against. `sort=cited_by_count:desc` is the
        real bounding mechanism: it takes the single page of most-established
        work in the topic rather than a deeper, noisier slice.
        """
        short_ids = sorted({tid.rsplit("/", 1)[-1] for tid in topic_ids})
        out: list[dict] = []
        for batch in chunked(short_ids, batch_size):
            filter_val = f"topics.id:{'|'.join(batch)},publication_year:>{since_year - 1}"
            data = self._get(
                "/works",
                {
                    "filter": filter_val,
                    "select": WORK_SELECT,
                    "per-page": per_page,
                    "sort": "cited_by_count:desc",
                },
            )
            out.extend(data.get("results", []) if data else [])
        return out
