# Changelog

What each release changed for you, newest first. Each line is a commit's summary, linked to its full description and diff.

## 1.0.4 - 2026-11-20

### Features

- Require internetdata 2.16.0: a tier, standing, license type, format or outcome the API adds reads as served ([`152f365`](https://github.com/internetdata/sdk-python-fastapi/commit/152f365bebf301afe0977f050e7bd8296711b1af))

## 1.0.3 - 2026-11-19

### Fixes

- Require internetdata 2.15.2: a reduced answer is cached a minute at most ([`f64bc97`](https://github.com/internetdata/sdk-python-fastapi/commit/f64bc979d198fa6e7705a5b9fe94f7d3b2faedb9))

## 1.0.2 - 2026-11-19

### Fixes

- Require internetdata 2.15.1: cap_reached on a paid answer past its overage cap ([`36d9659`](https://github.com/internetdata/sdk-python-fastapi/commit/36d9659c67ea3193e223618051966bd1089e38e0))

## 1.0.1 - 2026-11-14

### Fixes

- Require internetdata 2.12.1: a 403 can mean the key lacks the ip.lookup scope ([`287aecf`](https://github.com/internetdata/sdk-python-fastapi/commit/287aecf8bc8ce5046731b31ba9b08f76f0058e72))

## 1.0.0 - 2026-11-14

### Features

- Attach the visitor's InternetData record to every request, read with `lookup(request)`, as pure ASGI middleware for FastAPI, Starlette and Litestar: the location, the network and the company behind the address, and the anonymity and hosting flags your plan serves ([`68f2916`](https://github.com/internetdata/sdk-python-fastapi/commit/68f2916e9dc33f397747eec3375d08b2f4911f33))
- Refuse a request with a `block_condition` written in the shape of a result, by country, network type, anonymity or any other member, with 403 or an `on_blocked` of your own ([`68f2916`](https://github.com/internetdata/sdk-python-fastapi/commit/68f2916e9dc33f397747eec3375d08b2f4911f33))
- Refuse one FastAPI endpoint, router or app with the `block_if` dependency, judging the answer the middleware already attached ([`68f2916`](https://github.com/internetdata/sdk-python-fastapi/commit/68f2916e9dc33f397747eec3375d08b2f4911f33))
- Choose where the client address comes from: `request.client.host` by default, `xff_ip_selector`, `header_ip_selector` for a header your edge writes, or a callable of your own ([`68f2916`](https://github.com/internetdata/sdk-python-fastapi/commit/68f2916e9dc33f397747eec3375d08b2f4911f33))
- Fail open on any lookup failure unless `fail_closed` is set, with a 2.5 s bound, no retry and a cache by default ([`68f2916`](https://github.com/internetdata/sdk-python-fastapi/commit/68f2916e9dc33f397747eec3375d08b2f4911f33))
