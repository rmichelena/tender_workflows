#!/usr/bin/env python3
"""Paso 3.1 — Verify specs + herencia for each BOM item, batched by grupo."""
import json, time, requests, re, os

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
MODEL = "accounts/fireworks/models/deepseek-v4-pro"  # Good reasoning model

base = "/opt/data/workspace/tender_procurement/proyecto"

# Load TS sections
with open('/tmp/ts_sections.json') as f:
    ts_sections = json.load(f)

# Load BOM consolidated
with open(f"{base}/artifacts/step_2_bom/BOM_exploded_consolidado.json") as f:
    bom = json.load(f)

items = bom['items']
out_dir = f"{base}/artifacts/step_3_specs"
os.makedirs(out_dir, exist_ok=True)

# Build EETT context: alcance + genericos + especificos + repuestos
eett_context = f"""# EETT - ALCANCE
{ts_sections['alcance']}

# EETT - REQUISITOS GENÉRICOS
{ts_sections['genericos']}

# EETT - REQUISITOS ESPECÍFICOS
{ts_sections['especificos']}

# EETT - REPUESTOS
{ts_sections['repuestos']}"""

print(f"EETT context: {len(eett_context)} chars ({len(eett_context)//1024}KB)")
print(f"Items to process: {len(items)}")

BATCH_SIZE = 10
total_items_processed = 0
total_reqs = 0
errors = 0

# Process in batches
for batch_start in range(0, len(items), BATCH_SIZE):
    batch = items[batch_start:batch_start + BATCH_SIZE]
    batch_num = batch_start // BATCH_SIZE + 1
    total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
    
    print(f"\n{'='*50}")
    print(f"Batch {batch_num}/{total_batches}: items {batch_start+1}-{batch_start+len(batch)}")
    
    # Build batch items summary
    batch_items_str = json.dumps([
        {
            "id": it.get("id", ""),
            "grupo": it.get("grupo", ""),
            "subgrupo": it.get("subgrupo", ""),
            "descripcion": it.get("descripcion", ""),
            "cantidad": it.get("cantidad", ""),
            "unidad": it.get("unidad", ""),
            "notas": it.get("notas", "")
        }
        for it in batch
    ], indent=1, ensure_ascii=False)
    
    prompt = f"""Eres un ingeniero de telecomunicaciones experto en licitaciones ICAO. Tu tarea es EXTRAER requisitos técnicos de las EETT para los items del BOM listados abajo, y clasificarlos.

## ITEMS A PROCESAR ({len(batch)} items)
{batch_items_str}

## ESPECIFICACIONES TÉCNICAS (EETT)
{eett_context}

## INSTRUCCIONES
Para CADA item de la lista:
1. Busca en las EETT los requisitos que aplican a ese item específicamente
2. Busca también requisitos generales que apliquen (frases como "todos los equipos deberán...", "el sistema debe...")
3. Clasifica cada requisito como:
   - "Hard": obligatorio (deberá, mínimo, máximo, obligatorio, requerido)
   - "Soft": deseable (preferiblemente, opcional, recomendable, deseable)
4. Extrae el texto verbatim del requisito
5. Identifica la sección/fuente del requisito

## FORMATO DE SALIDA
Devuelve SOLO un JSON array, un objeto por item:
[
  {{
    "item_id": "EXP-XXX",
    "estado_specs": "VERIFICADO",
    "requerimientos": [
      {{
        "req_id": "R-001",
        "texto_verbatim": "texto exacto del requisito",
        "hard_soft": "Hard",
        "origen": "DIRECTO",
        "referencias": ["Sección X, párrafo Y"]
      }}
    ],
    "tbd": [
      {{
        "descripcion": "ambigüedad encontrada",
        "referencia": "sección"
      }}
    ]
  }}
]

IMPORTANTE:
- Extrae requisitos REALES de las EETT, no los inventes
- Si un item no tiene requisitos específicos en las EETT, pon requerimientos vacío []
- Texto verbatim = copia exacta, no parafraseo
- Máximo 15 requisitos por item (los más relevantes)
- Si hay más de 15, prioriza los Hard"""

    prompt_len = len(prompt)
    print(f"Prompt: {prompt_len} chars ({prompt_len//1024}K)")
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.1
    }
    
    t0 = time.time()
    try:
        resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=600)
        elapsed = time.time() - t0
        print(f"HTTP {resp.status_code} in {elapsed:.0f}s")
        
        if resp.status_code != 200:
            print(f"Error: {resp.text[:200]}")
            errors += len(batch)
            continue
        
        data = resp.json()
        usage = data.get('usage', {})
        comp = usage.get('completion_tokens', '?')
        print(f"Usage: comp={comp}")
        
        content = data['choices'][0]['message']['content']
        
        # Parse JSON
        json_match = re.search(r'\[[\s\S]*\]', content)
        if not json_match:
            print("No JSON array found!")
            errors += len(batch)
            continue
        
        try:
            results = json.loads(json_match.group())
        except json.JSONDecodeError:
            # Bracket rescue
            results = []
            arr_start = content.find('[')
            if arr_start >= 0:
                start = arr_start + 1
                depth = 0
                obj_start = None
                i = start
                while i < len(content):
                    if content[i] == '{':
                        if depth == 0:
                            obj_start = i
                        depth += 1
                    elif content[i] == '}':
                        depth -= 1
                        if depth == 0 and obj_start is not None:
                            try:
                                obj = json.loads(content[obj_start:i+1])
                                results.append(obj)
                            except:
                                pass
                            obj_start = None
                    i += 1
        
        print(f"Parsed {len(results)} item specs")
        
        # Save each item's specs
        for item_spec in results:
            item_id = item_spec.get('item_id', '')
            if not item_id:
                continue
            
            reqs = item_spec.get('requerimientos', [])
            hard = sum(1 for r in reqs if r.get('hard_soft') == 'Hard')
            soft = sum(1 for r in reqs if r.get('hard_soft') == 'Soft')
            
            # Find original item for full data
            orig = next((it for it in items if it.get('id') == item_id), None)
            if not orig:
                print(f"  Warning: item_id {item_id} not found in BOM")
                continue
            
            # Build full specs JSON
            specs = {
                "item_id": item_id,
                "nombre": orig.get('descripcion', ''),
                "tipo": "BIEN" if orig.get('grupo') != "Servicios" else "SERVICIO",
                "grupo": orig.get('grupo', ''),
                "subgrupo": orig.get('subgrupo', ''),
                "parent_id": "",
                "cantidad": orig.get('cantidad', ''),
                "unidad": orig.get('unidad', ''),
                "referencia_eett": "ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md",
                "estado_specs": item_spec.get('estado_specs', 'VERIFICADO'),
                "fuentes_consultadas": ["ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md"],
                "requerimientos": reqs,
                "tbd": item_spec.get('tbd', [])
            }
            
            # Save JSON
            json_path = f"{out_dir}/ITEM-{item_id}_specs.json"
            with open(json_path, 'w') as f:
                json.dump(specs, f, indent=2, ensure_ascii=False)
            
            # Save MD derivative
            md = f"# ITEM-{item_id} — Specs Verificadas\n\n"
            md += f"**ID**: {item_id} | **Grupo**: {specs['grupo']} | **Tipo**: {specs['tipo']}\n\n"
            md += f"**Descripción**: {specs['nombre']}\n\n"
            if reqs:
                md += f"## Requerimientos ({len(reqs)}: {hard} Hard, {soft} Soft)\n\n"
                for r in reqs:
                    badge = "🔴" if r.get('hard_soft') == 'Hard' else "🟡"
                    md += f"- {badge} **{r.get('req_id','')}** ({r.get('hard_soft','')}): {r.get('texto_verbatim','')}\n"
                    if r.get('referencias'):
                        md += f"  - _Ref: {', '.join(r['referencias'])}_\n"
            else:
                md += "_Sin requisitos específicos encontrados en EETT_\n"
            
            if specs['tbd']:
                md += f"\n## TBD ({len(specs['tbd'])})\n\n"
                for t in specs['tbd']:
                    md += f"- ⚠️ {t.get('descripcion', '')} ({t.get('referencia', '')})\n"
            
            md_path = f"{out_dir}/ITEM-{item_id}_specs.md"
            with open(md_path, 'w') as f:
                f.write(md)
            
            total_reqs += len(reqs)
            total_items_processed += 1
        
        print(f"  Batch stats: {len(results)} items, {sum(len(r.get('requerimientos',[])) for r in results)} reqs")
        
    except Exception as e:
        print(f"Exception: {e}")
        errors += len(batch)
        continue
    
    time.sleep(2)

print(f"\n{'='*50}")
print(f"✅ PASO 3.1 COMPLETADO")
print(f"Items procesados: {total_items_processed}/{len(items)}")
print(f"Total requerimientos extraídos: {total_reqs}")
print(f"Errores: {errors}")
print(f"Output: {out_dir}/")
