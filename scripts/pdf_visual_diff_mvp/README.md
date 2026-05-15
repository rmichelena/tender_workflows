# PDF Visual Diff MVP

Companion para QA visual de scripts que limpian/manipulan PDFs.

Genera:

- renders PNG por página del PDF original y procesado;
- imagen `diff` por página;
- imagen `overlay` por página;
- `summary.json` con métricas;
- `index.html` para ver original/procesado/diff lado a lado;
- servidor Flask en puerto configurable.

## Instalación

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Si `opencv-python-headless` no instala en tu VPS, puedes omitirlo. El script cae a un modo PIL-only menos preciso:

```bash
pip install pymupdf pillow flask
```

## Uso básico

```bash
python pdf_visual_diff.py compare original.pdf cleaned.pdf --out diff_run --dpi 160
python pdf_visual_diff.py serve diff_run --host 0.0.0.0 --port 8787
```

Luego abre:

```text
http://TU_VPS:8787/
```

## Sugerencia con SSH tunnel

Si no quieres exponer el puerto públicamente:

```bash
ssh -L 8787:127.0.0.1:8787 usuario@tu-vps
python pdf_visual_diff.py serve diff_run --host 127.0.0.1 --port 8787
```

Y en tu máquina local abre:

```text
http://127.0.0.1:8787/
```

## Parámetros útiles

```bash
python pdf_visual_diff.py compare original.pdf cleaned.pdf \
  --out diff_run \
  --dpi 180 \
  --threshold 35 \
  --min-region-area-px 100 \
  --workers 4
```

- `--dpi`: más alto = más detalle, más lento y más archivos.
- `--threshold`: tolerancia de diferencia por pixel. Subirlo reduce ruido por anti-aliasing.
- `--min-region-area-px`: ignora regiones muy pequeñas.

## Interpretación de riesgo

- `none`: sin cambios visibles relevantes.
- `low`: cambios pequeños.
- `medium`: cambios moderados o muchas regiones.
- `high`: cambios grandes o página agregada/removida.

## Estructura de salida

```text
diff_run/
  index.html
  summary.json
  images/
    original/
    processed/
    diff/
    overlay/
```

## Integración sugerida en tender_procurement

Ruta recomendada:

```text
scripts/pdf_visual_diff.py
```

y un output típico:

```text
artifacts/pdf_visual_diff/<stem>/
```
