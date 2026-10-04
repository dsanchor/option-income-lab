"""Tests for the API Chat prompt contract (src/api_chat_instructions.py)."""

import json

import pytest

from src.api_chat_instructions import API_CHAT_INSTRUCTIONS, build_api_chat_prompt


def test_build_api_chat_prompt_embeds_message_and_history_as_untrusted_json():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    prompt = build_api_chat_prompt(history=history, message="What are my holdings?")

    assert prompt.instructions == API_CHAT_INSTRUCTIONS
    assert "UNTRUSTED_CONVERSATION_JSON" in prompt.message

    conversation_json = prompt.message.split("UNTRUSTED_CONVERSATION_JSON\n", 1)[1].split("\n\nTASK")[0]
    conversation = json.loads(conversation_json)
    assert conversation["message"] == "What are my holdings?"
    assert conversation["history"] == history


def test_build_api_chat_prompt_rejects_empty_message():
    with pytest.raises(ValueError):
        build_api_chat_prompt(history=[], message="   ")


def test_build_api_chat_prompt_defaults_are_sane():
    prompt = build_api_chat_prompt(history=[], message="hi")
    assert prompt.max_completion_tokens > 0
    assert 0 <= prompt.temperature <= 1
