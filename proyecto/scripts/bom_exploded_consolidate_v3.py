#!/usr/bin/env python3
"""Consolidate remaining groups: VSAT/HUB (by subgrupo) and Energia."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
MODEL = "accounts/fireworks/models/glm-5p1"

base = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

# Load all 4 variants
all_items = []
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
        it['_variant'] = v
        all_items.append(it)

# Filter to VSAT/HUB and Energia only
target_items = [it for it in all_items if it.get('grupo') in ('VSAT/HUB', 'Energía')]
print(f"Target items: {len(target_items)}")

# Split VSAT/HUB by subgrupo for smaller batches
vsat_items = [it for it in target_items if it.get('grupo') == 'VSAT/HUB']
energia_items = [it for it in target_items if it.get('grupo') == 'Energía']

# VSAT subgrupos
vsat_subgroups = {}
for it in vsat_items:
    sg = it.get('subgrupo', 'sin_subgrupo')
    if sg not in vsat_subgroups:
        vsat_subgroups[sg] = []
    vsat_subgroups[sg].append(it)

print(f"VSAT/HUB subgroups ({len(vsat_items)} items):")
for sg, items in sorted(vsat_subgroups.items()):
    print(f"  {sg}: {len(items)} items")

print(f"Energía: {len(energia_items)} items")

# Process each batch
consolidated_items = []
exp_counter = 51  # Start after the 50 already consolidated

def consolidate_batch(grupo, subgrupo, items, start_id):
    """Consolidate a batch of items."""
    items_str = json.dumps([
        {
            "id": it.get("id",""),
            "subgrupo": it.get("subgrupo",""),
            "descripcion": it.get("descripcion",""),
            "cantidad": it.get("cantidad",""),
            "unidad": it.get("unidad",""),
            "notas": it.get("notas",""),
            "_var": it["_variant"]
        }
        for it in items
    ], indent=1, ensure_ascii=False)
    
    prompt = f"""Consolida estos {len(items)} items del grupo "{grupo}" subgrupo "{subgrupo}" del BOM Exploded para ICAO-00068 (Red VSAT-Radar CORPAC Perú).

REGLAS:
1. Deduplica items semánticamente iguales (pueden tener IDs distintos y venir de variantes distintas, indicadas por _var)
2. Conserva la descripción más completa
3. Prefiere cantidades numéricas sobre "según sitios"
4. Fusiona notas
5. Asigna IDs secuenciales empezando por EXP-{start_id:03d}

DEVUELVE SOLO un JSON array:
[
  {{"id": "EXP-XXX", "grupo": "{grupo}", "subgrupo": "{subgrupo}", "descripcion": "...", "cantidad": "...", "unidad": "...", "notas": "..."}}
]

ITEMS ({len(items)}):
{items_str}"""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32768,
        "temperature": 0.15
    }
    
    print(f"\n  Batch '{subgrupo}': {len(items)} items, {len(prompt)//1000}K chars prompt")
    
    t0 = time.time()
    resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=600)
    elapsed = time.time() - t0
    print(f"  HTTP {resp.status_code} in {elapsed:.0f}s")
    
    if resp.status_code != 200:
        print(f"  Error: {resp.text[:200]}")
        return []
    
    data = resp.json()
    usage = data.get('usage', {})
    print(f"  Usage: prompt={usage.get('prompt_tokens','?')} comp={usage.get('completion_tokens','?')}")
    
    content = data['choices'][0]['message']['content']
    
    # Try full JSON parse first
    json_match = re.search(r'\[[\s\S]*\]', content)
    if json_match:
        try:
            items_result = json.loads(json_match.group())
            print(f"  ✅ Consolidated: {len(items_result)} items")
            return items_result
        except:
            pass
    
    # If full parse fails, try bracket counting rescue
    print("  Full JSON parse failed, trying bracket rescue...")
    json_match = re.search(r'\[', content)
    if json_match:
        start = json_match.end()
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
            print(f"  ✅ Rescued: {len(rescued)} items")
            return rescued
    
    print("  ❌ Failed completely")
    return []

# Process VSAT/HUB by subgrupo
for sg in sorted(vsat_subgroups.keys()):
    items = vsat_subgroups[sg]
    result = consolidate_batch("VSAT/HUB", sg, items, exp_counter)
    for it in result:
        it['grupo'] = 'VSAT/HUB'
        consolidated_items.append(it)
    exp_counter += len(result)
    time.sleep(2)

# Process Energía
if energia_items:
    result = consolidate_batch("Energía", "all", energia_items, exp_counter)
    for it in result:
        it['grupo'] = 'Energía'
        consolidated_items.append(it)
    exp_counter += len(result)

# Now merge with the existing consolidated (50 items from Radio+Infra+Serv)
existing_path = f"{base}/BOM_exploded_consolidado.json"
with open(existing_path) as f:
    existing = json.load(f)

all_consolidated = existing['items'] + consolidated_items
all_consolidated.sort(key=lambda x: x.get('id', 'ZZZ'))

# Re-number IDs
for i, it in enumerate(all_consolidated, 1):
    it['id'] = f"EXP-{i:03d}"

# Save final
final = {
    "tipo": "BOM_EXPLODED_CONSOLIDADO",
    "proyecto": "ICAO-00068",
    "total_items": len(all_consolidated),
    "items": all_consolidated
}

with open(existing_path, 'w') as f:
    json.dump(final, f, indent=2, ensure_ascii=False)

# Stats
groups_final = {}
for it in all_consolidated:
    g = it.get('grupo', 'sin_grupo')
    groups_final[g] = groups_final.get(g, 0) + 1

print(f"\n{'='*50}")
print(f"✅ CONSOLIDADO FINAL: {len(all_consolidated)} items")
for g, c in sorted(groups_final.items()):
    print(f"  {g}: {c}")
