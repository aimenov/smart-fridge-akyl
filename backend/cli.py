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
        help="PEM certificate (overrides auto dev certificate)",
    )
    p.add_argument("--ssl-keyfile", default=None, help="Private key (overrides auto dev certificate)")
    p.add_argument("--no-scheduler", action="store_true", help="Disable background jobs")
    p.add_argument(
        "--no-dev-https",
        action="store_true",
        help="Do not create or use data/certs/dev.pem — plain HTTP only (desktop / tests)",
    )

    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]

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
    if args.no_dev_https:
        os.environ["SMART_FRIDGE_DEV_HTTPS"] = "false"

    from backend.app.config import settings

    import uvicorn

    cert_path = repo_root / "data" / "certs" / "dev.pem"
    key_path = repo_root / "data" / "certs" / "dev.key"

    ssl_cert = settings.ssl_certfile
    ssl_key = settings.ssl_keyfile
    ssl_kw: dict = {}

    if ssl_cert and ssl_key:
        ssl_kw["ssl_certfile"] = str(ssl_cert)
        ssl_kw["ssl_keyfile"] = str(ssl_key)
        print(
            "smart-fridge: TLS — using configured cert files. Open https://<this-PC-LAN-IP>:"
            f"{settings.port}/ on your phone.",
            file=sys.stderr,
        )
    elif ssl_cert or ssl_key:
        print(
            "smart-fridge: warning — provide both --ssl-certfile and --ssl-keyfile (or rely on dev HTTPS).",
            file=sys.stderr,
        )
        from backend.app.dev_tls import print_plain_http_warning

        print_plain_http_warning(settings.port)
    elif settings.dev_https:
        from backend.app.dev_tls import ensure_dev_tls_pair

        try:
            ensure_dev_tls_pair(cert_path, key_path)
            ssl_kw["ssl_certfile"] = str(cert_path.resolve())
            ssl_kw["ssl_keyfile"] = str(key_path.resolve())
            print(
                "smart-fridge: TLS — dev certificate ready. Open https://<this-PC-LAN-IP>:"
                f"{settings.port}/ (accept the security warning once).",
                file=sys.stderr,
            )
        except RuntimeError as exc:
            print(f"smart-fridge: could not enable HTTPS ({exc})", file=sys.stderr)
            print(
                "smart-fridge: falling back to HTTP — use --no-dev-https to silence this, "
                "or install OpenSSL / Git for Windows.",
                file=sys.stderr,
            )
            from backend.app.dev_tls import print_plain_http_warning

            print_plain_http_warning(settings.port)
    else:
        from backend.app.dev_tls import print_plain_http_warning

        print_plain_http_warning(settings.port)

    scheme = "https" if ssl_kw else "http"
    print(
        f"smart-fridge: {scheme}://{settings.host}:{settings.port}/ "
        f"(reload={settings.reload}, scheduler={settings.scheduler_enabled})",
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
