"""Image-only preparation stops before tracker uploads and reuses hosted frames."""

# ruff: noqa: S101

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src import early_tasks, imagehost_only, prep_helpers
from src.args import Args
from src.meta import Meta
from src.screenshot_manifest import register
from src.uploadscreens import UploadScreensManager


def config():
    return {
        "DEFAULT": {
            "img_host_1": "imgbb",
            "img_host_2": "imgbox",
            "img_host_3": "ptscreens",
            "screens": 3,
            "image_upload_concurrency": 1,
            "image_upload_delay": 0,
            "min_successful_image_uploads": 1,
        },
        "TRACKERS": {"default_trackers": ""},
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_mode_is_explicit_and_does_not_leak_from_saved_meta(tmp_path, enabled):
    args = [str(tmp_path), *(["--imagehost-upload-only"] if enabled else [])]
    meta, _, _ = Args(config()).parse(args, Meta(imagehost_upload_only=True))
    assert meta.imagehost_upload_only is enabled


@pytest.mark.parametrize("other", ["--skip-imagehost-upload", "--site-check", "--site-upload"])
def test_cli_rejects_conflicting_modes(tmp_path, other):
    args = [str(tmp_path), "--imagehost-upload-only", other, *(["AITHER"] if other == "--site-upload" else [])]
    with pytest.raises(SystemExit) as error:
        Args(config()).parse(args, Meta())
    assert error.value.code == 2


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    release = tmp_path / "release.mkv"
    release.write_bytes(b"video")
    folder = tmp_path / "tmp" / "release" / "screenshots"
    folder.mkdir(parents=True)
    sources = [folder / f"image-{index}.png" for index in range(1, 4)]
    for source in sources:
        source.write_bytes(b"screenshot")
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="release",
        path=str(release),
        filelist=[str(release)],
        filename="release",
        category="TV",
        screens=3,
        imghost="imgbb",
        imagehost_upload_only=True,
        unattended=True,
    )
    capture = SimpleNamespace(screenshots=AsyncMock(), disc_screenshots=AsyncMock(), dvd_screenshots=AsyncMock(), xxx_contact_sheets=AsyncMock())
    monkeypatch.setattr("src.uploadscreens.record_event_async", AsyncMock())
    return meta, capture, sources


def result(source, host):
    url = f"https://{host}.example/{Path(source).name}"
    return {"status": "success", "img_url": url, "raw_url": url, "web_url": url}


def test_fallback_uploads_every_frame_and_saves_reusable_results(preparation, monkeypatch):
    meta, capture, sources = preparation
    calls = []

    async def upload(args):
        source, host = args[:2]
        name = Path(source).name
        calls.append((name, host))
        successful = {sources[0].name: "imgbb", sources[1].name: "imgbox", sources[2].name: "ptscreens"}
        return result(source, host) if host == successful[name] else {"status": "failed", "reason": "API key error"}

    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert calls == [
        (sources[0].name, "imgbb"),
        (sources[1].name, "imgbb"),
        (sources[2].name, "imgbb"),
        (sources[1].name, "imgbox"),
        (sources[2].name, "imgbox"),
        (sources[2].name, "ptscreens"),
    ]
    assert len(meta.image_list) == 3
    saved = json.loads((Path(meta.base_dir) / "tmp" / meta.uuid / "image_data.json").read_text())
    assert saved["image_list"] == meta.image_list
    assert len(asyncio.run(imagehost_only.restore_prepared_images(meta))) == 3
    assert not meta.we_are_uploading


def test_partial_upload_fails_even_above_minimum_and_retry_skips_successes(preparation, monkeypatch):
    meta, capture, sources = preparation
    calls = []
    failed = True

    async def upload(args):
        source, host = args[:2]
        calls.append(Path(source).name)
        if failed and Path(source).name == sources[2].name:
            return {"status": "failed", "reason": "API key error"}
        return result(source, host)

    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    uploader = UploadScreensManager(config())
    assert not asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    assert len(meta.image_list) == 2
    assert (Path(meta.base_dir) / "tmp" / meta.uuid / "image_data.json").exists()
    calls.clear()
    failed = False
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    assert calls == [sources[2].name]
    assert len(meta.image_list) == 3


def test_replaced_frame_invalidates_only_its_saved_url(preparation, monkeypatch):
    meta, capture, sources = preparation
    calls = []

    async def upload(args):
        source, host = args[:2]
        calls.append(Path(source).name)
        return result(source, host)

    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    uploader = UploadScreensManager(config())
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    sources[1].write_bytes(b"replacement screenshot")
    assert len(asyncio.run(imagehost_only.restore_prepared_images(meta))) == 2
    assert len(meta.image_list) == 2
    calls.clear()
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    assert calls == [sources[1].name]


def test_complete_preparation_does_not_upload_again(preparation, monkeypatch):
    meta, capture, _ = preparation
    upload = AsyncMock(side_effect=lambda args: result(args[0], args[1]))
    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    uploader = UploadScreensManager(config())
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    upload.reset_mock()
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    upload.assert_not_awaited()


def test_normal_upload_respects_newer_remote_replacements(preparation, monkeypatch):
    meta, capture, sources = preparation
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    meta.imagehost_upload_only = False
    replacement = result("replacement.png", "imgbox")
    meta.image_list[1] = replacement
    expected = list(meta.image_list)
    restored = asyncio.run(imagehost_only.restore_prepared_images(meta))
    assert meta.image_list == expected
    assert str(sources[1].resolve()) not in restored


def test_missing_capture_fails_even_when_all_available_frames_upload(preparation, monkeypatch):
    meta, capture, sources = preparation
    sources[2].unlink()
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert not asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert len(meta.image_list) == 2


@pytest.mark.parametrize("frames", [[100, 200, 300], "100,200,300"])
def test_manual_frames_determine_requested_count(preparation, monkeypatch, frames):
    meta, capture, _ = preparation
    meta.screens = 6
    meta.manual_frames = frames
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert meta.screens == 3


def test_book_hosts_available_extracted_pages(preparation, monkeypatch):
    meta, capture, _ = preparation
    meta.category = "BOOK"
    meta.screens = 6  # Short books can produce fewer pages than requested.
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    capture.screenshots.assert_awaited_once()
    assert meta.screens == len(meta.image_list) == 3


def test_smart_selection_uses_only_hosts_accepted_by_selected_trackers(preparation, monkeypatch):
    meta, capture, _ = preparation
    meta.trackers = ["EXAMPLE"]
    policy = SimpleNamespace(approved_image_hosts=("ptscreens",), check_image_hosts=lambda: None)
    monkeypatch.setattr(imagehost_only, "tracker_class_map", {"EXAMPLE": policy})
    upload = AsyncMock(side_effect=lambda args: result(args[0], args[1]))
    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert meta.imghost == "ptscreens"
    assert {call.args[0][1] for call in upload.await_args_list} == {"ptscreens"}


def test_smart_selection_reports_no_common_host_before_capture(preparation, monkeypatch):
    meta, capture, _ = preparation
    meta.trackers = ["FIRST", "SECOND"]
    monkeypatch.setattr(
        imagehost_only,
        "tracker_class_map",
        {
            "FIRST": SimpleNamespace(approved_image_hosts=("imgbb",), check_image_hosts=lambda: None),
            "SECOND": SimpleNamespace(approved_image_hosts=("imgbox",), check_image_hosts=lambda: None),
        },
    )
    uploader = SimpleNamespace(upload_screens=AsyncMock())
    assert not asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    capture.screenshots.assert_not_awaited()
    uploader.upload_screens.assert_not_awaited()


def test_explicit_unconfigured_host_overrides_smart_selection(preparation, monkeypatch):
    meta, capture, _ = preparation
    meta.trackers = ["EXAMPLE"]
    meta.imghost = "onlyimage"
    meta.imghost_from_cli = True
    monkeypatch.setattr(
        imagehost_only,
        "tracker_class_map",
        {
            "EXAMPLE": SimpleNamespace(approved_image_hosts=("ptscreens",), check_image_hosts=lambda: None),
        },
    )
    upload = AsyncMock(side_effect=lambda args: result(args[0], args[1]))
    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert {call.args[0][1] for call in upload.await_args_list} == {"onlyimage"}


def test_review_can_cancel_before_any_image_upload(preparation, monkeypatch):
    meta, capture, sources = preparation
    meta.unattended = False
    prompt = AsyncMock(return_value=False)
    monkeypatch.setattr(imagehost_only, "prompt_in_thread", prompt)
    uploader = SimpleNamespace(upload_screens=AsyncMock())
    assert not asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    uploader.upload_screens.assert_not_awaited()
    assert all(source.exists() for source in sources)


def test_repeat_preparation_reviews_and_replaces_local_frames_through_webui(preparation, monkeypatch):
    from web_ui import server

    meta, capture, sources = preparation
    upload = AsyncMock(side_effect=lambda args: result(args[0], args[1]))
    monkeypatch.setattr("src.uploadscreens.upload_image_task", upload)
    uploader = UploadScreensManager(config())
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    upload.reset_mock()
    meta.unattended = False
    temp_dir = Path(meta.base_dir) / "tmp" / meta.uuid
    monkeypatch.setattr(server, "_resolve_execution_screenshot_review", lambda _session: (temp_dir, json.loads((temp_dir / "meta.json").read_text())))
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_verify_same_origin", lambda: True)

    async def capture_replacement(_temp_dir, _meta, item):
        item.path.write_bytes(b"a newly reviewed screenshot")
        return 42.0

    monkeypatch.setattr("src.screenshot_review._capture_fresh_frame", capture_replacement)

    def review_and_replace():
        with server.app.test_client() as client:
            response = client.get("/api/execution_screenshots?session_id=preparation")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["can_add"]
            assert len(payload["screenshots"]) == 3
            assert all(item["source"] == "local" and item["can_replace"] and item["can_delete"] for item in payload["screenshots"])
            first = payload["screenshots"][0]
            assert first["image_url"] == first["full_image_url"]
            with client.get(first["full_image_url"]) as full_size:
                assert full_size.status_code == 200
                assert full_size.data == sources[0].read_bytes()
            replaced = client.post(f"/api/execution_screenshots/{first['id']}/replace", json={"session_id": "preparation"})
            assert replaced.status_code == 200

    async def confirm_review(*_args, **_kwargs):
        await asyncio.to_thread(review_and_replace)
        return True

    monkeypatch.setattr(imagehost_only, "prompt_in_thread", confirm_review)
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, uploader))
    assert [Path(call.args[0][0]).name for call in upload.await_args_list] == [sources[0].name]
    assert len(meta.image_list) == 3


@pytest.mark.parametrize("disc,method", [("BDMV", "disc_screenshots"), ("DVD", "dvd_screenshots"), ("", "screenshots")])
def test_disc_capture_routes_through_existing_capture_methods(preparation, monkeypatch, disc, method):
    meta, capture, _ = preparation
    meta.is_disc = disc
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    getattr(capture, method).assert_awaited_once()


def test_additional_disc_images_are_saved_for_description_reuse(preparation, monkeypatch):
    meta, capture, sources = preparation
    meta.is_disc = "BDMV"
    register(meta.base_dir, meta.uuid, sources, "main")
    extra = sources[0].parent / "PLAYLIST_1-1.png"
    extra.write_bytes(b"extra")
    register(meta.base_dir, meta.uuid, [extra], "PLAYLIST_1")
    monkeypatch.setattr("src.uploadscreens.upload_image_task", AsyncMock(side_effect=lambda args: result(args[0], args[1])))
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    assert len(meta.image_list) == 3
    saved = json.loads((Path(meta.base_dir) / "tmp" / meta.uuid / "pack_image_links.json").read_text())
    assert len(saved["keys"]["new_images_playlist_1"]["images"]) == 1
    assert asyncio.run(imagehost_only.prepare_imagehost_upload_only(meta, config(), capture, UploadScreensManager(config())))
    saved = json.loads((Path(meta.base_dir) / "tmp" / meta.uuid / "pack_image_links.json").read_text())
    assert len(saved["keys"]["new_images_playlist_1"]["images"]) == 1
    extra_source = next(source for source, record in asyncio.run(imagehost_only.restore_prepared_images(meta)).items() if record["group"] == "PLAYLIST_1")
    Path(extra_source).unlink()
    asyncio.run(imagehost_only.restore_prepared_images(meta))
    saved = json.loads((Path(meta.base_dir) / "tmp" / meta.uuid / "pack_image_links.json").read_text())
    assert "new_images_playlist_1" not in saved["keys"]


def test_image_only_skips_early_torrent_and_usenet_work(monkeypatch):
    meta = Meta(imagehost_upload_only=True, trackers=["USENET"], usenet=True)
    client = SimpleNamespace(find_existing_torrent=AsyncMock())
    create = AsyncMock()
    archive = AsyncMock()
    monkeypatch.setattr("src.early_tasks.TorrentCreator.create_torrent", create)
    monkeypatch.setattr("src.usenetcreate.prepare_and_upload_usenet", archive)
    asyncio.run(early_tasks.create_base_torrents_early(meta, client))
    asyncio.run(early_tasks.prepare_usenet_archive_early(meta, {"USENET": {}}))
    client.find_existing_torrent.assert_not_awaited()
    create.assert_not_awaited()
    archive.assert_not_awaited()


def test_image_only_does_not_search_torrent_client():
    client = SimpleNamespace(find_existing_torrent=AsyncMock(), get_ptp_from_hash=AsyncMock())
    meta = Meta(imagehost_upload_only=True, trackers=["AITHER"])
    asyncio.run(prep_helpers.process_trackers_and_torrent(SimpleNamespace(config=config()), meta, client, ["infohash", "skip_auto_torrent"], [], "", ""))
    client.find_existing_torrent.assert_not_awaited()
    client.get_ptp_from_hash.assert_not_awaited()
    assert meta.trackers == ["AITHER"]


def test_process_meta_returns_before_tracker_validation_and_confirmation(preparation, monkeypatch):
    import upload

    meta, capture, _ = preparation
    prepare = AsyncMock(return_value=True)
    validations = AsyncMock()
    descriptions = AsyncMock()
    monkeypatch.setattr(upload, "config", config())
    monkeypatch.setattr(upload, "Prep", lambda **_kwargs: SimpleNamespace(gather_prep=AsyncMock(return_value=meta)))
    monkeypatch.setattr(upload, "TrackerSetup", lambda **_kwargs: SimpleNamespace(filter_unsupported_trackers=Mock()))
    monkeypatch.setattr(upload, "name_manager", SimpleNamespace(get_name=AsyncMock(return_value=("Release", "Release", "Release", []))), raising=False)
    monkeypatch.setattr(upload, "takescreens_manager", capture, raising=False)
    monkeypatch.setattr(upload, "uploadscreens_manager", SimpleNamespace(), raising=False)
    monkeypatch.setattr(upload, "_publish_webui_preview_target", Mock())
    monkeypatch.setattr(upload, "validate_tracker_logins", validations)
    monkeypatch.setattr(upload, "gen_desc", descriptions)
    monkeypatch.setattr(imagehost_only, "prepare_imagehost_upload_only", prepare)
    assert asyncio.run(upload.process_meta(meta, meta.base_dir))
    prepare.assert_awaited_once()
    validations.assert_not_awaited()
    descriptions.assert_not_awaited()


@pytest.mark.parametrize("succeeded", [True, False])
@pytest.mark.parametrize("line_queue", [True, False])
def test_controller_stops_before_tracker_client_and_hook_flows(preparation, monkeypatch, succeeded, line_queue):
    import importlib
    import sys

    import data.config as config_module
    import upload

    meta, _, _ = preparation
    cfg = config()
    cfg["DEFAULT"].update({"sanitize_meta": False, "cross_seeding": True})
    monkeypatch.setattr(config_module, "config", cfg)
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    monkeypatch.setattr(upload, "config", {})
    monkeypatch.setattr(upload, "parser", Args(cfg), raising=False)
    monkeypatch.setattr(upload, "load_heavy_globals", Mock())
    monkeypatch.setattr(upload, "update_notification", AsyncMock(return_value="test"))
    monkeypatch.setattr("src.stats.configure_stats", Mock())
    item_stats = AsyncMock()
    monkeypatch.setattr("src.stats.record_event_async", item_stats)
    monkeypatch.setattr(upload, "configured_binary", lambda *_args: True)
    monkeypatch.setattr("bin.get_mediainfo.MediaInfoBinaryManager.ensure_mediainfo_binary", AsyncMock())
    monkeypatch.setattr("src.configvalidator.validate_config", lambda *_args: (True, [], []))
    monkeypatch.setattr(upload, "get_mkbrr_path", AsyncMock(side_effect=AssertionError("no hashing dependency needed")))
    monkeypatch.setattr(sys, "argv", ["upload.py", meta.path, "--imagehost-upload-only", "-ua"])

    async def queue(_path, candidate, _paths, _base):
        candidate.args_line_queue = line_queue
        items = [{"path": meta.path, "args": [meta.path, "--screens", "3"]}] if line_queue else [meta.path]
        return items, str(Path(meta.base_dir) / "queue_processed.log")

    monkeypatch.setattr(upload.QueueManager, "handle_queue", queue)
    save_queue = AsyncMock()
    monkeypatch.setattr(upload, "save_processed_file", save_queue)

    async def prepare(candidate, _base_dir):
        assert candidate.imagehost_upload_only
        candidate.we_are_uploading = True  # Even stale state must not enter upload flows.
        return succeeded

    prepare_mock = AsyncMock(side_effect=prepare)
    cross_seed, hooks, upload_order = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(upload, "process_meta", prepare_mock)
    monkeypatch.setattr(upload, "process_cross_seeds", cross_seed)
    monkeypatch.setattr(upload, "run_post_upload_hooks", hooks)
    monkeypatch.setattr(upload, "run_upload_order", upload_order)
    monkeypatch.setattr(upload, "cleanup_manager", SimpleNamespace(cleanup=AsyncMock(), reset_terminal=Mock()))
    monkeypatch.setattr(upload.asyncio, "sleep", AsyncMock())
    if succeeded:
        asyncio.run(upload.do_the_thing(meta.base_dir))
    else:
        with pytest.raises(SystemExit) as error:
            asyncio.run(upload.do_the_thing(meta.base_dir))
        assert error.value.code == 1
    prepare_mock.assert_awaited_once()
    cross_seed.assert_not_awaited()
    hooks.assert_not_awaited()
    upload_order.assert_not_awaited()
    save_queue.assert_not_awaited()
    item_stats.assert_not_awaited()
