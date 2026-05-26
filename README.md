# My PDF AI · Motor Vetorial

Backend FastAPI + PyMuPDF + Frontend HTML que edita boletos no nível **vetorial**.
Substitui valor, vencimento, linha digitável e código de barras preservando fundo,
watermarks e estrutura do PDF original.

---

## 🚀 Deploy no Replit (5 minutos)

### Opção 1 · Importar do GitHub
1. Sobe esse projeto inteiro pro GitHub
2. No Replit, **Create Repl** → **Import from GitHub**
3. Cola a URL → **Import**
4. Clica em **▶ Run**
5. Pronto · vai abrir a URL pública (formato `https://<nome>.<usuario>.repl.co`)

### Opção 2 · Upload direto
1. No Replit, **Create Repl** → **Python** → nomeia o projeto
2. Apaga o `main.py` default
3. Arrasta a pasta inteira deste projeto pro file tree do Replit
4. Clica em **▶ Run**
5. O Replit instala dependências automaticamente e sobe o servidor

### Estrutura esperada no Replit
```
my_pdf_ai/
├── .replit              ← config do Replit (já incluído)
├── replit.nix           ← deps nix (já incluído)
├── requirements.txt     ← deps python
├── README.md
├── my_pdf_ai/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── detector.py
│   │   ├── extractor.py
│   │   ├── barcode_gen.py
│   │   └── editor.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py      ← FastAPI app
│   └── static/
│       └── index.html   ← frontend
```

---

## 💻 Rodar local (alternativa)

```bash
pip install -r requirements.txt
uvicorn my_pdf_ai.api.main:app --reload --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000` no browser.

---

## 🔌 Endpoints HTTP

| Método | Rota | O que faz |
|---|---|---|
| `GET`  | `/`        | Serve o frontend HTML |
| `GET`  | `/health`  | Status do motor |
| `POST` | `/detect`  | Detecta campos no template (multipart: `template`) |
| `POST` | `/extract` | Extrai dados do PDF 2 (multipart: `source`) |
| `POST` | `/edit`    | Motor principal — gera PDF editado (multipart: `template` + `fields` + `data_json`) |

### Exemplo cURL
```bash
curl -X POST http://localhost:8000/edit \
  -F "template=@boleto_bv.pdf" \
  -F "fields=valor,vencimento,linha_digitavel,codigo_barras" \
  -F 'data_json={"valor":"1.247,89","vencimento":"27/05/2026","linha_digitavel":"46191.11000 00000.000042 44860.678018 5 14570001200000","codigo_barras":"46195145700012000001110000000000044486067801"}' \
  --output editado.pdf
```

---

## 🧪 O que o motor faz

1. **Detect** — varredura vetorial via PyMuPDF identifica TODAS as ocorrências
   de cada campo no template (boletos repetem dados em Recibo + Ficha de Compensação)
2. **Extract** — lê PDF 2 e deriva os 44 dígitos do barcode a partir da linha
   digitável Febraban (matemática determinística, sem OCR)
3. **Edit** — `add_redact_annot(fill=None)` REMOVE objetos-texto antigos do stream
   do PDF, depois `insert_textbox` injeta o novo texto. **Fundo, watermarks e linhas
   de tabela ficam intactos** (zero retângulo branco)
4. **Barcode** — gerado vetorialmente como N retângulos pretos ITF (Interleaved 2 of 5)
   diretamente no PDF — nítido em qualquer zoom

---

## 📦 Estado do projeto

**v0.2 · funcionando** (testado em boleto BV real)
- ✅ Detecção de valor, vencimento, linha digitável e barcode
- ✅ Múltiplas ocorrências por campo (Recibo + Ficha de Compensação)
- ✅ Substituição vetorial sem tocar no fundo
- ✅ Frontend HTML standalone
- ⚠️ Campo `pagador` ainda usa heurística textual frágil — v0.3
