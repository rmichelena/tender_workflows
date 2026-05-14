#!/usr/bin/env python3
"""Normalize BOM high-level JSONs to standard structure."""
import json, os, sys

OUT_DIR = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

models = {
    1: "kimi-k2p6",
    2: "glm-5p1",
    3: "deepseek-v4-pro"
}

for var in [1, 2, 3]:
    path = f"{OUT_DIR}/BOM_highlevel_var{var}.json"
    if not os.path.exists(path):
        print(f"var{var}: MISSING")
        continue
    
    with open(path) as f:
        data = json.load(f)
    
    # Check structure
    if isinstance(data, list):
        # Wrap in standard structure
        items = data
        groups = list(set(it.get('grupo', 'Unknown') for it in items if isinstance(it, dict)))
        normalized = {
            "tipo": "BOM_HIGH_LEVEL",
            "variante": var,
            "modelo_usado": models[var],
            "fuentes_consultadas": ["ITB-ICAO-00068_V2_aclarada_v1.md", "ITB-ICAO-00068_Tech_Specs_V12_aclarada_v1.md"],
            "grupos_identificados": sorted(groups),
            "items": items,
            "tbd": [],
            "checklist_cobertura": {}
        }
        print(f"var{var} ({models[var]}): Wrapped list of {len(items)} items into standard structure")
        with open(path, 'w') as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
    elif isinstance(data, dict):
        items = data.get('items', [])
        print(f"var{var} ({models[var]}): Already dict structure, {len(items)} items")
        # Ensure standard fields
        if 'tipo' not in data:
            data['tipo'] = 'BOM_HIGH_LEVEL'
        if 'variante' not in data:
            data['variante'] = var
        if 'modelo_usado' not in data:
            data['modelo_usado'] = models[var]
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Print summary
    for it in items[:5]:
        desc = it.get('major_unit', it.get('descripcion', '?'))[:60]
        rqs = len(it.get('requisitos_en_contexto', []))
        print(f"  {it.get('id','?')}: {desc} | {rqs} reqs")
    if len(items) > 5:
        print(f"  ... and {len(items)-5} more items")
    print()
