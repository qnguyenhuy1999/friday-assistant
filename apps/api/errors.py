"""Centralized application-error-to-HTTP mapping. Routes never catch
`ApplicationError` (or SQLAlchemy exceptions, which never escape the
`UnitOfWork` boundary) themselves — this module is the single place that
translates the stable application error hierarchy into HTTP responses, so
every route gets identical error shapes for free.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from friday.application.errors import (
    ApplicationError,
    ApprovalNotFound,
    ArtifactNotFound,
    ConcurrencyConflict,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
    TaskNotFound,
    ToolInvocationNotFound,
    TransactionFailure,
)
from friday.domain.errors import DomainValidationError


class ErrorDetail(BaseModel):
    type: str
    message: str
    details: dict[str, str] = {}


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Resource not found."},
    409: {"model": ErrorResponse, "description": "State conflict."},
    422: {"model": ErrorResponse, "description": "Request validation failed."},
    500: {"model": ErrorResponse, "description": "Internal server error."},
}


_NOT_FOUND_TYPES: dict[type[ApplicationError], str] = {
    TaskNotFound: "task_not_found",
    RunNotFound: "run_not_found",
    RunStepNotFound: "run_step_not_found",
    ApprovalNotFound: "approval_not_found",
    ToolInvocationNotFound: "tool_invocation_not_found",
    ArtifactNotFound: "artifact_not_found",
}

_CONFLICT_TYPES: dict[type[ApplicationError], str] = {
    EntityConflict: "entity_conflict",
    ConcurrencyConflict: "concurrency_conflict",
}


def _error_body(error_type: str, message: str) -> dict[str, object]:
    return {"error": {"type": error_type, "message": message, "details": {}}}


def _map_application_error(exc: ApplicationError) -> JSONResponse:
    for error_cls, error_type in _NOT_FOUND_TYPES.items():
        if isinstance(exc, error_cls):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=_error_body(error_type, str(exc)),
            )
    for error_cls, error_type in _CONFLICT_TYPES.items():
        if isinstance(exc, error_cls):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=_error_body(error_type, str(exc)),
            )
    if isinstance(exc, TransactionFailure):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("transaction_failure", "The database transaction failed."),
        )
    # Any other stable ApplicationError subclass is an application-safe
    # internal error: the message is already safe to surface (see
    # friday.application.errors), but the type stays generic.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body("internal_error", str(exc)),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def _application_error_handler(_request: Request, exc: ApplicationError) -> JSONResponse:
        return _map_application_error(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", "The request failed schema validation."),
        )

    @app.exception_handler(DomainValidationError)
    async def _domain_validation_handler(
        _request: Request, _exc: DomainValidationError
    ) -> JSONResponse:
        # Domain conversion at the transport boundary (typed IDs/timestamps)
        # must be indistinguishable from normal request-schema validation.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", "The request failed schema validation."),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            return JSONResponse(
                status_code=exc.status_code,
                content=_error_body("validation_error", "The request failed schema validation."),
            )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
