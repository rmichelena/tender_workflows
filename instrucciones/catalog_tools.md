# Catálogo de tools (search / fetch / parse)

> Actualizado en v0.2 tras el aprendizaje de ICAO-00068.
> El orquestador y los subagentes-item seleccionan tools desde este catálogo.
> Reglas: diversidad entre workers paralelos, fallback explícito, monitorear créditos antes de paso 6.

## Lección de ICAO-00068

**Lo que falló**: Firecrawl como única tool de fetch quedó sin créditos a mitad del Paso 6. No había fallback. El workflow degradó silenciosamente. Hit rate final: 10.7%.

**Regla nueva**: nunca depender de un solo vendor para una capa crítica. Cada capa tiene un primario y al menos un fallback explícito.

---

## 1. Estado del arte 2024-2025

El espacio se consolidó en cuatro categorías:

1. **Search APIs orientadas a agentes** — Brave, Tavily, Exa, Perplexity Sonar. Devuelven resultados ya filtrados para LLMs.
2. **Fetch + parse "URL→Markdown"** — Firecrawl, Jina Reader, Spider Cloud. Una URL entra, markdown sale.
3. **Browser-as-a-service** — Browserbase, Hyperbrowser. Sesiones de Chromium reales para JS-heavy, gates suaves, descargas.
4. **PDF parsers especializados** — LlamaParse, MinerU, Marker, Docling. Convierten PDF a markdown estructurado.

---

## 2. Cuadro comparativo

| Tool | Tipo | Descarga PDF | Parsea PDF→MD | Idiomas | Pricing | Pros para nuestro caso | Contras |
|---|---|---|---|---|---|---|---|
| **Brave Search API** | Search SERP | No | No | ES/EN | USD 3 por 1k consultas (free 2k/mes) | Índice independiente, no Google-dependiente. Recall amplio en B2B | Solo SERP; necesita pareja para fetch |
| **Exa** | Neural search + findSimilar | No | No | ES/EN | Free tier modesto; ~USD 5/1k búsquedas | `findSimilar(url)` es oro para expandir desde un fabricante a competidores. p95 ~1.5s | Cobertura B2B industrial irregular |
| **Tavily** | Search + extract | Solo HTML | No (extract devuelve texto) | ES/EN | 1k créditos free/mes; PAYG USD 0.008/cred | Mejor SERP "limpia" para RAG | No baja PDFs; 4-5× más caro que Firecrawl a 100k pages |
| **Perplexity Sonar** | Search + síntesis | No | No | ES/EN | Variable | Síntesis con citas — útil para "¿está discontinuado el modelo X?" | Riesgo de citar fuentes secundarias |
| **Jina Reader** (`r.jina.ai`, `s.jina.ai`) | Fetch + parse + search | PDFs nativos vía PDF.js | Calidad correcta | ES/EN | Free 20 RPM sin key, 200 RPM con key gratis; PAYG ~USD 0.045/1M tokens | **Casi gratis** a baja escala. Sin contrato. Excelente fallback. Acepta prompt inline | Token-based puede salir caro en datasheets largos. Sin browser real |
| **Firecrawl** | Fetch + parse + search + crawl + extract | Sí, directo y JS-rendered. `parsers:["pdf"]` con Fire-PDF (Rust + OCR opcional) | Alta calidad. Tablas con hasta 25s presupuesto, fórmulas LaTeX | ES/EN | 1k créditos free/mes; Standard 100k/mes USD 83-99; 1 crédito por página PDF | Una API cubre search + fetch HTML + PDF. SDKs estables | Créditos se consumen rápido. Sin sesión persistente para gates. Vendor lock-in |
| **Spider Cloud** | Crawler Rust + AI extract | Sí | Markdown directo | ES/EN | PAYG sin suscripción, sub-second | Muy rápido en crawling de catálogos completos | Producto joven, ecosistema menor |
| **Browserbase** | Browser-as-a-service (Chromium gestionado) | Sí, vía Playwright/Puppeteer | No (entrega HTML/screenshots) | ES/EN | Desde USD 50/mes; pago por hora de sesión | Único que resuelve gates JS reales, descargas con clic, formularios sin email | Costoso a escala. Hay que escribir scripts |
| **AgentQL** | Query DSL sobre páginas | Sí (JS render) | No | ES/EN | Free tier; pago por requests | Permite describir "encontrar el link 'Datasheet'" en lenguaje natural | Específico, no parser de PDF |
| **LlamaParse** | PDF parser cloud (closed) | No descarga | **Best-in-class para tablas técnicas** | ES/EN | 10k páginas free/mes; PAYG después | Mejor extracción de tablas multipágina y specs técnicas | Solo cloud |
| **Docling** (IBM) | PDF/Office parser open-source | No | DoclingDocument unificado; Granite-Docling-258M VLM | ES/EN | Apache 2.0, **ya en uso en el repo** | Excelente para nuestro pipeline — ya es la base de OCR del Paso 1. Self-hosted, sin créditos | Foco en docs estructurados, no en bypass de gates |
| **MinerU 2.5** | PDF parser open-source | No | Score 86.2 OmniDocBench; merge cross-page tables | ES/EN | Self-host gratis | Calidad SOTA open-source | Requiere GPU para velocidad |
| **LandingAI ADE** | PDF parser cloud | No | OCR + estructura + tablas | ES/EN | Créditos por página | **Ya en uso en el Paso 1** del workflow | — |

---

## 3. Stack recomendado por capa

### Capa A — Descubrimiento (search engine, Paso 6)

| Rol | Tool | Justificación |
|---|---|---|
| **Primario** | Brave Search API | Índice independiente, costo predecible (USD 3/1k), ya integrado |
| **Diversidad / expansión** | Exa con `findSimilar(url)` | Expande desde un fabricante conocido a competidores semánticamente similares |
| **RAG-style queries** | Tavily o Perplexity Sonar | Para preguntas tipo "¿modelo X está discontinuado?" |

**Regla**: cada search worker del Paso 6 debe usar un motor distinto (diversidad obligatoria). Worker A = Brave, Worker B = Exa o Tavily.

### Capa B — Fetch HTML del fabricante

| Rol | Tool | Justificación |
|---|---|---|
| **Primario** | Jina Reader (`r.jina.ai/<url>`) | Free hasta 200 RPM con API key gratis. Cubre 70-80% de páginas estáticas |
| **Secundario** (cuando Jina devuelve poco) | Firecrawl scrape | Umbral: `<800 tokens útiles` en respuesta de Jina → escalar |
| **Terciario** (JS-heavy) | Browserbase + Playwright | Solo para sites con `<div id="app">` vacío en static render |

### Capa C — PDF fetch + parse

| Rol | Tool | Justificación |
|---|---|---|
| **Descarga directa** | HTTP `curl` / `requests` | Cuando el href termina en `.pdf` y es público. **No consumir créditos de Firecrawl para esto** — fue el error caro de ICAO |
| **Parser por defecto** | **Docling** (ya self-hosted) | Calidad muy buena con TableFormer y DocLayNet. Sin créditos. Es la **opción default** |
| **Upgrade selectivo** | LlamaParse | Para datasheets con tablas complejas multipágina (switches industriales, radios con matriz de puertos). 10k páginas/mes free alcanzan para una licitación entera |
| **PDF detrás de redirects o JS** | Firecrawl Fire-PDF | Raro pero ocurre en algunos portales OEM |

### Capa D — Gates con formulario

| Rol | Tool | Justificación |
|---|---|---|
| **Form simple (sin email obligatorio)** | Browserbase + Playwright | Único que automatiza "completar nombre + país + descargar" |
| **Form con reCAPTCHA o email verificado** | **Fallar gracefully** | Marcar candidato como `evidence_quality: weak (no_public_datasheet)`. **No gastar más créditos** intentando bypass |

---

## 4. Patrones operativos para el subagente

### 4.1 Encontrar el link al PDF en la página del fabricante

Heurística que el subagente aplica en orden:

1. Pedir HTML con Jina Reader o Firecrawl (markdown ya simplificado).
2. Buscar links cuyo `href` termine en `.pdf`, `.PDF` o contenga `/datasheet`, `/spec`, `/brochure`, `/download`, `/dokumente`, `/documentos`, `/ficha`, `/folleto`.
3. Si el anchor text contiene `datasheet | spec sheet | technical specifications | brochure | ficha técnica | folleto técnico | hoja de datos | hoja técnica` → prioridad máxima.
4. Si hay múltiples PDFs, ranking por:
   - Match más cercano al model number
   - Tamaño plausible (200 KB – 5 MB)
   - Idioma del filename si distingue
5. Si nada aparece: usar AgentQL o un prompt al LLM para identificar el elemento desde screenshot.

### 4.2 Búsqueda bilingüe (ES + EN)

**Regla obligatoria**: cada query se ejecuta en español Y en inglés. Inglés primero (mayor recall en productos técnicos), español como complemento.

Ejemplos:
- "VSAT satellite modem datasheet" + "modem satelital VSAT ficha técnica"
- "industrial Ethernet switch IP67" + "switch ethernet industrial IP67"

### 4.3 PDFs detrás de form simple

- Detectar `<form>` con `<input>` y botón con texto "download" / "descargar".
- Si el form tiene solo nombre/empresa/país y campos opcionales: ejecutar con datos genéricos vía Browserbase + Playwright.
- Si tiene reCAPTCHA o requiere verificación de email: abortar y registrar evidencia "gated, requires registration".

### 4.4 Páginas JS-heavy

- Síntoma: Jina/Firecrawl devuelven `<html>` con `<div id="app">` vacío o markdown `<200 tokens`.
- Acción: escalar a Firecrawl con `actions:[{wait:3000}]` o directamente a Browserbase.

### 4.5 Detección de "solicite cotización" sin datasheet público

Red flags en la página del producto:
- "request a quote", "contact sales"
- "solicite cotización", "contáctenos para más información"
- Ausencia total de specs numéricas en el HTML
- Ausencia de PDFs en el dominio

En ese caso, el subagente marca `evidence_quality: weak (no_public_datasheet)` y **no gasta más créditos** intentando bypass.

---

## 5. Reglas operativas transversales

1. **Diversidad obligatoria** entre workers paralelos del mismo ítem: tools distintas.
2. **Monitorear créditos** de cada vendor antes de lanzar Paso 6. Si un vendor está cerca del límite, escalar a fallback.
3. **Cota dura por ítem**: máximo 8 fetches HTML + 3 PDFs por ítem antes de abortar y reportar.
4. **Validación de evidencia**: priorizar fuente fabricante (datasheet/manual). Distribuidores/marketplaces solo para descubrir URLs, no como evidencia de specs.
5. **Producto vigente**: verificar página del fabricante activa sin mención de EOL/discontinuado.

---

## 6. Costo estimado del stack

Volumen típico (≈1 licitación/semana, ~150 búsquedas + ~80 PDFs/licitación, ~600 búsquedas + ~320 PDFs/mes):

| Componente | Costo mensual |
|---|---|
| Brave (600 × USD 3/1k) | USD 1.80 |
| Exa (200 × USD 5/1k) | USD 1.00 |
| Jina Reader (free key cubre el volumen) | USD 0 |
| Firecrawl (free 1k créditos para fallbacks) | USD 0 |
| LlamaParse (free 10k páginas/mes) | USD 0 |
| Docling self-hosted | USD 0 |
| Browserbase (uso intermitente) | ~USD 50 |
| **Total** | **~USD 55/mes** |

Frente al riesgo de quedarse sin créditos de Firecrawl a mitad de proceso.

---

## Fuentes

- [Firecrawl vs Tavily](https://www.firecrawl.dev/alternatives/firecrawl-vs-tavily)
- [Firecrawl vs Jina AI](https://www.firecrawl.dev/alternatives/firecrawl-vs-jina-ai)
- [Firecrawl Fire-PDF launch](https://www.firecrawl.dev/blog/fire-pdf-launch)
- [Firecrawl — Best PDF parsers for AI and RAG (2026)](https://www.firecrawl.dev/blog/best-pdf-parsers)
- [Jina Reader API](https://jina.ai/reader/)
- [Exa vs Tavily](https://exa.ai/versus/tavily)
- [Browserbase — Top 10 web scraping tools 2025](https://www.browserbase.com/blog/best-web-scraping-tools)
- [MinerU GitHub](https://github.com/opendatalab/mineru)
- [LlamaParse how-to](https://www.llamaindex.ai/blog/pdf-parsing-llamaparse)
- [CodeCut — Docling vs Marker vs LlamaParse](https://codecut.ai/docling-vs-marker-vs-llamaparse/)
- [Procycons — PDF data extraction benchmark 2025](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/)
- [Docling GitHub](https://github.com/docling-project/docling)
