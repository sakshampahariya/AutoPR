"""
GitHub API integration for Research, Coding, and PR agents.

Uses PyGithub for repository/issue/PR operations and httpx for code search.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

import httpx
from github import Auth, Github
from github.GithubException import (
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_FILE_BYTES = 500 * 1024
MAX_SEARCH_RESULTS = 10
CODE_SEARCH_URL = "https://api.github.com/search/code"

EXCLUDED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})
EXCLUDED_SUFFIX = ".pyc"
SENSITIVE_FILES = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "known_hosts",
        "authorized_keys",
    }
)
SENSITIVE_EXTENSIONS = (
    ".env",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".crt",
    ".cer",
    ".der",
)

_tools_cache: Dict[str, "GitHubTools"] = {}


class GitHubToolsError(Exception):
    """Raised when a GitHub API operation fails."""


class GitHubTools:
    """Wrapper around PyGithub and the GitHub REST API for agent tooling."""

    def __init__(self, token: str) -> None:
        """
        Initialize the GitHub client.

        Args:
            token: GitHub personal access token with repo access.
        """
        if not token or not token.strip():
            raise ValueError("GitHub token must be a non-empty string")
        self._token = token.strip()
        self._github = Github(auth=Auth.Token(self._token))

    def _repo(self, owner: str, repo: str):
        """Return a PyGithub Repository handle."""
        try:
            return self._github.get_repo(f"{owner}/{repo}")
        except UnknownObjectException as exc:
            raise GitHubToolsError(
                f"Repository not found or not accessible: {owner}/{repo}"
            ) from exc
        except GithubException as exc:
            raise GitHubToolsError(
                f"Failed to access repository {owner}/{repo}: {exc.data}"
            ) from exc

    def _wait_for_rate_limit_reset(self) -> None:
        """Sleep until the authenticated client's rate limit resets."""
        reset = getattr(self._github, "rate_limiting_resettime", None)
        if reset is None:
            time.sleep(60)
            return
        wait_seconds = max(0, int(reset - time.time()) + 1)
        logger.warning("GitHub rate limit hit; sleeping %s seconds", wait_seconds)
        time.sleep(min(wait_seconds, 300))

    def _call_with_rate_limit_retry(self, operation: Callable[[], T], label: str) -> T:
        """
        Execute a PyGithub call, retrying once after a rate-limit reset.

        Args:
            operation: Zero-argument callable performing the API call.
            label: Human-readable operation name for error messages.

        Returns:
            The result of ``operation()``.
        """
        try:
            return operation()
        except RateLimitExceededException:
            self._wait_for_rate_limit_reset()
            try:
                return operation()
            except RateLimitExceededException as exc:
                raise GitHubToolsError(
                    f"GitHub rate limit exceeded again while {label}"
                ) from exc
        except GithubException as exc:
            raise GitHubToolsError(f"GitHub API error while {label}: {exc.data}") from exc

    @staticmethod
    def _should_exclude(name: str, is_dir: bool) -> bool:
        if name in EXCLUDED_DIRS:
            return True
        if not is_dir and name.endswith(EXCLUDED_SUFFIX):
            return True
        if name in SENSITIVE_FILES:
            return True
        if name.startswith(".env."):
            return True
        if name.endswith(SENSITIVE_EXTENSIONS):
            return True
        return False

    def get_issue_details(self, owner: str, repo: str, issue_number: int) -> dict:
        """
        Fetch metadata for a GitHub issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.

        Returns:
            Dict with keys: title, body, labels, state, created_at, comments_count.
        """
        repository = self._repo(owner, repo)

        def _fetch():
            return repository.get_issue(issue_number)

        issue = self._call_with_rate_limit_retry(
            _fetch, f"fetching issue #{issue_number} in {owner}/{repo}"
        )
        created_at = issue.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return {
            "title": issue.title or "",
            "body": issue.body or "",
            "labels": [label.name for label in issue.labels],
            "state": issue.state,
            "created_at": created_at.isoformat(),
            "comments_count": issue.comments,
        }

    def get_repo_structure(
        self,
        owner: str,
        repo: str,
        path: str = "",
        max_depth: int = 3,
        ref: str = "main",
    ) -> str:
        """
        Build a formatted directory tree for the repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: Starting path (empty string = repository root).
            max_depth: Maximum directory depth to traverse.
            ref: Git ref (branch, tag, or SHA).

        Returns:
            Multi-line string resembling ``tree`` output with folder/file icons.
        """
        repository = self._repo(owner, repo)
        lines = self._build_tree_lines(repository, path, depth=0, max_depth=max_depth, ref=ref)
        root_label = path or "/"
        header = f"📁 {root_label}\n" if path else "📁 /\n"
        return header + "\n".join(lines)

    def _build_tree_lines(
        self,
        repository,
        path: str,
        depth: int,
        max_depth: int,
        ref: str,
    ) -> List[str]:
        if depth > max_depth:
            return []

        def _list_contents():
            return repository.get_contents(path, ref=ref)

        try:
            contents = self._call_with_rate_limit_retry(
                _list_contents, f"listing contents at '{path or '/'}'"
            )
        except GitHubToolsError:
            return []

        if not isinstance(contents, list):
            return []

        items = sorted(contents, key=lambda c: (c.type != "dir", c.name.lower()))
        lines: List[str] = []

        for item in items:
            is_dir = item.type == "dir"
            if self._should_exclude(item.name, is_dir):
                continue

            indent = "  " * depth
            icon = "📁 " if is_dir else "📄 "
            lines.append(f"{indent}{icon}{item.name}")

            if is_dir and depth < max_depth:
                lines.extend(
                    self._build_tree_lines(repository, item.path, depth + 1, max_depth, ref)
                )

        return lines

    def read_file(
        self,
        owner: str,
        repo: str,
        file_path: str,
        ref: str = "main",
    ) -> str:
        """
        Read a single file from the repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            file_path: Path relative to repository root.
            ref: Git ref (branch, tag, or SHA).

        Returns:
            Decoded UTF-8 file content.

        Raises:
            FileNotFoundError: If the path does not exist or is not a file.
            ValueError: If the file exceeds 500KB.
        """
        repository = self._repo(owner, repo)
        normalized = file_path.lstrip("/")
        base_name = normalized.split("/")[-1]
        if self._should_exclude(base_name, is_dir=False):
            raise FileNotFoundError(
                f"File not available: {owner}/{repo}/{normalized} (ref={ref})"
            )

        def _fetch():
            return repository.get_contents(normalized, ref=ref)

        try:
            content_file = self._call_with_rate_limit_retry(
                _fetch, f"reading file '{normalized}'"
            )
        except GitHubToolsError as exc:
            if "404" in str(exc):
                raise FileNotFoundError(
                    f"File not found: {owner}/{repo}/{normalized} (ref={ref})"
                ) from exc
            raise

        if isinstance(content_file, list):
            raise FileNotFoundError(
                f"Path is a directory, not a file: {owner}/{repo}/{normalized}"
            )

        raw = content_file.decoded_content
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(
                f"File '{normalized}' exceeds 500KB limit ({len(raw)} bytes)"
            )

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"File '{normalized}' is not valid UTF-8 text"
            ) from exc

    def list_directory(self, owner: str, repo: str, path: str) -> List[str]:
        """
        List entry names at a repository path.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: Directory path (empty string for root).

        Returns:
            Sorted list of file and directory names.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the path is a file, not a directory.
        """
        repository = self._repo(owner, repo)
        normalized = path.lstrip("/") if path else ""

        def _fetch():
            return repository.get_contents(normalized) if normalized else repository.get_contents("")

        try:
            contents = self._call_with_rate_limit_retry(
                _fetch, f"listing directory '{normalized or '/'}'"
            )
        except GitHubToolsError as exc:
            if "404" in str(exc):
                raise FileNotFoundError(
                    f"Directory not found: {owner}/{repo}/{normalized or '/'}"
                ) from exc
            raise

        if not isinstance(contents, list):
            raise ValueError(
                f"Path is a file, not a directory: {owner}/{repo}/{normalized}"
            )

        names = [item.name for item in contents if not self._should_exclude(item.name, item.type == "dir")]
        return sorted(names, key=str.lower)

    def search_codebase(self, owner: str, repo: str, query: str) -> List[dict]:
        """
        Search code in a repository via the GitHub Code Search API.

        Args:
            owner: Repository owner.
            repo: Repository name.
            query: Search terms (repo filter is applied automatically).

        Returns:
            Up to 10 dicts: {path, name, html_url, snippet}.
        """
        q = f"{query} repo:{owner}/{repo}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        for attempt in range(2):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(
                        CODE_SEARCH_URL,
                        params={"q": q, "per_page": MAX_SEARCH_RESULTS},
                        headers=headers,
                    )
            except httpx.HTTPError as exc:
                raise GitHubToolsError(f"Code search request failed: {exc}") from exc

            if response.status_code == 403 and attempt == 0:
                reset_header = response.headers.get("X-RateLimit-Reset")
                if reset_header:
                    wait = max(0, int(reset_header) - int(time.time()) + 1)
                    time.sleep(min(wait, 300))
                    continue
                raise GitHubToolsError("GitHub code search rate limit exceeded")

            if response.status_code >= 400:
                detail = response.text[:500]
                raise GitHubToolsError(
                    f"Code search failed ({response.status_code}): {detail}"
                )

            data = response.json()
            break
        else:
            raise GitHubToolsError("Code search failed after rate-limit retry")

        results: List[dict] = []
        for item in data.get("items", [])[:MAX_SEARCH_RESULTS]:
            snippet = ""
            for match in item.get("text_matches", []) or []:
                fragment = match.get("fragment")
                if fragment:
                    snippet = fragment
                    break

            results.append(
                {
                    "path": item.get("path", ""),
                    "name": item.get("name", ""),
                    "html_url": item.get("html_url", ""),
                    "snippet": snippet,
                }
            )
        return results

    def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        base_branch: str = "main",
    ) -> str:
        """
        Create a new branch from the tip of ``base_branch``.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch_name: Desired branch name.
            base_branch: Branch to fork from.

        Returns:
            The name of the created branch (may include a timestamp suffix if
            the requested name already exists).
        """
        repository = self._repo(owner, repo)
        effective_name = branch_name

        def _get_base():
            return repository.get_branch(base_branch)

        try:
            base = self._call_with_rate_limit_retry(
                _get_base, f"resolving base branch '{base_branch}'"
            )
        except GitHubToolsError as exc:
            raise GitHubToolsError(
                f"Base branch '{base_branch}' not found in {owner}/{repo}"
            ) from exc

        sha = base.commit.sha

        def _create_ref(name: str):
            repository.create_git_ref(ref=f"refs/heads/{name}", sha=sha)

        try:
            self._call_with_rate_limit_retry(
                lambda: _create_ref(effective_name),
                f"creating branch '{effective_name}'",
            )
            return effective_name
        except GitHubToolsError as exc:
            if "422" not in str(exc) and "Reference already exists" not in str(exc):
                raise

        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        effective_name = f"{branch_name}-{suffix}"

        self._call_with_rate_limit_retry(
            lambda: _create_ref(effective_name),
            f"creating branch '{effective_name}' with suffix",
        )
        return effective_name

    def commit_files(
        self,
        owner: str,
        repo: str,
        branch: str,
        file_changes: List[dict],
        commit_message: str,
    ) -> str:
        """
        Create or update files on a branch in a single logical commit flow.

        Each change dict must include:
          - ``file_path``: repository-relative path
          - ``patched_content`` or ``content``: new file text

        PyGithub performs one commit per file; this returns the SHA of the
        last successful commit.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Target branch name.
            file_changes: List of file change dicts.
            commit_message: Git commit message.

        Returns:
            SHA of the last commit created.

        Raises:
            ValueError: If ``file_changes`` is empty or missing required keys.
            GitHubToolsError: On API failure.
        """
        if not file_changes:
            raise ValueError("file_changes must contain at least one file")

        repository = self._repo(owner, repo)
        last_sha = ""

        for change in file_changes:
            path = change.get("file_path") or change.get("path")
            content = change.get("patched_content") or change.get("content")
            if not path:
                raise ValueError("Each file change must include 'file_path'")
            if content is None:
                raise ValueError(f"Missing content for file '{path}'")

            normalized = str(path).lstrip("/")

            def _get_existing():
                try:
                    existing = repository.get_contents(normalized, ref=branch)
                    if isinstance(existing, list):
                        return None
                    return existing.sha
                except UnknownObjectException:
                    return None

            existing_sha = self._call_with_rate_limit_retry(
                _get_existing, f"checking existing file '{normalized}'"
            )

            if existing_sha:

                def _update():
                    result = repository.update_file(
                        path=normalized,
                        message=commit_message,
                        content=content,
                        sha=existing_sha,
                        branch=branch,
                    )
                    return result["commit"].sha

                last_sha = self._call_with_rate_limit_retry(
                    _update, f"updating '{normalized}' on '{branch}'"
                )
            else:

                def _create():
                    result = repository.create_file(
                        path=normalized,
                        message=commit_message,
                        content=content,
                        branch=branch,
                    )
                    return result["commit"].sha

                last_sha = self._call_with_rate_limit_retry(
                    _create, f"creating '{normalized}' on '{branch}'"
                )

        return last_sha

    def open_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> dict:
        """
        Open a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: PR title.
            body: PR description (markdown).
            head: Head branch name.
            base: Base branch name.

        Returns:
            Dict with ``pr_url`` and ``pr_number``.
        """
        repository = self._repo(owner, repo)

        def _create():
            return repository.create_pull(title=title, body=body, head=head, base=base)

        pr = self._call_with_rate_limit_retry(
            _create, f"opening pull request '{title}'"
        )
        return {"pr_url": pr.html_url, "pr_number": pr.number}


def get_github_tools(token: str) -> GitHubTools:
    """
    Return a cached ``GitHubTools`` instance for the given token.

    Cache key is a SHA-256 hash of the token so raw tokens are not stored
    as dict keys in logs or reprs.

    Args:
        token: GitHub personal access token.

    Returns:
        Shared ``GitHubTools`` for this token.
    """
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if key not in _tools_cache:
        _tools_cache[key] = GitHubTools(token)
    return _tools_cache[key]
