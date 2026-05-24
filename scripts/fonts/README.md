# Fuentes para `pdf_plan_pages.py`

`NotoSans-Regular.ttf` se embebe vía PyMuPDF para renderizar texto Unicode en
imágenes/PDFs de reemplazo de planos (Paso 1.2b). Evita la limitación Latin-1 de
Helvetica Base-14 (`helv`).

Si falta el archivo bundled, el script intenta fuentes del sistema (DejaVu,
Liberation, Arial Unicode) y falla con mensaje claro.

Fuente: [Noto Sans](https://fonts.google.com/noto/specimen/Noto+Sans) (SIL Open Font License).
