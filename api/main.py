"""
api/main.py · FastAPI · Caminho C
─────────────────────────────────────────────────────────────────────────────
Backend HTTP que envelopa o motor vetorial e SERVE o frontend estático na
mesma URL. Sem CORS, sem build step, deploy num clique.

Endpoints:
  GET  /              → serve o frontend (static/index.html)
  GET  /health        → status do motor
  POST /detect        → analisa o template e retorna campos detectados
  POST /extract       → extrai dados do PDF de origem (PDF 2)
  POST /edit          → motor principal: gera PDF editado vetorialmente

Como rodar local:
    pip install -r requirements.txt
    uvicorn my_pdf_ai.api.main:app --reload --host 0.0.0.0 --port 8000

No Replit: o arquivo .replit já roda esse uvicorn automaticamente.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from my_pdf_ai.core.models import BoletoData, EditRequest, FieldKey
from my_pdf_ai.core import extractor, editor, detector

# ─── Logging ───
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger("my_pdf_ai.api")

# ─── App ───
app = FastAPI(
    title="My PDF AI · Motor Vetorial",
    version="0.2.0",
    description="Edição vetorial pixel-perfect de boletos bancários",
)

# Caminho do front estático: <repo>/my_pdf_ai/static
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ─────────────── Health ───────────────
@app.get("/health")
def health():
    return {"status": "ok", "engine": "pymupdf", "version": "0.2.0"}


# ─────────────── Frontend (index.html) ───────────────
@app.get("/")
def index():
    """Serve o frontend HTML."""
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(
            {"error": "Frontend não encontrado", "expected_at": str(idx)},
            status_code=500
        )
    return FileResponse(idx, media_type="text/html")


# Assets adicionais (se um dia tiver .css/.js separado)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────── Detect ───────────────
@app.post("/detect")
async def detect_endpoint(template: UploadFile = File(...)):
    """
    Analisa o PDF template e retorna a contagem/bboxes de cada campo detectado.
    O front usa essa info pra mostrar o que vai dar pra editar.
    """
    pdf_bytes = await template.read()
    try:
        detections = detector.detect_all(pdf_bytes)
    except Exception as e:
        log.exception("detect falhou")
        raise HTTPException(500, f"Falha ao detectar: {e}")

    response = {}
    for key, campos in detections.items():
        response[key.value] = {
            "count": len(campos),
            "raw_text": campos[0].raw_text if campos else None,
            "bboxes": [c.bbox.to_tuple() for c in campos],
        }
    return {"detections": response}


# ─────────────── Extract ───────────────
@app.post("/extract")
async def extract_endpoint(source: UploadFile = File(...)):
    """
    Extrai dados do PDF de origem (PDF 2). Retorna JSON com cada campo
    (valor, vencimento, pagador, linha_digitavel, codigo_barras) ou null.
    """
    pdf_bytes = await source.read()
    try:
        data = extractor.extract_from_pdf(pdf_bytes)
    except Exception as e:
        log.exception("extract falhou")
        raise HTTPException(500, f"Falha ao extrair: {e}")

    return {
        "valor":           data.valor,
        "vencimento":      data.vencimento,
        "pagador":         data.pagador,
        "linha_digitavel": data.linha_digitavel,
        "codigo_barras":   data.codigo_barras,
    }


# ─────────────── Edit (motor principal) ───────────────
@app.post("/edit")
async def edit_endpoint(
    template:  UploadFile = File(..., description="PDF base (PDF 1)"),
    fields:    str  = Form(..., description="CSV de FieldKey, ex: valor,vencimento"),
    data_json: Optional[str] = Form(None, description="JSON com valores já validados"),
    source:    Optional[UploadFile] = File(None, description="PDF de dados (opcional se data_json fornecido)"),
):
    """
    Edita o template aplicando os campos selecionados.

    Se `data_json` é fornecido, usa esses valores (já validados pelo usuário no front).
    Senão, extrai automaticamente do `source` (modo legacy/sem revisão).
    """
    template_bytes = await template.read()

    # Resolve dados
    if data_json:
        try:
            payload = json.loads(data_json)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"data_json inválido: {e}")
        # Pega só os campos válidos da BoletoData
        valid_keys = {k.value for k in FieldKey}
        clean_payload = {k: v for k, v in payload.items() if k in valid_keys}
        data = BoletoData(**clean_payload)
    elif source is not None:
        source_bytes = await source.read()
        data = extractor.extract_from_pdf(source_bytes)
    else:
        raise HTTPException(400, "É preciso enviar 'data_json' ou 'source'")

    # Resolve fields
    try:
        keys = [FieldKey(s.strip()) for s in fields.split(",") if s.strip()]
    except ValueError as e:
        raise HTTPException(400, f"FieldKey inválido: {e}")

    if not keys:
        raise HTTPException(400, "Nenhum campo especificado em 'fields'")

    req = EditRequest(template_pdf_bytes=template_bytes, data=data, fields_to_edit=keys)
    try:
        report = editor.apply_edits(req)
    except Exception as e:
        log.exception("edit falhou")
        raise HTTPException(500, f"Falha ao editar: {e}")

    edited_count = getattr(report, 'edited_count', {})
    return Response(
        content=report.output_pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Edited-Fields":  ", ".join(
                f"{k.value} ({edited_count.get(k, 1)}x)" for k in report.edited
            ),
            "X-Skipped-Fields": "; ".join(
                f"{k.value}: {r}" for k, r in report.skipped.items()
            ) if report.skipped else "",
        }
    )
