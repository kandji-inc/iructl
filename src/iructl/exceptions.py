import typer


class IructlError(Exception):
    """Base exception class for all iructl related errors."""


class InvalidRepositoryError(IructlError):
    """Raised when a repository is not valid."""


class UnmigratedRepositoryError(InvalidRepositoryError):
    """Raised when a repo has a legacy ``.kst`` marker but no current marker."""

    def __init__(self, warning: str, command: str) -> None:
        super().__init__(f"{warning}\n{command}")
        self.warning = warning
        self.command = command


class GitRepositoryError(IructlError):
    """Raised when a git command fails."""


class ApiClientError(IructlError):
    """Base exception class for all ApiClient related errors."""


class PayloadTransferError(IructlError):
    """Raised when transferring an installer binary to or from S3 fails."""


class PayloadIntegrityError(PayloadTransferError):
    """Raised when downloaded bytes fail sha256 verification."""


class TransferCancelledError(IructlError):
    """Raised in a transfer worker when the run is cancelled, to abort an in-flight stream."""


class InvalidRepositoryMemberError(IructlError):
    """Raised when a repository member is invalid."""


# --- Script Exceptions ---
class InvalidScriptError(InvalidRepositoryMemberError):
    """Raised when a script or its info file is invalid."""


class MissingScriptError(InvalidRepositoryMemberError):
    """Raised when a script is missing."""


class DuplicateScriptError(InvalidRepositoryMemberError):
    """Raised when a script is duplicated."""


# --- Profile Exceptions ---
class InvalidProfileError(InvalidRepositoryMemberError):
    """Raised when a profile or its info file is invalid."""


class MissingProfileError(InvalidRepositoryMemberError):
    """Raised when a profile is missing."""


class DuplicateProfileError(InvalidRepositoryMemberError):
    """Raised when a profile is duplicated."""


# --- App Exceptions ---
class InvalidAppError(InvalidRepositoryMemberError):
    """Raised when a custom app or its info file is invalid."""


class DuplicateAppError(InvalidRepositoryMemberError):
    """Raised when a custom app script file is duplicated."""


class MissingAppInstallerError(InvalidRepositoryMemberError):
    """Raised when a custom app's installer binary is missing from the payload directory."""


# --- Info Exceptions ---
class InvalidInfoFileError(InvalidRepositoryMemberError):
    """Raised when an info file is invalid."""


class MissingInfoFileError(InvalidRepositoryMemberError):
    """Raised when an info file is missing."""


class DuplicateInfoFileError(InvalidRepositoryMemberError):
    """Raised when an info file is duplicated."""


# --- CLI Exceptions ---
class KstUnavailableOption(typer.BadParameter):
    """An iructl-era option used on the kst command line, rendered like an unknown option.

    Raised with the offending flag (e.g. ``--preview``) as its message. Subclasses
    ``BadParameter`` -- which Typer renders cleanly when raised from a parameter callback -- and
    overrides ``format_message`` to read exactly like Click's native unknown-option error,
    without importing click directly.
    """

    def format_message(self) -> str:
        return f"No such option: {self.message}"
