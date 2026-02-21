# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from fastapi import HTTPException


LOGGER = logging.getLogger("tele_port")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s event=%(message)s",
    )


def is_private_or_local_ip(ip: str) -> bool:
    ip_obj = ipaddress.ip_address(ip)
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def validate_external_url(url: str, *, allow_private: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL hostname")

    if not allow_private:
        blocked_hostnames = {
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "host.docker.internal",
            "metadata.google.internal",
        }
        if hostname.lower() in blocked_hostnames:
            raise HTTPException(status_code=400, detail="Blocked host")


def resolve_and_check_hostname(hostname: str, *, allow_private: bool = False) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {exc}") from exc

    if not allow_private:
        for info in infos:
            ip = info[4][0]
            if is_private_or_local_ip(ip):
                raise HTTPException(status_code=400, detail="Resolved to private or local address")


def safe_filename(value: str) -> str:
    keep = [c if c.isalnum() or c in "._-" else "_" for c in value]
    name = "".join(keep).strip("._")
    return (name or "media_file")[:120]
