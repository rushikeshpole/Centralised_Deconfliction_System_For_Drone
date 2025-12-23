"""
Background service to clean up expired trajectories
"""
import time
import logging
from datetime import datetime, timedelta
from database import cleanup_expired_trajectories

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_cleanup_service(interval_seconds=300):
    """Run cleanup service in background"""
    logger.info("Starting trajectory cleanup service...")
    
    while True:
        try:
            cleanup_expired_trajectories()
            time.sleep(interval_seconds)
        except Exception as e:
            logger.error(f"Cleanup service error: {e}")
            time.sleep(60)  # Wait before retry

if __name__ == "__main__":
    run_cleanup_service()
