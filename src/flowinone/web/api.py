"""Runtime request and response contracts shared by Flowinone JSON APIs."""

from __future__ import annotations

from typing import Any, TypeVar

from flask import Flask, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, ValidationError
from werkzeug.exceptions import HTTPException


class ApiInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class ApiOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ApiQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ApiValidationIssue(ApiOutput):
    field: str
    message: str
    type: str


class ErrorResponse(ApiOutput):
    error: str
    details: list[ApiValidationIssue] = []


class ApiRequestValidationError(ValueError):
    def __init__(
        self,
        validation_error: ValidationError | None = None,
        *,
        issues: list[dict] | None = None,
    ):
        self.issues = issues or [
            {
                "field": ".".join(str(part) for part in error["loc"]) or "body",
                "message": error["msg"],
                "type": error["type"],
            }
            for error in (validation_error.errors(include_url=False) if validation_error else [])
        ]
        super().__init__("Invalid request payload")


InputT = TypeVar("InputT", bound=ApiInput)
OutputT = TypeVar("OutputT", bound=BaseModel)


def parse_payload(schema: type[InputT], payload: Any) -> InputT:
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ApiRequestValidationError(exc) from exc


def parse_json(schema: type[InputT]) -> InputT:
    """Validate one JSON object and reject coercion or undocumented fields."""
    payload = request.get_json(silent=True)
    if payload is None and request.get_data(cache=True).strip():
        raise ApiRequestValidationError(
            issues=[
                {
                    "field": "body",
                    "message": "Request body must contain valid JSON",
                    "type": "json_invalid",
                }
            ]
        )
    return parse_payload(schema, {} if payload is None else payload)


QueryT = TypeVar("QueryT", bound=ApiQuery)


def parse_query(schema: type[QueryT], *, list_fields: tuple[str, ...] = ()) -> QueryT:
    """Validate query parameters, including explicitly repeated list fields."""
    payload: dict[str, Any] = request.args.to_dict(flat=True)
    for field in list_fields:
        if field in request.args:
            payload[field] = request.args.getlist(field)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ApiRequestValidationError(exc) from exc


def validated_json(
    payload: Any, schema: type[OutputT], status: int = 200
) -> tuple[Response, int]:
    """Validate the server's public response before serializing it."""
    try:
        validated = schema.model_validate(payload)
    except ValidationError as exc:
        current_app.logger.exception(
            "API response violated %s: %s", schema.__name__, exc
        )
        error = ErrorResponse(error="response_schema_violation")
        return jsonify(error.model_dump(mode="json")), 500
    return (
        jsonify(
            validated.model_dump(
                mode="json", by_alias=True, exclude_unset=True
            )
        ),
        status,
    )


def api_error(
    message: str, status: int = 400, *, details: list[dict] | None = None
) -> tuple[Response, int]:
    payload = ErrorResponse(error=message, details=details or [])
    return jsonify(payload.model_dump(mode="json")), status


def register_api_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiRequestValidationError)
    def handle_request_validation(error: ApiRequestValidationError):
        return api_error("invalid_request", 400, details=error.issues)

    @app.errorhandler(HTTPException)
    def handle_api_http_error(error: HTTPException):
        if not request.path.startswith("/api/"):
            return error
        return api_error(str(error.description), error.code or 500)


__all__ = [
    "ApiInput",
    "ApiOutput",
    "ApiQuery",
    "ErrorResponse",
    "api_error",
    "parse_json",
    "parse_payload",
    "parse_query",
    "register_api_error_handlers",
    "validated_json",
]
