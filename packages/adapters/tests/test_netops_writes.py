"""The netops writes, at the connector (S33-09, ADR 0052): each pfSense write is idempotent by the
action's key and returns what undoes it; a replay that lost its answer says it cannot know the
previous rule rather than invent it; a GitOps proposal is one policy, inside its root, on a branch.
"""

from __future__ import annotations

import json

import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes import FakeScm
from choregos_adapters.fakes.netops import FakePfSense, FakeRepo
from choregos_adapters.netops import NginxConfig, PfSenseFirewall
from choregos_adapters.netops.cilium import checked_policy
from choregos_adapters.netops.gitops import GitOpsTarget, branch_for

RULE = {"type": "pass", "interface": ["lan"], "protocol": "tcp", "source": "lan", "destination": "any",
        "destination_port": "8443", "descr": "LAN to the admin port"}  # fmt: skip


def _firewall(fake: FakePfSense) -> PfSenseFirewall:
    return PfSenseFirewall(base_url="https://fw.example.test", api_key="k", transport=fake.transport())


async def test_a_created_rule_is_created_once_per_action() -> None:
    fake = FakePfSense()
    firewall = _firewall(fake)
    first = await firewall.create_firewall_rule(RULE, action_key="action-1")
    again = await firewall.create_firewall_rule(RULE, action_key="action-1")
    other = await firewall.create_firewall_rule(RULE, action_key="action-2")
    assert (first["created"], again["created"], other["created"]) == (True, False, True)
    assert again["tracker"] == first["tracker"] != other["tracker"]
    assert fake.writes == 2 and len(fake.rules) == 5
    assert first["rule"]["descr"].startswith("LAN to the admin port [choregos:")


async def test_an_update_returns_the_previous_rule_and_a_replay_says_it_no_longer_knows_it() -> None:
    fake = FakePfSense()
    firewall = _firewall(fake)
    before = fake.state()[1]
    done = await firewall.update_firewall_rule(1700000102, {"destination_port": "9443"}, action_key="a")
    assert json.loads(done["snapshot"]) == {"rule": before, "placement": 1}
    replay = await firewall.update_firewall_rule(1700000102, {"destination_port": "9443"}, action_key="a")
    assert replay["changed"] is False and replay["snapshot"] == ""
    with pytest.raises(AdapterError, match="nothing is restored"):
        await firewall.restore_firewall_rule(replay["snapshot"])
    await firewall.restore_firewall_rule(done["snapshot"])
    assert fake.state()[1] == before
    with pytest.raises(AdapterError, match="cannot be changed"):
        await firewall.update_firewall_rule(1700000102, {"tracker": 1}, action_key="b")


async def test_a_deleted_rule_comes_back_at_its_position_and_deleting_twice_is_done() -> None:
    fake = FakePfSense()
    firewall = _firewall(fake)
    before = [r["descr"] for r in fake.rules]
    deleted = await firewall.delete_firewall_rule("1700000101")
    assert (await firewall.delete_firewall_rule("1700000101"))["deleted"] is False
    restored = await firewall.restore_firewall_rule(deleted["snapshot"])
    assert restored["restored"] == "recreated"
    assert [r["descr"] for r in fake.rules] == before and [r["id"] for r in fake.rules] == [0, 1, 2]


def test_only_one_named_cilium_policy_is_proposed() -> None:
    assert checked_policy("apiVersion: cilium.io/v2\nkind: CiliumNetworkPolicy\nmetadata: {name: a}\n")
    for manifest, refusal in (
        ("apiVersion: v1\nkind: ConfigMap\nmetadata: {name: a}\n", "only"),
        ("apiVersion: cilium.io/v2\nkind: CiliumNetworkPolicy\nmetadata: {}\n", "no metadata.name"),
        ("kind: CiliumNetworkPolicy\n---\nkind: CiliumNetworkPolicy\n", "exactly one"),
        ("a: [", "not YAML"),
    ):
        with pytest.raises(AdapterError, match=refusal):
            checked_policy(manifest)


def test_the_branch_names_the_work_item_only_in_its_own_repository() -> None:
    assert branch_for("acme/gitops", "0123456789abcdef", "acme/gitops#42") == "choregos/42-netops-01234567"
    assert branch_for("acme/gitops", "0123456789abcdef", "acme/billing#42") == "netops/0123456789ab"
    assert branch_for("acme/gitops", "0123456789abcdef", None) == "netops/0123456789ab"


async def test_an_nginx_proposal_without_a_repository_or_empty_is_refused() -> None:
    with pytest.raises(AdapterError, match="no repository"):
        await NginxConfig(FakeRepo()).propose_nginx_config(
            "nginx.conf", "events {}", "t", "r", action_key="k"
        )
    nginx = NginxConfig(FakeRepo(), gitops=GitOpsTarget(FakeScm(), "acme/gitops", "main", "nginx"))
    with pytest.raises(AdapterError, match="empty"):
        await nginx.propose_nginx_config("nginx.conf", "  ", "t", "r", action_key="k")
