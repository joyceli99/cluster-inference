"""FastAPI backend: serves the latest cluster graph snapshot as JSON.

Run:  uvicorn main:app --port 8000   (from radar/backend)

A background task polls the Kubernetes API on an interval and caches the snapshot, so
the number of browsers watching never multiplies load on the cluster's API server.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from cluster_poller import ClusterPoller, namespaces_from_env


class GraphCache:
    def __init__(self, poller):
        self._poller = poller
        self.graph: dict | None = None
        self.error: str | None = None

    def refresh(self) -> None:
        try:
            self.graph = self._poller.snapshot()
            self.error = None
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"


def create_app(cache: GraphCache | None = None, interval: float | None = None) -> FastAPI:
    if cache is None:
        poller = ClusterPoller(context=os.environ.get("RADAR_KUBE_CONTEXT"), namespaces=namespaces_from_env())
        cache = GraphCache(poller)
    if interval is None:
        interval = float(os.environ.get("RADAR_POLL_INTERVAL", "2"))

    async def poll_forever() -> None:
        while True:
            await asyncio.to_thread(cache.refresh)
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(poll_forever())
        yield
        task.cancel()

    app = FastAPI(title="Cluster Radar", lifespan=lifespan)

    @app.get("/api/graph")
    def get_graph() -> dict:
        if cache.error:
            raise HTTPException(status_code=503, detail=cache.error)
        if cache.graph is None:
            raise HTTPException(status_code=503, detail="waiting for first cluster snapshot")
        return cache.graph

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "connected": cache.error is None and cache.graph is not None}

    return app


app = create_app()
