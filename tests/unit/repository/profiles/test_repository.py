import random
import shutil
from pathlib import Path

import pytest

from iructl.exceptions import InvalidRepositoryError
from iructl.repository import CustomProfile, Repository


def test_init(profiles_list):
    """A CustomProfilesRepo object should be created with a list of profiles."""
    repo = Repository(profiles_list)
    assert len(repo) == len(profiles_list)
    assert repo.root is None
    for profile in profiles_list:
        assert repo[profile.id] == profile


class TestLoadPath:
    """Test cases for the load_path method"""

    @pytest.mark.usefixtures("profiles_repo", "iructl_repo_cd")
    def test_from_cwd(self):
        """Ensure loading defaults to cwd when no args are passed."""
        repo = Repository.load_path(model=CustomProfile)
        profile_count = len(list(Path.cwd().glob("**/*.mobileconfig")))
        assert profile_count > 0
        assert len(repo) == profile_count

    def test_from_path(self, profiles_repo: Path):
        """Ensure loading works from outside a repository when passed a path."""
        repo = Repository.load_path(model=CustomProfile, path=profiles_repo)
        profile_count = len(list(profiles_repo.glob("**/*.mobileconfig")))
        assert profile_count > 0
        assert len(repo) == profile_count

    def test_duplicate_ids(self, profiles_repo: Path):
        """A InvalidRepositoryError should be raised on load if there are duplicate IDs in the repository."""
        profile = next(profiles_repo.rglob("*.mobileconfig"))
        shutil.copytree(profile.parent, profile.parent.parent / "duplicate")
        with pytest.raises(InvalidRepositoryError, match=r"Duplicate member ID"):
            Repository.load_path(model=CustomProfile, path=profiles_repo)


class TestMapping:
    """Test cases for MutableMapping functionality."""

    def test_get_by_id(self, profiles_repo_obj: Repository):
        """Bracket notation with an ID should return the CustomProfile object."""
        profile_in = random.choice(list(profiles_repo_obj.values()))
        profile_out = profiles_repo_obj[profile_in.id]
        assert profile_in == profile_out

    def test_get_by_profile_path(self, profiles_repo_obj: Repository):
        """Bracket notation with a Path or str(Path) should return the CustomProfile object."""
        profile_in = random.choice(list(profiles_repo_obj.values()))

        # Test getting by profile path string
        profile_out = profiles_repo_obj[str(profile_in.profile_path)]
        assert profile_in is profile_out

        # Test getting by profile path object
        profile_out2 = profiles_repo_obj[profile_in.profile_path]
        assert profile_in is profile_out2

    def test_get_missing(self, profiles_repo_obj: Repository):
        """Bracket notation with a non-existent key should raise a KeyError."""
        with pytest.raises(KeyError):
            profiles_repo_obj["missing"]

    def test_set_without_path(self, profiles_repo_obj: Repository, custom_profile_obj: CustomProfile):
        """Setting a CustomProfile without a profile_path attribute should only update the _id_dict."""
        assert custom_profile_obj.profile.path is None
        assert custom_profile_obj.id not in profiles_repo_obj
        prev_count = len(profiles_repo_obj)
        prev_count_paths = len(profiles_repo_obj._path_dict)

        profiles_repo_obj[custom_profile_obj.id] = custom_profile_obj

        # Check that the profile was added to the repo by ID
        assert custom_profile_obj.id in profiles_repo_obj
        assert len(profiles_repo_obj) == prev_count + 1
        assert len(profiles_repo_obj._id_dict) == prev_count + 1

        # Check that the profile was not added to the repo by path
        assert len(profiles_repo_obj._path_dict) == prev_count_paths

    def test_set_with_path(self, profiles_repo_obj: Repository, custom_profile_obj_with_paths: CustomProfile):
        """Setting a CustomProfile with a profile_path attribute should only update both the _id_dict and _path_dict."""
        assert custom_profile_obj_with_paths.id not in profiles_repo_obj
        assert custom_profile_obj_with_paths.profile_path not in profiles_repo_obj
        prev_count = len(profiles_repo_obj)
        prev_count_paths = len(profiles_repo_obj._path_dict)

        profiles_repo_obj[str(custom_profile_obj_with_paths.id)] = custom_profile_obj_with_paths

        # Check that the profile was added to the repo by ID
        assert len(profiles_repo_obj) == prev_count + 1
        assert custom_profile_obj_with_paths.id in profiles_repo_obj

        # Check that the profile was added to the repo by path
        assert len(profiles_repo_obj._path_dict) == prev_count_paths + 1
        assert str(custom_profile_obj_with_paths.profile_path) in profiles_repo_obj
        assert custom_profile_obj_with_paths.profile_path in profiles_repo_obj

        # Check that the _path_dict value is a reference to the corresponding ID
        assert (
            profiles_repo_obj._path_dict[custom_profile_obj_with_paths.profile_path.resolve().parent]
            == custom_profile_obj_with_paths.id
        )
        assert profiles_repo_obj[custom_profile_obj_with_paths.id] == custom_profile_obj_with_paths
        assert profiles_repo_obj[custom_profile_obj_with_paths.profile_path] == custom_profile_obj_with_paths

    def test_set_different_id(self, profiles_repo_obj: Repository, custom_profile_obj: CustomProfile):
        """Setting a profile to a non-matching ID key should raise a ValueError."""
        with pytest.raises(ValueError, match="Key must match member ID"):
            profiles_repo_obj["different"] = custom_profile_obj

    def test_delete_without_path(self, profiles_repo_obj_without_paths: Repository):
        """Deleting a key/value without paths set should remove it from the _id_dict only."""
        profile_in = random.choice(list(profiles_repo_obj_without_paths.values()))
        assert profile_in.id in profiles_repo_obj_without_paths
        assert profile_in.id in profiles_repo_obj_without_paths._id_dict
        assert profiles_repo_obj_without_paths._path_dict == {}

        del profiles_repo_obj_without_paths[profile_in.id]

        assert profile_in.id not in profiles_repo_obj_without_paths
        assert profile_in.id not in profiles_repo_obj_without_paths._id_dict

    def test_delete_with_path(self, profiles_repo_obj: Repository):
        """Deleting a key/value with paths set should remove it from the _id_dict and _path_dict."""
        profile_in = random.choice(list(profiles_repo_obj.values()))
        assert profile_in.id in profiles_repo_obj
        assert profile_in.id in profiles_repo_obj._id_dict
        assert profile_in.profile_path in profiles_repo_obj
        assert profile_in.profile_path.resolve().parent in profiles_repo_obj._path_dict
        del profiles_repo_obj[profile_in.id]
        assert profile_in.id not in profiles_repo_obj
        assert profile_in.id not in profiles_repo_obj._id_dict
        assert profile_in.profile_path not in profiles_repo_obj
        assert profile_in.profile_path.resolve().parent not in profiles_repo_obj._path_dict

    def test_del_missing(self, profiles_repo_obj: Repository):
        """Deleting a non-existent key should raise a KeyError."""
        with pytest.raises(KeyError):
            del profiles_repo_obj["missing"]
