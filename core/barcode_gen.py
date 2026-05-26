"""
barcode_gen.py · Geração de código de barras ITF (Interleaved 2 of 5)
─────────────────────────────────────────────────────────────────────────────
Boletos bancários no Brasil usam ITF (Interleaved 2 of 5) com exatamente
44 dígitos. Esta implementação gera as barras pretas como retângulos
vetoriais que serão desenhados direto no PDF (não como imagem raster),
preservando nitidez em qualquer zoom.

Por que não usar `python-barcode`?
- python-barcode gera SVG ou PNG. Para inserir vetorial no PDF, eu preciso
  da lista de larguras das barras (narrow/wide) — é só isso que importa.
- Implementar ITF puro é ~50 linhas, sem dependência externa, e dá controle
  total da largura/altura final no PDF.
"""
from __future__ import annotations
from typing import List, Tuple


# Tabela ITF — cada dígito 0-9 é representado por 5 barras (Narrow=0, Wide=1)
ITF_PATTERNS = {
    "0": "00110", "1": "10001", "2": "01001", "3": "11000", "4": "00101",
    "5": "10100", "6": "01100", "7": "00011", "8": "10010", "9": "01010",
}

START_BARS = "0000"  # NNNN
STOP_BARS  = "100"   # WNN


def _interleave_pair(a: str, b: str) -> str:
    """
    ITF entrelaça pares de dígitos: o primeiro vira barras pretas,
    o segundo vira espaços brancos, alternando 5 a 5.
    Retorna string de 10 caracteres: 'A B A B A B A B A B' onde A=padrão de a, B=padrão de b.
    """
    pa, pb = ITF_PATTERNS[a], ITF_PATTERNS[b]
    return "".join(pa[i] + pb[i] for i in range(5))


def build_itf_pattern(digits: str) -> List[Tuple[str, int]]:
    """
    Recebe string de dígitos (deve ter QUANTIDADE PAR, ITF exige isso).
    Retorna lista [(cor, largura_unitária), ...] onde:
      cor = 'B' (preto) ou 'W' (branco)
      largura_unitária = 1 (narrow) ou 2 (wide)  ← módulos
    """
    if len(digits) % 2 != 0:
        raise ValueError(f"ITF exige dígitos em quantidade par, recebi {len(digits)}")
    if not all(c.isdigit() for c in digits):
        raise ValueError("Dígitos inválidos no payload do barcode")

    bars: List[Tuple[str, int]] = []

    # Start
    for ch in START_BARS:
        bars.append(("B" if len(bars) % 2 == 0 else "W", 2 if ch == "1" else 1))

    # Pares de dígitos
    for i in range(0, len(digits), 2):
        a, b = digits[i], digits[i + 1]
        interleaved = _interleave_pair(a, b)
        for j, ch in enumerate(interleaved):
            # Em ITF interleaved, índices pares → barra preta, ímpares → espaço branco
            color = "B" if j % 2 == 0 else "W"
            bars.append((color, 2 if ch == "1" else 1))

    # Stop
    for j, ch in enumerate(STOP_BARS):
        # Stop: padrão começa com barra preta
        color = "B" if j % 2 == 0 else "W"
        bars.append((color, 2 if ch == "1" else 1))

    return bars


def render_to_rects(
    digits: str,
    bbox: Tuple[float, float, float, float],
) -> List[Tuple[float, float, float, float]]:
    """
    Converte o padrão ITF em retângulos pretos prontos pra inserir no PDF.

    Args:
        digits: 44 dígitos do código de barras
        bbox: (x0, y0, x1, y1) em pontos do PDF onde o barcode deve caber

    Returns:
        Lista de bbox (x0,y0,x1,y1) de cada BARRA PRETA. Espaços brancos
        não retornam retângulo (deixam o fundo do PDF aparecer naturalmente).
    """
    pattern = build_itf_pattern(digits)
    x0, y0, x1, y1 = bbox
    total_w = x1 - x0
    total_modules = sum(width for _, width in pattern)
    module_w = total_w / total_modules

    rects: List[Tuple[float, float, float, float]] = []
    cursor = x0
    for color, width in pattern:
        bar_w = width * module_w
        if color == "B":
            rects.append((cursor, y0, cursor + bar_w, y1))
        cursor += bar_w
    return rects
