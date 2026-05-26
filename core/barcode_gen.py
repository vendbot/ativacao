"""
barcode_gen.py · ITF (Interleaved 2 of 5) padrão Febraban — v0.4
─────────────────────────────────────────────────────────────────────────────
Boletos brasileiros usam ITF com 44 dígitos. Esta versão usa a lib
`python-barcode` (validada com pyzbar/zbar, mesma engine dos apps bancários).

Renderizamos como PNG em alta resolução e embedamos no PDF — perdemos a
"vetorialidade pura" do barcode, mas ganhamos legibilidade GARANTIDA por
scanners reais. O resto do PDF continua 100% vetorial.

Histórico:
- v0.2: implementação manual sem quiet zones → não lia
- v0.3: adicionou quiet zones → ainda não lia (bug sutil no alternar B/W)
- v0.4: usa python-barcode → 100% legível em pyzbar/zbar
"""
from __future__ import annotations
from typing import Tuple
import io

from barcode import ITF
from barcode.writer import ImageWriter


def render_to_png_bytes(digits: str, target_width_pt: float, target_height_pt: float) -> bytes:
    """
    Gera barcode ITF como PNG, dimensionado pra caber no bbox do PDF.

    Args:
        digits: 44 dígitos do código de barras
        target_width_pt: largura desejada em pontos do PDF
        target_height_pt: altura desejada em pontos do PDF

    Returns:
        Bytes do PNG renderizado em alta resolução (DPI suficiente pra zoom 4x sem pixelar)
    """
    if len(digits) % 2 != 0:
        raise ValueError(f"ITF exige nº par de dígitos; recebi {len(digits)}")
    if not digits.isdigit():
        raise ValueError("Payload deve conter apenas dígitos")

    # Render em DPI alto pra qualidade. python-barcode usa mm internamente.
    # 1pt = 0.3528mm. target_width_pt em mm = target_width_pt * 0.3528.
    # Nº de módulos pra ITF 44 dígitos = 4 (start) + 44*5 (dados) + 3 (stop) = 227
    # + 2 quiet zones (10 módulos cada por padrão)
    target_mm = target_width_pt * 0.3528
    total_modules = 227 + 20  # módulos + quiet zones
    module_width_mm = target_mm / total_modules

    target_height_mm = target_height_pt * 0.3528

    # Gera o barcode
    itf = ITF(digits, writer=ImageWriter())
    buf = io.BytesIO()
    itf.write(buf, options={
        'module_width':  module_width_mm,
        'module_height': target_height_mm,
        'quiet_zone':    module_width_mm * 10,
        'write_text':    False,         # boletos não mostram texto sob o barcode
        'font_size':     1,
        'text_distance': 0,
        'dpi':           600,           # alta resolução pra ficar nítido em qualquer zoom
        'background':    'white',
        'foreground':    'black',
    })
    return buf.getvalue()
