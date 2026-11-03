"""Notification helpers (currently: ntfy)."""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def notify_ntfy(
    topic: str | None,
    title: str,
    message: str,
    priority: str = "default",
    server: str | None = None,
) -> None:
    """Send a notification to an ntfy topic. Silently no-ops if topic is falsy."""
    if not topic:
        return
    server = (server or os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    try:
        requests.post(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "scroll"},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Failed to send ntfy notification: %s", exc)
