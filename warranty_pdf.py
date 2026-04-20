"""Resize warranty uploads (image or PDF) and store as A4 PDF with JPEG raster pages."""
from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image, ImageOps

# A4 in points (72 dpi, ISO 216)
A4_W = 210 * 72 / 25.4
A4_H = 297 * 72 / 25.4

A4_FILL = 0.9
MAX_PDF_PAGES = 40
# Long edge after resize (px).
DOC_MAX_EDGE = 2000
# Embedded raster: JPEG inside PDF (PyMuPDF passes stream through as DCT).
JPEG_QUALITY = 75
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
    """Build PDF with PyMuPDF so JPEG bytes stay as DCT streams (smaller than ReportLab path)."""
    doc = fitz.open()
    try:
        box_w = A4_W * A4_FILL
        box_h = A4_H * A4_FILL
        margin_x = (A4_W - box_w) / 2
        margin_y = (A4_H - box_h) / 2
        for img in images:
            rgb = _to_rgb(img)
            raster_buf = BytesIO()
            rgb.save(
                raster_buf,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                subsampling=JPEG_SUBSAMPLING,
            )
            jpeg_bytes = raster_buf.getvalue()
            iw, ih = rgb.size
            scale = min(box_w / iw, box_h / ih)
            dw, dh = iw * scale, ih * scale
            x0 = margin_x + (box_w - dw) / 2
            # Previous ReportLab code used bottom-left origin; PyMuPDF uses top-left.
            y_bottom = margin_y + (box_h - dh) / 2
            y_top = A4_H - y_bottom - dh
            rect = fitz.Rect(x0, y_top, x0 + dw, y_top + dh)
            page = doc.new_page(width=A4_W, height=A4_H)
            page.insert_image(rect, stream=jpeg_bytes, keep_proportion=False, overlay=True)
        return doc.write()
    finally:
        doc.close()


def warranty_upload_to_pdf_bytes(data: bytes, ext: str) -> bytes:
    return warranty_uploads_to_pdf_bytes([(data, ext)])


def warranty_uploads_to_pdf_bytes(parts: list[tuple[bytes, str]]) -> bytes:
    """One or more uploads → one PDF. Multiple files = images only, one page per photo."""
    if not parts:
        raise WarrantyPdfError("flash.warranty_doc_failed")
    if len(parts) > 1:
        for _, ext in parts:
            if ext.lower() == "pdf":
                raise WarrantyPdfError("flash.warranty_doc_multi_no_pdf")
        if len(parts) > MAX_PDF_PAGES:
            raise WarrantyPdfError("flash.warranty_doc_too_many_photos")
    all_pages: list[Image.Image] = []
    for data, ext in parts:
        all_pages.extend(_build_pages(data, ext))
    if len(all_pages) > MAX_PDF_PAGES:
        raise WarrantyPdfError("flash.warranty_doc_too_many_pages")
    return images_to_a4_pdf(all_pages)
