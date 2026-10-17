"""WebUI forwards the public image-only CLI mode to the isolated subprocess."""

# ruff: noqa: S101

import io
import json
from types import SimpleNamespace

import pytest

from web_ui import server


def test_webui_accepts_image_only_arguments_and_forwards_them(tmp_path, monkeypatch):
    source = tmp_path / "release.mkv"
    source.touch()
    commands = []
    session_id = "imagehost-only-test"
    process = SimpleNamespace(pid=0, stdin=io.StringIO(), stdout=io.StringIO("Images prepared\n"), stderr=io.StringIO(), poll=lambda: 0, wait=lambda: 0)
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_get_browse_roots", lambda: [str(tmp_path)])

    def spawn(command, *_args):
        commands.append(command)
        return process, "subprocess"

    monkeypatch.setattr(server, "_spawn_webui_upload_process", spawn)
    response = server.app.test_client().post(
        "/api/execute",
        json={
            "path": str(source),
            "args": "--imagehost-upload-only --screens 6",
            "session_id": session_id,
        },
    )
    try:
        assert response.status_code == 200
        response.get_data(as_text=True)
        assert len(commands) == 1
        assert commands[0][4:] == ["--imagehost-upload-only", "--screens", "6"]
    finally:
        response.close()
        with server.active_processes_lock:
            server.active_processes.pop(session_id, None)


@pytest.mark.parametrize("preparation", [True, False])
def test_only_preparation_keeps_local_review_when_hosted_cache_exists(tmp_path, preparation):
    hosted = [{"img_url": "https://images.example.invalid/thumbnail.png", "raw_url": "https://images.example.invalid/full.png"}]
    (tmp_path / "image_data.json").write_text(json.dumps({"image_list": hosted}))
    meta = {"category": "MOVIE", "image_list": [], "imagehost_upload_only": preparation}
    review = server._screenshot_review_meta(tmp_path, meta)
    assert review["image_list"] == ([] if preparation else hosted)
