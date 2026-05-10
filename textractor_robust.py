#!/usr/bin/env python3
"""
Textractor Robust - Extractor de preguntas de exámenes tolerante a errores

Este script unifica textractor.py y simplificar.py en una sola herramienta
que es más tolerante a campos vacíos/nulos y procesa todo automáticamente.

Características:
- Extracción de PDFs con OpenAI Vision (gpt-5.4-mini por defecto)
- Tolerante a campos vacíos o nulos
- Fusión automática de preguntas cortadas entre páginas
- Simplificación y limpieza del output
- Genera JSON listo para usar
"""

import os
import json
import base64
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-5.4-mini")

fitz = None
openai_client = None

def _ensure_pdf_dependencies():
    """Carga las dependencias necesarias para procesar PDFs."""
    global fitz, openai_client
    if fitz is None:
        try:
            import fitz as _fitz
            fitz = _fitz
        except ImportError:
            raise ImportError("PyMuPDF (fitz) no está instalado. Ejecuta: pip install pymupdf")
    if openai_client is None:
        try:
            from openai import OpenAI
            openai_client = OpenAI(api_key=OPENAI_API_KEY)
        except ImportError:
            raise ImportError("openai no está instalado. Ejecuta: pip install openai")


# =============================================================================
# UTILIDADES TOLERANTES A ERRORES
# =============================================================================

def safe_get(obj: Any, key: str, default: Any = "") -> Any:
    """Obtiene un valor de forma segura, retornando default si es None o no existe."""
    if obj is None:
        return default
    val = obj.get(key) if isinstance(obj, dict) else None
    return val if val is not None else default


def safe_str(val: Any) -> str:
    """Convierte cualquier valor a string de forma segura."""
    if val is None:
        return ""
    return str(val).strip()


def safe_list(val: Any) -> List:
    """Asegura que el valor sea una lista."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return []


def clean_option(opt: Dict) -> Dict:
    """Limpia una opción asegurando que tenga letra y texto."""
    return {
        "letra": safe_str(safe_get(opt, "letra", "?")),
        "texto": safe_str(safe_get(opt, "texto", ""))
    }


def clean_question(q: Dict) -> Dict:
    """
    Limpia y normaliza una pregunta, manejando campos vacíos.
    Retorna un dict con estructura consistente.
    """
    texto_referencia = safe_str(safe_get(q, "texto_referencia"))
    pregunta = safe_str(safe_get(q, "pregunta"))
    
    # Unificar texto_referencia y pregunta
    if texto_referencia and pregunta:
        texto_final = f"{texto_referencia}\n{pregunta}"
    elif texto_referencia:
        texto_final = texto_referencia
    else:
        texto_final = pregunta
    
    # Limpiar opciones
    opciones_raw = safe_list(safe_get(q, "opciones", []))
    opciones = [clean_option(opt) for opt in opciones_raw if safe_get(opt, "texto")]
    
    respuesta = safe_str(safe_get(q, "respuesta_correcta", "no_marcada"))
    if not respuesta:
        respuesta = "no_marcada"
    
    return {
        "numero": safe_str(safe_get(q, "numero", "?")),
        "pregunta": texto_final,
        "opciones": opciones,
        "respuesta_correcta": respuesta,
        "pagina_origen": safe_get(q, "pagina_origen", safe_get(q, "pagina", 0)),
        "incompleta": bool(safe_get(q, "incompleta", False)),
        "fusionada_desde_pagina": safe_get(q, "fusionada_desde_pagina"),
    }


# =============================================================================
# EXTRACCIÓN CON OPENAI VISION (TOLERANTE A ERRORES)
# =============================================================================

def setup_vision_model():
    """Configura el cliente de OpenAI y retorna el nombre del modelo."""
    _ensure_pdf_dependencies()
    return OPENAI_VISION_MODEL


def pdf_page_to_image(pdf_path: str, page_num: int, dpi: int = 200) -> bytes:
    """Convierte una página específica del PDF a imagen PNG."""
    _ensure_pdf_dependencies()
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    
    doc.close()
    return img_bytes


def get_pdf_page_count(pdf_path: str) -> int:
    """Obtiene el número total de páginas del PDF."""
    _ensure_pdf_dependencies()
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def extract_questions_from_page(model_name: str, image_bytes: bytes, page_num: int) -> dict:
    """
    Usa OpenAI Vision para extraer preguntas, TOLERANTE A ERRORES.
    Incluye retry con backoff para 429.
    """
    import time

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    image_data_url = f"data:image/png;base64,{image_b64}"

    prompt = """Eres un experto transcribiendo exámenes. Analiza esta imagen de un examen o evaluación y extrae TODAS las preguntas con sus opciones de respuesta.

INSTRUCCIONES CRÍTICAS:

1. **TRANSCRIPCIÓN EXACTA**: Copia el texto EXACTAMENTE como aparece, incluyendo errores tipográficos si los hay.

2. **TEXTOS DE REFERENCIA**: Si hay un párrafo, caso clínico, lectura o enunciado que precede a una o varias preguntas, DEBES incluirlo en "texto_referencia". Es común en exámenes de medicina, derecho, etc.

3. **RESPUESTA CORRECTA**: 
   - Si ves alguna marca (círculo, subrayado, check, letra resaltada), indica esa letra en "respuesta_correcta"
   - Si NO hay ninguna marca visible, usa "no_marcada"

4. **CAMPOS OBLIGATORIOS**: SIEMPRE incluye estos campos, usa string vacío "" si no hay contenido:
   - "numero": string con el número de pregunta
   - "texto_referencia": string (vacío "" si no hay)
   - "pregunta": string con el texto de la pregunta
   - "opciones": array de objetos con "letra" y "texto"
   - "respuesta_correcta": string ("no_marcada" si no hay marca visible)
   - "incompleta": boolean

5. **PREGUNTAS CORTADAS/INCOMPLETAS**:
   - Si al INICIO de la página hay texto que parece ser CONTINUACIÓN de algo anterior, ponlo en "contenido_inicio_continuacion"
   - Si al FINAL de la página una pregunta parece CORTADA, marca "pregunta_final_incompleta": true

6. **TABLAS Y CUADROS**: Si hay tablas, reconstruirlas en formato ASCII/markdown.

Responde ÚNICAMENTE con JSON válido:
{
    "pagina": <número>,
    "contenido_inicio_continuacion": "<texto o string vacío>",
    "preguntas": [
        {
            "numero": "<número o identificador>",
            "texto_referencia": "<texto o string vacío>",
            "pregunta": "<texto de la pregunta>",
            "opciones": [
                {"letra": "a", "texto": "<texto>"},
                {"letra": "b", "texto": "<texto>"}
            ],
            "respuesta_correcta": "<letra o 'no_marcada'>",
            "incompleta": false
        }
    ],
    "pregunta_final_incompleta": false,
    "notas": ""
}

IMPORTANTE: NUNCA uses null, usa strings vacíos "" o arrays vacíos [] en su lugar."""

    max_retries = 5
    base_delay = 5

    for attempt in range(max_retries + 1):
        try:
            response = openai_client.chat.completions.create(
                model=model_name,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    }
                ],
            )
            response_text = response.choices[0].message.content or ""

            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            result = json.loads(response_text.strip())
            result = sanitize_page_result(result, page_num)
            return result

        except json.JSONDecodeError as e:
            return create_empty_page_result(page_num, f"Error al parsear respuesta de OpenAI: {str(e)}")
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = any(keyword in error_str for keyword in [
                '429', 'rate limit', 'too many requests', 'resource exhausted',
                'quota', 'resourceexhausted'
            ])

            if is_rate_limit and attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), 60)
                print(f"\n⚠️  Rate limited (429), esperando {delay}s antes de reintentar (intento {attempt + 1}/{max_retries})...", flush=True)
                time.sleep(delay)
                continue

            return create_empty_page_result(page_num, f"Error al procesar página: {str(e)}")


def sanitize_page_result(result: dict, page_num: int) -> dict:
    """
    Sanitiza el resultado de una página, convirtiendo nulls a valores por defecto.
    """
    return {
        "pagina": page_num + 1,
        "contenido_inicio_continuacion": safe_str(safe_get(result, "contenido_inicio_continuacion")),
        "preguntas": safe_list(safe_get(result, "preguntas", [])),
        "pregunta_final_incompleta": bool(safe_get(result, "pregunta_final_incompleta", False)),
        "notas": safe_str(safe_get(result, "notas"))
    }


def create_empty_page_result(page_num: int, error_msg: str = "") -> dict:
    """Crea un resultado de página vacío para casos de error."""
    return {
        "pagina": page_num + 1,
        "contenido_inicio_continuacion": "",
        "preguntas": [],
        "pregunta_final_incompleta": False,
        "notas": error_msg
    }


# =============================================================================
# FUSIÓN DE PREGUNTAS CORTADAS
# =============================================================================

def merge_split_questions(pages_data: List[Dict]) -> List[Dict]:
    """
    Fusiona preguntas que fueron cortadas entre páginas.
    Versión tolerante a campos vacíos.
    """
    if len(pages_data) < 2:
        return pages_data
    
    merged_pages = []
    
    for i, current_page in enumerate(pages_data):
        page_copy = json.loads(json.dumps(current_page))
        
        if i > 0:
            prev_page = merged_pages[-1] if merged_pages else None
            
            if prev_page and safe_get(prev_page, "pregunta_final_incompleta", False):
                prev_preguntas = safe_list(safe_get(prev_page, "preguntas"))
                if prev_preguntas:
                    last_question = prev_preguntas[-1]
                    
                    # Caso 1: Hay contenido_inicio_continuacion
                    continuation = safe_str(safe_get(page_copy, "contenido_inicio_continuacion"))
                    if continuation:
                        _merge_continuation_into_question(last_question, continuation, page_copy.get("pagina", 0))
                        page_copy["contenido_inicio_continuacion"] = ""
                        page_copy["notas"] = safe_str(safe_get(page_copy, "notas")) + f" [Continuación fusionada con página {prev_page.get('pagina', '?')}]"
                    
                    # Caso 2: Primera pregunta parece ser continuación
                    elif safe_list(safe_get(page_copy, "preguntas")):
                        first_question = page_copy["preguntas"][0]
                        
                        if _is_orphan_options(first_question):
                            _merge_orphan_options(last_question, first_question, page_copy.get("pagina", 0))
                            page_copy["preguntas"] = page_copy["preguntas"][1:]
                            page_copy["notas"] = safe_str(safe_get(page_copy, "notas")) + f" [Opciones fusionadas con pregunta {safe_get(last_question, 'numero', '?')} de página {prev_page.get('pagina', '?')}]"
                            prev_page["pregunta_final_incompleta"] = False
        
        merged_pages.append(page_copy)
    
    return merged_pages


def _is_orphan_options(question: Dict) -> bool:
    """Detecta si una 'pregunta' es en realidad opciones huérfanas."""
    import re
    
    numero = safe_str(safe_get(question, "numero")).lower()
    pregunta_text = safe_str(safe_get(question, "pregunta")).lower()
    
    if "sin_numero" in numero:
        return True
    
    if re.match(r'^[a-e]\)', pregunta_text):
        return True
    
    if pregunta_text.count(')') >= 3:
        return True
    
    return False


def _merge_continuation_into_question(question: Dict, continuation: str, from_page: int):
    """Fusiona texto de continuación en una pregunta incompleta."""
    opciones = safe_list(safe_get(question, "opciones"))
    
    if not opciones:
        current_text = safe_str(safe_get(question, "pregunta"))
        question["pregunta"] = f"{current_text} {continuation}".strip()
    else:
        last_opt_text = safe_str(safe_get(opciones[-1], "texto"))
        question["opciones"][-1]["texto"] = f"{last_opt_text} {continuation}".strip()
    
    question["incompleta"] = False
    question["fusionada_desde_pagina"] = from_page


def _merge_orphan_options(incomplete_question: Dict, orphan_question: Dict, from_page: int):
    """Fusiona opciones huérfanas con la pregunta incompleta anterior."""
    import re
    
    orphan_options = safe_list(safe_get(orphan_question, "opciones"))
    
    if orphan_options:
        if not safe_get(incomplete_question, "opciones"):
            incomplete_question["opciones"] = orphan_options
        else:
            existing_letters = {opt.get("letra", "") for opt in incomplete_question.get("opciones", [])}
            for opt in orphan_options:
                if opt.get("letra", "") not in existing_letters:
                    incomplete_question["opciones"].append(opt)
    
    orphan_text = safe_str(safe_get(orphan_question, "pregunta"))
    if orphan_text and not orphan_options:
        option_matches = re.findall(r'([a-e])\)\s*(.+?)(?=[a-e]\)|$)', orphan_text, re.DOTALL)
        for letter, text in option_matches:
            existing_opts = safe_list(safe_get(incomplete_question, "opciones"))
            if not any(opt.get("letra") == letter for opt in existing_opts):
                if "opciones" not in incomplete_question:
                    incomplete_question["opciones"] = []
                incomplete_question["opciones"].append({
                    "letra": letter,
                    "texto": text.strip()
                })
    
    incomplete_question["incompleta"] = False
    incomplete_question["fusionada_desde_pagina"] = from_page
    
    if safe_get(incomplete_question, "opciones"):
        incomplete_question["opciones"].sort(key=lambda x: safe_get(x, "letra", "z"))


# =============================================================================
# CONSOLIDACIÓN Y SIMPLIFICACIÓN
# =============================================================================

def extract_all_questions(data: Dict) -> List[Dict]:
    """
    Extrae todas las preguntas de cualquier estructura de JSON.
    Maneja múltiples formatos:
    - {"preguntas": [...]}
    - {"paginas": [{"preguntas": [...]}]}
    - {"paginas_detalle": [{"preguntas": [...]}]}
    - [...]  (array directo)
    """
    all_questions = []
    
    # Caso: Array directo
    if isinstance(data, list):
        return data
    
    # Caso: Tiene "preguntas" en la raíz
    if "preguntas" in data and isinstance(data["preguntas"], list):
        # Verificar si son preguntas directas o páginas
        if data["preguntas"] and isinstance(data["preguntas"][0], dict):
            first_item = data["preguntas"][0]
            if "pregunta" in first_item or "texto" in first_item:
                # Son preguntas directas
                for q in data["preguntas"]:
                    q_clean = q.copy()
                    if "pagina_origen" not in q_clean:
                        q_clean["pagina_origen"] = 0
                    all_questions.append(q_clean)
                return all_questions
    
    # Caso: Tiene "paginas" o "paginas_detalle"
    pages_key = None
    for key in ["paginas", "paginas_detalle"]:
        if key in data and isinstance(data[key], list):
            pages_key = key
            break
    
    if pages_key:
        for page in data[pages_key]:
            page_num = safe_get(page, "pagina", 0)
            for question in safe_list(safe_get(page, "preguntas")):
                q_copy = question.copy()
                q_copy["pagina_origen"] = page_num
                all_questions.append(q_copy)
    
    # Si no encontró nada, intentar con "preguntas" como páginas
    elif "preguntas" in data:
        for item in safe_list(data.get("preguntas", [])):
            if "preguntas" in item:  # Es una página
                page_num = safe_get(item, "pagina", 0)
                for q in safe_list(item.get("preguntas", [])):
                    q_copy = q.copy()
                    q_copy["pagina_origen"] = page_num
                    all_questions.append(q_copy)
            else:  # Es una pregunta directa
                all_questions.append(item)
    
    return all_questions


def consolidate_questions(pages_data: List[Dict]) -> List[Dict]:
    """Consolida todas las preguntas en una lista única."""
    all_questions = []
    
    for page in pages_data:
        page_num = safe_get(page, "pagina", 0)
        for question in safe_list(safe_get(page, "preguntas")):
            q_copy = question.copy()
            q_copy["pagina_origen"] = page_num
            all_questions.append(q_copy)
    
    return all_questions


def simplify_questions(questions: List[Dict]) -> List[Dict]:
    """
    Simplifica las preguntas a un formato limpio para uso final.
    """
    simplified = []
    
    for q in questions:
        cleaned = clean_question(q)
        
        # Solo agregar si tiene contenido real
        if cleaned["pregunta"].strip():
            simplified.append({
                "pregunta": cleaned["pregunta"],
                "opciones": cleaned["opciones"],
            })
    
    return simplified


# =============================================================================
# PROCESAMIENTO PRINCIPAL
# =============================================================================

def process_pdf(pdf_path: str, output_path: Optional[str] = None, 
                start_page: int = 0, end_page: Optional[int] = None,
                verbose: bool = True, simplify: bool = True) -> dict:
    """
    Procesa un PDF completo extrayendo preguntas de cada página.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"No se encontró el archivo: {pdf_path}")
    
    if verbose:
        print(f"🤖 Inicializando OpenAI Vision ({OPENAI_VISION_MODEL})...")
    model = setup_vision_model()
    
    total_pages = get_pdf_page_count(pdf_path)
    end_page = end_page if end_page is not None else total_pages
    end_page = min(end_page, total_pages)
    
    if verbose:
        print(f"📄 PDF: {os.path.basename(pdf_path)}")
        print(f"📊 Total de páginas: {total_pages}")
        print(f"📖 Procesando páginas {start_page + 1} a {end_page}")
        print("-" * 50)
    
    pages_data = []
    
    for page_num in range(start_page, end_page):
        if verbose:
            print(f"⏳ Procesando página {page_num + 1}/{end_page}...", end=" ", flush=True)
        
        try:
            image_bytes = pdf_page_to_image(pdf_path, page_num)
            page_result = extract_questions_from_page(model, image_bytes, page_num)
            pages_data.append(page_result)
            
            num_questions = len(safe_list(safe_get(page_result, "preguntas")))
            has_continuation = bool(safe_str(safe_get(page_result, "contenido_inicio_continuacion")))
            is_incomplete = bool(safe_get(page_result, "pregunta_final_incompleta", False))
            
            if verbose:
                status_parts = []
                if num_questions > 0:
                    status_parts.append(f"{num_questions} pregunta(s)")
                if has_continuation:
                    status_parts.append("📎 continuación detectada")
                if is_incomplete:
                    status_parts.append("✂️ pregunta cortada al final")
                
                if status_parts:
                    print(f"✅ {', '.join(status_parts)}")
                else:
                    print(f"ℹ️  Sin preguntas")
                    
        except Exception as e:
            if verbose:
                print(f"❌ Error: {str(e)}")
            pages_data.append(create_empty_page_result(page_num, f"Error: {str(e)}"))
        
        # Delay between pages to respect API rate limits (even on paid plans)
        import time
        if page_num < end_page - 1:  # No delay after last page
            time.sleep(3)
        # Guardar progreso cada 20 páginas
        if output_path and (page_num + 1) % 20 == 0:
            _save_progress(output_path, pdf_path, pages_data, page_num + 1)
    
    # Guardar datos crudos
    if output_path:
        raw_path = output_path.replace('.json', '_raw.json')
        with open(raw_path, 'w', encoding='utf-8') as f:
            json.dump({
                "archivo": os.path.basename(pdf_path),
                "estado": "pre_fusion",
                "total_paginas": len(pages_data),
                "paginas_detalle": pages_data
            }, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"💾 Datos crudos guardados en: {raw_path}")
    
    if verbose:
        print("-" * 50)
        print("🔗 Fusionando preguntas cortadas entre páginas...")
    
    merged_pages = merge_split_questions(pages_data)
    all_questions = consolidate_questions(merged_pages)
    
    # Resultado completo
    result = {
        "archivo": os.path.basename(pdf_path),
        "total_paginas_procesadas": len(merged_pages),
        "total_preguntas": len(all_questions),
        "preguntas": all_questions,
        "paginas_detalle": merged_pages
    }
    
    if verbose:
        print(f"🎉 Proceso completado!")
        print(f"📊 Total de preguntas extraídas: {result['total_preguntas']}")
    
    # Guardar resultado completo
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"💾 Resultado completo guardado en: {output_path}")
    
    # Generar versión simplificada
    if simplify and output_path:
        simplified_questions = simplify_questions(all_questions)
        simple_path = output_path.replace('.json', '_simple.json')
        with open(simple_path, 'w', encoding='utf-8') as f:
            json.dump(simplified_questions, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"✨ Versión simplificada guardada en: {simple_path}")
            print(f"   ({len(simplified_questions)} preguntas con contenido válido)")
    
    return result


def _save_progress(output_path: str, pdf_path: str, pages_data: List, page_num: int):
    """Guarda el progreso parcial."""
    partial_path = output_path.replace('.json', '_parcial.json')
    with open(partial_path, 'w', encoding='utf-8') as f:
        json.dump({
            "archivo": os.path.basename(pdf_path),
            "estado": "procesando",
            "paginas_procesadas": page_num,
            "paginas_detalle": pages_data
        }, f, ensure_ascii=False, indent=2)


def process_existing_json(input_path: str, output_path: Optional[str] = None, verbose: bool = True) -> List[Dict]:
    """
    Procesa un JSON existente (de textractor.py o similar) y lo simplifica.
    Maneja múltiples formatos de entrada.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"No se encontró el archivo: {input_path}")
    
    if verbose:
        print(f"📂 Cargando {input_path}...")
    
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extraer todas las preguntas de cualquier formato
    all_questions = extract_all_questions(data)
    
    if verbose:
        print(f"📊 Encontradas {len(all_questions)} preguntas en bruto")
    
    # Simplificar
    simplified = simplify_questions(all_questions)
    
    if verbose:
        print(f"✅ {len(simplified)} preguntas con contenido válido")
    
    # Guardar si se especifica output
    if output_path is None:
        output_path = input_path.replace('.json', '_simple.json')
        if output_path == input_path:
            output_path = "resultado_simple.json"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(simplified, f, ensure_ascii=False, indent=2)
    
    if verbose:
        print(f"💾 Guardado en: {output_path}")
    
    return simplified


# =============================================================================
# CLI PRINCIPAL
# =============================================================================

def main():
    """Punto de entrada principal del script."""
    parser = argparse.ArgumentParser(
        description="Extrae preguntas de exámenes desde PDFs usando OpenAI Vision (versión robusta)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:

  # Procesar un PDF nuevo:
  python textractor_robust.py examen.pdf
  python textractor_robust.py examen.pdf -o resultado.json
  python textractor_robust.py examen.pdf --start 1 --end 5

  # Simplificar un JSON existente:
  python textractor_robust.py --simplify resultado_existente.json
  python textractor_robust.py --simplify resultado.json -o limpio.json

  # Cargar y reprocesar datos crudos:
  python textractor_robust.py --load-raw resultado_raw.json
        """
    )
    
    parser.add_argument("input", nargs="?", help="Ruta al archivo PDF o JSON a procesar")
    parser.add_argument("-o", "--output", help="Ruta del archivo JSON de salida")
    parser.add_argument("--simplify", action="store_true",
                        help="Simplificar un JSON existente en lugar de procesar un PDF")
    parser.add_argument("--load-raw", action="store_true",
                        help="Cargar un archivo raw y solo hacer la fusión")
    parser.add_argument("--start", type=int, default=1, 
                        help="Página inicial (1-indexed, default: 1)")
    parser.add_argument("--end", type=int, default=None,
                        help="Página final (1-indexed, default: última)")
    parser.add_argument("--no-simplify", action="store_true",
                        help="No generar versión simplificada automática")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Modo silencioso (sin mensajes de progreso)")
    
    args = parser.parse_args()
    
    if not args.input:
        parser.print_help()
        return 1
    
    try:
        # Modo: Simplificar JSON existente
        if args.simplify:
            process_existing_json(
                input_path=args.input,
                output_path=args.output,
                verbose=not args.quiet
            )
            return 0
        
        # Modo: Cargar raw y fusionar
        if args.load_raw:
            if not os.path.exists(args.input):
                print(f"❌ Error: No se encontró el archivo: {args.input}")
                return 1
            
            print(f"📂 Cargando datos crudos desde {args.input}...")
            with open(args.input, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            pages_data = safe_list(safe_get(raw_data, "paginas_detalle"))
            pdf_basename = safe_str(safe_get(raw_data, "archivo", "desconocido.pdf"))
            
            print(f"🔗 Fusionando {len(pages_data)} páginas...")
            merged_pages = merge_split_questions(pages_data)
            all_questions = consolidate_questions(merged_pages)
            
            result = {
                "archivo": pdf_basename,
                "total_paginas_procesadas": len(merged_pages),
                "total_preguntas": len(all_questions),
                "preguntas": all_questions,
                "paginas_detalle": merged_pages
            }
            
            output_path = args.output or args.input.replace('_raw.json', '.json')
            if output_path == args.input:
                output_path = "resultado_fusionado.json"
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"🎉 Resultado guardado en: {output_path}")
            
            # También generar versión simplificada
            if not args.no_simplify:
                simplified = simplify_questions(all_questions)
                simple_path = output_path.replace('.json', '_simple.json')
                with open(simple_path, 'w', encoding='utf-8') as f:
                    json.dump(simplified, f, ensure_ascii=False, indent=2)
                print(f"✨ Versión simplificada: {simple_path}")
            
            return 0
        
        # Modo: Procesar PDF
        start_page = max(0, args.start - 1)
        end_page = args.end
        
        output_path = args.output
        if output_path is None:
            pdf_name = Path(args.input).stem
            output_path = f"{pdf_name}_preguntas.json"
        
        process_pdf(
            pdf_path=args.input,
            output_path=output_path,
            start_page=start_page,
            end_page=end_page,
            verbose=not args.quiet,
            simplify=not args.no_simplify
        )
        
        return 0
        
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        return 1
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
