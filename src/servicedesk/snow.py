"""A small client for the ServiceNow REST API (Table API and Service Catalog API).

Every value goes over the wire as ServiceNow stores it: references are sys_ids, booleans are
``"true"``/``"false"`` and date-times are UTC ``yyyy-MM-dd HH:mm:ss``. Reads use
``sysparm_exclude_reference_link`` so a reference comes back as a plain sys_id.

A developer instance that has gone to sleep answers with a web page instead of JSON; that becomes
:class:`InstanceAsleepError` with instructions, instead of a confusing parse error.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

Record = dict[str, Any]

RETRY_STATUS = {429, 500, 502, 503, 504}
PAGE_SIZE = 500
HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# What getaddrinfo says, on Linux, macOS and Windows, when a host name does not exist.
NO_SUCH_HOST = (
    "Name or service not known",
    "nodename nor servname",
    "No address associated",
    "getaddrinfo failed",
)


class ServiceNowError(RuntimeError):
    """ServiceNow refused a request, or answered with something that is not JSON."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class InstanceAsleepError(ServiceNowError):
    """The developer instance is hibernating and must be woken from the developer site."""


@dataclass(frozen=True)
class Connection:
    instance: str
    username: str
    password: str

    @property
    def base_url(self) -> str:
        """``https://`` and the host: from a name (dev123456), a host name, or an address copied from a browser."""
        value = self.instance.strip()
        if "://" not in value:
            value = "https://" + (value if "." in value else f"{value}.service-now.com")
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            url = None
        if url is None or url.userinfo or not HOST_NAME.match(url.host):
            # The value itself is never shown: it is a secret in the workflows.
            raise ServiceNowError(
                "SERVICENOW_INSTANCE is not an instance name. It should be the name from developer.servicenow.com, "
                "like dev123456, or the instance's address, like https://dev123456.service-now.com."
            )
        return f"https://{url.host}"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Connection:
        """SERVICENOW_INSTANCE (dev123456), SERVICENOW_USERNAME (default admin), SERVICENOW_PASSWORD."""
        env = os.environ if environ is None else environ
        instance = env.get("SERVICENOW_INSTANCE", "").strip()
        # Generated passwords never start or end with a space; a pasted one can end with a line break.
        password = env.get("SERVICENOW_PASSWORD", "").strip()
        if not instance or not password:
            raise ServiceNowError(
                "No ServiceNow connection: set SERVICENOW_INSTANCE and SERVICENOW_PASSWORD "
                "(and SERVICENOW_USERNAME if it is not admin)."
            )
        connection = cls(instance, env.get("SERVICENOW_USERNAME", "admin").strip() or "admin", password)
        _ = connection.base_url  # checks the instance name before any request is made
        return connection

    def __repr__(self) -> str:  # never show the password
        return f"Connection(instance={self.instance!r}, username={self.username!r})"


def encode(value: Any) -> str:
    """A Python value as ServiceNow stores it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def quote(value: str) -> str:
    """Escape a value for an encoded query: ``^`` separates conditions, so it is doubled."""
    return value.replace("^", "^^")


def in_query(field: str, values: Iterable[str]) -> str:
    """``fieldINa,b,c``. Values containing commas cannot go in an IN list, so they are ORed instead."""
    items = list(values)
    if not items:
        return f"{field}=NOTHING-MATCHES-THIS"
    if any("," in item for item in items):
        return "^OR".join(f"{field}={quote(item)}" for item in items)
    return f"{field}IN" + ",".join(quote(item) for item in items)


class ServiceNow:
    """Talks to one instance. Pass ``client`` to reuse an httpx client (tests pass a mocked one)."""

    def __init__(
        self,
        connection: Connection,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        self.connection = connection
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=timeout)
        self._auth = httpx.BasicAuth(connection.username, connection.password)
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ServiceNow:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- transport -------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        allow_not_found: bool = False,
    ) -> Any:
        """Calls the API and returns the ``result`` part of the reply (``None`` for 204 or 404)."""
        url = self.connection.base_url + path
        headers = {"Accept": "application/json"}
        for attempt in range(1, self.max_attempts + 1):
            self.requests_made += 1
            try:
                response = self._client.request(
                    method, url, params=params, json=json, headers=headers, auth=self._auth
                )
            except httpx.TransportError as error:
                if any(text in str(error) for text in NO_SUCH_HOST):
                    raise ServiceNowError(
                        f"There is no ServiceNow instance at {self.connection.base_url}. Check the instance name "
                        "at developer.servicenow.com: a released instance is gone, and a new one has a new name."
                    ) from error
                if attempt < self.max_attempts:
                    self._sleep(2**attempt)
                    continue
                raise ServiceNowError(f"Could not reach {self.connection.base_url}: {error}") from error

            if response.status_code in RETRY_STATUS and attempt < self.max_attempts:
                self._sleep(2**attempt)
                continue
            return self._result(method, path, response, allow_not_found)
        raise AssertionError("unreachable")  # pragma: no cover

    def _result(self, method: str, path: str, response: httpx.Response, allow_not_found: bool) -> Any:
        status = response.status_code
        if status == 204:
            return None
        if status == 404 and allow_not_found:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            text = response.text.lower()
            if "hibernat" in text or "instance is waking" in text or "wake your instance" in text:
                raise InstanceAsleepError(
                    "The ServiceNow developer instance is asleep. Sign in at https://developer.servicenow.com "
                    "to wake it (a few minutes), then run this again.",
                    status,
                )
            raise ServiceNowError(
                f"{method} {path} answered {status} with a web page instead of JSON.", status
            )
        try:
            body = response.json()
        except ValueError as error:
            raise ServiceNowError(f"{method} {path} answered {status} with invalid JSON.", status) from error

        if status == 401:
            raise ServiceNowError(
                f"ServiceNow refused the user name or password for {self.connection.username}.", status
            )
        if status >= 400:
            detail = body.get("error", {}) if isinstance(body, dict) else {}
            message = detail.get("message") or "error"
            extra = detail.get("detail") or ""
            raise ServiceNowError(f"{method} {path} failed ({status}): {message}. {extra}".strip(), status)
        return body.get("result") if isinstance(body, dict) else body

    # -- Table API -------------------------------------------------------------------------------

    def query(
        self,
        table: str,
        query: str = "",
        *,
        fields: Iterable[str] | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[Record]:
        """All records matching an encoded query, fetched a page at a time."""
        records: list[Record] = []
        offset = 0
        encoded = query
        if order_by:
            encoded = f"{query}^ORDERBY{order_by}" if query else f"ORDERBY{order_by}"
        while True:
            page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(records))
            params = {
                "sysparm_query": encoded,
                "sysparm_limit": str(page),
                "sysparm_offset": str(offset),
                "sysparm_exclude_reference_link": "true",
            }
            if fields:
                params["sysparm_fields"] = ",".join(fields)
            batch = self.request("GET", f"/api/now/table/{table}", params=params) or []
            records.extend(batch)
            offset += len(batch)
            if len(batch) < page or (limit is not None and len(records) >= limit):
                return records

    def first(self, table: str, query: str, *, fields: Iterable[str] | None = None) -> Record | None:
        found = self.query(table, query, fields=fields, limit=1)
        return found[0] if found else None

    def get(self, table: str, sys_id: str, *, fields: Iterable[str] | None = None) -> Record | None:
        params = {"sysparm_exclude_reference_link": "true"}
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        result = self.request("GET", f"/api/now/table/{table}/{sys_id}", params=params, allow_not_found=True)
        return result if isinstance(result, dict) else None

    def create(self, table: str, data: Mapping[str, Any], *, display_values: bool = False) -> Record:
        params = {"sysparm_exclude_reference_link": "true"}
        if display_values:
            params["sysparm_input_display_value"] = "true"
        body = {key: encode(value) for key, value in data.items()}
        result = self.request("POST", f"/api/now/table/{table}", params=params, json=body)
        if not isinstance(result, dict) or not result.get("sys_id"):
            raise ServiceNowError(f"Creating a {table} record returned no sys_id.")
        return result

    def update(
        self, table: str, sys_id: str, data: Mapping[str, Any], *, display_values: bool = False
    ) -> Record:
        params = {"sysparm_exclude_reference_link": "true"}
        if display_values:
            params["sysparm_input_display_value"] = "true"
        body = {key: encode(value) for key, value in data.items()}
        result = self.request("PATCH", f"/api/now/table/{table}/{sys_id}", params=params, json=body)
        return result if isinstance(result, dict) else {}

    def delete(self, table: str, sys_id: str) -> None:
        self.request("DELETE", f"/api/now/table/{table}/{sys_id}", allow_not_found=True)

    # -- Service Catalog API ---------------------------------------------------------------------

    def order(
        self, item_sys_id: str, variables: Mapping[str, Any], *, requested_for: str | None = None
    ) -> Record:
        """Orders a catalogue item as a person would, so variables, approvals and rules all run.

        Returns the request (REQ) as ``{"sys_id", "number"}``.
        """
        body: dict[str, Any] = {
            "sysparm_quantity": "1",
            "variables": {name: encode(value) for name, value in variables.items()},
        }
        if requested_for:
            body["sysparm_requested_for"] = requested_for
        result = self.request("POST", f"/api/sn_sc/servicecatalog/items/{item_sys_id}/order_now", json=body)
        if not isinstance(result, dict):
            raise ServiceNowError("Ordering the catalogue item returned nothing.")
        sys_id = result.get("request_id") or result.get("sys_id")
        number = result.get("request_number") or result.get("number")
        if not sys_id:
            raise ServiceNowError(f"Ordering the catalogue item returned no request: {result}")
        return {"sys_id": sys_id, "number": number}

    def instance_info(self) -> Record:
        """Who we are signed in as, and the instance's release (from the glide.buildtag property)."""
        user = self.first(
            "sys_user", f"user_name={quote(self.connection.username)}", fields=["sys_id", "name"]
        )
        build = self.first("sys_properties", "name=glide.buildtag.last", fields=["value"])
        return {
            "user": (user or {}).get("name", self.connection.username),
            "build": (build or {}).get("value", "unknown"),
        }
