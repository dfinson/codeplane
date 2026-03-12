"""Approval resolution endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["approvals"])


# GET /api/jobs/{job_id}/approvals — List approvals for a job
# POST /api/approvals/{approval_id}/resolve — Approve or reject
