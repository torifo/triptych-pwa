"""Collector: thin FastAPI proxy for triptych.

Stateless aside from the SQLite response cache — see specs/mvp-core/design.md.
Serves the built frontend (`dist/`) alongside `/api/*` for a same-origin setup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import errors
from .dto import HealthResponse

BACKEND_DIR = Path(__file__).resolve().parent
DIST_DIR = BACKEND_DIR.parent / "dist"

TOKEN_ENV_VAR = "TRIPTYCH_TOKEN"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def get_auth_token() -> Optional[str]:
    """Reads the bearer token requirement from the environment at call time.

    Read lazily (not at import time) so tests can set/unset the env var
    per-case without reloading the module.
    """
    token = os.environ.get(TOKEN_ENV_VAR)
    return token or None


def create_app() -> FastAPI:
    from .api import anilist, catalog, kakuyomu, narou, ndl, openbd, search, syoboi

    app = FastAPI(title="triptych collector")
    for module in (anilist, narou, openbd, ndl, syoboi, kakuyomu, search, catalog):
        app.include_router(module.router)

    @app.middleware("http")
    async def bearer_auth_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = get_auth_token()
        if token and request.url.path.startswith("/api"):
            header = request.headers.get("authorization", "")
            expected = f"Bearer {token}"
            if header != expected:
                return JSONResponse(
                    status_code=401, content={"detail": "unauthorized"}
                )
        return await call_next(request)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        # Routers record failures via `errors.record_adapter_error`; read the
        # module attribute at request time rather than binding it at import.
        return HealthResponse(status="ok", errors=dict(errors.last_adapter_errors))

    if DIST_DIR.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(DIST_DIR), html=True), name="frontend"
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("TRIPTYCH_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TRIPTYCH_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
