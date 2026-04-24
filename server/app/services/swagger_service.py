"""Swagger / OpenAPI specification parser — fully dereferences $ref pointers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import prance

logger = logging.getLogger(__name__)


@dataclass
class EndpointRecord:
    """Structured representation of a single API endpoint."""
    http_method: str
    path: str
    operation_id: str | None = None
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    request_body: dict | None = None
    responses: dict = field(default_factory=dict)


def parse_swagger(file_path: str) -> list[EndpointRecord]:
    """Parse an OpenAPI/Swagger file, resolving all $ref pointers.

    Supports both JSON and YAML files.
    Returns one EndpointRecord per (path, method) combination.
    """
    parser = prance.ResolvingParser(file_path, strict=False)
    spec = parser.specification

    endpoints: list[EndpointRecord] = []

    paths = spec.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "options", "head"):
            operation = path_item.get(method)
            if operation is None:
                continue

            endpoint = EndpointRecord(
                http_method=method.upper(),
                path=path,
                operation_id=operation.get("operationId"),
                summary=operation.get("summary", ""),
                description=operation.get("description", ""),
                tags=operation.get("tags", []),
                parameters=operation.get("parameters", []),
                request_body=operation.get("requestBody"),
                responses=operation.get("responses", {}),
            )
            endpoints.append(endpoint)

    logger.info("Parsed %d endpoints from '%s'.", len(endpoints), file_path)
    return endpoints


def endpoint_to_text(ep: EndpointRecord) -> str:
    """Convert an EndpointRecord to a human-readable text block for chunking.

    This text representation is what gets embedded into the vector store.
    """
    parts = [f"{ep.http_method} {ep.path}"]

    if ep.summary:
        parts.append(f"Summary: {ep.summary}")
    if ep.description:
        parts.append(f"Description: {ep.description}")
    if ep.tags:
        parts.append(f"Tags: {', '.join(ep.tags)}")

    if ep.parameters:
        param_lines = []
        for p in ep.parameters:
            name = p.get("name", "?")
            location = p.get("in", "?")
            required = p.get("required", False)
            param_lines.append(f"  - {name} (in: {location}, required: {required})")
        parts.append("Parameters:\n" + "\n".join(param_lines))

    if ep.request_body:
        parts.append(f"Request Body: {json.dumps(ep.request_body, indent=2, default=str)}")

    if ep.responses:
        resp_lines = []
        for status_code, resp in ep.responses.items():
            desc = resp.get("description", "") if isinstance(resp, dict) else str(resp)
            resp_lines.append(f"  {status_code}: {desc}")
        parts.append("Responses:\n" + "\n".join(resp_lines))

    return "\n".join(parts)
