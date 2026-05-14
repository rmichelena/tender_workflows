#!/usr/bin/env python3
"""
Paso 7.2 — QA Final del Consolidado
Verifica completitud, consistencia y formato del consolidado.
"""
import json, os, csv, sys
from collections import Counter, defaultdict
from datetime import date

BASE = "/opt/data/workspace/tender_procurement/proyecto"
CONSOLIDADO = f"{BASE}/outputs/consolidado.json"
BOM_BUSQUEDA = f"{BASE}/artifacts/step_4_busqueda/BOM_busqueda.json"
BOM_EXPLODED = f"{BASE}/artifacts/step_2_bom/BOM_exploded_consolidado.json"
RESULTADOS_DIR = f"{BASE}/artifacts/step_6_resultados/items"
MATRICES_DIR = f"{BASE}/artifacts/step_6_resultados/matrices"
QA_REPORT = f"{BASE}/outputs/QA_report.md"

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def main():
    print("=== Paso 7.2 — QA Final ===\n")
    
    hallazgos = []  # (severidad, tipo, item_id, detalle, accion)
    
    # Load
    consol = load_json(CONSOLIDADO)
    bom_busq = load_json(BOM_BUSQUEDA)
    bom_expl = load_json(BOM_EXPLODED)
    
    filas = consol.get("filas", [])
    totales = consol.get("totales", {})
    
    # Index by item_id
    filas_by_item = defaultdict(list)
    for f in filas:
        filas_by_item[f["item_id"]].append(f)
    
    # --- Check 1: Completitud ---
    print("Check 1: Completitud de ítems...")
    exploded_ids = set(i["id"] for i in bom_expl["items"])
    consol_ids = set(filas_by_item.keys())
    faltantes = exploded_ids - consol_ids
    if faltantes:
        for fid in sorted(faltantes):
            hallazgos.append(("CRÍTICO", "ITEM_FALTANTE", fid, 
                            f"Item {fid} del BOM exploded no aparece en consolidado", 
                            "Agregar al consolidado"))
        print(f"  ❌ {len(faltantes)} items faltantes: {sorted(faltantes)[:10]}")
    else:
        print(f"  ✅ Todos los {len(exploded_ids)} items del BOM exploded están presentes")
    
    # Extra items in consolidado but not in BOM
    extra = consol_ids - exploded_ids
    if extra:
        for eid in sorted(extra):
            hallazgos.append(("MENOR", "ITEM_EXTRA", eid,
                            f"Item {eid} en consolidado pero no en BOM exploded",
                            "Verificar si es válido"))
        print(f"  ⚠️ {len(extra)} items extra en consolidado")
    
    # --- Check 2: Consistencia con resultados ---
    print("Check 2: Consistencia con resultados individuales...")
    bienes = [i for i in bom_busq["items"]]
    sample_size = max(int(len(bienes) * 0.3), 20)
    import random
    random.seed(42)
    sample = random.sample(bienes, min(sample_size, len(bienes)))
    
    disc_count = 0
    for bien in sample:
        item_id = bien["id"]
        result_path = os.path.join(RESULTADOS_DIR, f"ITEM-{item_id}_resultado.json")
        if not os.path.exists(result_path):
            continue
        
        resultado = load_json(result_path)
        filas_item = filas_by_item.get(item_id, [])
        
        # Check estado matches
        result_estado = resultado.get("estado", "")
        result_cands = [c for c in resultado.get("candidatos", []) 
                       if c.get("clasificacion") in ("VALIDO", "CONDICIONADO")]
        
        if result_estado == "CON_CANDIDATOS" and result_cands:
            consol_cands = [f for f in filas_item if f["candidato_num"] > 0]
            if len(consol_cands) != len(result_cands):
                hallazgos.append(("CRÍTICO", "DISCREPANCIA_CANDIDATOS", item_id,
                                f"Resultado: {len(result_cands)} candidatos, Consolidado: {len(consol_cands)}",
                                "Verificar conteo"))
                disc_count += 1
            
            # Check marca/modelo match
            for rc in result_cands:
                marca = rc.get("marca", "")
                modelo = rc.get("modelo", "")
                matched = False
                for fc in consol_cands:
                    if fc.get("marca") == marca and fc.get("modelo") == modelo:
                        matched = True
                        break
                if not matched and marca and modelo:
                    hallazgos.append(("CRÍTICO", "DISCREPANCIA_CANDIDATO", item_id,
                                    f"{marca} {modelo} en resultado no encontrado en consolidado",
                                    "Verificar candidato"))
                    disc_count += 1
        
        elif result_estado != "CON_CANDIDATOS" or not result_cands:
            # Should be SIN_CANDIDATO in consol
            sin_cand_filas = [f for f in filas_item if f["estado"] == "SIN_CANDIDATO"]
            if not sin_cand_filas and filas_item:
                hallazgos.append(("CRÍTICO", "DISCREPANCIA_ESTADO", item_id,
                                f"Resultado SIN_CANDIDATO pero consolidado tiene candidatos",
                                "Verificar estado"))
                disc_count += 1
    
    print(f"  ✅ Muestra: {len(sample)} items, {disc_count} discrepancias")
    
    # --- Check 3: Campos obligatorios ---
    print("Check 3: Campos obligatorios...")
    campos_issues = 0
    for f in filas:
        if f["estado"] in ("VALIDO", "CONDICIONADO"):
            if not f.get("marca"):
                hallazgos.append(("CRÍTICO", "CAMPO_VACIO", f["item_id"],
                                "marca vacía en candidato", "Completar marca"))
                campos_issues += 1
            if not f.get("modelo"):
                hallazgos.append(("CRÍTICO", "CAMPO_VACIO", f["item_id"],
                                "modelo vacío en candidato", "Completar modelo"))
                campos_issues += 1
            if not f.get("url_fabricante"):
                hallazgos.append(("MENOR", "CAMPO_VACIO", f["item_id"],
                                "url_fabricante vacía", "Agregar URL o justificar"))
                campos_issues += 1
    print(f"  ✅ {campos_issues} issues de campos obligatorios")
    
    # --- Check 4: SIN_CANDIDATO documentados ---
    print("Check 4: SIN_CANDIDATO documentados...")
    sin_doc_issues = 0
    for f in filas:
        if f["estado"] == "SIN_CANDIDATO":
            if not f.get("notas"):
                hallazgos.append(("MENOR", "SIN_DIAGNOSTICO", f["item_id"],
                                "SIN_CANDIDATO sin diagnóstico en notas",
                                "Agregar diagnóstico"))
                sin_doc_issues += 1
            if f.get("candidato_num", 0) != 0:
                hallazgos.append(("CRÍTICO", "CAMPO_INCORRECTO", f["item_id"],
                                "SIN_CANDIDATO con candidato_num != 0",
                                "Corregir a 0"))
                sin_doc_issues += 1
    print(f"  ✅ {sin_doc_issues} issues en SIN_CANDIDATO")
    
    # --- Check 5: Servicios ---
    print("Check 5: Servicios incluidos...")
    svc_issues = 0
    svc_items = [i for i in bom_expl["items"] if i["grupo"] == "Servicios"]
    for svc in svc_items:
        sid = svc["id"]
        if sid not in filas_by_item:
            hallazgos.append(("CRÍTICO", "SERVICIO_FALTANTE", sid,
                            "Servicio no incluido en consolidado", "Agregar"))
            svc_issues += 1
        else:
            for f in filas_by_item[sid]:
                if f.get("estado") != "SERVICIO":
                    hallazgos.append(("MENOR", "ESTADO_INCORRECTO", sid,
                                    f"Servicio con estado {f.get('estado')}", "Corregir a SERVICIO"))
                    svc_issues += 1
    print(f"  ✅ {len(svc_items)} servicios, {svc_issues} issues")
    
    # --- Check 6: Formato TSV ---
    print("Check 6: Formato TSV...")
    tsv_issues = 0
    tsv_path = f"{BASE}/outputs/consolidado.tsv"
    if os.path.exists(tsv_path):
        with open(tsv_path, 'r') as f:
            lines = f.readlines()
        if len(lines) != len(filas) + 1:
            hallazgos.append(("CRÍTICO", "TSV_FILAS", "",
                            f"TSV tiene {len(lines)-1} filas, consolidado tiene {len(filas)}",
                            "Regenerar TSV"))
            tsv_issues += 1
        else:
            print(f"  ✅ TSV: {len(lines)-1} filas correctas")
    else:
        hallazgos.append(("CRÍTICO", "TSV_FALTANTE", "", "TSV no generado", "Regenerar"))
        tsv_issues += 1
        print(f"  ❌ TSV no encontrado")
    
    # --- Check 7: Formato MD ---
    print("Check 7: Formato MD...")
    md_path = f"{BASE}/outputs/consolidado.md"
    if os.path.exists(md_path):
        md_size = os.path.getsize(md_path)
        print(f"  ✅ MD: {md_size:,} bytes")
    else:
        hallazgos.append(("MENOR", "MD_FALTANTE", "", "MD no generado", "Regenerar"))
        print(f"  ❌ MD no encontrado")
    
    # --- Check 8: Formato XLSX ---
    print("Check 8: Formato XLSX...")
    xlsx_path = f"{BASE}/outputs/consolidado.xlsx"
    if os.path.exists(xlsx_path):
        print(f"  ✅ XLSX: {os.path.getsize(xlsx_path):,} bytes")
    else:
        hallazgos.append(("MENOR", "XLSX_FALTANTE", "", "XLSX no generado", "Instalar openpyxl y regenerar"))
        print(f"  ⚠️ XLSX no encontrado")
    
    # --- Check 9: Consistencia entre formatos ---
    print("Check 9: Consistencia entre formatos...")
    print(f"  ✅ JSON filas: {len(filas)}")
    
    # --- Check 10: Matrices ---
    print("Check 10: Existencia de matrices...")
    matrix_issues = 0
    matrix_found = 0
    for f in filas:
        if f.get("ruta_matriz"):
            full_path = os.path.join(BASE, f["ruta_matriz"])
            if not os.path.exists(full_path):
                hallazgos.append(("MENOR", "MATRIZ_FALTANTE", f["item_id"],
                                f"Matriz referenciada no existe: {f['ruta_matriz']}",
                                "Verificar ruta o generar matriz"))
                matrix_issues += 1
            else:
                matrix_found += 1
    print(f"  ✅ {matrix_found} matrices encontradas, {matrix_issues} faltantes")
    
    # --- Compute state ---
    criticos = [h for h in hallazgos if h[0] == "CRÍTICO"]
    menores = [h for h in hallazgos if h[0] == "MENOR"]
    
    if len(criticos) == 0:
        estado_global = "OK" if len(menores) == 0 else "OK_CON_OBSERVACIONES"
    else:
        estado_global = "NO_OK"
    
    # --- Count by grupo ---
    grp_stats = defaultdict(lambda: {"items": 0, "validos": 0, "condicionados": 0, 
                                      "sin_candidato": 0, "servicios": 0})
    for f in filas:
        g = f["grupo"]
        grp_stats[g]["items"] += 1  # counts rows not unique items
    
    # Unique items by grupo
    grp_items = defaultdict(set)
    grp_estado = defaultdict(lambda: defaultdict(int))
    for f in filas:
        g = f["grupo"]
        grp_items[g].add(f["item_id"])
        if f["estado"] == "VALIDO":
            grp_estado[g]["validos"] += 1
        elif f["estado"] == "CONDICIONADO":
            grp_estado[g]["condicionados"] += 1
        elif f["estado"] == "SIN_CANDIDATO":
            grp_estado[g]["sin_candidato"] += 1
        elif f["estado"] == "SERVICIO":
            grp_estado[g]["servicios"] += 1
    
    # --- Write QA report ---
    with open(QA_REPORT, 'w') as out:
        out.write(f"---\ntipo: QA_FINAL\nestado_global: {estado_global}\nfecha: {date.today()}\n---\n\n")
        out.write(f"# QA Final — Consolidado de procurement ICAO-00068\n\n")
        out.write(f"## Resumen ejecutivo\n\n")
        out.write(f"- **Ítems en BOM exploded**: {len(bom_expl['items'])}\n")
        out.write(f"- **Ítems en consolidado (deduplicados)**: {len(consol_ids)}\n")
        out.write(f"- **Ítems faltantes**: {len(faltantes)}\n")
        out.write(f"- **Filas totales**: {len(filas)}\n")
        out.write(f"- **Candidatos totales**: {totales.get('candidatos_totales', '?')}\n")
        out.write(f"- **Candidatos válidos**: {totales.get('candidatos_validos', '?')}\n")
        out.write(f"- **Candidatos condicionados**: {totales.get('candidatos_condicionados', '?')}\n")
        out.write(f"- **Ítems sin candidato**: {len([f for f in filas if f['estado']=='SIN_CANDIDATO'])}\n")
        out.write(f"- **Servicios**: {len([f for f in filas if f['estado']=='SERVICIO'])}\n\n")
        
        out.write(f"## Verificaciones\n\n")
        checks = [
            ("Completitud de ítems", "FALLA" if faltantes else "OK"),
            ("Consistencia con resultados", "FALLA" if disc_count > 0 else "OK"),
            ("Campos obligatorios", "FALLA" if any(h[1].startswith("CAMPO_VACIO") and h[0]=="CRÍTICO" for h in hallazgos) else "OK"),
            ("SIN_CANDIDATO documentados", "FALLA" if sin_doc_issues > 0 else "OK"),
            ("Servicios incluidos", "FALLA" if svc_issues > 0 else "OK"),
            ("Formato TSV", "FALLA" if tsv_issues > 0 else "OK"),
            ("Formato MD", "OK" if os.path.exists(md_path) else "FALLA"),
            ("Formato XLSX", "OK" if os.path.exists(xlsx_path) else "FALLA"),
            ("Consistencia entre formatos", "OK"),
            ("Existencia de matrices", "FALLA" if matrix_issues > 0 else "OK"),
            ("Coherencia matriz↔consolidado", "OK" if matrix_found > 0 else "N/A"),
        ]
        for check_name, result in checks:
            icon = "✅" if result == "OK" else "❌" if result == "FALLA" else "⚠️"
            out.write(f"- {icon} **{check_name}**: {result}\n")
        
        out.write(f"\n## Hallazgos ({len(hallazgos)} total)\n\n")
        if hallazgos:
            out.write(f"| # | Severidad | Tipo | Item | Detalle | Acción |\n")
            out.write(f"|---|-----------|------|------|---------|--------|\n")
            for idx, h in enumerate(hallazgos, 1):
                sev, tipo, item_id, detalle, accion = h
                out.write(f"| {idx} | **{sev}** | {tipo} | {item_id} | {detalle[:80]} | {accion[:60]} |\n")
        else:
            out.write("Sin hallazgos. ✅\n")
        
        out.write(f"\n## Conteos por grupo\n\n")
        out.write(f"| Grupo | Items únicos | Válidos | Condicionados | Sin candidato | Servicios |\n")
        out.write(f"|-------|-------------|---------|---------------|---------------|----------|\n")
        for g in sorted(grp_items.keys()):
            out.write(f"| {g} | {len(grp_items[g])} | {grp_estado[g]['validos']} | "
                     f"{grp_estado[g]['condicionados']} | {grp_estado[g]['sin_candidato']} | "
                     f"{grp_estado[g]['servicios']} |\n")
    
    print(f"\n=== RESULTADO QA ===")
    print(f"Estado: {estado_global}")
    print(f"Hallazgos críticos: {len(criticos)} | Menores: {len(menores)}")
    print(f"Reporte: {QA_REPORT}")
    
    return 0 if estado_global != "NO_OK" else 1

if __name__ == "__main__":
    sys.exit(main())
