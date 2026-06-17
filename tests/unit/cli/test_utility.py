import hashlib
import logging
import re
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
import requests
import typer
from pydantic import TypeAdapter, ValidationError

from iructl._cli import utility
from iructl._cli.common import (
    ActionResponse,
    ActionType,
    BlueprintActionOutcome,
    ForceMode,
    OperationType,
    PayloadTransfer,
    PreparedAction,
    ResultType,
    SyncResults,
)
from iructl._cli.utility import (
    _classify_assign_error,
    _http_error_detail,
    _reconcile_blueprints,
    _reconcile_unchanged,
    _resolve_result_with_blueprints,
    api_config_prompt,
    compute_exit_code,
    do_pulls,
    do_push,
    do_pushes,
    show_blueprint_dry_run,
    show_blueprint_report,
    show_pull_report,
    show_push_report,
    show_sync_report,
    validate_output_path,
    validate_repo_path,
    warn_preview_off_if_declared,
)
from iructl._constants import APP_BRANDING, LEGACY_ROOT_MARKER, ROOT_MARKER
from iructl._diff import ChangeType
from iructl.api import CustomAppPayload
from iructl.exceptions import InvalidAppError, MissingAppInstallerError, PayloadTransferError
from iructl.repository import BlueprintAssignment, PushOutcome, RepositoryDirectory
from iructl.repository.custom_app import CustomApp, DownloadResult

# --- Shared blueprint fixtures, constants, and fakes ---
BLUEPRINT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BLUEPRINT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BLUEPRINT_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
NODE_A = "11111111-1111-1111-1111-111111111111"
# Real 400 bodies are JSON-encoded strings, so the raw text carries surrounding quotes; the
# duplicate-detection must match these as substrings rather than by exact equality.
DUPLICATE_BODY_NODE_EXCLUSIVE = b'"Assignment node can only have one of this Library Item type."'
DUPLICATE_BODY_ALREADY_ASSIGNED = b'"Library Item already exists in Assignment Node"'


def _make_http_error(status_code: int, body: bytes) -> requests.HTTPError:
    """Build an HTTPError carrying a response with the given status and raw body."""
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    return requests.HTTPError(response=response)


def _make_validation_error() -> ValidationError:
    """Build the ValidationError assign raises when a 2xx body is not a list[str]."""
    try:
        TypeAdapter(list[str]).validate_json(b"{}")
    except ValidationError as error:
        return error
    raise AssertionError("expected a ValidationError")


class FakeInfo:
    """Stand-in for an InfoFile exposing only the attributes the blueprint code reads."""

    def __init__(self, member_id: str, name: str, ensure_blueprints: list | None):
        self.id = member_id
        self.name = name
        self.ensure_blueprints = ensure_blueprints


class FakeMember:
    """Minimal MemberBase stand-in for the blueprint reconciliation paths."""

    def __init__(self, member_id, *, ensure_blueprints=None, created_id=None, raises=None):
        self.info = FakeInfo(member_id, member_id, ensure_blueprints)
        self._created_id = created_id or member_id
        self._raises = raises

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def name(self) -> str:
        return self.info.name

    def push_remote(self, config=None, *, create, payload_dir=None, other=None, reporter=None):  # noqa: ARG002
        if self._raises is not None:
            raise self._raises
        return PushOutcome(payload=FakeMember(self._created_id if create else self.info.id))

    def delete_remote(self, config=None):  # noqa: ARG002
        if self._raises is not None:
            raise self._raises
        return None

    def from_api_payload(self, payload):
        return payload


class FakeBlueprintsResource:
    """Records assign calls and simulates per-blueprint responses."""

    def __init__(self, *, duplicates=(), failures=None, validation_errors=()):
        self.assign_calls: list[tuple[str, str, str | None]] = []
        self.enter_count = 0
        self.exit_count = 0
        self._duplicates = set(duplicates)
        self._failures = dict(failures or {})  # blueprint id -> (status_code, body bytes)
        self._validation_errors = set(validation_errors)  # blueprint ids that yield an unparseable 2xx

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, *exc_info):
        self.exit_count += 1
        return False

    def assign(self, blueprint, *, library_item_id, node=None):
        self.assign_calls.append((blueprint, library_item_id, node))
        if blueprint in self._duplicates:
            raise _make_http_error(400, DUPLICATE_BODY_ALREADY_ASSIGNED)
        if blueprint in self._failures:
            status_code, body = self._failures[blueprint]
            raise _make_http_error(status_code, body)
        if blueprint in self._validation_errors:
            raise _make_validation_error()
        return [library_item_id]


@pytest.fixture(autouse=True)
def stub_blueprint_names(monkeypatch):
    """The display-only reverse name lookup hits the API; stub it off for every test."""
    monkeypatch.setattr("iructl._cli.utility.get_blueprint_name", lambda _config, _blueprint_id: None)


class TestApiConfigPrompt:
    @pytest.mark.parametrize(
        ("tenant_url", "api_token", "interactive", "input_url", "input_token", "expectation", "log_out"),
        [
            pytest.param(
                "https://test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                True,
                None,
                None,
                nullcontext(("https://test.api.iru.com", "00000000-0000-0000-0000-000000000000")),
                "",
                id="https_with_token",
            ),
            pytest.param(
                "http://test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                True,
                None,
                None,
                nullcontext(("https://test.api.iru.com", "00000000-0000-0000-0000-000000000000")),
                "",
                id="http_with_token",
            ),
            pytest.param(
                "test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                True,
                None,
                None,
                nullcontext(("https://test.api.iru.com", "00000000-0000-0000-0000-000000000000")),
                "",
                id="no_scheme_with_token",
            ),
            pytest.param(
                "a" * 3000,  # greater than max length of HttpUrl
                "00000000-0000-0000-0000-000000000000",
                True,
                None,
                None,
                pytest.raises(
                    typer.BadParameter, match=re.escape("The Tenant URL must be a valid Kandji or Iru API URL.")
                ),
                "The Tenant URL must be a valid Kandji or Iru API URL.",
                id="invalid_url_with_token",
            ),
            pytest.param(
                "https://test.api.iru.com",
                "",
                True,
                None,
                None,
                pytest.raises(typer.BadParameter, match=re.escape("The API token must be a valid UUID4 string.")),
                "The API token must be a valid UUID4 string.",
                id="https_without_token",
            ),
            pytest.param(
                None,
                None,
                True,
                "https://test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                nullcontext(("https://test.api.iru.com", "00000000-0000-0000-0000-000000000000")),
                "",
                id="prompt-for-both",
            ),
            pytest.param(
                "https://test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                False,
                None,
                None,
                nullcontext(("https://test.api.iru.com", "00000000-0000-0000-0000-000000000000")),
                "",
                id="https_with_token_non_interactive",
            ),
            pytest.param(
                None,
                None,
                False,
                "https://test.api.iru.com",
                "00000000-0000-0000-0000-000000000000",
                pytest.raises(typer.BadParameter, match=re.escape("You must provide a valid Iru Tenant API URL.")),
                "You must provide a valid Iru Tenant API URL.",
                id="no_url_non_interactive",
            ),
            pytest.param(
                "https://test.api.iru.com",
                None,
                False,
                "https://test.api.iru.com",
                None,
                pytest.raises(typer.BadParameter, match=re.escape("You must provide a valid Iru API Token")),
                "You must provide a valid Iru API Token",
                id="no_token_non_interactive",
            ),
        ],
    )
    def test_returns_config_or_raises_bad_parameter(
        self,
        monkeypatch,
        response_factory,
        caplog,
        tenant_url,
        api_token,
        interactive,
        input_url,
        input_token,
        expectation,
        log_out,
    ):
        user_prompted = False

        def fake_prompt(text, *args, **kwargs):
            nonlocal user_prompted
            user_prompted = True
            if "URL" in text:
                return input_url
            elif "Token" in text:
                return input_token
            else:
                pytest.fail("Unexpected prompt")

        def fake_requests_get(url, *args, **kwargs):
            if url == "https://test.api.iru.com/app/v1/ping":
                return response_factory(200, b'"ping"')
            return response_factory(404, {"error": "tenantNotFound"})

        monkeypatch.setattr("typer.prompt", fake_prompt)
        monkeypatch.setattr("requests.get", fake_requests_get)
        with expectation as values:
            config = api_config_prompt(tenant_url, api_token, interactive=interactive)
            assert config.url == values[0]
            assert config.api_token == values[1]

        if not interactive:
            assert user_prompted is False
        assert log_out in caplog.text


class TestValidateRepoPath:
    @pytest.mark.usefixtures("iructl_repo")
    def test_raises_when_called_outside_a_repo(self):
        # The autouse cwd is tmp_path, which is outside the repo created at tmp_path/repo.
        with pytest.raises(typer.BadParameter, match="is not a valid"):
            validate_repo_path()

    def test_returns_root_for_a_repo_path(self, iructl_repo):
        assert iructl_repo == validate_repo_path(iructl_repo)

    def test_resolves_subdirectory_to_repo_root(self, iructl_repo):
        profiles_path = iructl_repo / "profiles/subdir/Test Profile"
        assert iructl_repo == validate_repo_path(profiles_path)

    def test_returns_requested_subdir(self, iructl_repo):
        profiles_path = iructl_repo / "profiles/subdir/Test Profile"
        assert iructl_repo / RepositoryDirectory.PROFILES == validate_repo_path(
            profiles_path, RepositoryDirectory.PROFILES
        )
        assert iructl_repo / RepositoryDirectory.SCRIPTS == validate_repo_path(
            profiles_path, RepositoryDirectory.SCRIPTS
        )

    def test_raises_when_subdir_mismatches_and_validation_on(self, iructl_repo):
        profiles_path = iructl_repo / "profiles/subdir/Test Profile"
        with pytest.raises(typer.BadParameter, match="is not a valid"):
            validate_repo_path(profiles_path, RepositoryDirectory.SCRIPTS, validate_subdir=True)

    def test_warns_migration_and_exits_for_unmigrated_kst_repo(self, tmp_path: Path, caplog, monkeypatch):
        legacy_repo = tmp_path / "legacy"
        legacy_repo.mkdir()
        (legacy_repo / LEGACY_ROOT_MARKER).touch()
        monkeypatch.chdir(legacy_repo)
        with caplog.at_level(logging.WARNING), pytest.raises(typer.Exit):
            validate_repo_path(repo=legacy_repo)
        # The warning panel, the instruction, and the mv command are shown; the generic
        # "not a valid" error is not also emitted.
        assert "unmigrated kst repository" in caplog.text.lower()
        assert "To migrate, rename the marker file:" in caplog.text
        assert f"mv {LEGACY_ROOT_MARKER}" in caplog.text
        assert "is not a valid" not in caplog.text


class TestValidateOutputPath:
    repo_dir_name = "repo"

    @pytest.mark.parametrize(
        ("cd_path", "override_path", "expectation", "log_msg"),
        [
            pytest.param(
                ".",
                None,
                pytest.raises(
                    typer.BadParameter,
                    match=re.escape(f"is not an initialized {APP_BRANDING} repository."),
                ),
                f"is not an initialized {APP_BRANDING} repository.",
                id="external without override exits",
            ),
            pytest.param(
                ".",
                "profile.mobileconfig",
                pytest.raises(
                    typer.BadParameter,
                    match=re.escape(
                        f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository."
                    ),
                ),
                f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository.",
                id="external with invalid override exits",
            ),
            pytest.param(
                ".",
                f"{repo_dir_name}/profile.mobileconfig",
                pytest.raises(
                    typer.BadParameter,
                    match=re.escape(
                        f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository."
                    ),
                ),
                f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository.",
                id="external with invalid internal override exits",
            ),
            pytest.param(
                ".",
                f"{repo_dir_name}/{RepositoryDirectory.PROFILES}/subdirectory",
                nullcontext(f"{repo_dir_name}/{RepositoryDirectory.PROFILES}/subdirectory"),
                "",
                id="external with valid override returns override",
            ),
            pytest.param(
                repo_dir_name,
                None,
                nullcontext(f"{repo_dir_name}/{RepositoryDirectory.PROFILES}"),
                "",
                id="internal without override returns profile root",
            ),
            pytest.param(
                repo_dir_name,
                "profile.mobileconfig",
                pytest.raises(
                    typer.BadParameter,
                    match=re.escape(
                        f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository."
                    ),
                ),
                f"The output path must be located inside a profiles directory of a valid {APP_BRANDING} repository.",
                id="internal with invalid override exits",
            ),
        ],
    )
    @pytest.mark.usefixtures("iructl_repo")
    def test_validate_output_path(self, monkeypatch, caplog, tmp_path, cd_path, override_path, expectation, log_msg):
        """Test the resolved output path when the command is run within a repository."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.chdir(tmp_path / cd_path)
        with expectation as expected_path:
            output_path = validate_output_path(
                directory=RepositoryDirectory.PROFILES,
                override=str(tmp_path / override_path) if override_path else None,
            )
            assert output_path == tmp_path / expected_path
        if log_msg:
            assert log_msg in caplog.text

    @pytest.mark.usefixtures("iructl_repo")
    def test_defaults_into_repo_not_cwd(self, tmp_path):
        """With no override, the output path defaults into the --repo directory, not cwd.

        cwd is the non-repo tmp_path (autouse tmp_path_cd), so resolving into the repo can only
        come from the repo argument -- the regression behind the --repo output-dir bug.
        """
        output_path = validate_output_path(
            directory=RepositoryDirectory.PROFILES,
            repo=str(tmp_path / self.repo_dir_name),
        )
        assert output_path == tmp_path / self.repo_dir_name / RepositoryDirectory.PROFILES


class TestClassifyAssignError:
    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_code", "expected_message"),
        [
            pytest.param(
                _make_http_error(400, DUPLICATE_BODY_ALREADY_ASSIGNED),
                "skipped",
                None,
                None,
                id="already-assigned-400-skipped",
            ),
            pytest.param(
                _make_http_error(400, DUPLICATE_BODY_NODE_EXCLUSIVE),
                "skipped",
                None,
                None,
                id="node-exclusive-400-skipped",
            ),
            pytest.param(
                _make_http_error(400, b"Assignment node not found"),
                "failed",
                400,
                "Assignment node not found",
                id="other-400-failed",
            ),
            pytest.param(
                _make_http_error(404, b'{"details": "blueprint not found"}'),
                "failed",
                404,
                '{"details": "blueprint not found"}',
                id="404-failed-verbatim",
            ),
            pytest.param(
                _make_http_error(404, b'{"detail": "No Blueprint matches the given query."}'),
                "failed",
                404,
                "No Blueprint matches the given query",
                id="404-detail-unwrapped",
            ),
            pytest.param(
                _make_http_error(400, b'"Assignment node not found"'),
                "failed",
                400,
                "Assignment node not found",
                id="400-json-string-unwrapped",
            ),
            pytest.param(_make_http_error(500, b"server error"), "failed", 500, "server error", id="500-failed"),
            pytest.param(requests.ConnectionError("boom"), "failed", None, "ConnectionError", id="transport-failed"),
        ],
    )
    def test_maps_error_to_outcome(self, error, expected_status, expected_code, expected_message):
        outcome = _classify_assign_error(BLUEPRINT_A, NODE_A, error)
        assert outcome.blueprint_id == BLUEPRINT_A
        assert outcome.node_id == NODE_A
        assert outcome.status == expected_status
        assert outcome.status_code == expected_code
        assert outcome.error_message == expected_message


class TestHttpErrorDetail:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param({"detail": "Not found."}, "Not found", id="detail-string"),
            pytest.param(
                {"name": ["Ensure this field has no more than 50 characters."]},
                "name: Ensure this field has no more than 50 characters",
                id="field-error",
            ),
            pytest.param(
                {"name": ["Too long."], "payload": ["This field is required."]},
                "name: Too long.; payload: This field is required",
                id="multiple-fields",
            ),
            pytest.param(
                {"non_field_errors": ["The fields must make a unique set."]},
                "The fields must make a unique set",
                id="non-field-errors-drops-key",
            ),
            pytest.param(
                {"settings": {"timeout": ["A valid integer is required."]}},
                "settings: timeout: A valid integer is required",
                id="nested-serializer",
            ),
            pytest.param(["Something went wrong."], "Something went wrong", id="top-level-list"),
            pytest.param(b"Bad Request", "Bad Request", id="plain-text-body"),
        ],
    )
    def test_surfaces_flattened_response_body(self, response_factory, body, expected):
        error = requests.HTTPError(response=response_factory(400, body))
        assert _http_error_detail(error) == expected

    def test_returns_fixed_message_for_401(self, response_factory):
        # The auth gateway returns unparsable JSON for 401s; the status alone drives the message.
        malformed = b'{"message":"{"message":"Invalid Token Provided"}","request_id":"abc"}'
        error = requests.HTTPError(response=response_factory(401, malformed))
        assert _http_error_detail(error) == "Invalid or expired API token"

    def test_falls_back_to_str_when_http_error_has_no_response(self):
        error = requests.HTTPError("400 Client Error: Bad Request")
        assert _http_error_detail(error) == "400 Client Error: Bad Request"

    def test_returns_str_for_non_http_error(self):
        assert _http_error_detail(requests.ConnectionError("boom")) == "boom"


class TestReconcileBlueprints:
    def test_reconcile_blueprints_classifies_each_declared_pair(self):
        member = FakeMember(
            "li-1",
            ensure_blueprints=[
                BlueprintAssignment(blueprint=BLUEPRINT_A, node=NODE_A),
                BlueprintAssignment(blueprint=BLUEPRINT_B),
            ],
        )
        resource = FakeBlueprintsResource(failures={BLUEPRINT_B: (404, b'{"details": "missing"}')})

        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        assert resource.assign_calls == [(BLUEPRINT_A, "li-1", NODE_A), (BLUEPRINT_B, "li-1", None)]
        assert [outcome.status for outcome in outcomes] == ["assigned", "failed"]
        assert outcomes[1].error_message == '{"details": "missing"}'

    def test_reconcile_blueprints_records_unparseable_response_as_failed(self):
        member = FakeMember(
            "li-1",
            ensure_blueprints=[
                BlueprintAssignment(blueprint=BLUEPRINT_A),
                BlueprintAssignment(blueprint=BLUEPRINT_B),
            ],
        )
        resource = FakeBlueprintsResource(validation_errors={BLUEPRINT_A})

        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        # The unparsable response is failed without aborting the loop; the next pair still runs.
        assert resource.assign_calls == [(BLUEPRINT_A, "li-1", None), (BLUEPRINT_B, "li-1", None)]
        assert [outcome.status for outcome in outcomes] == ["failed", "assigned"]
        assert outcomes[0].status_code is None
        assert outcomes[0].error_message == "ValidationError"

    def test_reconcile_blueprints_resolves_names_before_assigning(self, monkeypatch):
        monkeypatch.setattr(
            "iructl._cli.utility.get_blueprint_id", lambda _config, name: {"Production Macs": BLUEPRINT_A}[name]
        )
        monkeypatch.setattr(
            "iructl._cli.utility.get_blueprint_name",
            lambda _config, blueprint_id: {BLUEPRINT_A: "Production Macs"}.get(blueprint_id),
        )
        member = FakeMember("li-1", ensure_blueprints=[BlueprintAssignment(blueprint="Production Macs", node=NODE_A)])
        resource = FakeBlueprintsResource()

        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        assert resource.assign_calls == [(BLUEPRINT_A, "li-1", NODE_A)]
        assert [(outcome.blueprint_id, outcome.blueprint_name, outcome.status) for outcome in outcomes] == [
            (BLUEPRINT_A, "Production Macs", "assigned")
        ]

    def test_reconcile_blueprints_reverse_resolves_declared_uuid_for_display(self, monkeypatch):
        monkeypatch.setattr(
            "iructl._cli.utility.get_blueprint_name",
            lambda _config, blueprint_id: {BLUEPRINT_A: "Production Macs"}.get(blueprint_id),
        )
        member = FakeMember(
            "li-1",
            ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A), BlueprintAssignment(blueprint=BLUEPRINT_B)],
        )
        resource = FakeBlueprintsResource()

        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        # An unknown UUID resolves to no name and the label falls back to the UUID.
        assert [(outcome.blueprint_name, outcome.blueprint_label) for outcome in outcomes] == [
            ("Production Macs", "Production Macs"),
            (None, BLUEPRINT_B),
        ]

    def test_reconcile_blueprints_records_unresolvable_name_as_failed(self, monkeypatch):
        def fake_get_blueprint_id(config, name):
            if name == "Missing":
                raise ValueError(f"Unable to get ID for blueprint '{name}'. Ensure the blueprint exists.")
            return name

        monkeypatch.setattr("iructl._cli.utility.get_blueprint_id", fake_get_blueprint_id)
        member = FakeMember(
            "li-1",
            ensure_blueprints=[BlueprintAssignment(blueprint="Missing"), BlueprintAssignment(blueprint=BLUEPRINT_A)],
        )
        resource = FakeBlueprintsResource()

        # The unresolvable name is failed without aborting the loop; the next pair still runs.
        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        assert resource.assign_calls == [(BLUEPRINT_A, "li-1", None)]
        assert [(outcome.blueprint_id, outcome.status) for outcome in outcomes] == [
            ("Missing", "failed"),
            (BLUEPRINT_A, "assigned"),
        ]
        assert outcomes[0].blueprint_name == "Missing"
        assert outcomes[0].status_code is None
        assert outcomes[0].error_message == "Unable to get ID for blueprint 'Missing'. Ensure the blueprint exists."

    @pytest.mark.parametrize(
        ("error", "expected_message"),
        [
            pytest.param(requests.ConnectionError("connection refused"), "ConnectionError", id="connection-error"),
            pytest.param(requests.ReadTimeout("read timed out"), "ReadTimeout", id="read-timeout"),
        ],
    )
    def test_reconcile_blueprints_classifies_resolver_request_errors(self, monkeypatch, error, expected_message):
        def fake_get_blueprint_id(config, name):
            raise error

        monkeypatch.setattr("iructl._cli.utility.get_blueprint_id", fake_get_blueprint_id)
        member = FakeMember("li-1", ensure_blueprints=[BlueprintAssignment(blueprint="Production Macs")])
        resource = FakeBlueprintsResource()

        outcomes = _reconcile_blueprints(None, resource, member, "li-1")

        assert resource.assign_calls == []
        assert [(outcome.blueprint_id, outcome.status) for outcome in outcomes] == [("Production Macs", "failed")]
        assert outcomes[0].error_message == expected_message


class TestShowBlueprintDryRun:
    def test_show_blueprint_dry_run_lists_pending_excluding_skips_and_pulls(self, caplog):
        create = FakeMember("create", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        unchanged = FakeMember("unchanged", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_B, node=NODE_A)])
        conflict = FakeMember("conflict", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_C)])
        local_repo = {"create": create, "unchanged": unchanged, "conflict": conflict}
        actions = [
            PreparedAction(
                action=ActionType.CREATE, operation=OperationType.PUSH, change=ChangeType.CREATE_LOCAL, member=create
            ),
            PreparedAction(
                action=ActionType.SKIP, operation=OperationType.SKIP, change=ChangeType.CONFLICT, member=conflict
            ),
        ]

        with caplog.at_level(logging.INFO):
            listed = show_blueprint_dry_run(local_repo, actions)

        assert listed is True
        # Push action and no-action items are reconciled; the skipped item is not.
        assert BLUEPRINT_A in caplog.text
        assert BLUEPRINT_B in caplog.text
        assert NODE_A in caplog.text
        assert BLUEPRINT_C not in caplog.text

    def test_show_blueprint_dry_run_returns_false_when_nothing_pending(self, caplog):
        plain = FakeMember("plain", ensure_blueprints=None)
        conflict = FakeMember("conflict", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        actions = [
            PreparedAction(
                action=ActionType.SKIP, operation=OperationType.SKIP, change=ChangeType.CONFLICT, member=conflict
            )
        ]

        with caplog.at_level(logging.INFO):
            listed = show_blueprint_dry_run({"plain": plain, "conflict": conflict}, actions)

        assert listed is False
        assert "Would have assigned" not in caplog.text

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("a[0]b", id="inert-tag-not-dropped"),
            pytest.param("x[/]y", id="unbalanced-tag-no-crash"),
            pytest.param("p[red]q", id="style-tag-not-applied"),
        ],
    )
    def test_show_blueprint_dry_run_escapes_markup_in_member_name(self, caplog, name):
        member = FakeMember(name, ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])

        with caplog.at_level(logging.INFO):
            listed = show_blueprint_dry_run({name: member}, actions=[])

        # The user-controlled name survives verbatim instead of being parsed
        # (or rejected) as Rich markup.
        assert listed is True
        assert name in caplog.text


class TestResolveResultWithBlueprints:
    @pytest.mark.parametrize(
        ("base", "outcomes", "expected"),
        [
            pytest.param(
                ResultType.FAILURE,
                [BlueprintActionOutcome(BLUEPRINT_A, None, "failed")],
                ResultType.FAILURE,
                id="failure-stays-failure",
            ),
            pytest.param(
                ResultType.SUCCESS,
                [BlueprintActionOutcome(BLUEPRINT_A, None, "failed")],
                ResultType.PARTIAL,
                id="success-with-failed-becomes-partial",
            ),
            pytest.param(
                ResultType.SUCCESS,
                [
                    BlueprintActionOutcome(BLUEPRINT_A, None, "assigned"),
                    BlueprintActionOutcome(BLUEPRINT_B, None, "skipped"),
                ],
                ResultType.SUCCESS,
                id="success-clean-stays-success",
            ),
        ],
    )
    def test_combines_base_with_outcomes(self, base, outcomes, expected):
        assert _resolve_result_with_blueprints(base, outcomes) is expected


class TestComputeExitCode:
    @staticmethod
    def _bare_response(result: ResultType) -> ActionResponse:
        return ActionResponse(
            id="x", action=ActionType.UPDATE, operation=OperationType.PUSH, result=result, member=None
        )

    @pytest.mark.parametrize(
        ("results", "expected"),
        [
            pytest.param(SyncResults(), 0, id="empty"),
            pytest.param(SyncResults(success=[_bare_response(ResultType.SUCCESS)]), 0, id="success-only"),
            pytest.param(SyncResults(failure=[_bare_response(ResultType.FAILURE)]), 1, id="any-failure"),
            pytest.param(SyncResults(partial=[_bare_response(ResultType.PARTIAL)]), 1, id="any-partial"),
        ],
    )
    def test_maps_results_to_exit_code(self, results, expected):
        assert compute_exit_code(results) == expected


class TestWarnPreviewOffIfDeclared:
    @pytest.mark.parametrize(
        ("declared", "expected_phrase"),
        [
            pytest.param({"absent": None}, None, id="none-declared"),
            pytest.param({"absent": None, "empty": []}, None, id="empty-lists-ignored"),
            pytest.param({"declared": [BLUEPRINT_A]}, "declared on 1 Library Item,", id="single-pluralizes-item"),
            pytest.param(
                {"absent": None, "a": [BLUEPRINT_A], "b": [BLUEPRINT_B]},
                "declared on 2 Library Items,",
                id="multiple-pluralizes-items",
            ),
        ],
    )
    def test_warns_only_when_declared(self, caplog, declared, expected_phrase):
        local_repo = {
            name: FakeMember(
                name, ensure_blueprints=None if bps is None else [BlueprintAssignment(blueprint=b) for b in bps]
            )
            for name, bps in declared.items()
        }
        with caplog.at_level(logging.WARNING):
            warn_preview_off_if_declared(local_repo)
        if expected_phrase is None:
            assert "preview mode is off" not in caplog.text
        else:
            assert caplog.text.count("preview mode is off") == 1
            assert expected_phrase in caplog.text
            assert "IRUCTL_PREVIEW=1" in caplog.text


class TestReconcileUnchanged:
    def test_reconcile_unchanged_reconciles_only_unacted_declared_items(self):
        acted = FakeMember("acted", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        unchanged = FakeMember("unchanged", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_B)])
        plain = FakeMember("plain", ensure_blueprints=None)
        local_repo = {"acted": acted, "unchanged": unchanged, "plain": plain}
        resource = FakeBlueprintsResource()
        # The acted item already has a content-op response recorded; it must not be reconciled again.
        results = SyncResults(
            success=[
                ActionResponse(
                    id="acted",
                    action=ActionType.UPDATE,
                    operation=OperationType.PUSH,
                    result=ResultType.SUCCESS,
                    member=acted,
                )
            ]
        )

        _reconcile_unchanged(None, results, local_repo, resource)

        assert resource.assign_calls == [(BLUEPRINT_B, "unchanged", None)]
        assert [response.id for response in results.success] == ["acted", "unchanged"]
        assert results.partial == []

    def test_reconcile_unchanged_skips_created_item_under_its_new_id(self):
        # A created item is re-keyed to its new Iru ID; its content-op response carries that new id,
        # so the unchanged sweep must exclude it rather than assign it a second time.
        created = FakeMember("new-iru-id", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        results = SyncResults(
            success=[
                ActionResponse(
                    id="old-local-id",
                    action=ActionType.CREATE,
                    operation=OperationType.PUSH,
                    result=ResultType.SUCCESS,
                    member=created,
                )
            ]
        )
        resource = FakeBlueprintsResource()

        _reconcile_unchanged(None, results, {"new-iru-id": created}, resource)

        assert resource.assign_calls == []

    def test_reconcile_unchanged_routes_blueprint_failure_to_partial(self):
        member = FakeMember("li", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        resource = FakeBlueprintsResource(failures={BLUEPRINT_A: (404, b"nope")})
        results = SyncResults()

        _reconcile_unchanged(None, results, {"li": member}, resource)

        assert [response.id for response in results.partial] == ["li"]
        assert results.success == []
        assert results.partial[0].action is ActionType.SKIP
        assert results.partial[0].operation is OperationType.PUSH


class TestDoPushes:
    def test_do_pushes_opens_blueprints_session_once_for_unchanged_items(self, monkeypatch):
        member = FakeMember("li", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)])
        resource = FakeBlueprintsResource()

        def fake_blueprints_resource(*args, **kwargs):
            return resource

        monkeypatch.setattr(utility, "BlueprintsResource", fake_blueprints_resource)

        results = do_pushes(config=None, local_repo={"li": member}, actions=[], preview=True)

        assert resource.enter_count == 1
        assert resource.exit_count == 1
        assert resource.assign_calls == [(BLUEPRINT_A, "li", None)]
        assert [response.id for response in results.success] == ["li"]

    def test_failed_push_is_bucketed_failure_and_order_is_preserved(self, monkeypatch):
        # A worker raising must surface as a FAILURE response, not abort the run, and results must
        # stay in action order regardless of the order workers complete in.
        def _noop_update(*args, **kwargs) -> None:
            del args, kwargs

        monkeypatch.setattr(utility, "update_local_member", _noop_update)
        members = [
            FakeMember("first"),
            FakeMember("boom", raises=RuntimeError("upload exploded")),
            FakeMember("third"),
        ]
        actions = [
            PreparedAction(
                action=ActionType.CREATE,
                operation=OperationType.PUSH,
                change=ChangeType.CREATE_LOCAL,
                member=member,
            )
            for member in members
        ]

        results = do_pushes(config=None, local_repo={}, actions=actions, preview=False)

        assert [response.id for response in results.success] == ["first", "third"]
        assert [response.id for response in results.failure] == ["boom"]
        assert results.failure[0].result is ResultType.FAILURE


class _SpyExecutor:
    """Records shutdown calls so a test can assert the queue was dropped without a blocking join."""

    def __init__(self) -> None:
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class _SpyReporter:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class TestCancelOnInterrupt:
    def test_passes_through_when_no_interrupt(self):
        executor, reporter = _SpyExecutor(), _SpyReporter()
        with utility._cancel_on_interrupt(executor, reporter):
            pass
        assert not reporter.cancelled
        assert executor.shutdown_calls == []

    def test_interrupt_cancels_reporter_drops_queue_and_aborts(self):
        executor, reporter = _SpyExecutor(), _SpyReporter()
        with pytest.raises(typer.Abort), utility._cancel_on_interrupt(executor, reporter):
            raise KeyboardInterrupt
        assert reporter.cancelled
        assert executor.shutdown_calls == [(False, True)]


class TestDoPulls:
    def test_announces_nothing_to_do_for_empty_actions(self, caplog):
        with caplog.at_level(logging.INFO, logger="iructl._cli.utility"):
            results = do_pulls(local_repo={}, actions=[])

        assert "Nothing to do." in caplog.text
        assert not results.success
        assert not results.failure
        assert not results.skipped

    def test_announce_empty_false_silences_nothing_to_do(self, caplog):
        with caplog.at_level(logging.INFO, logger="iructl._cli.utility"):
            results = do_pulls(local_repo={}, actions=[], announce_empty=False)

        assert "Nothing to do." not in caplog.text
        assert not results.success
        assert not results.failure
        assert not results.skipped

    def test_buckets_results_in_action_order_despite_out_of_order_completion(self, monkeypatch):
        # Downloads run concurrently and finish out of order; results must still be in action order.
        class _Repo(dict):
            root = Path("/tmp")

        members = [FakeMember(f"id-{i}") for i in range(4)]
        actions = [
            PreparedAction(
                action=ActionType.TRANSFER,
                operation=OperationType.PULL,
                change=ChangeType.NONE,
                member=member,
                download=DownloadResult.DOWNLOADED,
            )
            for member in members
        ]

        def fake_pull_action(action, *, payload_dir=None, force=False, reporter=None, lock=None):
            # Earlier-index items sleep longest, so completion order is the reverse of action order.
            index = int(action.member.id.removeprefix("id-"))
            time.sleep(0.02 * (len(members) - index))
            return utility._DownloadOutcome(transfer=PayloadTransfer.DOWNLOADED)

        monkeypatch.setattr(utility, "_pull_action", fake_pull_action)

        results = do_pulls(_Repo({m.id: m for m in members}), actions, payload_dir=Path("/tmp"))

        assert [response.id for response in results.success] == [member.id for member in members]


class TestPullActionErrorHandling:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(PayloadTransferError("Failed to download file from S3: boom"), id="s3-download"),
            pytest.param(OSError("[Errno 28] No space left on device"), id="disk-error"),
            pytest.param(InvalidAppError("The download URL is not set"), id="invalid-app"),
        ],
    )
    def test_download_errors_become_failed_outcome(self, error, caplog, custom_app_factory, tmp_path, monkeypatch):
        """A download error fails just that item instead of aborting the whole parallel pull."""

        def raise_error(*args, **kwargs):
            raise error

        monkeypatch.setattr(CustomApp, "download_binary", raise_error)
        app = custom_app_factory(has_audit=False, file_name="installer.pkg")
        action = PreparedAction(
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            change=ChangeType.NONE,
            member=app,
            download=DownloadResult.DOWNLOADED,
        )

        with caplog.at_level(logging.ERROR):
            outcome = utility._pull_action(action, payload_dir=tmp_path)

        assert outcome.failed is True
        assert outcome.transfer is PayloadTransfer.FAILED
        assert str(error) in caplog.text


class TestPullActionMigration:
    def test_migrate_intent_renames_legacy_binary(self, custom_app_factory, tmp_path):
        content = b"installer"
        app = custom_app_factory(
            has_audit=False, file_name="installer.pkg", file_sha256=hashlib.sha256(content).hexdigest()
        )
        legacy = tmp_path / app.info.file.payload_name
        legacy.write_bytes(content)
        action = PreparedAction(
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            change=ChangeType.NONE,
            member=app,
            download=DownloadResult.MIGRATED,
        )

        outcome = utility._pull_action(action, payload_dir=tmp_path)

        assert outcome.transfer is PayloadTransfer.MIGRATED
        assert (tmp_path / app.info.file.name).read_bytes() == content
        assert not legacy.exists()

    def test_migrate_error_becomes_failed_outcome(self, caplog, custom_app_factory, tmp_path, monkeypatch):
        def raise_error(*args, **kwargs):
            raise OSError("rename failed")

        monkeypatch.setattr(CustomApp, "download_binary", raise_error)
        app = custom_app_factory(has_audit=False, file_name="installer.pkg")
        action = PreparedAction(
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            change=ChangeType.NONE,
            member=app,
            download=DownloadResult.MIGRATED,
        )

        with caplog.at_level(logging.ERROR):
            outcome = utility._pull_action(action, payload_dir=tmp_path)

        assert outcome.failed is True
        assert outcome.transfer is PayloadTransfer.FAILED
        assert "Failed to rename" in caplog.text

    def test_finalize_migrated_transfer_reports_rename(self, caplog, custom_app_factory, tmp_path):
        """A transfer-only pull that renamed a legacy installer reports the rename, not a download."""

        class _Repo(dict):
            root: Path

        (tmp_path / ROOT_MARKER).touch()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg")
        repo = _Repo({app.id: app})
        repo.root = tmp_path
        action = PreparedAction(
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            change=ChangeType.NONE,
            member=app,
            download=DownloadResult.MIGRATED,
        )
        outcome = utility._DownloadOutcome(transfer=PayloadTransfer.MIGRATED)

        with caplog.at_level(logging.INFO):
            response = utility._finalize_pull(repo, action, outcome, payload_dir=tmp_path)

        assert response.result is ResultType.SUCCESS
        assert response.transfer is PayloadTransfer.MIGRATED
        assert f"Existing installer {app.info.file.payload_name} renamed to {app.info.file.name}" in caplog.text

    def test_finalize_migrated_transfer_rewrites_stored_installer_name(self, custom_app_factory, tmp_path):
        """The local info file is re-serialized so its stored file.name matches the renamed installer."""

        class _Repo(dict):
            root: Path

        (tmp_path / ROOT_MARKER).touch()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg")
        repo = _Repo({app.id: app})
        repo.root = tmp_path
        action = PreparedAction(
            action=ActionType.TRANSFER,
            operation=OperationType.PULL,
            change=ChangeType.NONE,
            member=app,
            download=DownloadResult.MIGRATED,
        )

        utility._finalize_pull(repo, action, utility._DownloadOutcome(transfer=PayloadTransfer.MIGRATED))

        assert app.info.file.name in app.info_path.read_text()


def _remote_app(custom_app_factory, file_name: str, sha256: str) -> CustomApp:
    """Build a CustomApp as it would arrive from the API (carrying file_url/file_size)."""
    info = custom_app_factory(has_audit=False, file_name=file_name, file_sha256=sha256).info
    payload = info.model_dump(mode="json", exclude={"sync_hash", "file"}) | {
        "sha256": info.file.sha256,
        "file_key": f"tenants/1/library/custom_apps/{info.file.name}",
        "file_url": "https://example.com/file",
        "file_size": 1234,
        "file_updated": info.updated_at or info.created_at,
        "audit_script": "",
        "preinstall_script": "",
        "postinstall_script": "",
    }
    return CustomApp.from_api_payload(CustomAppPayload.model_validate(payload))


class TestPullDownloadDeduplication:
    def test_same_target_transfers_download_once(self, custom_app_factory, monkeypatch, tmp_path):
        """Two apps sharing a content-addressed installer download it once; the second finds it in place."""
        content = b"installer"
        sha = hashlib.sha256(content).hexdigest()
        apps = [_remote_app(custom_app_factory, "installer.pkg", sha) for _ in range(2)]
        downloads = []

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            time.sleep(0.05)  # hold the lock long enough for the second action to be waiting on it
            dest.write_bytes(content)
            downloads.append(dest)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        class _Repo(dict):
            root: Path

        (tmp_path / ROOT_MARKER).touch()
        repo = _Repo({app.id: app for app in apps})
        repo.root = tmp_path
        actions = [
            PreparedAction(
                action=ActionType.TRANSFER,
                operation=OperationType.PULL,
                change=ChangeType.NONE,
                member=app,
                other=app,
                download=DownloadResult.DOWNLOADED,
            )
            for app in apps
        ]

        results = do_pulls(repo, actions, payload_dir=tmp_path)

        assert len(downloads) == 1
        assert (tmp_path / apps[0].info.file.name).read_bytes() == content
        assert sorted(response.transfer for response in results.success) == [
            PayloadTransfer.DOWNLOADED,
            PayloadTransfer.NONE,
        ]


class TestPreparePullActionsDownload:
    def test_legacy_named_binary_becomes_rename_transfer(self, custom_app_factory, tmp_path):
        content = b"installer"
        app = custom_app_factory(
            has_audit=False, file_name="installer.pkg", file_sha256=hashlib.sha256(content).hexdigest()
        )
        (tmp_path / app.info.file.payload_name).write_bytes(content)

        actions = utility.prepare_pull_actions({ChangeType.NONE: [(app, app)]}, payload_dir=tmp_path, download=True)

        assert [(action.action, action.download) for action in actions] == [
            (ActionType.TRANSFER, DownloadResult.MIGRATED)
        ]

    def test_up_to_date_suffixed_binary_needs_no_action(self, custom_app_factory, tmp_path):
        content = b"installer"
        app = custom_app_factory(
            has_audit=False, file_name="installer.pkg", file_sha256=hashlib.sha256(content).hexdigest()
        )
        (tmp_path / app.info.file.name).write_bytes(content)

        actions = utility.prepare_pull_actions({ChangeType.NONE: [(app, app)]}, payload_dir=tmp_path, download=True)

        assert actions == []


class TestDoPushBlueprintBranches:
    @staticmethod
    def _skip_local_member_write(*args, **kwargs) -> None:
        """No-op stand-in for update_local_member; its disk persistence is out of scope here."""

    @pytest.mark.parametrize(
        ("action_type", "change", "member", "resource", "expected_result", "expected_assigns", "expected_statuses"),
        [
            pytest.param(
                ActionType.CREATE,
                ChangeType.CREATE_LOCAL,
                FakeMember(
                    "local-id",
                    ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A, node=NODE_A)],
                    created_id="iru-id",
                ),
                FakeBlueprintsResource(),
                ResultType.SUCCESS,
                [(BLUEPRINT_A, "iru-id", NODE_A)],
                ["assigned"],
                id="create-assigns-with-post-create-id",
            ),
            pytest.param(
                ActionType.UPDATE,
                ChangeType.UPDATE_LOCAL,
                FakeMember("li", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)]),
                FakeBlueprintsResource(failures={BLUEPRINT_A: (404, b"nope")}),
                ResultType.PARTIAL,
                [(BLUEPRINT_A, "li", None)],
                ["failed"],
                id="update-failure-marks-partial",
            ),
            pytest.param(
                ActionType.CREATE,
                ChangeType.CREATE_LOCAL,
                FakeMember(
                    "li",
                    ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)],
                    raises=requests.HTTPError("boom"),
                ),
                FakeBlueprintsResource(),
                ResultType.FAILURE,
                [],
                [],
                id="content-failure-skips-blueprints",
            ),
            pytest.param(
                ActionType.CREATE,
                ChangeType.CREATE_LOCAL,
                FakeMember(
                    "li",
                    ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)],
                    raises=MissingAppInstallerError("installer binary missing"),
                ),
                FakeBlueprintsResource(),
                ResultType.FAILURE,
                [],
                [],
                id="missing-binary-fails-per-item",
            ),
            pytest.param(
                ActionType.DELETE,
                ChangeType.CREATE_REMOTE,
                FakeMember("li", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)]),
                FakeBlueprintsResource(),
                ResultType.SUCCESS,
                [],
                [],
                id="delete-skips-blueprints",
            ),
            pytest.param(
                ActionType.SKIP,
                ChangeType.CONFLICT,
                FakeMember("li", ensure_blueprints=[BlueprintAssignment(blueprint=BLUEPRINT_A)]),
                FakeBlueprintsResource(),
                ResultType.SKIPPED,
                [],
                [],
                id="skip-action-skips-blueprints",
            ),
        ],
    )
    def test_do_push_blueprint_branches(
        self, monkeypatch, action_type, change, member, resource, expected_result, expected_assigns, expected_statuses
    ):
        monkeypatch.setattr(utility, "update_local_member", self._skip_local_member_write)
        operation = OperationType.SKIP if action_type is ActionType.SKIP else OperationType.PUSH
        action = PreparedAction(action=action_type, operation=operation, change=change, member=member)

        response = do_push(config=None, local_repo={}, action=action, preview=True, blueprints=resource)

        assert response.result is expected_result
        assert resource.assign_calls == expected_assigns
        assert [outcome.status for outcome in response.blueprint_outcomes] == expected_statuses


class TestDoPushErrorHandling:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(MissingAppInstallerError("Unable to locate the installer binary at /p/app.pkg"), id="missing"),
            pytest.param(InvalidAppError("The sha256 of /p/app.pkg does not match the info file"), id="sha-mismatch"),
            pytest.param(PayloadTransferError("Failed to upload file to S3: boom"), id="s3-upload"),
        ],
    )
    def test_app_push_errors_become_failures_with_their_message(self, error, caplog):
        """Each custom app push error is caught and reported, not raised as a traceback."""
        member = FakeMember("li", raises=error)
        action = PreparedAction(
            action=ActionType.UPDATE, operation=OperationType.PUSH, change=ChangeType.UPDATE_REMOTE, member=member
        )

        with caplog.at_level(logging.ERROR):
            response = do_push(config=None, local_repo={}, action=action)

        assert response.result is ResultType.FAILURE
        assert response.member is member
        assert str(error) in caplog.text
        # An S3 upload that failed partway is a failed transfer; validation errors moved nothing.
        expected_transfer = PayloadTransfer.FAILED if isinstance(error, PayloadTransferError) else PayloadTransfer.NONE
        assert response.transfer is expected_transfer


class TestShowBlueprintReport:
    @staticmethod
    def _blueprint_outcome(blueprint, *, node=None, status, status_code=None, error_message=None):
        return BlueprintActionOutcome(
            blueprint_id=blueprint, node_id=node, status=status, status_code=status_code, error_message=error_message
        )

    @staticmethod
    def _blueprint_response(member_id, outcomes, result=ResultType.SUCCESS):
        return ActionResponse(
            id=member_id,
            action=ActionType.UPDATE,
            operation=OperationType.PUSH,
            result=result,
            member=None,
            blueprint_outcomes=outcomes,
        )

    def test_show_blueprint_report_splits_assigned_failed_from_skipped(self, caplog):
        results = SyncResults(
            success=[
                self._blueprint_response(
                    "li-1",
                    [
                        self._blueprint_outcome(BLUEPRINT_A, status="assigned"),
                        self._blueprint_outcome(BLUEPRINT_B, node=NODE_A, status="skipped"),
                    ],
                )
            ],
            partial=[
                self._blueprint_response(
                    "li-2",
                    [
                        self._blueprint_outcome(
                            BLUEPRINT_C, node=NODE_A, status="failed", status_code=404, error_message="nope"
                        )
                    ],
                    result=ResultType.PARTIAL,
                )
            ],
        )

        with caplog.at_level(logging.INFO):
            show_blueprint_report(results)
        text = caplog.text

        assert "Blueprint assignments" in text
        # assigned + failed land in the Assignment Summary (the only table with a Status column).
        # UUIDs are truncated to an ellipsis at the capture width, so match their leading segment.
        assert "Status" in text
        assert BLUEPRINT_A[:8] in text
        assert "assigned" in text
        assert BLUEPRINT_C[:8] in text
        assert "failed" in text
        assert "[404] nope" in text
        # the skipped pair lands in its own table with the constant reason
        assert "Skipped Assignment Summary" in text
        assert "Already Assigned" in text

    @pytest.mark.parametrize(
        "error_message",
        [
            pytest.param("a[0]b", id="inert-tag-not-dropped"),
            pytest.param("x[/]y", id="unbalanced-tag-no-crash"),
            pytest.param("p[red]q", id="style-tag-not-applied"),
        ],
    )
    def test_show_blueprint_report_escapes_markup_in_detail(self, caplog, error_message):
        results = SyncResults(
            partial=[
                self._blueprint_response(
                    "li",
                    [
                        self._blueprint_outcome(
                            BLUEPRINT_A, status="failed", status_code=400, error_message=error_message
                        )
                    ],
                    result=ResultType.PARTIAL,
                )
            ]
        )

        with caplog.at_level(logging.INFO):
            show_blueprint_report(results)

        # The user-controlled error text survives verbatim instead of being parsed
        # (or rejected) as Rich markup.
        assert "[400]" in caplog.text
        assert error_message in caplog.text

    def test_show_blueprint_report_skipped_only_omits_assignment_table(self, caplog):
        results = SyncResults(
            success=[self._blueprint_response("li", [self._blueprint_outcome(BLUEPRINT_A, status="skipped")])]
        )

        with caplog.at_level(logging.INFO):
            show_blueprint_report(results)
        text = caplog.text

        assert "Blueprint assignments" in text
        assert "Skipped Assignment Summary" in text
        assert text.count("Assignment Summary") == 1

    def test_show_blueprint_report_renders_nothing_without_outcomes(self, caplog):
        with caplog.at_level(logging.INFO):
            show_blueprint_report(SyncResults())

        assert "Blueprint assignments" not in caplog.text

    def test_show_blueprint_report_prefers_blueprint_name_over_uuid(self, caplog):
        assigned = BlueprintActionOutcome(
            blueprint_id=BLUEPRINT_A, node_id=None, status="assigned", blueprint_name="Production Macs"
        )
        skipped = BlueprintActionOutcome(
            blueprint_id=BLUEPRINT_B, node_id=None, status="skipped", blueprint_name="Staging"
        )
        results = SyncResults(success=[self._blueprint_response("li", [assigned, skipped])])

        with caplog.at_level(logging.INFO):
            show_blueprint_report(results)
        text = caplog.text

        assert "Production Macs" in text
        assert "Staging" in text
        assert BLUEPRINT_A[:8] not in text
        assert BLUEPRINT_B[:8] not in text


def _xfer(operation, transfer, *, result=ResultType.SUCCESS, action=ActionType.UPDATE):
    """Build an ActionResponse carrying a payload transfer for report tests."""
    return ActionResponse(id="li", action=action, operation=operation, result=result, member=None, transfer=transfer)


class TestShowChangeReports:
    @staticmethod
    def _changes_with_one_unchanged() -> dict:
        """A changes dict whose only entry is a single content-unchanged item."""
        changes = {change_type: [] for change_type in ChangeType}
        changes[ChangeType.NONE] = [(None, None)]
        return changes

    @staticmethod
    def _empty_changes() -> dict:
        return {change_type: [] for change_type in ChangeType}

    @staticmethod
    def _item_response(action, operation, result=ResultType.SUCCESS):
        return ActionResponse(id="li", action=action, operation=operation, result=result, member=None)

    def test_show_push_report_splits_needs_retarget_from_up_to_date(self, make_partial_sync_results, caplog):
        # The content-unchanged item failed blueprint reconcile (partial, SKIP action), so it must
        # be reported as needing a re-target rather than counted as already up to date.
        with caplog.at_level(logging.INFO):
            show_push_report(
                make_partial_sync_results(), self._changes_with_one_unchanged(), force_push=False, allow_delete=False
            )

        assert "Needs re-target" in caplog.text
        assert "Already up to date" not in caplog.text

    def test_show_sync_report_splits_needs_retarget_from_up_to_date(self, make_partial_sync_results, caplog):
        # Regression: sync once counted a content-unchanged partial as "Already up to date".
        with caplog.at_level(logging.INFO):
            show_sync_report(make_partial_sync_results(), self._changes_with_one_unchanged(), ForceMode.SKIP)

        assert "Needs re-target" in caplog.text
        assert "Already up to date" not in caplog.text

    def test_show_push_report_prints_no_changes_line(self, caplog):
        with caplog.at_level(logging.INFO):
            show_push_report(SyncResults(), self._empty_changes(), force_push=False, allow_delete=False)

        assert "Library Item changes" in caplog.text
        assert "No Library Item changes to push." in caplog.text

    def test_show_pull_report_prints_no_changes_line(self, caplog):
        with caplog.at_level(logging.INFO):
            show_pull_report(SyncResults(), self._empty_changes(), force_pull=False, allow_delete=False)

        assert "Library Item changes" in caplog.text
        assert "No Library Item changes from this pull." in caplog.text

    def test_show_sync_report_prints_no_changes_line(self, caplog):
        with caplog.at_level(logging.INFO):
            show_sync_report(SyncResults(), self._empty_changes(), ForceMode.SKIP)

        assert "Library Item changes" in caplog.text
        assert "No Library Item changes from this sync." in caplog.text

    def test_show_pull_report_includes_partial_column(self, caplog):
        pull_results = SyncResults(success=[self._item_response(ActionType.UPDATE, OperationType.PULL)])

        with caplog.at_level(logging.INFO):
            show_pull_report(pull_results, self._empty_changes(), force_pull=False, allow_delete=False)
        text = caplog.text

        assert "Updated Item Summary" in text
        assert "Partial" in text
        assert "No Library Item changes" not in text

    def test_show_sync_report_pulled_table_includes_partial_column(self, caplog):
        sync_results = SyncResults(success=[self._item_response(ActionType.UPDATE, OperationType.PULL)])

        with caplog.at_level(logging.INFO):
            show_sync_report(sync_results, self._empty_changes(), ForceMode.SKIP)
        text = caplog.text

        assert "Pulled Item Summary" in text
        assert "Partial" in text

    def test_show_push_report_counts_uploads_across_success_and_partial(self, caplog):
        # Two successful uploads plus one upload whose blueprint reconcile went partial: the
        # binary still moved, so all three count toward the transfer summary.
        push_results = SyncResults(
            success=[
                _xfer(OperationType.PUSH, PayloadTransfer.UPLOADED),
                _xfer(OperationType.PUSH, PayloadTransfer.UPLOADED, action=ActionType.CREATE),
            ],
            partial=[_xfer(OperationType.PUSH, PayloadTransfer.UPLOADED, result=ResultType.PARTIAL)],
        )

        with caplog.at_level(logging.INFO):
            show_push_report(push_results, self._empty_changes(), force_push=False, allow_delete=False)
        text = caplog.text

        assert "Installer Transfer Summary" in text
        assert re.search(r"Uploaded\s+3", text)

    def test_show_push_report_omits_transfer_summary_without_uploads(self, caplog):
        push_results = SyncResults(success=[self._item_response(ActionType.UPDATE, OperationType.PUSH)])

        with caplog.at_level(logging.INFO):
            show_push_report(push_results, self._empty_changes(), force_push=False, allow_delete=False)

        assert "Installer Transfer Summary" not in caplog.text

    def test_show_pull_report_counts_downloads(self, caplog):
        pull_results = SyncResults(
            success=[_xfer(OperationType.PULL, PayloadTransfer.DOWNLOADED, action=ActionType.SKIP) for _ in range(3)]
        )

        with caplog.at_level(logging.INFO):
            show_pull_report(pull_results, self._empty_changes(), force_pull=False, allow_delete=False)
        text = caplog.text

        assert "Installer Transfer Summary" in text
        assert re.search(r"Downloaded\s+3", text)

    def test_show_sync_report_shows_both_transfer_directions(self, caplog):
        sync_results = SyncResults(
            success=[
                _xfer(OperationType.PUSH, PayloadTransfer.UPLOADED),
                _xfer(OperationType.PULL, PayloadTransfer.DOWNLOADED, action=ActionType.SKIP),
            ]
        )

        with caplog.at_level(logging.INFO):
            show_sync_report(sync_results, self._empty_changes(), ForceMode.SKIP)
        text = caplog.text

        assert "Installer Transfer Summary" in text
        assert re.search(r"Uploaded\s+1", text)
        assert re.search(r"Downloaded\s+1", text)

    @pytest.mark.parametrize(
        ("invoke_report", "count"),
        [
            pytest.param(
                lambda results, changes: show_pull_report(results, changes, force_pull=False, allow_delete=False),
                2,
                id="pull",
            ),
            pytest.param(
                lambda results, changes: show_sync_report(results, changes, ForceMode.SKIP),
                1,
                id="sync",
            ),
        ],
    )
    def test_show_report_counts_installer_mismatches(self, invoke_report, count, caplog):
        results = SyncResults(
            skipped=[
                _xfer(OperationType.PULL, PayloadTransfer.MISMATCH, result=ResultType.SKIPPED, action=ActionType.SKIP)
                for _ in range(count)
            ]
        )

        with caplog.at_level(logging.INFO):
            invoke_report(results, self._empty_changes())

        assert re.search(rf"Installer differs\s+{count}", caplog.text)
