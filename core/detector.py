"""
detector.py · Detecção vetorial de campos no PDF template
─────────────────────────────────────────────────────────────────────────────
Aqui está a diferença real entre Python e o JS que tentamos antes:
o PyMuPDF expõe os objetos-texto do PDF com suas bbox vetoriais nativas.
Não preciso analisar pixels — eu pergunto pro PDF "onde está o texto X?"
e ele me responde com coordenadas em pontos com precisão sub-pixel.

Estratégia de detecção:
- VALOR           → regex de moeda BR (R$, com ponto e vírgula); preferir
                    o valor maior da página (boletos têm várias linhas R$,
                    o "valor do documento" é o de maior magnitude).
- VENCIMENTO      → regex de data brasileira; preferir o que está perto
                    do label "Vencimento".
- LINHA_DIGITAVEL → regex de 47 dígitos com pontuação Febraban.
- CODIGO_BARRAS   → não é texto, é desenho vetorial (linhas verticais
                    pretas). Detectamos via análise de imagens/drawings
                    da página: cluster denso de retângulos pretos finos
                    no terço inferior.

Cada função retorna CampoDetectado ou None. Resiliente: nunca lança em
campo ausente — apenas avisa via logger.
"""
from __future__ import annotations
import re
import logging
from typing import Optional, List, Tuple
import fitz  # PyMuPDF

from .models import BBox, CampoDetectado, FieldKey

log = logging.getLogger(__name__)


# ─────────────── Regex helpers ───────────────
RE_VALOR        = re.compile(r"R\$\s*([\d.]+,\d{2})")
RE_DATA_BR      = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
RE_LINHA_DIGIT  = re.compile(r"\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14}")


def _page_words(page: fitz.Page) -> List[Tuple[float, float, float, float, str, int, int, int]]:
    """
    Lista de words em ordem de leitura. Cada tuple é (x0,y0,x1,y1,word,block,line,word_no).
    Wrap defensivo: se a API mudar de assinatura, ainda retorna lista vazia.
    """
    try:
        return page.get_text("words") or []
    except Exception as e:
        log.warning("page.get_text('words') falhou: %s", e)
        return []


def _bbox_around(words, predicate) -> Optional[BBox]:
    """Une bbox de words contíguas que casam com `predicate`."""
    hits = [w for w in words if predicate(w[4])]
    if not hits:
        return None
    x0 = min(w[0] for w in hits)
    y0 = min(w[1] for w in hits)
    x1 = max(w[2] for w in hits)
    y1 = max(w[3] for w in hits)
    return BBox(x0, y0, x1, y1)


# ─────────────── Campos de texto ───────────────
def detect_valor(page: fitz.Page) -> List[CampoDetectado]:
    """
    Acha TODAS as ocorrências do valor "do Documento" do boleto.
    Heurística: dentre todos os 'R$ X.XXX,XX' da página, escolhe o de maior
    magnitude — esse é o valor do documento na Febraban (os outros costumam
    ser desconto, multa, juros).

    Retorna todas as ocorrências (boleto repete o valor em Recibo + Ficha
    de Compensação). Cada ocorrência é editada separadamente.
    """
    text = page.get_text("text")
    matches = RE_VALOR.findall(text)
    if not matches:
        return []

    def to_float(s: str) -> float:
        return float(s.replace(".", "").replace(",", "."))

    best_str = max(matches, key=to_float)
    # Pega TODAS as ocorrências, não só a primeira
    rects = page.search_for(f"R$ {best_str}", quads=False)
    if not rects:
        rects = page.search_for(best_str, quads=False)
    if not rects:
        return []

    out = []
    for r in rects:
        out.append(CampoDetectado(
            key=FieldKey.VALOR,
            bbox=BBox(r.x0, r.y0, r.x1, r.y1),
            raw_text=f"R$ {best_str}",
            page_index=page.number,
        ))
    return out


def detect_vencimento(page: fitz.Page) -> List[CampoDetectado]:
    """
    Acha TODAS as ocorrências da data de vencimento. Heurística:
    - busca todas as datas brasileiras na página
    - identifica a data mais próxima de label 'Vencimento' (a "data alvo")
    - retorna TODAS as ocorrências dessa mesma data string na página
      (boleto repete vencimento em Recibo + Ficha de Compensação)
    """
    words = _page_words(page)
    if not words:
        return []

    # Coleta todas as datas únicas
    datas = []
    for w in words:
        clean = w[4].strip(",.;")
        if RE_DATA_BR.fullmatch(clean):
            datas.append((w, clean))
    if not datas:
        return []

    # Acha y das labels "Vencimento" pra identificar QUAL data é a alvo
    label_ys = [w[1] for w in words if w[4].lower().startswith("vencimento")]

    if label_ys:
        def dist_to_label(item):
            w = item[0]
            return min(abs(w[1] - ly) for ly in label_ys)
        datas.sort(key=dist_to_label)
        target_str = datas[0][1]
    else:
        # Sem label → usa a data mais à direita (canto sup. direito típico)
        datas.sort(key=lambda item: -item[0][2])
        target_str = datas[0][1]

    # Retorna TODAS as ocorrências exatas da string-alvo
    out = []
    for w, clean in datas:
        if clean == target_str:
            out.append(CampoDetectado(
                key=FieldKey.VENCIMENTO,
                bbox=BBox(w[0], w[1], w[2], w[3]),
                raw_text=clean,
                page_index=page.number,
            ))
    return out


def detect_linha_digitavel(page: fitz.Page) -> List[CampoDetectado]:
    """
    A linha digitável Febraban tem formato fixo, mas aparece DUAS VEZES
    no boleto padrão (Recibo do Pagador + Ficha de Compensação).
    Retorna TODAS as ocorrências.
    """
    text = page.get_text("text")
    m = RE_LINHA_DIGIT.search(text)
    if not m:
        return []
    linha = m.group(0)
    rects = page.search_for(linha, quads=False)
    if not rects:
        # Fallback: busca só os primeiros 11 chars (mais robusto a quebras)
        rects = page.search_for(linha[:11], quads=False)
        if not rects:
            return []
    out = []
    for r in rects:
        out.append(CampoDetectado(
            key=FieldKey.LINHA_DIGITAVEL,
            bbox=BBox(r.x0, r.y0, r.x1, r.y1),
            raw_text=linha,
            page_index=page.number,
        ))
    return out


# ─────────────── Código de barras (vetorial, não-texto) ───────────────
def detect_codigo_barras(page: fitz.Page) -> List[CampoDetectado]:
    """
    O código de barras Interleaved-2-of-5 num boleto NÃO é texto — é uma
    sequência de retângulos vetoriais pretos finos OU uma imagem raster.
    Geralmente aparece UMA vez (Ficha de Compensação), mas retornamos lista
    pra consistência com os outros detectores.

    Estratégia em duas frentes:
    (1) get_drawings() → procura cluster denso de retângulos altos+finos
        no terço inferior da página.
    (2) get_images() → se nada vetorial encontrado, procura imagem larga
        e baixa (proporção ~10:1) no terço inferior.
    """
    page_h = page.rect.height
    page_w = page.rect.width
    y_threshold = page_h * 0.55  # só procura abaixo da metade

    # (1) Tentativa vetorial
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    rects_pretos = []
    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        if isinstance(fill, (tuple, list)) and len(fill) >= 3:
            r_, g_, b_ = fill[0], fill[1], fill[2]
            if max(r_, g_, b_) > 0.25:
                continue
        rect = d.get("rect")
        if rect is None:
            continue
        if rect.y0 < y_threshold:
            continue
        h = rect.y1 - rect.y0
        w = rect.x1 - rect.x0
        if h <= 0 or w <= 0:
            continue
        if h / max(w, 0.01) >= 3 and h >= 8:
            rects_pretos.append(rect)

    if len(rects_pretos) >= 30:
        x0 = min(r.x0 for r in rects_pretos)
        y0 = min(r.y0 for r in rects_pretos)
        x1 = max(r.x1 for r in rects_pretos)
        y1 = max(r.y1 for r in rects_pretos)
        return [CampoDetectado(
            key=FieldKey.CODIGO_BARRAS,
            bbox=BBox(x0, y0, x1, y1),
            raw_text=f"<barcode:{len(rects_pretos)} bars>",
            page_index=page.number,
        )]

    # (2) Tentativa raster
    try:
        images = page.get_image_info(xrefs=True)
    except Exception:
        images = []
    candidatos = []
    for img in images:
        bbox = img.get("bbox")
        if bbox is None:
            continue
        x0_, y0_, x1_, y1_ = bbox
        if y0_ < y_threshold:
            continue
        w = x1_ - x0_; h = y1_ - y0_
        if h <= 0 or w <= 0:
            continue
        aspect = w / h
        if aspect >= 6 and w >= page_w * 0.3:
            candidatos.append((aspect, x0_, y0_, x1_, y1_))
    if candidatos:
        candidatos.sort(key=lambda t: -t[0])
        _, x0, y0, x1, y1 = candidatos[0]
        return [CampoDetectado(
            key=FieldKey.CODIGO_BARRAS,
            bbox=BBox(x0, y0, x1, y1),
            raw_text="<barcode:raster>",
            page_index=page.number,
        )]

    return []


# ─────────────── Dispatcher ───────────────
DETECTORS = {
    FieldKey.VALOR:           detect_valor,
    FieldKey.VENCIMENTO:      detect_vencimento,
    FieldKey.LINHA_DIGITAVEL: detect_linha_digitavel,
    FieldKey.CODIGO_BARRAS:   detect_codigo_barras,
    # PAGADOR omitido por enquanto: heurística mais complexa, deixei pra v2
}


def detect_all(pdf_bytes: bytes) -> dict[FieldKey, List[CampoDetectado]]:
    """
    Roda todos os detectores na primeira página do PDF.
    Retorna dict {FieldKey: [CampoDetectado, ...]} com TODAS as ocorrências
    de cada campo (boletos repetem dados em Recibo + Ficha de Compensação).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        found: dict[FieldKey, List[CampoDetectado]] = {}
        for key, fn in DETECTORS.items():
            try:
                campos = fn(page) or []
                if campos:
                    found[key] = campos
                    bboxes = [c.bbox.to_tuple() for c in campos]
                    log.info("Detectado %s (%d ocorrência(s)): %s",
                             key.value, len(campos), bboxes)
                else:
                    log.info("Não encontrado: %s", key.value)
            except Exception as e:
                log.warning("Detector %s falhou: %s", key.value, e)
        return found
    finally:
        doc.close()
