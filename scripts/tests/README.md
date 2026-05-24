# Tests etapa C (`scripts/`)

```bash
# Desde la raíz del repo (usa el venv del portal + deps de scripts)
apps/portal/.venv/bin/pip install openpyxl
apps/portal/.venv/bin/pytest scripts/tests -v
```

- Docling: `apps/portal/.venv/bin/pytest scripts/tests -v`
- Portal análisis: `apps/portal/.venv/bin/pytest apps/portal/seace_monitor -q`
