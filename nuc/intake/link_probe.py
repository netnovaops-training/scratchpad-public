#!/usr/bin/env python3
"""Header-only look at a shared link: what is it (Content-Type, size, file name) before anything is fetched.

T0889 step 3 (2026-11-22, autopilot). The link router (step 4 on) needs to know whether a link is a direct
file (pdf, docx, image, video) or a web page, without downloading it. probe(url) answers that.

SAFETY (why this does not just call httpx.head(url))
- Same rule as share._public_url: only http(s) to PUBLIC addresses (no private, loopback, link-local,
  reserved, multicast, Tailscale 100.64/10). That keeps a shared link from making the NUC fetch the house's
  own services (SSRF).
- The address is resolved ONCE and the connection goes to THAT address. share._public_url checks DNS and
  then gotenberg-web resolves again, so a host that answers "public" first and "192.168.x" second
  (DNS rebinding) could slip through; found in the T0889 step 1 read (2026-11-22). Here the TCP connection
  is made to the checked IP, with the real name in the Host header and in TLS SNI, so the certificate is
  still checked against the real name (httpx "sni_hostname" request extension).
- Redirects are followed by hand, at most MAX_HOPS, and every hop is checked again the same way.
- HEAD first; a site that refuses HEAD (405/501) gets GET with "Range: bytes=0-0" and the body is not read.
OUTCOMES: see test_link_probe.py (offline cases) and the T0889 Review line of 2026-11-22 (live links).
"""
from __future__ import annotations

import email.message
import ipaddress
import socket
import urllib.parse

import httpx

MAX_HOPS = 5
TIMEOUT = 10
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


class Refused(Exception):
    """The link (or a redirect hop) is not a public http(s) address; the message says why, in plain words."""


def _public_ip(host: str, port: int) -> str:
    """Resolve host once; return one address if EVERY answer is public, else raise Refused.

    Every answer must be public (not just the first): a name with one public and one private record would
    otherwise be a coin toss.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise Refused(f"the site name {host!r} does not resolve") from e
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or ip in _TAILSCALE):
            raise Refused("only public http(s) links are fetched")
        ips.append(str(ip))
    if not ips:
        raise Refused(f"the site name {host!r} does not resolve")
    return ips[0]


def _pinned(url: str) -> tuple[str, str, str]:
    """Return (url with the checked IP in place of the name, Host header value, TLS SNI name)."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise Refused("only public http(s) links are fetched")
    port = p.port or (443 if p.scheme == "https" else 80)
    ip = _public_ip(p.hostname, port)
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{p.port}" if p.port else "")
    host_header = p.hostname + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", p.query, "")), host_header, p.hostname


def _filename(disposition: str | None) -> str | None:
    """File name from a Content-Disposition header (handles filename*=UTF-8''...), else None."""
    if not disposition:
        return None
    m = email.message.Message()
    m["content-disposition"] = disposition
    name = m.get_filename()
    return name.replace("/", "_").replace("\\", "_") if name else None


async def probe(url: str, client: httpx.AsyncClient | None = None) -> dict:
    """Look at a link's headers only. Returns {url, status, content_type, length, filename, hops}.

    Raises Refused for a non-public address (on any hop), too many redirects, or no answer.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        for hop in range(MAX_HOPS + 1):
            target, host, sni = _pinned(url)
            ext = {"sni_hostname": sni} if url.startswith("https") else {}
            headers = {"Host": host, "User-Agent": "Mozilla/5.0 (Atomic Share link check)"}
            try:
                r = await client.head(target, headers=headers, extensions=ext)
                if r.status_code in (405, 501):
                    req = client.build_request("GET", target, headers={**headers, "Range": "bytes=0-0"},
                                               extensions=ext)
                    r = await client.send(req, stream=True)
                    await r.aclose()  # headers only; the body is never read
            except httpx.HTTPError as e:
                raise Refused(f"the site did not answer ({type(e).__name__})") from e
            if r.is_redirect and r.headers.get("location"):
                url = urllib.parse.urljoin(url, r.headers["location"])
                continue
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            length = r.headers.get("content-length")
            if r.status_code == 206 and "content-range" in r.headers:  # "bytes 0-0/12345"
                length = r.headers["content-range"].rpartition("/")[2]
            return {"url": url, "status": r.status_code, "content_type": ctype,
                    "length": int(length) if length and length.isdigit() else None,
                    "filename": _filename(r.headers.get("content-disposition")), "hops": hop}
        raise Refused(f"more than {MAX_HOPS} redirects")
    finally:
        if own:
            await client.aclose()


class TooBig(Exception):
    """The file is bigger than the limit; the download was stopped and the part file removed."""


async def fetch(url: str, dest, max_bytes: int, client: httpx.AsyncClient | None = None) -> int:
    """Download url (a link probe() already followed to its end) into dest; return the byte count.

    T0889 step 4 (2026-11-22, autopilot). Same pinning as probe(): the name is resolved and checked again
    here and the connection goes to that checked IP (a name that turned private since the probe is refused).
    A redirect at this point is refused, not followed: probe() already gave the final URL, so a new redirect
    means the site changed its answer between the two requests.
    The body is streamed in chunks and stopped at max_bytes (TooBig): Content-Length is not trusted, because
    the step 3 live test showed YouTube answering HEAD with Content-Length 0.
    """
    import pathlib
    dest = pathlib.Path(dest)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        target, host, sni = _pinned(url)
        ext = {"sni_hostname": sni} if url.startswith("https") else {}
        req = client.build_request("GET", target, extensions=ext,
                                   headers={"Host": host, "User-Agent": "Mozilla/5.0 (Atomic Share link fetch)"})
        try:
            r = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            raise Refused(f"the site did not answer ({type(e).__name__})") from e
        try:
            if r.is_redirect:
                raise Refused("the site sent a new redirect while downloading")
            if r.status_code != 200:
                raise Refused(f"the site answered {r.status_code} to the download")
            got = 0
            with dest.open("wb") as out:
                async for chunk in r.aiter_bytes(1 << 20):
                    got += len(chunk)
                    if got > max_bytes:
                        break
                    out.write(chunk)
            if got > max_bytes:
                dest.unlink(missing_ok=True)
                raise TooBig(f"the file is bigger than {max_bytes // 1048576} MB")
            return got
        except httpx.HTTPError as e:
            dest.unlink(missing_ok=True)
            raise Refused(f"the download broke off ({type(e).__name__})") from e
        finally:
            await r.aclose()
    finally:
        if own:
            await client.aclose()
