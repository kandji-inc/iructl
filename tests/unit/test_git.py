import contextlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iructl import _git
from iructl._constants import ROOT_MARKER
from iructl.exceptions import InvalidRepositoryError


@pytest.fixture
def tmp_path_git_cd(git_repo):
    """Change the working directory to a temporary git repository and return the path."""
    with contextlib.chdir(git_repo):
        yield git_repo


class TestGitCommand:
    """Tests for the git command wrapper."""

    @pytest.mark.usefixtures("tmp_path_git_cd")
    def test_git_status_cwd(self):
        """Ensure the git function returns a subprocess.CompletedProcess as a result if successful."""
        result = _git.git("status")
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert "On branch main" in result.stdout

    def test_git_status_cd_path(self, git_repo):
        """Ensure the git function changes the working directory when the cd_path argument is provided."""
        result = _git.git("status", cd_path=git_repo)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert "On branch main" in result.stdout

    def test_git_init(self, tmp_path):
        assert not (tmp_path / ".git").exists()
        result = _git.git("init", cd_path=tmp_path)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert (tmp_path / ".git").exists()
        assert "Initialized empty Git repository" in result.stdout

    @pytest.mark.usefixtures("git_remote")
    def test_add_reset_commit_push_workflow(self, git_repo):
        """Ensure the git function can add files to the index."""

        with contextlib.chdir(git_repo):
            # create a file and add it to the index
            (git_repo / "file01.txt").write_text("test")
            _git.git("add", "file01.txt")
            result = _git.git("status")
            assert "Changes to be committed" in result.stdout
            assert re.search(r"new file:\s+file01.txt", result.stdout) is not None

            # create a second file and add all files to the index
            (git_repo / "file02.txt").write_text("test")
            _git.git("add", "--all")
            result = _git.git("status")
            assert "Changes to be committed" in result.stdout
            assert re.search(r"new file:\s+file01.txt", result.stdout) is not None
            assert re.search(r"new file:\s+file02.txt", result.stdout) is not None

            # reset the first file
            _git.git("reset", "file01.txt")
            result = _git.git("status")
            assert "Changes to be committed" in result.stdout
            assert re.search(r"new file:\s+file02.txt", result.stdout) is not None
            assert "Untracked files" in result.stdout
            assert "file01.txt" in result.stdout

            # commit the second file
            result = _git.git("commit", "-m", "added file02.txt")
            assert "1 file changed" in result.stdout
            assert "create mode 100644 file02.txt" in result.stdout

            # push the commit to the remote
            result = _git.git("push")
            assert "main -> main" in result.stderr

    def test_expected_exit_code(self, tmp_path):
        """Ensure the setting the correct expected exit code suppresses exceptions."""
        assert not (tmp_path / ".git").exists()
        _git.git("status", cd_path=tmp_path, expected_exit_code=128)

    def test_git_error(self, tmp_path):
        """Ensure the git function raises an exception when the git command fails."""
        assert not (tmp_path / ".git").exists()
        with pytest.raises(_git.GitRepositoryError):
            _git.git("status", cd_path=tmp_path, expected_exit_code=0)


class TestLocateGit:
    """Tests for the _locate_git function."""

    def test_locate_git(self):
        """Ensure the _locate_git function returns the path to the git executable."""
        assert _git.locate_git().endswith("git")

    def test_git_missing(self, monkeypatch):
        """Ensure the _locate_git function raises an exception when the git executable is missing."""

        monkeypatch.setattr("shutil.which", lambda _: None)
        with pytest.raises(FileNotFoundError, match=r"Failed to locate the git executable\."):
            _git.locate_git()

    def test_git_execution_error(self, monkeypatch):
        """Ensure the _locate_git function raises an exception when the git executable is not working."""

        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(returncode=1, cmd=" ".join(*args), stderr="error executing git")

        monkeypatch.setattr(subprocess, "run", mock_run)
        with pytest.raises(FileNotFoundError, match=r"Git execution yielded unexpected result: error executing git"):
            _git.locate_git()

    def test_locate_git_cached_response(self):
        """Ensure the _locate_get function caches the result and doesn't needlessly generate a subprocess."""
        git_path = _git.locate_git()

        for _ in range(10):
            assert git_path == _git.locate_git()

        assert _git.locate_git.cache_info().misses == 1
        assert _git.locate_git.cache_info().hits == 10


class TestLocateGitRoot:
    """Tests for locate_git_root: the git working-tree root (via ``git rev-parse``)."""

    def test_repo_check_cache(self, git_repo):
        """Ensure locate_git_root caches the result and doesn't needlessly call subprocess run."""
        for _ in range(10):
            _git.locate_git_root(cd_path=git_repo)

        assert _git.locate_git_root.cache_info().misses == 1
        assert _git.locate_git_root.cache_info().hits == 9

        subdir = git_repo / "subdir"
        subdir.mkdir()
        _git.locate_git_root(cd_path=subdir)

        assert _git.locate_git_root.cache_info().misses == 2

        _git.locate_git_root(cd_path=git_repo)

        assert _git.locate_git_root.cache_info().hits == 10

    def test_locate_repo_invalid_repo(self, tmp_path):
        """Ensure locate_git_root raises an exception when the path is not in a git repository."""
        with pytest.raises(InvalidRepositoryError):
            _git.locate_git_root(cd_path=tmp_path)

    def test_locate_existing_parent(self, git_repo):
        """Ensure locate_git_root resolves a non-existent subpath up to the git root."""
        assert _git.locate_git_root(cd_path=git_repo / "some/other/non_existent/path.txt") == git_repo

    def test_failed_locate_existing_parent_raises(self):
        """Ensure locate_git_root raises when no existing parent directory can be found."""
        with pytest.raises(InvalidRepositoryError, match=r"Failed to locate an existing parent directory"):
            _git.locate_git_root(cd_path=Path("/non_existent/path.txt"))

    def test_returns_git_root_ignoring_marker(self, git_repo):
        """The git-root lookup returns the git toplevel regardless of where a marker file sits."""
        subdir = git_repo / "subdir"
        subdir.mkdir()
        (subdir / ROOT_MARKER).touch()
        assert _git.locate_git_root(cd_path=subdir) == git_repo


class TestCommitAllChanges:
    """Tests for the commit_all_changes function."""

    def test_no_changes(self, iructl_repo, caplog):
        """Ensure the commit_all_changes function does nothing when there are no changes."""
        caplog.set_level(logging.DEBUG)
        _git.commit_all_changes(cd_path=iructl_repo, message="test", include_body=False)
        assert "No changes to commit" in caplog.text
        # An empty staged diff (exit 0) is an expected outcome, not a warning.
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_unstaged_changes(self, iructl_repo, caplog):
        """Ensure the commit_all_changes function stages and commits unstaged changes."""
        caplog.set_level(logging.DEBUG)
        (iructl_repo / "file01.txt").write_text("test")
        (iructl_repo / "file02.txt").write_text("test2")
        _git.commit_all_changes(cd_path=iructl_repo, message="test commit", include_body=False)
        assert "Changes committed. 2 files changed," in caplog.text
        status = _git.git("log", "--oneline", "-1", cd_path=iructl_repo)
        assert "test commit" in status.stdout

    def test_staged_changes(self, iructl_repo, caplog):
        """Ensure the commit_all_changes function commits staged changes."""
        caplog.set_level(logging.DEBUG)
        (iructl_repo / "file01.txt").write_text("test")
        (iructl_repo / "file02.txt").write_text("test2")
        _git.git("add", "--all", cd_path=iructl_repo)
        if _git.git("diff", "--staged", "--exit-code", cd_path=iructl_repo).returncode != 1:
            pytest.fail("Failed to stage changes for test.")
        _git.commit_all_changes(cd_path=iructl_repo, message="test commit", include_body=False)
        assert "Changes committed. 2 files changed," in caplog.text
        status = _git.git("log", "--oneline", "-1", cd_path=iructl_repo)
        assert "test commit" in status.stdout

    def test_disabled_skips_commit(self, iructl_repo: Path, caplog):
        """With enabled=False, commit_all_changes leaves the repo untouched and logs a debug skip."""
        caplog.set_level(logging.DEBUG)
        (iructl_repo / "file01.txt").write_text("test")
        head_before = _git.git("rev-parse", "HEAD", cd_path=iructl_repo).stdout

        _git.commit_all_changes(cd_path=iructl_repo, message="test", include_body=False, enabled=False)

        assert _git.git("rev-parse", "HEAD", cd_path=iructl_repo).stdout == head_before
        assert "Skipping git commits" in caplog.text

    def test_unstage_keeps_path_tracked_and_uncommitted(self, iructl_repo: Path):
        """unstage keeps a tracked path's changes out of the commit without untracking it."""
        tracked = iructl_repo / "payloads" / "installer.pkg"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("binary bytes")
        _git.git("add", "--all", cd_path=iructl_repo, expected_exit_code=0)
        _git.git("commit", "-m", "add installer", cd_path=iructl_repo, expected_exit_code=0)
        assert "payloads/installer.pkg" in _git.git("ls-files", cd_path=iructl_repo).stdout

        # Modify the tracked binary and add an unrelated change so a commit is still made.
        tracked.write_text("new bytes")
        (iructl_repo / "other.txt").write_text("change")
        _git.commit_all_changes(cd_path=iructl_repo, message="unstage", include_body=False, unstage={tracked})

        assert "payloads/installer.pkg" in _git.git("ls-files", cd_path=iructl_repo).stdout  # still tracked
        assert tracked.read_text() == "new bytes"  # working-tree change preserved
        committed = _git.git("show", "HEAD:payloads/installer.pkg", cd_path=iructl_repo).stdout
        assert committed == "binary bytes"  # the update was not committed

    def test_commit_with_scope(self, iructl_repo: Path):
        profile_path = iructl_repo / "profiles/Test Profile"
        profile_path.mkdir(parents=True)
        (profile_path / "info.yaml").write_text("test")
        (profile_path / "profile.mobileconfig").write_text("test")

        script_path = iructl_repo / "scripts/Test Script"
        script_path.mkdir(parents=True)
        (script_path / "info.yaml").write_text("test")
        (script_path / "audit.sh").write_text("test")
        (script_path / "remediation.sh").write_text("test")

        result = _git.git("status", cd_path=iructl_repo)
        assert "profiles/" in result.stdout
        assert "scripts/" in result.stdout
        _git.commit_all_changes(cd_path=iructl_repo, message="test commit", scope=iructl_repo / "profiles")
        result = _git.git("status", cd_path=iructl_repo)
        assert "profiles/" not in result.stdout
        assert "scripts/" in result.stdout


def test_generate_commit_body(iructl_repo: Path):
    """Ensure the generate_commit_body function generates the expected commit body."""

    profile_path = iructl_repo / "profiles/Test Profile"
    profile_path.mkdir(parents=True)
    (profile_path / "info.yaml").write_text("test")
    (profile_path / "profile.mobileconfig").write_text("test")

    script_path = iructl_repo / "scripts/Test Script"
    script_path.mkdir(parents=True)
    (script_path / "info.yaml").write_text("test")
    (script_path / "audit.sh").write_text("test")
    (script_path / "remediation.sh").write_text("test")

    (iructl_repo / "README.md").write_text("# Iru Repository")

    # Check that the commit body reflects the scope
    _git.git("add", str(iructl_repo / "profiles"), cd_path=iructl_repo)
    message_body = _git.generate_commit_body(repo=iructl_repo, stage=True)
    assert message_body == (
        "--- Profiles Added ---\n* profiles/Test Profile/info.yaml\n* profiles/Test Profile/profile.mobileconfig"
    )

    _git.git("add", "--all", cd_path=iructl_repo)
    message_body = _git.generate_commit_body(repo=iructl_repo, stage=True)
    assert message_body == (
        "--- Profiles Added ---\n"
        "* profiles/Test Profile/info.yaml\n"
        "* profiles/Test Profile/profile.mobileconfig\n"
        "\n"
        "--- Scripts Added ---\n"
        "* scripts/Test Script/audit.sh\n"
        "* scripts/Test Script/info.yaml\n"
        "* scripts/Test Script/remediation.sh\n"
        "\n"
        "--- Other Added ---\n"
        "* README.md"
    )
    _git.git("commit", "-m", "create files", cd_path=iructl_repo)

    (profile_path / "info.yaml").write_text("changed")
    (profile_path / "profile.mobileconfig").write_text("changed")

    (script_path / "info.yaml").write_text("changed")
    (script_path / "audit.sh").write_text("changed")
    (script_path / "remediation.sh").write_text("changed")

    _git.git("add", "--all", cd_path=iructl_repo)
    message_body = _git.generate_commit_body(repo=iructl_repo, stage=True)
    assert message_body == (
        "--- Profiles Modified ---\n"
        "* profiles/Test Profile/info.yaml\n"
        "* profiles/Test Profile/profile.mobileconfig\n"
        "\n"
        "--- Scripts Modified ---\n"
        "* scripts/Test Script/audit.sh\n"
        "* scripts/Test Script/info.yaml\n"
        "* scripts/Test Script/remediation.sh"
    )
    _git.git("commit", "-m", "modify files", cd_path=iructl_repo)

    shutil.rmtree(profile_path)
    shutil.rmtree(script_path)
    _git.git("add", "--all", cd_path=iructl_repo)
    message_body = _git.generate_commit_body(repo=iructl_repo, stage=True)
    assert message_body == (
        "--- Profiles Deleted ---\n"
        "* profiles/Test Profile/info.yaml\n"
        "* profiles/Test Profile/profile.mobileconfig\n"
        "\n"
        "--- Scripts Deleted ---\n"
        "* scripts/Test Script/audit.sh\n"
        "* scripts/Test Script/info.yaml\n"
        "* scripts/Test Script/remediation.sh"
    )


def test_git_status_from_status(git_repo):
    """Ensure the git status function returns the correct status."""
    assert _git.GitStatus.from_status("A") == _git.GitStatus.ADDED
    assert _git.GitStatus.from_status("M") == _git.GitStatus.MODIFIED
    assert _git.GitStatus.from_status("D") == _git.GitStatus.DELETED
    assert _git.GitStatus.from_status("R") == _git.GitStatus.RENAMED
    assert _git.GitStatus.from_status("C") == _git.GitStatus.COPIED
    assert _git.GitStatus.from_status("T") == _git.GitStatus.TYPE_CHANGED
    assert _git.GitStatus.from_status("U") == _git.GitStatus.UNMERGED
    assert _git.GitStatus.from_status("X") == _git.GitStatus.UNKNOWN
