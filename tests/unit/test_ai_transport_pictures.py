"""A message with a picture (0.44.1): the Chat Completions form (text and image_url parts)
goes as it is to /chat/completions and in the Responses form (input_text and input_image
parts) to /responses; a plain text stays a text in both."""

from __future__ import annotations

from typing import Any

from app.ai.providers import ApiStyle
from app.ai.transport import CallOptions, Connection, OutputFormat, request_body, responses_content

PICTURE = "data:image/png;base64,AAAA"
PARTS: list[dict[str, Any]] = [
    {"type": "text", "text": "Read this"},
    {"type": "image_url", "image_url": {"url": PICTURE}},
]


def test_a_picture_goes_in_the_form_of_each_api_style() -> None:
    assert responses_content("plain") == "plain"
    assert responses_content(PARTS) == [
        {"type": "input_text", "text": "Read this"},
        {"type": "input_image", "image_url": PICTURE},
    ]
    settings = Connection("https://ai.example/v1", "a-model")
    messages: list[Any] = [
        {"role": "system", "content": "You read."},
        {"role": "user", "content": PARTS},
    ]
    responses = CallOptions(ApiStyle.RESPONSES, OutputFormat.TEXT)
    body = request_body(settings, responses, messages, None, 100)
    assert body["input"][0]["content"] == "You read."
    assert body["input"][1]["content"][1] == {"type": "input_image", "image_url": PICTURE}
    chat_style = CallOptions(ApiStyle.CHAT, OutputFormat.TEXT)
    chat = request_body(settings, chat_style, messages, None, 100)
    assert chat["messages"][1]["content"] == PARTS
