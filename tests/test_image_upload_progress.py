"""Image retry progress follows the real upload decisions without contacting hosts."""

# ruff: noqa: S101

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src import uploadscreens, webui_progress
from src.meta import Meta


@pytest.fixture
def image_run(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(webui_progress, "_emit", events.append)
    monkeypatch.setattr(uploadscreens, "screenshots_dir", lambda *_args: tmp_path)
    real_sleep = asyncio.sleep

    async def no_delay(_delay):
        await real_sleep(0)

    monkeypatch.setattr(uploadscreens.asyncio, "sleep", no_delay)

    async def run(upload, *, hosts=("imgbox",), count=2):
        for index in range(count):
            (tmp_path / f"image-{index + 1}.png").write_bytes(b"image")
        meta = Meta(base_dir=str(tmp_path), uuid="image-progress", imghost=hosts[0])
        config = {"DEFAULT": {**{f"img_host_{index + 1}": host for index, host in enumerate(hosts)}, "image_upload_delay": 0}, "TRACKERS": {}}
        monkeypatch.setattr(uploadscreens, "upload_image_task", upload)
        result = await uploadscreens._upload_screens(config, meta, count, 1, 0, count, [], {})
        return result, events

    return run


def uploaded(args):
    url = f"https://images.example/{Path(args[0]).name}"
    return {"status": "success", "img_url": url, "raw_url": url, "web_url": url}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["response", "connection", "timeout"])
async def test_retry_reason_arrives_before_recovery_and_clears_on_success(image_run, failure):
    calls = 0

    async def upload(args):
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure == "connection":
                raise httpx.ConnectError("")
            if failure == "timeout":
                raise TimeoutError
            return {"status": "failed", "reason": "Connection failed"}
        return uploaded(args)

    (images, count), events = await image_run(upload, count=1)
    retry = next(event for event in events if event["status"] == "retrying")
    assert "Connection" in retry["detail"]
    assert "Image 1 of 1 (imgbox)" in retry["detail"]
    assert "Retrying 1/3 in" in retry["detail"]
    assert retry["current"] == 0
    assert count == len(images) == 1
    assert calls == 2
    assert len({event["id"] for event in events}) == 1
    assert events[-1]["status"] == "completed"
    assert events[-1]["detail"] == "1 of 1 images uploaded successfully"


@pytest.mark.asyncio
async def test_failed_images_never_count_as_uploaded_or_show_full_progress(image_run):
    attempts = 0

    async def upload(args):
        nonlocal attempts
        if Path(args[0]).name == "image-2.png":
            attempts += 1
            return {"status": "failed", "reason": "Connection failed"}
        return uploaded(args)

    (images, count), events = await image_run(upload)
    assert count == len(images) == 1
    assert attempts == 4
    assert events[-1]["status"] == "failed"
    assert events[-1]["current"] == 1
    assert events[-1]["total"] == 2
    assert "Connection failed" in events[-1]["detail"]
    assert "No more image hosts available" in events[-1]["detail"]
    assert not any(event["status"] == "completed" for event in events)


@pytest.mark.asyncio
async def test_fallback_hosts_share_one_bar_and_preserve_partial_successes(image_run):
    calls = []

    async def upload(args):
        name, host = Path(args[0]).name, args[1]
        calls.append((name, host))
        if name == "image-2.png" and host != "ptscreens":
            return {"status": "failed", "reason": "Missing API key"}
        return uploaded(args)

    (images, count), events = await image_run(upload, hosts=("imgbox", "imgbb", "ptscreens"))
    assert count == len(images) == 2
    assert calls == [("image-1.png", "imgbox"), ("image-2.png", "imgbox"), ("image-2.png", "imgbb"), ("image-2.png", "ptscreens")]
    assert len({event["id"] for event in events}) == 1
    switches = [event for event in events if "Switching to" in event["detail"]]
    assert len(switches) == 2
    assert all(event["status"] == "retrying" and event["current"] == 1 and event["total"] == 2 for event in switches)
    assert events[-1]["current"] == events[-1]["total"] == 2
    assert events[-1]["status"] == "completed"
    assert events[-1]["detail"] == "2 of 2 images uploaded successfully"


@pytest.mark.asyncio
async def test_another_image_finishing_does_not_hide_an_active_retry(image_run, monkeypatch):
    other_finished = asyncio.Event()
    attempts = 0
    events = []

    def emit(event):
        events.append(event)
        if event.get("current") == 1 and event["status"] == "retrying":
            other_finished.set()

    async def wait_for_other_image(_delay):
        await asyncio.wait_for(other_finished.wait(), timeout=2)

    async def upload(args):
        nonlocal attempts
        if Path(args[0]).name == "image-1.png":
            attempts += 1
            if attempts == 1:
                return {"status": "failed", "reason": "Connection failed"}
        return uploaded(args)

    monkeypatch.setattr(webui_progress, "_emit", emit)
    monkeypatch.setattr(uploadscreens.asyncio, "sleep", wait_for_other_image)
    await image_run(upload)
    retry_during_success = next(event for event in events if event["current"] == 1)
    assert retry_during_success["status"] == "retrying"
    assert "Connection failed" in retry_during_success["detail"]
    assert events[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_image_host_error_details_are_redacted_before_streaming(image_run):
    async def upload(_args):
        return {"status": "failed", "reason": 'Upload rejected: https://images.example/upload?api_key=private-secret\n{"error":"Rejected","token":"private-token"}'}

    _result, events = await image_run(upload, count=1)
    output = json.dumps(events)
    assert "private-secret" not in output
    assert "private-token" not in output
    assert "Rejected" in output


@pytest.mark.asyncio
async def test_imgbox_submission_errors_reach_the_retry_layer(monkeypatch, tmp_path):
    class Gallery:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def add(self, _images):
            yield {"success": False, "error": "Connection failed"}

    monkeypatch.setattr(uploadscreens.pyimgbox, "Gallery", lambda **_kwargs: Gallery())
    result = await uploadscreens.upload_image_task([str(tmp_path / "image.png"), "imgbox", {}, Meta()])
    assert result == {"status": "failed", "reason": "Connection failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("error, expected", [(httpx.ConnectError(""), "Connection failed"), (httpx.ReadTimeout(""), "Request timed out")])
async def test_http_image_host_keeps_a_readable_reason_for_empty_network_errors(monkeypatch, tmp_path, error, expected):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            raise error

    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(uploadscreens.httpx, "AsyncClient", Client)
    result = await uploadscreens.upload_image_task([str(image), "imgbb", {"DEFAULT": {"imgbb_api": "fictional-key"}}, Meta()])
    assert result == {"status": "failed", "reason": expected}
