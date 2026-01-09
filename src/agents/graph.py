"""
LangGraph assembly + analyze_stock wrapper.
No optional Finnhub settings.
"""
import os
from dotenv import load_dotenv
load_dotenv()
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

from src.agents.nodes import (
    performance_analyst_node,
    market_expert_node,
    report_generator_node,
    critic_node
)
from src.memory.semantic_cache import SemanticCache

try:
    from langchain_aws import BedrockEmbeddings
except ImportError:
    BedrockEmbeddings = None


class AgentState(MessagesState):
    ticker: str
    predictions: str
    news_sentiment: str
    final_report: str
    recommendation: str
    confidence: str


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("perf", performance_analyst_node)
    g.add_node("news", market_expert_node)
    g.add_node("report", report_generator_node)
    g.add_node("critic", critic_node)

    g.set_entry_point("perf")
    g.add_edge("perf", "news")
    g.add_edge("news", "report")
    g.add_edge("report", "critic")
    g.add_edge("critic", END)

    return g.compile(checkpointer=MemorySaver())


def analyze_stock(ticker: str, thread_id: str = None):
    # ---------------------------------------------------------
    # 1. SEMANTIC CACHE CHECK (Qdrant)
    # ---------------------------------------------------------
    ticker_upper = ticker.upper()
    
    # Initialize Embedding Model
    # Initialize Embedding Model
    # Initialize Embedding Model
    embedder = None
    if BedrockEmbeddings:
        try:
            embedder = BedrockEmbeddings(
                model_id="amazon.titan-embed-text-v1",
                region_name=os.getenv("AWS_REGION"),
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
            )
        except Exception:
            pass

    # Initialize Memory
    query_vec = None
    if embedder:
        try:
            # Relies on QDRANT_HOST env var or defaults to 'qdrant'
            mem = SemanticCache(collection_name="dataset_cache")
            
            # Create query embedding
            query_text = f"Analysis report for {ticker_upper}"
            query_vec = embedder.embed_query(query_text)
            
            # Search (fetch more to sort by time)
            hits = mem.recall(query_vec, ticker=ticker_upper, limit=5)
            
            # Filter for high score
            valid_hits = [h for h in hits if h.score > 0.95]
            
            if valid_hits:
                # Sort by created_at_ts descending to get newest
                valid_hits.sort(key=lambda x: x.payload.get("created_at_ts", 0), reverse=True)
                best_hit = valid_hits[0]
                
                # Cache HIT
                cached_payload = best_hit.payload
                # Check if it's for the same ticker to be safe (semantic search might be fuzzy)
                if cached_payload.get("ticker") == ticker_upper:
                    print(f"✅ Semantic Cache HIT for {ticker_upper}")
                    return {
                        "final_report": cached_payload.get("summary"),
                        "recommendation": cached_payload.get("recommendation", "Neutral"),
                        "confidence": cached_payload.get("confidence", "Medium"),
                        "last_price": cached_payload.get("last_price", 0.0),
                        "predictions": cached_payload.get("predictions", {})
                    }
        except Exception as e:
            err_str = str(e)
            if "429" in err_str:
                print(f"⚠️ Semantic Cache Skipped: Quota Exceeded (AWS Bedrock)")
            else:
                print(f"⚠️ Semantic Cache Error: {e}")

    # ---------------------------------------------------------
    # 2. FETCH DATA & RUN AGENT
    # ---------------------------------------------------------
    
    # FIRST attempt prediction directly
    from src.agents.tools import fetch_prediction_data
    
    # Use the new fetcher
    raw_data = fetch_prediction_data(ticker)

    # ---- FIX: MODEL TRAINING CASE ----
    if raw_data == "__MODEL_TRAINING__":
        return {
            "status": "training",
            "detail": f"Model for {ticker} is being trained. Retry after a few seconds.",
            "ticker": ticker_upper
        }
    
    # Check for error string
    if isinstance(raw_data, str):
        # This means an error occurred (e.g. 500 or 404 text)
        return {
            "status": "error",
            "detail": raw_data,
            "ticker": ticker_upper
        }

    # Format for Agent (String)
    try:
        forecast = (
            raw_data.get("result", {})
            .get("predictions", {})
            .get("full_forecast", [])
        )
        if not forecast:
            pred_str = f"No predictions available for {ticker}"
        else:
            lines = [f"7-Day Price Forecast for {ticker}:"]
            for row in forecast[:7]:
                price = float(row.get("close", 0))
                lines.append(f"  {row['date']}: ${price:.2f}")
            pred_str = "\n".join(lines)
    except Exception as e:
        pred_str = f"Prediction parsing failed: {e}"

    # Continue only if predictions available
    graph = build_graph()

    state = {
        "ticker": ticker_upper,
        "messages": [HumanMessage(content=f"Start analysis {ticker}")],
        "predictions": pred_str 
    }

    config = {"configurable": {"thread_id": thread_id or "1"}}
    
    # Invoke the graph
    result = graph.invoke(state, config=config)
    
    # Inject RAW data for frontend (so it has history)
    if isinstance(raw_data, dict):
        result["predictions"] = raw_data.get("result", {}).get("predictions", {})

    # ---------------------------------------------------------
    # 3. SAVE TO CACHE
    # ---------------------------------------------------------
    if embedder and "final_report" in result and query_vec:
        try:
            # Extract metadata
            rec = result.get("recommendation", "Neutral")
            conf = result.get("confidence", "Medium")
            
            # Extract last_price from predictions dict
            last_price = 0.0
            preds_data = result.get("predictions")
            if isinstance(preds_data, dict):
                hist = preds_data.get("historical", [])
                if hist:
                    last_price = float(hist[-1].get("close", 0))
                else:
                    fc = preds_data.get("full_forecast", [])
                    if fc:
                        last_price = float(fc[0].get("close", 0))

            mem.save_episode(
                ticker=ticker_upper,
                summary=result["final_report"],
                embedding=query_vec, # Reuse the query vector as the key
                recommendation=rec,
                confidence=conf,
                last_price=last_price,
                predictions=preds_data if isinstance(preds_data, dict) else {}
            )
            print(f"✅ Saved to Qdrant: {ticker_upper}")
        except Exception as e:
            print(f"⚠️ Failed to save cache: {e}")
    
    return result
