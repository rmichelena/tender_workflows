# Etapa B — Staging → portafolio

Sistema **no agéntico**: formularios en el portal para armar el expediente que alimentará etapas C y D.

**Estado:** no implementado — especificación de diseño.

---

## Objetivo

Cuando un proceso está en **`portafolio`**, el usuario prepara la transición de **pre-portafolio** a **portafolio de trabajo**:

1. Elegir qué documentos de `pre_portafolio/` se copian a `portafolio/inputs/`.
2. Clasificar la función de cada documento incluido: `bases_iniciales`, `aclaraciones`, `bases_aclaradas`, `especificaciones_tecnicas`, `otros`.
3. Subir documentos adicionales (EETT no descargados, aclaraciones en papel, etc.).
4. Declarar aclaraciones/addenda/enmiendas (**UI**, no chat con agente — sustituye Gate 0.a legacy).
5. Generar **`staging_manifest.json`** — fuente de verdad para C.2 y auditoría.
6. Deducir el escenario de arranque Hermes:
   - `initial_bases` — bases iniciales sin aclaraciones; si consultas sigue abierto, preparar preguntas/observaciones.
   - `integrate_clarifications` — bases iniciales + aclaraciones/respuestas; integrar aclaraciones.
   - `verify_integrated_bases` — bases iniciales/aclaraciones + bases aclaradas/integradas; verificar integración y completar brechas.

Estado objetivo opcional: **`portafolio_preparado`** cuando el manifest está completo y la copia terminó.

---

## UI propuesta

### Pantalla «Preparar portafolio»

| Control | Descripción |
|---------|-------------|
| Tabla documentos | Lista de archivos en `pre_portafolio/documentos/_extracted/` con checkbox «incluir» |
| Función documental | Menú por archivo incluido: bases iniciales, aclaraciones, bases aclaradas, EETT, otros |
| Upload | Drag-drop → `portafolio/inputs/_uploads/` o directo a `inputs/` |
| Aclaraciones | Por archivo upload o existente: tipo `aclaracion` / `addenda` / `enmienda` / `ninguno` |
| Metadatos | Notas libres, referencia interna |
| Acción | «Confirmar staging» → copia atómica + manifest |

### Reglas

- `pre_portafolio/` no se muta (solo lectura lógica tras confirmar).
- El merge de aclaraciones (C.2) usa el manifest; el humano **no** mapea documentos base afectados.
- Si el usuario marcó portafolio sin analizar, el free reader ya corrió en A; B no re-analiza salvo que el usuario pida «re-analizar».

---

## `staging_manifest.json`

Schema: [schemas/staging_manifest.schema.json](schemas/staging_manifest.schema.json)

Campos principales:

- `process_id`, `tenant_id`, `source`
- `selected_documents[]` — path origen, path destino, hash opcional
- `selected_documents[].document_role` — función documental elegida por el usuario
- `portfolio_scenario` — escenario deducido para seed prompt Hermes
- `uploads[]` — archivos nuevos
- `clarifications[]` — `{ file, type, declared_at }`
- `free_reader_profile` — copia de `pre_portafolio/fast_analysis/profile.json`
- `prepared_at`, `prepared_by` (futuro multi-user)

---

## Relación con otras etapas

| Etapa | Handoff |
|-------|---------|
| A | Lee `pre_portafolio/` |
| B | Escribe `portafolio/inputs/` + manifest |
| C | Lee `portafolio/inputs/`; C.2 lee `clarifications[]` |
| D | Trabaja bajo `portafolio/` |

---

## Referencias

- [docs/STAGES.md](../../docs/STAGES.md)
- Legacy Gate 0.a: `../01_workflow.md` (deprecado)
