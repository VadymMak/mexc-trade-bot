# app/routers/metrics.py
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

router = APIRouter(tags=["metrics"])

@router.get("/metrics")
def metrics() -> Response:
    """
    Expose Prometheus metrics collected in the default registry.
    Works with all counters/gauges/histograms defined in app.infra.metrics.
    """
    data = generate_latest(REGISTRY)
    return Response(data, media_type=CONTENT_TYPE_LATEST)
