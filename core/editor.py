"""
editor.py · Motor de edição vetorial pixel-perfect
─────────────────────────────────────────────────────────────────────────────
v0.5 — mantém fonte/size do original. Expande bbox horizontalmente em vez de encolher fonte.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple
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


def _sample_font_at(page: fitz.Page, bbox: BBox) -> Tuple[Optional[str], float, Tuple[float, float, float], int]:
    """Retorna (font_name, size, color, flags) do span original."""
    default = ("helv", 10.0, (0.0, 0.0, 0.0), 0)
    try:
        rect = _bbox_to_rect(bbox, pad=0.5)
        blocks = page.get_text("dict", clip=rect)
        for blk in blocks.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    raw_size = span.get("size", default[1])
                    raw_font = span.get("font", default[0])
                    raw_color = span.get("color", 0)
                    raw_flags = span.get("flags", 0)
                    r = ((raw_color >> 16) & 0xFF) / 255.0
                    g = ((raw_color >> 8)  & 0xFF) / 255.0
                    b = ( raw_color        & 0xFF) / 255.0
                    return raw_font, float(raw_size), (r, g, b), int(raw_flags)
    except Exception as e:
        log.debug("Não consegui amostrar fonte em %s: %s", bbox.to_tuple(), e)
    return default


def _safe_font(font_name: Optional[str], flags: int = 0) -> str:
    """Mapeia fonte do PDF pra uma das 14 fontes base do PyMuPDF, respeitando bold/italic."""
    is_bold = bool(flags & 16)
    is_italic = bool(flags & 2)

    if not font_name:
        font_name = "helv"
    fn = font_name.lower()

    # Detecta bold/italic pelo nome também (alguns PDFs marcam só no nome)
    if "bold" in fn:
        is_bold = True
    if "italic" in fn or "oblique" in fn:
        is_italic = True

    if any(x in fn for x in ("courier", "mono", "consolas", "menlo")):
        if is_bold and is_italic: return "cobi"
        if is_bold: return "cobo"
        if is_italic: return "coit"
        return "cour"
    if any(x in fn for x in ("times", "serif", "roman", "georgia")):
        if is_bold and is_italic: return "tibi"
        if is_bold: return "tibo"
        if is_italic: return "tiit"
        return "tiro"
    # Default sans → helv
    if is_bold and is_italic: return "hebi"
    if is_bold: return "hebo"
    if is_italic: return "heit"
    return "helv"


def _erase_region(page: fitz.Page, bbox: BBox) -> None:
    """Remove os objetos-texto do bbox. fill=None garante que NÃO pinta branco por cima."""
    rect = _bbox_to_rect(bbox, pad=0.5)
    page.add_redact_annot(rect, fill=None)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)


def _measure_text_width(text: str, font_name: str, font_size: float) -> float:
    """Mede a largura em pontos que o texto vai ocupar nessa fonte/size."""
    try:
        font = fitz.Font(fontname=font_name)
        return font.text_length(text, fontsize=font_size)
    except Exception:
        # Fallback: ~0.55 da fonte por char (estimativa Helvetica)
        return len(text) * font_size * 0.55


def _expand_bbox_right(
    page: fitz.Page,
    bbox: BBox,
    needed_width: float,
    max_extra: float = 60.0,
) -> BBox:
    """
    Expande o bbox para a direita se o texto novo não couber.
    Limite: max_extra pontos (pra não invadir células vizinhas).

    Boletos têm espaço sobrando à direita do valor/data — esse é o truque.
    """
    current_width = bbox.x1 - bbox.x0
    if needed_width <= current_width:
        return bbox  # já cabe, não precisa expandir

    extra_needed = needed_width - current_width
    extra = min(extra_needed + 2.0, max_extra)  # +2pt de respiro

    new_bbox = BBox(
        x0=bbox.x0,
        y0=bbox.y0,
        x1=bbox.x1 + extra,
        y1=bbox.y1,
    )
    return new_bbox


def _write_text(
    page: fitz.Page,
    bbox: BBox,
    text: str,
    font_name: str,
    font_size: float,
    color: Tuple[float, float, float],
    flags: int = 0,
    align: int = fitz.TEXT_ALIGN_LEFT,
) -> bool:
    """
    v0.5: NÃO encolhe a fonte. Mantém size original. Se não couber, retorna False.
    A expansão do bbox deve ser feita ANTES desta função.
    """
    safe_name = _safe_font(font_name, flags)
    rect = _bbox_to_rect(bbox, pad=0.0)

    rc = page.insert_textbox(
        rect,
        text,
        fontname=safe_name,
        fontsize=font_size,
        color=color,
        align=align,
    )
    if rc >= 0:
        return True

    log.warning("Texto não coube no bbox %s mesmo expandido: %r (size=%s)",
                bbox.to_tuple(), text, font_size)
    return False


def _draw_barcode(page: fitz.Page, bbox: BBox, digits: str) -> bool:
    """v0.4: PNG embebido via python-barcode (legível por scanners reais)."""
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
                        # 1) Amostra atributos do texto original (fonte, size, cor, bold)
                        font, size, color, flags = _sample_font_at(page, campo.bbox)
                        safe_name = _safe_font(font, flags)

                        # 2) Monta texto final
                        text = new_value
                        if key == FieldKey.VALOR and not text.strip().startswith("R$"):
                            text = f"R$ {text}"

                        # 3) Mede largura necessária no size ORIGINAL
                        needed_w = _measure_text_width(text, safe_name, size)

                        # 4) Expande o bbox pra direita se precisar
                        write_bbox = _expand_bbox_right(page, campo.bbox, needed_w)

                        # 5) Apaga a região original (não a expandida — só onde tinha texto)
                        _erase_region(page, campo.bbox)

                        # 6) Escreve no bbox (possivelmente expandido) com size ORIGINAL
                        ok = _write_text(page, write_bbox, text, font, size, color, flags)
                        if ok:
                            successes += 1
                            edited.setdefault(key, campo.bbox)
                        else:
                            failures.append(f"#{idx}: texto não coube nem expandido")
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
