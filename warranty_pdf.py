"""Resize warranty uploads (image or PDF) and store as A4 PDF with JPEG raster pages (smaller than PNG for photos)."""
from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

DOC_MAX_EDGE = 2048
A4_FILL = 0.9
MAX_PDF_PAGES = 40
# Embedded raster: JPEG is much smaller than PNG for photographs / scans.
JPEG_QUALITY = 84
JPEG_SUBSAMPLING = 2  # 4:2:0


class WarrantyPdfError(Exception):
    __slots__ = ("key",)

    def __init__(self, key: str = "flash.warranty_doc_failed") -> None:
        self.key = key
        super().__init__(key)


def _resample():
    return getattr(Image, "Resampling", Image).LANCZOS


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB")


def _resize_max_edge(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m <= max_edge:
        return img
    scale = max_edge / m
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return img.resize((nw, nh), _resample())


def _pages_from_image(data: bytes) -> list[Image.Image]:
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except OSError as e:
        raise WarrantyPdfError("flash.warranty_doc_failed") from e
    img = ImageOps.exif_transpose(img)
    img = _to_rgb(img)
    return [_resize_max_edge(img, DOC_MAX_EDGE)]


def _pages_from_pdf(data: bytes) -> list[Image.Image]:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as e:
        raise WarrantyPdfError("flash.warranty_doc_failed") from e
    try:
        n = len(doc)
        if n == 0:
            raise WarrantyPdfError("flash.warranty_doc_failed")
        if n > MAX_PDF_PAGES:
            raise WarrantyPdfError("flash.warranty_doc_too_many_pages")
        out: list[Image.Image] = []
        for i in range(n):
            page = doc[i]
            rect = page.rect
            mw, mh = rect.width, rect.height
            if mw < 1 or mh < 1:
                continue
            z = DOC_MAX_EDGE / max(mw, mh)
            mat = fitz.Matrix(z, z)
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False)
            except RuntimeError as e:
                raise WarrantyPdfError("flash.warranty_doc_failed") from e
            if pix.n == 4:
                im = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
            elif pix.n == 3:
                im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            else:
                im = Image.frombytes("L", [pix.width, pix.height], pix.samples).convert("RGB")
            im = _to_rgb(im)
            out.append(_resize_max_edge(im, DOC_MAX_EDGE))
        if not out:
            raise WarrantyPdfError("flash.warranty_doc_failed")
        return out
    finally:
        doc.close()


def _build_pages(data: bytes, ext: str) -> list[Image.Image]:
    ext = ext.lower()
    if ext == "pdf":
        return _pages_from_pdf(data)
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        return _pages_from_image(data)
    raise WarrantyPdfError("flash.warranty_doc_failed")


def images_to_a4_pdf(images: list[Image.Image]) -> bytes:
    buf = BytesIO()
    pw, ph = A4
    box_w, box_h = pw * A4_FILL, ph * A4_FILL
    margin_x = (pw - box_w) / 2
    margin_y = (ph - box_h) / 2
    c = canvas.Canvas(buf, pagesize=A4)
    png_buffers: list[BytesIO] = []
    for img in images:
        png_buf = BytesIO()
        img.save(png_buf, format="PNG", optimize=True)
        png_buf.seek(0)
        png_buffers.append(png_buf)
        ir = ImageReader(png_buf)
        iw, ih = img.size
        scale = min(box_w / iw, box_h / ih)
        dw, dh = iw * scale, ih * scale
        x = margin_x + (box_w - dw) / 2
        y_bottom = margin_y + (box_h - dh) / 2
        c.drawImage(ir, x, y_bottom, width=dw, height=dh, mask="auto")
        c.showPage()
    c.save()
    del png_buffers
    return buf.getvalue()


def warranty_upload_to_pdf_bytes(data: bytes, ext: str) -> bytes:
    pages = _build_pages(data, ext)
    return images_to_a4_pdf(pages)
