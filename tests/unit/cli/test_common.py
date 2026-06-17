import json
from typing import Annotated

import typer
from typer.testing import CliRunner

from iructl._cli import common
from iructl._cli.common import (
    ActionResponse,
    ActionType,
    BlueprintActionOutcome,
    OperationType,
    PayloadTransfer,
    ResultType,
    SyncResults,
    option_was_set,
    reject_under_kst,
)
from iructl._constants import INFO_FORMAT_ENV
from iructl.repository import InfoFormat

BLUEPRINT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
NODE_A = "11111111-1111-1111-1111-111111111111"


def _success(member_id, outcomes):
    return ActionResponse(
        id=member_id,
        action=ActionType.UPDATE,
        operation=OperationType.PUSH,
        result=ResultType.SUCCESS,
        member=None,
        blueprint_outcomes=outcomes,
    )


def test_format_report_marks_status_partial_with_serialized_outcomes(make_partial_sync_results):
    report = make_partial_sync_results().format_report(preview=True)

    assert report["status"] == "partial"
    assert report["partial"][0]["blueprint_outcomes"] == [
        {
            "blueprint_id": BLUEPRINT_A,
            "blueprint_name": None,
            "node_id": NODE_A,
            "status": "failed",
            "status_code": 404,
            "error_message": "nope",
        }
    ]


def test_format_summary_lists_failed_blueprint_under_partial(make_partial_sync_results):
    summary = make_partial_sync_results(node_id=None).format_summary()

    assert "Partial (1)" in summary
    assert f"blueprint {BLUEPRINT_A}" in summary


def test_format_summary_prefers_blueprint_name(make_partial_sync_results):
    summary = make_partial_sync_results(node_id=None, blueprint_name="Production Macs").format_summary()

    assert "blueprint Production Macs" in summary
    assert BLUEPRINT_A not in summary


def test_format_report_includes_blueprint_outcomes_when_preview():
    results = SyncResults(
        success=[
            _success("li-1", [BlueprintActionOutcome(blueprint_id=BLUEPRINT_A, node_id=None, status="assigned")]),
            _success("li-2", []),
        ]
    )

    report = results.format_report(preview=True)

    assert report["success"][0]["blueprint_outcomes"] == [
        {
            "blueprint_id": BLUEPRINT_A,
            "blueprint_name": None,
            "node_id": None,
            "status": "assigned",
            "status_code": None,
            "error_message": None,
        }
    ]
    # The key is present even when the item recorded no outcomes.
    assert report["success"][1]["blueprint_outcomes"] == []


def test_format_report_omits_blueprint_outcomes_when_not_preview():
    results = SyncResults(
        success=[_success("li", [BlueprintActionOutcome(blueprint_id=BLUEPRINT_A, node_id=None, status="assigned")])]
    )

    report = results.format_report()

    assert "blueprint_outcomes" not in report["success"][0]


def _action(result, *, transfer=PayloadTransfer.NONE):
    return ActionResponse(
        id="li",
        action=ActionType.UPDATE,
        operation=OperationType.PUSH,
        result=result,
        member=None,
        transfer=transfer,
    )


def test_format_report_includes_transfer_on_every_bucket():
    """Every entry carries a transfer value (serialized as a string), defaulting to 'none'."""
    results = SyncResults(
        success=[_action(ResultType.SUCCESS, transfer=PayloadTransfer.UPLOADED)],
        partial=[_action(ResultType.PARTIAL, transfer=PayloadTransfer.UPLOADED)],
        failure=[_action(ResultType.FAILURE)],
        skipped=[_action(ResultType.SKIPPED)],
    )

    report = results.format_report()

    assert report["success"][0]["transfer"] == "uploaded"
    assert report["partial"][0]["transfer"] == "uploaded"
    assert report["failure"][0]["transfer"] == "none"
    assert report["skipped"][0]["transfer"] == "none"
    # The value survives JSON serialization as a plain string for machine consumers.
    assert '"transfer": "uploaded"' in json.dumps(report)


def test_format_report_records_downloaded_transfer():
    results = SyncResults(success=[_action(ResultType.SUCCESS, transfer=PayloadTransfer.DOWNLOADED)])

    assert results.format_report()["success"][0]["transfer"] == "downloaded"


def test_transfer_action_verb_reads_download():
    """A TRANSFER action displays as "Download" ("Rename" when migrated); other actions capitalize their name."""

    def response(action: ActionType, transfer: PayloadTransfer = PayloadTransfer.NONE) -> ActionResponse:
        return ActionResponse(
            id="x",
            action=action,
            operation=OperationType.PULL,
            result=ResultType.SUCCESS,
            member=None,
            transfer=transfer,
        )

    assert response(ActionType.TRANSFER).verb == "Download"
    assert response(ActionType.TRANSFER, transfer=PayloadTransfer.MIGRATED).verb == "Rename"
    assert response(ActionType.UPDATE).verb == "Update"
    assert response(ActionType.CREATE).verb == "Create"


def test_reject_under_kst_treats_env_sourced_option_as_unset(monkeypatch):
    """Under kst, an env-sourced restricted option falls back to its default and reads as unset.

    pull/sync gate the info-file format on ``option_was_set(ctx, "format")``, so an ignored env
    var must leave that gate False -- otherwise it would silently force the default format.
    """
    monkeypatch.setattr(common, "IS_KST", True)

    captured = {}
    app = typer.Typer()

    @app.command()
    def run(
        ctx: typer.Context,
        format: Annotated[
            InfoFormat,
            typer.Option("--info-format", envvar=INFO_FORMAT_ENV, callback=reject_under_kst),
        ] = InfoFormat.PLIST,
    ) -> None:
        captured["value"] = format
        captured["gated"] = format if option_was_set(ctx, "format") else None

    result = CliRunner().invoke(app, [], env={INFO_FORMAT_ENV: "json"})

    assert result.exit_code == 0
    assert captured["value"] is InfoFormat.PLIST  # env var ignored, default applied
    assert captured["gated"] is None  # the gate sees the option as unset
