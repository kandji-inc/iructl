import threading

import pytest
from rich.console import Console, Group
from rich.progress import DownloadColumn, TransferSpeedColumn

from iructl._progress import UPLOAD_FRACTION, BatchReporter, _pulse_progress
from iructl.exceptions import TransferCancelledError


def _active_bar(total: int = 2, item_fraction: float = UPLOAD_FRACTION) -> BatchReporter:
    # disable=False bypasses the terminal check so the bar's state is exercised under pytest.
    return BatchReporter("run", total, item_fraction=item_fraction, disable=False)


def _outer_completed(bar: BatchReporter) -> float:
    return bar._outer.tasks[0].completed


def _render(*renderables) -> str:
    console = Console(width=120, record=True, force_terminal=True)
    console.print(Group(*renderables))
    return console.export_text()


class TestDisabled:
    def test_disabled_bar_has_no_live(self):
        assert BatchReporter("run", 3, item_fraction=UPLOAD_FRACTION, disable=True).live is None

    def test_disabled_bar_operations_are_noops(self):
        bar = BatchReporter("run", 3, item_fraction=UPLOAD_FRACTION, disable=True)
        bar.advance_item()
        with bar.stream("upload", 100) as transfer:
            transfer.advance(50)  # must not raise even though nothing is rendered
            transfer.pulse("Finalizing")


class TestStream:
    @pytest.mark.parametrize(
        ("item_fraction", "stream_total", "advances", "extra_item", "expected"),
        [
            (0.75, 100, [50], 0.0, 0.375),  # half the bytes -> half the item's byte-fraction
            (0.75, 100, [50, 50], 0.0, 0.75),  # all bytes -> the full byte-fraction
            (0.75, 100, [100], 1 - 0.75, 1.0),  # bytes plus the tail-step remainder land a whole item
            (UPLOAD_FRACTION, None, [2048], 0.0, 0.0),  # unsized stream: per-byte undefined, outer untouched
        ],
    )
    def test_outer_advances_by_byte_fraction_and_remainder(
        self, item_fraction, stream_total, advances, extra_item, expected
    ):
        bar = _active_bar(total=2, item_fraction=item_fraction)
        with bar.stream("upload", stream_total) as transfer:
            for delta in advances:
                transfer.advance(delta)
        if extra_item:
            bar.advance_item(extra_item)
        assert _outer_completed(bar) == pytest.approx(expected)

    def test_pulse_moves_task_to_byte_free_finalize_bar(self):
        bar = _active_bar()
        with bar.stream("upload", 100) as transfer:
            transfer.advance(100)
            transfer.pulse("Finalizing app")
            assert bar._inner.tasks == []
            task = bar._pulse.tasks[0]
            assert task.total is None
            assert task.description.endswith("Finalizing app")

    def test_finalize_bar_renders_no_byte_or_speed_readout(self):
        bar = _active_bar()
        with bar.stream("upload", 100) as transfer:
            transfer.advance(100)
            transfer.pulse("Finalizing app")
            text = _render(bar._pulse)
        assert "Finalizing app" in text
        assert "?" not in text
        assert "/s" not in text

    def test_empty_finalize_bar_adds_no_line_during_upload(self):
        bar = _active_bar()
        with bar.stream("upload", 100) as transfer:
            transfer.advance(50)
            with_pulse = _render(bar._outer, bar._inner, bar._pulse)
            without_pulse = _render(bar._outer, bar._inner)
        assert with_pulse.splitlines() == without_pulse.splitlines()

    def test_finalize_progress_has_no_byte_columns(self):
        columns = _pulse_progress().columns
        assert not any(isinstance(column, (DownloadColumn, TransferSpeedColumn)) for column in columns)

    def test_inner_task_is_removed_after_the_stream(self):
        bar = _active_bar()
        with bar.stream("upload", 100):
            assert len(bar._inner.tasks) == 1
        assert len(bar._inner.tasks) == 0


class TestConcurrency:
    def test_simultaneous_streams_get_separate_inner_tasks(self):
        bar = _active_bar(total=2, item_fraction=1.0)
        with bar.stream("a", 100) as transfer_a, bar.stream("b", 100) as transfer_b:
            assert len(bar._inner.tasks) == 2
            transfer_a.advance(100)
            transfer_b.advance(50)
        assert _outer_completed(bar) == pytest.approx(1.5)  # 1.0 + 0.5

    def test_concurrent_advances_are_not_lost(self):
        # Eight threads each stream a full item; the lock must keep every delta, summing exactly.
        bar = _active_bar(total=8, item_fraction=1.0)

        def work() -> None:
            with bar.stream("x", 100) as transfer:
                for _ in range(100):
                    transfer.advance(1)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert _outer_completed(bar) == pytest.approx(8.0)


class TestCancellation:
    def test_advance_raises_once_cancelled(self):
        bar = _active_bar()
        with bar.stream("upload", 100) as transfer:
            transfer.advance(10)  # not cancelled yet -> fine
            bar.cancel()
            with pytest.raises(TransferCancelledError):
                transfer.advance(10)

    def test_advance_aborts_even_when_disabled(self):
        # A disabled bar renders nothing but must still abort an in-flight transfer on cancel.
        bar = BatchReporter("run", 1, item_fraction=UPLOAD_FRACTION, disable=True)
        bar.cancel()
        with bar.stream("upload", 100) as transfer, pytest.raises(TransferCancelledError):
            transfer.advance(10)
