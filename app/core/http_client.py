from __future__ import annotations

import json
from urllib import error, request


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: int = 30,
    disable_proxies: bool = True,
) -> dict[str, object]:
    raw_payload = json.dumps(payload).encode("utf-8")
    request_obj = request.Request(
        url=url,
        data=raw_payload,
        headers=headers,
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({} if disable_proxies else None))
    try:
        with opener.open(request_obj, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP request failed with status {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc.reason}") from exc

