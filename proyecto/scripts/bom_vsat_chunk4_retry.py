#!/usr/bin/env python3
"""Retry VSAT/HUB chunk 4 with deepseek-v4-pro."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

base = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

# Load all VSAT/HUB items from var1-4
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

all_vsat.sort(key=lambda x: x.get('descripcion', ''))

# Chunk 4 = items 165-219 (0-indexed)
CHUNK_SIZE = 55
chunk4 = all_vsat[3*CHUNK_SIZE : 4*CHUNK_SIZE]
print(f"Chunk 4 retry: {len(chunk4)} items")

items_str = json.dumps([
    {
        "id": it.get("id",""),
        "descripcion": it.get("descripcion",""),
        "cantidad": it.get("cantidad",""),
        "unidad": it.get("unidad",""),
        "notas": it.get("notas",""),
        "_var": it["_variant"]
    }
    for it in chunk4
], indent=1, ensure_ascii=False)

prompt = f"""Consolida estos {len(chunk4)} items VSAT/HUB del BOM Exploded para ICAO-00068 (Red VSAT-Radar CORPAC Perú).

REGLAS:
1. Deduplica items semánticamente iguales (pueden venir de variantes distintas _var 1-4)
2. Conserva la descripción más completa y técnica
3. Prefiere cantidades numéricas
4. Fusiona notas
5. Asigna IDs: EXP-062 en adelante
6. Clasifica cada item en un subgrupo apropiado (Hub/Master, VSAT Remote, Antenas, LNB/BUC, Modems, Routers, Switches, Cables/Conectores, Redundancia, UPS VSAT, etc.)

DEVUELVE SOLO un JSON array:
[
  {{"id": "EXP-XXX", "grupo": "VSAT/HUB", "subgrupo": "...", "descripcion": "...", "cantidad": "...", "unidad": "...", "notas": "..."}}
]

ITEMS ({len(chunk4)}):
{items_str}"""

# Try with deepseek first, then kimi
for model in ["accounts/fireworks/models/deepseek-v4-pro", "accounts/fireworks/models/kimi-k2p6"]:
    model_name = model.split('/')[-1]
    print(f"\nTrying {model_name}...")
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16384,
        "temperature": 0.15
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
        usage = data.get('usage', {})
        print(f"Usage: comp={usage.get('completion_tokens','?')}")
        
        content = data['choices'][0]['message']['content']
        
        # Try full parse
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            try:
                items_result = json.loads(json_match.group())
                print(f"✅ {len(items_result)} items parsed directly")
                
                # Merge into consolidated
                consol_path = f"{base}/BOM_exploded_consolidado.json"
                with open(consol_path) as f:
                    consol = json.load(f)
                
                # Get max EXP id
                max_id = max(int(it['id'].split('-')[1]) for it in consol['items'])
                for i, it in enumerate(items_result, max_id + 1):
                    it['id'] = f"EXP-{i:03d}"
                    it['grupo'] = 'VSAT/HUB'
                    consol['items'].append(it)
                
                consol['total_items'] = len(consol['items'])
                with open(consol_path, 'w') as f:
                    json.dump(consol, f, indent=2, ensure_ascii=False)
                
                groups = {}
                for it in consol['items']:
                    g = it.get('grupo', '?')
                    groups[g] = groups.get(g, 0) + 1
                
                print(f"\n✅ UPDATED CONSOLIDADO: {consol['total_items']} items")
                for g, c in sorted(groups.items()):
                    print(f"  {g}: {c}")
                
                break  # Success!
            except json.JSONDecodeError:
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
                
                consol_path = f"{base}/BOM_exploded_consolidado.json"
                with open(consol_path) as f:
                    consol = json.load(f)
                
                max_id = max(int(it['id'].split('-')[1]) for it in consol['items'])
                for i, it in enumerate(rescued, max_id + 1):
                    it['id'] = f"EXP-{i:03d}"
                    it['grupo'] = 'VSAT/HUB'
                    consol['items'].append(it)
                
                consol['total_items'] = len(consol['items'])
                with open(consol_path, 'w') as f:
                    json.dump(consol, f, indent=2, ensure_ascii=False)
                
                groups = {}
                for it in consol['items']:
                    g = it.get('grupo', '?')
                    groups[g] = groups.get(g, 0) + 1
                
                print(f"\n✅ UPDATED CONSOLIDADO: {consol['total_items']} items")
                for g, c in sorted(groups.items()):
                    print(f"  {g}: {c}")
                
                break
        else:
            print("No JSON array found")
    except Exception as e:
        print(f"Exception: {e}")
        continue

print("\nDONE")
