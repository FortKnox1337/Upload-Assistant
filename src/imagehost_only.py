"""Prepare hosted screenshots independently of tracker uploads."""

import json
from pathlib import Path
from typing import Any

import cli_ui

from src.console import logger, prompt_in_thread
from src.meta import Meta
from src.meta_file import _replace_with_retry, write_meta_file
from src.rehostimages import has_restricted_image_hosts, select_common_image_host
from src.screenshot_review import list_screenshots, target_count
from src.takescreens import TakeScreensManager
from src.tracker_images import image_tags
from src.trackersetup import tracker_class_map
from src.uploadscreens import UploadScreensManager


def _temp_dir(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid


def _read_cache(meta: Meta) -> dict[str, Any]:
    try:
        cache = json.loads((_temp_dir(meta) / "imagehost_uploads.json").read_text(encoding="utf-8"))
        if isinstance(cache, dict) and cache.get("source") == str(Path(meta.path or "").resolve()) and isinstance(cache.get("files"), dict):
            return cache["files"]
    except OSError, ValueError:
        pass
    return {}


def _valid_images(meta: Meta, records: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources = {str(item.path.resolve()) for item in list_screenshots(_temp_dir(meta), meta.to_dict()) if item.path}
    valid = {}
    for source, record in records.items():
        if source not in sources or not isinstance(record, dict):
            continue
        image = record.get("image")
        if not isinstance(image, dict) or not all(image.get(key) for key in ("img_url", "raw_url", "web_url")):
            continue
        try:
            stat = Path(source).stat()
            if stat.st_size == record.get("size") and stat.st_mtime_ns == record.get("mtime_ns"):
                valid[source] = record
        except OSError:
            continue
    return valid


async def restore_prepared_images(meta: Meta) -> dict[str, dict[str, Any]]:
    """Reuse matching hosted sources and discard links for replaced frames."""
    records = _read_cache(meta)
    if not records:
        return {}
    valid = _valid_images(meta, records)
    stale_urls = {
        record["image"].get("raw_url") for source, record in records.items() if source not in valid and isinstance(record, dict) and isinstance(record.get("image"), dict)
    }
    meta.image_list = [image for image in meta.image_list if image.get("raw_url") not in stale_urls]
    known = {image.get("raw_url") for image in meta.image_list}
    if meta.imagehost_upload_only:
        for record in valid.values():
            image = record["image"]
            if record.get("group", "main") == "main" and image["raw_url"] not in known:
                meta.image_list.append(dict(image))
                known.add(image["raw_url"])
    else:
        # Normal runs load image_data.json before this check. Respect any
        # subsequent remote replacements or tracker-specific rehosting.
        valid = {source: record for source, record in valid.items() if record.get("group", "main") != "main" or record["image"]["raw_url"] in known}
    if stale_urls:
        path = _temp_dir(meta) / "pack_image_links.json"
        data = _read_pack_images(path)
        for key, value in list(data["keys"].items()):
            images = [image for image in value.get("images", []) if image.get("raw_url") not in stale_urls] if isinstance(value, dict) else []
            if images:
                data["keys"][key] = {"count": len(images), "images": images}
            else:
                data["keys"].pop(key, None)
        if path.exists():
            await _write_pack_images(path, data)
    return valid


async def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        await _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def _save_images(meta: Meta, records: dict[str, Any]) -> None:
    meta.image_sizes.update({record["image"]["raw_url"]: record["size"] for record in records.values()})
    await _write_json(_temp_dir(meta) / "imagehost_uploads.json", {"source": str(Path(meta.path or "").resolve()), "files": records})
    await _write_json(_temp_dir(meta) / "image_data.json", {"image_list": meta.image_list, "image_sizes": meta.image_sizes, "tonemapped": meta.tonemapped})
    await write_meta_file(meta)


def _read_pack_images(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except OSError, ValueError:
        data = {}
    if not isinstance(data.get("keys"), dict):
        data["keys"] = {}
    return data


async def _write_pack_images(path: Path, data: dict[str, Any]) -> None:
    data["total_count"] = sum(len(value.get("images", [])) for value in data["keys"].values() if isinstance(value, dict))
    await _write_json(path, data)


async def _save_extra_images(meta: Meta, records: dict[str, Any], groups: set[str]) -> None:
    path = _temp_dir(meta) / "pack_image_links.json"
    data = _read_pack_images(path)
    keys = data["keys"]
    for group in groups:
        if group.startswith("PLAYLIST_"):
            key = f"new_images_playlist_{group.removeprefix('PLAYLIST_')}"
        elif group.startswith("FILE_"):
            key = f"new_images_disc_{group.removeprefix('FILE_')}"
        else:
            continue
        images = [dict(record["image"], index=index) for index, record in enumerate(record for record in records.values() if record.get("group") == group)]
        if images:
            keys[key] = {"count": len(images), "images": images}
        else:
            keys.pop(key, None)
    await _write_pack_images(path, data)


def _host_index(config: dict[str, Any], host: str) -> int:
    return next((index for index in range(1, 10) if str(config.get("DEFAULT", {}).get(f"img_host_{index}", "")).strip().lower() == host), 0)


async def prepare_imagehost_upload_only(
    meta: Meta,
    config: dict[str, Any],
    takescreens: TakeScreensManager,
    uploader: UploadScreensManager,
) -> bool:
    """Capture/review screenshots, host every requested image, and save URLs."""
    meta.we_are_uploading = False
    defaults = config.get("DEFAULT", {})
    meta.imghost = str(meta.imghost or "").strip().lower() or next(
        (str(defaults[f"img_host_{index}"]).strip().lower() for index in range(1, 10) if str(defaults.get(f"img_host_{index}") or "").strip()), ""
    )
    if not meta.imghost:
        logger.error("[red]Configure an image host or choose one with --imghost before preparing images.[/red]")
        return False
    allowed_hosts = None
    if defaults.get("smart_image_host_selection", True) and not meta.imghost_from_cli:
        selected = select_common_image_host(defaults, meta.trackers, tracker_class_map)
        if selected:
            allowed_hosts = [
                str(defaults[f"img_host_{index}"]).strip().lower()
                for index in range(1, 10)
                if defaults.get(f"img_host_{index}") and select_common_image_host({"img_host_1": defaults[f"img_host_{index}"]}, meta.trackers, tracker_class_map)
            ]
            if meta.imghost not in allowed_hosts:
                meta.imghost = selected
        elif has_restricted_image_hosts(meta.trackers, tracker_class_map):
            logger.error(
                "[red]The selected trackers do not share a configured image host. Prepare images for a compatible tracker selection or choose --imghost explicitly.[/red]"
            )
            return False

    frames = meta.manual_frames
    if frames:
        meta.screens = len(frames) if isinstance(frames, list) else len([frame for frame in frames.split(",") if frame.strip()])
    meta.screens = target_count(_temp_dir(meta), meta.screens)
    # Show local frames during review, including sources already hosted by an
    # earlier preparation. Replacing a frame invalidates its cached upload.
    meta.image_list = []
    if meta.screens <= 0 and meta.category != "BOOK":
        logger.error("[red]Image-only preparation requires at least one screenshot (--screens).[/red]")
        return False
    if meta.is_disc == "BDMV":
        await takescreens.disc_screenshots(meta, meta.filename, meta.bdinfo, meta.uuid, meta.base_dir, meta.vapoursynth, [], meta.ffdebug, 0, cleanup_after_capture=False)
    elif meta.is_disc == "DVD":
        await takescreens.dvd_screenshots(meta, disc_num=0, num_screens=0, retry_cap=False, cleanup_after_capture=False)
    elif meta.category == "XXX":
        await takescreens.xxx_contact_sheets(meta.filelist or [], meta.uuid, meta.base_dir, meta)
    else:
        video = str(meta.filelist[0]) if meta.filelist else str(meta.path or "")
        await takescreens.screenshots(video, meta.filename, meta.uuid, meta.base_dir, meta, manual_frames=meta.manual_frames or "", cleanup_after_capture=False)

    await write_meta_file(meta)
    logger.info(f"[cyan]Review screenshots in {_temp_dir(meta) / 'screenshots'} before image hosting.[/cyan]")
    if (not meta.unattended or meta.unattended_confirm) and not await prompt_in_thread(cli_ui.ask_yes_no, "Upload the prepared screenshots to image hosts?", default=True):
        logger.info("[yellow]Image hosting cancelled. Local screenshots have been kept.[/yellow]")
        return False

    meta.screens = target_count(_temp_dir(meta), meta.screens)
    items = [item for item in list_screenshots(_temp_dir(meta), meta.to_dict()) if item.path]
    main = [item for item in items if item.group == "main"]
    requested = len(main) if meta.category == "BOOK" else meta.screens
    if requested <= 0:
        logger.error("[red]No screenshots are available for image-only preparation.[/red]")
        return False
    meta.screens = requested
    sources = {str(item.path.resolve()): item for item in items if item.path}
    previous_groups = {record.get("group", "main") for record in _read_cache(meta).values() if isinstance(record, dict)} - {"main"}
    records = await restore_prepared_images(meta)
    if allowed_hosts is not None:
        records = {source: record for source, record in records.items() if record.get("host") in allowed_hosts}
        meta.image_list = [record["image"] for record in records.values() if record.get("group", "main") == "main"]
    meta.image_list = meta.image_list[:requested]
    counters: dict[str, Any] = {"_uploaded_image_files": set(records), "_uploaded_image_results": {}}
    snapshots = {source: (Path(source).stat().st_size, Path(source).stat().st_mtime_ns) for source in sources}

    def remember_uploads(results: dict[str, Any]) -> None:
        for source, upload in results.items():
            if source not in sources:
                continue
            item = sources[source]
            size, mtime_ns = snapshots[source]
            image = {key: upload[key] for key in ("img_url", "raw_url", "web_url")}
            image["tags"] = image_tags(meta, custom=item.group != "main")
            image["local_file_path"] = source
            records[source] = {"image": image, "host": upload.get("host", meta.imghost), "group": item.group, "size": size, "mtime_ns": mtime_ns}

    try:
        if len(meta.image_list) < requested:
            await uploader.upload_screens(meta, requested, _host_index(config, meta.imghost), 0, requested, [], counters, allowed_hosts=allowed_hosts)
        remember_uploads(counters["_uploaded_image_results"])

        # Additional disc/playlist images are separate from the main image
        # list. Upload one source at a time so partial custom-batch fallback
        # cannot discard successful results or reorder source associations.
        for source, item in sources.items():
            if item.group == "main" or source in records:
                continue
            image_meta = meta.copy()
            image_meta.image_list = []
            result: dict[str, Any] = {"_uploaded_image_results": {}}
            await uploader.upload_screens(image_meta, 1, _host_index(config, image_meta.imghost), 0, 1, [source], result, allowed_hosts=allowed_hosts)
            remember_uploads(result["_uploaded_image_results"])

        groups = {item.group for item in items if item.group != "main"} | previous_groups
        if groups:
            await _save_extra_images(meta, records, groups)
    finally:
        remember_uploads(counters["_uploaded_image_results"])
        await _save_images(meta, records)

    extra = [source for source, item in sources.items() if item.group != "main"]
    uploaded = len(meta.image_list) + sum(source in records for source in extra)
    total = requested + len(extra)
    complete = len(meta.image_list) >= requested and all(source in records for source in extra)
    if complete:
        logger.info(f"[green]Image preparation complete: {uploaded}/{total} images uploaded. Saved for a later tracker upload.[/green]")
    else:
        logger.error(
            f"[red]Image preparation incomplete: {uploaded}/{total} images uploaded. Successful images are saved; rerun --imagehost-upload-only to retry remaining files.[/red]"
        )
    return complete
