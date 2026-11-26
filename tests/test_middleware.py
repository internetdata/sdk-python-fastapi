"""The adapter, through a real ASGI app, plus the shared corpus.

A test client connects from ``testclient``/``127.0.0.1``, which is a bogon and is
answered locally without a request. Anything that needs a served answer therefore has
to arrive wearing a public address, through a selector.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from internetdata import AsyncInternetData
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from internetdata_fastapi import (
    InternetDataMiddleware,
    block_if,
    header_ip_selector,
    lookup,
    xff_ip_selector,
)

CORPUS = json.loads(
    (pathlib.Path(__file__).parent.parent / "testdata/testdata.json").read_text()
)
MIDDLEWARE = CORPUS["middleware"]
# Served answers from the lookup corpus: Unlimited's, and a paid plan's.
ANSWERS: dict[str, dict[str, Any]] = {case["name"]: case["body"] for case in CORPUS["lookup"]}
UNLIMITED = ANSWERS["unlimited-answer"]
PAID = ANSWERS["paid-full-shape"]
ELSEWHERE = {**UNLIMITED, "location": {**UNLIMITED["location"], "country": "FR"}}
PUBLIC_IP = PAID["ip"]
EMBARGO = {"location": {"country": ["DE", "AT"]}}


def serving(body: dict[str, Any], *, status: int = 200) -> AsyncInternetData:
    """A client whose every answer is ``body`` for the address asked about, recording
    those addresses."""

    async def handle(request: httpx.Request) -> httpx.Response:
        ip = request.url.path.lstrip("/")
        asked.append(ip)
        return httpx.Response(status, json={**body, "ip": ip})

    asked: list[str] = []
    client = AsyncInternetData(cache=False, retries=0, transport=httpx.MockTransport(handle))
    client.asked = asked  # type: ignore[attr-defined]
    return client


async def index(request: Request) -> JSONResponse:
    found = lookup(request)
    result = found.result if found else None
    return JSONResponse(
        {
            "ip": found.ip if found else None,
            "country": result.location.country if result else None,
            "is_vpn": result.is_vpn if result else None,
            "is_bogon": result.is_bogon if result else None,
            "error": type(found.error).__name__ if found and found.error else None,
            "attached": found is not None,
        }
    )


def app_with(**options: Any) -> Starlette:
    app = Starlette(routes=[Route("/", index)])
    app.add_middleware(InternetDataMiddleware, **options)
    return app


def get(app: Starlette, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    # A real peer address, because TestClient's default is the literal string
    # "testclient" - which is not an address at all, so it is not a bogon and would be
    # sent to the API as if it were one.
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/", headers=headers or {})
    return response.status_code, response.json()


def fixed_ip(_request: Any) -> str:
    return PUBLIC_IP


def test_enriches_the_request_and_leaves_the_decision_to_the_app() -> None:
    client = serving(UNLIMITED)
    status, body = get(app_with(client=client, ip_selector=fixed_ip))
    assert status == 200
    assert body["attached"] is True and body["country"] == "AT" and body["ip"] == PUBLIC_IP
    assert body["is_vpn"] is None, "Unlimited leaves is_vpn out, and absent is not false"
    assert client.asked == [PUBLIC_IP]


def test_blocks_on_a_country_and_the_endpoint_never_runs() -> None:
    status, body = get(
        app_with(client=serving(UNLIMITED), ip_selector=fixed_ip, block_condition=EMBARGO)
    )
    assert status == 403
    assert body == {"error": "access denied"}

    status, _ = get(
        app_with(client=serving(ELSEWHERE), ip_selector=fixed_ip, block_condition=EMBARGO)
    )
    assert status == 200


def test_blocks_on_a_network_type_and_on_anonymity() -> None:
    status, _ = get(
        app_with(
            client=serving(UNLIMITED),
            ip_selector=fixed_ip,
            block_condition={"asn": {"type": "hosting"}},
        )
    )
    assert status == 403, "the network's type is served on every plan"

    status, _ = get(
        app_with(
            client=serving(PAID),
            ip_selector=fixed_ip,
            block_condition=[{"is_tor": True}, {"is_vpn": True}],
        )
    )
    assert status == 403


def test_a_condition_reaches_the_evidence_fields() -> None:
    status, _ = get(
        app_with(
            client=serving(PAID),
            ip_selector=fixed_ip,
            block_condition={"vpn": {"provider": "nordvpn"}},
        )
    )
    assert status == 200, "a different provider must not match"

    status, _ = get(
        app_with(
            client=serving(PAID),
            ip_selector=fixed_ip,
            block_condition={"vpn": {"provider": "MULLVAD"}},
        )
    )
    assert status == 403, "a provider must compare without case"


def test_on_blocked_replaces_the_refusal() -> None:
    status, body = get(
        app_with(
            client=serving(UNLIMITED),
            ip_selector=fixed_ip,
            block_condition=EMBARGO,
            on_blocked=lambda request, found: JSONResponse(
                {"why": found.result.location.country}, status_code=451
            ),
        )
    )
    assert status == 451
    assert body == {"why": "AT"}


def test_skip_leaves_the_request_untouched() -> None:
    client = serving(UNLIMITED)
    status, body = get(
        app_with(
            client=client,
            ip_selector=fixed_ip,
            block_condition=EMBARGO,
            skip=lambda request: request.url.path == "/",
        )
    )
    assert status == 200 and body["attached"] is False
    assert client.asked == []


def test_a_failing_lookup_lets_the_visitor_through() -> None:
    failing = serving({"error": "boom"}, status=500)
    status, body = get(app_with(client=failing, ip_selector=fixed_ip, block_condition=EMBARGO))
    assert status == 200
    assert body["error"] == "InternetDataError"


# The test that matters. Every other assertion here would pass whether or not the
# selector is right, because a direct connection has nothing to confuse.
def test_a_forged_x_forwarded_for_is_ignored_by_default() -> None:
    client = serving(UNLIMITED)
    _, body = get(app_with(client=client), {"X-Forwarded-For": PUBLIC_IP})
    assert body["ip"] == "127.0.0.1", "client.host is the peer; the header is a forgery"
    assert client.asked == [], "and a bogon is answered locally, so nothing was asked"

    explicit = serving(UNLIMITED)
    _, body = get(
        app_with(client=explicit, ip_selector=xff_ip_selector()), {"X-Forwarded-For": PUBLIC_IP}
    )
    assert body["ip"] == PUBLIC_IP
    assert explicit.asked == [PUBLIC_IP]


def test_a_header_selector_reads_the_edge_that_writes_it() -> None:
    client = serving(UNLIMITED)
    _, body = get(
        app_with(client=client, ip_selector=header_ip_selector("CF-Connecting-IP")),
        {"CF-Connecting-IP": "45.83.91.9"},
    )
    assert body["ip"] == "45.83.91.9"
    assert client.asked == ["45.83.91.9"]


def test_depth_counts_trusted_hops_from_the_right() -> None:
    client = serving(UNLIMITED)
    get(
        app_with(client=client, ip_selector=xff_ip_selector(1)),
        {"X-Forwarded-For": f"{PUBLIC_IP}, 70.41.3.18, 150.172.238.178"},
    )
    assert client.asked == ["150.172.238.178"]


def test_a_private_client_address_is_answered_locally_and_never_blocks() -> None:
    client = serving(UNLIMITED)
    status, body = get(
        app_with(
            client=client,
            block_condition={"asn": {"type": "hosting"}},
            ip_selector=lambda request: "10.0.0.7",
        )
    )
    assert status == 200, "local development must not lock you out of your own app"
    assert body["is_bogon"] is True
    assert client.asked == []


def test_a_condition_that_constrains_nothing_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="constrains nothing"):
        InternetDataMiddleware(
            lambda scope, receive, send: None, block_condition={"is_vpn": False}
        )


def test_a_sync_client_is_refused_by_the_async_middleware() -> None:
    from internetdata import InternetData as SyncClient

    with pytest.raises(TypeError, match="use Core"):
        InternetDataMiddleware(lambda scope, receive, send: None, client=SyncClient())


@pytest.mark.parametrize("case", MIDDLEWARE["conditions"], ids=lambda c: c["name"])
def test_corpus_conditions(case: dict[str, Any]) -> None:
    ip = case.get("bogon") or case["body"]["ip"]
    client = serving(case.get("body") or {})
    warnings: list[str] = []
    status, _ = get(
        app_with(
            client=client,
            ip_selector=lambda _request, ip=ip: ip,
            block_condition=case["condition"],
            on_warn=warnings.append,
        )
    )
    assert status == (403 if case["expect"]["blocked"] else 200), case["why"]
    reported = [w for w in warnings if "does not include" in w]
    assert len(reported) == (1 if case["expect"]["missing"] else 0), case["why"]
    for member in case["expect"]["missing"]:
        assert member in reported[0], case["why"]


def fastapi_with(guard: dict[str, Any] | None = None, **options: Any) -> FastAPI:
    """A FastAPI app with the middleware, whose ``/guarded`` depends on
    ``block_if(**guard)`` and ``/`` on nothing."""
    app = FastAPI()
    # A default rather than Annotated: under postponed annotations FastAPI resolves an
    # annotation in module globals, where this per-app dependency does not live.
    check = block_if(**(guard or {"condition": EMBARGO}))
    app.add_api_route("/", index)

    @app.get("/guarded")
    async def guarded(found: Any = Depends(check)) -> dict[str, Any]:  # noqa: B008
        return {"attached": found is not None, "ip": found.ip if found else None}

    app.add_middleware(InternetDataMiddleware, **options)
    return app


def fetch(app: Any, url: str) -> tuple[int, dict[str, Any]]:
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get(url)
    return response.status_code, response.json()


def test_block_if_refuses_only_the_endpoint_that_depends_on_it() -> None:
    client = serving(UNLIMITED)
    app = fastapi_with(client=client, ip_selector=fixed_ip)
    status, body = fetch(app, "/guarded")
    assert status == 403
    assert body == {"detail": "access denied"}
    status, body = fetch(app, "/")
    assert status == 200, "the middleware itself has no condition, so other endpoints are open"
    assert body["country"] == "AT"
    assert client.asked == [PUBLIC_IP, PUBLIC_IP], (
        "one lookup per request, none by the dependency"
    )

    status, body = fetch(
        fastapi_with(client=serving(ELSEWHERE), ip_selector=fixed_ip), "/guarded"
    )
    assert status == 200
    assert body == {"attached": True, "ip": PUBLIC_IP}, "the dependency hands over the answer"


def test_block_if_takes_its_status_and_detail() -> None:
    guard = {"condition": EMBARGO, "status_code": 451, "detail": "not in your country"}
    status, body = fetch(
        fastapi_with(guard, client=serving(UNLIMITED), ip_selector=fixed_ip), "/guarded"
    )
    assert status == 451
    assert body == {"detail": "not in your country"}


def test_block_if_lets_a_skipped_request_through() -> None:
    client = serving(UNLIMITED)
    app = fastapi_with(client=client, ip_selector=fixed_ip, skip=lambda request: True)
    status, body = fetch(app, "/guarded")
    assert status == 200
    assert body == {"attached": False, "ip": None}
    assert client.asked == []


def test_block_if_fails_open_unless_told_to_fail_closed() -> None:
    failing = serving({"error": "boom"}, status=500)
    status, _ = fetch(fastapi_with(client=failing, ip_selector=fixed_ip), "/guarded")
    assert status == 200

    guard = {"condition": EMBARGO, "fail_closed": True}
    status, body = fetch(fastapi_with(guard, client=failing, ip_selector=fixed_ip), "/guarded")
    assert status == 403
    assert body == {"detail": "access denied"}


def test_block_if_without_the_middleware_is_an_error() -> None:
    app = FastAPI()
    check = block_if(EMBARGO)

    @app.get("/guarded")
    async def guarded(found: Any = Depends(check)) -> None:  # noqa: B008
        return None

    with pytest.raises(RuntimeError, match="add InternetDataMiddleware"):
        fetch(app, "/guarded")


def test_block_if_refuses_a_condition_that_constrains_nothing() -> None:
    with pytest.raises(ValueError, match="constrains nothing"):
        block_if({"is_vpn": False})


def test_block_if_warns_once() -> None:
    warnings: list[str] = []
    guard = {"condition": {"location": {"city": "Vienna"}}, "on_warn": warnings.append}
    app = fastapi_with(guard, client=serving(UNLIMITED), ip_selector=fixed_ip)
    fetch(app, "/guarded")
    fetch(app, "/guarded")
    assert len(warnings) == 1
    assert warnings[0].startswith("block_if names location.city")


@pytest.mark.parametrize("case", MIDDLEWARE["conditions"], ids=lambda c: c["name"])
def test_corpus_conditions_through_block_if(case: dict[str, Any]) -> None:
    ip = case.get("bogon") or case["body"]["ip"]
    client = serving(case.get("body") or {})
    warnings: list[str] = []
    app = fastapi_with(
        {"condition": case["condition"], "on_warn": warnings.append},
        client=client,
        ip_selector=lambda _request, ip=ip: ip,
    )
    status, _ = fetch(app, "/guarded")
    assert status == (403 if case["expect"]["blocked"] else 200), case["why"]
    reported = [w for w in warnings if "does not include" in w]
    assert len(reported) == (1 if case["expect"]["missing"] else 0), case["why"]
    for member in case["expect"]["missing"]:
        assert member in reported[0], case["why"]


# Built exactly as the README builds it: the key from the environment and nothing else.
# Every other test hands the middleware a client, so none of them could see a core that
# cannot start from a key alone. The test client's loopback peer is a bogon, answered
# with no request.
@pytest.mark.parametrize("key", ["a-key", None])
def test_the_middleware_starts_the_way_the_readme_builds_it(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    if key is None:
        monkeypatch.delenv("INTERNETDATA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("INTERNETDATA_API_KEY", key)
    status, body = get(app_with(api_key=os.environ.get("INTERNETDATA_API_KEY")))

    assert status == 200
    assert body["attached"] is True
    assert body["is_bogon"] is True
    assert body["error"] is None


# The README's middleware again, served a PUBLIC peer with a forged X-Forwarded-For: the
# key must reach the API when it is set and no Authorization go out when it is not, and
# the socket address must be the one looked up. The test above is answered with no
# request, so it passed with the key dropped. httpx's own transport answers for the API's
# host alone, so nothing leaves the machine.
@pytest.mark.parametrize("key", ["a-key", None])
def test_the_readme_middleware_sends_its_key_for_a_public_peer(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    sent: list[tuple[str, str | None]] = []

    async def handle(
        _transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        if request.url.host != "api.internetdata.io":
            raise httpx.ConnectError("only the API's host answers here", request=request)
        sent.append((request.url.path, request.headers.get("Authorization")))
        return httpx.Response(200, json={**UNLIMITED, "ip": PUBLIC_IP})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    if key is None:
        monkeypatch.delenv("INTERNETDATA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("INTERNETDATA_API_KEY", key)
    app = app_with(api_key=os.environ.get("INTERNETDATA_API_KEY"))
    with TestClient(app, client=(PUBLIC_IP, 12345)) as client:
        response = client.get("/", headers={"X-Forwarded-For": "1.1.1.1"})
    status, body = response.status_code, response.json()

    assert status == 200
    assert body["ip"] == PUBLIC_IP, "the socket peer is looked up; the header is a forgery"
    assert body["error"] is None and body["country"] == "AT"
    assert len(sent) == 1 and sent[0][0].endswith(PUBLIC_IP)
    assert sent[0][1] == (f"Bearer {key}" if key else None)


# The bot flag is a paid answer's: the README's middleware with a condition on it, served a PUBLIC
# peer, refuses a crawler's address and lets a person's through with no warning, and warns once
# that an Unlimited answer, which lacks the flag, can never match.
@pytest.mark.parametrize("paid", [True, False], ids=["a paid answer", "an Unlimited answer"])
def test_a_condition_on_the_bot_flag_refuses_a_bot_from_a_public_peer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, paid: bool
) -> None:
    bot = ANSWERS["paid-bot"]
    served = (
        {bot["ip"]: bot, PUBLIC_IP: PAID}
        if paid
        else {ip: {**UNLIMITED, "ip": ip} for ip in (bot["ip"], PUBLIC_IP)}
    )
    sent: list[str] = []

    async def handle(
        _transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        if request.url.host != "api.internetdata.io":
            raise httpx.ConnectError("only the API's host answers here", request=request)
        ip = request.url.path.lstrip("/")
        sent.append(ip)
        return httpx.Response(200, json=served[ip])

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setenv("INTERNETDATA_API_KEY", "a-key")
    statuses = []
    for peer in (bot["ip"], PUBLIC_IP):
        app = app_with(
            api_key=os.environ.get("INTERNETDATA_API_KEY"), block_condition={"is_bot": True}
        )
        with TestClient(app, client=(peer, 12345)) as client:
            statuses.append(client.get("/").status_code)

    assert statuses == ([403, 200] if paid else [200, 200])
    assert sent == [bot["ip"], PUBLIC_IP]
    warned = [r for r in caplog.records if "names is_bot" in r.getMessage()]
    assert bool(warned) is not paid, "only an answer without the flag is a plan gap"


GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def api_answering(
    monkeypatch: pytest.MonkeyPatch, verdicts: dict[str, str] | None
) -> list[str | None]:
    """The API's host, answering every lookup as a match for ``{"is_hosting": True}`` and,
    for a sent agent, with the verdict ``verdicts`` holds for it (None: no verdict, as
    Unlimited answers). Returns the ``user_agent`` each lookup sent."""
    sent: list[str | None] = []

    async def handle(
        _transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        if request.url.host != "api.internetdata.io":
            raise httpx.ConnectError("only the API's host answers here", request=request)
        agent = request.url.params.get("user_agent")
        sent.append(agent)
        body: dict[str, Any] = {**PAID, "ip": request.url.path.lstrip("/"), "is_hosting": True}
        if agent and verdicts is not None:
            verdict = verdicts.get(agent, "not_bot")
            body["user_agent"] = {"agent": "Googlebot", "kind": "bot", "verdict": verdict}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setenv("INTERNETDATA_API_KEY", "a-key")
    return sent


def visit(app: Any, *requests: tuple[str | None, str, str]) -> list[int]:
    """Each (User-Agent, path, peer) through one app, answering each status."""
    statuses: list[int] = []
    for user_agent, url, peer in requests:
        with TestClient(app, client=(peer, 12345)) as client:
            # The test client sends a User-Agent of its own unless told otherwise.
            client.headers.pop("User-Agent", None)
            headers = {} if user_agent is None else {"User-Agent": user_agent}
            statuses.append(client.get(url, headers=headers).status_code)
    return statuses


def readme_app(**options: Any) -> FastAPI:
    """The README's build, plus ``options``, with ``/guarded`` depending on
    ``block_if({"is_hosting": True})``."""
    return fastapi_with(
        {"condition": {"is_hosting": True}},
        api_key=os.environ["INTERNETDATA_API_KEY"],
        **options,
    )


def test_allow_bots_sends_the_requests_own_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = api_answering(monkeypatch, {GOOGLEBOT: "verified"})

    visit(
        readme_app(allow_bots=True),
        (GOOGLEBOT, "/", PUBLIC_IP),
        ("", "/", "45.83.91.2"),
        (None, "/", "45.83.91.3"),
    )

    assert sent == [GOOGLEBOT, None, None], "an empty or absent header sent an agent"


def test_without_allow_bots_no_user_agent_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = api_answering(monkeypatch, {GOOGLEBOT: "verified"})

    statuses = visit(
        readme_app(block_condition={"is_hosting": True}), (GOOGLEBOT, "/", PUBLIC_IP)
    )

    assert sent == [None]
    assert statuses == [403]


@pytest.mark.parametrize(
    ("verdict", "status"), [("verified", 200), ("spoofed", 403), ("not_bot", 403)]
)
@pytest.mark.parametrize("url", ["/", "/guarded"])
def test_a_verified_crawler_passes_a_condition_it_meets_and_no_other_does(
    monkeypatch: pytest.MonkeyPatch, url: str, verdict: str, status: int
) -> None:
    api_answering(monkeypatch, {GOOGLEBOT: verdict})
    options: dict[str, Any] = {"allow_bots": True}
    if url == "/":
        options["block_condition"] = {"is_hosting": True}

    assert visit(readme_app(**options), (GOOGLEBOT, url, PUBLIC_IP)) == [status]


def test_a_condition_naming_the_verdict_refuses_a_forged_crawler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_answering(monkeypatch, {GOOGLEBOT: "spoofed"})
    app = readme_app(allow_bots=True, block_condition={"user_agent": {"verdict": "spoofed"}})

    assert visit(app, (GOOGLEBOT, "/", PUBLIC_IP)) == [403]


def test_an_answer_without_a_verdict_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = api_answering(monkeypatch, None)
    warnings: list[str] = []
    app = readme_app(allow_bots=True, on_warn=warnings.append)

    visit(app, (GOOGLEBOT, "/", PUBLIC_IP), (GOOGLEBOT, "/", "45.83.91.2"))

    assert sent == [GOOGLEBOT, GOOGLEBOT]
    assert len([w for w in warnings if "user_agent verdict" in w]) == 1, warnings
