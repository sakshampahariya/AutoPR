"""LangGraph state schema and factory helpers."""

from __future__ import annotations

import re
from operator import add
from typing import Annotated, List, Literal, Optional, TypedDict

_ISSUE_URL_PATTERN = re.compile(r"github\.com/([^/]+)/([^/]+)/issues/(\d+)")


class FileChange(TypedDict):
    file_path: str
    original_content: str
    patched_content: str
    diff: str
    change_description: str


class TestResult(TypedDict):
    status: Literal["pass", "fail", "error", "pending"]
    total_tests: int
    passed: int
    failed: int
    errors: int
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


class AgentLog(TypedDict):
    agent_name: Literal["research", "coding", "testing", "pr", "system"]
    level: Literal["info", "warning", "error", "debug"]
    message: str
    timestamp: str


class AgentState(TypedDict):
    # Input fields
    run_id: str
    issue_url: str
    repo_owner: str
    repo_name: str
    issue_number: int
    github_token: str
    model_name: str

    # Research agent output
    issue_title: Optional[str]
    issue_body: Optional[str]
    relevant_files: Optional[List[str]]
    codebase_context: Optional[str]
    repo_structure: Optional[str]

    # Coding agent output
    file_changes: Optional[List[FileChange]]
    patch_summary: Optional[str]

    # Testing agent output
    test_result: Optional[TestResult]
    retry_count: int

    # PR agent output
    branch_name: Optional[str]
    pr_url: Optional[str]
    pr_number: Optional[int]

    # System fields
    logs: Annotated[List[AgentLog], add]
    current_node: str
    final_status: Literal["running", "success", "failed"]
    error_message: Optional[str]


def parse_issue_url(issue_url: str) -> tuple[str, str, int]:
    """Extract owner, repo name, and issue number from a GitHub issue URL."""
    match = _ISSUE_URL_PATTERN.search(issue_url)
    if not match:
        raise ValueError(
            f"Invalid GitHub issue URL: {issue_url!r}. "
            "Expected format: https://github.com/owner/repo/issues/123"
        )
    owner, repo, issue_number_str = match.groups()
    return owner, repo, int(issue_number_str)


def create_initial_state(
    run_id: str,
    issue_url: str,
    github_token: str,
    model_name: str,
) -> AgentState:
    """Build a fresh AgentState for a new orchestration run."""
    repo_owner, repo_name, issue_number = parse_issue_url(issue_url)

    return AgentState(
        run_id=run_id,
        issue_url=issue_url,
        repo_owner=repo_owner,
        repo_name=repo_name,
        issue_number=issue_number,
        github_token=github_token,
        model_name=model_name,
        issue_title=None,
        issue_body=None,
        relevant_files=None,
        codebase_context=None,
        repo_structure=None,
        file_changes=None,
        patch_summary=None,
        test_result=None,
        retry_count=0,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        logs=[],
        current_node="",
        final_status="running",
        error_message=None,
    )
