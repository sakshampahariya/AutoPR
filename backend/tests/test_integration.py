"""Integration tests for API wiring, state schema, routing, and Docker security."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from core.state import AgentState, create_initial_state
from graph.router import MAX_RETRIES, route_after_testing
from main import app
from tools.docker_tools import DockerSandbox


def _sample_test_result(status: str = "pass") -> dict:
    return {
        "status": status,
        "total_tests": 3,
        "passed": 2 if status != "pass" else 3,
        "failed": 1 if status == "fail" else 0,
        "errors": 0,
        "stdout": "3 passed in 0.5s",
        "stderr": "",
        "exit_code": 0 if status == "pass" else 1,
        "duration_seconds": 0.5,
    }


def _base_state() -> AgentState:
    return create_initial_state(
        run_id="test-123",
        issue_url="https://github.com/owner/repo/issues/42",
        github_token="fake_token",
        model_name="claude-sonnet-4-20250514",
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    api_health = await client.get("/api/health")
    assert api_health.status_code == 200
    body = api_health.json()
    assert body["status"] == "ok"
    assert "docker_available" in body


@pytest.mark.asyncio
async def test_create_run_invalid_url(client: AsyncClient):
    response = await client.post(
        "/api/runs",
        json={
            "issue_url": "https://notgithub.com/owner/repo/issues/1",
            "github_token": "fake",
            "model_name": "claude-sonnet-4-20250514",
        },
    )
    # Pydantic URL validation rejects before GitHub check (422) or bad request (400)
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_run_invalid_github_access(client: AsyncClient):
    """Valid URL format but unreachable issue returns 400."""
    with patch("api.routes.get_github_tools") as mock_tools:
        instance = MagicMock()
        instance.get_issue_details.side_effect = Exception("not found")
        mock_tools.return_value = instance

        response = await client.post(
            "/api/runs",
            json={
                "issue_url": "https://github.com/owner/repo/issues/42",
                "github_token": "fake",
                "model_name": "claude-sonnet-4-20250514",
            },
        )
        assert response.status_code == 400


def test_state_schema_creation():
    state = create_initial_state(
        run_id="test-123",
        issue_url="https://github.com/owner/repo/issues/42",
        github_token="fake_token",
        model_name="claude-sonnet-4-20250514",
    )
    assert state["repo_owner"] == "owner"
    assert state["repo_name"] == "repo"
    assert state["issue_number"] == 42
    assert state["retry_count"] == 0
    assert state["logs"] == []


def test_router_logic():
    state_pass = _base_state()
    state_pass["test_result"] = _sample_test_result("pass")  # type: ignore[typeddict-item]
    assert route_after_testing(state_pass) == "pass"

    state_fail = _base_state()
    state_fail["test_result"] = _sample_test_result("fail")  # type: ignore[typeddict-item]
    state_fail["retry_count"] = 0
    assert route_after_testing(state_fail) == "retry"

    state_max = _base_state()
    state_max["test_result"] = _sample_test_result("fail")  # type: ignore[typeddict-item]
    state_max["retry_count"] = MAX_RETRIES
    assert route_after_testing(state_max) == "fail"

    state_no_result = _base_state()
    assert route_after_testing(state_no_result) == "fail"


@patch("tools.docker_tools.docker.from_env")
def test_docker_tools_security(mock_from_env: MagicMock, tmp_path):
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.images.get.return_value = MagicMock()

    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.side_effect = lambda stdout=True, stderr=False: (
        b"1 passed in 0.1s" if stdout else b""
    )
    mock_client.containers.run.return_value = mock_container
    mock_from_env.return_value = mock_client

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    sandbox = DockerSandbox()
    sandbox.run_tests(str(workspace))

    mock_client.containers.run.assert_called_once()
    call_kwargs = mock_client.containers.run.call_args.kwargs

    assert call_kwargs["network_mode"] == "none"
    assert call_kwargs["read_only"] is True
    assert call_kwargs["mem_limit"] == "512m"
    assert call_kwargs["cap_drop"] == ["ALL"]
    assert call_kwargs["security_opt"] == ["no-new-privileges:true"]
    assert call_kwargs["working_dir"] == "/app"

    volumes = call_kwargs["volumes"]
    assert any(v.get("mode") == "ro" for v in volumes.values())

    mock_container.remove.assert_called_with(force=True)
