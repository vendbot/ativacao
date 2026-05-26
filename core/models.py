"""
models.py · Tipos de domínio
─────────────────────────────────────────────────────────────────────────────
Dataclasses puras que descrevem o que circula entre os módulos.
Sem dependência de PyMuPDF aqui — facilita testar e portar pra API.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum


class FieldKey(str, Enum):
    """Identificadores estáveis dos campos editáveis de um boleto."""
    VALOR           = "valor"
    VENCIMENTO      = "vencimento"
    PAGADOR         = "pagador"
    LINHA_DIGITAVEL = "linha_digitavel"
    CODIGO_BARRAS   = "codigo_barras"


@dataclass(frozen=True)
class BBox:
    """
    Bounding box em coordenadas de PDF (pontos · origem inferior-esquerda
    no padrão PyMuPDF). Use as helpers para converter quando necessário.
    """
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:  return self.x1 - self.x0
    @property
    def height(self) -> float: return self.y1 - self.y0

    def expanded(self, pad: float = 1.0) -> "BBox":
        """Retorna bbox com padding em todos os lados."""
        return BBox(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class CampoDetectado:
    """
    Resultado da detecção de um campo no PDF: onde ele está (bbox), qual
    o valor atual lido (raw_text), fonte e tamanho. Tudo que preciso pra
    substituir o conteúdo mantendo a aparência original.
    """
    key: FieldKey
    bbox: BBox
    raw_text: str
    page_index: int = 0
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    color: Optional[tuple[float, float, float]] = None  # RGB 0-1


@dataclass
class BoletoData:
    """
    Dados extraídos do PDF de origem (PDF 2). Cada campo é Optional porque
    nem todo boleto tem todos os campos, e o usuário pode escolher só alguns.
    """
    valor: Optional[str]            = None  # ex: "1.247,89"
    vencimento: Optional[str]       = None  # ex: "27/05/2026"
    pagador: Optional[str]          = None
    linha_digitavel: Optional[str]  = None  # 47 dígitos formatados
    codigo_barras: Optional[str]    = None  # 44 dígitos puros (sem formatação)

    def get(self, key: FieldKey) -> Optional[str]:
        return getattr(self, key.value, None)

    def has(self, key: FieldKey) -> bool:
        return bool(self.get(key))


@dataclass
class EditRequest:
    """
    Contrato de entrada do motor de edição. É também o payload que vai virar
    o body do endpoint POST /edit quando migrarmos pra FastAPI no Caminho C.
    """
    template_pdf_bytes: bytes
    data: BoletoData
    fields_to_edit: List[FieldKey]   # quais campos da BoletoData injetar


@dataclass
class EditReport:
    """
    Relatório de saída: quais campos foram substituídos com sucesso, quais
    falharam (e por quê), e bytes do PDF resultante.
    """
    output_pdf_bytes: bytes
    edited: Dict[FieldKey, BBox] = field(default_factory=dict)
    skipped: Dict[FieldKey, str] = field(default_factory=dict)  # key -> reason
