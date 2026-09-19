from __future__ import annotations
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from starlette.datastructures import Headers,UploadFile
import pytest
from app.uploads import save_image_upload, save_reference_upload
from app.automation.gemini_worker import _uploaded_pixels_match


def test_same_named_references_are_stored_independently(tmp_path):
    uploads=tmp_path/'storage/uploads';uploads.mkdir(parents=True)
    settings=SimpleNamespace(browser_upload_dir=uploads,browser_screenshot_dir=tmp_path/'storage/screenshots')
    async def save():
        result=[]
        for content in [b'first',b'second']:
            upload=UploadFile(filename='same.png',file=io.BytesIO(content),headers=Headers({'content-type':'image/png'}))
            result.append(await save_image_upload(upload,'job',settings))
        return result
    paths=asyncio.run(save())
    assert paths[0]!=paths[1]
    assert [(tmp_path/path).read_bytes() for path in paths]==[b'first',b'second']


def test_preview_identity_rejects_swapped_or_duplicate_attachments(tmp_path):
    paths=[];pixels=[]
    for color in ['red','blue']:
        image=Image.new('RGBA',(32,32),color);path=tmp_path/f'{color}.png';image.save(path)
        paths.append(path);pixels.append(list(image.tobytes()))
    assert _uploaded_pixels_match({'attachmentPixels':pixels},paths)
    assert not _uploaded_pixels_match({'attachmentPixels':list(reversed(pixels))},paths)
    assert not _uploaded_pixels_match({'attachmentPixels':[pixels[0],pixels[0]]},paths)
    assert not _uploaded_pixels_match({'attachmentPixels':[None,None]},paths)


def test_reference_upload_accepts_only_signed_ttf_or_otf_documents(tmp_path):
    uploads=tmp_path/'storage/uploads';uploads.mkdir(parents=True)
    settings=SimpleNamespace(browser_upload_dir=uploads,browser_screenshot_dir=tmp_path/'storage/screenshots')
    async def save_font(content=b'\x00\x01\x00\x00font-data', name='Quicky Story.ttf'):
        upload=UploadFile(filename=name,file=io.BytesIO(content),headers=Headers({'content-type':'font/ttf'}))
        return await save_reference_upload(upload,'job',settings)
    saved=asyncio.run(save_font())
    assert (tmp_path/saved).read_bytes().startswith(b'\x00\x01\x00\x00')
    with pytest.raises(ValueError, match='valid TrueType'):
        asyncio.run(save_font(b'not-a-font'))
    with pytest.raises(ValueError, match='Only image, TTF, and OTF'):
        asyncio.run(save_font(b'%PDF', 'document.pdf'))
