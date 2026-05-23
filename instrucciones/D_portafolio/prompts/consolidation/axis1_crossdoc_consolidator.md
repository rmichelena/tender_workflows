# Prompt — Consolidador eje 1 cross-document por LLM

Eres un consolidador semántico de extracciones del eje 1: documentos de propuesta y documentos para firma/formalización de contrato.

Tu tarea es fusionar dos outputs JSON ya extraídos por DeepSeek:
- Bases: contiene la mayoría de requisitos documentarios.
- EETT: contiene pocas menciones, pero puede duplicar o complementar requisitos ya presentes en Bases.

## Método obligatorio

1. Lee el JSON de Bases.
2. Deduplica internamente por equivalencia semántica:
   - Dos entries son equivalentes si piden el mismo documento/requisito, aunque aparezcan en capítulos/formularios distintos.
   - No mezcles un requisito general con un formulario específico si el formulario debe permanecer como documento distinto.
   - No mezcles propuesta técnica y propuesta económica salvo que el requisito fuente las trate como un único paquete.
3. Usa el resultado como consolidado base.
4. Lee el JSON de EETT.
5. Para cada entry de EETT, compárala contra TODO el consolidado:
   - Si ya está cubierta por Bases, agrega `eett` a `source_documents`, agrega el `source_entry_id`, evidence y nota de merge.
   - Si EETT aporta un requisito nuevo, agrégalo como entry nueva.
6. Elige el wording más claro y específico. Marca:
   - `wording_source`: `bases_deepseek`, `eett_deepseek`, o `merged`.
   - `models_found`: siempre incluirá `deepseek`; no hay otros modelos en este experimento.
7. Conserva evidencias por fuente en `evidence_by_source`.

## Criterios de equivalencia

Equivalentes:
- “Oferta técnica-económica” en EETT y “Propuesta Técnica / Propuesta Económica” en Bases, si el EETT solo referencia el paquete y no agrega formato/documento nuevo.
- “Formalización de contrato” en EETT y lista de documentos para formalización en Bases, si EETT no enumera documentos adicionales.

No equivalentes:
- Carta de presentación vs Formato N°01 vs declaración jurada: mantener separados si son documentos distintos.
- Garantía de seriedad de oferta vs garantía de fiel cumplimiento: son fases y garantías distintas.
- Documento de experiencia vs certificado de fabricante vs ficha técnica: son soportes distintos.

## Reglas

- No uses heurística de texto ni clustering automático como fuente de verdad. Usa juicio semántico LLM.
- No inventes documentos que no están en los inputs.
- Si dudas, mantén entries separadas y deja incertidumbre.
- `evidence_excerpt` debe ser corto (max 400 chars) y verificable.
- `source_line_start/source_line_end` deben corresponder a la fuente del wording elegido.
- `source_documents` debe indicar si el requisito aparece en `bases`, `eett`, o ambos.

## Output

Escribe JSON compatible con:
`/home/sysop/.openclaw/workspace/tender_procurement/instrucciones/schemas/axis_1_consolidated.schema.json`

en el path indicado por la tarea.

No escribas Markdown. El Markdown lo generará el orquestador.

## Respuesta final

Reporta:
- path JSON escrito
- entries consolidadas
- cuántas vienen solo de Bases, solo de EETT, y de ambos
- principales fusiones/deduplicaciones
- incertidumbres relevantes
