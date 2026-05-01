"""Tests for API error handling middleware."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aurex_trade.web.app import create_app


@pytest.fixture
def client() -> Generator[TestClient]:
    """Create test client with lifespan."""
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestHTTPExceptionHandler:
    """Tests for HTTPException handling."""

    def test_404_returns_json(self, client: TestClient) -> None:
        """Non-existent route returns JSON, not HTML."""
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "Not Found"
        assert data["status_code"] == 404
        assert data["detail"] is None

    def test_custom_http_exception(self) -> None:
        """Custom HTTPException returns consistent JSON shape."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/fail")
        def fail() -> None:
            raise HTTPException(status_code=403, detail="Forbidden access")

        with TestClient(app) as tc:
            response = tc.get("/fail")
            assert response.status_code == 403
            data = response.json()
            assert data["error"] == "Forbidden access"
            assert data["status_code"] == 403


class TestValidationErrorHandler:
    """Tests for RequestValidationError handling."""

    def test_invalid_body_returns_422(self, client: TestClient) -> None:
        """Invalid request body returns 422 with field errors."""
        response = client.post(
            "/api/backtest",
            json={"capital": -100},
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "Validation error"
        assert data["status_code"] == 422
        assert data["detail"] is not None
        assert "capital" in data["detail"]

    def test_multiple_field_errors(self, client: TestClient) -> None:
        """Multiple invalid fields are all reported."""
        response = client.post(
            "/api/backtest",
            json={"capital": -100, "spread": -1},
        )
        assert response.status_code == 422
        data = response.json()
        assert "capital" in data["detail"]
        assert "spread" in data["detail"]

    def test_invalid_date_format(self, client: TestClient) -> None:
        """Invalid date format returns validation error."""
        response = client.post(
            "/api/backtest",
            json={"start_date": "not-a-date"},
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "Validation error"


class TestUnhandledExceptionHandler:
    """Tests for catch-all exception handling."""

    def test_unhandled_exception_returns_500(self) -> None:
        """Unhandled exceptions return safe 500 JSON, no stack trace."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/crash")
        def crash() -> None:
            raise RuntimeError("something broke")

        with TestClient(app, raise_server_exceptions=False) as tc:
            response = tc.get("/crash")
            assert response.status_code == 500
            data = response.json()
            assert data["error"] == "Internal server error"
            assert data["detail"] is None
            assert data["status_code"] == 500

    def test_no_traceback_exposed(self) -> None:
        """Internal details are never exposed to the client."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/crash")
        def crash() -> None:
            raise ValueError("secret internal details")

        with TestClient(app, raise_server_exceptions=False) as tc:
            response = tc.get("/crash")
            body = response.text
            assert "secret internal details" not in body
            assert "Traceback" not in body

    def test_exception_is_logged(self) -> None:
        """Unhandled exceptions are logged with structlog."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/crash")
        def crash() -> None:
            raise RuntimeError("log me")

        with patch("aurex_trade.web.errors.logger") as mock_logger:
            with TestClient(app, raise_server_exceptions=False) as tc:
                tc.get("/crash")
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs["exc_type"] == "RuntimeError"
            assert "log me" in call_kwargs["traceback"]


class TestHTMXErrorHandling:
    """Tests that HTMX routes receive HTML errors, not JSON."""

    def test_htmx_validation_error_returns_html(self, client: TestClient) -> None:
        """Validation errors on /htmx/* return HTML fragments."""
        response = client.post(
            "/htmx/backtest/submit",
            json={"capital": -100},
        )
        assert response.status_code == 422
        assert "text/html" in response.headers["content-type"]
        assert "alert-error" in response.text
        assert "capital" in response.text

    def test_htmx_404_returns_html(self) -> None:
        """HTTPException on /htmx/* returns HTML fragment."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/htmx/thing")
        def thing() -> None:
            raise HTTPException(status_code=404, detail="Not found")

        with TestClient(app) as tc:
            response = tc.get("/htmx/thing")
            assert response.status_code == 404
            assert "text/html" in response.headers["content-type"]
            assert "Not found" in response.text

    def test_htmx_500_returns_html(self) -> None:
        """Unhandled exceptions on /htmx/* return HTML fragment."""
        from aurex_trade.web.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/htmx/explode")
        def explode() -> None:
            raise RuntimeError("boom")

        with TestClient(app, raise_server_exceptions=False) as tc:
            response = tc.get("/htmx/explode")
            assert response.status_code == 500
            assert "text/html" in response.headers["content-type"]
            assert "Internal server error" in response.text
            assert "boom" not in response.text

    def test_api_route_still_returns_json(self, client: TestClient) -> None:
        """Non-HTMX routes still get JSON errors (regression check)."""
        response = client.post("/api/backtest", json={"capital": -100})
        assert response.status_code == 422
        assert "application/json" in response.headers["content-type"]


class TestErrorResponseSchema:
    """Tests that all error responses follow the consistent schema."""

    def test_schema_has_three_fields(self, client: TestClient) -> None:
        """Error response always has error, detail, and status_code."""
        response = client.get("/api/nonexistent")
        data = response.json()
        assert set(data.keys()) == {"error", "detail", "status_code"}

    def test_content_type_is_json(self, client: TestClient) -> None:
        """Error responses have application/json content type."""
        response = client.get("/api/nonexistent")
        assert "application/json" in response.headers["content-type"]
