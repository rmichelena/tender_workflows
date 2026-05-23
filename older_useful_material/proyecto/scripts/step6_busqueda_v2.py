#!/usr/bin/env python3
"""Paso 6 v2 — Búsqueda con queries traducidas a inglés."""
import json, time, os, re, sys

sys.path.insert(0, "/opt/data/home/.local/lib/python3.13/site-packages")
import tinyfish
import requests as http_requests

API_KEY_FW = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL_FW = "https://api.fireworks.ai/inference/v1/chat/completions"
HEADERS_FW = {"Authorization": f"Bearer {API_KEY_FW}", "Content-Type": "application/json"}

base = "/opt/data/workspace/tender_procurement/proyecto"

with open("/opt/data/home/.hermes/.env") as f:
    for line in f:
        if line.startswith("TINYFISH_API_KEY="):
            tf_key = line.strip().split("=", 1)[1].strip('"')
            break

tf_client = tinyfish.TinyFish(api_key=tf_key)

with open(f"{base}/artifacts/step_4_busqueda/BOM_busqueda.json") as f:
    bom_busqueda = json.load(f)

# Translation map for common Spanish→English terms
TRANSLATIONS = {
    "radioenlace": "microwave radio link",
    "antena": "antenna",
    "parabólica": "parabolic",
    "radomo": "radome",
    "cable coaxial": "coaxial cable",
    "conector": "connector",
    "fibra óptica": "fiber optic",
    "monomodo": "single-mode",
    "conversor óptico": "fiber media converter",
    "teléfono analógico": "analog telephone",
    "protector descarga": "lightning protector",
    "grounding": "grounding kit",
    "fuente alimentación": "power supply",
    "transformador": "transformer",
    "rectificador": "rectifier",
    "batería": "battery bank",
    "ups": "UPS uninterruptible power supply",
    "generador": "generator",
    "panel solar": "solar panel",
    "shelter": "shelter cabinet outdoor enclosure",
    "rack": "rack cabinet 19 inch",
    "switch": "network switch managed",
    "router": "router",
    "modem satelital": "satellite modem",
    "vsat": "VSAT satellite terminal",
    "hub satelital": "satellite hub",
    "buc": "block upconverter BUC",
    "lnb": "low noise block downconverter LNB",
    "odu": "outdoor unit ODU",
    "idu": "indoor unit IDU",
    "nms": "network management system NMS",
    "multiplexor": "multiplexer",
    "repuesto": "spare part",
    "herramienta": "tool kit",
    "analizador espectro": "spectrum analyzer",
    "multímetro": "multimeter",
    "losa": "concrete foundation pad",
    "obra civil": "civil works",
    "torre": "tower mast",
    "soporte": "mount bracket",
    "cableado": "cabling wiring",
    "fibra": "fiber",
    "cobre": "copper",
    "energía": "power energy",
    "solar": "solar",
    "regulador": "voltage regulator",
    "inversor": "inverter",
    "transferencia": "transfer switch",
    "aire acondicionado": "air conditioner HVAC",
    "ventilación": "ventilation",
    "redunancia": "redundancy",
    "switcher": "redundancy switch",
    "filtro": "filter",
    "pasabanda": "bandpass",
    "divisor": "splitter divider",
    "combinador": "combiner",
    "acoplador": "coupler",
    "atenuador": "attenuator",
    "detector": "detector",
    "beacon": "beacon",
    "oscilador": "oscillator",
    "modulador": "modulator",
    "demodulador": "demodulator",
    "transceptor": "transceiver",
    "transmisor": "transmitter",
    "receptor": "receiver",
    "amplificador": "amplifier",
    "convertidor": "converter",
    "estabilizador": "stabilizer",
}

def translate_query(desc, grupo, params):
    """Build an English search query from Spanish description."""
    text = desc.lower()
    
    # Try direct keyword matching
    en_parts = []
    for es, en in sorted(TRANSLATIONS.items(), key=lambda x: -len(x[0])):
        if es in text:
            en_parts.append(en)
            text = text.replace(es, '')
    
    # Add group context
    grupo_en = {
        "VSAT/HUB": "VSAT satellite equipment",
        "Radio Comms": "microwave radio communication equipment",
        "Energía": "power supply energy equipment",
        "Infraestructura": "telecom infrastructure equipment"
    }
    if grupo in grupo_en:
        en_parts.insert(0, grupo_en[grupo])
    
    # Add key specs from params (these are often already technical)
    if params:
        for p in params[:2]:
            # Extract numeric specs (GHz, MHz, W, etc.)
            specs = re.findall(r'[\d.]+\s*(?:GHz|MHz|W|dB|V|A|Mbps|Gbps|rpm|mm|cm|m|kg)', p)
            en_parts.extend(specs)
    
    if not en_parts:
        # Fallback: use first 80 chars of description
        en_parts.append(desc[:80])
    
    query = " ".join(en_parts) + " datasheet specifications buy product"
    return query[:300]

items = bom_busqueda['items']
out_items_dir = f"{base}/artifacts/step_6_resultados/items"
out_matrices_dir = f"{base}/artifacts/step_6_resultados/matrices"
os.makedirs(out_items_dir, exist_ok=True)
os.makedirs(out_matrices_dir, exist_ok=True)

total_processed = 0
total_validos = 0
total_sin_candidato = 0

for i, item in enumerate(items):
    desc_short = item['descripcion_limpia'][:60]
    print(f"\n{i+1}/{len(items)} {item['id']}: {desc_short}...")
    
    # Translate and search
    query = translate_query(item['descripcion_limpia'], item['grupo'], item.get('params_busqueda', []))
    print(f"  Query: {query[:100]}...")
    
    search_results = []
    try:
        resp = tf_client.search.query(query, location="us", language="en")
        for r in resp.results[:5]:
            search_results.append({"title": r.title, "url": r.url, "snippet": r.snippet})
    except Exception as e:
        print(f"  Search error: {e}")
    
    print(f"  Search: {len(search_results)} results")
    
    if not search_results:
        result = {
            "item_id": item['id'],
            "estado": "SIN_CANDIDATO",
            "diagnostico": "Búsqueda sin resultados",
            "query_usado": query,
            "candidatos": []
        }
        with open(f"{out_items_dir}/ITEM-{item['id']}_resultado.json", 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        total_sin_candidato += 1
        total_processed += 1
        continue
    
    # Fetch top 3 pages
    detailed = []
    for sr in search_results[:3]:
        try:
            fr = tf_client.fetch.get_contents(urls=[sr['url']], format="markdown")
            content = fr.results[0].text[:3000] if fr.results else ""
        except:
            content = ""
        detailed.append({**sr, "page_content": content})
    
    # Evaluate with LLM
    params = item.get('params_busqueda', [])
    prompt = f"""Evaluate these product candidates against the required specs for a satellite/telecom equipment tender.

REQUIRED ITEM:
- Group: {item['grupo']}
- Description: {item['descripcion_limpia'][:200]}
- Key specs: {json.dumps(params[:6], ensure_ascii=False)}

CANDIDATES:
{json.dumps([{"title": c["title"], "url": c["url"], "snippet": c["snippet"], "content_preview": c.get("page_content","")[:1000]} for c in detailed[:3]], indent=1, ensure_ascii=False)}

For each candidate, classify: VALIDO (meets key specs), CONDICIONADO (partially meets), DESCARTADO (doesn't meet).

Return JSON array:
[{{"marca": "...", "modelo": "...", "clasificacion": "VALIDO|CONDICIONADO|DESCARTADO", "specs_relevantes": "...", "url_fuente": "...", "notas": "..."}}]

If no valid candidates, return []. ONLY JSON."""

    payload = {
        "model": "accounts/fireworks/models/glm-5p1",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }
    
    candidatos = []
    try:
        llm_resp = http_requests.post(BASE_URL_FW, headers=HEADERS_FW, json=payload, timeout=120)
        if llm_resp.status_code == 200:
            content = llm_resp.json()['choices'][0]['message']['content']
            jm = re.search(r'\[[\s\S]*\]', content)
            if jm:
                candidatos = json.loads(jm.group())
    except:
        pass
    
    validos = [c for c in candidatos if c.get('clasificacion') in ('VALIDO', 'CONDICIONADO')]
    estado = "CON_CANDIDATOS" if validos else "SIN_CANDIDATO"
    
    result = {
        "item_id": item['id'],
        "estado": estado,
        "query_usado": query,
        "candidatos": candidatos,
        "total_validos": len(validos)
    }
    
    with open(f"{out_items_dir}/ITEM-{item['id']}_resultado.json", 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    # MD
    md = f"# {item['id']} — {estado}\n\nQuery: `{query}`\n\n"
    if validos:
        for j, c in enumerate(validos, 1):
            md += f"## {j}. {c.get('marca','?')} {c.get('modelo','?')} [{c.get('clasificacion','')}]\n"
            md += f"- Specs: {c.get('specs_relevantes','')[:200]}\n"
            md += f"- URL: {c.get('url_fuente','')}\n\n"
    else:
        md += "_Sin candidatos válidos_\n"
    with open(f"{out_items_dir}/ITEM-{item['id']}_resultado.md", 'w') as f:
        f.write(md)
    
    # Matrices
    if validos:
        mat_dir = f"{out_matrices_dir}/ITEM-{item['id']}"
        os.makedirs(mat_dir, exist_ok=True)
        for j, c in enumerate(validos, 1):
            marca = (c.get('marca') or 'X').replace(' ', '_').replace('/','_')[:20]
            modelo = (c.get('modelo') or 'X').replace(' ', '_').replace('/','_')[:20]
            if not marca: marca = 'X'
            if not modelo: modelo = 'X'
            mat = {"item_id": item['id'], "candidato_num": j, **c}
            with open(f"{mat_dir}/ITEM-{item['id']}_candidato_{j}_{marca}_{modelo}.json", 'w') as f:
                json.dump(mat, f, indent=2, ensure_ascii=False)
    
    total_validos += len(validos)
    total_sin_candidato += (1 if not validos else 0)
    total_processed += 1
    
    print(f"  → {estado} ({len(validos)} válidos)")
    
    time.sleep(1)

print(f"\n{'='*50}")
print(f"✅ PASO 6 COMPLETADO")
print(f"Procesados: {total_processed}")
print(f"Con candidatos: {total_processed - total_sin_candidato}")
print(f"Sin candidato: {total_sin_candidato}")
print(f"Candidatos válidos totales: {total_validos}")
