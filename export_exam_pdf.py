#!/usr/bin/env python3
"""
Exporta exámenes JSON al mismo estilo visual del frontend como HTML y PDF.
Uso: python3 export_exam_pdf.py <archivo.json> [--no-pdf]
"""

import json
import sys
import os
import subprocess
import html
from pathlib import Path


def load_exam(json_path: str) -> list[dict]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "preguntas" in data:
        return data["preguntas"]
    raise ValueError(f"Formato no reconocido en {json_path}")


def render_justification(text: str) -> str:
    """Convierte el texto de justificación en HTML con formato."""
    if not text:
        return ""
    lines = html.escape(text).split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # Líneas de separador
        if stripped == "---":
            result.append('<hr class="separator">')
        # Encabezados tipo "1) ...", "2) ..."
        elif stripped and stripped[0].isdigit() and len(stripped) > 2 and stripped[1] == ")":
            result.append(f'<p class="just-point"><span class="just-num">{stripped[:2]}</span>{stripped[2:]}</p>')
        # Listas con guión
        elif stripped.startswith("- "):
            result.append(f'<p class="just-list-item">• {stripped[2:]}</p>')
        # Línea vacía
        elif stripped == "":
            result.append('<br>')
        else:
            result.append(f'<p>{line}</p>')
    return "\n".join(result)


def render_question(q: dict, index: int) -> str:
    pregunta = html.escape(q.get("pregunta", "")).replace("\n", "<br>")
    opciones = q.get("opciones", [])
    respuesta_correcta = q.get("respuesta_correcta", "")
    justificacion = q.get("justificacion", "")

    # Normalizar respuesta_correcta a lista
    if isinstance(respuesta_correcta, str):
        correctas = [respuesta_correcta.strip().upper()]
    elif isinstance(respuesta_correcta, list):
        correctas = [r.strip().upper() for r in respuesta_correcta]
    else:
        correctas = []

    # Renderizar opciones
    options_html = []
    for op in opciones:
        letra = html.escape(op.get("letra", ""))
        texto = html.escape(op.get("texto", ""))
        is_correct = letra.upper() in correctas
        option_class = "exam-option correct-answer" if is_correct else "exam-option"
        letter_class = "option-letter correct" if is_correct else "option-letter"
        options_html.append(f"""
        <div class="{option_class}">
          <span class="{letter_class}">{letra})</span>
          <span class="option-text">{texto}</span>
          {'<span class="correct-badge">✓ Correcta</span>' if is_correct else ''}
        </div>""")

    # Renderizar justificación
    just_html = ""
    if justificacion:
        just_html = f"""
        <div class="analysis-section">
          <div class="analysis-label">JUSTIFICACIÓN</div>
          <div class="justification-text">
            {render_justification(justificacion)}
          </div>
        </div>"""

    return f"""
    <div class="exam-card">
      <div class="card-header">
        <span class="question-number">Pregunta {index}</span>
      </div>
      <div class="exam-question">{pregunta}</div>
      <div class="exam-options">
        {"".join(options_html)}
      </div>
      {just_html}
    </div>"""


def generate_html(questions: list[dict], title: str) -> str:
    cards_html = "\n".join(render_question(q, i + 1) for i, q in enumerate(questions))
    total = len(questions)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    /* ── Variables ── */
    :root {{
      --bg-primary:    #0d1117;
      --bg-secondary:  #161b22;
      --bg-tertiary:   #21262d;
      --border-primary: #30363d;
      --text-primary:  #e6edf3;
      --text-secondary:#8b949e;
      --text-muted:    #6e7681;
      --accent-blue:   #1f6feb;
      --accent-green:  #2ea043;
      --radius-sm:     4px;
      --radius-md:     6px;
      --radius-lg:     12px;
    }}

    /* ── Reset ── */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      color: var(--text-primary);
      background: var(--bg-primary);
      padding: 24px;
    }}

    /* ── Cover / Header ── */
    .cover {{
      text-align: center;
      padding: 40px 20px 32px;
      margin-bottom: 32px;
      border-bottom: 2px solid var(--border-primary);
    }}
    .cover h1 {{
      font-size: 26px;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 8px;
    }}
    .cover .subtitle {{
      font-size: 14px;
      color: var(--text-secondary);
    }}
    .cover .badge {{
      display: inline-block;
      margin-top: 12px;
      background: rgba(31, 111, 235, 0.15);
      border: 1px solid rgba(31, 111, 235, 0.4);
      color: var(--accent-blue);
      padding: 4px 14px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
    }}

    /* ── Exam Container ── */
    .exam-container {{
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}

    /* ── Exam Card ── */
    .exam-card {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      padding: 20px;
      page-break-inside: avoid;
    }}

    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}

    .question-number {{
      font-size: 12px;
      font-weight: 600;
      color: var(--accent-blue);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}

    /* ── Question Text ── */
    .exam-question {{
      font-size: 15px;
      line-height: 1.6;
      color: var(--text-primary);
      margin-bottom: 16px;
      padding: 8px;
      border-radius: var(--radius-sm);
      white-space: pre-wrap;
    }}

    /* ── Options ── */
    .exam-options {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}

    .exam-option {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 10px 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
    }}

    .exam-option.correct-answer {{
      background: rgba(34, 197, 94, 0.1);
      border-color: rgba(34, 197, 94, 0.3);
    }}

    .option-letter {{
      font-weight: 600;
      color: var(--text-secondary);
      min-width: 26px;
      flex-shrink: 0;
    }}

    .option-letter.correct {{
      color: var(--accent-green);
    }}

    .option-text {{
      flex: 1;
      color: var(--text-primary);
      font-size: 14px;
    }}

    .correct-badge {{
      font-size: 11px;
      font-weight: 600;
      color: var(--accent-green);
      background: rgba(34, 197, 94, 0.15);
      padding: 2px 8px;
      border-radius: 10px;
      flex-shrink: 0;
      white-space: nowrap;
    }}

    /* ── Analysis / Justification ── */
    .analysis-section {{
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px dashed var(--border-primary);
    }}

    .analysis-label {{
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 8px;
      font-weight: 600;
      letter-spacing: 0.4px;
    }}

    .justification-text {{
      color: #1e3a8a;
      font-size: 13.5px;
      padding: 12px;
      border-radius: var(--radius-sm);
      background: #fef9c3;
      border: 1px solid #fde68a;
    }}

    .justification-text p {{
      margin: 0 0 6px 0;
      line-height: 1.55;
    }}

    .justification-text br {{ display: block; margin: 2px 0; }}

    .just-point {{
      display: flex;
      gap: 4px;
      margin: 4px 0;
    }}
    .just-num {{
      color: #1e3a8a;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .just-list-item {{
      padding-left: 12px;
      color: #1e3a8a;
      margin: 2px 0;
    }}

    hr.separator {{
      border: none;
      border-top: 1px solid rgba(30, 58, 138, 0.25);
      margin: 10px 0;
    }}

    @media print {{
      body {{ background: var(--bg-primary); padding: 12px; }}
      .exam-card {{ page-break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <div class="cover">
    <h1>{html.escape(title)}</h1>
    <div class="subtitle">Resuelto con CloudExam</div>
    <div class="badge">{total} {"pregunta" if total == 1 else "preguntas"}</div>
  </div>

  <div class="exam-container">
    {cards_html}
  </div>
</body>
</html>"""


def export(json_path: str, generate_pdf: bool = True):
    path = Path(json_path)
    if not path.exists():
        print(f"ERROR: No existe el archivo {json_path}")
        sys.exit(1)

    questions = load_exam(json_path)
    title = path.stem.replace("_", " ").title()

    html_path = path.with_suffix(".html")
    pdf_path  = path.with_suffix(".pdf")

    # Generar HTML
    html_content = generate_html(questions, title)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"HTML generado: {html_path}  ({len(questions)} preguntas)")

    # Generar PDF con wkhtmltopdf
    if generate_pdf:
        cmd = [
            "wkhtmltopdf",
            "--enable-local-file-access",
            "--page-size", "A4",
            "--margin-top",    "15mm",
            "--margin-bottom", "15mm",
            "--margin-left",   "12mm",
            "--margin-right",  "12mm",
            "--encoding", "utf-8",
            "--no-outline",
            "--footer-center", f"{title} — Página [page] de [topage]",
            "--footer-font-size", "9",
            "--footer-spacing", "5",
            str(html_path),
            str(pdf_path),
        ]
        print("Generando PDF (puede tardar unos segundos)...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            size_kb = pdf_path.stat().st_size // 1024
            print(f"PDF generado:  {pdf_path}  ({size_kb} KB)")
        else:
            print(f"ERROR al generar PDF:\n{result.stderr}")


def main():
    args = sys.argv[1:]
    if not args:
        print("Uso: python3 export_exam_pdf.py <archivo.json> [--no-pdf]")
        print("\nEjemplos:")
        print("  python3 export_exam_pdf.py cendeiss_ultimo.json")
        print("  python3 export_exam_pdf.py cendeiss_2023.json")
        print("  python3 export_exam_pdf.py cendeiss_ultimo.json --no-pdf  # solo HTML")
        sys.exit(0)

    json_file = args[0]
    no_pdf = "--no-pdf" in args
    export(json_file, generate_pdf=not no_pdf)


if __name__ == "__main__":
    main()
