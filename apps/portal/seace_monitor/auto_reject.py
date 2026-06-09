"""Reglas de rechazo automático para publicaciones."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

import yaml

from .config import AppConfig
from .db.models import Entity, FeedItem, ProcessStatus
from .tenant_paths import tenant_settings_dir

DEFAULT_RULES_PATH = Path(__file__).with_name("auto_reject_rules.yaml")
EDITABLE_RULES_FILENAME = "auto_reject_rules.yaml"
ALLOWED_FIELDS = frozenset({"objeto", "descripcion", "nomenclatura", "entidad", "source"})
MAX_RULES_YAML_BYTES = 64 * 1024
MAX_RULE_COUNT = 100
MAX_QUERY_LENGTH = 1000


@dataclass(frozen=True)
class AutoRejectRule:
    id: str
    query: str
    reason: str


class _Node(Protocol):
    def evaluate(self, ctx: "_Context") -> bool: ...


def _normalize(text: str | None) -> str:
    value = unicodedata.normalize("NFD", text or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.lower()


def _contains_term(text: str, term: str) -> bool:
    normalized_term = _normalize(term).strip()
    if not normalized_term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_term) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


class _Context:
    def __init__(self, process: FeedItem, entity: Entity | None) -> None:
        self.fields = {
            "objeto": _normalize(process.objeto),
            "descripcion": _normalize(process.descripcion),
            "nomenclatura": _normalize(process.nomenclatura),
            "entidad": _normalize(entity.nombre if entity else ""),
            "source": _normalize(process.source),
        }
        self.default = " ".join(
            [
                self.fields["objeto"],
                self.fields["descripcion"],
                self.fields["nomenclatura"],
            ]
        ).strip()

    def text_for(self, field: str | None) -> str:
        if field is None:
            return self.default
        return self.fields[field]


@dataclass(frozen=True)
class _Term:
    value: str
    field: str | None = None

    def evaluate(self, ctx: _Context) -> bool:
        return _contains_term(ctx.text_for(self.field), self.value)


@dataclass(frozen=True)
class _And:
    nodes: list[_Node]

    def evaluate(self, ctx: _Context) -> bool:
        return all(node.evaluate(ctx) for node in self.nodes)


@dataclass(frozen=True)
class _Or:
    nodes: list[_Node]

    def evaluate(self, ctx: _Context) -> bool:
        return any(node.evaluate(ctx) for node in self.nodes)


@dataclass(frozen=True)
class _Not:
    node: _Node

    def evaluate(self, ctx: _Context) -> bool:
        return not self.node.evaluate(ctx)


@dataclass(frozen=True)
class _Field:
    field: str
    node: _Node

    def evaluate(self, ctx: _Context) -> bool:
        return _with_field(self.node, self.field).evaluate(ctx)


def _with_field(node: _Node, field: str) -> _Node:
    if isinstance(node, _Term):
        return _Term(node.value, field=field)
    if isinstance(node, _And):
        return _And([_with_field(child, field) for child in node.nodes])
    if isinstance(node, _Or):
        return _Or([_with_field(child, field) for child in node.nodes])
    if isinstance(node, _Not):
        return _Not(_with_field(node.node, field))
    if isinstance(node, _Field):
        raise ValueError("No se admite anidar campos en una consulta")
    return node


_TOKEN_RE = re.compile(r'"([^"]+)"|(\()|(\))|(:)|(-)|(\bAND\b)|(\bOR\b)|([^\s():-]+)', re.I)


class _Parser:
    def __init__(self, query: str) -> None:
        self.tokens = [match.group(0) for match in _TOKEN_RE.finditer(query)]
        self.index = 0

    def parse(self) -> _Node:
        if not self.tokens:
            raise ValueError("Consulta vacía")
        node = self._parse_or()
        if self._peek() is not None:
            raise ValueError(f"Token inesperado: {self._peek()}")
        return node

    def _peek(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _pop(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _parse_or(self) -> _Node:
        nodes = [self._parse_and()]
        while (self._peek() or "").upper() == "OR":
            self._pop()
            nodes.append(self._parse_and())
        if len(nodes) == 1:
            return nodes[0]
        return _Or(nodes)

    def _parse_and(self) -> _Node:
        nodes: list[_Node] = [self._parse_factor()]
        while True:
            nxt = self._peek()
            if nxt is None or nxt == ")" or nxt.upper() == "OR":
                break
            # `AND` explícito es opcional: la yuxtaposición ya implica AND. Se acepta el
            # keyword para que las reglas puedan escribirse de forma natural y legible.
            if nxt.upper() == "AND":
                self._pop()
                after = self._peek()
                if after is None or after == ")" or after.upper() == "OR":
                    raise ValueError("Expresión incompleta tras AND")
            nodes.append(self._parse_factor())
        if len(nodes) == 1:
            return nodes[0]
        return _And(nodes)

    def _parse_factor(self) -> _Node:
        token = self._peek()
        if token == "-":
            self._pop()
            if self._peek() is None:
                raise ValueError("Expresión incompleta")
            return _Not(self._parse_factor())
        node = self._parse_primary()
        if self._peek() == ":" and isinstance(node, _Term):
            self._pop()
            field = _normalize(node.value)
            if field not in ALLOWED_FIELDS:
                raise ValueError(f"Campo desconocido: {node.value}")
            child = self._parse_factor()
            if isinstance(child, _Field):
                raise ValueError("No se admite anidar campos en una consulta")
            return _Field(field, child)
        return node

    def _parse_primary(self) -> _Node:
        if self.index >= len(self.tokens):
            raise ValueError("Fin inesperado de consulta")
        token = self._pop()
        if token == "(":
            node = self._parse_or()
            if self._peek() != ")":
                raise ValueError("Paréntesis sin cerrar")
            self._pop()
            return node
        if token == ")":
            raise ValueError("Paréntesis de cierre inesperado")
        if token.startswith('"') and token.endswith('"'):
            return _Term(token[1:-1])
        return _Term(token)


def evaluate_query(query: str, process: FeedItem, entity: Entity | None = None) -> bool:
    return _Parser(query).parse().evaluate(_Context(process, entity))


def editable_auto_reject_rules_path(config: AppConfig) -> Path:
    if config.auto_reject_rules_path is not None:
        return config.auto_reject_rules_path
    return tenant_settings_dir(config) / EDITABLE_RULES_FILENAME


def active_auto_reject_rules_path(config: AppConfig) -> Path:
    editable = editable_auto_reject_rules_path(config)
    if editable.exists():
        return editable
    return DEFAULT_RULES_PATH


def validate_rules_yaml(text: str) -> list[AutoRejectRule]:
    if len(text.encode("utf-8")) > MAX_RULES_YAML_BYTES:
        raise ValueError("El YAML de reglas excede 64KB.")
    raw = yaml.safe_load(text) or {}
    return _rules_from_raw(raw)


def _rules_from_raw(raw: dict[str, Any]) -> list[AutoRejectRule]:
    if not isinstance(raw, dict) or not isinstance(raw.get("rules", []), list):
        raise ValueError("El YAML debe contener una lista `rules`.")
    if len(raw.get("rules", [])) > MAX_RULE_COUNT:
        raise ValueError(f"No se admiten más de {MAX_RULE_COUNT} reglas.")
    rules = []
    for item in raw.get("rules", []):
        if not isinstance(item, dict):
            raise ValueError("Cada regla debe ser un objeto YAML.")
        if not item.get("id") or not item.get("query"):
            raise ValueError("Cada regla requiere `id` y `query`.")
        query = str(item["query"])
        if not query.strip():
            raise ValueError("La consulta de una regla no puede estar vacía.")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"Las consultas no pueden exceder {MAX_QUERY_LENGTH} caracteres.")
        _Parser(query).parse()
        rules.append(
            AutoRejectRule(
                id=str(item["id"]),
                query=query,
                reason=str(item.get("reason") or item["id"]),
            )
        )
    return rules


def load_auto_reject_rules(config: AppConfig) -> list[AutoRejectRule]:
    path = active_auto_reject_rules_path(config)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    enabled_rules = [item for item in raw.get("rules", []) if item.get("enabled", True)]
    return _rules_from_raw({"rules": enabled_rules})


def first_matching_rule(
    process: FeedItem, entity: Entity | None, rules: list[AutoRejectRule]
) -> AutoRejectRule | None:
    for rule in rules:
        if evaluate_query(rule.query, process, entity):
            return rule
    return None


def autoreject_reason_text(rule: AutoRejectRule) -> str:
    """Motivo canónico (``id: reason``) que se persiste en el overlay."""
    return f"{rule.id}: {rule.reason}"


def apply_auto_reject_rules(
    process: FeedItem,
    entity: Entity | None,
    rules: list[AutoRejectRule],
    session: "Session | None" = None,
) -> AutoRejectRule | None:
    """Predicado puro de autoreject (0.3c-3): devuelve la regla que matchea sin mutar el
    feed.

    El estado del autoreject vive en el overlay por tenant (`TenantFeedDecision`), no en
    `FeedItem.status`. El caller registra la decisión vía `record_autoreject_decision`.
    Los guards consultan el overlay (si hay ``session``) y el campo legacy
    ``auto_reject_exempt`` durante la transición.
    """
    if process.status != ProcessStatus.publicada:
        return None
    if process.auto_reject_exempt:
        return None
    if session is not None and _overlay_exempt(session, process):
        return None
    return first_matching_rule(process, entity, rules)


def _overlay_exempt(session: "Session", process: FeedItem) -> bool:
    from .feed.decisions import DECISION_EXEMPT
    from .feed.repository import FeedRepository

    return FeedRepository(session).decision_for(process) == DECISION_EXEMPT
