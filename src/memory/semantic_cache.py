import os
import uuid
import time
from datetime import datetime, timedelta

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    Range
)


class SemanticCache:
    """
    Semantic cache with:
    - strict ticker filtering
    - TTL-based time decay (default: 24 hours)
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        collection_name: str = "dataset_cache",
        vector_size: int = 1536,
        ttl_hours: int = 24,
    ):
        self.host = host or os.getenv("QDRANT_HOST", "qdrant")
        env_port = os.getenv("QDRANT_PORT", "6333")
        if env_port.isdigit():
            self.port = port or int(env_port)
        else:
            self.port = port or 6333 # Fallback if K8s injects a URI

        self.collection_name = collection_name
        self.vector_size = vector_size
        self.ttl_seconds = ttl_hours * 3600

        self.client = QdrantClient(host=self.host, port=self.port)
        self._ensure_collection()

    def _ensure_collection(self):
        try:
            # Check if collection exists
            collection_info = self.client.get_collection(self.collection_name)
            
            # Check dimension mismatch
            if collection_info.config.params.vectors.size != self.vector_size:
                print(f"⚠️ Dimension Mismatch (Expected: {self.vector_size}, Found: {collection_info.config.params.vectors.size}). Recreating collection...")
                self.client.delete_collection(self.collection_name)
                raise ValueError("Recreating") # Trigger creation below
                
        except Exception:
            # Create if missing or deleted
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )

    def save_episode(self, ticker: str, summary: str, embedding: list, recommendation: str, confidence: str, last_price: float, predictions: dict):
        """
        Save episode with numeric timestamp for TTL filtering.
        """
        now_ts = int(time.time())

        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={
                "ticker": ticker,
                "summary": summary,
                "recommendation": recommendation,
                "confidence": confidence,
                "last_price": last_price,
                "predictions": predictions,
                "created_at_ts": now_ts,
            },
        )

        self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )

    def recall(self, query_embedding: list, ticker: str, limit: int = 3):
        """
        Retrieve only:
        - same ticker
        - entries newer than TTL window
        """
        min_ts = int(time.time()) - self.ttl_seconds

        ticker_filter = Filter(
            must=[
                FieldCondition(
                    key="ticker",
                    match=MatchValue(value=ticker),
                ),
                FieldCondition(
                    key="created_at_ts",
                    range=Range(gte=min_ts),
                ),
            ]
        )

        result = self.client.query_points(
            collection_name=self.collection_name,
            query=query_embedding,
            limit=limit,
            with_payload=True,
            query_filter=ticker_filter,
        )

        return result.points
