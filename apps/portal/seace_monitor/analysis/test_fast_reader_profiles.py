"""Tests de perfiles source-aware para fast reader."""

from pathlib import Path

from seace_monitor.analysis.fast_reader import (
    _build_user_context,
    _load_system_prompt,
    append_seace_cronograma,
)
from seace_monitor.config import AppConfig
from seace_monitor.db.models import Process


def _config(repo_root: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.analysis.tender.repo_path = repo_root
    return cfg


def test_load_system_prompt_uses_private_documents_profile(tmp_path: Path):
    profiles = tmp_path / "instrucciones" / "A_pre_portafolio"
    prompts = profiles / "prompts"
    prompts.mkdir(parents=True)
    (profiles / "free_reader_profiles.yaml").write_text(
        """
version: 0.1
sections:
  cronograma_proceso:
    label: Cronograma del proceso
    default: true
  requisitos_postor:
    label: Requisitos del postor
    default: true
profiles:
  private_documents:
    source_types: [private_portal]
    prompt_template: prompts/private_documents.template.md
    include_sections: [cronograma_proceso, requisitos_postor]
""",
        encoding="utf-8",
    )
    (prompts / "private_documents.template.md").write_text(
        "Prompt privado\n{{SECTIONS_BLOCK}}\n",
        encoding="utf-8",
    )
    proc = Process(source="private_portal", source_ref="ABC-123", nid_proceso="ABC-123")

    prompt = _load_system_prompt(_config(tmp_path), proc)

    assert "Prompt privado" in prompt
    assert "- Cronograma del proceso" in prompt
    assert "- Requisitos del postor" in prompt
    assert "{{SECTIONS_BLOCK}}" not in prompt


def test_build_user_context_is_not_seace_specific_for_private_sources():
    proc = Process(
        source="private_portal",
        source_ref="ABC-123",
        nid_proceso="ABC-123",
        nomenclatura="Invitación privada",
        descripcion="Compra de equipos",
    )

    context = _build_user_context(proc, [Path("bases.pdf")])

    assert "Referencia private_portal: ABC-123" in context
    assert "Nomenclatura SEACE" not in context
    assert "NO incluyas sección de cronograma" not in context


def test_append_seace_cronograma_only_applies_to_seace_sources():
    proc = Process(
        source="private_portal",
        source_ref="ABC-123",
        nid_proceso="ABC-123",
        cronograma_json='[{"etapa": "Presentación", "fecha_inicio": "01/06/2026", "fecha_fin": "02/06/2026"}]',
    )

    assert append_seace_cronograma("## Resumen", proc) == "## Resumen"
