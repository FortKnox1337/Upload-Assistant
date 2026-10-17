# ruff: noqa: S101
import asyncio
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src import trackerhandle, trackerstatus, webui_progress, webui_prompts
from src.meta import Meta
from src.webui_results import tracker_result, tracker_retry_context


@pytest.fixture
def progress(monkeypatch):
    events = []
    monkeypatch.setenv("UA_WEBUI_PROGRESS_STDOUT", "1")
    monkeypatch.setenv("UA_WEBUI_PROMPTS_STDOUT", "1")
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    return events


@pytest.mark.asyncio
async def test_retry_reports_are_tracker_scoped_redacted_and_cleaned_up(progress):
    logger = logging.getLogger("test.webui.retries")
    logger.setLevel(logging.INFO)
    original = list(logger.handlers)
    first_started, second_started = asyncio.Event(), asyncio.Event()

    async def first():
        with tracker_retry_context(logger, "FIRST"):
            first_started.set()
            await second_started.wait()
            logger.info("FIRST: [yellow]HTTP 500 error, retrying in 5s... (attempt 1/3)[/yellow]")
            await asyncio.to_thread(logger.info, "FIRST: Connection failed, retrying in 10s: https://tracker.example/?api_key=private-token")

    async def second():
        await first_started.wait()
        with tracker_retry_context(logger, "SECOND"):
            second_started.set()
            logger.info("SECOND: Request timed out, retrying in 2 seconds... (attempt 2/4)")
            await asyncio.sleep(0)

    await asyncio.gather(first(), second())
    logger.info("BACKGROUND: HTTP 500 error, retrying in 5s")
    rows = [event for event in progress if event.get("group") == "tracker"]
    assert [row["label"] for row in rows].count("FIRST") == 2
    assert [row["label"] for row in rows].count("SECOND") == 1
    assert all(row["status"] == "Retrying…" for row in rows)
    assert all("private-token" not in row["detail"] and "[yellow]" not in row["detail"] for row in rows)
    assert logger.handlers == original
    with pytest.raises(asyncio.CancelledError), tracker_retry_context(logger, "CANCELLED"):
        raise asyncio.CancelledError
    assert logger.handlers == original


def test_retry_capture_ignores_advice_and_console_only_runs(monkeypatch, progress):
    logger = logging.getLogger("test.webui.retry-advice")
    logger.setLevel(logging.INFO)
    with tracker_retry_context(logger, "TEST"):
        logger.info("TEST: Request failed; check the tracker before retrying.")
        logger.info("TEST: There are no retries remaining.")
    assert not progress
    monkeypatch.delenv("UA_WEBUI_PROGRESS_STDOUT")
    with tracker_retry_context(logger, "TEST"):
        logger.info("TEST: RETRYING UPLOAD")
    assert not progress


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["api", "http", "other", "ptp"])
async def test_retry_lifecycle_returns_to_uploaded_for_all_upload_paths(monkeypatch, progress, branch):
    name = "PASSTHEPOPCORN" if branch == "ptp" else "TEST"

    async def upload(meta, *_args):
        trackerhandle.logger.info(f"{name}: HTTP 500 error, retrying in 5s... (attempt 1/3)")
        meta.tracker_status[name]["torrent_id"] = "42"
        return True

    tracker = SimpleNamespace(tracker=name, torrent_url="https://tracker.example/torrents/", upload=upload,
                              fill_upload_form=AsyncMock(return_value=("https://tracker.example/upload", {})))
    monkeypatch.setattr(trackerhandle, "TrackerSetup", lambda **_kwargs: SimpleNamespace(trackers_enabled=lambda meta: meta.trackers))
    monkeypatch.setattr(trackerhandle, "provision_tracker_torrents", AsyncMock())
    monkeypatch.setattr(trackerhandle, "check_tracker_image_hosts", AsyncMock())
    monkeypatch.setattr(trackerhandle, "PassThePopcorn", lambda **_kwargs: tracker)
    meta = Meta(name="Example", category="MOVIE", trackers=[name], print_tracker_messages=False, print_tracker_links=False)
    meta.tracker_status = {name: {"upload": True}}
    await trackerhandle.process_trackers(
        meta, {"DEFAULT": {"smart_image_host_selection": False}}, SimpleNamespace(add_to_client=AsyncMock()),
        [name] if branch == "api" else [], {name: lambda **_kwargs: tracker},
        [name] if branch == "http" else [], [name] if branch == "other" else [],
    )
    rows = [event for event in progress if event.get("label") == name and event.get("group") == "tracker"]
    assert any(row["status"] == "Retrying…" and "HTTP 500" in row["detail"] for row in rows)
    assert rows[-1]["status"] == "Uploaded"
    assert rows[-1]["detail"] == ""
    assert rows[-1]["url"].endswith("/42")


@pytest.mark.asyncio
async def test_declined_real_tracker_checks_retain_final_skip_explanations(monkeypatch, progress):
    from src.trackers.UNIT3D.retromoviesclub import RetroMoviesClub
    from src.trackers.UNIT3D.skipthecommercials import SkipTheCommercials

    monkeypatch.setattr(trackerstatus.sys, "stdin", SimpleNamespace(closed=False))
    setup = SimpleNamespace(filter_unsupported_trackers=lambda _meta: None, check_banned_group=AsyncMock(return_value=False), get_torrent_claims=AsyncMock(return_value=False))
    monkeypatch.setattr(trackerstatus, "TrackerSetup", lambda **_kwargs: setup)
    monkeypatch.setattr(trackerstatus, "AvistaZNetworkRouter", lambda *_args: SimpleNamespace(apply=AsyncMock()))
    monkeypatch.setattr(trackerstatus, "UploadHelper", lambda _config: SimpleNamespace(prompt_yes_no=AsyncMock(return_value=False)))
    trackers = [RetroMoviesClub.__new__(RetroMoviesClub), SkipTheCommercials.__new__(SkipTheCommercials)]
    monkeypatch.setattr(trackerstatus, "tracker_class_map", {
        tracker.tracker: (lambda tracker=tracker, **_kwargs: tracker) for tracker in trackers
    })
    meta = Meta(name="Clean Hands", title="Clean Hands", category="MOVIE", year=2026, genres=["Crime"], imdb_id=123, trackers=[tracker.tracker for tracker in trackers])
    assert await trackerstatus.TrackerStatusManager({}).process_all_trackers(meta) == 0
    results = {name: tracker_result(name, status) for name, status in meta.tracker_status.items()}
    assert "2000 or earlier" in results["RETROMOVIESCLUB"]["detail"]
    assert "Only TV and documentary" in results["SKIPTHECOMMERCIALS"]["detail"]
    assert all(result["outcome"] == "Skipped" for result in results.values())
    for name, result in results.items():
        streamed = [event for event in progress if event.get("id") == f"tracker:{name}"]
        assert streamed[-1]["detail"] == result["detail"]


@pytest.mark.parametrize("debug", [False, True])
def test_already_in_client_results_do_not_change_debug_selection(progress, debug):
    from upload import _remove_client_trackers

    meta = Meta(trackers=["AITHER", "NEW"], remove_trackers=["AITHER", "NOTSELECTED"], debug=debug)
    meta.tracker_status = {"AITHER": {"upload_success": True, "uploaded_url": "https://tracker.example/old"}}
    assert _remove_client_trackers(meta) == ([] if debug else ["AITHER"])
    assert meta.trackers == (["AITHER", "NEW"] if debug else ["NEW"])
    if not debug:
        result = tracker_result("AITHER", meta.tracker_status["AITHER"])
        assert result == {"tracker": "AITHER", "outcome": "Skipped", "detail": "Already in your torrent client"}
        assert progress[-1]["status"] == "Skipped"
    else:
        assert not progress


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, post_status, matched", [(True, 302, True), (False, 302, False), (True, 500, False), (True, 302, False)])
async def test_privatehd_database_prompt_and_registration_lifecycle(monkeypatch, tmp_path, progress, answer, post_status, matched):
    import src.trackers.AVISTAZ as az
    from src.trackers.AVISTAZ.privatehd import PrivateHD

    prompts, requests = [], []
    added = False

    def respond(request):
        nonlocal added
        requests.append(request)
        if request.method == "POST":
            added = post_status == 302
            return httpx.Response(post_status, text="response")
        entries = [{"id": 38726, "imdb": "tt36271247", "tmdb": "1452110"}] if added and matched else []
        return httpx.Response(200, json={"data": entries})

    async def ask(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(webui_prompts, "_emit", prompts.append)
    monkeypatch.setattr(az, "prompt_in_thread", ask)
    monkeypatch.setattr(az.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n" if answer else "no\n"))
    webui_prompts.install_cli_ui_prompts()
    tracker = PrivateHD.__new__(PrivateHD)
    tracker.az_class = SimpleNamespace(secret_token="private-csrf-token")
    tracker.session = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    meta = Meta(category="MOVIE", title="Clean Hands", year=2026, imdb_tt="tt36271247", tmdb="1452110", base_dir=str(tmp_path), uuid="database")
    try:
        assert await tracker.get_media_code(meta) is (answer and post_status == 302 and matched)
    finally:
        await tracker.session.aclose()
    opened = [event["prompt"] for event in prompts if event["op"] == "open"]
    assert len(opened) == 1
    prompt = opened[0]
    assert prompt["kind"] == "yes_no" and prompt["default"] is False
    assert prompt["database_review"]["title"] == "Clean Hands"
    assert prompt["database_review"]["imdb"] == "tt36271247"
    assert "check_review" not in prompt
    assert len([request for request in requests if request.method == "POST"]) == int(answer)
    database = [event for event in progress if event.get("group") == "database"]
    assert database[-1]["status"] == ("matched" if matched and answer else "declined" if not answer else "failed")
    if matched and answer:
        assert database[-1]["url"] == "https://privatehd.to/movie/38726"
    assert "private-csrf-token" not in str(progress) + str(prompts)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["video", "disc", "dvd"])
async def test_existing_screenshots_skip_capture_and_publish_reuse(monkeypatch, tmp_path, progress, kind):
    from src import takescreens

    monkeypatch.setattr(takescreens, "get_image_host", AsyncMock(return_value="imgbox"))
    monkeypatch.setattr(takescreens, "cutoff", 6)
    meta = Meta(category="MOVIE", screens=6, image_list=[{"img_url": f"https://images.example/{index}.png"} for index in range(7)])
    if kind == "video":
        await takescreens.screenshots("missing.mkv", "Example", "reuse", str(tmp_path), meta)
    elif kind == "disc":
        await takescreens.disc_screenshots(meta, "Example", {}, "reuse", str(tmp_path), False)
    else:
        await takescreens.dvd_screenshots(meta, 0)
    reused = [event for event in progress if event.get("id") == "screenshots:reuse"]
    assert len(reused) == 1 and reused[0]["group"] == "images"
    assert reused[0]["current"] == 7 and reused[0]["status"] == "completed"
    assert "no additional captures needed" in reused[0]["detail"]
