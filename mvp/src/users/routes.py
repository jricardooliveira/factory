"""HTTP routes for GDPR personal data exports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from auth.dependencies import CurrentUser, get_current_user
from common.errors import ApiError
from users.schemas import ExportRequestRead
from users.services import get_export_download_path, get_export_status, request_personal_data_export

router = APIRouter(prefix="/users/me", tags=["users"])


@router.post("/exports", response_model=ExportRequestRead, status_code=202)
def create_export(request: Request, current_user: CurrentUser = Depends(get_current_user)) -> ExportRequestRead:
    """Create a personal data export request for the authenticated user."""

    return request_personal_data_export(
        request.app.state.session_factory,
        current_user.id,
        request.app.state.storage_dir,
    )


@router.get("/exports/{export_id}", response_model=ExportRequestRead)
def read_export_status(
    export_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> ExportRequestRead:
    """Return export request status and metadata."""

    return get_export_status(request.app.state.session_factory, current_user.id, export_id)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    """Stream the ZIP artifact for the authenticated user."""

    file_path = get_export_download_path(
        request.app.state.session_factory,
        current_user.id,
        export_id,
        request.app.state.storage_dir,
    )
    return FileResponse(file_path, media_type="application/zip", filename=file_path.name)
