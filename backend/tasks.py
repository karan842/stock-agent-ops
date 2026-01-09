import json
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, Optional
import psutil

from backend.state import (
    redis_client, executor, 
    TRAINING_DURATION, TRAINING_STATUS, TRAINING_MSE, 
    SYSTEM_CPU, SYSTEM_RAM, SYSTEM_DISK, REDIS_KEYS,
    CACHE_HIT, CACHE_MISS
)
from logger.logger import get_logger

logger = get_logger()

# =========================================================
# Helper Functions
# =========================================================

def refresh_system_metrics():
    SYSTEM_CPU.set(psutil.cpu_percent())
    SYSTEM_RAM.set(psutil.virtual_memory().used / (1024**2))
    SYSTEM_DISK.set(psutil.disk_usage('/').used / (1024**2))
    if redis_client:
        try:
            REDIS_KEYS.set(redis_client.dbsize())
        except:
            pass

def get_or_set_cache(key: str, compute_fn, expire: int = 86400):
    """Helper to check Redis cache or compute and cache."""
    refresh_system_metrics()
    try:
        if redis_client:
            val = redis_client.get(key)
            if val:
                CACHE_HIT.labels(key).inc()
                return json.loads(val), True
        
        result = compute_fn()
        
        if redis_client:
            redis_client.set(key, json.dumps(result), ex=expire)
            CACHE_MISS.labels(key).inc()
        return result, False
    except Exception as e:
        logger.error(f"Redis cache error: {e}")
        return compute_fn(), False

# =========================================================
# Task Status Tracking (Redis)
# =========================================================
def get_task_key(task_id: str) -> str:
    return f"task_status:{task_id.lower()}"

def save_task_status(task_id: str, status_data: Dict[str, Any], ttl: int = 3600):
    """Save task status to Redis with TTL."""
    try:
        if redis_client:
            redis_client.set(get_task_key(task_id), json.dumps(status_data), ex=ttl)
    except Exception as e:
        logger.error(f"Failed to save task status for {task_id}: {e}")

def get_task_status_redis(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task status from Redis."""
    try:
        if redis_client:
            val = redis_client.get(get_task_key(task_id))
            if val:
                return json.loads(val)
    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {e}")
    return None

async def run_training_worker(task_id: str, fn, *args, chain_fn=None):
    """Actual training worker (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    start_time = time.time()
    try:
        result = await loop.run_in_executor(executor, fn, *args)

        if chain_fn:
            logger.info(f"Task {task_id}: Training complete, running chained task...")
            # Run the chained function (e.g., prediction & caching)
            await loop.run_in_executor(executor, chain_fn)
            logger.info(f"Task {task_id}: Chained task complete.")

        duration = time.time() - start_time
        TRAINING_DURATION.labels(task_id).observe(duration)
        
        status_data = {"status": "completed", "result": result, "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        save_task_status(task_id, status_data, ttl=3600) # Keep completed status for 1 hour
        
        TRAINING_STATUS.labels(task_id).set(2)
        
        # Update metrics if MSE is available in result
        if isinstance(result, dict) and "mse" in result:
             TRAINING_MSE.set(result["mse"])
    except Exception as e:
        status_data = {"status": "failed", "error": str(e), "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        save_task_status(task_id, status_data, ttl=3600)
        
        TRAINING_STATUS.labels(task_id).set(0)
        logger.error(f"Training failed: {e}")

async def run_blocking_fn(fn, *args):
    """Run a blocking function in the thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, fn, *args)

async def run_training(task_id: str, fn, *args, chain_fn=None):
    """Start training in background and return immediately."""
    task_id = task_id.lower()
    
    # Check if already running or completed using Redis
    current_status = get_task_status_redis(task_id)
    if current_status and current_status.get("status") in ["running", "completed"]:
        return
        
    # Set initial status
    status_data = {"status": "running", "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_task_status(task_id, status_data, ttl=7200) # 2 hours max run time assumption
    
    TRAINING_STATUS.labels(task_id).set(1)
    
    # Spawn background task
    asyncio.create_task(run_training_worker(task_id, fn, *args, chain_fn=chain_fn))
