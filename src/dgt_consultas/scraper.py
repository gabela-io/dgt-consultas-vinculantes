"""DGT PETETE scraper – session management, search pagination, and document fetching."""

import logging
import os
import threading
import time

import requests
import urllib3

from .ratelimit import TokenBucket
from .retry import MAX_RETRIES, RETRY_BACKOFFS, compute_backoff

# (connect, read) seconds: a stalled PETETE connection must fail and be
# retried, not hang a scheduled run until the job's own timeout.
REQUEST_TIMEOUT = (10, 60)

logger = logging.getLogger(__name__)

BASE_URL = "https://petete.tributos.hacienda.gob.es"

HEADERS = {
    # Identify the project honestly; PETETE accepts it.
    "User-Agent": "dgt-consultas/0.1 (+https://github.com/gabela-io/dgt-consultas-vinculantes)",
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/consultas/",
    "Content-Type": "application/x-www-form-urlencoded",
}


class DGTSession:
    """Manages an HTTP session against the PETETE server."""

    def __init__(
        self,
        rate_limit: float = 1.0,
        min_rate: float = 0.2,
        max_rate: float = 4.0,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # TLS is verified. PETETE's chain has been broken in the past (missing
        # intermediate); DGT_INSECURE_TLS=1 is the explicit escape hatch for
        # local runs. Never set it in CI: the bot commits what it downloads.
        if os.environ.get("DGT_INSECURE_TLS") == "1":
            logger.warning("TLS verification disabled (DGT_INSECURE_TLS=1)")
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.session.verify = False
        self.bucket = TokenBucket(rate=rate_limit, min_rate=min_rate, max_rate=max_rate)
        self._initialized = False
        self._init_lock = threading.Lock()

    def init(self) -> None:
        """Visit the landing page to obtain session cookies."""
        with self._init_lock:
            if self._initialized:
                return
            for attempt in range(MAX_RETRIES + 1):
                self.bucket.wait()
                try:
                    resp = self.session.get(
                        f"{BASE_URL}/consultas/", timeout=REQUEST_TIMEOUT
                    )
                except requests.RequestException as exc:
                    if attempt < MAX_RETRIES:
                        wait = RETRY_BACKOFFS[attempt]
                        logger.warning(
                            "Init request error (%s), retrying in %ds…", exc, wait
                        )
                        time.sleep(wait)
                        continue
                    raise
                if resp.status_code == 503:
                    if attempt < MAX_RETRIES:
                        wait = RETRY_BACKOFFS[attempt]
                        logger.warning("Init got 503, retrying in %ds…", wait)
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                self._initialized = True
                logger.info("Session initialized (cookies obtained)")
                return
            raise RuntimeError("Failed to initialize session after retries")

    def _ensure_init(self) -> None:
        if not self._initialized:
            self.init()

    def _post_with_retry(self, url: str, data: dict) -> requests.Response:
        self._ensure_init()
        for attempt in range(MAX_RETRIES + 1):
            self.bucket.wait()
            try:
                resp = self.session.post(url, data=data, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                self.bucket.record_failure()
                if attempt < MAX_RETRIES:
                    wait = compute_backoff(attempt)
                    logger.warning("Request error (%s), retrying in %.1fs…", exc, wait)
                    time.sleep(wait)
                    continue
                raise
            if resp.status_code == 401:
                if attempt < MAX_RETRIES:
                    logger.warning("Got 401, reinitializing session…")
                    with self._init_lock:
                        self._initialized = False
                    self.init()
                    continue
                resp.raise_for_status()
            if resp.status_code == 429 or resp.status_code >= 500:
                self.bucket.record_failure()
                if attempt < MAX_RETRIES:
                    wait = compute_backoff(attempt, resp)
                    logger.warning("Got %d, retrying in %.1fs…", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            self.bucket.record_success()
            return resp
        raise RuntimeError("Exceeded max retries")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        page: int = 1,
        date_start: str | None = None,
        date_end: str | None = None,
        number: str | None = None,
    ) -> str:
        """Run a paginated search and return the raw HTML.

        `number` restricts the search to one consulta number, which gives its
        current document id (ids in an old search_index/ can point elsewhere).
        """
        data: dict[str, str] = {
            "type2": "on",
            "NMCMP_1": "NUM-CONSULTA",
            "VLCMP_1": number or "",
            "OPCMP_1": ".Y",
            "NMCMP_2": "FECHA-SALIDA",
            "OPCMP_2": ".Y",
            "cmpOrder": "FECHA-SALIDA",
            "dirOrder": "1",
            "tab": "2",
            "page": str(page),
        }
        if date_start and date_end:
            data["dateIni_2"] = date_start
            data["dateEnd_2"] = date_end
            data["VLCMP_2"] = f"{date_start}..{date_end}"
        resp = self._post_with_retry(f"{BASE_URL}/consultas/do/search", data)
        return resp.text

    def fetch_document(self, doc_id: str) -> str:
        """Fetch a single document by its internal numeric ID."""
        data = {
            "doc": doc_id,
            "tab": "2",
            "query": ".T",
        }
        resp = self._post_with_retry(f"{BASE_URL}/consultas/do/document", data)
        return resp.text
