from __future__ import annotations

from pathlib import Path

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
    target = resolved.browser_upload_dir / f"{job_id}_{slugify_filename(upload.filename)}"
    async with aiofiles.open(target, "wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            await handle.write(chunk)
    await upload.close()
    return storage_relative_path(target, resolved)
