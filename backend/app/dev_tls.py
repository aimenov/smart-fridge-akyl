"""Create a throwaway TLS certificate for LAN HTTPS (phone camera needs a secure context)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def _openssl_exe() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    if sys.platform == "win32":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates = [
            base / "Git" / "usr" / "bin" / "openssl.exe",
            base / "OpenSSL-Win64" / "bin" / "openssl.exe",
            base / "OpenSSL" / "bin" / "openssl.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def _guess_lan_ipv4() -> str:
    """Pick a plausible LAN address for SAN (browser warning still expected for self-signed)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if isinstance(ip, str) and ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    return "127.0.0.1"


def ensure_dev_tls_pair(cert_path: Path, key_path: Path) -> None:
    """
    Ensure PEM cert + key exist; create with openssl if missing.

    Requires ``openssl`` on PATH (Git for Windows / OpenSSL builds).
    """
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    if cert_path.is_file() and key_path.is_file():
        print(f"smart-fridge: dev TLS — using existing\n  {cert_path}\n  {key_path}", file=sys.stderr)
        return

    openssl = _openssl_exe()
    if not openssl:
        raise RuntimeError(
            "openssl not found. On Windows install Git for Windows (includes openssl) or add "
            "OpenSSL to PATH, then retry --dev-https. Or pass your own PEM files via "
            "--ssl-certfile / --ssl-keyfile."
        )

    lan = _guess_lan_ipv4()
    subject = f"/CN=smart-fridge-{lan}"
    # OpenSSL 1.1.1+ -addext for SAN so phones can pin to LAN IP after accepting prompt.
    cmd = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        "3650",
        "-nodes",
        "-subj",
        subject,
        "-addext",
        f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{lan}",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "openssl disappeared between discovery and exec; retry with a full path to openssl"
        ) from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"openssl failed to create dev certificate: {err}") from e

    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    print(
        f"smart-fridge: dev TLS — created certificate (SAN includes localhost, 127.0.0.1, {lan})",
        file=sys.stderr,
    )


def print_plain_http_warning(port: int) -> None:
    print(
        "\n*** smart-fridge is serving plain HTTP (no TLS).\n"
        f"    Open: http://<this-pc-ip>:{port}/\n"
        "    Do NOT use https:// — the browser will send TLS bytes and uvicorn will log "
        '"Invalid HTTP request received."\n'
        f"    For HTTPS from a phone (camera): restart with --dev-https or pass "
        "--ssl-certfile / --ssl-keyfile.\n",
        file=sys.stderr,
    )
