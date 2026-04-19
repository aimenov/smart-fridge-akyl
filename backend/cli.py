"""CLI entrypoint: `smart-fridge` runs the local API server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="smart-fridge", description="Smart Fridge local API + web UI")
    p.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run",),
        help="Command (default: run)",
    )
    p.add_argument("--host", default=None, help="Bind host (default: env SMART_FRIDGE_HOST or 0.0.0.0)")
    p.add_argument("--port", type=int, default=None, help="Port (default: env SMART_FRIDGE_PORT or 8765)")
    p.add_argument("--reload", action="store_true", help="Dev auto-reload")
    p.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    p.add_argument(
        "--ssl-certfile",
        default=None,
        help="PEM certificate (HTTPS — required for phone camera on LAN)",
    )
    p.add_argument("--ssl-keyfile", default=None, help="Private key for HTTPS")
    p.add_argument("--no-scheduler", action="store_true", help="Disable background jobs")
    p.add_argument(
        "--http-protocol",
        default=None,
        choices=("auto", "h11", "httptools"),
        help="Uvicorn HTTP stack (default: h11 — fewer mobile multipart issues than httptools)",
    )
    p.add_argument(
        "--dev-https",
        action="store_true",
        help=(
            "Serve HTTPS using (or creating) data/certs/dev.pem + dev.key "
            '(requires openssl in PATH). Use https://<ip>:port/ — matches TLS to the browser.'
        ),
    )

    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]

    if args.dev_https:
        from backend.app.dev_tls import ensure_dev_tls_pair

        cert = repo_root / "data" / "certs" / "dev.pem"
        key = repo_root / "data" / "certs" / "dev.key"
        try:
            ensure_dev_tls_pair(cert, key)
        except RuntimeError as exc:
            print(f"smart-fridge: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        os.environ["SMART_FRIDGE_SSL_CERTFILE"] = str(cert.resolve())
        os.environ["SMART_FRIDGE_SSL_KEYFILE"] = str(key.resolve())

    if args.host is not None:
        os.environ["SMART_FRIDGE_HOST"] = args.host
    if args.port is not None:
        os.environ["SMART_FRIDGE_PORT"] = str(args.port)
    if args.reload:
        os.environ["SMART_FRIDGE_RELOAD"] = "true"
    if args.log_level:
        os.environ["SMART_FRIDGE_LOG_LEVEL"] = args.log_level
    if args.ssl_certfile:
        os.environ["SMART_FRIDGE_SSL_CERTFILE"] = args.ssl_certfile
    if args.ssl_keyfile:
        os.environ["SMART_FRIDGE_SSL_KEYFILE"] = args.ssl_keyfile
    if args.no_scheduler:
        os.environ["SMART_FRIDGE_SCHEDULER_ENABLED"] = "false"
    if args.http_protocol is not None:
        os.environ["SMART_FRIDGE_HTTP_PROTOCOL"] = args.http_protocol

    # Import after env so Settings picks up overrides
    from backend.app.config import settings

    import uvicorn

    ssl_cert = settings.ssl_certfile
    ssl_key = settings.ssl_keyfile
    ssl_kw = {}
    if ssl_cert and ssl_key:
        ssl_kw["ssl_certfile"] = str(ssl_cert)
        ssl_kw["ssl_keyfile"] = str(ssl_key)
        print(
            "smart-fridge: TLS enabled — open https://<this-PC-LAN-address>:"
            f"{settings.port}/ on your phone (not https://0.0.0.0). Accept the cert warning once.",
            file=sys.stderr,
        )
    elif ssl_cert or ssl_key:
        print(
            "smart-fridge: warning — provide both --ssl-certfile and --ssl-keyfile for HTTPS",
            file=sys.stderr,
        )
    else:
        from backend.app.dev_tls import print_plain_http_warning

        print_plain_http_warning(settings.port)

    scheme = "https" if ssl_kw else "http"
    print(
        f"smart-fridge: binding {scheme}://{settings.host}:{settings.port}/ "
        f"(reload={settings.reload}, scheduler={settings.scheduler_enabled}, "
        f"http={settings.http_protocol})",
        file=sys.stderr,
    )

    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
        http=settings.http_protocol,
        **ssl_kw,
    )


if __name__ == "__main__":
    main()
