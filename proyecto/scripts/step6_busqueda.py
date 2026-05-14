#!/usr/bin/env python3
"""Paso 6 — Búsqueda de equipamiento con Tinyfish Search+Fetch.
Processes BOM items in batches, searches for products matching specs.
"""
import json, time, os, re, sys

# Tinyfish setup
sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")
import tinyfish

# Fireworks for LLM reasoning
import requests as http_requests

API_KEY_FW = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL_FW = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS_FW = {"Authorization": f"Bearer {API_KEY_FW}", "Content-Type": "application/json"}

base = "/opt/data/workspace/tender_procurement/proyecto"

# Load Tinyfish API key
with open("/opt/data/home/.hermes/.env") as f:
    for line in f:
        if line.startswith("TINYFISH_API_KEY="):
            tf_key = line.strip().split("=", 1)[1].strip('"')
            break

tf_client = tinyfish.TinyFish(api_key=tf_key)
print(f"Tinyfish client initialized")

# Load BOM búsqueda
with open(f"{base}/artifacts/step_4_busqueda/BOM_busqueda.json") as f:
    bom_busqueda = json.load(f)

# Load overlay
with open(f"{base}/overlay_usuario.yaml") as f:
    overlay_text = f.read()

items = bom_busqueda['items']
print(f"Items to search: {len(items)}")

out_items_dir = f"{base}/artifacts/step_6_resultados/items"
out_matrices_dir = f"{base}/artifacts/step_6_resultados/matrices"
os.makedirs(out_items_dir, exist_ok=True)
os.makedirs(out_matrices_dir, exist_ok=True)

def search_product(item):
    """Search for products matching item specs using Tinyfish."""
    desc = item['descripcion_limpia']
    grupo = item['grupo']
    params = item.get('params_busqueda', [])
    
    # Build search query
    query_parts = [desc[:100]]
    if params:
        # Add top 3 most relevant params
        for p in params[:3]:
            # Extract key technical terms
            query_parts.append(p[:80])
    
    query = " ".join(query_parts)[:300]
    
    # Add manufacturer preference hints from overlay
    if 'modem' in desc.lower() or 'nms' in desc.lower():
        query += " Comtech"
    elif 'buc' in desc.lower() or 'upconverter' in desc.lower():
        query += " Terrasat"
    elif 'lnb' in desc.lower() or 'low noise block' in desc.lower():
        query += " Norsat"
    
    query += " datasheet specifications buy"
    
    results = []
    try:
        search_resp = tf_client.search.query(query, location="us", language="en")
        for r in search_resp.results[:5]:
            results.append({
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet
            })
    except Exception as e:
        print(f"  Search error: {e}")
    
    return results, query

def fetch_product_page(url):
    """Fetch product page content."""
    try:
        resp = tf_client.fetch.get_contents(urls=[url], format="markdown")
        if resp.results:
            return resp.results[0].text[:5000]  # Limit content
    except Exception as e:
        print(f"  Fetch error for {url}: {e}")
    return ""

def evaluate_candidates(item, candidates):
    """Use LLM to evaluate which candidates match the specs."""
    if not candidates:
        return []
    
    params = item.get('params_busqueda', [])
    
    candidates_str = json.dumps(candidates[:5], indent=1, ensure_ascii=False)
    
    prompt = f"""Eres un ingeniero de telecomunicaciones evaluando productos para una licitación ICAO.

## ITEM BUSCADO
- ID: {item['id']}
- Grupo: {item['grupo']}
- Descripción: {item['descripcion_limpia']}
- Requisitos Hard clave: {json.dumps(params[:8], ensure_ascii=False)}

## CANDIDATOS ENCONTRADOS
{candidates_str}

## TAREA
Evalúa cada candidato y clasifica:
- VALIDO: cumple los requisitos técnicos principales
- CONDICIONADO: cumple parcialmente, con notas
- DESCARTADO: no cumple requisitos clave

Para cada VÁLIDO o CONDICIONADO, extrae:
- Marca
- Modelo  
- Especificaciones técnicas relevantes
- URL fuente

## FORMATO DE SALIDA
JSON array:
[
  {{
    "marca": "...",
    "modelo": "...",
    "clasificacion": "VALIDO" | "CONDICIONADO" | "DESCARTADO",
    "specs_relevantes": "...",
    "cumplimiento": {{"req": "status"}},
    "url_fuente": "...",
    "notas": "..."
  }}
]

DEVUELVE SOLO EL JSON ARRAY. Si no hay candidatos válidos, devuelve array vacío []."""

    payload = {
        "model": "accounts/fireworks/models/glm-5p1",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }
    
    try:
        resp = http_requests.post(BASE_URL_FW, headers=HEADERS_FW, json=payload, timeout=120)
        if resp.status_code != 200:
            return []
        
        content = resp.json()['choices'][0]['message']['content']
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return []

# Process items
BATCH_SIZE = 5  # Search 5 items at a time
total_processed = 0
total_validos = 0
total_sin_candidato = 0

for i in range(0, len(items), BATCH_SIZE):
    batch = items[i:i+BATCH_SIZE]
    batch_num = i // BATCH_SIZE + 1
    total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
    
    print(f"\n{'='*50}")
    print(f"Batch {batch_num}/{total_batches}")
    
    for item in batch:
        item_id = item['id']
        desc_short = item['descripcion_limpia'][:60]
        print(f"\n  {item_id}: {desc_short}...")
        
        # Step 1: Search
        search_results, query_used = search_product(item)
        print(f"  Search: {len(search_results)} results (query: {query_used[:80]}...)")
        
        if not search_results:
            # No results - save as SIN_CANDIDATO
            result = {
                "item_id": item_id,
                "estado": "SIN_CANDIDATO",
                "diagnostico": "Búsqueda no retornó resultados",
                "query_usado": query_used,
                "candidatos": [],
                "logs": [{"paso": "search", "resultado": "0 results"}]
            }
            result_path = f"{out_items_dir}/ITEM-{item_id}_resultado.json"
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            total_sin_candidato += 1
            total_processed += 1
            continue
        
        # Step 2: Fetch top 2 candidate pages
        detailed_candidates = []
        for sr in search_results[:3]:
            content = fetch_product_page(sr['url'])
            if content:
                detailed_candidates.append({
                    "title": sr['title'],
                    "url": sr['url'],
                    "snippet": sr['snippet'],
                    "page_content": content[:3000]
                })
            else:
                detailed_candidates.append({
                    "title": sr['title'],
                    "url": sr['url'],
                    "snippet": sr['snippet'],
                    "page_content": ""
                })
        
        print(f"  Fetched {len(detailed_candidates)} pages")
        
        # Step 3: Evaluate with LLM
        candidatos_eval = evaluate_candidates(item, detailed_candidates)
        validos = [c for c in candidatos_eval if c.get('clasificacion') in ('VALIDO', 'CONDICIONADO')]
        
        estado = "CON_CANDIDATOS" if validos else "SIN_CANDIDATO"
        
        result = {
            "item_id": item_id,
            "estado": estado,
            "query_usado": query_used,
            "candidatos": candidatos_eval,
            "total_candidatos": len(candidatos_eval),
            "total_validos": len(validos),
            "logs": [
                {"paso": "search", "resultados": len(search_results)},
                {"paso": "fetch", "pages": len(detailed_candidates)},
                {"paso": "eval", "validos": len(validos), "total": len(candidatos_eval)}
            ]
        }
        
        result_path = f"{out_items_dir}/ITEM-{item_id}_resultado.json"
        with open(result_path, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # Generate MD
        md = f"# ITEM-{item_id} — Resultado Búsqueda\n\n"
        md += f"**Estado**: {estado}\n"
        md += f"**Query**: {query_used}\n\n"
        
        if validos:
            md += f"## Candidatos Válidos ({len(validos)})\n\n"
            for j, c in enumerate(validos, 1):
                md += f"### Candidato {j}: {c.get('marca','?')} {c.get('modelo','?')}\n"
                md += f"- **Clasificación**: {c.get('clasificacion','?')}\n"
                md += f"- **Specs**: {c.get('specs_relevantes','N/A')[:200]}\n"
                md += f"- **URL**: {c.get('url_fuente','N/A')}\n"
                if c.get('notas'):
                    md += f"- **Notas**: {c['notas']}\n"
                md += "\n"
        else:
            md += "## Sin candidatos válidos\n\n"
            md += f"Se encontraron {len(search_results)} resultados pero ninguno cumple los requisitos.\n"
        
        md_path = f"{out_items_dir}/ITEM-{item_id}_resultado.md"
        with open(md_path, 'w') as f:
            f.write(md)
        
        # Save matrices for valid candidates
        if validos:
            mat_dir = f"{out_matrices_dir}/ITEM-{item_id}"
            os.makedirs(mat_dir, exist_ok=True)
            for j, c in enumerate(validos, 1):
                marca = c.get('marca', 'unknown').replace(' ', '_')
                modelo = c.get('modelo', 'unknown').replace(' ', '_')
                mat = {
                    "item_id": item_id,
                    "candidato_num": j,
                    "marca": c.get('marca', ''),
                    "modelo": c.get('modelo', ''),
                    "clasificacion": c.get('clasificacion', ''),
                    "cumplimiento_reqs": c.get('cumplimiento', {}),
                    "specs_relevantes": c.get('specs_relevantes', ''),
                    "url_fuente": c.get('url_fuente', ''),
                    "notas": c.get('notas', '')
                }
                mat_path = f"{mat_dir}/ITEM-{item_id}_candidato_{j}_{marca}_{modelo}.json"
                with open(mat_path, 'w') as f:
                    json.dump(mat, f, indent=2, ensure_ascii=False)
        
        total_validos += len(validos)
        total_processed += 1
        
        print(f"  Resultado: {estado} ({len(validos)} válidos)")
        
        time.sleep(1)  # Rate limit Tinyfish
    
    print(f"\n  Batch progress: {total_processed} processed, {total_validos} valid, {total_sin_candidato} sin candidato")

print(f"\n{'='*50}")
print(f"✅ PASO 6 COMPLETADO")
print(f"Items procesados: {total_processed}/{len(items)}")
print(f"Candidatos válidos totales: {total_validos}")
print(f"Sin candidato: {total_sin_candidato}")
