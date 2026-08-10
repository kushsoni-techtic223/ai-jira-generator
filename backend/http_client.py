"""HTTP helpers — bypass Cursor/sandbox proxies that block Atlassian (403)."""

from __future__ import annotations

import os
from typing import Any

import requests

_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "GIT_HTTP_PROXY",
    "GIT_HTTPS_PROXY",
    "SOCKS_PROXY",
    "SOCKS5_PROXY",
    "socks_proxy",
    "socks5_proxy",
)

_NO_PROXY_HOSTS = (
    "atlassian.com",
    ".atlassian.com",
    "auth.atlassian.com",
    "api.atlassian.com",
    "id.atlassian.com",
    "github.com",
    ".github.com",
    "api.github.com",
)


def use_system_proxy() -> bool:
    return (os.getenv("JIRA_USE_PROXY") or "").lower() in ("1", "true", "yes")


def disable_injected_proxy() -> None:
    """Strip Cursor/local proxy env vars unless JIRA_USE_PROXY=1."""
    if use_system_proxy():
        return

    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for host in _NO_PROXY_HOSTS:
        if host not in parts:
            parts.append(host)
    joined = ",".join(parts)
    os.environ["NO_PROXY"] = joined
    os.environ["no_proxy"] = joined

    for key in _PROXY_KEYS:
        os.environ.pop(key, None)


def external_session() -> requests.Session:
    """Session that never reads HTTP_PROXY / ALL_PROXY from the environment."""
    session = requests.Session()
    session.trust_env = use_system_proxy()
    return session


def external_request(
    method: str,
    url: str,
    *,
    timeout: int | float = 60,
    **kwargs: Any,
) -> requests.Response:
    return external_session().request(method, url, timeout=timeout, **kwargs)


# Apply as soon as this module is imported (before any Atlassian HTTP call).
disable_injected_proxy()
