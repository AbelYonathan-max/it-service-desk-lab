import httpx
import pytest
import respx

from servicedesk.snow import Connection, InstanceAsleepError, ServiceNow, ServiceNowError, in_query

BASE = "https://dev123456.service-now.com"


@pytest.fixture
def sn() -> ServiceNow:
    return ServiceNow(Connection("dev123456", "admin", "secret"), sleep=lambda _: None)


def test_connection_accepts_a_name_or_a_url() -> None:
    assert Connection("dev1", "a", "b").base_url == "https://dev1.service-now.com"
    assert Connection("https://dev1.service-now.com/", "a", "b").base_url == "https://dev1.service-now.com"
    assert Connection("dev1.service-now.com", "a", "b").base_url == "https://dev1.service-now.com"
    copied = "https://Dev1.service-now.com/now/nav/ui/classic/params/target/home.do"
    assert Connection(copied, "a", "b").base_url == "https://dev1.service-now.com"
    assert "secret" not in repr(Connection("dev1", "admin", "secret"))


@pytest.mark.parametrize("value", ["someone@example.com", "dev 123456", "https:///home", "dev1/"])
def test_a_value_that_is_not_an_instance_is_refused_without_showing_it(value: str) -> None:
    with pytest.raises(ServiceNowError, match="not an instance name") as caught:
        Connection.from_environment({"SERVICENOW_INSTANCE": value, "SERVICENOW_PASSWORD": "pw"})
    assert value not in str(caught.value)


def test_connection_from_environment_needs_instance_and_password() -> None:
    with pytest.raises(ServiceNowError, match="SERVICENOW_INSTANCE"):
        Connection.from_environment({})
    connection = Connection.from_environment({"SERVICENOW_INSTANCE": "dev9", "SERVICENOW_PASSWORD": "pw"})
    assert connection.username == "admin"
    pasted = Connection.from_environment({"SERVICENOW_INSTANCE": "dev9", "SERVICENOW_PASSWORD": " pw\r\n"})
    assert pasted.password == "pw"


def test_in_query_falls_back_to_or_for_values_with_commas() -> None:
    assert in_query("name", ["a", "b"]) == "nameINa,b"
    assert in_query("name", ["a, b", "c"]) == "name=a, b^ORname=c"


@respx.mock
def test_query_pages_through_results(sn: ServiceNow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("servicedesk.snow.PAGE_SIZE", 2)
    route = respx.get(f"{BASE}/api/now/table/incident")
    route.side_effect = [
        httpx.Response(200, json={"result": [{"sys_id": "1"}, {"sys_id": "2"}]}),
        httpx.Response(200, json={"result": [{"sys_id": "3"}]}),
    ]
    records = sn.query("incident", "active=true", fields=["sys_id"])
    assert [r["sys_id"] for r in records] == ["1", "2", "3"]
    assert route.calls[1].request.url.params["sysparm_offset"] == "2"
    assert route.calls[0].request.url.params["sysparm_exclude_reference_link"] == "true"


@respx.mock
def test_an_instance_that_does_not_exist_says_so(sn: ServiceNow) -> None:
    route = respx.get(f"{BASE}/api/now/table/sys_user")
    route.side_effect = httpx.ConnectError("[Errno -2] Name or service not known")
    with pytest.raises(ServiceNowError, match="There is no ServiceNow instance at"):
        sn.first("sys_user", "user_name=admin")
    assert route.call_count == 1


@respx.mock
def test_retries_when_rate_limited(sn: ServiceNow) -> None:
    route = respx.get(f"{BASE}/api/now/table/sys_user")
    route.side_effect = [
        httpx.Response(429, json={}),
        httpx.Response(200, json={"result": [{"sys_id": "1"}]}),
    ]
    assert sn.first("sys_user", "user_name=admin") == {"sys_id": "1"}
    assert route.call_count == 2


@respx.mock
def test_a_sleeping_instance_gets_a_clear_message(sn: ServiceNow) -> None:
    respx.get(f"{BASE}/api/now/table/sys_user").mock(
        return_value=httpx.Response(
            200, text="<html>Your instance is hibernating</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(InstanceAsleepError, match=r"developer\.servicenow\.com"):
        sn.query("sys_user")


@respx.mock
def test_errors_carry_servicenows_message(sn: ServiceNow) -> None:
    respx.post(f"{BASE}/api/now/table/incident").mock(
        return_value=httpx.Response(403, json={"error": {"message": "Operation Failed", "detail": "ACL"}})
    )
    with pytest.raises(ServiceNowError, match=r"403.*Operation Failed") as error:
        sn.create("incident", {"short_description": "x"})
    assert error.value.status == 403


@respx.mock
def test_instance_info_names_the_installed_build(sn: ServiceNow) -> None:
    respx.get(f"{BASE}/api/now/table/sys_user").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": "1", "name": "System Administrator"}]})
    )
    properties = respx.get(f"{BASE}/api/now/table/sys_properties")
    properties.mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"name": "com.glide.embedded_help.version", "value": "brazil"},
                    {"name": "glide.war", "value": "glide-brazil-08-12-2026__patch0.zip"},
                ]
            },
        )
    )
    assert sn.instance_info() == {"user": "System Administrator", "build": "glide-brazil-08-12-2026__patch0"}

    properties.mock(return_value=httpx.Response(200, json={"result": []}))
    assert sn.instance_info()["build"] == "unknown"


@respx.mock
def test_wrong_password(sn: ServiceNow) -> None:
    respx.get(f"{BASE}/api/now/table/sys_user").mock(return_value=httpx.Response(401, json={"error": {}}))
    with pytest.raises(ServiceNowError, match="refused the user name or password"):
        sn.query("sys_user")


@respx.mock
def test_create_sends_servicenow_values(sn: ServiceNow) -> None:
    route = respx.post(f"{BASE}/api/now/table/sys_user_group").mock(
        return_value=httpx.Response(201, json={"result": {"sys_id": "abc"}})
    )
    assert (
        sn.create("sys_user_group", {"name": "Service Desk", "active": True, "order": 100})["sys_id"] == "abc"
    )
    assert route.calls[0].request.content == b'{"name":"Service Desk","active":"true","order":"100"}'


@respx.mock
def test_get_returns_none_when_missing(sn: ServiceNow) -> None:
    respx.get(f"{BASE}/api/now/table/incident/nope").mock(
        return_value=httpx.Response(404, json={"error": {}})
    )
    assert sn.get("incident", "nope") is None


@respx.mock
def test_order_uses_the_service_catalog_api(sn: ServiceNow) -> None:
    route = respx.post(f"{BASE}/api/sn_sc/servicecatalog/items/item1/order_now").mock(
        return_value=httpx.Response(
            200, json={"result": {"request_id": "req1", "request_number": "REQ0010001"}}
        )
    )
    request = sn.order("item1", {"share": "Finance", "user": "u1"}, requested_for="u1")
    assert request == {"sys_id": "req1", "number": "REQ0010001"}
    body = route.calls[0].request.read()
    assert b'"sysparm_requested_for":"u1"' in body
    assert b'"variables":{"share":"Finance","user":"u1"}' in body
