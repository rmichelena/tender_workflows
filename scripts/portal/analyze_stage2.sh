#!/usr/bin/env bash
# Etapa 2: indexación, BOM, equipos, entregables (solo si se continúa)
set -euo pipefail

PROC_DIR="${1:?Ruta del proceso requerida}"
OUT="$PROC_DIR/analysis_stage2.json"

echo "Stage 2 — procesando $PROC_DIR"

cat > "$OUT" <<EOF
{
  "entregables": "Pendiente: integrar pipeline etapa 2",
  "equipos": "Pendiente: matching de equipos"
}
EOF

# El runner fusiona stage2 en analysis_output si existe el stage1
STAGE1="$PROC_DIR/analysis_output.json"
if [[ -f "$STAGE1" ]]; then
  python3 - "$STAGE1" "$OUT" <<'PY'
import json, sys
s1_path, s2_path = sys.argv[1], sys.argv[2]
with open(s1_path) as f: data = json.load(f)
with open(s2_path) as f: data.update(json.load(f))
with open(s1_path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)
PY
fi

echo "Stage 2 completado"
