"""Uvicorn entry point for the API delivery process."""

from __future__ import annotations

import uvicorn

from apps.api.app import create_app
from apps.api.settings import ApiSettings

settings = ApiSettings.from_env()
app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
