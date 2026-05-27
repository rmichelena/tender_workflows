"""Reglas de rechazo automático para publicaciones."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus

DEFAULT_RULES_PATH = Path(__file__).with_name("auto_reject_rules.yaml")


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


class _Context:
    def __init__(self, process: Process, entity: Entity | None) -> None:
        self.fields = {
            "objeto": _normalize(process.objeto),
            "descripcion": _normalize(process.descripcion),
            "nomenclatura": _normalize(process.nomenclatura),
            "entidad": _normalize(entity.nombre if entity else ""),
            "source": _normalize(process.source),
        }
        self.default = " ".join(
            [
                self.fields["descripcion"],
                self.fields["nomenclatura"],
            ]
        ).strip()

    def text_for(self, field: str | None) -> str:
        if field is None:
            return self.default
        return self.fields.get(field, "")


@dataclass(frozen=True)
class _Term:
    value: str
    field: str | None = None

    def evaluate(self, ctx: _Context) -> bool:
        return _normalize(self.value) in ctx.text_for(self.field)


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
        return node
    return node


_TOKEN_RE = re.compile(r'"([^"]+)"|(\()|(\))|(:)|(-)|(\bOR\b)|([^\s():-]+)', re.I)


class _Parser:
    def __init__(self, query: str) -> None:
        self.tokens = [match.group(0) for match in _TOKEN_RE.finditer(query)]
        self.index = 0

    def parse(self) -> _Node:
        if not self.tokens:
            return _And([])
        return self._parse_or()

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
        nodes: list[_Node] = []
        while self._peek() is not None and self._peek() != ")" and self._peek().upper() != "OR":
            nodes.append(self._parse_factor())
        if len(nodes) == 1:
            return nodes[0]
        return _And(nodes)

    def _parse_factor(self) -> _Node:
        token = self._peek()
        if token == "-":
            self._pop()
            return _Not(self._parse_factor())
        node = self._parse_primary()
        if self._peek() == ":" and isinstance(node, _Term):
            self._pop()
            return _Field(node.value, self._parse_factor())
        return node

    def _parse_primary(self) -> _Node:
        token = self._pop()
        if token == "(":
            node = self._parse_or()
            if self._peek() == ")":
                self._pop()
            return node
        if token.startswith('"') and token.endswith('"'):
            return _Term(token[1:-1])
        return _Term(token)


def evaluate_query(query: str, process: Process, entity: Entity | None = None) -> bool:
    return _Parser(query).parse().evaluate(_Context(process, entity))


def load_auto_reject_rules(config: AppConfig) -> list[AutoRejectRule]:
    path = config.auto_reject_rules_path or DEFAULT_RULES_PATH
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        AutoRejectRule(
            id=str(item["id"]),
            query=str(item["query"]),
            reason=str(item.get("reason") or item["id"]),
        )
        for item in raw.get("rules", [])
        if item.get("enabled", True)
    ]


def first_matching_rule(
    process: Process, entity: Entity | None, rules: list[AutoRejectRule]
) -> AutoRejectRule | None:
    for rule in rules:
        if evaluate_query(rule.query, process, entity):
            return rule
    return None


def apply_auto_reject_rules(
    process: Process, entity: Entity | None, rules: list[AutoRejectRule]
) -> AutoRejectRule | None:
    if process.status != ProcessStatus.publicada:
        return None
    rule = first_matching_rule(process, entity, rules)
    if rule is None:
        return None
    process.status = ProcessStatus.autorejected
    process.auto_reject_reason = f"{rule.id}: {rule.reason}"
    return rule
