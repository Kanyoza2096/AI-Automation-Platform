"""
Integration tests — Authentication, Workspace Isolation, Users CRUD.

Tests real API endpoints with the Starlette TestClient.
Supabase is mocked; in-memory fallback is used for data.
"""

import pytest
from unittest.mock import patch


class TestAuthentication:
    """Test auth guard on protected endpoints."""

    def test_public_endpoint_no_auth_required(self, client):
        """Public endpoints should return 200 without auth."""
        public = ["/api/v1/status", "/api/v1/health/deep"]
        for path in public:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} should be public"

    def test_protected_endpoint_requires_auth(self, client):
        """Protected endpoints should return 401 without auth."""
        protected = [
            "/api/v1/posts",
            "/api/v1/analytics",
            "/api/v1/metrics",
            "/api/v1/audit-logs",
        ]
        for path in protected:
            resp = client.get(path)
            assert resp.status_code == 401, f"{path} should require auth"

    def test_valid_token_grants_access(self, client, auth_headers):
        """Valid auth token should grant access."""
        resp = client.get("/api/v1/posts", headers=auth_headers)
        assert resp.status_code == 200

    def test_invalid_token_denied(self, client):
        """Invalid token should return 401."""
        resp = client.get(
            "/api/v1/posts",
            headers={"Authorization": "Bearer invalid-token-12345"},
        )
        assert resp.status_code == 401

    def test_missing_token_denied(self, client):
        """No auth header should return 401."""
        resp = client.get("/api/v1/posts", headers={})
        assert resp.status_code == 401


class TestWorkspaceIsolation:
    """Test that workspaces are isolated from each other."""

    def test_create_workspace(self, client, auth_headers):
        """Should create a workspace and return it."""
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace A", "description": "Isolation test"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert "workspace" in data
        return data["workspace"]["id"]

    def test_create_second_workspace(self, client, auth_headers):
        """Should create a second workspace."""
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace B", "description": "Second workspace"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()["workspace"]["id"]

    def test_list_workspaces(self, client, auth_headers):
        """Should list all workspaces."""
        resp = client.get("/api/v1/workspaces", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "workspaces" in data

    def test_get_workspace_by_id(self, client, auth_headers):
        """Should get a specific workspace."""
        # Create one first
        create_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Get By ID Test", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = create_resp.json()["workspace"]["id"]

        resp = client.get(f"/api/v1/workspaces/{ws_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["workspace"]["id"] == ws_id

    def test_workspace_not_found_returns_404(self, client, auth_headers):
        """Non-existent workspace should return 404."""
        resp = client.get("/api/v1/workspaces/nonexistent-999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_workspace(self, client, auth_headers):
        """Should delete a workspace."""
        create_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "To Delete", "description": "Delete me"},
            headers=auth_headers,
        )
        ws_id = create_resp.json()["workspace"]["id"]

        resp = client.delete(f"/api/v1/workspaces/{ws_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        get_resp = client.get(f"/api/v1/workspaces/{ws_id}", headers=auth_headers)
        assert get_resp.status_code == 404


class TestUsersCRUD:
    """Test workspace user management with RBAC."""

    def test_list_users_in_workspace(self, client, auth_headers):
        """Should list users in a workspace."""
        # Create workspace first
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "User Test WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        resp = client.get(f"/api/v1/workspaces/{ws_id}/users", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "count" in data

    def test_add_user_to_workspace(self, client, auth_headers):
        """Should add a user to a workspace."""
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Add User WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        resp = client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "testuser@example.com", "role": "editor"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["user"]["user_id"] == "testuser@example.com"
        assert data["user"]["role"] == "editor"

    def test_add_duplicate_user_rejected(self, client, auth_headers):
        """Should reject duplicate user in same workspace."""
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Dup User WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        # First add
        client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "dup@example.com", "role": "viewer"},
            headers=auth_headers,
        )

        # Duplicate
        resp = client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "dup@example.com", "role": "editor"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_invalid_role_rejected(self, client, auth_headers):
        """Should reject invalid role values."""
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Role Test WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        resp = client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "badrole@example.com", "role": "superadmin"},
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    def test_update_user_role(self, client, auth_headers):
        """Should update a user's role."""
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Update Role WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        # Add user
        client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "rolechange@example.com", "role": "viewer"},
            headers=auth_headers,
        )

        # Update role
        resp = client.put(
            f"/api/v1/workspaces/{ws_id}/users/rolechange@example.com",
            json={"role": "admin"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "admin"

    def test_remove_user_from_workspace(self, client, auth_headers):
        """Should remove a user from a workspace."""
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Remove User WS", "description": "Test"},
            headers=auth_headers,
        )
        ws_id = ws_resp.json()["workspace"]["id"]

        # Add user
        client.post(
            f"/api/v1/workspaces/{ws_id}/users",
            json={"email": "remove@example.com", "role": "viewer"},
            headers=auth_headers,
        )

        # Remove user
        resp = client.delete(
            f"/api/v1/workspaces/{ws_id}/users/remove@example.com",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
