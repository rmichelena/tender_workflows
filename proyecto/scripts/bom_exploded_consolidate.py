#!/usr/bin/env python3
"""Consolidate 4 BOM Exploded variants into one definitive BOM."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

base = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

# Load all 4 variants
variants = {}
for v in [1, 2, 3, 4]:
    orig = f"{base}/BOM_exploded_var{v}.json"
    rescued = f"{base}/BOM_exploded_var{v}_rescued.json"
    try:
        with open(orig) as f:
            d = json.load(f)
    except:
        with open(rescued) as f:
            d = json.load(f)
    # Keep only essential fields to reduce size
    items_compact = []
    for it in d['items']:
        items_compact.append({
            "id": it.get("id", ""),
            "grupo": it.get("grupo", ""),
            "subgrupo": it.get("subgrupo", ""),
            "descripcion": it.get("descripcion", ""),
            "cantidad": it.get("cantidad", ""),
            "unidad": it.get("unidad", ""),
            "notas": it.get("notas", "")
        })
    variants[v] = items_compact

# Build consolidation prompt
all_items_str = ""
for v, items in variants.items():
    all_items_str += f"\n### VARIANTE {v} ({len(items)} items)\n"
    all_items_str += json.dumps(items, indent=1, ensure_ascii=False)
    all_items_str += "\n"

prompt = f"""Eres un ingeniero de telecomunicaciones experto en licitaciones internacionales. Tu tarea es CONSOLIDAR 4 variantes de un BOM Exploded (Bill of Materials desagregado) para el proyecto ICAO-00068 "Nueva Red VSAT-Radar para CORPAC, Perú".

## REGLAS DE CONSOLIDACIÓN

1. **Deduplicar**: Múltiples variantes pueden describir el mismo ítem con IDs diferentes. Agrúpalos en un solo ítem.
2. **Elegir la mejor descripción**: De las descripciones duplicadas, conserva la más completa y precisa.
3. **Cantidad más específica**: Si las cantidades difieren, usa la que tenga mejor justificación. Si una variante dice "según sitios" y otra da un número, prefiere el número.
4. **IDs uniformes**: Asigna IDs secuenciales tipo EXP-001, EXP-002, etc.
5. **Conservar grupos**: Mantén los grupos (VSAT/HUB, Radio Comms, Infraestructura, Energía, Servicios).
6. **No perder items**: Si un item aparece en solo 1 variante pero es válido, inclúyelo.
7. **Combinar notas**: Fusiona las notas de las variantes duplicadas.

## FORMATO DE SALIDA

Devuelve SOLO un JSON con esta estructura exacta:
{{
  "tipo": "BOM_EXPLODED_CONSOLIDADO",
  "proyecto": "ICAO-00068",
  "total_items": <n>,
  "items": [
    {{
      "id": "EXP-001",
      "grupo": "...",
      "subgrupo": "...",
      "descripcion": "...",
      "cantidad": "...",
      "unidad": "...",
      "notas": "..."
    }}
  ]
}}

## VARIANTES A CONSOLIDAR

{all_items_str}

## INSTRUCCIONES FINALES
- Analiza cuidadosamente cada ítem de cada variante.
- Identifica duplicados buscando similitud semántica en descripciones, no solo coincidencia exacta.
- El resultado debe ser un BOM EXPLODED definitivo, sin duplicados, con las mejores descripciones.
- DEVUELVE SOLO EL JSON, sin texto adicional antes o después."""

prompt_len = len(prompt)
print(f"Prompt: {prompt_len} chars ({prompt_len//1000}K)")

payload = {
    "model": "accounts/fireworks/models/glm-5p1",
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 49152,
    "temperature": 0.15
}

print("Calling glm-5p1 for consolidation...")
t0 = time.time()
resp = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=900)
elapsed = time.time() - t0
print(f"HTTP {resp.status_code} in {elapsed:.0f}s")

data = resp.json()
usage = data.get('usage', {})
print(f"Usage: prompt={usage.get('prompt_tokens','?')} comp={usage.get('completion_tokens','?')}")

content = data['choices'][0]['message']['content']

# Save raw
raw_path = f"{base}/BOM_exploded_consolidado_raw.txt"
with open(raw_path, 'w') as f:
    f.write(content)
print(f"Raw saved ({len(content)} chars)")

# Parse JSON
json_match = re.search(r'\{[\s\S]*\}', content)
if json_match:
    try:
        result = json.loads(json_match.group())
        items = result.get('items', [])
        
        # Save consolidated
        out_path = f"{base}/BOM_exploded_consolidado.json"
        with open(out_path, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # Stats
        groups = {}
        for it in items:
            g = it.get('grupo', 'sin_grupo')
            groups[g] = groups.get(g, 0) + 1
        
        print(f"\n✅ CONSOLIDADO: {len(items)} items")
        print(f"Saved to {out_path}")
        for g, c in sorted(groups.items()):
            print(f"  {g}: {c}")
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print("First 500 chars of match:")
        print(json_match.group()[:500])
else:
    print("No JSON found in response")
