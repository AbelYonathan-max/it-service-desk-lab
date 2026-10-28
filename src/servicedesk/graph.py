"""A small client for Microsoft Graph, signed in as the lab's deploy identity.

In GitHub Actions the workflow signs in to Azure with OpenID Connect and this asks the Azure CLI for
a Graph token, so no secret is stored anywhere. Locally, set GRAPH_TOKEN instead.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com"
RETRY_STATUS = {429, 500, 502, 503, 504}


class GraphError(RuntimeError):
    """Graph refused a request. ``code`` is Graph's own error code, such as ``Request_ResourceNotFound``."""

    def __init__(self, message: str, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def token_from_azure_cli() -> str:
    """A Graph token for whoever the Azure CLI is signed in as (the workflow's OIDC sign-in)."""
    token = os.environ.get("GRAPH_TOKEN", "").strip()
    if token:
        return token
    command = ["az", "account", "get-access-token", "--resource-type", "ms-graph", "--query", "accessToken"]
    # The Azure CLI from PATH, with fixed arguments and no shell.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [*command, "--output", "tsv"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GraphError(f"Could not get a Microsoft Graph token from the Azure CLI: {result.stderr.strip()}")
    return result.stdout.strip()


class Graph:
    """Calls Graph with retries. Paths start with the version: ``/v1.0/users`` or ``/beta/...``."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        self._token = token
        self._client = client or httpx.Client(timeout=timeout)
        self.max_attempts = max_attempts
        self._sleep = sleep
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Graph:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        """The reply's JSON body (``None`` for 204, or for 404 when ``allow_not_found``)."""
        url = path if path.startswith("https://") else GRAPH + path
        sent = {"Authorization": f"Bearer {self._token}", "Accept": "application/json", **(headers or {})}
        for attempt in range(1, self.max_attempts + 1):
            self.requests_made += 1
            try:
                response = self._client.request(method, url, params=params, json=json, headers=sent)
            except httpx.TransportError as error:
                if attempt == self.max_attempts:
                    raise GraphError(f"{method} {path} failed: {error}") from error
                self._sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUS and attempt < self.max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else 2**attempt)
                continue
            return self._result(method, path, response, allow_not_found)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _result(method: str, path: str, response: httpx.Response, allow_not_found: bool) -> Any:
        status = response.status_code
        if status == 204 or (status == 404 and allow_not_found):
            return None
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {"value": response.text}
        if status >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            code = str(error.get("code", ""))
            message = str(error.get("message", "")) or response.text[:300]
            raise GraphError(f"{method} {path} answered {status} {code}: {message}", status, code)
        return body

    def get(self, path: str, params: Mapping[str, str] | None = None, **kwargs: Any) -> Any:
        return self.request("GET", path, params=params, **kwargs)

    def get_all(
        self, path: str, params: Mapping[str, str] | None = None, *, headers: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Every item of a collection, following ``@odata.nextLink``."""
        items: list[dict[str, Any]] = []
        page = self.request("GET", path, params=params, headers=headers)
        while True:
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return items
            page = self.request("GET", next_link, headers=headers)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, json=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, json=body)

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, json=body)


def odata_quote(value: str) -> str:
    """A string literal for an OData ``$filter``: single quotes are doubled."""
    return "'" + value.replace("'", "''") + "'"
