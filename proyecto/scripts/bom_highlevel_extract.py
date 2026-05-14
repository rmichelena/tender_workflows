#!/usr/bin/env python3
"""
BOM High-Level extraction — calls Fireworks API with 3 different models.
Produces 3 variants of BOM_HIGH_LEVEL JSON.
"""
import json
import os
import sys
import time
import requests

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

# Paths
BASE = "/opt/data/workspace/tender_procurement/proyecto"
ITB_PATH = f"{BASE}/artifacts/step_1_aclaradas/ITB-ICAO-00068_V2_aclarada_v1.md"
TS_PATH = f"{BASE}/artifacts/step_1_aclaradas/ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md"
OUT_DIR = f"{BASE}/artifacts/step_2_bom"

# Read documents
with open(ITB_PATH, 'r') as f:
    itb_content = f.read()
with open(TS_PATH, 'r') as f:
    ts_content = f.read()

print(f"ITB: {len(itb_content)} chars ({itb_content.count(chr(10))} lines)")
print(f"TechSpecs: {len(ts_content)} chars ({ts_content.count(chr(10))} lines)")

# Find key sections in Tech Specs for BOM extraction
# Section 3 = Scope of Supply, Section 4-6 = Technical specs, Section 5.5 = specific equipment
lines = ts_content.split('\n')
section_starts = {}
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith('#') and not stripped.startswith('####'):
        section_starts[i] = stripped

print(f"\nFound {len(section_starts)} section headers")
for idx, (lineno, title) in enumerate(sorted(section_starts.items())):
    print(f"  Line {lineno}: {title[:80]}")

# Build focused prompt — extract key sections that contain equipment/BOM info
# We need: Section 3 (Scope), Section 4-6 (Technical specs per subsystem)
# ITB has the BOQ structure

# For API calls, we need to be strategic about context size.
# Split into 2 calls per model: 
#   Call 1: ITB (77K chars) — extract BOQ structure + services  
#   Call 2: Tech Specs key sections — extract equipment specs

# Actually, let's try sending both docs but with a very focused prompt
# Fireworks models typically support 128K+ context

MODELS = [
    ("accounts/fireworks/models/kimi-k2p6", "kimi-k2p6", 1),
    ("accounts/fireworks/models/glm-5p1", "glm-5p1", 2),
    ("accounts/fireworks/models/deepseek-v4-pro", "deepseek-v4-pro", 3),
]

SYSTEM_PROMPT = """Eres un experto en extracción estructurada de BOMs (Bill of Materials) para licitaciones de telecomunicaciones.
Tu tarea es leer las Especificaciones Técnicas y el ITB de la licitación ICAO-00068 y extraer un BOM High-Level.

REGLAS:
1. Incluí bienes Y servicios
2. Los accesorios NO van como ítems separados — van incluidos en la descripción del major unit
3. Extraé los requisitos en contexto (verbatim) de la sección donde se menciona cada unidad
4. Cada ítem debe referenciar documento + sección + página
5. Organizá en grupos lógicos
6. IDs consecutivos: HL-001, HL-002, etc.
7. No inventar ítems. Si ambiguo, marcar [TBD]
8. Respondé SOLO con JSON válido, sin markdown code fences
"""

USER_PROMPT_TEMPLATE = """Extraé el BOM High-Level de los siguientes documentos de la licitación ICAO-00068 (Nueva Red VSAT-Radar para CORPAC, Perú).

DOCUMENTO 1 — ITB V2 (Instruction to Bidders):
{itb}

DOCUMENTO 2 — Tech Specs V12 (Especificaciones Técnicas):
{ts}

Producí un JSON con esta estructura exacta:
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

results = {}

for model_id, model_name, var_num in MODELS:
    print(f"\n{'='*60}")
    print(f"Calling {model_name} (variant {var_num})...")
    print(f"{'='*60}")
    
    user_prompt = USER_PROMPT_TEMPLATE.format(
        itb=itb_content,
        ts=ts_content,
        var_num=var_num,
        model_name=model_name
    )
    
    total_chars = len(SYSTEM_PROMPT) + len(user_prompt)
    print(f"Total prompt chars: {total_chars}")
    
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
            timeout=300
        )
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            print(f"OK in {elapsed:.1f}s — tokens: prompt={usage.get('prompt_tokens','?')}, completion={usage.get('completion_tokens','?')}")
            print(f"Response length: {len(content)} chars")
            
            # Clean JSON — remove code fences if present
            if content.strip().startswith("```"):
                content = content.strip()
                content = content.split("\n", 1)[1]  # Remove first line
                content = content.rsplit("```", 1)[0]  # Remove last fence
            
            # Try to parse JSON
            try:
                bom = json.loads(content.strip())
                out_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}.json"
                with open(out_path, 'w') as f:
                    json.dump(bom, f, indent=2, ensure_ascii=False)
                print(f"Written to {out_path}")
                print(f"Items: {len(bom.get('items', []))}")
                results[model_name] = {"status": "ok", "items": len(bom.get('items', [])), "path": out_path}
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                # Save raw response for inspection
                raw_path = f"{OUT_DIR}/BOM_highlevel_var{var_num}_raw.txt"
                with open(raw_path, 'w') as f:
                    f.write(content)
                print(f"Raw response saved to {raw_path}")
                results[model_name] = {"status": "json_error", "error": str(e)}
        else:
            print(f"HTTP {response.status_code}: {response.text[:500]}")
            results[model_name] = {"status": "http_error", "code": response.status_code}
            
    except requests.exceptions.Timeout:
        print(f"TIMEOUT after 300s")
        results[model_name] = {"status": "timeout"}
    except Exception as e:
        print(f"ERROR: {e}")
        results[model_name] = {"status": "error", "error": str(e)}

print(f"\n{'='*60}")
print("RESULTS SUMMARY")
print(f"{'='*60}")
for model, res in results.items():
    print(f"  {model}: {res}")
