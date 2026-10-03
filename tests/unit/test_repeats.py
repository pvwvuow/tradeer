from app.observability.repeats import RepeatFilter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_repeated_message_is_logged_once_per_window_with_a_count() -> None:
    clock = Clock()
    repeats = RepeatFilter(window_seconds=60, clock=clock)
    message = "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0"
    assert repeats.check(message) == (True, 0)
    assert [repeats.check(message) for _ in range(955)] == [(False, 0)] * 955
    assert repeats.check("another message") == (True, 0)
    clock.now = 61
    log_it, hidden = repeats.check(message)
    assert (log_it, hidden) == (True, 955)
    assert repeats.text(message, hidden).endswith("(repeated 955 more times in 60 s)")
    assert repeats.text(message, 0) == message


def test_the_filter_forgets_the_oldest_messages_when_full() -> None:
    clock = Clock()
    repeats = RepeatFilter(clock=clock)
    for index in range(300):
        clock.now = index * 0.001
        assert repeats.check(f"message {index}")[0]
    assert repeats.check("message 0")[0]  # forgotten, so logged again
    assert not repeats.check("message 299")[0]
