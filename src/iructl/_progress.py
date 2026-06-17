"""Nested progress for batched transfers: an outer item bar with inner byte/speed sub-bars.

A single Live stacks two Progress bars so a transfer streams beneath the overall progress.
Thread-safe: concurrent transfers each add an inner task and advance the outer bar as bytes flow.
"""

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Protocol

from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)
from rich.table import Column

from iructl._console import OutputConsole
from iructl.exceptions import TransferCancelledError

__all__ = [
    "NULL_REPORTER",
    "UPLOAD_FRACTION",
    "BatchReporter",
    "StreamReporter",
    "TransferHandle",
    "batch_progress",
]

_console = OutputConsole(logging.getLogger(__name__))

# An upload counts as this fraction of its item; the metadata create/update is the remainder.
UPLOAD_FRACTION = 0.95

# Fixed layout so the outer and inner bars share the same start and end columns: a fixed-width
# description column and a fixed bar width align the two bars (their trailing columns still differ).
_DESCRIPTION_WIDTH = 30
_BAR_WIDTH = 40

# Transfer (inner) bars are indented so they read as nested under the overall (outer) bar.
_INNER_INDENT = "  "


# --- Public protocols ---


class TransferHandle(Protocol):
    """A streamed transfer's progress handle."""

    def advance(self, advanced: int) -> None:
        """Report bytes transferred; pass this as the on_progress callback."""
        ...

    def pulse(self, description: str) -> None:
        """Switch the inner bar to an indeterminate pulse with a new message.

        Shows that a no-byte-progress tail step (e.g. a metadata create/update) is still running.
        """
        ...


class StreamReporter(Protocol):
    """Opens an inner byte-progress sub-bar for a single streamed transfer.

    The resource layer holds one of these so it can report transfer progress without knowing
    about the outer bar.
    """

    def stream(self, description: str, total: int | None) -> AbstractContextManager[TransferHandle]: ...


# --- Rendering helpers ---


def _disabled(disable: bool | None) -> bool:
    """Resolve whether to suppress the bars: explicit override, else logging-to-stream / non-terminal."""
    if disable is not None:
        return disable
    return _console.logs_to_std or not _console.stdout.is_terminal


def _description_column(text_format: str) -> TextColumn:
    return TextColumn(text_format, table_column=Column(width=_DESCRIPTION_WIDTH, no_wrap=True, overflow="ellipsis"))


def _outer_progress() -> Progress:
    return Progress(
        _description_column("[bold]{task.description}"),
        BarColumn(bar_width=_BAR_WIDTH, complete_style="cyan"),
        TextColumn("[progress.percentage]{task.percentage:.0f}%"),
        console=_console.stdout,
    )


def _inner_progress() -> Progress:
    return Progress(
        _description_column("[progress.description]{task.description}"),
        BarColumn(bar_width=_BAR_WIDTH),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=_console.stdout,
    )


def _pulse_progress() -> Progress:
    return Progress(
        _description_column("[progress.description]{task.description}"),
        BarColumn(bar_width=_BAR_WIDTH),
        console=_console.stdout,
    )


# --- Transfer handles ---


class _StreamHandle:
    """Concrete TransferHandle backed by two callables from BatchReporter.stream."""

    def __init__(self, advance: Callable[[int], None], pulse: Callable[[str], None]) -> None:
        self._advance = advance
        self._pulse = pulse

    def advance(self, advanced: int) -> None:
        self._advance(advanced)

    def pulse(self, description: str) -> None:
        self._pulse(description)


class _NoopHandle:
    """A TransferHandle that renders nothing (disabled bar / NULL_REPORTER)."""

    def advance(self, advanced: int) -> None:
        del advanced

    def pulse(self, description: str) -> None:
        del description


_NOOP_HANDLE: TransferHandle = _NoopHandle()


# --- Reporters ---


class BatchReporter:
    """An outer item-count bar with transient inner byte/speed sub-bars.

    Each unit of the outer bar represents one item (e.g. one app push). Streaming a transfer adds an
    inner byte/speed bar and credits the outer bar a fraction (item_fraction) of that item as bytes
    flow; the caller lands the remaining fraction with advance_item once the item's tail step
    finishes. A lock guards the shared Progress state so concurrent streams never drop a delta.
    """

    def __init__(self, description: str, total: int, *, item_fraction: float, disable: bool) -> None:
        self._disable = disable
        self._item_fraction = item_fraction
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self.live: Live | None = None
        if disable:
            return
        self._outer = _outer_progress()
        self._inner = _inner_progress()
        self._pulse = _pulse_progress()
        self.live = Live(
            Group(self._outer, self._inner, self._pulse),
            console=_console.stdout,
            transient=True,
            refresh_per_second=12,
        )
        self._task = self._outer.add_task(description, total=total)

    def advance_item(self, amount: float = 1.0) -> None:
        """Advance the overall bar by amount items (whole or fractional)."""
        if self._disable:
            return
        with self._lock:
            self._outer.advance(self._task, amount)

    def cancel(self) -> None:
        """Signal in-flight transfers to abort at the next chunk; safe to call from any thread."""
        self._cancelled.set()

    def _check_cancel(self) -> None:
        if self._cancelled.is_set():
            raise TransferCancelledError

    @contextlib.contextmanager
    def stream(self, description: str, total: int | None) -> Iterator[TransferHandle]:
        """Add an inner byte bar; yield a handle. Byte deltas advance the outer bar by item_fraction.

        The bar stays up until the context exits, so a caller can keep it visible through a tail step
        and pulse it via handle.pulse(). The handle's advance also aborts the stream once cancelled,
        so a worker stops within one chunk of cancel() even when the bar is disabled.
        """
        if self._disable:

            def advance(delta: int) -> None:
                del delta
                self._check_cancel()

            def pulse(message: str) -> None:
                del message

            yield _StreamHandle(advance, pulse)
            return
        task = self._inner.add_task(f"{_INNER_INDENT}{description}", total=total)
        holder = self._inner
        per_byte = self._item_fraction / total if total else 0.0

        def advance(delta: int) -> None:
            self._check_cancel()
            with self._lock:
                holder.advance(task, delta)
                if per_byte:
                    self._outer.advance(self._task, delta * per_byte)

        def pulse(message: str) -> None:
            nonlocal task, holder
            with self._lock:
                holder.remove_task(task)
                task = self._pulse.add_task(f"{_INNER_INDENT}{message}", total=None)
                holder = self._pulse

        try:
            yield _StreamHandle(advance, pulse)
        finally:
            with self._lock:
                holder.remove_task(task)


class _NullReporter:
    """A StreamReporter that renders nothing: stream(...) yields a no-op handle."""

    @contextlib.contextmanager
    def stream(self, description: str, total: int | None) -> Iterator[TransferHandle]:
        del description, total
        yield _NOOP_HANDLE


NULL_REPORTER: StreamReporter = _NullReporter()


# --- Entry points ---


@contextlib.contextmanager
def batch_progress(
    description: str, total: int, *, item_fraction: float = UPLOAD_FRACTION, disable: bool | None = None
) -> Iterator[BatchReporter]:
    """Open a BatchReporter for a push/pull/sync run; suppressed when logging to a stream or non-terminal."""
    bar = BatchReporter(description, total, item_fraction=item_fraction, disable=_disabled(disable))
    if bar.live is None:
        yield bar
        return
    with bar.live:
        yield bar
