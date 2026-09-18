"""Erreurs HTTP au format RFC 9457 (`application/problem+json`)."""

from __future__ import annotations

from typing import Any

from choregos_core import ChoregosError, ValidationError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_JSON = "application/problem+json"


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
    ) -> None:
        super().__init__(title)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.type = type_
        self.errors = errors or []
        self.retry_after = retry_after

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
        return problem


def not_found(what: str, key: str) -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, f"{what} introuvable", f"{what} `{key}` n'existe pas")


def forbidden(detail: str = "droits insuffisants") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "Interdit", detail)


def unauthorized(detail: str = "authentification requise") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "Non authentifié", detail)


def conflict(detail: str) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, "Conflit d'état", detail)


def unprocessable(detail: str, errors: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "Entité non traitable", detail, errors=errors)


def upstream(service: str, detail: str, retry_after: int | None = None) -> ApiError:
    """Erreur d'un service externe (GitHub, Argo, LiteLLM) encapsulée avec un `retry_after`."""
    return ApiError(
        status.HTTP_502_BAD_GATEWAY,
        f"Erreur du service {service}",
        detail,
        type_=f"https://choregos.dev/problems/upstream/{service}",
        retry_after=retry_after,
    )


def install_error_handlers(app: FastAPI) -> None:
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
            f"{exc.subject} invalide",
            str(exc),
            errors=[
                {
                    "loc": [issue.path] if issue.path else [],
                    "msg": issue.message,
                    "code": issue.code,
                    "line": issue.line,
                    "column": issue.column,
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
            "Requête invalide",
            "le corps ou les paramètres ne respectent pas le contrat",
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
