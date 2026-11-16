# SPDX-License-Identifier: Apache-2.0
"""The Teams notifier posts a message card to an incoming webhook or Workflows URL (S63-25).

The URL is the credential: it is never in an error, a repr or a log; the call goes through the guarded
transport, so the organisation's egress allowlist decides; the shipped fake records what Teams would show.
"""

from __future__ import annotations

import pytest
from choregos_adapters import build, spec_of
from choregos_adapters.errors import AdapterError, UpstreamError
from choregos_adapters.fakes.teams import FakeTeamsWebhook
from choregos_adapters.notify.teams import TeamsNotifier, card
from choregos_core.domain import Message
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, use_policy

URL = "https://prod-12.westeurope.logic.azure.com/workflows/SECRETPATH123/triggers/manual/paths/invoke?sig=SECRETSIG"


def _policy(*hosts: str) -> EgressPolicy:
    return EgressPolicy(org="varga", rules=tuple(AllowedRule(h) for h in hosts), mode="enforce")


def _notifier(fake: FakeTeamsWebhook) -> TeamsNotifier:
    return TeamsNotifier(webhook_url=URL, transport=fake.transport())


async def test_a_message_is_one_adaptive_card_with_its_facts_and_its_link() -> None:
    fake = FakeTeamsWebhook()
    message = Message(
        title="morning_brief: brief",
        body="Three things today.",
        severity="warning",
        context={"task": "morning_brief", "work_item": "w-1"},
        url="https://choregos.example/w/1",
    )
    with use_policy(_policy("prod-12.westeurope.logic.azure.com")):
        await _notifier(fake).send("ignored-channel", message)
    ((url, posted),) = fake.posts
    assert url == URL
    assert posted["type"] == "message"
    content = posted["attachments"][0]["content"]
    assert posted["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert content["type"] == "AdaptiveCard"
    assert fake.texts() == [
        "morning_brief: brief",
        "Three things today.",
        "task: morning_brief",
        "work_item: w-1",
    ]
    assert content["body"][0]["color"] == "warning"
    assert content["actions"][0]["url"] == "https://choregos.example/w/1"


def test_a_long_body_is_cut_under_the_size_teams_takes() -> None:
    body = card(Message(title="t", body="x" * 50_000))["attachments"][0]["content"]["body"][1]["text"]
    assert len(body) == 4000


async def test_a_refused_or_failing_webhook_is_an_error_that_never_carries_the_url() -> None:
    fake = FakeTeamsWebhook()
    fake.fail_with(400)
    with use_policy(_policy("prod-12.westeurope.logic.azure.com")):
        with pytest.raises(UpstreamError) as refused:
            await _notifier(fake).send("", Message(title="t"))
        fake.status = 202
        fake.unreachable = True
        with pytest.raises(UpstreamError) as down:
            await _notifier(fake).send("", Message(title="t"))
    for error in (refused.value, down.value):
        assert "SECRET" not in str(error) and "workflows" not in str(error), str(error)
    assert "400" in str(refused.value) and "ConnectError" in str(down.value)
    assert "SECRET" not in repr(_notifier(fake))


async def test_the_organisations_egress_allowlist_decides_and_a_refusal_posts_nothing() -> None:
    fake = FakeTeamsWebhook()
    with use_policy(_policy("somewhere-else.example")), pytest.raises(EgressRefusedError) as refused:
        await _notifier(fake).send("", Message(title="t"))
    assert fake.posts == [], "the refused card never left"
    assert "SECRET" not in str(refused.value)
    with use_policy(_policy("prod-12.westeurope.logic.azure.com")):
        await _notifier(fake).send("", Message(title="t"))
    assert len(fake.posts) == 1


@pytest.mark.parametrize(
    "url",
    ["http://x.example/hook", "https://user:pw@x.example/hook", "not a url", "https:///nohost"],
)
def test_only_an_https_url_without_credentials_is_taken(url: str) -> None:
    with pytest.raises(AdapterError) as refused:
        TeamsNotifier(webhook_url=url)
    assert "pw" not in str(refused.value) and url not in str(refused.value)


def test_the_type_is_registered_with_its_url_as_a_secret_and_the_fake_serves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import choregos_adapters

    spec = spec_of("notify", "teams")
    assert spec is not None and spec.secret_fields == ("webhook_url",)
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    monkeypatch.setattr(choregos_adapters, "FAKE_TEAMS", None)
    built = build("notify", "teams", {"webhook_url": URL})
    assert isinstance(built, TeamsNotifier), (
        "under CHOREGOS_FAKES the real card is built over the fake webhook"
    )
    assert isinstance(choregos_adapters.FAKE_TEAMS, FakeTeamsWebhook)
    with pytest.raises(AdapterError):
        build("notify", "teams", {})


async def test_a_post_reaches_the_fake_of_the_process_under_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    import choregos_adapters

    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    monkeypatch.setattr(choregos_adapters, "FAKE_TEAMS", None)
    notifier = build("notify", "teams", {"webhook_url": URL})
    with use_policy(_policy("prod-12.westeurope.logic.azure.com")):
        await notifier.send("", Message(title="hello"))
    assert choregos_adapters.FAKE_TEAMS.texts() == ["hello"]
