from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import __version__
from .fortran import _END_PROCEDURE, _PROCEDURE_START, _parse_declaration, _runtime_control, extract_fortran_file, logical_statements


_NAME = re.compile(r"(?i)[a-z_][a-z0-9_]*(?:%[a-z_][a-z0-9_]*)*")
_TOKEN = re.compile(
    r"(?ix)\s*("
    r"\d+(?:\.\d*)?(?:[ed][+-]?\d+)?|\.\d+(?:[ed][+-]?\d+)?|"
    r"[a-z_][a-z0-9_]*(?:%[a-z_][a-z0-9_]*)*|"
    r"\.and\.|\.or\.|\.not\.|\.eq\.|\.ne\.|\.lt\.|\.le\.|\.gt\.|\.ge\.|"
    r"\*\*|=>|==|/=|<=|>=|[+\-*/(),:<>])"
)


def _tokenize(expression: str) -> list[str] | None:
    tokens = []
    position = 0
    while position < len(expression):
        match = _TOKEN.match(expression, position)
        if not match:
            return None
        tokens.append(match.group(1))
        position = match.end()
    return tokens


class _ExpressionParser:
    binding = {
        ".or.": (1, 2), ".and.": (3, 4),
        "==": (5, 6), "/=": (5, 6), "<": (5, 6), "<=": (5, 6), ">": (5, 6), ">=": (5, 6),
        ".eq.": (5, 6), ".ne.": (5, 6), ".lt.": (5, 6), ".le.": (5, 6), ".gt.": (5, 6), ".ge.": (5, 6),
        "+": (7, 8), "-": (7, 8), "*": (9, 10), "/": (9, 10), "**": (12, 11),
    }

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.position = 0

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.position += 1
        return token

    def parse(self, minimum: int = 0) -> dict:
        token = self.take()
        lower = token.lower()
        if lower in {"+", "-", ".not."}:
            left = {"kind": "unary", "operator": lower, "operand": self.parse(11)}
        elif token == "(":
            left = self.parse(0)
            if self.take() != ")":
                raise ValueError("missing closing parenthesis")
        elif re.fullmatch(r"(?i)\d+(?:\.\d*)?(?:[ed][+-]?\d+)?|\.\d+(?:[ed][+-]?\d+)?", token):
            left = {"kind": "literal", "value": token}
        elif _NAME.fullmatch(token):
            left = {"kind": "reference", "name": token, "arguments": []}
            if self.peek() == "(":
                self.take()
                arguments = []
                if self.peek() != ")":
                    while True:
                        arguments.append(self.parse(0))
                        if self.peek() != ",":
                            break
                        self.take()
                if self.take() != ")":
                    raise ValueError("missing reference parenthesis")
                left["arguments"] = arguments
        else:
            raise ValueError(f"unexpected token {token}")

        while True:
            operator = (self.peek() or "").lower()
            if operator not in self.binding:
                break
            left_binding, right_binding = self.binding[operator]
            if left_binding < minimum:
                break
            self.take()
            right = self.parse(right_binding)
            left = {"kind": "binary", "operator": operator, "left": left, "right": right}
        return left


def parse_expression(expression: str) -> tuple[dict | None, str]:
    tokens = _tokenize(expression)
    if not tokens:
        return None, "unsupported_tokens"
    try:
        parser = _ExpressionParser(tokens)
        tree = parser.parse()
        if parser.peek() is not None:
            return None, "unconsumed_tokens"
        return tree, "parsed"
    except ValueError:
        return None, "parse_error"


def _references(expression: str) -> list[str]:
    intrinsic_words = {"true", "false", "kind"}
    return sorted(
        {match.group(0).split("%")[-1].lower() for match in _NAME.finditer(expression)} - intrinsic_words
    )


def _split_assignment(text: str) -> tuple[str, str, str] | None:
    depth = 0
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith("=>", index):
            return text[:index].strip(), "pointer", text[index + 2 :].strip()
        elif depth == 0 and char == "=":
            before = text[index - 1] if index else ""
            after = text[index + 1] if index + 1 < len(text) else ""
            if before not in "</>=" and after != "=":
                return text[:index].strip(), "assignment", text[index + 1 :].strip()
        index += 1
    return None


def _single_line_if(text: str) -> tuple[str | None, str]:
    if not re.match(r"(?i)^\s*if\s*\(", text):
        return None, text
    start = text.find("(")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                tail = text[index + 1 :].strip()
                if tail and not re.match(r"(?i)^then\b", tail):
                    return text[start + 1 : index].strip(), tail
                break
    return None, text


def _target(text: str) -> dict | None:
    match = re.fullmatch(r"(?is)\s*([a-z_][a-z0-9_]*(?:%[a-z_][a-z0-9_]*)*)\s*(?:\((.*)\))?\s*", text)
    if not match:
        return None
    qualified = match.group(1)
    return {
        "text": text.strip(),
        "qualified_name": qualified,
        "base_name": qualified.split("%")[-1].lower(),
        "indices": [item.strip() for item in _split_commas(match.group(2) or "")],
    }


def _split_commas(text: str) -> list[str]:
    if not text:
        return []
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _loop_control(text: str, stack: list[dict], span: dict) -> bool:
    if re.match(r"(?i)^\s*end\s*do\b|^\s*enddo\b", text):
        if stack:
            stack.pop()
        return True
    match = re.match(r"(?i)^\s*(?:[a-z_][a-z0-9_]*\s*:\s*)?do\s+([a-z_][a-z0-9_]*)\s*=\s*(.*)$", text)
    if match:
        bounds = _split_commas(match.group(2))
        stack.append({"kind": "do", "variable": match.group(1), "bounds": bounds, "span": span})
        return True
    match = re.match(r"(?i)^\s*(?:[a-z_][a-z0-9_]*\s*:\s*)?do\s+while\s*\((.*)\)\s*$", text)
    if match:
        stack.append({"kind": "do_while", "expression": match.group(1), "span": span})
        return True
    return False


def _effective_files(index: dict, graph: dict, source_root: Path) -> list[dict]:
    result = list(index.get("files", []))
    paths = {item["path"] for item in result}
    for path in sorted({node["span"]["path"] for node in graph.get("nodes", [])} - paths):
        source = source_root / path
        if source.is_file():
            result.append(extract_fortran_file(source, source_root))
    return result


def _assignments_for_procedure(source_root: Path, procedure: dict, file: dict) -> list[dict]:
    path = source_root / file["path"]
    runtime_stack: list[dict] = []
    loop_stack: list[dict] = []
    assignments = []
    declarations = {
        item["name"].lower(): item
        for item in file.get("declarations", [])
        if item.get("scope_id") == procedure["id"]
    }
    for statement in logical_statements(path):
        if statement.start_line <= procedure["span"]["start_line"] or statement.start_line >= procedure["end_line"]:
            continue
        span = {"path": file["path"], "start_line": statement.start_line, "end_line": statement.end_line}
        if _runtime_control(statement, runtime_stack, file["path"]):
            continue
        if _loop_control(statement.text, loop_stack, span):
            continue
        if _parse_declaration(statement, file["path"], procedure["id"]):
            continue
        inline_condition, executable = _single_line_if(statement.text)
        split = _split_assignment(executable)
        if not split:
            continue
        left, operation, right = split
        target = _target(left)
        if not target:
            continue
        tree, parse_status = parse_expression(right)
        dependencies = _references(right)
        record = {
                "procedure_id": procedure["id"],
                "procedure": procedure["name"],
                "operation": operation,
                "assignment_class": (
                    "pointer_assignment" if operation == "pointer"
                    else "self_update" if target["base_name"] in dependencies
                    else "overwrite"
                ),
                "target": target,
                "target_declaration": declarations.get(target["base_name"]),
                "statement_text": executable,
                "rhs_text": right,
                "rhs_ir": tree,
                "parse_status": parse_status,
                "dependencies": dependencies,
                "cpp_condition": statement.condition,
                "runtime_conditions": [dict(item) for item in runtime_stack]
                + ([{"kind": "inline_if", "expression": inline_condition, "span": span}] if inline_condition else []),
                "loops": [dict(item) for item in loop_stack],
                "span": span,
                "source_sha256": file.get("sha256"),
            }
        record["evidence_id"] = "eq-" + hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        assignments.append(record)
    return assignments


def create_equation_evidence(
    index: dict,
    graph: dict,
    source_root: Path,
    variables: list[str],
    procedure_names: list[str] | None = None,
) -> dict:
    variables = sorted({item.lower() for item in variables})
    if not variables:
        raise ValueError("at least one variable is required")
    files = _effective_files(index, graph, source_root)
    file_by_path = {item["path"]: item for item in files}
    procedures = {
        procedure["id"]: procedure
        for file in files
        for procedure in file.get("procedures", [])
    }
    selected_ids = {node["id"] for node in graph.get("nodes", [])}
    procedure_filter = sorted({item.lower() for item in procedure_names or []})
    if procedure_filter:
        selected_ids = {
            procedure_id for procedure_id in selected_ids
            if procedure_id in procedures and procedures[procedure_id]["name"].lower() in procedure_filter
        }
        found = {procedures[item]["name"].lower() for item in selected_ids}
        missing = sorted(set(procedure_filter) - found)
        if missing:
            raise ValueError(f"procedures are not present in graph: {', '.join(missing)}")
    all_assignments: list[dict] = []
    for procedure_id in sorted(selected_ids):
        procedure = procedures.get(procedure_id)
        if not procedure or procedure.get("end_line") is None:
            continue
        file = file_by_path[procedure["span"]["path"]]
        all_assignments.extend(_assignments_for_procedure(source_root, procedure, file))

    by_scope: dict[str, list[dict]] = defaultdict(list)
    for assignment in all_assignments:
        by_scope[assignment["procedure_id"]].append(assignment)
    selected = []
    tracked_by_scope = {}
    for scope, assignments in sorted(by_scope.items()):
        target_names = {item["target"]["base_name"] for item in assignments}
        needed = set(variables)
        changed = True
        chosen: set[int] = set()
        while changed:
            changed = False
            for position, assignment in enumerate(assignments):
                if assignment["target"]["base_name"] not in needed:
                    continue
                if position not in chosen:
                    chosen.add(position)
                    changed = True
                for dependency in assignment["dependencies"]:
                    if dependency in target_names and dependency not in needed:
                        needed.add(dependency)
                        changed = True
        tracked_by_scope[scope] = sorted(needed)
        selected.extend(assignments[position] for position in sorted(chosen))

    call_links = []
    for edge in graph.get("edges", []):
        if edge.get("caller_id") not in selected_ids:
            continue
        mappings = [
            item for item in edge.get("argument_mappings", [])
            if item.get("callee_dummy", "").lower() in variables
            or any(re.search(rf"(?i)(?<![a-z0-9_]){re.escape(variable)}(?![a-z0-9_])", item.get("caller_expression", "")) for variable in variables)
        ]
        if mappings:
            call_links.append(
                {
                    "caller_id": edge["caller_id"], "callee_id": edge["callee_id"],
                    "call_span": edge["call_span"], "mappings": mappings,
                    "confidence": edge.get("confidence"),
                }
            )

    core = {
        "scheme": graph.get("scheme"),
        "variables": variables,
        "procedure_filter": procedure_filter or None,
        "configuration": graph.get("configuration", {}),
        "graph_sha256": hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "assignments": selected,
        "call_links": call_links,
    }
    return {
        "schema_version": 1,
        "generator_version": __version__,
        "evidence_type": "scoped_fortran_equation_evidence",
        "evidence_id": "equations-" + hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16],
        **core,
        "assignment_count": len(selected),
        "parsed_assignment_count": sum(item["parse_status"] == "parsed" for item in selected),
        "unparsed_assignment_count": sum(item["parse_status"] != "parsed" for item in selected),
        "tracked_variables_by_scope": tracked_by_scope,
        "limitations": [
            "Lexical expression parsing does not distinguish array references from function calls.",
            "The trace follows scoped assignments and explicit call mappings, not pointer aliasing or indirect calls.",
            "An assignment and INTENT metadata do not establish scientific equivalence with a published equation.",
            "Only assignments in procedures selected by the supplied graph are considered.",
        ],
    }


def equation_evidence_to_markdown(evidence: dict) -> str:
    lines = [f"# {evidence['scheme']} equation evidence", ""]
    lines.extend(
        [
            f"- Evidence ID: `{evidence['evidence_id']}`",
            f"- Variables: `{', '.join(evidence['variables'])}`",
            f"- Assignments: {evidence['assignment_count']}",
            f"- Parsed expressions: {evidence['parsed_assignment_count']}",
            f"- Unparsed expressions: {evidence['unparsed_assignment_count']}",
            f"- Call mappings: {len(evidence['call_links'])}", "", "## Scoped assignments", "",
        ]
    )
    for item in evidence["assignments"]:
        location = f"{item['span']['path']}:{item['span']['start_line']}"
        intent = (item.get("target_declaration") or {}).get("intent") or "local/unknown"
        lines.append(
            f"- [{item['evidence_id']}] `{item['procedure']}`: `{item['target']['text']} = {item['rhs_text']}` "
            f"— `{location}` (`{intent}`, {item['parse_status']})"
        )
    lines.extend(["", "## Limitations", ""] + [f"- {item}" for item in evidence["limitations"]])
    return "\n".join(lines) + "\n"
