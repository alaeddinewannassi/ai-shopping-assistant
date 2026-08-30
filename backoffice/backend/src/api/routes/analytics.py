"""Analytics endpoints (T505) — thin wrappers over src/analytics/queries.py (T403/T404).

No timeseries/breakdowns/cost endpoints — those need either rollup tables (Phase 4,
deferred) or real LLM cost data (Phase 3, deferred); see admin-api.yaml's contract note.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.analytics.queries import FunnelMetrics, OverviewMetrics, get_funnel, get_overview
from src.auth.dependencies import VIEW_ANALYTICS, get_db, require_tenant_role

router = APIRouter(prefix="/tenants/{tenant_id}/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewMetrics)
def overview(
    tenant_id: uuid.UUID,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*VIEW_ANALYTICS)),
) -> OverviewMetrics:
    return get_overview(db, tenant_id, start, end)


@router.get("/funnel", response_model=FunnelMetrics)
def funnel(
    tenant_id: uuid.UUID,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*VIEW_ANALYTICS)),
) -> FunnelMetrics:
    return get_funnel(db, tenant_id, start, end)
