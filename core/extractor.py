"""
extractor.py · Extração de dados do PDF de origem (PDF 2)
─────────────────────────────────────────────────────────────────────────────
Lê o PDF 2 e devolve um BoletoData com os valores que conseguir identificar.
Usa as mesmas heurísticas regex do detector.py, mas SEM se preocupar com bbox
— aqui interessa só o valor textual.

Para CODIGO_BARRAS, decodifica os 44 dígitos puros a partir da linha digitável
Febraban (47 dígitos com pontuação). Isso é determinístico, sem OCR.
"""
from __future__ import annotations
import logging
from typing import Optional
import fitz

from .models import BoletoData
from .detector import RE_VALOR, RE_DATA_BR, RE_LINHA_DIGIT

log = logging.getLogger(__name__)


def linha_digitavel_to_44digits(linha: str) -> Optional[str]:
    """
    Converte a linha digitável Febraban (47 dígitos formatados) nos 44
    dígitos brutos do código de barras.

    Layout Febraban (cobrança bancária, banco):
        Campo 1: posições 1-4   = AAABK            (4 dígitos da linha)
        Campo 1: posições 5-9   = UUUUU            (5 dígitos da linha)
        DV campo 1                                 (1 dígito - descartar)
        Campo 2: 10 dígitos                        + DV (descartar)
        Campo 3: 10 dígitos                        + DV (descartar)
        Campo 4: DV geral (1 dígito → posição 5 do barcode)
        Campo 5: 14 dígitos (fator vencimento + valor)

    Resultado dos 44 dígitos do barcode (ordem):
        [Banco(3)] [Moeda(1)] [DV(1)] [Fator(4)] [Valor(10)] [CampoLivre(25)]
    """
    digits = "".join(c for c in linha if c.isdigit())
    if len(digits) != 47:
        log.warning("Linha digitável com %d dígitos (esperado 47)", len(digits))
        return None

    # Extrai partes
    campo1 = digits[0:9]   # 9 dígitos (pos 1-9 do barcode = banco+moeda+...)
    # digits[9] é DV do campo 1 → descarta
    campo2 = digits[10:20]
    # digits[20] DV campo 2 → descarta
    campo3 = digits[21:31]
    # digits[31] DV campo 3 → descarta
    dv     = digits[32]
    campo5 = digits[33:47]  # 14 dígitos: fator(4) + valor(10)

    # Remonta os 44 dígitos na ordem do barcode
    # Barcode: Banco(3) + Moeda(1) + DV(1) + Fator(4) + Valor(10) + CampoLivre(25)
    # Campo1 (9) = Banco(3) + Moeda(1) + CampoLivre[0:5]
    banco_moeda = campo1[0:4]
    campo_livre = campo1[4:9] + campo2 + campo3  # 5 + 10 + 10 = 25
    barcode = banco_moeda + dv + campo5 + campo_livre
    if len(barcode) != 44:
        log.error("Falha ao montar barcode: %d dígitos", len(barcode))
        return None
    return barcode


def extract_from_pdf(pdf_bytes: bytes) -> BoletoData:
    """
    Lê o PDF de origem e retorna BoletoData com o que conseguiu identificar.
    Nenhum campo levanta exceção — se não achar, fica None.
    """
    data = BoletoData()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text("text"))
        full_text = "\n".join(text_parts)

        # Valor: pega o maior R$ da página
        valores = RE_VALOR.findall(full_text)
        if valores:
            def to_float(s): return float(s.replace(".", "").replace(",", "."))
            data.valor = max(valores, key=to_float)

        # Vencimento: pega a primeira data que esteja perto da palavra "Vencimento"
        lines = full_text.splitlines()
        venc = None
        for i, line in enumerate(lines):
            if "vencimento" in line.lower():
                # procura data nesta linha, na seguinte ou na anterior
                for offset in (0, 1, -1, 2):
                    j = i + offset
                    if 0 <= j < len(lines):
                        m = RE_DATA_BR.search(lines[j])
                        if m:
                            venc = m.group(1); break
                if venc: break
        # Fallback: primeira data do documento
        if not venc:
            m = RE_DATA_BR.search(full_text)
            if m:
                venc = m.group(1)
        data.vencimento = venc

        # Linha digitável
        m = RE_LINHA_DIGIT.search(full_text)
        if m:
            data.linha_digitavel = m.group(0)
            data.codigo_barras = linha_digitavel_to_44digits(m.group(0))

        # Pagador: heurística simples — palavra "Pagador" seguida de nome
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("pagador"):
                # Tenta pegar próximas 1-2 linhas como nome
                nome_parts = []
                for j in range(i + 1, min(i + 4, len(lines))):
                    candidate = lines[j].strip()
                    if not candidate: continue
                    # para no próximo label conhecido
                    if any(candidate.lower().startswith(x)
                           for x in ("cpf", "cnpj", "beneficiário", "endereço", "rua",
                                     "av", "linha", "instrucões", "instruções")):
                        if nome_parts: break
                    nome_parts.append(candidate)
                    if len(nome_parts) >= 1:
                        break
                if nome_parts:
                    data.pagador = " · ".join(nome_parts[:1])
                    break

        return data
    finally:
        doc.close()
