from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .util import run, sha256_file

FORTRAN_SUFFIXES = {".f", ".f90", ".f95", ".for"}
_NAME = r"[a-z_][a-z0-9_]*"
_TYPE_PREFIX = (
    r"double\s+precision|double\s+complex|"
    r"(?:integer|real|logical|complex)(?:\s*\([^)]*\)|\s*\*\s*[^,\s:]+)?|"
    r"character(?:\s*\([^)]*\)|\s*\*\s*[^,\s:]+)?|"
    r"type\s*\([^)]*\)|class\s*\([^)]*\)"
)
_MODULE_START = re.compile(rf"(?i)^\s*module\s+({_NAME})\s*$")
_PROCEDURE_START = re.compile(
    rf"(?i)^\s*(?P<prefix>(?:(?:recursive|pure|elemental|impure|module|non_recursive)\s+|"
    rf"(?:{_TYPE_PREFIX})\s+)*)"
    rf"(?P<kind>subroutine|function)\s+"
    rf"(?P<name>{_NAME})\s*(?:\((?P<args>[^)]*)\))?\s*(?:result\s*\((?P<result>{_NAME})\))?"
)
_END_MODULE = re.compile(r"(?i)^\s*end\s*module\b")
_END_PROCEDURE = re.compile(
    rf"(?i)^\s*end\s*(?:(?:subroutine|function)(?:\s+{_NAME})?)?\s*$"
)
_USE = re.compile(
    rf"(?i)^\s*use\s*(?:,\s*(?P<nature>intrinsic|non_intrinsic)\s*)?(?:::)?\s*"
    rf"(?P<module>{_NAME})(?:\s*,\s*only\s*:\s*(?P<only>.*))?\s*$"
)
_CALL = re.compile(rf"(?i)^\s*call\s+(?P<target>{_NAME}(?:\s*%\s*{_NAME})*)\s*(?:\((?P<args>.*)\))?")
_DECLARATION = re.compile(
    rf"(?ix)^\s*(?P<type>{_TYPE_PREFIX})"
    r"(?P<rest>.*)$"
)
_DECL_ATTRIBUTE = re.compile(
    r"(?ix)^\s*(intent\s*\([^)]*\)|dimension\s*\([^)]*\)|parameter|optional|"
    r"allocatable|pointer|target|save|public|private|external|intrinsic|volatile|"
    r"contiguous|value|asynchronous|protected)\s*$"
)


@dataclass(frozen=True)
class Statement:
    text: str
    start_line: int
    end_line: int
    condition: str | None


def _strip_comment(line: str) -> str:
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if char == quote:
                if index + 1 < len(line) and line[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "!":
            return line[:index]
        index += 1
    return line


def _split_top_level(text: str, delimiter: str = ",") -> list[str]:
    pieces: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == delimiter and depth == 0:
            pieces.append(text[start:index].strip())
            start = index + 1
        index += 1
    pieces.append(text[start:].strip())
    return [piece for piece in pieces if piece]


def _condition_text(stack: list[str]) -> str | None:
    return " && ".join(stack) if stack else None


def _update_conditions(directive: str, stack: list[str]) -> None:
    match = re.match(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b\s*(.*)", directive, re.I)
    if not match:
        return
    action, expression = match.group(1).lower(), match.group(2).strip()
    if action == "if":
        stack.append(expression or "<empty #if>")
    elif action == "ifdef":
        stack.append(f"defined({expression})")
    elif action == "ifndef":
        stack.append(f"!defined({expression})")
    elif action == "elif" and stack:
        stack[-1] = f"elif({expression})"
    elif action == "else" and stack:
        stack[-1] = f"else({stack[-1]})"
    elif action == "endif" and stack:
        stack.pop()


def logical_statements(path: Path) -> Iterable[Statement]:
    """Yield normalized statements while retaining raw line spans and CPP context."""
    lines = path.read_text(errors="replace").splitlines()
    conditions: list[str] = []
    parts: list[str] = []
    start_line = 0
    statement_condition: str | None = None

    for number, raw in enumerate(lines, 1):
        if raw.lstrip().startswith("#"):
            _update_conditions(raw.lstrip(), conditions)
            continue

        # Traditional fixed-form comment markers are safe only in column one.
        if path.suffix in {".f", ".for"} and raw[:1] in {"c", "C", "*"}:
            continue
        clean = _strip_comment(raw).strip()
        if not clean:
            continue

        if not parts:
            start_line = number
            statement_condition = _condition_text(conditions)
        if clean.startswith("&"):
            clean = clean[1:].lstrip()
        continues = clean.endswith("&")
        if continues:
            clean = clean[:-1].rstrip()
        if clean:
            parts.append(clean)
        if not continues:
            text = " ".join(parts).strip()
            if text:
                # Semicolon-separated statements are uncommon in WRF. Preserve
                # the same span for each deterministic top-level statement.
                for piece in _split_top_level(text, ";"):
                    yield Statement(piece, start_line, number, statement_condition)
            parts = []

    if parts:
        yield Statement(" ".join(parts).strip(), start_line, len(lines), statement_condition)


def _span(path: str, statement: Statement) -> dict:
    return {"path": path, "start_line": statement.start_line, "end_line": statement.end_line}


def _scope_id(path: str, modules: list[dict], procedures: list[dict]) -> str:
    if procedures:
        return procedures[-1]["id"]
    if modules:
        return modules[-1]["id"]
    return f"file:{path}"


def _argument_records(text: str | None) -> list[dict]:
    if not text:
        return []
    return [{"position": position, "name": value.strip()} for position, value in enumerate(_split_top_level(text), 1)]


def _parse_import(statement: Statement, relative: str, scope: str) -> dict | None:
    match = _USE.match(statement.text)
    if not match:
        return None
    items = []
    for position, raw in enumerate(_split_top_level(match.group("only") or ""), 1):
        if "=>" in raw:
            local, source = (part.strip() for part in raw.split("=>", 1))
        else:
            local = source = raw.strip()
        items.append({"position": position, "local_name": local, "source_name": source})
    return {
        "scope_id": scope,
        "module": match.group("module"),
        "nature": (match.group("nature") or "unspecified").lower(),
        "only": match.group("only") is not None,
        "items": items,
        "condition": statement.condition,
        "span": _span(relative, statement),
    }


def _parse_call(statement: Statement, relative: str, scope: str) -> dict | None:
    match = _CALL.match(statement.text)
    if not match:
        return None
    arguments = []
    for position, raw in enumerate(_split_top_level(match.group("args") or ""), 1):
        keyword = None
        expression = raw
        if "=" in raw and not re.search(r"(==|/=|<=|>=)", raw):
            left, right = raw.split("=", 1)
            if re.fullmatch(_NAME, left.strip(), re.I):
                keyword, expression = left.strip(), right.strip()
        arguments.append({"position": position, "keyword": keyword, "expression": expression})
    target = re.sub(r"\s+", "", match.group("target"))
    return {
        "scope_id": scope,
        "target": target,
        "dispatch": "type_bound" if "%" in target else "direct_name",
        "arguments": arguments,
        "condition": statement.condition,
        "runtime_conditions": [],
        "span": _span(relative, statement),
    }


def _runtime_control(statement: Statement, stack: list[dict], relative: str) -> bool:
    text = statement.text.strip()
    span = _span(relative, statement)
    end_if = re.match(r"(?i)^end\s*if\b|^endif\b", text)
    end_select = re.match(r"(?i)^end\s*select\b", text)
    if end_if:
        if stack and stack[-1]["kind"] == "if":
            stack.pop()
        return True
    if end_select:
        if stack and stack[-1]["kind"] == "case":
            stack.pop()
        return True

    else_if = re.match(r"(?i)^else\s*if\s*\((.*)\)\s*then\b|^elseif\s*\((.*)\)\s*then\b", text)
    if else_if:
        if stack and stack[-1]["kind"] == "if":
            expression = next(group for group in else_if.groups() if group is not None).strip()
            stack[-1] = {"kind": "if", "expression": expression, "branch": "elseif", "span": span}
        return True
    if re.match(r"(?i)^else\b", text):
        if stack and stack[-1]["kind"] == "if":
            previous = stack[-1]
            stack[-1] = {
                "kind": "if",
                "expression": f".not. ({previous['expression']})",
                "branch": "else",
                "span": span,
            }
        return True

    select_match = re.match(r"(?i)^(?:[a-z_][a-z0-9_]*\s*:\s*)?select\s+case\s*\((.*)\)\s*$", text)
    if select_match:
        stack.append(
            {
                "kind": "case",
                "selector": select_match.group(1).strip(),
                "values": [],
                "default": False,
                "span": span,
            }
        )
        return True
    case_match = re.match(r"(?i)^case\s*(default|\((.*)\))", text)
    if case_match:
        if stack and stack[-1]["kind"] == "case":
            current = stack[-1]
            current["default"] = case_match.group(1).lower() == "default"
            current["values"] = _split_top_level(case_match.group(2) or "")
            current["span"] = span
        return True

    if_match = re.match(r"(?i)^if\s*\((.*)\)\s*then\s*$", text)
    if if_match:
        stack.append(
            {
                "kind": "if",
                "expression": if_match.group(1).strip(),
                "branch": "if",
                "span": span,
            }
        )
        return True
    return False


def _parse_declaration(statement: Statement, relative: str, scope: str) -> list[dict]:
    match = _DECLARATION.match(statement.text)
    if not match:
        return []
    rest = match.group("rest").strip()
    if not rest or re.match(r"(?i)^\s*(function|subroutine)\b", rest):
        return []
    if "::" in rest:
        attribute_text, variable_text = rest.split("::", 1)
        attribute_parts = _split_top_level(attribute_text.lstrip(" ,"))
    else:
        attribute_parts = []
        variable_text = rest
    attributes = [part.strip() for part in attribute_parts if _DECL_ATTRIBUTE.match(part)]
    intent = None
    dimension_attribute = None
    for attribute in attributes:
        intent_match = re.match(r"(?i)intent\s*\(\s*(inout|in|out)\s*\)", attribute)
        dimension_match = re.match(r"(?i)dimension\s*\((.*)\)", attribute)
        if intent_match:
            intent = intent_match.group(1).lower()
        if dimension_match:
            dimension_attribute = dimension_match.group(1).strip()

    records = []
    for raw in _split_top_level(variable_text):
        variable = raw.strip()
        initializer = None
        pointer_initializer = None
        if "=>" in variable:
            variable, pointer_initializer = (part.strip() for part in variable.split("=>", 1))
        elif "=" in variable:
            variable, initializer = (part.strip() for part in variable.split("=", 1))
        name_match = re.match(rf"(?i)^\s*({_NAME})\s*(?:\((.*)\))?", variable)
        if not name_match:
            continue
        records.append(
            {
                "scope_id": scope,
                "name": name_match.group(1),
                "type_spec": re.sub(r"\s+", " ", match.group("type").strip()),
                "attributes": attributes,
                "intent": intent,
                "dimensions": name_match.group(2) or dimension_attribute,
                "initializer": initializer,
                "pointer_initializer": pointer_initializer,
                "condition": statement.condition,
                "span": _span(relative, statement),
            }
        )
    return records


def extract_fortran_file(path: Path, wrf_root: Path) -> dict:
    relative = path.relative_to(wrf_root).as_posix()
    modules: list[dict] = []
    procedures: list[dict] = []
    declarations: list[dict] = []
    imports: list[dict] = []
    calls: list[dict] = []
    module_stack: list[dict] = []
    procedure_stack: list[dict] = []
    interface_depth = 0
    runtime_stack: list[dict] = []

    for statement in logical_statements(path):
        lower = statement.text.lower().strip()
        if re.match(r"^abstract\s+interface\b|^interface\b", lower):
            interface_depth += 1
            continue
        if re.match(r"^end\s*interface\b", lower):
            interface_depth = max(0, interface_depth - 1)
            continue
        if interface_depth:
            continue

        if _runtime_control(statement, runtime_stack, relative):
            continue

        if _END_PROCEDURE.match(statement.text) and procedure_stack:
            current = procedure_stack.pop()
            current["end_line"] = statement.end_line
            continue
        if _END_MODULE.match(statement.text) and module_stack:
            current = module_stack.pop()
            current["end_line"] = statement.end_line
            continue

        module_match = _MODULE_START.match(statement.text)
        if module_match and not re.match(r"(?i)^\s*module\s+(procedure|subroutine|function)\b", statement.text):
            name = module_match.group(1)
            record = {
                "id": f"module:{relative}:{name.lower()}",
                "name": name,
                "parent_module_id": module_stack[-1]["id"] if module_stack else None,
                "condition": statement.condition,
                "span": _span(relative, statement),
                "end_line": None,
            }
            modules.append(record)
            module_stack.append(record)
            continue

        procedure_match = _PROCEDURE_START.match(statement.text)
        if procedure_match and not re.match(r"(?i)^\s*end\b", statement.text):
            name = procedure_match.group("name")
            variant_arguments = _argument_records(procedure_match.group("args"))
            if procedure_stack and procedure_stack[-1]["name"].lower() == name.lower():
                current = procedure_stack[-1]
                current["signature_variants"].append(
                    {
                        "arguments": variant_arguments,
                        "condition": statement.condition,
                        "span": _span(relative, statement),
                    }
                )
                known = {argument["name"].lower() for argument in current["arguments"]}
                for argument in variant_arguments:
                    if argument["name"].lower() not in known:
                        current["arguments"].append(
                            {"position": len(current["arguments"]) + 1, "name": argument["name"]}
                        )
                        known.add(argument["name"].lower())
                continue
            parent = procedure_stack[-1]["id"] if procedure_stack else None
            module_id = module_stack[-1]["id"] if module_stack else None
            record = {
                "id": f"procedure:{relative}:{module_id or 'file'}:{parent or 'root'}:{name.lower()}:{statement.start_line}",
                "name": name,
                "kind": procedure_match.group("kind").lower(),
                "prefix": re.sub(r"\s+", " ", procedure_match.group("prefix").strip()) or None,
                "module_id": module_id,
                "parent_procedure_id": parent,
                "arguments": variant_arguments,
                "signature_variants": [
                    {
                        "arguments": variant_arguments,
                        "condition": statement.condition,
                        "span": _span(relative, statement),
                    }
                ],
                "result_name": procedure_match.group("result"),
                "condition": statement.condition,
                "span": _span(relative, statement),
                "end_line": None,
            }
            procedures.append(record)
            procedure_stack.append(record)
            continue

        scope = _scope_id(relative, module_stack, procedure_stack)
        imported = _parse_import(statement, relative, scope)
        if imported:
            imports.append(imported)
            continue
        called = _parse_call(statement, relative, scope)
        if called:
            called["runtime_conditions"] = [dict(condition) for condition in runtime_stack]
            calls.append(called)
            continue
        declarations.extend(_parse_declaration(statement, relative, scope))

    return {
        "path": relative,
        "sha256": sha256_file(path),
        "modules": modules,
        "procedures": procedures,
        "declarations": declarations,
        "imports": imports,
        "calls": calls,
    }


def create_fortran_index(wrf_root: Path, inventory: dict | None = None) -> dict:
    wrf_root = wrf_root.resolve()
    if inventory is None:
        try:
            tracked = run(("git", "ls-files", "--recurse-submodules"), cwd=wrf_root)
            candidates = [wrf_root / relative for relative in tracked.splitlines() if relative]
            source_selection = "git_tracked_recursive"
        except Exception:
            candidates = [path for path in wrf_root.rglob("*") if path.is_file()]
            source_selection = "filesystem_fallback"
    else:
        candidates = [wrf_root / record["path"] for record in inventory.get("files", [])]
        source_selection = "provided_inventory"
    paths = sorted(
        {path for path in candidates if path.is_file() and path.suffix.lower() in FORTRAN_SUFFIXES},
        key=lambda path: path.relative_to(wrf_root).as_posix(),
    )
    files = [extract_fortran_file(path, wrf_root) for path in paths]
    totals = Counter({key: 0 for key in ("modules", "procedures", "declarations", "imports", "calls")})
    for record in files:
        for key in ("modules", "procedures", "declarations", "imports", "calls"):
            totals[key] += len(record[key])
    return {
        "schema_version": 1,
        "extractor": {
            "name": "wrf_source_tools.lexical_fortran",
            "method": "deterministic_logical_statement_analysis",
            "limitations": [
                "Direct CALL statements only; function references and resolved call targets are not yet inferred.",
                "CPP conditions are preserved as text but not evaluated.",
                "Arguments spanning CPP branches are recorded as one ordered syntactic union; per-argument conditions are not yet represented.",
                "Interface bodies are excluded from implementation procedures.",
                "This is a lexical structural extractor, not a complete Fortran semantic parser.",
            ],
        },
        "scope": "provided_inventory" if inventory is not None else "wrf_tree",
        "source_selection": source_selection,
        "file_count": len(files),
        "totals": dict(sorted(totals.items())),
        "files": files,
    }
