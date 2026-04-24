"""Integration tests for /api/v1/auth/* endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestSignup:
    def test_success(self, client: TestClient):
        resp = client.post("/api/v1/auth/signup", json={
            "email": "newuser@test.com",
            "password": "Valid@1234",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "user" in data
        assert data["user"]["email"] == "newuser@test.com"
        assert data["user"]["role"] == "user"
        # Cookies should be set
        assert "access_token" in resp.cookies or any("access_token" in c for c in resp.headers.getlist("set-cookie"))

    def test_duplicate_email(self, client: TestClient):
        client.post("/api/v1/auth/signup", json={"email": "dupe@test.com", "password": "Valid@1234"})
        resp = client.post("/api/v1/auth/signup", json={"email": "dupe@test.com", "password": "Valid@1234"})
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"].lower()

    def test_weak_password(self, client: TestClient):
        resp = client.post("/api/v1/auth/signup", json={"email": "weak@test.com", "password": "short"})
        assert resp.status_code == 422

    def test_invalid_email(self, client: TestClient):
        resp = client.post("/api/v1/auth/signup", json={"email": "not-an-email", "password": "Valid@1234"})
        assert resp.status_code == 422

    def test_missing_fields(self, client: TestClient):
        resp = client.post("/api/v1/auth/signup", json={})
        assert resp.status_code == 422


class TestLogin:
    def test_success(self, client: TestClient):
        # Create user first
        client.post("/api/v1/auth/signup", json={"email": "login@test.com", "password": "Valid@1234"})
        resp = client.post("/api/v1/auth/login", json={"email": "login@test.com", "password": "Valid@1234"})
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "login@test.com"

    def test_wrong_password(self, client: TestClient):
        client.post("/api/v1/auth/signup", json={"email": "wrongpw@test.com", "password": "Valid@1234"})
        resp = client.post("/api/v1/auth/login", json={"email": "wrongpw@test.com", "password": "Wrong@9999"})
        assert resp.status_code == 401

    def test_nonexistent_user(self, client: TestClient):
        resp = client.post("/api/v1/auth/login", json={"email": "ghost@test.com", "password": "Valid@1234"})
        assert resp.status_code == 401


class TestMe:
    def test_authenticated(self, auth_client: TestClient):
        resp = auth_client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "testuser@example.com"

    def test_unauthenticated(self, client: TestClient):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401


class TestRefreshToken:
    def test_refresh_without_cookie(self, client: TestClient):
        resp = client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401

    def test_refresh_with_invalid_cookie(self, client: TestClient):
        client.cookies.set("refresh_token", "invalid.token.here")
        resp = client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    def test_clears_cookies(self, client: TestClient):
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Logged out"
