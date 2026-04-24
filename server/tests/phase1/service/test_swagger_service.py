"""Unit tests for app.services.swagger_service — OpenAPI parsing and text conversion."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.services.swagger_service import EndpointRecord, endpoint_to_text, parse_swagger


# ---------------------------------------------------------------------------
# Minimal OpenAPI spec fixtures
# ---------------------------------------------------------------------------

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "summary": "List all users",
                "description": "Returns a list of users",
                "tags": ["users"],
                "parameters": [
                    {"name": "limit", "in": "query", "required": False}
                ],
                "responses": {
                    "200": {"description": "Success"},
                    "401": {"description": "Unauthorized"},
                },
            },
            "post": {
                "operationId": "createUser",
                "summary": "Create a user",
                "tags": ["users"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"}
                        }
                    }
                },
                "responses": {
                    "201": {"description": "Created"},
                },
            },
        },
        "/users/{id}": {
            "delete": {
                "operationId": "deleteUser",
                "summary": "Delete a user",
                "responses": {"204": {"description": "Deleted"}},
            }
        },
    },
}


def _write_spec_to_file(spec: dict, suffix: str = ".json") -> str:
    """Write a spec dict to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        json.dump(spec, f)
    return path


class TestParseSwagger:
    def test_parses_all_endpoints(self):
        path = _write_spec_to_file(MINIMAL_SPEC)
        try:
            endpoints = parse_swagger(path)
            assert len(endpoints) == 3

            methods = {(ep.http_method, ep.path) for ep in endpoints}
            assert ("GET", "/users") in methods
            assert ("POST", "/users") in methods
            assert ("DELETE", "/users/{id}") in methods
        finally:
            os.unlink(path)

    def test_extracts_metadata(self):
        path = _write_spec_to_file(MINIMAL_SPEC)
        try:
            endpoints = parse_swagger(path)
            get_users = [ep for ep in endpoints if ep.operation_id == "listUsers"][0]

            assert get_users.summary == "List all users"
            assert get_users.description == "Returns a list of users"
            assert "users" in get_users.tags
            assert len(get_users.parameters) == 1
        finally:
            os.unlink(path)

    def test_empty_paths(self):
        spec = {"openapi": "3.0.0", "info": {"title": "Empty", "version": "1.0"}, "paths": {}}
        path = _write_spec_to_file(spec)
        try:
            endpoints = parse_swagger(path)
            assert endpoints == []
        finally:
            os.unlink(path)


class TestEndpointToText:
    def test_full_endpoint(self):
        ep = EndpointRecord(
            http_method="GET",
            path="/items",
            operation_id="listItems",
            summary="List items",
            description="Get all items",
            tags=["items", "inventory"],
            parameters=[{"name": "page", "in": "query", "required": True}],
            request_body=None,
            responses={"200": {"description": "OK"}},
        )
        text = endpoint_to_text(ep)

        assert "GET /items" in text
        assert "Summary: List items" in text
        assert "Description: Get all items" in text
        assert "Tags: items, inventory" in text
        assert "page" in text
        assert "200: OK" in text

    def test_minimal_endpoint(self):
        ep = EndpointRecord(
            http_method="DELETE",
            path="/things/{id}",
        )
        text = endpoint_to_text(ep)
        assert "DELETE /things/{id}" in text
        # Optional fields should not appear
        assert "Summary:" not in text
        assert "Description:" not in text

    def test_with_request_body(self):
        ep = EndpointRecord(
            http_method="POST",
            path="/data",
            request_body={"content": {"application/json": {"schema": {"type": "object"}}}},
        )
        text = endpoint_to_text(ep)
        assert "Request Body:" in text
        assert "application/json" in text
