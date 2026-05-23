#!/usr/bin/env python3
"""Paso 3.2 — Revision 'ojos frescos' con modelo distinto al productor."""
import json, time, requests, re, os

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
MODEL = "accounts/fireworks/models/minimax-m2p7"  # Revisor ≠ productor (deepseek/glm)

base = "/opt/data/workspace/tender_procurement/proyecto"
specs_dir = f"{base}/artifacts/step_3_specs"

# Load TS sections
with open('/tmp/ts_sections.json') as f:
    ts_sections = json.load(f)

# Load review sample
with open('/tmp/review_sample.json') as f:
    review_sample = json.load(f)

eett_context = f"""# EETT - ALCANCE
{ts_sections['alcance']}

# EETT - REQUISITOS GENÉRICOS
{ts_sections['genericos']}

# EETT - REQUISITOS ESPECÍFICOS
{ts_sections['especificos']}"""

# Review in batches of 8
BATCH_SIZE = 8
all_corrections = []

for batch_start in range(0, len(review_sample), BATCH_SIZE):
    batch = review_sample[batch_start:batch_start + BATCH_SIZE]
    batch_num = batch_start // BATCH_SIZE + 1
    total_batches = (len(review_sample) + BATCH_SIZE - 1) // BATCH_SIZE
    
    print(f"\n{'='*50}")
    print(f"Review batch {batch_num}/{total_batches}: {len(batch)} items")
    
    # Compact items for review
    items_for_review = []
    for s in batch:
        compact_reqs = []
        for r in s.get('requerimientos', []):
            compact_reqs.append({
                "req_id": r.get("req_id", ""),
                "texto_verbatim": r.get("texto_verbatim", "")[:200],  # Truncate long text
                "hard_soft": r.get("hard_soft", ""),
                "referencias": r.get("referencias", [])
            })
        items_for_review.append({
            "item_id": s.get("item_id", ""),
            "grupo": s.get("grupo", ""),
            "descripcion": s.get("nombre", "")[:150],
            "requerimientos": compact_reqs
        })
    
    items_str = json.dumps(items_for_review, indent=1, ensure_ascii=False)
    
    prompt = f"""Eres un revisor técnico experto en licitaciones ICAO. Estás haciendo una revisión "ojos frescos" de especificaciones extraídas por otro equipo.

## ITEMS A REVISAR ({len(batch)} items)
{items_str}

## EETT (para verificación)
{eett_context}

## TU TAREA
Para cada item, revisa:
1. **Completitud**: ¿Faltan requisitos obvios de las EETT que no fueron extraídos?
2. **Clasificación hard/soft**: ¿Hay requisitos mal clasificados? (ej. "deseable", "preferiblemente" marcados como Hard, o "deberá" marcado como Soft)
3. **Veracidad**: ¿Las citas/referencias parecen correctas? ¿El texto verbatim es realmente de las EETT?
4. **Duplicados**: ¿Hay requisitos equivalentes repetidos?

## FORMATO DE SALIDA
JSON array con las correcciones encontradas:
[
  {{
    "item_id": "EXP-XXX",
    "estado": "OK" o "CORRECCIONES",
    "correcciones": [
      {{
        "tipo": "CLASIFICACION" | "FALTANTE" | "DUPLICADO" | "REFERENCIA_ERRONEA",
        "req_id": "R-XXX" (si aplica),
        "detalle": "descripción de la corrección",
        "accion_sugerida": "qué hacer"
      }}
    ]
  }}
]

Si un item está OK, pon estado "OK" y correcciones vacías.
DEVUELVE SOLO EL JSON ARRAY."""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.2
    }
    
    t0 = time.time()
    try:
        resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=600)
        elapsed = time.time() - t0
        print(f"HTTP {resp.status_code} in {elapsed:.0f}s")
        
        if resp.status_code != 200:
            print(f"Error: {resp.text[:200]}")
            continue
        
        data = resp.json()
        content = data['choices'][0]['message']['content']
        
        json_match = re.search(r'\[[\s\S]*\]', content)
        if not json_match:
            print("No JSON found")
            continue
        
        try:
            results = json.loads(json_match.group())
        except:
            results = []
            arr_start = content.find('[')
            if arr_start >= 0:
                start = arr_start + 1
                depth = 0
                obj_start = None
                i = start
                while i < len(content):
                    if content[i] == '{':
                        if depth == 0: obj_start = i
                        depth += 1
                    elif content[i] == '}':
                        depth -= 1
                        if depth == 0 and obj_start is not None:
                            try: results.append(json.loads(content[obj_start:i+1]))
                            except: pass
                            obj_start = None
                    i += 1
        
        print(f"Parsed {len(results)} reviews")
        
        ok_count = sum(1 for r in results if r.get('estado') == 'OK')
        corr_count = sum(1 for r in results if r.get('estado') == 'CORRECCIONES')
        total_corrections = sum(len(r.get('correcciones', [])) for r in results)
        
        print(f"  OK: {ok_count}, Con correcciones: {corr_count}, Total correcciones: {total_corrections}")
        
        all_corrections.extend(results)
        
    except Exception as e:
        print(f"Error: {e}")
    
    time.sleep(2)

# Save revision report
report = {
    "tipo": "REVISION_SPECS",
    "modelo_revisor": MODEL,
    "items_revisados": len(review_sample),
    "resultados": all_corrections
}

report_path = f"{base}/artifacts/step_3_specs/revision_specs.json"
with open(report_path, 'w') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

# Stats
ok_total = sum(1 for r in all_corrections if r.get('estado') == 'OK')
corr_total = sum(1 for r in all_corrections if r.get('estado') == 'CORRECCIONES')
total_corrs = sum(len(r.get('correcciones', [])) for r in all_corrections)

# Correction types
corr_types = {}
for r in all_corrections:
    for c in r.get('correcciones', []):
        t = c.get('tipo', '?')
        corr_types[t] = corr_types.get(t, 0) + 1

# Generate MD report
md = f"""# Revisión Ojos Frescos — Paso 3.2

**Modelo revisor**: {MODEL}  
**Items revisados**: {len(review_sample)}  
**OK**: {ok_total} | **Con correcciones**: {corr_total}  
**Total correcciones**: {total_corrs}

## Tipos de correcciones

"""
for t, c in sorted(corr_types.items(), key=lambda x: -x[1]):
    md += f"- **{t}**: {c}\n"

md += "\n## Detalle de correcciones\n\n"
for r in all_corrections:
    if r.get('estado') == 'CORRECCIONES':
        md += f"### {r.get('item_id', '?')}\n\n"
        for c in r.get('correcciones', []):
            md += f"- **{c.get('tipo', '?')}** ({c.get('req_id', 'N/A')}): {c.get('detalle', '')}\n"
            md += f"  - Acción: {c.get('accion_sugerida', '')}\n"
        md += "\n"

md_path = f"{base}/artifacts/step_3_specs/revision_specs.md"
with open(md_path, 'w') as f:
    f.write(md)

print(f"\n{'='*50}")
print(f"✅ REVISIÓN COMPLETADA")
print(f"Items revisados: {len(review_sample)}")
print(f"OK: {ok_total}, Correcciones: {corr_total}")
print(f"Total correcciones: {total_corrs}")
print(f"Tipos: {corr_types}")
print(f"Reporte: {md_path}")
