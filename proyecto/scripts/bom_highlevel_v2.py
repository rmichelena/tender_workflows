#!/usr/bin/env python3
"""
BOM High-Level extraction v2 — smart chunking approach.
Extract key BOM-related sections, then send to Fireworks API.
"""
import json
import os
import time
import requests

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
BASE = "/opt/data/workspace/tender_procurement/proyecto"
OUT_DIR = f"{BASE}/artifacts/step_2_bom"

# Read documents
with open(f"{BASE}/artifacts/step_1_aclaradas/ITB-ICAO-00068_V2_aclarada_v1.md", 'r') as f:
    itb_content = f.read()
with open(f"{BASE}/artifacts/step_1_aclaradas/ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md", 'r') as f:
    ts_content = f.read()

print(f"ITB: {len(itb_content)} chars, TechSpecs: {len(ts_content)} chars")

# Extract BOM-relevant sections from Tech Specs
# Key sections: 3 (Scope), 4-6 (technical specs per subsystem), tables, BOQ
# Skip: Civil works (section 8+), Annexes with link characteristics

lines = ts_content.split('\n')
print(f"TechSpecs: {len(lines)} lines")

# Find section boundaries
sections = {}
current_section = "PREAMBLE"
sections[current_section] = []

for i, line in enumerate(lines):
    stripped = line.strip()
    # Detect main sections (single # or ## headers)
    if stripped.startswith('# ') and not stripped.startswith('## '):
        current_section = stripped[2:].strip()[:80]
        sections[current_section] = []
    elif stripped.startswith('## ') and i < len(lines) - 1:
        # Check if this is a major subsection
        pass
    sections.setdefault(current_section, []).append(line)

print(f"\nSections found:")
for name, content in sections.items():
    chars = sum(len(l) for l in content)
    print(f"  {name[:60]}: {len(content)} lines, {chars} chars")

# BOM-relevant sections keywords
bom_keywords = [
    'alcance', 'scope', 'suministro', 'equip', 'radio', 'vsat', 'modem', 'antena',
    'buc', 'lnb', 'l nb', ' filtro', 'switch', 'nms', 'management', 'torre', 'tower',
    'pararrayos', 'energ', 'solar', 'ups', 'fuente', 'power', 'bill of quantities',
    'boq', 'tabla 1', 'tabla 2', 'tabla 3', 'table', 'servicio', 'training',
    'garant', 'warranty', 'mantenim', 'sopport', 'voip', 'sip', 'codec',
    'multiplex', 'netperformer', 'comtech', 'terrasat', 'norsat',
    'hub', 'redun', 'accesorio', 'cable', 'coaxial', 'fibra',
    'rack', 'patch', 'telefono', 'telephone', 'weather',
    'introducci', 'generalidades', 'requerimiento', 'requisito',
    'capacitac', 'instalaci', 'configurac', 'puesta en marcha', 'comisionamiento'
]

# Build focused content: include sections that match BOM keywords
bom_relevant = []
preamble_lines = sections.get("PREAMBLE", [])
bom_relevant.extend(preamble_lines[:50])  # Include intro

for name, content in sections.items():
    name_lower = name.lower()
    content_text = '\n'.join(content).lower()
    is_relevant = any(kw in name_lower or kw in content_text[:2000] for kw in bom_keywords)
    # Skip civil works and annexes
    is_skip = any(s in name_lower for s in ['obra civil', 'civil work', 'concreto', 'movimiento de tierra', 
                                              'anexo iii', 'anexo iv', 'anexo v', 'anexo vi',
                                              'especificacione complementaria'])
    if is_relevant and not is_skip:
        bom_relevant.extend(content)

ts_focused = '\n'.join(bom_relevant)
print(f"\nTechSpecs BOM-relevant: {len(ts_focused)} chars ({len(bom_relevant)} lines)")

# For ITB: extract sections related to BOQ, scope, deliverables
itb_lines = itb_content.split('\n')
itb_relevant = []
capture = True
for line in itb_lines:
    # Skip long administrative sections but keep BOQ/deliverables
    if 'article 15' in line.lower() or 'article 16' in line.lower():
        capture = False
    if capture or any(kw in line.lower() for kw in ['boq', 'bill of quant', 'deliverab', 'scope', 
                                                       'form', 'tabla', 'table', 'annex', 'anexo',
                                                       'equip', 'service']):
        itb_relevant.append(line)

itb_focused = '\n'.join(itb_relevant)
print(f"ITB BOM-relevant: {len(itb_focused)} chars")

# Now we have focused content. Check total size
total = len(itb_focused) + len(ts_focused)
print(f"\nTotal focused content: {total} chars")
print(f"Total focused content: {total // 1000}K chars")

# If still too large, truncate Tech Specs focused to ~100K chars (keep the first 100K which has main specs)
if total > 180000:
    print(f"Truncating Tech Specs focused to fit context...")
    ts_focused = ts_focused[:120000]
    total = len(itb_focused) + len(ts_focused)
    print(f"After truncation: {total} chars ({total // 1000}K)")

SYSTEM_PROMPT = """Eres un experto en extracción estructurada de BOMs (Bill of Materials) para licitaciones de telecomunicaciones y redes VSAT.
Tu tarea es leer los documentos de la licitación ICAO-00068 y extraer un BOM High-Level.

REGLAS:
1. Incluí bienes Y servicios
2. Los accesorios NO van como ítems separados — van incluidos en la descripción del major unit
3. Extraé los requisitos en contexto (verbatim) de la sección donde se menciona cada unidad
4. Cada ítem debe referenciar documento + sección + página
5. Organizá en grupos lógicos (VSAT/HUB, Radio Comunicaciones, Energía, Infraestructura, Servicios, etc.)
6. IDs consecutivos: HL-001, HL-002, etc.
7. No inventar ítems. Si ambiguo, marcar [TBD]
8. Respondé SOLO con JSON válido, sin markdown code fences
9. Esta es una Red VSAT-Radar para CORPAC Perú con 8 nodos VSAT + 7 enlaces terrestres. Los equipos principales incluyen: modems VSAT, antenas VSAT, BUCs, LNBs, radios VHF/UHF, multiplexores, sistema NMS, torres, sistemas de energía solar/DC, UPS, etc.
"""

USER_PROMPT_TEMPLATE = """Extraé el BOM High-Level de los siguientes documentos de la licitación ICAO-00068 (Nueva Red VSAT-Radar para CORPAC, Perú).

DOCUMENTO 1 — ITB V2 (Instruction to Bidders):
{itb}

DOCUMENTO 2 — Tech Specs V12 (Especificaciones Técnicas):
{ts}

Producí un JSON con esta estructura exacta (sin code fences, solo JSON):
{{
  "tipo": "BOM_HIGH_LEVEL",
  "variante": {var_num},
  "modelo_usado": "{model_name}",
  "fuentes_consultadas": ["ITB-ICAO-00068_V2_aclarada_v1.md", "ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md"],
  "grupos_identificados": [],
  "items": [
    {{
      "id": "HL-001",
      "grupo": "...",
      "tipo": "BIEN o SERVICIO",
      "major_unit": "nombre corto",
      "descripcion_completa": "descripción incluyendo accesorios",
      "cantidad": "",
      "unidad": "",
      "referencia_eett": "Doc, Sección X, PAGE Y",
      "requisitos_en_contexto": [{{"texto_verbatim": "...", "referencia": "..."}}],
      "marcadores": []
    }}
  ],
  "tbd": [],
  "checklist_cobertura": {{}}
}}

JSON:"""

MODELS = [
    ("accounts/fireworks/models/kimi-k2p6", "kimi-k2p6", 1),
    ("accounts/fireworks/models/glm-5p1", "glm-5p1", 2),
    ("accounts/fireworks/models/deepseek-v4-pro", "deepseek-v4-pro", 3),
]

results = {}

for model_id, model_name, var_num in MODELS:
    print(f"\n{'='*60}")
    print(f"Calling {model_name} (variant {var_num})...")
    user_prompt = USER_PROMPT_TEMPLATE.format(
        itb=itb_focused,
        ts=ts_focused,
        var_num=var_num,
        model_name=model_name
    )
    total_chars = len(SYSTEM_PROMPT) + len(user_prompt)
    print(f"Total prompt chars: {total_chars} ({total_chars // 1000}K)")
    
    try:
        start_time = time.time()
        response = requests.post(
            BASE_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 16384,
            },
            timeout=600
        )
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            print(f"OK in {elapsed:.1f}s — tokens: prompt={usage.get('prompt_tokens','?')}, completion={usage.get('completion_tokens','?')}")
            
            # Clean JSON
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            
            try:
                bom = json.loads(cleaned.strip())
                out_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}.json"
                with open(out_path, 'w') as f:
                    json.dump(bom, f, indent=2, ensure_ascii=False)
                n_items = len(bom.get('items', []))
                groups = bom.get('grupos_identificados', [])
                print(f"Written to {out_path}")
                print(f"Items: {n_items}, Groups: {groups}")
                results[model_name] = {"status": "ok", "items": n_items, "groups": groups}
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                # Try to find JSON in the response
                import re
                json_match = re.search(r'\{[\s\S]*\}', cleaned)
                if json_match:
                    try:
                        bom = json.loads(json_match.group())
                        out_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}.json"
                        with open(out_path, 'w') as f:
                            json.dump(bom, f, indent=2, ensure_ascii=False)
                        n_items = len(bom.get('items', []))
                        print(f"Recovered JSON! Items: {n_items}")
                        results[model_name] = {"status": "ok_recovered", "items": n_items}
                    except:
                        raw_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}_raw.txt"
                        with open(raw_path, 'w') as f:
                            f.write(content)
                        results[model_name] = {"status": "json_error", "path": raw_path}
                else:
                    raw_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}_raw.txt"
                    with open(raw_path, 'w') as f:
                        f.write(content)
                    results[model_name] = {"status": "json_error", "path": raw_path}
        else:
            print(f"HTTP {response.status_code}: {response.text[:500]}")
            results[model_name] = {"status": "http_error", "code": response.status_code}
            
    except requests.exceptions.Timeout:
        print(f"TIMEOUT after 600s")
        results[model_name] = {"status": "timeout"}
    except Exception as e:
        print(f"ERROR: {e}")
        results[model_name] = {"status": "error", "error": str(e)}

print(f"\n{'='*60}")
print("RESULTS SUMMARY")
print(f"{'='*60}")
for model, res in results.items():
    print(f"  {model}: {res}")
