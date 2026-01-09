import os
import time
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from backend.state import redis_client, PREDICTION_COUNTER, PREDICTION_LATENCY
from backend.schemas import AnalyzeRequest
from backend.tasks import (
    run_training, run_blocking_fn, save_task_status, 
    get_task_status_redis, get_or_set_cache, refresh_system_metrics
)
from backend.rate_limiter import rate_limit
from logger.logger import get_logger

# Import from src (untouhed as requested)
from src.pipelines.training_pipeline import train_parent, train_child
from src.pipelines.inference_pipeline import predict_parent, predict_child
from src.agents.graph import analyze_stock
from src.monitoring.drift import check_drift
from src.monitoring.agent_eval import AgentEvaluator
from src.config import Config
from src.exception import PipelineError

logger = get_logger()
router = APIRouter()
BASE_PATH = "outputs"
cfg = Config()

def check_model_exists(ticker: str, model_type: str = "child") -> bool:
    """Check if model file exists on disk."""
    if model_type == "parent":
        path = os.path.join(cfg.parent_dir, f"{cfg.parent_ticker}_parent_model.pt")
    else:
        path = os.path.join(cfg.workdir, ticker.upper(), f"{ticker.upper()}_child_model.pt")
    return os.path.exists(path)

# =========================================================
# Core Routes
# =========================================================
@router.get("/")
def root():
    """Project information and available commands."""
    return {
        "project": "MLOps Stock Prediction Pipeline",
        "version": "3.1",
        "description": "Production-ready MLOps system for stock price prediction using LSTM and Transfer Learning",
        "features": [
            "Parent-Child Transfer Learning Strategy",
            "Real-time predictions with Redis caching",
            "Feast feature store integration",
            "MLflow experiment tracking",
            "Qdrant semantic memory for AI agents",
            "Prometheus monitoring & Grafana dashboards",
            "Auto-healing: Missing models trigger background training"
        ],
        "endpoints": {
            "health": "GET /health - Health check",
            "docs": "GET /docs - Interactive API documentation",
            "training": {
                "train_parent": "POST /train-parent - Train parent model (S&P 500)",
                "train_child": "POST /train-child - Train child model for specific ticker"
            },
            "prediction": {
                "predict_parent": "POST /predict-parent - Predict using parent model",
                "predict_child": "POST /predict-child - Predict using child model (auto-trains if missing)"
            },
            "monitoring": {
                "status": "GET /status/{task_id} - Check training task status",
                "monitor_parent": "POST /monitor/parent - Monitor parent model drift & agent eval",
                "monitor_ticker": "POST /monitor/{ticker} - Monitor specific ticker",
                "drift_report": "GET /monitor/{ticker}/drift - Get drift analysis JSON",
                "eval_report": "GET /monitor/{ticker}/eval - Get agent evaluation JSON"
            },
            "system": {
                "outputs": "GET /outputs - List all files in outputs directory",
                "cache": "GET /system/cache - Inspect Redis cache",
                "logs": "GET /system/logs - Retrieve latest log lines",
                "reset": "DELETE /system/reset - Wipe all system data (Redis, Qdrant, Feast, Outputs)",
                "metrics": "GET /metrics - Prometheus metrics"
            },
            "agent": {
                "analyze": "POST /analyze - Analyze stock with AI agent"
            }
        },
        "quick_start": {
            "1_train_parent": "curl -X POST http://localhost:8000/train-parent",
            "2_predict_child": "curl -X POST http://localhost:8000/predict-child -H 'Content-Type: application/json' -d '{\"ticker\": \"AAPL\"}'",
            "3_check_status": "curl -X GET http://localhost:8000/status/aapl",
            "4_view_outputs": "curl -X GET http://localhost:8000/outputs"
        },
        "documentation": "See /docs for full interactive API documentation"
    }

@router.get("/health")
def health():
    return {"status": "healthy"}

@router.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.ticker:
        raise HTTPException(400, "Ticker required")

    try:
        return analyze_stock(req.ticker, thread_id=req.thread_id)
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(500, "Internal Server Error: Analysis failed")

# =========================================================
# Training Endpoints
# =========================================================
@router.post("/train-parent")
@rate_limit(limit=5, window_sec=3600, key_prefix="train_parent")
async def train_parent_endpoint():
    """Trigger parent model training."""
    try:
        task_id = "parent_training"
        
        # Check if Parent Model already exists
        if check_model_exists("parent", "parent"):
            return {"status": "completed", "task_id": task_id, "detail": "Parent model already exists"}

        if get_task_status_redis(task_id) and get_task_status_redis(task_id).get("status") == "running":
             return {"status": "already running", "task_id": task_id}
             
        await run_training(task_id, train_parent)
        return {"status": "started", "task_id": task_id}
    except Exception as e:
        logger.error(f"Train Parent failed: {e}", exc_info=True)
        raise HTTPException(500, "Internal Server Error: Training initiation failed")

@router.post("/train-child")
@rate_limit(limit=5, window_sec=3600, key_prefix="train_child")
async def train_child_endpoint(request: Request):
    """Trigger child model training."""
    try:
        data = await request.json()
        ticker = data.get("ticker", "").strip().upper()
        if not ticker:
            raise HTTPException(400, "ticker is required")
            
        task_id = ticker.lower()
        
        # Check if Parent Model exists
        cfg = Config()
        parent_path = os.path.join(cfg.parent_dir, f"{cfg.parent_ticker}_parent_model.pt")
        
        if not os.path.exists(parent_path):
            logger.warning("Parent model missing. Triggering parent training first.")
            parent_status = get_task_status_redis("parent_training")
            if not parent_status or parent_status.get("status") != "completed":
                 await run_training("parent_training", train_parent)
                 parent_status = get_task_status_redis("parent_training")
                 if parent_status and parent_status.get("status") == "running":
                     return {"status": "started_parent", "task_id": "parent_training", "detail": "Parent model missing. Training parent first."}
        
        # Check if Child Model already exists
        if check_model_exists(ticker, "child"):
            return {"status": "completed", "task_id": task_id, "detail": "Model already exists"}

        # Check if THIS task is running
        curr_status = get_task_status_redis(task_id)
        if curr_status and curr_status.get("status") == "running":
            return {"status": "running", "task_id": task_id, "detail": "Training already in progress for this ticker"}

        # GLOBAL CHECK: Is ANY child model training?
        if redis_client:
            # Scan for all task keys
            keys = redis_client.keys("task_status:*")
            for k in keys:
                key_str = k.decode('utf-8')
                tid = key_str.split(":", 1)[1]
                
                # Skip parent training
                if tid == "parent_training":
                    continue
                
                # Check if its running
                val = redis_client.get(k)
                if val:
                    s = json.loads(val)
                    if s.get("status") == "running":
                        # Found another child training
                        return {
                            "status": "busy", 
                            "task_id": task_id, 
                            "blocking_task": tid,
                            "detail": f"System is currently training '{tid.upper()}'. Please wait."
                        }

        def chain_predict():
            # Chain prediction and caching after training
            logger.info(f"Auto-predicting for {ticker} after training...")
            get_or_set_cache(f"predict_child_{ticker.lower()}", lambda: predict_child(ticker), expire=86400)

        await run_training(task_id, train_child, ticker, chain_fn=chain_predict)
        return {"status": "started", "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Train Child failed: {e}", exc_info=True)
        raise HTTPException(500, "Internal Server Error: Training initiation failed")

# =========================================================
# Prediction Endpoints
# =========================================================
@router.post("/predict-parent")
@rate_limit(limit=40, window_sec=3600, key_prefix="predict_parent")
async def predict_parent_endpoint():
    """Get parent model predictions."""
    PREDICTION_COUNTER.labels(type="parent").inc()
    start_time = time.time()
    try:
        result = await run_blocking_fn(predict_parent)
        PREDICTION_LATENCY.labels(type="parent").observe(time.time() - start_time)
        return {"result": result}
    except Exception as e:
        logger.error(f"Predict Parent failed: {e}", exc_info=True)
        raise HTTPException(500, "Internal Server Error")

@router.post("/predict-child")
@rate_limit(limit=40, window_sec=3600, key_prefix="predict_child")
async def predict_child_endpoint(request: Request, response: Response):
    """Get child model predictions."""
    data = await request.json()
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        raise HTTPException(400, "ticker is required")
        
    task_id = ticker.lower()
    PREDICTION_COUNTER.labels(type="child").inc()

    start_time = time.time()
    try:
        def get_preds():
            return get_or_set_cache(f"predict_child_{ticker.lower()}", lambda: predict_child(ticker), expire=86400)
            
        preds, _ = await run_blocking_fn(get_preds)
        PREDICTION_LATENCY.labels(type="child").observe(time.time() - start_time)
        return {"result": preds}
    except (FileNotFoundError, PipelineError) as e:
        if "Missing" in str(e) or "not found" in str(e):
            logger.info(f"Model missing for {ticker}, triggering auto-training.")
            
            # Check if Parent Model exists
            if not check_model_exists("parent", "parent"):
                logger.warning("Parent model missing. Triggering parent training first.")
                parent_status = get_task_status_redis("parent_training")
                if not parent_status or parent_status.get("status") != "completed":
                    await run_training("parent_training", train_parent)
                    response.status_code = 202
                    return {"status": "training", "detail": "Parent model missing. Training parent first.", "task_id": "parent_training"}

            status = get_task_status_redis(task_id)
            if status and status.get("status") == "running":
                 response.status_code = 202
                 return {"status": "training", "detail": "Training in progress. Please retry later.", "task_id": task_id}
            
            def chain_predict():
                # Chain prediction and caching after training
                logger.info(f"Auto-predicting for {ticker} after auto-training...")
                get_or_set_cache(f"predict_child_{ticker.lower()}", lambda: predict_child(ticker), expire=86400)

            await run_training(task_id, train_child, ticker, chain_fn=chain_predict)
            response.status_code = 202
            return {"status": "training", "detail": f"Model for {ticker} missing. Training started (with auto-prediction).", "task_id": task_id}
            
        raise HTTPException(500, "Internal Server Error")
    except Exception as e:
        logger.error(f"Predict Child failed: {e}", exc_info=True)
        raise HTTPException(500, "Internal Server Error")

# =========================================================
# System / Monitoring Endpoints
# =========================================================
@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """Check status of background training task (Async)."""
    tid = task_id.lower()
    if tid == "parent":
        tid = "parent_training"
        
    status = get_task_status_redis(tid)
    
    # Check disk as fallback or confirmation
    # For parents, tid is 'parent_training', but we want to check '^GSPC'
    # For children, tid is 'aapl'
    ticker_for_disk = "parent" if tid == "parent_training" else tid.upper()
    model_type = "parent" if tid == "parent_training" else "child"
    file_exists = check_model_exists(ticker_for_disk, model_type)

    if not status:
        if file_exists:
             return {"status": "completed", "detail": "Model file found on disk", "task_id": task_id}
        raise HTTPException(404, f"Task '{task_id}' not found.")
    
    # If status is not 'completed' or 'failed' but file exists, 
    # it might be a race condition or a previous run. 
    # But if it's 'running', we should probably stick to 'running'.
    # If status is not 'running' or 'failed' but file exists (e.g. status expired or some intermediate state), 
    # we can assume 'completed'. But do NOT override 'failed'.
    if status.get("status") not in ["running", "failed"] and file_exists:
        status["status"] = "completed"
    
    response = status.copy()
    if response.get("status") == "running" and "start_time" in response:
        try:
            start_dt = datetime.strptime(response["start_time"], "%Y-%m-%d %H:%M:%S")
            response["elapsed_seconds"] = int((datetime.now() - start_dt).total_seconds())
        except Exception:
            pass
    
    response.pop("start_time", None)
    return response

@router.get("/system/logs")
async def get_logs(lines: int = 100):
    """Retrieve the latest log lines."""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        return {"logs": "Log directory not found."}
    
    files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
    if not files:
        return {"logs": "No log files found."}
    
    latest_file = sorted(files)[-1]
    path = os.path.join(log_dir, latest_file)
    
    try:
        with open(path, "r") as f:
            content = f.readlines()
            last_lines = content[-lines:]
            return {"logs": "".join(last_lines), "filename": latest_file}
    except Exception as e:
        return {"error": f"Failed to read logs: {e}"}

@router.post("/monitor/parent")
async def monitor_parent():
    """Monitor ONLY the Parent Model (^GSPC)."""
    cfg = Config()
    ticker = cfg.parent_ticker
    
    try:
        drift_res = check_drift(ticker, BASE_PATH)
    except Exception as e:
        drift_res = {"status": "failed", "error": str(e)}

    try:
        evaluator = AgentEvaluator(BASE_PATH)
        eval_res = evaluator.evaluate_live(ticker)
    except Exception as e:
        eval_res = {"status": "failed", "error": str(e)}

    return {
        "ticker": ticker,
        "type": "Parent Model (Market Index)",
        "drift": drift_res,
        "agent_eval": eval_res,
        "links": {
            "get_drift_json": f"/monitor/{ticker}/drift",
            "get_eval_json": f"/monitor/{ticker}/eval"
        }
    }

@router.post("/monitor/{ticker}")
async def trigger_monitoring(ticker: str):
    """Trigger live monitoring for a ticker."""
    cfg = Config()
    clean_ticker = ticker.strip().upper()
    is_parent = (clean_ticker == cfg.parent_ticker)
    
    drift_res = {"status": "skipped", "detail": "Drift calculation reserved for parent model."}
    if is_parent:
        try:
            drift_res = check_drift(clean_ticker, BASE_PATH)
        except Exception as e:
            drift_res = {"status": "failed", "error": str(e)}
        
    try:
        evaluator = AgentEvaluator(BASE_PATH)
        eval_res = evaluator.evaluate_live(clean_ticker)
    except Exception as e:
        eval_res = {"status": "failed", "error": str(e)}
            
    return {
        "ticker": clean_ticker,
        "is_parent": is_parent,
        "drift": drift_res,
        "agent_eval": eval_res
    }

@router.get("/monitor/{ticker}/drift")
def get_drift_result(ticker: str):
    t = ticker.lower()
    drift_dir = os.path.join(BASE_PATH, t, "drift")
    if not os.path.exists(drift_dir):
        raise HTTPException(404, "No drift report found")
    json_path = os.path.join(drift_dir, "latest_drift.json")
    if os.path.exists(json_path):
        with open(json_path, "r") as f:
            return json.load(f)
    files = os.listdir(drift_dir)
    return {"files": files, "message": "Access HTML report in outputs/", "detail": "JSON summary missing."}

@router.get("/monitor/{ticker}/eval")
def get_eval_result(ticker: str):
    t = ticker.lower()
    path = os.path.join(BASE_PATH, t, "agent_eval", "latest_eval.json")
    if not os.path.exists(path):
        raise HTTPException(404, "No evaluation found. Run POST /monitor/{ticker} first.")
    with open(path, "r") as f:
        return json.load(f)

@router.delete("/system/reset")
def reset_system():
    """Wipe all system data and reset for a fresh start."""
    results = {}
    try:
        # 1. Reset Redis
        if redis_client:
            try:
                redis_client.flushall()
                results["redis"] = "✅ Flushed"
                logger.info("✅ Redis flushed")
            except Exception as e:
                results["redis"] = f"❌ Failed: {e}"
        else:
            results["redis"] = "Skipped (Not connected)"
            
        # 2. Reset Qdrant (Semantic Cache)
        try:
            from src.memory.semantic_cache import SemanticCache
            # Initialize with default env logic
            mem = SemanticCache() 
            collections = [c.name for c in mem.client.get_collections().collections]
            if mem.collection_name in collections:
                mem.client.delete_collection(mem.collection_name)
            mem._ensure_collection() # Re-create empty
            results["qdrant"] = "✅ Collection wiped and recreated"
            logger.info("✅ Qdrant wiped")
        except Exception as e:
            results["qdrant"] = f"❌ Failed: {e}"

        # 3. Reset Feast Registry & Data
        try:
            repo_path = os.path.join(os.getcwd(), "feature_store")
            files_to_remove = [
                os.path.join(repo_path, "data", "registry.db"),
                os.path.join(repo_path, "data", "features.parquet"),
                os.path.join(repo_path, "registry.db"), # Fallback location
                os.path.join(repo_path, "online_store.db") # Sometimes created locally
            ]
            removed_feast = []
            for p in files_to_remove:
                 if os.path.exists(p):
                     os.remove(p)
                     removed_feast.append(os.path.basename(p))
            results["feast"] = f"✅ Removed: {', '.join(removed_feast)}" if removed_feast else "✅ Nothing to remove"
            logger.info(f"✅ Feast cleanup: {removed_feast}")
        except Exception as e:
            results["feast"] = f"❌ Failed: {e}"

        # 4. Wipe Outputs (Models, Plots, etc)
        try:
            import shutil
            if os.path.exists(BASE_PATH):
                # Cannot remove the root dir itself if it is a mount (Device or resource busy).
                # Instead, remove all contents inside it.
                for item in os.listdir(BASE_PATH):
                    item_path = os.path.join(BASE_PATH, item)
                    try:
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    except Exception as e:
                        logger.error(f"Failed to delete {item_path}: {e}")
                
                results["outputs"] = "✅ Wiped all files in outputs directory"
                logger.info(f"✅ Cleared outputs directory: {BASE_PATH}")
            else:
                os.makedirs(BASE_PATH, exist_ok=True)
                results["outputs"] = "✅ Created missing outputs directory"
        except Exception as e:
            results["outputs"] = f"❌ Failed: {e}"

        return {
            "status": "System Reset Complete",
            "timestamp": datetime.now().isoformat(),
            "details": results
        }
    except Exception as e:
        logger.error(f"Global reset failed: {e}")
        raise HTTPException(500, f"Critical reset failure: {e}")

@router.get("/system/cache")
def inspect_cache(ticker: Optional[str] = None):
    if not redis_client:
        raise HTTPException(503, "Redis not connected")
    pattern = "predict_child_*"
    try:
        keys = [k.decode("utf-8") for k in redis_client.keys(pattern)]
    except Exception as e:
         logger.error(f"Redis scan failed: {e}")
         return {"error": str(e)}

    cached_map = {k.replace("predict_child_", "").upper(): k for k in keys}

    if not ticker:
        return {
            "cached_tickers": list(cached_map.keys()),
            "count": len(cached_map),
        }
    
    target_ticker = ticker.strip().upper()
    target_key = cached_map.get(target_ticker)
    
    if not target_key:
        raise HTTPException(404, f"No cache found for {target_ticker}")

    val = redis_client.get(target_key)
    return json.loads(val)

@router.get("/outputs")
def list_outputs():
    """List all files and directories in the outputs folder."""
    if not os.path.exists(BASE_PATH):
        return {"error": "Outputs directory not found", "path": BASE_PATH}
    
    def scan_directory(path, relative_to):
        """Recursively scan directory and return structure."""
        items = []
        try:
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)
                rel_path = os.path.relpath(full_path, relative_to)
                
                if os.path.isdir(full_path):
                    # Count files in directory
                    try:
                        file_count = sum(len(files) for _, _, files in os.walk(full_path))
                    except:
                        file_count = 0
                    
                    items.append({
                        "name": entry,
                        "type": "directory",
                        "path": rel_path,
                        "file_count": file_count
                    })
                else:
                    # Get file size and modification time
                    try:
                        stat = os.stat(full_path)
                        size_kb = round(stat.st_size / 1024, 2)
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        size_kb = 0
                        modified = "unknown"
                    
                    items.append({
                        "name": entry,
                        "type": "file",
                        "path": rel_path,
                        "size_kb": size_kb,
                        "modified": modified
                    })
        except Exception as e:
            logger.error(f"Error scanning directory {path}: {e}")
        
        return sorted(items, key=lambda x: (x["type"] == "file", x["name"]))
    
    try:
        contents = scan_directory(BASE_PATH, BASE_PATH)
        
        # Get total size
        total_size_kb = sum(item.get("size_kb", 0) for item in contents if item["type"] == "file")
        
        return {
            "path": BASE_PATH,
            "total_items": len(contents),
            "directories": len([i for i in contents if i["type"] == "directory"]),
            "files": len([i for i in contents if i["type"] == "file"]),
            "total_size_kb": round(total_size_kb, 2),
            "contents": contents,
            "note": "Use GET /outputs/{ticker} to see detailed contents of a specific ticker directory"
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to list outputs: {e}")

@router.get("/outputs/{ticker}")
def list_ticker_outputs(ticker: str):
    """List all files for a specific ticker in the outputs folder."""
    ticker_path = os.path.join(BASE_PATH, ticker.lower())
    
    if not os.path.exists(ticker_path):
        raise HTTPException(404, f"No outputs found for ticker '{ticker}'")
    
    def scan_recursive(path, relative_to):
        """Recursively scan and return all files."""
        all_files = []
        try:
            for root, dirs, files in os.walk(path):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, relative_to)
                    
                    try:
                        stat = os.stat(full_path)
                        size_kb = round(stat.st_size / 1024, 2)
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        size_kb = 0
                        modified = "unknown"
                    
                    all_files.append({
                        "name": file,
                        "path": rel_path,
                        "size_kb": size_kb,
                        "modified": modified,
                        "category": os.path.basename(os.path.dirname(full_path))
                    })
        except Exception as e:
            logger.error(f"Error scanning {path}: {e}")
        
        return sorted(all_files, key=lambda x: x["path"])
    
    try:
        files = scan_recursive(ticker_path, ticker_path)
        total_size_kb = sum(f["size_kb"] for f in files)
        
        # Group by category
        categories = {}
        for f in files:
            cat = f["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f)
        
        return {
            "ticker": ticker.upper(),
            "path": ticker_path,
            "total_files": len(files),
            "total_size_kb": round(total_size_kb, 2),
            "categories": list(categories.keys()),
            "files_by_category": categories,
            "all_files": files
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to list ticker outputs: {e}")
