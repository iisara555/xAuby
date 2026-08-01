from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from xauby.saas.app import create_app
from xauby.saas.settings import SaaSSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the xAuby SaaS control plane")
    parser.add_argument("--host", default=os.environ.get("XAUBY_CONTROL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("XAUBY_CONTROL_PORT", "8790")))
    args = parser.parse_args(argv)
    # systemd captures stdout into the journal, so a plain stream handler is
    # all that is needed for `journalctl -u xauby-control` to carry the
    # request lines and tracebacks emitted by xauby.saas.
    logging.basicConfig(
        level=os.environ.get("XAUBY_CONTROL_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = SaaSSettings.from_env()
    uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
