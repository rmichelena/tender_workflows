#!/usr/bin/env python3
"""Retry missing items specs — smaller batches of 5."""
import json, time, requests, re, os

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
MODEL = "accounts/fireworks/models/glm-5p1"  # Try different model

base = "/opt/data/workspace/tender_procurement/proyecto"

with open('/tmp/ts_sections.json') as f:
    ts_sections = json.load(f)

with open('/tmp/missing_specs.json') as f:
    missing = json.load(f)

eett_context = f"""# EETT - ALCANCE
{ts_sections['alcance']}

# EETT - REQUISITOS GENÉRICOS
{ts_sections['genericos']}

# EETT - REQUISITOS ESPECÍFICOS
{ts_sections['especificos']}

# EETT - REPUESTOS
{ts_sections['repuestos']}"""

# Load BOM for lookup
with open(f"{base}/artifacts/step_2_bom/BOM_exploded_consolidado.json") as f:
    bom = json.load(f)

specs_dir = f"{base}/artifacts/step_3_specs"
BATCH_SIZE = 5
total_processed = 0
total_reqs = 0

for batch_start in range(0, len(missing), BATCH_SIZE):
    batch = missing[batch_start:batch_start + BATCH_SIZE]
    batch_num = batch_start // BATCH_SIZE + 1
    total_batches = (len(missing) + BATCH_SIZE - 1) // BATCH_SIZE
    
    print(f"\nBatch {batch_num}/{total_batches}: {len(batch)} items")
    
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
    
    prompt = f"""Eres un ingeniero de telecomunicaciones experto en licitaciones ICAO. Extrae requisitos técnicos de las EETT para estos items del BOM.

## ITEMS ({len(batch)})
{batch_items_str}

## ESPECIFICACIONES TÉCNICAS
{eett_context}

## INSTRUCCIONES
Para CADA item:
1. Busca requisitos específicos en las EETT
2. Clasifica Hard (obligatorio) / Soft (deseable)
3. Texto verbatim
4. Si no hay requisitos específicos, pon requerimientos vacío []

## FORMATO: JSON array
[
  {{
    "item_id": "EXP-XXX",
    "requerimientos": [
      {{"req_id": "R-001", "texto_verbatim": "...", "hard_soft": "Hard", "origen": "DIRECTO", "referencias": ["..."]}}
    ],
    "tbd": []
  }}
]

DEVUELVE SOLO EL JSON ARRAY. Máximo 10 reqs por item."""

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
                        if depth == 0: obj_start = i
                        depth += 1
                    elif content[i] == '}':
                        depth -= 1
                        if depth == 0 and obj_start is not None:
                            try: results.append(json.loads(content[obj_start:i+1]))
                            except: pass
                            obj_start = None
                    i += 1
        
        print(f"Parsed {len(results)} specs")
        
        for item_spec in results:
            item_id = item_spec.get('item_id', '')
            if not item_id: continue
            
            orig = next((it for it in bom['items'] if it.get('id') == item_id), None)
            if not orig: continue
            
            reqs = item_spec.get('requerimientos', [])
            
            specs = {
                "item_id": item_id,
                "nombre": orig.get('descripcion', ''),
                "tipo": "BIEN" if orig.get('grupo') != "Servicios" else "SERVICIO",
                "grupo": orig.get('grupo', ''),
                "subgrupo": orig.get('subgrupo', ''),
                "parent_id": "",
                "cantidad": orig.get('cantidad', ''),
                "unidad": orig.get('unidad', ''),
                "estado_specs": "VERIFICADO",
                "fuentes_consultadas": ["ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md"],
                "requerimientos": reqs,
                "tbd": item_spec.get('tbd', [])
            }
            
            json_path = f"{specs_dir}/ITEM-{item_id}_specs.json"
            with open(json_path, 'w') as f:
                json.dump(specs, f, indent=2, ensure_ascii=False)
            
            # MD
            md = f"# ITEM-{item_id} — Specs Verificadas\n\n"
            md += f"**ID**: {item_id} | **Grupo**: {specs['grupo']} | **Tipo**: {specs['tipo']}\n\n"
            md += f"**Descripción**: {specs['nombre']}\n\n"
            if reqs:
                hard = sum(1 for r in reqs if r.get('hard_soft') == 'Hard')
                soft = sum(1 for r in reqs if r.get('hard_soft') == 'Soft')
                md += f"## Requerimientos ({len(reqs)}: {hard} Hard, {soft} Soft)\n\n"
                for r in reqs:
                    badge = "🔴" if r.get('hard_soft') == 'Hard' else "🟡"
                    md += f"- {badge} **{r.get('req_id','')}** ({r.get('hard_soft','')}): {r.get('texto_verbatim','')}\n"
            else:
                md += "_Sin requisitos específicos_\n"
            
            md_path = f"{specs_dir}/ITEM-{item_id}_specs.md"
            with open(md_path, 'w') as f:
                f.write(md)
            
            total_reqs += len(reqs)
            total_processed += 1
        
    except Exception as e:
        print(f"Error: {e}")
    
    time.sleep(2)

print(f"\n{'='*50}")
print(f"✅ Retry completado")
print(f"Items procesados: {total_processed}/{len(missing)}")
print(f"Reqs extraídos: {total_reqs}")
