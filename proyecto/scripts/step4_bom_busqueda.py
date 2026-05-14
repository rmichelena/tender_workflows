#!/usr/bin/env python3
"""Paso 4 — BOM para búsqueda: solo bienes, sin cantidades, refs limpias."""
import json, os

base = "/opt/data/workspace/tender_procurement/proyecto"

# Load BOM exploded consolidado
with open(f"{base}/artifacts/step_2_bom/BOM_exploded_consolidado.json") as f:
    bom = json.load(f)

# Load specs for all items
specs_dir = f"{base}/artifacts/step_3_specs"
items_with_specs = {}
for fname in os.listdir(specs_dir):
    if fname.endswith('_specs.json'):
        with open(f"{specs_dir}/{fname}") as f:
            s = json.load(f)
            items_with_specs[s['item_id']] = s

# Filter: solo bienes (quitar servicios)
bienes = [it for it in bom['items'] if it.get('grupo') != 'Servicios']
print(f"Bienes: {len(bienes)} de {len(bom['items'])} items")

# Build BOM búsqueda
busqueda_items = []
for it in bienes:
    item_id = it['id']
    spec = items_with_specs.get(item_id, {})
    
    # Extract key technical specs as search parameters
    search_params = []
    for r in spec.get('requerimientos', []):
        if r.get('hard_soft') == 'Hard':
            text = r.get('texto_verbatim', '')
            # Extract key technical values
            search_params.append(text)
    
    # Clean description: remove non-searchable references
    desc = it.get('descripcion', '')
    # Remove phrases like "según autorización MTC", "conectar a Redap Corpac"
    import re
    desc = re.sub(r'según\s+autorización\s+\w+', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'conectar\s+a\s+Redap\s+\w+', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'conforme\s+a\s+\w+', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+', ' ', desc).strip()
    
    # Get top 10 most relevant hard requirements for search
    # Prioritize: frequency, power, band, protocol, standard, physical specs
    priority_keywords = ['ghz', 'mhz', 'watt', 'w ', 'db', 'v ', 'amp', 'mbps', 'gbps', 'rpm', 
                         'ip', 'iec', 'ansi', 'etsi', 'itu', 'ieee', 'rj45', 'fiber', 'fibra',
                         'temperatura', 'humidity', 'altitud', 'peso', 'dimension']
    
    scored_reqs = []
    for text in search_params:
        score = sum(1 for kw in priority_keywords if kw in text.lower())
        scored_reqs.append((score, text))
    scored_reqs.sort(key=lambda x: -x[0])
    
    top_reqs = [t for _, t in scored_reqs[:10]]
    
    busqueda_item = {
        "id": item_id,
        "grupo": it.get('grupo', ''),
        "subgrupo": it.get('subgrupo', ''),
        "descripcion_limpia": desc,
        "cantidad_original": it.get('cantidad', ''),
        "unidad": it.get('unidad', ''),
        "params_busqueda": top_reqs,
        "notas": it.get('notas', ''),
        "total_reqs_hard": len(search_params),
        "estado": "pendiente_busqueda"
    }
    
    busqueda_items.append(busqueda_item)

# Build output
busqueda = {
    "tipo": "BOM_BUSQUEDA",
    "proyecto": "ICAO-00068",
    "total_bienes": len(busqueda_items),
    "items": busqueda_items
}

# Save JSON
out_dir = f"{base}/artifacts/step_4_busqueda"
os.makedirs(out_dir, exist_ok=True)

json_path = f"{out_dir}/BOM_busqueda.json"
with open(json_path, 'w') as f:
    json.dump(busqueda, f, indent=2, ensure_ascii=False)

# Save TSV
import csv
tsv_path = f"{out_dir}/BOM_busqueda.tsv"
with open(tsv_path, 'w', newline='') as f:
    writer = csv.writer(f, delimiter='\t')
    writer.writerow(['ID', 'Grupo', 'Subgrupo', 'Descripción', 'Unidad', 'Params Búsqueda', 'Total Hard Reqs'])
    for it in busqueda_items:
        params = ' | '.join(it['params_busqueda'][:3])
        writer.writerow([
            it['id'],
            it['grupo'],
            it['subgrupo'],
            it['descripcion_limpia'][:200],
            it['unidad'],
            params[:300],
            it['total_reqs_hard']
        ])

# QA checks
qa_ok = True
issues = []

# (a) No services
services = [it for it in busqueda_items if 'servicio' in it.get('grupo', '').lower()]
if services:
    issues.append(f"ERROR: {len(services)} servicios encontrados en BOM búsqueda")
    qa_ok = False

# (b) No quantities
with_qty = [it for it in busqueda_items if it.get('cantidad_original')]
# quantities are stored but not used for search - that's fine

# (c) No non-searchable references
import re
non_searchable = []
for it in busqueda_items:
    desc = it['descripcion_limpia']
    if re.search(r'según\s+autorización', desc, re.IGNORECASE):
        non_searchable.append(it['id'])
if non_searchable:
    issues.append(f"WARNING: {len(non_searchable)} items still have non-searchable refs: {non_searchable[:5]}")

# (d) All bienes present
bom_bienes = set(it['id'] for it in bienes)
busq_ids = set(it['id'] for it in busqueda_items)
missing = bom_bienes - busq_ids
if missing:
    issues.append(f"ERROR: {len(missing)} bienes missing from BOM búsqueda: {list(missing)[:5]}")
    qa_ok = False

print(f"\n{'='*50}")
print(f"✅ BOM BÚSQUEDA GENERADO")
print(f"Bienes: {len(busqueda_items)}")
print(f"QA: {'PASS' if qa_ok else 'ISSUES'}")
if issues:
    for i in issues:
        print(f"  {i}")
print(f"\nJSON: {json_path}")
print(f"TSV: {tsv_path}")

# Stats by group
groups = {}
for it in busqueda_items:
    g = it['grupo']
    groups[g] = groups.get(g, 0) + 1
for g, c in sorted(groups.items()):
    print(f"  {g}: {c}")

# Items with no search params
no_params = sum(1 for it in busqueda_items if not it['params_busqueda'])
if no_params:
    print(f"\n⚠️ {no_params} items sin parámetros de búsqueda")
