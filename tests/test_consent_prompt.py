"""Deployment opt-out must never wait for remote client elicitation."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def consent(monkeypatch):
    for name in (
        "DISABLE_TELEMETRY",
        "BLENDER_MCP_DISABLE_TELEMETRY",
        "MCP_DISABLE_TELEMETRY",
    ):
        monkeypatch.delenv(name, raising=False)
    path = (
        Path(__file__).resolve().parents[1]
        / "vendor/blender-mcp/src/blender_mcp/consent_prompt.py"
    )
    spec = importlib.util.spec_from_file_location("consent_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_already_answered", lambda: False)
    monkeypatch.setattr(module, "_current_consent", lambda: False)
    monkeypatch.setattr(module, "_client_supports_elicitation", lambda ctx: True)
    return module


@pytest.mark.parametrize(
    "variable",
    ["DISABLE_TELEMETRY", "BLENDER_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"],
)
@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_disabled_never_elicits(consent, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(session=object()), elicit=AsyncMock()
    )
    assert asyncio.run(consent.maybe_prompt_for_consent(ctx)) == ""
    ctx.elicit.assert_not_awaited()
    assert not consent._asked_sessions


def test_enabled_preserves_prompt(consent):
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(session=object()),
        elicit=AsyncMock(return_value=SimpleNamespace(action="cancel")),
    )
    assert asyncio.run(consent.maybe_prompt_for_consent(ctx)) == ""
    ctx.elicit.assert_awaited_once()
    assert asyncio.run(consent.maybe_prompt_for_consent(ctx)) == ""
    ctx.elicit.assert_awaited_once()
