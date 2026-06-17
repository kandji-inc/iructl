import os
import subprocess
import sys

from tests.output import normalize_output


def _clean_env(**overrides):
    """Drop any KST_*/IRUCTL_* from the host env before adding our own."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(("KST_", "IRUCTL_"))}
    return {**base, **overrides}


_KST_ENV = {
    "_IRUCTL_APP_NAME": "kst",
    "IRUCTL_ENV_PREFIX": "KST",
    "_IRUCTL_APP_BRANDING": "Kandji Sync Toolkit",
}


def test_kst_compat_bootstrap(tmp_path):
    # The wrapper exposes three overrides: binary name (_IRUCTL_APP_NAME),
    # env-var prefix (IRUCTL_ENV_PREFIX), and product/tool branding
    # (_IRUCTL_APP_BRANDING). The Iru company name in resource-type labels
    # ("Iru Custom Profiles/Scripts", "Iru tenant", etc.) is a literal in
    # source and stays Iru regardless of the overrides.
    env = _clean_env(
        _IRUCTL_APP_NAME="kst",
        IRUCTL_ENV_PREFIX="KST",
        _IRUCTL_APP_BRANDING="Kandji Sync Toolkit",
        KST_TENANT="https://example.kandji.io",
        KST_TOKEN="abc",
    )
    out = subprocess.check_output(
        [sys.executable, "-m", "iructl", "--help"],
        env=env,
        text=True,
    )
    assert "Usage: kst" in out
    assert "Kandji Sync Toolkit" in out
    assert "Iru Custom" in out

    subprocess.check_call(
        [sys.executable, "-m", "iructl", "new", str(tmp_path / "repo")],
        env=env,
    )
    assert (tmp_path / "repo" / ".kst").exists()
    assert not (tmp_path / "repo" / ".iructl").exists()
    readme = (tmp_path / "repo" / "README.md").read_text()
    assert "# Welcome to your Kandji Sync Toolkit repository!" in readme


def test_kst_compat_strict_env_prefix(tmp_path):
    """Locks in decision #5: under kst mode, IRUCTL_TENANT is NOT a fallback.

    Wrong-prefix credentials (IRUCTL_TENANT/IRUCTL_TOKEN) are set, but KST_TENANT/
    KST_TOKEN are not. The CLI must not recognize the IRUCTL_* values: with stdin
    closed, the interactive tenant prompt fires and the command aborts.
    """
    repo = tmp_path / "repo"
    env = _clean_env(
        _IRUCTL_APP_NAME="kst",
        IRUCTL_ENV_PREFIX="KST",
        IRUCTL_TENANT="https://example.kandji.io",
        IRUCTL_TOKEN="abc",
    )
    subprocess.check_call(
        [sys.executable, "-m", "iructl", "new", str(repo)],
        env=env,
    )
    result = subprocess.run(
        [sys.executable, "-m", "iructl", "profile", "list", "--repo", str(repo)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Tenant API URL" in result.stdout


def test_kst_hides_app_command_and_root_flags():
    """Under kst, Custom Apps and the --repo/--git/--preview root flags are hidden from --help.

    The `app` subcommand is not registered, so invoking it errors. The root flags are absent
    from --help; their command-line rejection is covered by
    test_kst_rejects_hidden_options_on_command_line.
    """
    env = _clean_env(**_KST_ENV)

    help_text = normalize_output(
        subprocess.check_output([sys.executable, "-m", "iructl", "--help"], env=env, text=True)
    )
    assert "Interact with Iru Custom Apps" not in help_text
    assert "--repo" not in help_text
    assert "--git" not in help_text
    assert "--preview" not in help_text

    rejected = subprocess.run(
        [sys.executable, "-m", "iructl", "app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "No such command 'app'" in normalize_output(rejected.stdout + rejected.stderr)


def test_iructl_exposes_app_command_and_root_flags():
    """The real iructl CLI keeps Custom Apps and the root flags visible in --help."""
    help_text = normalize_output(
        subprocess.check_output([sys.executable, "-m", "iructl", "--help"], env=_clean_env(), text=True)
    )
    assert "Interact with Iru Custom Apps" in help_text
    assert "--repo" in help_text
    assert "--git" in help_text
    assert "--preview" in help_text


def test_kst_new_exposes_format_and_hides_info_format():
    """Under kst, `new` restores -f/--format and hides the iructl-era --info-format."""
    help_text = normalize_output(
        subprocess.check_output(
            [sys.executable, "-m", "iructl", "profile", "new", "--help"], env=_clean_env(**_KST_ENV), text=True
        )
    )
    # `--info-format` does not contain the token `--format`, so these checks are unambiguous.
    assert "--format" in help_text
    assert "--info-format" not in help_text


def test_kst_pull_hides_info_format_and_reformat_keeps_repo():
    """Under kst, pull hides --info-format/--reformat from --help and shows the subcommand --repo."""
    env = _clean_env(**_KST_ENV)
    help_text = normalize_output(
        subprocess.check_output([sys.executable, "-m", "iructl", "profile", "pull", "--help"], env=env, text=True)
    )
    assert "--repo" in help_text
    assert "--info-format" not in help_text
    assert "--reformat" not in help_text


def test_kst_subcommand_repo_is_shown_and_warning_free(tmp_path):
    """Under kst, the per-subcommand --repo is visible in help and used without a deprecation warning."""
    env = _clean_env(**_KST_ENV)
    help_text = normalize_output(
        subprocess.check_output([sys.executable, "-m", "iructl", "profile", "list", "--help"], env=env, text=True)
    )
    assert "--repo" in help_text

    repo = tmp_path / "repo"
    subprocess.check_call([sys.executable, "-m", "iructl", "new", str(repo)], env=env)
    result = subprocess.run(
        [sys.executable, "-m", "iructl", "profile", "list", "--local", "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "deprecated" not in normalize_output(result.stdout + result.stderr)


def test_kst_new_format_flag_creates_file_without_warning(tmp_path):
    """Under kst, `new --format json` creates the info file with no deprecation warning."""
    env = _clean_env(**_KST_ENV)
    repo = tmp_path / "repo"
    subprocess.check_call([sys.executable, "-m", "iructl", "new", str(repo)], env=env)
    result = subprocess.run(
        [sys.executable, "-m", "iructl", "profile", "new", "--name", "Fmt Profile", "--format", "json"],
        env=env,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "profiles" / "Fmt Profile" / "info.json").is_file()
    assert "deprecated" not in normalize_output(result.stdout + result.stderr)


def test_kst_rejects_hidden_options_on_command_line(tmp_path):
    """Under kst, the hidden iructl-era options fail like unknown options when typed on the CLI."""
    env = _clean_env(**_KST_ENV)
    repo = tmp_path / "repo"
    subprocess.check_call([sys.executable, "-m", "iructl", "new", str(repo)], env=env)

    cases = [
        (["--repo", str(repo), "profile", "list", "--local"], "--repo"),
        (["--preview", "profile", "list", "--local"], "--preview"),
        (["--no-git", "profile", "list", "--local"], "--no-git"),
        (["profile", "pull", "--info-format", "json"], "--info-format"),
        (["profile", "pull", "--reformat"], "--reformat"),
    ]
    for args, flag in cases:
        result = subprocess.run(
            [sys.executable, "-m", "iructl", *args],
            env=env,
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        combined = normalize_output(result.stdout + result.stderr)
        assert result.returncode == 2, f"{args}: exit {result.returncode}\n{combined}"
        assert f"No such option: {flag}" in combined, f"{args}: {combined}"


def test_kst_ignores_env_for_hidden_options(tmp_path):
    """Under kst, an env var for a hidden option is ignored; the option falls back to its default."""
    env = _clean_env(**_KST_ENV)
    repo = tmp_path / "repo"
    subprocess.check_call([sys.executable, "-m", "iructl", "new", str(repo)], env=env)

    # KST_INFO_FORMAT is ignored -> `new` uses the default plist, not json. KST_PREVIEW/
    # KST_GIT_ENABLED are tolerated (no error) rather than driving the hidden options.
    result = subprocess.run(
        [sys.executable, "-m", "iructl", "profile", "new", "--name", "EnvFmt"],
        env={**env, "KST_INFO_FORMAT": "json", "KST_PREVIEW": "1", "KST_GIT_ENABLED": "false"},
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "profiles" / "EnvFmt" / "info.plist").is_file()
    assert not (repo / "profiles" / "EnvFmt" / "info.json").exists()


def test_kst_ignores_repo_config_file(tmp_path):
    """Under kst, config in the repo marker is ignored; the same content drives the default on iructl."""
    env = _clean_env(**_KST_ENV)
    repo = tmp_path / "repo"
    subprocess.check_call([sys.executable, "-m", "iructl", "new", str(repo)], env=env)
    (repo / ".kst").write_text("info_format: json\n")

    result = subprocess.run(
        [sys.executable, "-m", "iructl", "profile", "new", "--name", "CfgProfile"],
        env=env,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "profiles" / "CfgProfile" / "info.plist").is_file()
    assert not (repo / "profiles" / "CfgProfile" / "info.json").exists()
