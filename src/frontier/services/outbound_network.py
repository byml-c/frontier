from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from ..config import settings


def _local_hosts() -> set[str]:
    api_host = urlsplit(settings.api_base_url).hostname
    return {host for host in ("127.0.0.1", "localhost", "::1", api_host) if host}


def _no_proxy_hosts(current: str | None) -> str:
    values = [item.strip() for item in (current or "").split(",") if item.strip()]
    for host in _local_hosts():
        if host not in values:
            values.append(host)
    return ",".join(values)


def outbound_proxy_for(url: str | None = None) -> str | None:
    proxy = (settings.outbound_proxy or "").strip()
    if not proxy:
        return None
    if url and (urlsplit(url).hostname or "").lower() in _local_hosts():
        return None
    return proxy


def external_httpx_kwargs(url: str | None = None) -> dict[str, Any]:
    """Return explicit HTTPX settings for an external service request."""
    proxy = outbound_proxy_for(url)
    return {"proxy": proxy} if proxy else {}


def agent_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build one outbound-network environment for every Agent invocation."""
    environment = dict(base if base is not None else os.environ)
    environment["FRONTIER_API_BASE_URL"] = settings.api_base_url
    environment["UV_CACHE_DIR"] = str(settings.task_dir / "uv-cache")
    proxy = outbound_proxy_for()
    if proxy:
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            environment[key] = proxy
        no_proxy = _no_proxy_hosts(environment.get("NO_PROXY") or environment.get("no_proxy"))
        environment["NO_PROXY"] = no_proxy
        environment["no_proxy"] = no_proxy
    return environment
