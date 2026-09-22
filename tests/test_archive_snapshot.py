"""scripts/archive_snapshot.sh, exercised against a throwaway git remote.

This script pushes to a branch and is only otherwise exercised by the scheduled
workflow, where a failure is noticed late. Two real bugs were found by testing
it this way: a bash-4-only array subscript, and a leftover local `data-raw`
branch that made every second run in the same clone fail.
"""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archive_snapshot.sh"

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def run_script(clone: Path, raw_dir: str = "data/raw") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), raw_dir],
        cwd=clone,
        capture_output=True,
        text=True,
    )


def write_snapshot(clone: Path, name: str, payload: bytes = b"{}") -> None:
    path = clone / "data" / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload))


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git("init", "-q", "--bare", cwd=origin)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git("config", "user.name", "test bot", cwd=work)
    git("config", "user.email", "bot@example.invalid", cwd=work)
    (work / "README.md").write_text("main content\n")
    git("add", "README.md", cwd=work)
    git("commit", "-qm", "init", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    (work / "data" / "raw").mkdir(parents=True)
    return work


def branch_files(clone: Path, branch: str) -> set[str]:
    return set(git("ls-tree", "-r", "--name-only", f"origin/{branch}", cwd=clone).splitlines())


def test_creates_the_orphan_branch_on_first_run(clone: Path):
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    result = run_script(clone)
    assert result.returncode == 0, result.stderr
    git("fetch", "-q", "origin", cwd=clone)
    files = branch_files(clone, "data-raw")
    assert "snapshots/2026/09/20260922T113117Z-abc123def456.json.gz" in files
    assert "README.md" in files


def test_main_is_left_alone(clone: Path):
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    run_script(clone)
    git("fetch", "-q", "origin", cwd=clone)
    assert branch_files(clone, "main") == {"README.md"}


def test_branch_shares_no_history_with_main(clone: Path):
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    run_script(clone)
    git("fetch", "-q", "origin", cwd=clone)
    main_commits = git("rev-list", "origin/main", cwd=clone).splitlines()
    raw_commits = git("rev-list", "origin/data-raw", cwd=clone).splitlines()
    assert not set(main_commits) & set(raw_commits)


def test_rerunning_with_the_same_snapshot_is_a_noop(clone: Path):
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    assert run_script(clone).returncode == 0
    second = run_script(clone)
    assert second.returncode == 0, second.stderr
    assert "already archived" in second.stdout


def test_second_run_in_the_same_clone_succeeds(clone: Path):
    """Regression: a leftover local data-raw branch used to break this."""
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    assert run_script(clone).returncode == 0
    write_snapshot(clone, "20261001T090000Z-fed654cba321.json.gz")
    result = run_script(clone)
    assert result.returncode == 0, result.stderr
    git("fetch", "-q", "origin", cwd=clone)
    assert "snapshots/2026/10/20261001T090000Z-fed654cba321.json.gz" in branch_files(
        clone, "data-raw"
    )


def test_snapshots_accumulate_by_year_and_month(clone: Path):
    for name in (
        "20260922T113117Z-aaaaaaaaaaaa.json.gz",
        "20261001T090000Z-bbbbbbbbbbbb.json.gz",
        "20271115T120000Z-cccccccccccc.json.gz",
    ):
        write_snapshot(clone, name)
        assert run_script(clone).returncode == 0
    git("fetch", "-q", "origin", cwd=clone)
    files = branch_files(clone, "data-raw")
    assert {
        "snapshots/2026/09/20260922T113117Z-aaaaaaaaaaaa.json.gz",
        "snapshots/2026/10/20261001T090000Z-bbbbbbbbbbbb.json.gz",
        "snapshots/2027/11/20271115T120000Z-cccccccccccc.json.gz",
    } <= files


def test_missing_snapshot_directory_is_not_an_error(clone: Path):
    result = run_script(clone, raw_dir="data/does-not-exist")
    assert result.returncode == 0
    assert "nothing to do" in result.stdout


def test_empty_snapshot_directory_is_not_an_error(clone: Path):
    result = run_script(clone)
    assert result.returncode == 0
    assert "nothing to do" in result.stdout


def test_leaves_no_stray_branch_or_worktree(clone: Path):
    write_snapshot(clone, "20260922T113117Z-abc123def456.json.gz")
    run_script(clone)
    branches = git("branch", "--list", cwd=clone)
    assert "archive-snapshot" not in branches
    assert "data-raw" not in branches
    worktrees = git("worktree", "list", cwd=clone)
    assert worktrees.count("\n") == 0
