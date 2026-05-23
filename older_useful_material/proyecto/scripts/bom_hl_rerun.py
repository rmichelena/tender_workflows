#!/usr/bin/env python3
"""Re-run kimi-k2p6 and deepseek-v4-pro with higher max_tokens."""
import json, time, requests, re

API_KEY = "fw_Bb55JPeskvxGDvZUyzXGbM"
BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
OUT_DIR = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_2_bom"

with open("/tmp/itb_bom.txt") as f:
    itb = f.read()
with open("/tmp/ts_bom.txt") as f:
    ts = f.read()

MODELS = [
    ("accounts/fireworks/models/kimi-k2p6", "kimi-k2p6", 1),
    ("accounts/fireworks/models/deepseek-v4-pro", "deepseek-v4-pro", 3),
]

SYSTEM = """Extract the High-Level BOM for ICAO-00068 (VSAT-Radar network for CORPAC Peru).
8 VSAT nodes + 7 terrestrial links.

RULES:
1. Goods AND services
2. Accessories inside major unit description (not separate items)
3. Requirements in context verbatim + reference
4. Groups: VSAT/HUB, Radio Comms, Energy, Infrastructure, Services
5. IDs: HL-001, HL-002...
6. No invention. Ambiguous = [TBD]
7. ONLY valid JSON, no code fences
8. For requisitos_en_contexto: include ONLY the top 3-5 most critical requirements per item (not every single one). Focus on key specs like frequency, power, standards, certifications.
"""

USER_TPL = """DOC 1 - ITB V2:
{itb}

DOC 2 - Tech Specs V12:
{ts}

BOM High-Level JSON:"""

for model_id, model_name, var in MODELS:
    print(f"\n{model_name} (var {var})...")
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
            print(f"OK {elapsed:.0f}s | prompt={usage.get('prompt_tokens','?')} comp={usage.get('completion_tokens','?')} resp={len(content)} chars")
            
            c = content.strip()
            if c.startswith("```"):
                c = c.split("\n", 1)[1] if "\n" in c else c[3:]
                c = c.rsplit("```", 1)[0]
            
            try:
                bom = json.loads(c.strip())
            except json.JSONDecodeError as e1:
                print(f"Direct parse failed: {e1}")
                m = re.search(r'\{[\s\S]*\}', c)
                if m:
                    try:
                        bom = json.loads(m.group())
                        print("Regex recovery OK")
                    except json.JSONDecodeError as e2:
                        print(f"Regex parse failed: {e2}")
                        raw_path = f"{OUT_DIR}/BOM_highlevel_var{var}_raw.txt"
                        with open(raw_path, 'w') as f:
                            f.write(content)
                        print(f"Saved raw to {raw_path}")
                        continue
                else:
                    print("No JSON found in response")
                    continue
            
            path = f"{OUT_DIR}/BOM_highlevel_var{var}.json"
            with open(path, 'w') as f:
                json.dump(bom, f, indent=2, ensure_ascii=False)
            n = len(bom.get('items', []))
            g = bom.get('grupos_identificados', [])
            print(f"Written {path} | {n} items | Groups: {g}")
        else:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
    except requests.exceptions.Timeout:
        print("TIMEOUT")
    except Exception as e:
        print(f"ERROR: {e}")

print("\nDONE")
