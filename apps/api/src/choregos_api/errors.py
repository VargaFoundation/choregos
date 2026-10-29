# SPDX-License-Identifier: Apache-2.0
"""Erreurs HTTP au format RFC 9457 (`application/problem+json`)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from choregos_contracts.issue_codes import deprecated_issue_code
from choregos_core import ChoregosError, ValidationError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_JSON = "application/problem+json"
#: A client `X-Request-Id` is used only when it is a short token; anything else is replaced.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def former_code(code: str) -> dict[str, str]:
    """`{"deprecated_code": ...}` for a code renamed to English, empty otherwise (ADR 0046).

    Spread into an issue so that a client matching the former code keeps working for one release.
    """
    former = deprecated_issue_code(code)
    return {"deprecated_code": former} if former is not None else {}


class ApiError(Exception):
    """Erreur applicative portant un statut HTTP et un type RFC 9457."""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str = "",
        *,
        type_: str = "about:blank",
        errors: list[dict[str, Any]] | None = None,
        retry_after: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.type = type_
        self.errors = errors or []
        self.retry_after = retry_after
        #: more members of the problem document (`code`, `verdicts`...), never overriding the standard ones
        self.extra = extra or {}

    def to_problem(self, instance: str = "") -> dict[str, Any]:
        problem: dict[str, Any] = {"type": self.type, "title": self.title, "status": self.status_code}
        if self.detail:
            problem["detail"] = self.detail
        if instance:
            problem["instance"] = instance
        if self.errors:
            problem["errors"] = self.errors
        if self.retry_after is not None:
            problem["retry_after"] = self.retry_after
        for key, value in self.extra.items():
            problem.setdefault(key, value)
        return problem


def not_found(what: str, key: str) -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, f"{what} not found", f"{what} `{key}` does not exist")


def forbidden(detail: str = "insufficient rights") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "Forbidden", detail)


def unauthorized(detail: str = "authentication required") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "Not authenticated", detail)


def conflict(detail: str, *, extra: dict[str, Any] | None = None) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, "State conflict", detail, extra=extra)


def unprocessable(detail: str, errors: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unprocessable entity", detail, errors=errors)


def upstream(service: str, detail: str, retry_after: int | None = None) -> ApiError:
    """Erreur d'un service externe (GitHub, Argo, LiteLLM) encapsulée avec un `retry_after`."""
    return ApiError(
        status.HTTP_502_BAD_GATEWAY,
        f"Error from the {service} service",
        detail,
        type_=f"https://choregos.dev/problems/upstream/{service}",
        retry_after=retry_after,
    )


def correlation_id(request: Request) -> str:
    """The id that ties an answer to its log lines: the one the logging middleware set, or a short token."""
    known = getattr(request.state, "request_id", None)
    if known:
        return str(known)
    supplied = request.headers.get("x-request-id", "")
    return supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex[:16]


def internal_error(request: Request, correlation: str) -> JSONResponse:
    """An unhandled error, as problem+json (S26-02): no stack trace, no exception message — they may
    carry secrets or tenant data — but the correlation id that finds them in the logs."""
    problem = ApiError(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error",
        f"an unexpected error occurred; quote the correlation id {correlation} to the operators",
        type_="https://choregos.dev/problems/internal",
    ).to_problem(str(request.url.path))
    problem["correlation_id"] = correlation
    return JSONResponse(
        problem, status_code=500, media_type=PROBLEM_JSON, headers={"X-Request-Id": correlation}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Last resort, for what fails outside the logging middleware (which answers itself).
        # Starlette still re-raises afterwards, so the server logs the traceback.
        return internal_error(request, correlation_id(request))

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(
            exc.to_problem(str(request.url.path)),
            status_code=exc.status_code,
            media_type=PROBLEM_JSON,
            headers=headers,
        )

    @app.exception_handler(ValidationError)
    async def _core_validation(request: Request, exc: ValidationError) -> JSONResponse:
        problem = ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid {exc.subject}",
            str(exc),
            errors=[
                {
                    "loc": [issue.path] if issue.path else [],
                    "msg": issue.message,
                    "code": issue.code,
                    "line": issue.line,
                    "column": issue.column,
                    **former_code(issue.code),
                }
                for issue in exc.issues
            ],
        )
        return JSONResponse(
            problem.to_problem(str(request.url.path)), status_code=422, media_type=PROBLEM_JSON
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problem = ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Invalid request",
            "the body or the parameters do not match the contract",
            errors=[{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        )
        return JSONResponse(
            problem.to_problem(str(request.url.path)), status_code=422, media_type=PROBLEM_JSON
        )

    @app.exception_handler(ChoregosError)
    async def _choregos_error(request: Request, exc: ChoregosError) -> JSONResponse:
        problem = ApiError(status.HTTP_400_BAD_REQUEST, type(exc).__name__, str(exc))
        return JSONResponse(
            problem.to_problem(str(request.url.path)), status_code=400, media_type=PROBLEM_JSON
        )
