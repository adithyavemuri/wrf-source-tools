from __future__ import annotations

import html
import re
import subprocess
from collections import defaultdict, deque
from copy import deepcopy
from pathlib import Path
from typing import Any

from .fortran import extract_fortran_file


def _lower(value: str | None) -> str:
    return (value or "").lower()


def _procedure_name(procedure: dict) -> str:
    module = procedure.get("module_name")
    return f"{module}::{procedure['name']}" if module else procedure["name"]


class FortranResolver:
    """Resolve direct CALL names using lexical module and USE evidence."""

    def __init__(self, index: dict):
        self.index = index
        self.procedures: dict[str, dict] = {}
        self.calls_by_scope: dict[str, list[dict]] = defaultdict(list)
        self.imports_by_scope: dict[str, list[dict]] = defaultdict(list)
        self.modules_by_id: dict[str, dict] = {}
        self.module_procedures: dict[str, list[dict]] = defaultdict(list)
        self.name_procedures: dict[str, list[dict]] = defaultdict(list)

        for file in index.get("files", []):
            module_names = {module["id"]: module["name"] for module in file.get("modules", [])}
            for module in file.get("modules", []):
                enriched = dict(module)
                enriched["file"] = file["path"]
                self.modules_by_id[module["id"]] = enriched
            for procedure in file.get("procedures", []):
                enriched = dict(procedure)
                enriched["file"] = file["path"]
                enriched["module_name"] = module_names.get(procedure.get("module_id"))
                self.procedures[procedure["id"]] = enriched
                self.name_procedures[_lower(procedure["name"])].append(enriched)
                if enriched.get("module_name"):
                    self.module_procedures[_lower(enriched["module_name"])].append(enriched)
            for call in file.get("calls", []):
                self.calls_by_scope[call["scope_id"]].append(call)
            for imported in file.get("imports", []):
                self.imports_by_scope[imported["scope_id"]].append(imported)

    @staticmethod
    def _wrf_preference(candidates: list[dict]) -> list[dict]:
        if len(candidates) <= 1:
            return candidates
        without_mpas = [item for item in candidates if "/mpas/" not in item["file"].lower()]
        if without_mpas:
            candidates = without_mpas
        without_misc = [item for item in candidates if "/misc/" not in item["file"].lower()]
        return without_misc or candidates

    def find_procedure(self, name: str, preferred_path: str | None = None) -> dict:
        candidates = list(self.name_procedures.get(name.lower(), []))
        if preferred_path:
            preferred = [item for item in candidates if item["file"] == preferred_path]
            if preferred:
                candidates = preferred
        candidates = self._wrf_preference(candidates)
        if len(candidates) != 1:
            paths = ", ".join(item["file"] for item in candidates[:8])
            raise ValueError(f"Procedure {name!r} is not unique ({len(candidates)} candidates): {paths}")
        return candidates[0]

    def _applicable_imports(self, caller: dict) -> list[dict]:
        scope_ids = [caller["id"]]
        if caller.get("module_id"):
            scope_ids.append(caller["module_id"])
        scope_ids.append(f"file:{caller['file']}")
        return [item for scope in scope_ids for item in self.imports_by_scope.get(scope, [])]

    def resolve_call(self, caller: dict, call: dict) -> dict:
        target = _lower(call["target"])
        if "%" in target:
            return {"status": "unresolved", "method": "type_bound_not_resolved", "candidates": []}

        same_module = [
            item
            for item in self.name_procedures.get(target, [])
            if caller.get("module_id") and item.get("module_id") == caller.get("module_id")
        ]
        same_module = self._wrf_preference(same_module)
        if len(same_module) == 1:
            return {"status": "resolved", "method": "same_module", "confidence": "high", "procedure": same_module[0]}

        imported_candidates: list[dict] = []
        import_evidence = []
        missing_import_modules = []
        for imported in self._applicable_imports(caller):
            source_name = target
            if imported.get("only"):
                match = next(
                    (item for item in imported.get("items", []) if _lower(item.get("local_name")) == target),
                    None,
                )
                if not match:
                    continue
                source_name = _lower(match.get("source_name"))
            matches = [
                item
                for item in self.module_procedures.get(_lower(imported.get("module")), [])
                if _lower(item["name"]) == source_name
            ]
            if matches:
                imported_candidates.extend(matches)
                import_evidence.append(imported)
            elif _lower(imported.get("module")) not in self.module_procedures:
                missing_import_modules.append(imported.get("module"))
        imported_candidates = self._wrf_preference(_unique_procedures(imported_candidates))
        if len(imported_candidates) == 1:
            return {
                "status": "resolved",
                "method": "use_association",
                "confidence": "high",
                "procedure": imported_candidates[0],
                "import_spans": [item["span"] for item in import_evidence],
            }
        if imported_candidates:
            return {
                "status": "ambiguous",
                "method": "use_association",
                "candidates": [item["id"] for item in imported_candidates],
            }

        global_candidates = self._wrf_preference(list(self.name_procedures.get(target, [])))
        if len(global_candidates) == 1:
            return {
                "status": "resolved",
                "method": "unique_global_name",
                "confidence": "medium",
                "procedure": global_candidates[0],
            }
        return {
            "status": "ambiguous" if global_candidates else "unresolved",
            "method": "global_name_lookup",
            "candidates": [item["id"] for item in global_candidates],
            "missing_import_modules": sorted(set(missing_import_modules)),
        }


def _unique_procedures(procedures: list[dict]) -> list[dict]:
    return list({item["id"]: item for item in procedures}.values())


def map_arguments(call: dict, callee: dict) -> list[dict]:
    dummy_arguments = callee.get("arguments", [])
    by_name = {_lower(item["name"]): item for item in dummy_arguments}
    mappings = []
    next_position = 1
    for actual in call.get("arguments", []):
        keyword = _lower(actual.get("keyword"))
        dummy = by_name.get(keyword) if keyword else None
        if dummy is None and not keyword:
            dummy = next((item for item in dummy_arguments if item["position"] == next_position), None)
            next_position += 1
        mappings.append(
            {
                "actual_position": actual["position"],
                "caller_expression": actual["expression"],
                "keyword": actual.get("keyword"),
                "dummy_position": dummy.get("position") if dummy else None,
                "callee_dummy": dummy.get("name") if dummy else None,
                "status": "mapped" if dummy else "unmapped",
            }
        )
    return mappings


def _node(procedure: dict, role: str, depth: int) -> dict:
    return {
        "id": procedure["id"],
        "name": procedure["name"],
        "qualified_name": _procedure_name(procedure),
        "module": procedure.get("module_name"),
        "role": role,
        "depth": depth,
        "span": procedure["span"],
    }


def _strip_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        closes_at_end = True
        for index, char in enumerate(result):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(result) - 1:
                    closes_at_end = False
                    break
        if not closes_at_end or depth != 0:
            break
        result = result[1:-1].strip()
    return result


def _split_logical(expression: str, operator: str) -> list[str]:
    pieces: list[str] = []
    depth = 0
    start = 0
    lower = expression.lower()
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and lower.startswith(operator, index):
            pieces.append(expression[start:index].strip())
            index += len(operator)
            start = index
            continue
        index += 1
    if pieces:
        pieces.append(expression[start:].strip())
    return pieces


def _config_key(selector: str) -> str:
    value = re.sub(r"\([^)]*\)$", "", selector.strip().lower())
    return value.split("%")[-1]


def evaluate_condition(expression: str, configuration: dict[str, str]) -> bool | None:
    expression = _strip_outer_parentheses(expression)
    parts = _split_logical(expression, ".or.")
    if parts:
        values = [evaluate_condition(part, configuration) for part in parts]
        if any(value is True for value in values):
            return True
        return False if all(value is False for value in values) else None
    parts = _split_logical(expression, ".and.")
    if parts:
        values = [evaluate_condition(part, configuration) for part in parts]
        if any(value is False for value in values):
            return False
        return True if all(value is True for value in values) else None
    negated = re.match(r"(?i)^\.not\.\s*(.*)$", expression)
    if negated:
        value = evaluate_condition(negated.group(1), configuration)
        return None if value is None else not value
    comparison = re.match(
        r"(?i)^\s*([a-z_][a-z0-9_%]*(?:\([^)]*\))?)\s*"
        r"(\.eq\.|==|\.ne\.|/=|<=|>=|<|>)\s*([a-z0-9_.+-]+)\s*$",
        expression,
    )
    if not comparison:
        return None
    key = _config_key(comparison.group(1))
    if key not in configuration:
        return None
    actual, expected = configuration[key], comparison.group(3).lower()
    operator = comparison.group(2).lower()
    if operator in {".eq.", "=="}:
        return actual == expected
    if operator in {".ne.", "/="}:
        return actual != expected
    try:
        left, right = float(actual), float(expected)
    except ValueError:
        return None
    return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[operator]


def call_enabled(call: dict, configuration: dict[str, str]) -> tuple[bool | None, str]:
    if not configuration:
        return None, "no runtime configuration supplied"
    unknown = False
    for condition in call.get("runtime_conditions", []):
        if condition.get("kind") == "case":
            key = _config_key(condition.get("selector", ""))
            if key not in configuration or condition.get("default"):
                unknown = True
                continue
            values = {
                configuration.get(_lower(value), _lower(value))
                for value in condition.get("values", [])
            }
            if configuration[key] not in values:
                return False, f"{key}={configuration[key]} not in CASE({','.join(condition.get('values', []))})"
        elif condition.get("kind") == "if":
            enabled = evaluate_condition(condition.get("expression", ""), configuration)
            if enabled is False:
                return False, f"condition is false: {condition.get('expression')}"
            if enabled is None:
                unknown = True
    return (None if unknown else True), ("some predicates unresolved" if unknown else "all predicates satisfied")


def create_scheme_graph(
    index: dict,
    scheme: str,
    root_name: str = "pbl_driver",
    root_path: str = "phys/module_pbl_driver.F",
    max_depth: int = 8,
    variable: str | None = None,
    entry_targets: list[str] | None = None,
    configuration: dict[str, str] | None = None,
    stop_files: set[str] | None = None,
) -> dict:
    resolver = FortranResolver(index)
    root = resolver.find_procedure(root_name, root_path)
    scheme_lower = scheme.lower()
    aliases = {
        "mynn": ("mynnedmf_driver", "mynn"),
        "ysu": ("ysu",),
        "myj": ("myjpbl", "myj"),
    }.get(scheme_lower, (scheme_lower,))
    configuration = {_lower(key): str(value).lower() for key, value in (configuration or {}).items()}
    stop_files = stop_files or set()

    normalized_entries = {_lower(target) for target in entry_targets or []}
    if normalized_entries:
        entry_calls = [
            call
            for call in resolver.calls_by_scope.get(root["id"], [])
            if _lower(call["target"]) in normalized_entries
        ]
    else:
        entry_calls = [
            call
            for call in resolver.calls_by_scope.get(root["id"], [])
            if any(alias in _lower(call["target"]) for alias in aliases)
        ]
    excluded_calls = []
    active_entry_calls = []
    for call in entry_calls:
        enabled, reason = call_enabled(call, configuration)
        if enabled is False:
            excluded_calls.append(
                {"caller_id": root["id"], "target": call["target"], "call_span": call["span"], "reason": reason}
            )
        else:
            active_entry_calls.append(call)

    resolved_entries = []
    unresolved_entries = []
    for call in active_entry_calls:
        resolution = resolver.resolve_call(root, call)
        if resolution["status"] == "resolved":
            resolved_entries.append((call, resolution))
        else:
            unresolved_entries.append({"call": call, "resolution": resolution})
    if not resolved_entries:
        raise ValueError(f"No resolved {scheme} entry call found in {root_name}")

    nodes = {root["id"]: _node(root, "root", 0)}
    edges: list[dict] = []
    unresolved: list[dict] = list(unresolved_entries)
    queue: deque[tuple[dict, int]] = deque()
    visited_depth: dict[str, int] = {root["id"]: 0}

    for call, resolution in resolved_entries:
        callee = resolution["procedure"]
        nodes[callee["id"]] = _node(callee, "scheme_entry", 1)
        edges.append(_edge(root, callee, call, resolution, variable, configuration))
        if callee["file"] not in stop_files:
            queue.append((callee, 1))
        visited_depth[callee["id"]] = 1

    while queue:
        caller, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for call in resolver.calls_by_scope.get(caller["id"], []):
            enabled, reason = call_enabled(call, configuration)
            if enabled is False:
                excluded_calls.append(
                    {"caller_id": caller["id"], "target": call["target"], "call_span": call["span"], "reason": reason}
                )
                continue
            resolution = resolver.resolve_call(caller, call)
            if resolution["status"] != "resolved":
                unresolved.append(
                    {
                        "caller_id": caller["id"],
                        "target": call["target"],
                        "call_span": call["span"],
                        "status": resolution["status"],
                        "method": resolution["method"],
                        "candidates": resolution.get("candidates", []),
                        "missing_import_modules": resolution.get("missing_import_modules", []),
                    }
                )
                continue
            callee = resolution["procedure"]
            if callee["id"] not in nodes:
                nodes[callee["id"]] = _node(callee, "dependency", depth + 1)
            edges.append(_edge(caller, callee, call, resolution, variable, configuration))
            prior = visited_depth.get(callee["id"])
            if callee["file"] not in stop_files and (prior is None or depth + 1 < prior):
                visited_depth[callee["id"]] = depth + 1
                queue.append((callee, depth + 1))

    edge_keys = set()
    unique_edges = []
    for edge in edges:
        key = (edge["caller_id"], edge["callee_id"], edge["call_span"]["start_line"])
        if key not in edge_keys:
            edge_keys.add(key)
            unique_edges.append(edge)

    result = {
        "schema_version": 1,
        "graph_type": "scheme_isolated_direct_call_graph",
        "scheme": scheme.upper(),
        "root": root["id"],
        "entry_targets": sorted(normalized_entries) if normalized_entries else None,
        "isolation_rule": (
            "Start at explicitly selected root calls; traverse only their resolved descendants."
            if normalized_entries
            else "Start at scheme-matching calls in the root; traverse only their resolved descendants."
        ),
        "max_depth": max_depth,
        "variable_filter": variable,
        "configuration": dict(sorted(configuration.items())),
        "expanded_boundary_files": sorted(stop_files),
        "node_count": len(nodes),
        "edge_count": len(unique_edges),
        "unresolved_call_count": len(unresolved),
        "nodes": sorted(nodes.values(), key=lambda item: (item["depth"], item["qualified_name"].lower(), item["id"])),
        "edges": unique_edges,
        "unresolved_calls": unresolved,
        "excluded_calls": excluded_calls,
        "limitations": [
            "Edges represent resolved direct CALL statements, not function references or runtime procedure pointers.",
            "Unique-global-name resolution is marked medium confidence; module-local and USE-associated resolution is high confidence.",
            "CPP conditions are retained on call evidence but not evaluated.",
            "Runtime IF and SELECT CASE predicates are filtered only when the supplied configuration evaluates them conclusively.",
        ],
    }
    return result


def _condition_label(condition: dict, configuration: dict[str, str]) -> str:
    if condition.get("kind") == "case":
        selector = _config_key(condition.get("selector", ""))
        if selector not in configuration or condition.get("default"):
            return ""
        values = condition.get("values", [])
        resolved = [configuration.get(_lower(value), value) for value in values]
        if configuration[selector] in resolved:
            return f"{selector}={configuration[selector]}"
        return ""
    if condition.get("kind") == "if":
        expression = condition.get("expression", "")
        referenced = [
            key
            for key in sorted(configuration)
            if re.search(rf"(?i)(?<![a-z0-9_]){re.escape(key)}(?![a-z0-9_])", expression)
        ]
        if referenced and evaluate_condition(expression, configuration) is not None:
            return ",".join(f"{key}={configuration[key]}" for key in referenced)
    return ""


def _edge(
    caller: dict,
    callee: dict,
    call: dict,
    resolution: dict,
    variable: str | None,
    configuration: dict[str, str],
) -> dict:
    mappings = map_arguments(call, callee)
    variable_mappings = []
    if variable:
        pattern = re.compile(rf"(?i)(?<![a-z0-9_]){re.escape(variable)}(?![a-z0-9_])")
        variable_mappings = [item for item in mappings if pattern.search(item["caller_expression"])]
    runtime_conditions = call.get("runtime_conditions", [])
    predicate_labels = [
        label for label in (_condition_label(condition, configuration) for condition in runtime_conditions) if label
    ]
    predicate_labels = list(dict.fromkeys(predicate_labels))
    return {
        "caller_id": caller["id"],
        "callee_id": callee["id"],
        "target_syntax": call["target"],
        "resolution_method": resolution["method"],
        "confidence": resolution.get("confidence"),
        "condition": call.get("condition"),
        "runtime_conditions": runtime_conditions,
        "configuration_predicates": predicate_labels,
        "call_span": call["span"],
        "argument_mappings": mappings,
        "variable_mappings": variable_mappings,
    }


def _rg_files(wrf_root: Path, pattern: str) -> list[Path]:
    command = [
        "rg", "-l", "-i",
        "--glob", "*.F", "--glob", "*.F90", "--glob", "*.f", "--glob", "*.f90",
        pattern, str(wrf_root),
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return [Path(line) for line in completed.stdout.splitlines() if line]


def expand_index_for_graph(index: dict, wrf_root: Path, graph: dict) -> tuple[dict, set[str]]:
    """Add source files that define unresolved calls at the selected graph boundary."""
    wrf_root = wrf_root.resolve()
    existing = {file["path"] for file in index.get("files", [])}
    additions: list[Path] = []
    for unresolved in graph.get("unresolved_calls", []):
        missing_modules = unresolved.get("missing_import_modules", [])
        for module in missing_modules:
            candidates = _rg_files(wrf_root, rf"^\s*module\s+{re.escape(module)}\s*$")
            if candidates:
                additions.append(sorted(candidates, key=lambda path: (len(path.parts), str(path)))[0])
        target = unresolved.get("target")
        if target:
            pattern = rf"^\s*(?:[a-z][a-z0-9_]*(?:\([^)]*\))?\s+)?subroutine\s+{re.escape(target)}\b"
            candidates = _rg_files(wrf_root, pattern)
            if target.lower().startswith("wrf_"):
                framework = [path for path in candidates if "/frame/" in path.as_posix()]
                candidates = framework or candidates
                non_stubs = [path for path in candidates if "stub" not in path.name.lower()]
                candidates = non_stubs or candidates
            if candidates:
                additions.append(sorted(candidates, key=lambda path: (len(path.parts), str(path)))[0])

    unique = sorted(
        {
            path.resolve()
            for path in additions
            if path.resolve().relative_to(wrf_root).as_posix() not in existing
        },
        key=lambda path: path.relative_to(wrf_root).as_posix(),
    )
    expanded = deepcopy(index)
    for path in unique:
        expanded["files"].append(extract_fortran_file(path, wrf_root))
    expanded["files"].sort(key=lambda item: item["path"])
    return expanded, {path.relative_to(wrf_root).as_posix() for path in unique}


def graph_to_dot(graph: dict) -> str:
    configuration = ", ".join(f"{key}={value}" for key, value in graph.get("configuration", {}).items())
    title = f"{graph['scheme']} isolated direct-call graph"
    if configuration:
        title += f"\\nConfiguration: {configuration}"
    lines = [
        "digraph scheme_graph {",
        '  rankdir="LR";',
        f'  graph [label="{title}", labelloc="t", fontsize=20];',
        '  node [shape="box", style="rounded,filled", fontname="Helvetica", fontsize=10];',
    ]
    for node in graph["nodes"]:
        color = {"root": "#dceeff", "scheme_entry": "#dff4e5"}.get(node["role"], "#f3f5f7")
        label = f"{node['qualified_name']}\\n{node['span']['path']}:{node['span']['start_line']}"
        lines.append(f'  "{node["id"]}" [label="{label}", fillcolor="{color}"];')
    for edge in graph["edges"]:
        mappings = edge.get("variable_mappings", [])
        label = edge["target_syntax"]
        if mappings:
            mapped = ", ".join(f"{item['caller_expression']} -> {item['callee_dummy']}" for item in mappings)
            label += f"\\n{mapped}"
        predicates = edge.get("configuration_predicates", [])
        if predicates:
            label += f"\\n[{'; '.join(predicates)}]"
        lines.append(f'  "{edge["caller_id"]}" -> "{edge["callee_id"]}" [label="{label}", fontsize=8];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def graph_to_svg(graph: dict) -> str:
    """Render a dependency-free, deterministic layered SVG overview."""
    levels: dict[int, list[dict]] = defaultdict(list)
    for node in graph["nodes"]:
        levels[node["depth"]].append(node)
    for nodes in levels.values():
        nodes.sort(key=lambda item: item["qualified_name"].lower())
    node_width, node_height = 250, 58
    x_gap, y_gap = 85, 24
    configuration = ", ".join(f"{key}={value}" for key, value in graph.get("configuration", {}).items())
    margin_x, margin_y = 45, (98 if configuration else 75)
    max_depth = max(levels, default=0)
    max_rows = max((len(nodes) for nodes in levels.values()), default=1)
    width = margin_x * 2 + (max_depth + 1) * node_width + max_depth * x_gap
    height = margin_y + max_rows * (node_height + y_gap) + 45
    positions: dict[str, tuple[float, float]] = {}
    for depth, nodes in levels.items():
        level_height = len(nodes) * (node_height + y_gap) - y_gap
        start_y = margin_y + max(0, (max_rows * (node_height + y_gap) - y_gap - level_height) / 2)
        for row, node in enumerate(nodes):
            positions[node["id"]] = (margin_x + depth * (node_width + x_gap), start_y + row * (node_height + y_gap))

    output = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,sans-serif;fill:#17212b}.title{font-size:22px;font-weight:bold}.configuration{font-size:11px;fill:#38546a}.name{font-size:12px;font-weight:bold}.source{font-size:9px;fill:#52616d}.edge{stroke:#71808c;stroke-width:1.2;fill:none}.edge-label{font-size:8px;fill:#38546a}</style>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#71808c"/></marker></defs>',
        f'<text class="title" x="{margin_x}" y="35">{html.escape(graph["scheme"])} isolated direct-call graph</text>',
    ]
    if configuration:
        output.append(f'<text class="configuration" x="{margin_x}" y="56">Configuration: {html.escape(configuration)}</text>')
    for edge in graph["edges"]:
        if edge["caller_id"] not in positions or edge["callee_id"] not in positions:
            continue
        sx, sy = positions[edge["caller_id"]]
        tx, ty = positions[edge["callee_id"]]
        x1, y1 = sx + node_width, sy + node_height / 2
        x2, y2 = tx, ty + node_height / 2
        mid = (x1 + x2) / 2
        output.append(f'<path class="edge" marker-end="url(#arrow)" d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"/>')
        edge_labels = []
        if edge.get("variable_mappings"):
            edge_labels.append(", ".join(f"{item['caller_expression']}→{item['callee_dummy']}" for item in edge["variable_mappings"][:2]))
        if edge.get("configuration_predicates"):
            edge_labels.append(f"[{'; '.join(edge['configuration_predicates'])}]")
        if edge_labels:
            label = " ".join(edge_labels)
            output.append(f'<text class="edge-label" x="{mid}" y="{(y1+y2)/2-3}" text-anchor="middle">{html.escape(label)}</text>')
    for node in graph["nodes"]:
        x, y = positions[node["id"]]
        fill = {"root": "#dceeff", "scheme_entry": "#dff4e5"}.get(node["role"], "#f3f5f7")
        output.append(f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" rx="7" fill="{fill}" stroke="#8293a1"/>')
        output.append(f'<text class="name" x="{x+9}" y="{y+21}">{html.escape(node["name"])}</text>')
        module = node.get("module") or "external/file procedure"
        output.append(f'<text class="source" x="{x+9}" y="{y+37}">{html.escape(module)}</text>')
        source = f"{node['span']['path']}:{node['span']['start_line']}"
        if len(source) > 43:
            source = "…" + source[-42:]
        output.append(f'<text class="source" x="{x+9}" y="{y+50}">{html.escape(source)}</text>')
    output.append("</svg>")
    return "\n".join(output) + "\n"


def write_scheme_graph_outputs(graph: dict, output_prefix: Path, write_json) -> dict[str, Path]:
    paths = {
        "json": output_prefix.with_suffix(".json"),
        "svg": output_prefix.with_suffix(".svg"),
    }
    write_json(paths["json"], graph)
    paths["svg"].write_text(graph_to_svg(graph))
    return paths
