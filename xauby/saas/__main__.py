from __future__ import annotations

import argparse
import os

import uvicorn

from xauby.saas.app import create_app
from xauby.saas.settings import SaaSSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the xAuby SaaS control plane")
    parser.add_argument("--host", default=os.environ.get("XAUBY_CONTROL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("XAUBY_CONTROL_PORT", "8790")))
    args = parser.parse_args(argv)
    settings = SaaSSettings.from_env()
    uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
