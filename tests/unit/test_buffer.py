import threading

import pytest

from app.observability.buffer import RecentLogBuffer


def test_entries_get_increasing_sequence_numbers() -> None:
    buffer = RecentLogBuffer(capacity=3)
    for index in range(5):
        assert buffer.append({"message": str(index)}) == index + 1
    assert len(buffer) == 3
    assert buffer.last_seq == 5
    assert [entry["message"] for entry in buffer.last(10)] == ["2", "3", "4"]
    assert [entry["message"] for entry in buffer.last(2)] == ["3", "4"]
    assert buffer.last(0) == []
    assert [seq for seq, _ in buffer.since(3)] == [4, 5]
    assert buffer.since(5) == []


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RecentLogBuffer(capacity=0)


def test_appends_from_many_threads_are_all_counted() -> None:
    buffer = RecentLogBuffer(capacity=10_000)

    def worker() -> None:
        for _ in range(1000):
            buffer.append({"message": "x"})

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert buffer.last_seq == len(buffer) == 4000
