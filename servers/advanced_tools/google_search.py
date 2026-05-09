"""
Google Search Tool - Búsqueda en Google + Extracción de contenido web
Usa requests + BeautifulSoup con encoding fix
"""

import logging
import os
import asyncio
import re
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.environ.get('GOOGLE_SEARCH_API_KEY', '')
GOOGLE_CX = os.environ.get('GOOGLE_SEARCH_CX', '')


def _extract_content(url: str) -> dict:
    """Extrae contenido de una URL."""
    import requests
    from bs4 import BeautifulSoup
    import ssl
    import urllib3
    
    # Deshabilitar warnings de SSL
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
        }
        
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        
        # Fix encoding
        if response.encoding == 'ISO-8859-1':
            response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remover elementos no deseados
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 
                         'noscript', 'iframe', 'form', 'button', 'input']):
            tag.decompose()
        
        # Obtener título
        title = soup.title.get_text(strip=True) if soup.title else "Sin título"
        
        # Buscar contenido principal
        main = soup.find('main') or soup.find('article') or soup.find('div', {'class': re.compile(r'content|article|post|entry|text', re.I)})
        
        if main:
            paragraphs = main.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4'])
        else:
            paragraphs = soup.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4'])
        
        # Extraer texto
        texts = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if len(text) > 30:  # Ignorar textos muy cortos
                texts.append(text)
        
        content = '\n\n'.join(texts)
        
        if len(content) > 4000:
            content = content[:4000] + "\n\n[...contenido truncado...]"
        
        if len(content) > 200:
            return {'url': url, 'title': title, 'content': content, 'success': True}
        else:
            return {'url': url, 'title': title, 'content': '', 'success': False}
            
    except Exception as e:
        logger.warning(f"Error extrayendo {url}: {e}")
        return {'url': url, 'title': 'Error', 'content': '', 'success': False}


async def google_search(
    query: str,
    state_key: Optional[str] = None,  # legacy alias for target_file
    target_file: Optional[str] = None,
    num_results: int = 3,
    max_results: Optional[int] = None,  # legacy alias for num_results
) -> str:
    """
    Busca en Google y devuelve el contenido extraído.

    - Si se pasa `target_file`, los resultados se anexan a ese archivo del workspace.
    - Si no, se devuelven como texto (Markdown) para que el agente los use directamente.
    """
    try:
        from googleapiclient.discovery import build
        import datetime

        if max_results is not None:
            num_results = max_results
        if target_file is None and state_key is not None:
            target_file = state_key  # backwards compat with old callers

        if not GOOGLE_CX:
            return "❌ Error: No se ha configurado GOOGLE_SEARCH_CX"
        if not GOOGLE_API_KEY:
            return "❌ Error: No se ha configurado GOOGLE_SEARCH_API_KEY"

        num_results = min(num_results, 5)
        logger.info(f"🔍 Buscando: '{query}'")

        try:
            service = build('customsearch', 'v1', developerKey=GOOGLE_API_KEY)
            result = service.cse().list(q=query, cx=GOOGLE_CX, num=num_results, hl="es").execute()
        except Exception as api_error:
            error_msg = str(api_error)
            if 'quota' in error_msg.lower() or 'limit' in error_msg.lower():
                return "❌ Error: cuota de Google Custom Search agotada (100 queries/día)."
            if 'invalid' in error_msg.lower() or 'key' in error_msg.lower():
                return "❌ Error: API key de Google inválida. Verifica GOOGLE_SEARCH_API_KEY."
            logger.error(f"Google API error: {api_error}")
            return f"❌ Error de API de Google: {error_msg[:200]}"

        items = result.get('items', [])
        if not items:
            return f"❌ No se encontraron resultados para: '{query}'"

        urls = [item.get('link') for item in items]
        logger.info(f"📄 Extrayendo contenido de {len(urls)} páginas...")

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = await loop.run_in_executor(
                executor,
                lambda: [_extract_content(url) for url in urls],
            )

        output = [
            f"# 🔍 Investigación: {query}",
            f"*Fecha: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}*\n",
            "---\n",
        ]
        successful = 0
        for i, r in enumerate(results, 1):
            if r['success']:
                successful += 1
                output.append(f"## {i}. {r['title']}")
                output.append(f"🔗 {r['url']}\n")
                output.append(r['content'])
                output.append("\n---\n")

        if successful == 0:
            return "❌ No se pudo extraer contenido de las páginas encontradas."

        formatted = "\n".join(output)

        if target_file:
            from servers.filesystem_service.file_operations import (
                read_file,
                write_file,
                file_exists,
            )
            if file_exists(target_file):
                existing = read_file(target_file)
                write_file(target_file, f"{existing}\n\n{formatted}")
                return f"✅ Se agregaron {successful} fuentes al archivo '{target_file}'."
            write_file(target_file, formatted)
            return f"✅ Se creó '{target_file}' con {successful} fuentes."

        return formatted

    except Exception as e:
        logger.error(f"Error: {e}")
        return f"❌ Error: {str(e)}"
