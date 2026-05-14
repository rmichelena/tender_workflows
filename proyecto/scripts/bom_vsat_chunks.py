#!/usr/bin/env python3
"""Consolidate VSAT/HUB group in chunks of ~50 items."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
MODEL = "accounts/fireworks/models/glm-5p1"

base = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

# Load all VSAT/HUB items
all_vsat = []
for v in [1, 2, 3, 4]:
    orig = f"{base}/BOM_exploded_var{v}.json"
    rescued = f"{base}/BOM_exploded_var{v}_rescued.json"
    try:
        with open(orig) as f:
            d = json.load(f)
    except:
        with open(rescued) as f:
            d = json.load(f)
    for it in d['items']:
        if it.get('grupo') == 'VSAT/HUB':
            it['_variant'] = v
            all_vsat.append(it)

print(f"VSAT/HUB items: {len(all_vsat)}")

# Sort by descripcion for better grouping in chunks
all_vsat.sort(key=lambda x: x.get('descripcion', ''))

# Split into chunks of 55 items
CHUNK_SIZE = 55
chunks = [all_vsat[i:i+CHUNK_SIZE] for i in range(0, len(all_vsat), CHUNK_SIZE)]
print(f"Split into {len(chunks)} chunks of ~{CHUNK_SIZE}")

vsat_consolidated = []

def consolidate_chunk(chunk, chunk_num, start_id):
    items_str = json.dumps([
        {
            "id": it.get("id",""),
            "descripcion": it.get("descripcion",""),
            "cantidad": it.get("cantidad",""),
            "unidad": it.get("unidad",""),
            "notas": it.get("notas",""),
            "_var": it["_variant"]
        }
        for it in chunk
    ], indent=1, ensure_ascii=False)
    
    prompt = f"""Consolida estos {len(chunk)} items VSAT/HUB del BOM Exploded para ICAO-00068 (Red VSAT-Radar CORPAC Perú). Este es el chunk {chunk_num} de {len(chunks)}.

REGLAS:
1. Deduplica items semánticamente iguales (pueden venir de variantes distintas _var 1-4)
2. Conserva la descripción más completa y técnica
3. Prefiere cantidades numéricas
4. Fusiona notas de variantes duplicadas
5. Asigna IDs: EXP-{start_id:03d} en adelante
6. Clasifica cada item en un subgrupo apropiado (ej: Hub/Master, VSAT Remote, Antenas, LNB/BUC, Modems, Routers, Switches, Cables/Conectores, redundancia, UPS VSAT, etc.)

DEVUELVE SOLO un JSON array:
[
  {{"id": "EXP-XXX", "grupo": "VSAT/HUB", "subgrupo": "...", "descripcion": "...", "cantidad": "...", "unidad": "...", "notas": "..."}}
]

ITEMS ({len(chunk)} del chunk {chunk_num}):
{items_str}"""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.15
    }
    
    prompt_len = len(prompt)
    print(f"\nChunk {chunk_num}: {len(chunk)} items, {prompt_len//1000}K chars")
    
    t0 = time.time()
    resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=600)
    elapsed = time.time() - t0
    print(f"HTTP {resp.status_code} in {elapsed:.0f}s")
    
    if resp.status_code != 200:
        print(f"Error: {resp.text[:200]}")
        return []
    
    data = resp.json()
    usage = data.get('usage', {})
    comp = usage.get('completion_tokens', '?')
    print(f"Usage: comp={comp}")
    
    content = data['choices'][0]['message']['content']
    
    # Try full parse
    json_match = re.search(r'\[[\s\S]*\]', content)
    if json_match:
        try:
            items_result = json.loads(json_match.group())
            print(f"✅ {len(items_result)} items")
            return items_result
        except:
            pass
    
    # Bracket rescue
    arr_start = content.find('[')
    if arr_start >= 0:
        start = arr_start + 1
        depth = 0
        rescued = []
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
                        rescued.append(obj)
                    except:
                        pass
                    obj_start = None
            i += 1
        if rescued:
            print(f"✅ Rescued: {len(rescued)} items")
            return rescued
    
    print("❌ Failed")
    return []

# Process each chunk
exp_id = 62  # After the 61 already consolidated
for i, chunk in enumerate(chunks, 1):
    result = consolidate_chunk(chunk, i, exp_id)
    for it in result:
        it['grupo'] = 'VSAT/HUB'
        vsat_consolidated.append(it)
    exp_id += len(result)
    time.sleep(2)

# Merge with existing
existing_path = f"{base}/BOM_exploded_consolidado.json"
with open(existing_path) as f:
    existing = json.load(f)

all_final = existing['items'] + vsat_consolidated
all_final.sort(key=lambda x: x.get('id', 'ZZZ'))

# Re-number
for i, it in enumerate(all_final, 1):
    it['id'] = f"EXP-{i:03d}"

final = {
    "tipo": "BOM_EXPLODED_CONSOLIDADO",
    "proyecto": "ICAO-00068",
    "total_items": len(all_final),
    "items": all_final
}

with open(existing_path, 'w') as f:
    json.dump(final, f, indent=2, ensure_ascii=False)

groups_final = {}
for it in all_final:
    g = it.get('grupo', 'sin_grupo')
    groups_final[g] = groups_final.get(g, 0) + 1

print(f"\n{'='*50}")
print(f"✅ CONSOLIDADO FINAL TOTAL: {len(all_final)} items")
for g, c in sorted(groups_final.items()):
    print(f"  {g}: {c}")
