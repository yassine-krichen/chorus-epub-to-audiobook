"""Job endpoints: upload URL, inspect, create, list, detail, cast report, cast review, cancel.

Stays thin — parses/validates the request, checks ownership where relevant,
and delegates everything else to service.py.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from book2audio.api.auth.rate_limit import enforce_inspect_rate_limit
from book2audio.api.deps import get_current_user, get_db
from book2audio.api.jobs import inspection, service
from book2audio.api.jobs.schemas import (
    BookInspection,
    CastReport,
    CastReviewRequest,
    CreateJobRequest,
    CreateJobResponse,
    InspectRequest,
    JobDetail,
    JobListResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from book2audio.db.models import User
from book2audio.storage.r2 import presign_put

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Enforced by the presigned PUT's implicit trust boundary: R2/S3 presigned PUT
# URLs (query-string V4 signing) cannot carry a server-verified Content-Length
# condition the way presigned POST policies can. The real cap must be enforced
# by an R2 bucket-level max-object-size rule; this constant only documents the
# intended limit for client-side guidance.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/upload-url", response_model=UploadUrlResponse)
def create_upload_url(
    payload: UploadUrlRequest, current_user: User = Depends(get_current_user)
) -> UploadUrlResponse:
    object_key = f"uploads/{current_user.id}/{uuid.uuid4()}-{payload.filename}"
    return UploadUrlResponse(upload_url=presign_put(object_key), object_key=object_key)


@router.post("/inspect", response_model=BookInspection)
def inspect_upload(
    payload: InspectRequest, current_user: User = Depends(get_current_user)
) -> BookInspection:
    """The chapters of an uploaded EPUB, so the wizard can offer a chapter picker."""
    enforce_inspect_rate_limit(current_user.id)
    return inspection.inspect_uploaded_epub(current_user.id, payload.epub_object_key)


@router.post("", response_model=CreateJobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: CreateJobRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateJobResponse:
    job = service.create_job(db, current_user.id, payload)
    return CreateJobResponse(job_id=job.id, status=job.status)


@router.get("", response_model=JobListResponse)
def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobListResponse:
    return service.list_jobs(db, current_user.id, page, page_size)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetail:
    job = service.get_job(db, current_user.id, job_id)
    if job is None:
        # 404, not 403: a non-owner must not be able to distinguish
        # "doesn't exist" from "exists but isn't yours".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return service.to_detail(job)


@router.get("/{job_id}/cast", response_model=CastReport)
def get_job_cast(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CastReport:
    job = service.get_job(db, current_user.id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    report = service.get_cast_report(job)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cast report not available")
    return report


@router.post("/{job_id}/cast-review", response_model=CreateJobResponse)
def confirm_cast_review(
    job_id: uuid.UUID,
    payload: CastReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateJobResponse:
    job = service.confirm_cast_review(db, current_user.id, job_id, payload)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return CreateJobResponse(job_id=job.id, status=job.status)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    job = service.cancel_job(db, current_user.id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
