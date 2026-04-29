"""Auto-shutdown after period of inactivity."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Idle timeout in seconds (default: 30 minutes)
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT_SECONDS", 3600))
CHECK_INTERVAL = 60  # Check every minute

_last_activity = datetime.now(timezone.utc)


def record_activity():
    """Call this whenever there's user activity."""
    global _last_activity
    _last_activity = datetime.now(timezone.utc)


def get_idle_seconds() -> int:
    """Get seconds since last activity."""
    return int((datetime.now(timezone.utc) - _last_activity).total_seconds())


async def idle_monitor_task():
    """Background task to monitor idle time and shutdown if needed."""
    if not os.getenv("ENABLE_IDLE_SHUTDOWN"):
        logger.info("Idle shutdown disabled")
        return

    logger.info(f"Idle monitor started: shutdown after {IDLE_TIMEOUT}s of inactivity")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        idle = get_idle_seconds()

        if idle > IDLE_TIMEOUT:
            logger.warning(f"Idle for {idle}s, triggering shutdown...")
            # In Kubernetes, exiting will cause pod restart
            # But combined with scale-to-zero, this is clean shutdown
            import sys
            sys.exit(0)
        elif idle > IDLE_TIMEOUT * 0.8:
            logger.info(f"Approaching idle timeout: {idle}s / {IDLE_TIMEOUT}s")
