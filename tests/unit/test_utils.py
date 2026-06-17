from pathlib import Path

import pytest

from iructl._constants import APP_BRANDING, LEGACY_ROOT_MARKER, ROOT_MARKER
from iructl._utils import locate_repo_root, nearest_existing_dir
from iructl.exceptions import InvalidRepositoryError, UnmigratedRepositoryError


class TestNearestExistingDir:
    """Tests for nearest_existing_dir: resolve a path to its nearest existing ancestor directory."""

    def test_returns_nearest_existing_ancestor(self, tmp_path: Path):
        assert nearest_existing_dir(tmp_path / "a/b/c") == tmp_path

    def test_raises_when_no_existing_parent(self):
        with pytest.raises(InvalidRepositoryError, match=r"Failed to locate an existing parent directory"):
            nearest_existing_dir(Path("/non_existent/path.txt"))


class TestLocateRepoRoot:
    """Tests for locate_repo_root: the iructl repository root (marker-based, git-free)."""

    @pytest.mark.parametrize(
        ("markers", "git_dirs", "cd_subpath", "expected"),
        [
            pytest.param([""], [], "a/b/c", "", id="marker-in-ancestor"),
            pytest.param(["", "inner"], [], "inner/sub", "inner", id="nearest-marker-wins"),
            pytest.param([""], ["gitrepo"], "gitrepo/sub", None, id="stops-at-enclosing-git-tree"),
        ],
    )
    def test_marker_resolution(self, tmp_path: Path, markers, git_dirs, cd_subpath, expected):
        """Resolve to the nearest ancestor marker, bounded by the enclosing git working tree."""
        for rel in markers:
            (tmp_path / rel).mkdir(parents=True, exist_ok=True)
            (tmp_path / rel / ROOT_MARKER).touch()
        for rel in git_dirs:
            (tmp_path / rel).mkdir(parents=True, exist_ok=True)
            (tmp_path / rel / ".git").mkdir()

        if expected is None:
            with pytest.raises(InvalidRepositoryError, match=rf"does not appear to be a {APP_BRANDING} repository"):
                locate_repo_root(cd_path=tmp_path / cd_subpath)
        else:
            assert locate_repo_root(cd_path=tmp_path / cd_subpath) == tmp_path / expected

    def test_raises_without_marker(self, tmp_path: Path):
        with pytest.raises(InvalidRepositoryError, match=rf"does not appear to be a {APP_BRANDING} repository"):
            locate_repo_root(cd_path=tmp_path)

    def test_unmigrated_hint_uses_relative_mv_when_cwd_is_repo_root(self, tmp_path: Path, monkeypatch):
        root = tmp_path.resolve()
        (root / LEGACY_ROOT_MARKER).touch()
        monkeypatch.chdir(root)
        with pytest.raises(UnmigratedRepositoryError) as exc_info:
            locate_repo_root(cd_path=root / "a/b/c")
        assert exc_info.value.command.endswith(f"mv {LEGACY_ROOT_MARKER} {ROOT_MARKER}")

    def test_unmigrated_hint_uses_absolute_mv_when_shorter_than_relative(self, tmp_path: Path, monkeypatch):
        root = tmp_path.resolve()
        (root / LEGACY_ROOT_MARKER).touch()
        # A deeply nested cwd makes the relative ("../" x N) form longer than the absolute path.
        deep = root.joinpath(*(["n"] * 80))
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        with pytest.raises(UnmigratedRepositoryError) as exc_info:
            locate_repo_root(cd_path=root)
        assert exc_info.value.command.endswith(f"mv {root / LEGACY_ROOT_MARKER} {root / ROOT_MARKER}")

    def test_unmigrated_hint_quotes_paths_containing_spaces(self, tmp_path: Path, monkeypatch):
        root = tmp_path.resolve() / "legacy repo"
        root.mkdir()
        (root / LEGACY_ROOT_MARKER).touch()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(UnmigratedRepositoryError) as exc_info:
            locate_repo_root(cd_path=root)
        assert exc_info.value.command == f"mv 'legacy repo/{LEGACY_ROOT_MARKER}' 'legacy repo/{ROOT_MARKER}'"

    def test_raises_plain_invalid_error_without_legacy_marker(self, tmp_path: Path):
        with pytest.raises(InvalidRepositoryError) as exc_info:
            locate_repo_root(cd_path=tmp_path)
        assert not isinstance(exc_info.value, UnmigratedRepositoryError)

    def test_raises_when_no_existing_parent(self):
        with pytest.raises(InvalidRepositoryError, match=r"Failed to locate an existing parent directory"):
            locate_repo_root(cd_path=Path("/non_existent/path.txt"))

    def test_works_without_git(self, tmp_path: Path, monkeypatch):
        """The repo-root lookup never invokes git, so it resolves even when git is unavailable."""

        def no_git():
            raise FileNotFoundError("Failed to locate the git executable.")

        monkeypatch.setattr("iructl._git.locate_git", no_git)
        (tmp_path / ROOT_MARKER).touch()

        assert locate_repo_root(cd_path=tmp_path) == tmp_path
