"""
editor.py · Motor de edição vetorial pixel-perfect
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional
import fitz

from .models import (
    BBox, BoletoData, CampoDetectado, EditReport, FieldKey, EditRequest
)
from .detector import detect_all
from . import barcode_gen

log = logging.getLogger(__name__)


def _bbox_to_rect(bbox: BBox, pad: float = 0.5) -> fitz.Rect:
    expanded = bbox.expanded(pad)
    return fitz.Rect(expanded.x0, expanded.y0, expanded.x1, expanded.y1)


def _sample_font_at(page: fitz.Page, bbox: BBox) -> tuple[Optional[str], float, tuple[float, float, float]]:
    default_font, default_size, default_color = "helv", 10.0, (0.0, 0.0, 0.0)
    try:
        rect = _bbox_to_rect(bbox, pad=0.5)
        blocks = page.get_text("dict", clip=rect)
        for blk in blocks.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    raw_size = span.get("size", default_size)
                    raw_font = span.get("font", default_font)
                    raw_color = span.get("color", 0)
                    r = ((raw_color >> 16) & 0xFF) / 255.0
                    g = ((raw_color >> 8)  & 0xFF) / 255.0
                    b = ( raw_color        & 0xFF) / 255.0
                    return raw_font, float(raw_size), (r, g, b)
    except Exception as e:
        log.debug("Não consegui amostrar fonte em %s: %s", bbox.to_tuple(), e)
    return default_font, default_size, default_color


def _safe_font(font_name: Optional[str]) -> str:
    if not font_name:
        return "helv"
    fn = font_name.lower()
    if any(x in fn for x in ("courier", "mono", "consolas", "menlo")):
        if "bold" in fn: return "cobo"
        if "italic" in fn or "oblique" in fn: return "coit"
        return "cour"
    if any(x in fn for x in ("times", "serif", "roman", "georgia")):
        if "bold" in fn and ("italic" in fn or "oblique" in fn): return "tibi"
        if "bold" in fn: return "tibo"
        if "italic" in fn or "oblique" in fn: return "tiit"
        return "tiro"
    if "bold" in fn and ("italic" in fn or "oblique" in fn): return "hebi"
    if "bold" in fn: return "hebo"
    if "italic" in fn or "oblique" in fn: return "heit"
    return "helv"


def _erase_region(page: fitz.Page, bbox: BBox) -> None:
    rect = _bbox_to_rect(bbox, pad=0.5)
    annot = page.add_redact_annot(rect, fill=None)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)


def _write_text(
    page: fitz.Page,
    bbox: BBox,
    text: str,
    font_name: str,
    font_size: float,
    color: tuple[float, float, float],
    align: int = fitz.TEXT_ALIGN_LEFT,
) -> bool:
    rect = _bbox_to_rect(bbox, pad=0.0)
    size = float(font_size)
    safe_name = _safe_font(font_name)
    min_size = 5.0

    while size >= min_size:
        rc = page.insert_textbox(
            rect, text, fontname=safe_name, fontsize=size, color=color, align=align,
        )
        if rc >= 0:
            return True
        size -= 0.5
    log.warning("Texto não coube no bbox %s: %r", bbox.to_tuple(), text)
    return False


def _draw_barcode(page: fitz.Page, bbox: BBox, digits: str) -> bool:
    """
    v0.4: usa PNG embebido (python-barcode) em vez de retângulos vetoriais,
    porque scanners reais não liam o barcode vetorial anterior.
    """
    _erase_region(page, bbox)
    try:
        png_bytes = barcode_gen.render_to_png_bytes(
            digits,
            target_width_pt=bbox.x1 - bbox.x0,
            target_height_pt=bbox.y1 - bbox.y0,
        )
    except Exception as e:
        log.error("Falha ao gerar barcode ITF: %s", e)
        return False
    rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    page.insert_image(rect, stream=png_bytes, keep_proportion=False)
    return True


def apply_edits(req: EditRequest) -> EditReport:
    detections: Dict[FieldKey, List[CampoDetectado]] = detect_all(req.template_pdf_bytes)
    total = sum(len(v) for v in detections.values())
    log.info("Detectados %d campos únicos (%d ocorrências totais): %s",
             len(detections), total,
             {k.value: len(v) for k, v in detections.items()})

    doc = fitz.open(stream=req.template_pdf_bytes, filetype="pdf")
    edited: Dict[FieldKey, BBox] = {}
    edited_count: Dict[FieldKey, int] = {}
    skipped: Dict[FieldKey, str] = {}

    try:
        for key in req.fields_to_edit:
            new_value = req.data.get(key)
            if not new_value:
                skipped[key] = "Sem valor novo na BoletoData"
                continue

            campos = detections.get(key, [])
            if not campos:
                skipped[key] = "Campo não detectado no template"
                continue

            successes = 0
            failures: List[str] = []
            for idx, campo in enumerate(campos):
                page = doc[campo.page_index]
                try:
                    if key == FieldKey.CODIGO_BARRAS:
                        if _draw_barcode(page, campo.bbox, new_value):
                            successes += 1
                            edited.setdefault(key, campo.bbox)
                        else:
                            failures.append(f"#{idx}: falha ao desenhar barcode")
                    else:
                        font, size, color = _sample_font_at(page, campo.bbox)
                        _erase_region(page, campo.bbox)
                        text = new_value
                        if key == FieldKey.VALOR and not text.strip().startswith("R$"):
                            text = f"R$ {text}"
                        ok = _write_text(page, campo.bbox, text, font, size, color)
                        if ok:
                            successes += 1
                            edited.setdefault(key, campo.bbox)
                        else:
                            failures.append(f"#{idx}: texto não coube")
                except Exception as e:
                    log.exception("Erro ao editar %s ocorrência #%d", key.value, idx)
                    failures.append(f"#{idx}: {e}")

            edited_count[key] = successes
            if successes == 0:
                skipped[key] = "; ".join(failures) if failures else "Nenhuma ocorrência editada"
            elif failures:
                log.warning("%s: %d/%d ocorrências editadas. Falhas: %s",
                            key.value, successes, len(campos), failures)
            else:
                log.info("%s: %d/%d ocorrências editadas", key.value, successes, len(campos))

        output = doc.tobytes(garbage=4, deflate=True)
        report = EditReport(output_pdf_bytes=output, edited=edited, skipped=skipped)
        report.edited_count = edited_count  # type: ignore[attr-defined]
        return report
    finally:
        doc.close()
