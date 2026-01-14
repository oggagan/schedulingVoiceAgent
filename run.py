#!/usr/bin/env python3
"""
Entry point for running the Voice Scheduling Agent
"""

import uvicorn
from app.config import settings


def main():
    """Run the application with uvicorn"""
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
        access_log=False  # We handle logging ourselves
    )


if __name__ == "__main__":
    main()
