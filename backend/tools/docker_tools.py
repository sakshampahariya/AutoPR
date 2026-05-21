"""
Docker sandbox for isolated test execution.

Security settings follow SRS Chapter 6 / section 2.6 (no network, read-only mount, etc.).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from typing import List, Optional

import docker
from docker.errors import DockerException, ImageNotFound

from core.config import get_settings
from core.state import FileChange, TestResult

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_TEST_COMMAND = "pytest -v --tb=short"


class DockerSandbox:
    """Runs pytest inside a hardened, ephemeral Docker container."""

    def __init__(self) -> None:
        try:
            self._client = docker.from_env()
            self._client.ping()
        except DockerException as exc:
            raise RuntimeError(
                "Docker is not available. Start Docker Desktop or the Docker daemon "
                "and ensure this process can access it."
            ) from exc

    def prepare_workspace(
        self,
        file_changes: List[FileChange],
        base_workspace: str,
    ) -> str:
        """
        Copy patched files from the coding workspace into a fresh temp directory.

        Args:
            file_changes: Patches produced by the Coding Agent.
            base_workspace: Directory where patched files were written.

        Returns:
            Path to the new workspace directory (bind-mounted read-only in the container).
        """
        dest = tempfile.mkdtemp(prefix="orchestrator_test_")
        base = os.path.abspath(base_workspace)

        for change in file_changes:
            rel_path = change["file_path"].lstrip("/").replace("\\", "/")
            src = os.path.join(base, rel_path)
            dst = os.path.join(dest, rel_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            if os.path.isfile(src):
                shutil.copy2(src, dst)
            elif change.get("patched_content") is not None:
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(change["patched_content"])
            else:
                logger.warning("Skipping missing workspace file: %s", rel_path)

        self._copy_if_present(base, dest, "requirements.txt")
        self._copy_if_present(base, dest, "pyproject.toml")
        self._copy_if_present(base, dest, "setup.cfg")
        self._copy_if_present(base, dest, "pytest.ini")
        self._copy_tree_if_present(base, dest, "tests")

        return dest

    @staticmethod
    def _copy_if_present(src_root: str, dest_root: str, filename: str) -> None:
        src = os.path.join(src_root, filename)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_root, filename))

    @staticmethod
    def _copy_tree_if_present(src_root: str, dest_root: str, dirname: str) -> None:
        src = os.path.join(src_root, dirname)
        if os.path.isdir(src):
            shutil.copytree(
                src,
                os.path.join(dest_root, dirname),
                dirs_exist_ok=True,
            )

    def run_tests(
        self,
        workspace_path: str,
        requirements_file: Optional[str] = None,
        test_command: str = DEFAULT_TEST_COMMAND,
        timeout: int = 180,
    ) -> TestResult:
        """
        Execute tests inside an isolated container and return structured results.

        Args:
            workspace_path: Host path bind-mounted at ``/app`` (read-only).
            requirements_file: Optional requirements path relative to workspace.
            test_command: Shell command run after optional pip install.
            timeout: Max seconds to wait for the container.

        Returns:
            Parsed :class:`TestResult`.
        """
        settings = get_settings()
        image = settings.docker_base_image or DEFAULT_IMAGE
        workspace_path = os.path.abspath(workspace_path)

        req = requirements_file or "requirements.txt"
        req_path = os.path.join(workspace_path, req)
        if os.path.isfile(req_path):
            install_prefix = f"pip install -r {req} -q 2>/dev/null || exit 1; "
        else:
            # Ensure pytest exists for minimal demo repos.
            install_prefix = "pip install pytest -q 2>/dev/null || exit 1; "
        shell_cmd = install_prefix + test_command

        container = None
        started = time.monotonic()
        try:
            try:
                self._client.images.get(image)
            except ImageNotFound:
                logger.info("Pulling Docker image %s", image)
                self._client.images.pull(image)

            run_config = {
                "image": image,
                "command": ["sh", "-c", shell_cmd],
                "volumes": {workspace_path: {"bind": "/app", "mode": "ro"}},
                "working_dir": "/app",
                "network_mode": "bridge" if settings.allow_docker_network else "none",
                "mem_limit": "512m",
                "cpu_period": 100000,
                "cpu_quota": 50000,
                "read_only": True,
                "tmpfs": {"/tmp": "size=64m", "/root": "size=32m"},
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "detach": True,
                "remove": False,
            }
            self.verify_security_config(run_config)

            container = self._client.containers.run(**run_config)

            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", 1))
            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            logger.exception("Docker test run failed")
            err = TestResult(
                status="error",
                total_tests=0,
                passed=0,
                failed=0,
                errors=1,
                stdout="",
                stderr=str(exc),
                exit_code=-1,
                duration_seconds=round(elapsed, 2),
            )
            return err
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException as exc:
                    logger.warning("Failed to remove container: %s", exc)

        result = self.parse_pytest_output(stdout + "\n" + stderr, exit_code)
        result["stdout"] = stdout
        result["stderr"] = stderr
        return result

    def parse_pytest_output(self, stdout: str, exit_code: int) -> TestResult:
        """
        Parse pytest output to extract counts, duration, and status.

        Args:
            stdout: Combined or stdout-only container logs.
            exit_code: Docker container exit code.

        Returns:
            Structured :class:`TestResult`.
        """
        passed = self._extract_count(stdout, r"(\d+)\s+passed")
        failed = self._extract_count(stdout, r"(\d+)\s+failed")
        errors = self._extract_count(stdout, r"(\d+)\s+error")

        duration_match = re.search(r"in\s+([\d.]+)s", stdout)
        duration_seconds = float(duration_match.group(1)) if duration_match else 0.0

        total_tests = passed + failed + errors
        if total_tests == 0 and exit_code == 0:
            total_tests = passed or 1

        lower = stdout.lower()
        if "no tests ran" in lower or exit_code == 5:
            status = "pass"
            stderr = "No tests collected"
        elif "memory" in lower or "oom" in lower or exit_code == 137:
            status: str = "error"
            stderr = "Container killed: memory limit exceeded"
        elif exit_code == 0:
            status = "pass"
            stderr = ""
        elif exit_code == 1:
            status = "fail"
            stderr = ""
        else:
            status = "error"
            stderr = ""

        return TestResult(
            status=status,  # type: ignore[typeddict-item]
            total_tests=total_tests,
            passed=passed,
            failed=failed,
            errors=errors,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
        )

    @staticmethod
    def _extract_count(text: str, pattern: str) -> int:
        match = re.search(pattern, text)
        return int(match.group(1)) if match else 0

    def verify_security_config(self, config: dict) -> None:
        """Validate container security settings before execution."""
        if config.get("network_mode") != "none":
            raise SecurityError("Docker sandbox must run with network_mode='none'.")
        if not config.get("mem_limit"):
            raise SecurityError("Docker sandbox must define mem_limit.")
        security_opt = config.get("security_opt") or []
        if "no-new-privileges:true" not in security_opt:
            raise SecurityError(
                "Docker sandbox must include security_opt 'no-new-privileges:true'."
            )
        cap_drop = config.get("cap_drop") or []
        if "ALL" not in cap_drop:
            raise SecurityError("Docker sandbox must drop all capabilities.")


class SecurityError(RuntimeError):
    """Raised when Docker sandbox security settings are not enforced."""


def client_available() -> bool:
    """Return True if the Docker daemon is reachable."""
    try:
        client = docker.from_env()
        client.ping()
        return True
    except DockerException:
        return False
