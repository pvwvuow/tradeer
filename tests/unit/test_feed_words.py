"""The channel feed's chat lines in Persian (0.44.x): the known lines are translated with
their parts (what is missing, the side), prices stay as written, anything else is kept."""

from __future__ import annotations

from app.ui.feed_words import feed_line_fa


def test_the_feed_lines_read_in_persian() -> None:
    waiting = "waiting for the rest of the signal (no stop loss, target yet); the AI reads it now"
    persian = "منتظر بقیه‌ی سیگنال (هنوز حد ضرر، تارگت ندارد)؛ AI الان می‌خواندش"
    assert feed_line_fa(waiting) == persian
    assert feed_line_fa("2 messages joined into one signal") == "2 پیام یک سیگنال شد"
    gone = "the signal stayed incomplete (no target), nothing done"
    assert feed_line_fa(gone) == "سیگنال ناقص ماند (تارگت نداشت)، کاری انجام نشد"
    signal = "XAUUSD sell 4190 sl 4200 tp 4170"
    read = f"the AI read the picture as: {signal} (check the numbers on the card)"
    assert feed_line_fa(read) == f"AI عکس را این‌طور خواند: {signal} (عددها را روی کارت چک کنید)"
    wrong = "the stop loss or a target is on the wrong side for a sell"
    assert feed_line_fa(wrong) == "حد ضرر یا تارگت برای فروش در سمت اشتباه است"
    assert feed_line_fa("something new") == "something new"
