"""Tests del motor de autoreject."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .auto_reject import (
    AutoRejectRule,
    apply_auto_reject_rules,
    autoreject_reason_text,
    evaluate_query,
    load_auto_reject_rules,
    validate_rules_yaml,
)
from .client import FichaResult, ProcessRow
from .config import AppConfig
from .db.models import Base, Entity, Process, ProcessStatus, TenantFeedDecision
from .parser import FichaData
from .scan_options import ScanOptions
from .scanner import MultiEntityScanner


def _process(*, objeto: str, descripcion: str, entidad: str = "Entidad") -> tuple[Process, Entity]:
    entity = Entity(ruc="20123456789", nombre=entidad, activa=True)
    process = Process(
        entity_id=1,
        anio=2026,
        nid_proceso="1",
        nomenclatura="AS-SM-1-2026",
        objeto=objeto,
        descripcion=descripcion,
        status=ProcessStatus.publicada,
    )
    return process, entity


def test_google_style_query_supports_field_or_phrase_and_exclusion():
    proc, entity = _process(
        objeto="Servicio",
        descripcion="SERVICIO DE ALQUILER DE CAMIONETAS PARA TRASLADO DE PERSONAL",
        entidad="MINISTERIO DE ENERGIA Y MINAS",
    )

    assert evaluate_query(
        'objeto:servicio ("alquiler de camionetas" OR "transporte de personal") -entidad:corpac',
        proc,
        entity,
    )


def test_and_keyword_is_an_operator_not_a_search_term():
    # Regresión: `AND` debe ser operador (como `OR`), no un término literal. Antes el
    # tokenizer trataba AND como término → toda regla con AND explícito nunca matcheaba.
    proc, entity = _process(
        objeto="Bien",
        descripcion="Adquisición de suministro de combustible Diesel B5 S-50",
    )
    assert evaluate_query(
        "objeto:bien (suministro AND (combustible OR gasohol OR diesel))", proc, entity
    )


def test_and_keyword_requires_both_operands():
    proc, entity = _process(
        objeto="Bien",
        descripcion="Adquisición de combustible para la flota vehicular",  # sin 'suministro'
    )
    # Falta 'suministro' → la regla con AND no debe matchear.
    assert not evaluate_query(
        "objeto:bien (suministro AND (combustible OR gasohol OR diesel))", proc, entity
    )


def test_and_keyword_in_alquiler_local_rule():
    proc, entity = _process(
        objeto="Servicio",
        descripcion="SERVICIO DE ARRENDAMIENTO DE LOCAL PARA OFICINAS ADMINISTRATIVAS",
    )
    assert evaluate_query(
        "objeto:servicio ((alquiler OR arrendamiento) AND "
        "(inmueble OR inmuebles OR local OR locales OR ambientes))",
        proc,
        entity,
    )


def test_google_style_query_exclusion_can_keep_corpac_transport():
    proc, entity = _process(
        objeto="Servicio",
        descripcion="CONTRATACION DEL SERVICIO DE TRANSPORTE DE PERSONAL CORPAC",
        entidad="CORPORACION PERUANA DE AEROPUERTOS Y AVIACION COMERCIAL S.A.- CORPAC",
    )

    assert not evaluate_query(
        'objeto:servicio ("transporte de personal" OR traslado OR movilidad) -entidad:corpac',
        proc,
        entity,
    )


def test_food_rule_does_not_match_operaciones_substring():
    proc, entity = _process(
        objeto="Bien",
        descripcion=(
            "SISTEMA DE SEGURIDAD INFORMATICA PARA EL CENTRO NACIONAL "
            "DE OPERACIONES DE IMAGENES SATELITALES"
        ),
    )
    rule = AutoRejectRule(
        id="bien_alimentos",
        query="objeto:bien (alimentos OR viveres OR raciones OR leche OR arroz OR azucar)",
        reason="Alimentos fuera de foco",
    )

    assert apply_auto_reject_rules(proc, entity, [rule]) is None
    assert proc.status == ProcessStatus.publicada


def test_food_rule_still_matches_raciones_as_word():
    proc, entity = _process(
        objeto="Bien",
        descripcion="ADQUISICION DE RACIONES PARA PERSONAL",
    )
    rule = AutoRejectRule(
        id="bien_alimentos",
        query="objeto:bien (alimentos OR viveres OR raciones OR leche OR arroz OR azucar)",
        reason="Alimentos fuera de foco",
    )

    assert apply_auto_reject_rules(proc, entity, [rule]) == rule
    # 0.3c-3: el predicado no muta el feed; el item sigue publicada.
    assert proc.status == ProcessStatus.publicada


def test_apply_auto_reject_is_a_pure_predicate():
    # 0.3c-3: apply_auto_reject_rules no muta status ni escribe el motivo en el feed; la
    # decisión la persiste el caller en el overlay. El motivo canónico se deriva de la regla.
    proc, entity = _process(
        objeto="Bien",
        descripcion="ADQUISICION DE UNIFORMES Y CALZADO PARA PERSONAL",
    )
    rule = AutoRejectRule(
        id="bien_uniformes",
        query="objeto:bien (uniformes OR vestimenta OR calzado)",
        reason="Bienes de uniformes/vestimenta fuera de foco",
    )

    match = apply_auto_reject_rules(proc, entity, [rule])

    assert match == rule
    assert proc.status == ProcessStatus.publicada
    assert proc.auto_reject_reason is None
    assert (
        autoreject_reason_text(match)
        == "bien_uniformes: Bienes de uniformes/vestimenta fuera de foco"
    )


def test_apply_auto_reject_does_not_change_downloaded_process():
    proc, entity = _process(
        objeto="Servicio",
        descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
    )
    proc.status = ProcessStatus.descargada
    rule = AutoRejectRule(
        id="servicio_limpieza",
        query="objeto:servicio limpieza",
        reason="Limpieza fuera de foco",
    )

    assert apply_auto_reject_rules(proc, entity, [rule]) is None
    assert proc.status == ProcessStatus.descargada


def test_apply_auto_reject_skips_human_exempt_process():
    proc, entity = _process(
        objeto="Servicio",
        descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
    )
    proc.auto_reject_exempt = True
    rule = AutoRejectRule(
        id="servicio_limpieza",
        query="objeto:servicio limpieza",
        reason="Limpieza fuera de foco",
    )

    assert apply_auto_reject_rules(proc, entity, [rule]) is None
    assert proc.status == ProcessStatus.publicada


def test_apply_auto_reject_skips_overlay_exempt_without_legacy_flag():
    from .feed import record_exempt_decision
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20123456789", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="1",
        nid_proceso="1",
        nomenclatura="NOM-1",
        status=ProcessStatus.publicada,
        objeto="Servicio",
        descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
        auto_reject_exempt=False,
    )
    session.add(proc)
    session.flush()
    record_exempt_decision(session, proc)
    session.commit()

    rule = AutoRejectRule(
        id="servicio_limpieza",
        query="objeto:servicio limpieza",
        reason="Limpieza fuera de foco",
    )
    assert apply_auto_reject_rules(proc, entity, [rule], session=session) is None


def test_load_default_rules_includes_alquiler_and_food_patterns():
    rules = load_auto_reject_rules(AppConfig())
    queries = "\n".join(rule.query for rule in rules)

    assert "alquiler" in queries
    assert "camionetas" in queries
    assert "local" in queries
    assert "alimentos" in queries


def test_process_auto_reject_reason_defaults_and_backfills():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    entity = Entity(ruc="20123456789", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    session.add(
        Process(
            entity_id=entity.id,
            anio=2026,
            nid_proceso="1",
            nomenclatura="AS-SM-1-2026",
        )
    )
    session.commit()

    proc = session.query(Process).one()
    assert proc.auto_reject_reason is None
    assert proc.auto_reject_exempt is False


def test_validate_rules_rejects_malformed_queries():
    malformed = [
        "rules:\n- id: empty\n  query: ''\n",
        "rules:\n- id: trailing_or\n  query: 'objeto:servicio OR'\n",
        "rules:\n- id: empty_group\n  query: 'objeto:servicio ()'\n",
        "rules:\n- id: open_paren\n  query: '(objeto:servicio'\n",
        "rules:\n- id: missing_value\n  query: 'objeto:'\n",
        "rules:\n- id: unknown_field\n  query: 'cuantia:50000'\n",
        "rules:\n- id: nested_field\n  query: 'objeto:descripcion:limpieza'\n",
        "rules:\n- id: bare_not\n  query: '-'\n",
    ]

    for text in malformed:
        try:
            validate_rules_yaml(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid rules YAML: {text}")


def test_bare_term_matches_objeto_in_default_context():
    proc, entity = _process(
        objeto="Servicio de limpieza de oficinas",
        descripcion="Contratación menor",
    )

    assert evaluate_query("limpieza", proc, entity)


def test_bare_term_still_requires_word_boundary_in_objeto():
    proc, entity = _process(
        objeto="Servicio de limpieza de oficinas",
        descripcion="Contratación menor",
    )

    assert not evaluate_query("limp", proc, entity)


def test_scanner_applies_auto_reject_rules_after_upsert(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    entity = Entity(ruc="20123456789", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    row = ProcessRow(
        row_index=0,
        numero="1",
        fecha_publicacion="01/01/2026",
        nomenclatura="AS-SM-1-2026",
        reiniciado_desde="",
        objeto="Servicio",
        descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso="123",
        nid_convocatoria="conv",
        nid_sistema="",
        link_id="link",
        ntipo="",
    )

    class FakeClient:
        def open_ficha(self, _row):
            return FichaResult(ficha_id="f1", html="<html>", url="http://x")

        def refresh_list_page_state(self, _page):
            return None

    monkeypatch.setattr(
        "seace_monitor.scanner.parse_ficha",
        lambda _html, ficha_id, nid: FichaData(
            ficha_id=ficha_id,
            nid_proceso=nid,
            nomenclatura=row.nomenclatura,
            descripcion=row.descripcion,
            objeto=row.objeto,
            fecha_publicacion=row.fecha_publicacion,
        ),
    )

    scanner = MultiEntityScanner(AppConfig(), session)
    created = scanner._upsert_from_ficha(
        entity,
        FakeClient(),
        row,
        proc=None,
        options=ScanOptions(),
    )

    assert created is True
    proc = session.query(Process).one()
    # 0.3c-3: el scanner ya no muta status; la decisión vive en el overlay.
    assert proc.status == ProcessStatus.publicada
    decision = (
        session.query(TenantFeedDecision).filter_by(feed_item_id=proc.id).one()
    )
    assert decision.decision == "autorejected"
    assert decision.reason.startswith("servicio_limpieza:")
