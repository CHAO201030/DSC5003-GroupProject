"""ASGI entry point.

Run with the DSC5003 environment active:

    uvicorn services.gateway.main:app --host 0.0.0.0 --port 8000

or use ``python -m services.gateway.main``.
"""

from __future__ import annotations

import logging

import uvicorn

from shared.config.settings import get_settings

from .app import create_app

app = create_app()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    uvicorn.run(
        "services.gateway.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
