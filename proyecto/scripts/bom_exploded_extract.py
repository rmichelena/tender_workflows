#!/usr/bin/env python3
"""BOM Exploded extraction — 4 variants via Fireworks API."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
BASE = "/opt/data/workspace/tender_procurement/proyecto"
OUT_DIR = f"{BASE}/artifacts/step_2_bom"

with open("/tmp/itb_bom.txt") as f:
    itb = f.read()
with open("/tmp/ts_bom.txt") as f:
    ts = f.read()
with open("/tmp/bom_hl_summary.txt") as f:
    bom_hl = f.read()

MODELS = [
    # Var 1: WITH BOM HL
    ("accounts/fireworks/models/kimi-k2p6", "kimi-k2p6", 1, True),
    # Var 2: WITH BOM HL
    ("accounts/fireworks/models/glm-5p1", "glm-5p1", 2, True),
    # Var 3: WITHOUT BOM HL
    ("accounts/fireworks/models/deepseek-v4-pro", "deepseek-v4-pro", 3, False),
    # Var 4: WITHOUT BOM HL
    ("accounts/fireworks/models/minimax-m2p7", "minimax-m2p7", 4, False),
]

SYSTEM = """Extraé el BOM Exploded (desagregado) de la licitación ICAO-00068 (Nueva Red VSAT-Radar para CORPAC, Perú).
8 nodos VSAT + 7 enlaces terrestres.

REGLAS:
1. CADA componente que pueda comprarse por separado debe ser ítem independiente (cables, conectores, protectores, soportes, fuentes, licencias, etc.)
2. Cada accesorio vincularse a equipo principal via parent_id
3. Bienes Y servicios por separado
4. Extraé requisitos en contexto verbatim + referencia (doc, sección, página) — solo top 3-5 más críticos
5. Organizá en grupos: VSAT/HUB, Radio Comms, Energía, Infraestructura, Servicios
6. IDs: IT-0001, IT-0002, ... (consecutivos)
7. tipo: BIEN o SERVICIO
8. No inventar. Ambiguo = [TBD]
9. SOLO JSON válido, sin code fences
10. parent_id vacío "" si es ítem independiente

JSON: {"tipo":"BOM_EXPLODED","variante":VAR,"modelo_usado":"MODEL","incluye_bom_hl":BOOL,"fuentes_consultadas":[...],"items":[{"id":"IT-0001","parent_id":"","grupo":"...","tipo":"BIEN|SERVICIO","descripcion":"...","cantidad":"","unidad":"","referencia_eett":"...","requisitos_en_contexto":[{"texto_verbatim":"...","referencia":"..."}],"marcadores":[]}],"tbd":[],"checklist_desagregacion":{}}
"""

USER_WITH_HL = """DOC 1 - ITB V2:
{itb}

DOC 2 - Tech Specs V12:
{ts}

BOM HIGH-LEVEL CONSOLIDADO (use como guía de estructura pero verificá/completá contra las EETT):
{bom_hl}

Extraé el BOM Exploded completo. JSON:"""

USER_WITHOUT_HL = """DOC 1 - ITB V2:
{itb}

DOC 2 - Tech Specs V12:
{ts}

Extraé el BOM Exploded completo. JSON:"""

for model_id, model_name, var, has_hl in MODELS:
    print(f"\n{'='*50}\n{model_name} var{var} (HL={'si' if has_hl else 'no'})...")
    sys_msg = SYSTEM.replace("VAR", str(var)).replace("MODEL", model_name).replace("BOOL", str(has_hl).lower())
    
    if has_hl:
        user_msg = USER_WITH_HL.format(itb=itb, ts=ts, bom_hl=bom_hl)
    else:
        user_msg = USER_WITHOUT_HL.format(itb=itb, ts=ts)
    
    total_chars = len(sys_msg) + len(user_msg)
    print(f"Prompt: {total_chars} chars ({total_chars//1000}K)")
    
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
            print(f"OK {elapsed:.0f}s | prompt={usage.get('prompt_tokens','?')} comp={usage.get('completion_tokens','?')}")
            
            c = content.strip()
            if c.startswith("```"):
                c = c.split("\n", 1)[1] if "\n" in c else c[3:]
                c = c.rsplit("```", 1)[0]
            
            # Try parse
            bom = None
            try:
                bom = json.loads(c.strip())
            except json.JSONDecodeError:
                m = re.search(r'\{[\s\S]*\}', c)
                if m:
                    try:
                        bom = json.loads(m.group())
                    except json.JSONDecodeError as e2:
                        print(f"Regex parse failed: {e2}")
            
            if bom is None:
                raw_path = f"{OUT_DIR}/BOM_exploded_var{var}_raw.txt"
                with open(raw_path, 'w') as f:
                    f.write(content)
                print(f"JSON parse failed, saved raw to {raw_path}")
                continue
            
            # Normalize if it's a plain list
            if isinstance(bom, list):
                items = bom
                groups = sorted(set(it.get('grupo', '?') for it in items if isinstance(it, dict)))
                bom = {"tipo": "BOM_EXPLODED", "variante": var, "modelo_usado": model_name, 
                       "incluye_bom_hl": has_hl, "items": items, "grupos_identificados": groups}
            
            path = f"{OUT_DIR}/BOM_exploded_var{var}.json"
            with open(path, 'w') as f:
                json.dump(bom, f, indent=2, ensure_ascii=False)
            n = len(bom.get('items', []))
            bienes = sum(1 for i in bom.get('items',[]) if isinstance(i, dict) and i.get('tipo') == 'BIEN')
            serv = sum(1 for i in bom.get('items',[]) if isinstance(i, dict) and i.get('tipo') == 'SERVICIO')
            print(f"Written {path} | {n} items (Bienes: {bienes}, Servicios: {serv})")
        else:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
    except requests.exceptions.Timeout:
        print("TIMEOUT 600s")
    except Exception as e:
        print(f"ERROR: {e}")

print("\nDONE")
