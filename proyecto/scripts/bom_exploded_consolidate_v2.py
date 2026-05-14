#!/usr/bin/env python3
"""Consolidate BOM Exploded by group — smaller calls to avoid 504."""
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

print(f"Total items loaded: {len(all_items)}")

# Group by grupo
groups = {}
for it in all_items:
    g = it.get('grupo', 'sin_grupo')
    if g not in groups:
        groups[g] = []
    groups[g].append(it)

for g, items in groups.items():
    print(f"  {g}: {len(items)} items")

# Consolidate each group separately
consolidated_items = []
exp_id = 1

for grupo, items in groups.items():
    print(f"\n{'='*50}")
    print(f"Consolidating group: {grupo} ({len(items)} items)")
    
    # Build compact item list
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
    
    prompt = f"""Consolida estos {len(items)} items del grupo "{grupo}" del BOM Exploded para ICAO-00068 (Red VSAT-Radar CORPAC Perú).

REGLAS:
1. Deduplica items semánticamente iguales (pueden tener IDs distintos y venir de variantes distintas, indicadas por _var)
2. Conserva la descripción más completa
3. Prefiere cantidades numéricas sobre "según sitios"
4. Fusiona notas
5. Asigna IDs secuenciales empezando por EXP-{exp_id:03d}

DEVUELVE SOLO un JSON array de items:
[
  {{
    "id": "EXP-XXX",
    "grupo": "{grupo}",
    "subgrupo": "...",
    "descripcion": "...",
    "cantidad": "...",
    "unidad": "...",
    "notas": "..."
  }}
]

ITEMS A CONSOLIDAR ({len(items)} items de 4 variantes):
{items_str}"""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.15
    }
    
    prompt_len = len(prompt)
    print(f"Prompt: {prompt_len} chars ({prompt_len//1000}K)")
    
    t0 = time.time()
    try:
        resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=600)
        elapsed = time.time() - t0
        print(f"HTTP {resp.status_code} in {elapsed:.0f}s")
        
        if resp.status_code != 200:
            print(f"Error: {resp.text[:200]}")
            continue
        
        data = resp.json()
        usage = data.get('usage', {})
        print(f"Usage: prompt={usage.get('prompt_tokens','?')} comp={usage.get('completion_tokens','?')}")
        
        content = data['choices'][0]['message']['content']
        
        # Parse JSON array
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            items_result = json.loads(json_match.group())
            print(f"Consolidated: {len(items_result)} items (from {len(items)} original)")
            
            for it in items_result:
                it['grupo'] = grupo
                consolidated_items.append(it)
            
            exp_id += len(items_result)
        else:
            print("No JSON array found!")
    except Exception as e:
        print(f"Error: {e}")
        continue
    
    time.sleep(2)  # Rate limit

# Save final consolidated
result = {
    "tipo": "BOM_EXPLODED_CONSOLIDADO",
    "proyecto": "ICAO-00068",
    "total_items": len(consolidated_items),
    "items": consolidated_items
}

out_path = f"{base}/BOM_exploded_consolidado.json"
with open(out_path, 'w') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

# Stats
groups_final = {}
for it in consolidated_items:
    g = it.get('grupo', 'sin_grupo')
    groups_final[g] = groups_final.get(g, 0) + 1

print(f"\n{'='*50}")
print(f"✅ CONSOLIDADO FINAL: {len(consolidated_items)} items")
print(f"Saved to {out_path}")
for g, c in sorted(groups_final.items()):
    print(f"  {g}: {c}")
