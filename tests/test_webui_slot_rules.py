"""Slot decisions remain actionable in the GUI, including filtered-out conflicts."""

# ruff: noqa: S101
import asyncio
import copy
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import trackerstatus, webui_progress, webui_prompts
from src.meta import Meta
from src.slot_rules.models import SlotDecision, Verdict
from src.uphelper import UploadHelper
from web_ui import server


def make_decision(verdict=Verdict.BLOCK, tracker="AITHER"):
    return SlotDecision(
        verdict=verdict, tracker=tracker, reason="Another release holds this slot", rules_date=date(2026, 10, 15),
        slot="1080p WEB-DL", why="One release is allowed in this slot", notes=["Review the tracker rules before overriding this recommendation"],
        conflicts=[{"name": "Example S01 1080p WEB-DL [GROUP]", "link": "https://tracker.example/torrents/42", "size": "2 GiB"}],
    )


def make_helper(monkeypatch, *, answer=False, show_difference=True):
    events, prompts = [], []
    helper = UploadHelper({"DEFAULT": {"show_dupe_size_diff": show_difference}})
    helper.tracker_class_map = {"AITHER": lambda **_kwargs: SimpleNamespace(get_name=AsyncMock(return_value=None))}
    monkeypatch.setattr(webui_progress, "_emit", events.append)

    async def ask(question, *, default=False):
        prompts.append({"question": question, "default": default, "details": copy.deepcopy(webui_prompts._details.get())})
        return answer

    monkeypatch.setattr(helper, "prompt_yes_no", ask)
    return helper, events, prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, basic_dupes, default", [(Verdict.BLOCK, [], False), (Verdict.REVIEW, [], False), (Verdict.ALLOW, ["Basic duplicate"], True)])
async def test_slot_review_uses_upstream_defaults_and_structured_conflicts(monkeypatch, verdict, basic_dupes, default):
    helper, events, prompts = make_helper(monkeypatch)
    meta = Meta(name="Our release", source_size=1024**3)
    skipped, _ = await helper.dupe_check(basic_dupes, meta, "AITHER", make_decision(verdict))
    assert skipped is True  # Explicit No remains authoritative, even for ALLOW.
    prompt = prompts[0]
    assert prompt["default"] is default
    review = prompt["details"]["duplicate_review"]
    assert review["kind"] == ("potential" if basic_dupes else "slot")
    assert review["position"] == 1
    assert review["upload_name"] == "Our release"
    decision = review["slot_review"]
    assert decision["verdict"] == verdict.value
    assert decision["rules_date"] == "2026-10-15"
    assert decision["slot"] == "1080p WEB-DL"
    assert decision["why"] and decision["notes"]
    assert decision["entries"][0] == {
        "name": "Example S01 1080p WEB-DL [GROUP]", "url": "https://tracker.example/torrents/42", "size": 2 * 1024**3,
        "difference": {"mb": 1024, "percent": 100},
    }
    if not basic_dupes:
        assert review["entries"][0]["name"] == decision["entries"][0]["name"]
    assert [event["status"] for event in events if event["group"] == "duplicate_review"] == ["queued", "reviewing", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, skipped", [(Verdict.ALLOW, False), (Verdict.BLOCK, True), (Verdict.REVIEW, False)])
async def test_unattended_slot_recommendations_are_published_without_prompting(monkeypatch, verdict, skipped):
    helper, events, prompts = make_helper(monkeypatch)
    actual, _ = await helper.dupe_check([], Meta(name="Our release", unattended=True), "AITHER", make_decision(verdict))
    assert actual is skipped
    assert prompts == []
    recommendation = next(event for event in events if event["group"] == "slot_review")
    assert recommendation["slot_review"]["verdict"] == verdict.value
    assert not any(event["status"] in ("queued", "reviewing") for event in events)
    state = {}
    server._apply_progress_event(state, json.loads(json.dumps(recommendation)))
    assert state["progress"][recommendation["id"]]["slot_review"] == recommendation["slot_review"]


def test_slot_payload_preserves_names_and_redacts_secrets(monkeypatch):
    helper, _events, _prompts = make_helper(monkeypatch, show_difference=False)
    decision = make_decision()
    decision.reason = 'Request https://tracker.example/api?api_key=private-secret\n{"token":"private-token","error":"Review needed"}'
    decision.conflicts[0]["link"] = "https://tracker.example/?passkey=private-link"
    review = helper._build_slot_review(decision, Meta(source_size=1024**3))
    text = json.dumps(review)
    assert all(secret not in text for secret in ("private-secret", "private-token", "private-link"))
    assert review["entries"][0]["name"].endswith("[GROUP]")
    assert "url" not in review["entries"][0]
    assert "difference" not in review["entries"][0]
    assert review["show_size_difference"] is False


@pytest.mark.asyncio
async def test_slot_only_reviews_are_counted_while_another_tracker_waits(monkeypatch):
    helper, events, prompts = make_helper(monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()

    async def ask(question, *, default=False):
        assert default is False
        if "FIRST" in question:
            started.set()
            await release.wait()
        return True

    monkeypatch.setattr(helper, "prompt_yes_no", ask)
    first = asyncio.create_task(helper.dupe_check([], Meta(name="First"), "FIRST", make_decision(tracker="FIRST")))
    await asyncio.wait_for(started.wait(), timeout=2)
    second = asyncio.create_task(helper.dupe_check([], Meta(name="Second"), "SECOND", make_decision(tracker="SECOND")))
    await asyncio.sleep(0)
    try:
        assert any(event["label"] == "SECOND" and event["status"] == "queued" for event in events)
    finally:
        release.set()
        await asyncio.gather(first, second)
    assert [event["current"] for event in events if event["status"] == "reviewing"] == [1, 2]
    assert prompts == []


@pytest.mark.asyncio
async def test_slot_skip_reason_is_present_in_tracker_results(monkeypatch):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    setup = SimpleNamespace(filter_unsupported_trackers=lambda _meta: None, trackers_enabled=lambda meta: meta.trackers, check_banned_group=AsyncMock(return_value=False), get_torrent_claims=AsyncMock(return_value=False))
    monkeypatch.setattr(trackerstatus, "TrackerSetup", lambda **_kwargs: setup)
    monkeypatch.setattr(trackerstatus, "AvistaZNetworkRouter", lambda *_args: SimpleNamespace(apply=AsyncMock()))
    def factory(**_kwargs):
        return SimpleNamespace(tracker="AITHER", banned_groups=[], search_existing=AsyncMock(return_value=[]), get_name=AsyncMock(return_value=None))
    monkeypatch.setattr(trackerstatus, "tracker_class_map", {"AITHER": factory})
    monkeypatch.setattr(trackerstatus, "DupeChecker", lambda _config: SimpleNamespace(filter_dupes=AsyncMock(return_value=[])))
    monkeypatch.setattr(trackerstatus, "evaluate_slots", AsyncMock(return_value=make_decision()))
    meta = Meta(category="TV", name="Our release", trackers=["AITHER"], unattended=True, imdb_id=123)
    assert await trackerstatus.TrackerStatusManager({}).process_all_trackers(meta) == 0
    assert meta.tracker_status["AITHER"]["skip_reason"] == "Slot rules: Another release holds this slot"
    assert any(event["status"] == "Skipped" and event["detail"] == "Slot rules: Another release holds this slot" for event in events if event.get("group") == "tracker")
