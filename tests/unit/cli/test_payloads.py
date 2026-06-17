import logging
import subprocess
from pathlib import Path

import pytest

from iructl._cli import payloads


def _commit_all(repo: Path) -> None:
    """Force-add (past .gitignore) and commit everything in the repo."""
    subprocess.run(["git", "-C", repo, "add", "-f", "--all"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "test"], check=True, capture_output=True)


class TestResolvePayloadDir:
    # resolve_payload_dir is a pure resolver; the flag/env/config layers collapse into `override`
    # upstream (Click's --payload-dir option) before it is called. cwd is tmp_path (autouse
    # tmp_path_cd) and repo_root is a child of it, so the relative case proves the override anchors
    # to the repo root rather than the CWD.

    @pytest.mark.parametrize(
        ("make_override", "make_expected"),
        [
            pytest.param(
                lambda repo: str(repo.parent / "elsewhere" / "payloads"),
                lambda repo: repo.parent / "elsewhere" / "payloads",
                id="absolute-override-used-as-is",
            ),
            pytest.param(
                lambda _: "staging/payloads",
                lambda repo: repo / "staging" / "payloads",
                id="relative-override-anchored-to-repo-root-not-cwd",
            ),
            pytest.param(
                lambda _: None,
                lambda repo: repo / "payloads",
                id="default-is-repo-payloads",
            ),
            pytest.param(
                lambda _: "   ",
                lambda repo: repo / "payloads",
                id="blank-override-is-unset",
            ),
        ],
    )
    def test_resolve_payload_dir(self, tmp_path, make_override, make_expected):
        repo_root = tmp_path / "repo"
        result = payloads.resolve_payload_dir(repo_root, override=make_override(repo_root))
        assert result == make_expected(repo_root).resolve()


class TestTrackedPayloadPaths:
    def test_returns_tracked_binaries_excluding_gitignore(self, iructl_repo):
        payload_dir = iructl_repo / "payloads"
        payload_dir.mkdir()
        (payload_dir / ".gitignore").write_text("*\n")
        (payload_dir / "installer.pkg").write_text("bytes")
        _commit_all(iructl_repo)

        tracked = payloads.tracked_payload_paths(payload_dir, iructl_repo)
        assert {path.resolve() for path in tracked} == {(payload_dir / "installer.pkg").resolve()}

    def test_empty_outside_git_tree(self, iructl_repo, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        assert payloads.tracked_payload_paths(outside, iructl_repo) == set()


class TestEnsurePayloadsGitignore:
    def test_creates_when_missing(self, iructl_repo):
        payload_dir = iructl_repo / "payloads"
        payloads.ensure_payloads_gitignore(payload_dir, iructl_repo)

        assert (payload_dir / ".gitignore").read_text() == "*\n"
        # The gitignore ignores itself, so nothing under payloads is ever tracked.
        tracked = subprocess.run(
            ["git", "-C", iructl_repo, "ls-files", "payloads"], check=True, capture_output=True, text=True
        )
        assert tracked.stdout == ""

    def test_noop_when_present(self, iructl_repo):
        payload_dir = iructl_repo / "payloads"
        payload_dir.mkdir()
        (payload_dir / ".gitignore").write_text("custom\n")
        payloads.ensure_payloads_gitignore(payload_dir, iructl_repo)
        assert (payload_dir / ".gitignore").read_text() == "custom\n"  # left untouched

    def test_noop_outside_git_tree(self, iructl_repo, tmp_path):
        outside = tmp_path / "outside"
        payloads.ensure_payloads_gitignore(outside, iructl_repo)
        assert not (outside / ".gitignore").exists()


class TestWarnTrackedPayloads:
    def test_warns_when_tracked(self, iructl_repo, caplog):
        payload_dir = iructl_repo / "payloads"
        payload_dir.mkdir()
        (payload_dir / "installer.pkg").write_text("bytes")
        _commit_all(iructl_repo)

        with caplog.at_level(logging.WARNING):
            payloads.warn_tracked_payloads(payload_dir, iructl_repo)
        assert "tracked in git" in caplog.text

    def test_silent_when_none_tracked(self, iructl_repo, caplog):
        payload_dir = iructl_repo / "payloads"
        payload_dir.mkdir()
        with caplog.at_level(logging.WARNING):
            payloads.warn_tracked_payloads(payload_dir, iructl_repo)
        assert "tracked in git" not in caplog.text
