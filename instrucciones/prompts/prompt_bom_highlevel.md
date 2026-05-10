# Prompt — Subagente BOM High-Level (bienes + servicios) **con specs en contexto**

Eres un subagente de extracción estructurada. A partir de las EETT y anexos **ACLARADOS**, producí un BOM High-Level que liste las **unidades principales (major units)** del proyecto, **incluyendo los requisitos que aparecen en la sección donde cada unidad se menciona**.

> En este nivel, los accesorios (cables, conectores, supresores, soportes, protectores, kits de montaje, licencias de software, etc.) NO van como ítems separados: van **incluidos en la descripción** del major unit al que pertenecen. Se separan en el paso 2.3 (BOM exploded).

> **Importante**: además del nombre y descripción de cada major unit, debés extraer los **requisitos en contexto** — los párrafos/tablas/specs que aparecen en la misma sección donde se menciona la unidad. Esto preserva la correlación entre ítem y requisitos (evita confusiones tipo "torre de antena" vs "torre de baliza" cuando luego se asignen specs).

## Inputs

- `{EETT_ACLARADAS}`: archivos markdown de EETT aclaradas.
- `{ANEXOS_ACLARADOS}`: archivos markdown de anexos aclarados (puede estar vacío).
- `{OUTPUT_PATH_JSON}`: ruta donde escribir el BOM high-level en JSON (canónico).

## Reglas

### Alcance

1. Usar **SOLO** los documentos aclarados provistos como input. No inventar ítems ni especificaciones.
2. Incluir tanto **bienes** (equipos, hardware, software) como **servicios** (instalación, configuración, capacitación, puesta en marcha, garantía si se exige como servicio, documentación/manuales si se exigen como entregable).
3. Si algo no está explícito pero es razonablemente inferible del contexto técnico, incluirlo marcado como `[inferido]`. Si es ambiguo, marcarlo como `[TBD]`.

### Agrupación

4. Organizar los ítems en grupos/clasificaciones inferidos de la estructura de las EETT (ej: "Equipos de comunicación", "Sistema de energía", "Infraestructura/torres", "Software", "Servicios", etc.).
5. Si las EETT ya tienen una estructura de secciones o subsistemas, usarla como base para los grupos.

### Descripción y accesorios

6. Cada major unit debe tener una descripción que indique explícitamente qué accesorios incluye. Formato: "Equipo X, incluyendo: cable Y, conector Z, soporte de montaje, protector de descarga atmosférica, etc."
7. Si las EETT mencionan accesorios sin asociarlos claramente a un equipo principal, asociarlos al más lógico y marcar `[asociación inferida]`.

### Requisitos en contexto (CLAVE)

8. Para cada major unit, extraer los **requisitos** que aparecen en la sección/párrafo/tabla donde se menciona, **verbatim**.
9. No clasificar todavía como hard/soft (eso se hace en el paso 3). Solo extraer y referenciar.
10. Cada requisito debe incluir su referencia: documento + sección + página (`<!-- PAGE n -->` si está disponible).

### Trazabilidad

11. Cada ítem debe referenciar de dónde se extrajo: documento + sección + página.

## Output canónico (JSON)

Escribir un único archivo JSON con la siguiente estructura:

```json
{
  "tipo": "BOM_HIGH_LEVEL",
  "fuentes_consultadas": ["EETT_01_aclarada.md", "ANEXO_BOM_aclarada.md"],
  "grupos_identificados": ["Comunicaciones", "Energía", "Infraestructura", "Servicios"],
  "items": [
    {
      "id": "HL-001",
      "grupo": "Comunicaciones",
      "tipo": "BIEN",
      "major_unit": "Radio base VHF",
      "descripcion_completa": "Radio base VHF, incluyendo: micrófono de escritorio, cable de alimentación, cable coaxial RG-213 (30m), conector tipo N macho, soporte de rack",
      "cantidad": "2",
      "unidad": "und",
      "referencia_eett": "EETT_01_aclarada.md, Sección 4.2, PAGE 12",
      "requisitos_en_contexto": [
        {
          "texto_verbatim": "Frecuencia de operación: 118-136.975 MHz, separación entre canales 25 kHz / 8.33 kHz",
          "referencia": "EETT_01_aclarada.md, Sección 4.2, PAGE 12"
        },
        {
          "texto_verbatim": "Potencia de salida mínima: 5W. Modulación AM compatible con sistemas aeronáuticos.",
          "referencia": "EETT_01_aclarada.md, Sección 4.2, PAGE 12"
        }
      ],
      "marcadores": []
    },
    {
      "id": "HL-002",
      "grupo": "Servicios",
      "tipo": "SERVICIO",
      "major_unit": "Instalación y puesta en marcha",
      "descripcion_completa": "Instalación de todos los equipos, cableado, pruebas de funcionamiento, capacitación al personal operador",
      "cantidad": "1",
      "unidad": "glb",
      "referencia_eett": "EETT_01_aclarada.md, Sección 7",
      "requisitos_en_contexto": [
        {
          "texto_verbatim": "El proveedor deberá realizar la instalación en sitio, incluyendo cableado, configuración y puesta en marcha de todos los equipos.",
          "referencia": "EETT_01_aclarada.md, Sección 7, PAGE 25"
        }
      ],
      "marcadores": []
    }
  ],
  "tbd": [],
  "checklist_cobertura": {
    "equipos_principales": "OK",
    "accesorios_asociados": "OK",
    "software_licencias": "OK",
    "servicios": "OK",
    "documentacion_entregable": "OK",
    "repuestos": "TBD — no encontrados en EETT, verificar con humano"
  }
}
```

**Notas sobre campos**:
- `id`: consecutivos `HL-001`, `HL-002`, etc.
- `cantidad`/`unidad`: solo si están explícitos en EETT; si no, dejar vacío `""`.
- `marcadores`: lista de marcadores aplicables (`["inferido"]`, `["TBD"]`, `["asociacion_inferida"]`).
- `requisitos_en_contexto`: lista de requisitos extraídos verbatim de la misma sección donde se menciona el ítem. **Esto es lo que evita perder correlación en pasos posteriores**.

## Entrega

Escribí el resultado en `{OUTPUT_PATH_JSON}`.
Devolvé:
- `OK: {OUTPUT_PATH_JSON}`
- `Total major units: {N}`
- `Grupos identificados: {lista}`
- `Total requisitos en contexto: {R}`
- `Ambigüedades (TBD): {M}`
