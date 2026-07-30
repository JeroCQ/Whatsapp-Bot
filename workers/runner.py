"""Compatibility entry point for Railway's ``python -m workers.runner`` command."""

import os

import uvicorn


def main() -> None:
    """Start the FastAPI application on Railway's assigned port."""
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
