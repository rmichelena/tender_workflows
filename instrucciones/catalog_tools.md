# Catálogo de tools (search / fetch)

> El orquestador y los subagentes-item seleccionan tools desde este catálogo.
> Reglas: diversidad entre workers paralelos, rotación en reintentos.
> Ver `params.yaml → tool_pools` y `step6_combos`.

## Tools disponibles

| Tool | Tipo | Descripción | Fortalezas | Limitaciones |
|------|------|-------------|-----------|--------------|
| Brave | Search web | Motor de búsqueda general | Buen recall, rápido, cobertura amplia de fabricantes y distribuidores | Puede traer ruido; requiere buenos keywords |
| Perplexity | Search + síntesis | Búsqueda con respuesta sintetizada y citas | Acelera shortlist inicial, provee URLs de evidencia directamente | Puede citar fuentes secundarias; verificar siempre contra fabricante |
| Tinyfish | Search web | Motor alternativo con índice diferente a Brave | Diversidad de resultados, cobertura de nicho/B2B | Cobertura variable según dominio |
| Firecrawl | Fetch / parse | Extrae contenido limpio de URLs (HTML, PDF) | Parsing de datasheets, manuales, fichas técnicas | No es motor de búsqueda; necesita URL como input |

## Cuándo usar cada tool

| Situación | Tool recomendada |
|-----------|------------------|
| Búsqueda inicial de candidatos (worker A) | Brave o Perplexity |
| Búsqueda inicial de candidatos (worker B, diversidad) | Tinyfish o el que no se usó en A |
| Relanzamiento (rotación) | Tool no usada en intento anterior (ver `params.yaml → step6_combos.intento_2/3`) |
| Validación de evidencia (descargar datasheet/manual) | Firecrawl |
| Confirmar página de fabricante activa (no EOL) | Brave o Firecrawl |

## Reglas operativas

1. Workers paralelos del mismo ítem: tools distintas obligatoriamente.
2. Relanzamientos: rotar a tool no usada en intentos previos para ese mismo ítem.
3. Validación de specs hard: el subagente-item usa **Firecrawl** para parsear el datasheet del fabricante y confirmar valores.
4. Evidencia de producto vigente: verificar que la página del fabricante está activa y sin mención de EOL/discontinuado. Brave para encontrar la URL, Firecrawl para parsear el contenido si es necesario.
5. Si el ítem es de nicho B2B (telecom industrial, instrumentación, etc.): privilegiar **Tinyfish** y datasheets vía **Firecrawl**, ya que Brave/Perplexity tienden a ruido para ese dominio.
