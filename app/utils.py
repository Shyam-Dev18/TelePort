# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import unicodedata
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
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    # Keep only cross-platform safe characters.
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_value)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    if not cleaned:
        cleaned = "media_file"
    # Avoid reserved Windows device names and dot-only names.
    reserved = {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }
    if cleaned.lower() in reserved:
        cleaned = f"file_{cleaned}"
    return cleaned[:120]
