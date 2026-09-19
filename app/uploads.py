from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import aiofiles
from starlette.datastructures import UploadFile

from app.artifacts import slugify_filename, storage_relative_path
from app.config import Settings, settings


async def save_image_upload(
    upload: UploadFile,
    job_id: str,
    app_settings: Settings | None = None,
) -> str:
    resolved = app_settings or settings
    if not upload.filename:
        raise ValueError("Uploaded file must have a filename.")
    content_type = upload.content_type or ""
    if not content_type.startswith("image/"):
        raise ValueError("Only image uploads are supported in this panel.")
    target = resolved.browser_upload_dir / f"{job_id}_{uuid4().hex}_{slugify_filename(upload.filename)}"
    try:
        async with aiofiles.open(target, "xb") as handle:
            while chunk := await upload.read(1024 * 1024):
                await handle.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return storage_relative_path(target, resolved)


async def save_reference_upload(
    upload: UploadFile,
    job_id: str,
    app_settings: Settings | None = None,
) -> str:
    """Persist an image or a narrowly validated font reference.

    General document uploads remain intentionally unsupported.  Thumbnail jobs
    need a real font attachment, so only SFNT/OpenType signatures with .ttf/.otf
    extensions are admitted in addition to the existing image contract.
    """
    filename = upload.filename or ""
    suffix = Path(filename).suffix.casefold()
    content_type = (upload.content_type or "").casefold()
    if content_type.startswith("image/"):
        return await save_image_upload(upload, job_id, app_settings)
    if suffix not in {".ttf", ".otf"}:
        raise ValueError("Only image, TTF, and OTF reference uploads are supported.")
    header = await upload.read(4)
    await upload.seek(0)
    if header not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}:
        await upload.close()
        raise ValueError("The uploaded font does not have a valid TrueType/OpenType signature.")
    resolved = app_settings or settings
    target = resolved.browser_upload_dir / f"{job_id}_{uuid4().hex}_{slugify_filename(filename)}"
    written = 0
    try:
        async with aiofiles.open(target, "xb") as handle:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > 25 * 1024 * 1024:
                    raise ValueError("Font reference exceeds the 25 MiB limit.")
                await handle.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return storage_relative_path(target, resolved)
