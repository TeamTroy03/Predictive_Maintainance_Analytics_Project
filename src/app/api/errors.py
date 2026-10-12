"""One error format for every failure:

{"error": {"code": "...", "message": "...", "correlation_id": "...", "details": [...]}}
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.core.logging import correlation_id


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list[dict] = []


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raise this anywhere: it becomes a clean JSON error with the right status code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def error_response(status: int, code: str, message: str, details: list | None = None):  # noqa: ANN201
    body = ErrorBody(code=code, message=message, correlation_id=correlation_id.get(), details=details or [])
    headers = {"Retry-After": "10"} if status == 429 else None
    return JSONResponse(ErrorResponse(error=body).model_dump(), status_code=status, headers=headers)


# Documented in Swagger for every protected route
ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing or invalid credentials"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
    503: {"model": ErrorResponse, "description": "Gold or LLM unavailable"},
}


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field + reason only: input values are not echoed back.
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return error_response(422, "validation_error", "Request failed validation", details)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))
