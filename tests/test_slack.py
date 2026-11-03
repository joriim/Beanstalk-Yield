import json

import pytest

from yieldsuite.config import SlackSettings
from yieldsuite.levels import Level, ProductClass
from yieldsuite.notify import slack as slack_mod
from yieldsuite.notify.slack import SlackNotifier, format_summary
from yieldsuite.pipeline import RunSummary


def _summary(**kw):
    return RunSummary("f1", 2026, Level.L4, ProductClass.ABSOLUTE, **kw)


def test_format_success_and_failure():
    text = format_summary(_summary(flagged_pct=27.4, k_factors={"a": 1.034}, mass_ok=True))
    assert "f1 / 2026" in text and "flagged 27%" in text and "a=1.034" in text and "OK" in text
    assert ":x:" in format_summary(_summary(error="L2 failed: boom"))


def test_requires_credentials():
    with pytest.raises(RuntimeError):
        SlackNotifier(SlackSettings(), env={})


def test_webhook_post(monkeypatch):
    sent = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return Resp()

    monkeypatch.setattr(slack_mod.urllib.request, "urlopen", fake_urlopen)
    settings = SlackSettings(viewer_base_url="https://maps.example.org")
    n = SlackNotifier(settings, env={"YIELDSUITE_SLACK_WEBHOOK_URL": "https://hooks.example/x"})
    n.notify_run(_summary())
    assert sent["url"] == "https://hooks.example/x"
    assert "https://maps.example.org/f1/2026" in sent["body"]["text"]
