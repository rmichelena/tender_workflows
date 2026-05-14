#!/usr/bin/env python3
"""BOM High-Level extraction v3 — focused content, 3 Fireworks API calls."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
OUT_DIR = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

with open("/tmp/itb_bom.txt") as f:
    itb = f.read()
with open("/tmp/ts_bom.txt") as f:
    ts = f.read()

print(f"ITB: {len(itb)} chars, TS: {len(ts)} chars, Total: {len(itb)+len(ts)}")

SYSTEM = """Extraé el BOM High-Level de la licitación ICAO-00068 (Nueva Red VSAT-Radar para CORPAC, Perú).
Esta es una red de 8 nodos VSAT + 7 enlaces terrestres con equipos de comunicación satelital y radio.

REGLAS:
1. Bienes Y servicios
2. Accesorios dentro de la descripción del major unit (no separados)
3. Requisitos en contexto verbatim + referencia (doc, sección, página)
4. Grupos lógicos (VSAT/HUB, Radio Comms, Energía, Infraestructura, Servicios)
5. IDs: HL-001, HL-002, ...
6. No inventar. Ambiguo = [TBD]
7. SOLO JSON válido, sin code fences

JSON structure:
{"tipo":"BOM_HIGH_LEVEL","variante":VAR,"modelo_usado":"MODEL","fuentes_consultadas":["ITB V2","Tech Specs V12"],"grupos_identificados":[],"items":[{"id":"HL-001","grupo":"...","tipo":"BIEN|SERVICIO","major_unit":"...","descripcion_completa":"...","cantidad":"","unidad":"","referencia_eett":"...","requisitos_en_contexto":[{"texto_verbatim":"...","referencia":"..."}],"marcadores":[]}],"tbd":[],"checklist_cobertura":{}}
"""

USER_TPL = """DOCUMENTO 1 — ITB V2 (Instruction to Bidders):
{itb}

DOCUMENTO 2 — Tech Specs V12 (Especificaciones Técnicas):
{ts}

Extraé el BOM High-Level completo. JSON:"""

MODELS = [
    ("accounts/fireworks/models/kimi-k2p6", "kimi-k2p6", 1),
    ("accounts/fireworks/models/glm-5p1", "glm-5p1", 2),
    ("accounts/fireworks/models/deepseek-v4-pro", "deepseek-v4-pro", 3),
]

for model_id, model_name, var in MODELS:
    print(f"\n{'='*50}\n{model_name} (var {var})...")
    sys_msg = SYSTEM.replace("VAR", str(var)).replace("MODEL", model_name)
    user_msg = USER_TPL.format(itb=itb, ts=ts)
    
    try:
        t0 = time.time()
        r = requests.post(BASE_URL, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
                  "temperature": 0.2, "max_tokens": 32768}, timeout=600)
        elapsed = time.time() - t0
        
        if r.status_code == 200:
            d = r.json()
            content = d["choices"][0]["message"]["content"]
            usage = d.get("usage", {})
            print(f"OK {elapsed:.0f}s | prompt={usage.get('prompt_tokens','?')} completion={usage.get('completion_tokens','?')}")
            
            # Clean
            c = content.strip()
            if c.startswith("```"):
                c = c.split("\n", 1)[1] if "\n" in c else c[3:]
                c = c.rsplit("```", 1)[0]
            
            # Parse JSON
            try:
                bom = json.loads(c.strip())
            except json.JSONDecodeError:
                m = re.search(r'\{[\s\S]*\}', c)
                if m:
                    bom = json.loads(m.group())
                else:
                    raise
            
            path = f"{OUT_DIR}/BOM_highlevel_var{var}.json"
            with open(path, 'w') as f:
                json.dump(bom, f, indent=2, ensure_ascii=False)
            n = len(bom.get('items', []))
            g = bom.get('grupos_identificados', [])
            print(f"Written {path} | {n} items | Groups: {g}")
        else:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
    except requests.exceptions.Timeout:
        print("TIMEOUT 600s")
    except Exception as e:
        print(f"ERROR: {e}")

print("\nDONE")
