from collections.abc import Iterable
from typing import Any, ClassVar, override

from pygments.lexers import guess_lexer
from rich.console import RenderableType
from rich.syntax import Syntax

from iructl._constants import SCRIPTS_DIR
from iructl.api import (
    ApiConfig,
    CustomScriptPayload,
    CustomScriptsResource,
    ExecutionFrequency,
)
from iructl.exceptions import (
    DuplicateScriptError,
    InvalidScriptError,
    MissingScriptError,
)

from .content import Script
from .info import ScriptInfoFile
from .member_base import ContentChild, MemberBase, MemberConfig, child_path
from .self_service import self_service_payload


class CustomScript(MemberBase[ScriptInfoFile, CustomScriptPayload]):
    """A data model for representing a custom script."""

    directory_name: ClassVar[str] = SCRIPTS_DIR

    _config: ClassVar = MemberConfig(
        member_name="custom script",
        resource_cls=CustomScriptsResource,
        content_specs=(
            ContentChild(
                attribute="audit",
                payload_field="script",
                output_key="audit_script",
                content_cls=Script,
                required=True,
                default_content=lambda *_: Script.default_content,
                empty_error=True,
            ),
            ContentChild(
                attribute="remediation",
                payload_field="remediation_script",
                content_cls=Script,
            ),
        ),
        invalid_error=InvalidScriptError,
        duplicate_error=DuplicateScriptError,
        missing_error=MissingScriptError,
    )

    info: ScriptInfoFile
    audit: Script
    remediation: Script | None = None

    audit_path = child_path("audit")
    remediation_path = child_path("remediation")

    @property
    def has_remediation(self) -> bool:
        """Check if the remediation script is set."""
        return self.remediation is not None

    @property
    def _formatted_execution_frequency(self) -> str:
        """Format the execution frequency for display."""
        match self.info.execution_frequency:
            case ExecutionFrequency.ONCE:
                return "Once"
            case ExecutionFrequency.EVERY_15_MIN:
                return "Every 15 minutes"
            case ExecutionFrequency.EVERY_DAY:
                return "Every day"
            case ExecutionFrequency.NO_ENFORCEMENT:
                return "No enforcement"

    @override
    def _update_payload(self, config: ApiConfig) -> dict[str, Any]:
        """Build the create/update keyword arguments for the custom script resource."""
        payload: dict[str, Any] = {
            "name": self.name,
            "script": self.audit.content,
            "remediation_script": None if self.remediation is None else self.remediation.content,
            "active": self.info.active,
            "execution_frequency": self.info.execution_frequency,
            "restart": self.info.restart,
            "show_in_self_service": self.info.show_in_self_service,
        }
        payload |= self_service_payload(config, self.info)
        return payload

    @override
    def _detail_rows(self) -> Iterable[tuple[str, RenderableType]]:
        return [
            ("Active", str(self.info.active)),
            ("Execution Frequency", self._formatted_execution_frequency),
            ("Restart", str(self.info.restart)),
            ("Show in Self Service", str(self.info.show_in_self_service)),
            ("Self Service Category ID", str(self.info.self_service_category_id or "")),
            (
                "Self Service Recommended",
                str("" if self.info.self_service_recommended is None else self.info.self_service_recommended),
            ),
        ]

    @override
    def _content_rows(self) -> Iterable[tuple[str, RenderableType]]:
        remediation_content = self.remediation.content if self.remediation is not None else ""
        return [
            (
                "Audit Script",
                Syntax(self.audit.content, guess_lexer(self.audit.content), background_color="default"),
            ),
            (
                "Remediation Script",
                Syntax(remediation_content, guess_lexer(remediation_content), background_color="default"),
            ),
        ]
