# [<img src="https://s3.internetdata.io/internetdata-public/brand/mark.svg" alt="InternetData" height="28"/>](https://internetdata.io/) InternetData FastAPI Middleware

[![PyPI](https://img.shields.io/pypi/v/internetdata-fastapi.svg)](https://pypi.org/project/internetdata-fastapi/)
[![license](https://img.shields.io/pypi/l/internetdata-fastapi.svg)](LICENSE)

The official [FastAPI](https://fastapi.tiangolo.com) and [Starlette](https://www.starlette.io) middleware for the [InternetData](https://internetdata.io) API.

It looks up the visitor behind each request, where they are, which network they come from and whether it's a VPN, a proxy, Tor or a hosting server, and hands the answer to your endpoints. Blocking is opt-in.

## Getting Started

```bash
pip install internetdata-fastapi
```

Requires Python 3.11 or newer.

Without an API key a lookup is on Unlimited, the free plan: the country, the network and the company behind the address, and the Tor, relay and anycast flags, with no daily or monthly cap. Every lookup your app makes comes from your server's one address, though, so a busy site will see bursts refused. A paid plan answers every field, VPN, the proxy flags, hosting, CDN, bot, carrier, satellite, the service flags and the city among them; create a key in the [console](https://app.internetdata.io). A free account tries every field.

```python
from fastapi import FastAPI
from internetdata_fastapi import InternetDataMiddleware

app = FastAPI()
app.add_middleware(InternetDataMiddleware, api_key=os.environ.get("INTERNETDATA_API_KEY"))
```

Pure ASGI, so this works on Starlette and Litestar too, not only FastAPI.

```python
from fastapi import Request
from internetdata_fastapi import lookup


@app.get("/")
async def index(request: Request):
    found = lookup(request)
    result = found.result if found else None
    return {"country": result.location.country if result else None}
```

By default nothing is blocked. Every request gets an answer and your own code decides what it means: a currency to show, a sign-up to review, a page to keep from some countries. A request your `skip` claims gets no answer, and `lookup(request)` is `None` for it.

## Blocking

Pass a `block_condition` and a matching request is answered with `403` and never reaches your endpoints.

```python
app.add_middleware(
    InternetDataMiddleware,
    api_key=os.environ.get("INTERNETDATA_API_KEY"),
    block_condition={"location": {"country": ["CU", "IR", "KP"]}},
)
```

A condition is written in the shape of a result, keyed by the same names the API uses, and only the members you name are considered, so it can refuse on anything in the answer. The country works on every plan, and so does the first of these:

```python
{"is_anycast": True}  # anycast space
{"asn": {"type": "hosting"}}  # the network's type
[{"is_bot": True}, {"is_satellite": True}]  # crawlers, monitors and scanners; satellite space
[{"is_hosting": True}, {"is_cdn": True}]  # traffic from servers, not people
[{"is_vpn": True}, {"is_tor": True}, {"is_resproxy": True}]  # anonymity: a list is OR
{"is_vpn": True, "vpn": {"provider": "nordvpn"}}  # one provider
{"resproxy": {"hits": {"gte": 5}}}  # a numeric threshold
```

Values are matched by equality, strings without regard to case. A list means any-of. A member the answer holds as a list, such as `web`'s rows or `pubproxy`'s `protocols`, matches when any one element does: `{"web": {"port": 443}}`. A dict of `gte`/`gt`/`lte`/`lt` compares numbers, and every bound you give must hold, so two of them are a range. Members set to `False` or `None` are ignored, so a condition states the signals you act on; one that constrains nothing would match every request, and is refused when the middleware is built rather than silently blocking all your traffic.

Replace the refusal with `on_blocked`, which takes the request and the answer and returns a response, or awaits one:

```python
app.add_middleware(
    InternetDataMiddleware,
    api_key=os.environ.get("INTERNETDATA_API_KEY"),
    block_condition={"location": {"country": ["CU", "IR", "KP"]}},
    on_blocked=lambda request, found: JSONResponse({"error": "not available"}, status_code=451),
)
```

### Letting verified crawlers through

`allow_bots=True` sends each request's own `User-Agent` header with its lookup, and a request whose answer verifies that crawler, `lookup(request).result.user_agent.verdict == "verified"`, is never refused, by `block_condition` or by `block_if`; the answer is still attached. A request with no `User-Agent` sends none. It is off by default, which keeps one cached answer per address rather than one per agent.

```python
app.add_middleware(
    InternetDataMiddleware,
    api_key=os.environ.get("INTERNETDATA_API_KEY"),
    block_condition={"is_hosting": True},
    allow_bots=True,
)
```

With it on, a condition can name the verdict, refusing a forged crawler and a script: `{"user_agent": {"verdict": ["spoofed", "not_bot"]}}`. Only a paid plan's answer carries `user_agent`: a keyless client, or a free key past its trial, never gets a verdict and lets no crawler through, and the middleware warns once when that happens.

### Blocking one endpoint

`block_condition` applies to every endpoint. In FastAPI, refuse a visitor on some endpoints only with the `block_if` dependency, on a path operation, a router or the app:

```python
from fastapi import Depends
from internetdata_fastapi import block_if

embargoed = block_if({"location": {"country": ["CU", "IR", "KP"]}})


@app.get("/checkout", dependencies=[Depends(embargoed)])
async def checkout(): ...
```

A refused request gets an `HTTPException`, `403` with `{"detail": "access denied"}` unless you pass `status_code` and `detail`. Declared as a parameter, the dependency hands the endpoint the answer. `block_if` takes the same condition, `fail_closed` and `on_missing_field`. It judges the answer the middleware already attached, so a visitor is looked up once however many endpoints check them, and it warns once for a member your plan doesn't include. A request `skip` claimed reaches the endpoint. The middleware still has to be added: without it, the dependency raises `RuntimeError` instead of letting everyone through.

## Where the client address comes from

This is the setting that decides whether any of the above works, and it is the one thing only you can get right.

By default the middleware uses `request.client.host`. uvicorn, and `fastapi run` with it, reads proxy headers by default, but only from a peer that `--forwarded-allow-ips` trusts, and that defaults to `127.0.0.1,::1` (or `$FORWARDED_ALLOW_IPS`). So behind a proxy on the same machine, the visitor's address arrives with no setup. Behind one anywhere else, such as nginx in its own container or a load balancer in a VPC, every visitor looks like the proxy. That address is usually a private one, which the middleware answers locally, as an address with no country: nothing is blocked, and the private-address warning below is the only sign. Name your proxy, by its address or its subnet: `--forwarded-allow-ips=172.28.5.0/28`. gunicorn's own `--forwarded-allow-ips` works the same way. Never pass `*`. It trusts every peer, so the left-most `X-Forwarded-For` entry wins, and that is whatever the visitor sent: someone who sends `X-Forwarded-For: 1.1.1.1` is looked up as `1.1.1.1`.

For an edge that writes the address into its own header, name the header:

```python
from internetdata_fastapi import header_ip_selector

app.add_middleware(
    InternetDataMiddleware,
    ip_selector=header_ip_selector("CF-Connecting-IP"),  # or True-Client-IP, or your own
)
```

`xff_ip_selector()` reads `X-Forwarded-For` directly. Be aware that the left-most entry is whatever the caller sent, because proxies append to that header, so it's only trustworthy when an edge you control overwrites it. If you know how many proxies sit in front, count from the right instead: `xff_ip_selector(1)` is the address your nearest proxy saw.

Anything else, pass your own callable. It receives the request and returns an address.

If the address resolves to a private one, the middleware says so once through the `internetdata` logger. That is expected on localhost and is the signal to fix your configuration anywhere else.

## When a lookup fails

The request is let through, and the reason is recorded on the answer's `error`. Our outage should not become yours, so a network failure, a rate limit or a rejected key all fail open. Pass `fail_closed=True` to block instead. Private addresses are answered locally and never fail, so this will not lock you out in development.

## Cost and latency

Answers are cached for an hour, so a returning visitor costs nothing, and private addresses never leave the process. A cache miss is one request to our API, bounded at 2.5 seconds by default and not retried: on a request path, failing open quickly beats holding a visitor while we try again. Both are adjustable with `timeout` and `retries`, and the cache through an `AsyncInternetData` client you build yourself and pass as `client`.

Skip what you do not care about:

```python
app.add_middleware(
    InternetDataMiddleware,
    api_key=os.environ.get("INTERNETDATA_API_KEY"),
    skip=lambda request: request.url.path.startswith("/static"),
)
```

Beyond a few million distinct visitors a day, stop calling the API per request: look addresses up locally in [our databases](https://internetdata.io/databases) instead.

## Absent is not false

Unlimited, with a key or without one, leaves out `is_vpn`, `is_hosting`, `is_cdn`, the four proxy flags, `is_bot`, `is_carrier`, `is_satellite`, the service flags (`is_ssh`, `is_smtp`, `is_web`, `is_dns_resolver` and `is_bittorrent`), the detail behind them and `bot`, and the paid members inside `location`, `asn`, `company` and `abuse`, such as `location.city` and `asn.type`. A field your plan does not include is `None`, which means "not in your plan" rather than "checked, and no". The exception, until the next major of `internetdata`, is `is_bot`, `is_carrier`, `is_satellite`, the service flags and `asn.type`: they keep their plain types, `bool` and `str`, and read `False` and `""` when the answer leaves them out, so `result.raw` is how you tell a member the answer carried from one it did not.

```python
lookup(request).result.flagged("is_vpn")  # False rather than None on a plan without it
```

A `block_condition` naming a member your plan does not serve, `location.city` on Unlimited included, can never match, so the middleware warns once instead of failing silently. Pass `on_missing_field="raise"` to make it an error.

## Other Libraries

There are official InternetData client libraries available for many languages including PHP, Python, Go, Java, and Ruby. See our GitHub at https://github.com/internetdata for more.

## About InternetData

Geolocation, anonymity, ownership, network, service and domain data for any IP address, AS or domain, by API or as databases you download.

[<picture><source media="(prefers-color-scheme: dark)" srcset="https://s3.internetdata.io/internetdata-public/brand/logo-dark.svg"><img src="https://s3.internetdata.io/internetdata-public/brand/logo.svg" alt="InternetData" height="40"/></picture>](https://internetdata.io/)

## License

This project is licensed under the [MIT License](LICENSE).
