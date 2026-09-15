from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import __version__
from .adapter import load_adapter
from .graph import FortranResolver, call_enabled, map_arguments


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _span(path: str, line: int, text: str, source_hash: str) -> dict:
    return {"path": path, "start_line": line, "end_line": line, "text": text.rstrip(), "source_sha256": source_hash}


def _line_evidence(wrf_root: Path, relative: str, pattern: re.Pattern) -> list[dict]:
    path = wrf_root / relative
    if not path.is_file():
        return []
    digest = _sha256(path)
    return [
        _span(relative, number, line, digest)
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if pattern.search(line)
    ]


def _registry_package(wrf_root: Path, spec: dict, component: dict) -> dict:
    relative = component["metadata"]["files"][0]
    pattern = re.compile(
        rf"(?i)^\s*package\s+{re.escape(spec['package'])}\s+{re.escape(spec['selector'])}\s*==\s*{re.escape(spec['value'])}\b"
    )
    evidence = _line_evidence(wrf_root, relative, pattern)
    return {"status": "found" if evidence else "not_found", "name": spec["package"], "evidence": evidence}


def _constant(wrf_root: Path, spec: dict, component: dict) -> dict:
    relative = component["scheme_constants"]["file"]
    pattern = re.compile(
        rf"(?i)^\s*integer\s*,\s*parameter\s*::\s*{re.escape(spec['symbol'])}\s*=\s*{re.escape(spec['value'])}\b"
    )
    evidence = _line_evidence(wrf_root, relative, pattern)
    return {"status": "found" if evidence else "not_found", "symbol": spec["symbol"], "value": spec["value"], "evidence": evidence}


def _namelist_option(wrf_root: Path, spec: dict, component: dict) -> dict:
    relative = component["documentation"]["files"][0]
    lines = (wrf_root / relative).read_text(errors="replace").splitlines()
    digest = _sha256(wrf_root / relative)
    selector = component["selector"]["name"]
    start = next((i for i, line in enumerate(lines) if re.match(rf"\s*{re.escape(selector)}\b", line)), None)
    evidence = []
    if start is not None:
        pattern = re.compile(rf"^\s*=\s*{re.escape(spec['value'])}\s*,")
        next_option = re.compile(r"^\s*[a-z][a-z0-9_]*\s*(?:\([^)]*\))?\s+")
        for index in range(start, min(start + 30, len(lines))):
            if index > start and next_option.match(lines[index]):
                break
            if pattern.search(lines[index]):
                evidence.append(_span(relative, index + 1, lines[index], digest))
    return {"status": "found" if evidence else "not_found", "evidence": evidence}


def _procedure_lookup(index: dict) -> tuple[dict[str, tuple[dict, dict]], dict[str, dict]]:
    procedures = {}
    nodes = {}
    for file in index.get("files", []):
        for procedure in file.get("procedures", []):
            procedures[procedure["id"]] = (procedure, file)
            nodes[procedure["name"].lower()] = procedure
    return procedures, nodes


def _interface(procedure: dict, file: dict) -> dict:
    declarations = {
        item["name"].lower(): item
        for item in file.get("declarations", [])
        if item.get("scope_id") == procedure["id"]
    }
    arguments = []
    for argument in procedure.get("arguments", []):
        declaration = declarations.get(argument["name"].lower(), {})
        arguments.append(
            {
                **argument,
                "type_spec": declaration.get("type_spec"),
                "intent": declaration.get("intent"),
                "dimensions": declaration.get("dimensions"),
                "attributes": declaration.get("attributes", []),
                "declaration_span": declaration.get("span"),
            }
        )
    return {"argument_count": len(arguments), "arguments": arguments, "procedure_span": procedure["span"]}


def _reference(index: dict, wrf_root: Path, key: str, component: dict) -> dict:
    spec = {**component["references"][key], "selector": component["selector"]["name"]}
    resolver = FortranResolver(index)
    driver = resolver.find_procedure(component["driver"]["procedure"], component["driver"]["file"])
    configuration = {spec["selector"]: spec["value"], spec["symbol"]: spec["value"]}
    target_names = set(spec["entry_targets"])
    driver_calls = []
    primary_procedure = None
    primary_file = None
    for call in resolver.calls_by_scope.get(driver["id"], []):
        if call["target"].lower() not in target_names:
            continue
        enabled, reason = call_enabled(call, configuration)
        resolution = resolver.resolve_call(driver, call)
        record = {
            "target": call["target"],
            "call_span": call["span"],
            "runtime_conditions": call.get("runtime_conditions", []),
            "configuration_status": enabled,
            "configuration_reason": reason,
            "resolution_status": resolution["status"],
            "resolution_method": resolution["method"],
            "confidence": resolution.get("confidence"),
        }
        if resolution["status"] == "resolved":
            callee = resolution["procedure"]
            record["callee_id"] = callee["id"]
            record["argument_mappings"] = map_arguments(call, callee)
            if callee["name"].lower() == spec["primary_entry"]:
                primary_procedure = callee
                primary_file = next(file for file in index["files"] if file["path"] == callee["file"])
        driver_calls.append(record)

    imports = []
    resolved_modules = {
        resolver.procedures[item["callee_id"]].get("module_name", "").lower()
        for item in driver_calls
        if item.get("callee_id")
    }
    driver_file = next(file for file in index["files"] if file["path"] == driver["file"])
    for use in driver_file.get("imports", []):
        if use.get("scope_id") == driver["id"] and use["module"].lower() in resolved_modules:
            imports.append(use)

    initialization = []
    for target in spec["initialization_targets"]:
        for file in index.get("files", []):
            for call in file.get("calls", []):
                if call["target"].lower() != target:
                    continue
                caller = resolver.procedures.get(call.get("scope_id"))
                if caller:
                    initialization.append({"target": target, "caller_id": caller["id"], "caller": caller["name"], "call_span": call["span"]})

    compatibility = [
        evidence
        for relative in component.get("compatibility", {}).get("files", [])
        for evidence in _line_evidence(wrf_root, relative, re.compile(rf"(?i)\b{re.escape(spec['symbol'])}\b"))
    ]
    return {
        "key": key,
        "display_name": spec["display_name"],
        "selector": {"name": spec["selector"], "value": spec["value"]},
        "symbolic_constant": _constant(wrf_root, spec, component),
        "registry_package": _registry_package(wrf_root, spec, component),
        "namelist_option": _namelist_option(wrf_root, spec, component),
        "driver": {
            "procedure_id": driver["id"],
            "span": driver["span"],
            "imports": imports,
            "entry_calls": driver_calls,
        },
        "primary_entry": spec["primary_entry"],
        "primary_interface": _interface(primary_procedure, primary_file) if primary_procedure else None,
        "initialization": {
            "configured_targets": spec["initialization_targets"],
            "calls": initialization,
            "status": "found" if initialization else ("not_applicable" if not spec["initialization_targets"] else "not_found"),
        },
        "compatibility_check_candidates": compatibility,
    }


def _compare_interfaces(left: dict, right: dict) -> dict:
    left_args = left["primary_interface"]["arguments"]
    right_args = right["primary_interface"]["arguments"]
    left_by_name = {item["name"].lower(): item for item in left_args}
    right_by_name = {item["name"].lower(): item for item in right_args}
    common = sorted(left_by_name.keys() & right_by_name.keys())
    return {
        "left": left["key"],
        "right": right["key"],
        "common_argument_count": len(common),
        "only_left": [item["name"] for item in left_args if item["name"].lower() not in right_by_name],
        "only_right": [item["name"] for item in right_args if item["name"].lower() not in left_by_name],
        "position_differences": [
            {
                "name": left_by_name[name]["name"],
                "left_position": left_by_name[name]["position"],
                "right_position": right_by_name[name]["position"],
            }
            for name in common
            if left_by_name[name]["position"] != right_by_name[name]["position"]
        ],
        "intent_differences": [
            {"name": name, "left_intent": left_by_name[name]["intent"], "right_intent": right_by_name[name]["intent"]}
            for name in common
            if left_by_name[name]["intent"] != right_by_name[name]["intent"]
        ],
    }


def create_surface_implementation_template(
    index: dict,
    wrf_root: Path,
    references: list[str],
    new_name: str | None = None,
    adapter: dict | None = None,
    component_name: str = "surface-layer",
) -> dict:
    adapter = adapter or load_adapter()
    if component_name not in adapter.get("components", {}):
        raise ValueError(f"component {component_name!r} is not declared by adapter")
    component = adapter["components"][component_name]
    catalogue = component.get("references", {})
    normalized = [name.lower() for name in references]
    unknown = [name for name in normalized if name not in catalogue]
    if unknown:
        raise ValueError(f"unknown surface reference: {', '.join(unknown)}")
    if len(normalized) < 1:
        raise ValueError("at least one reference scheme is required")
    results = [_reference(index, wrf_root.resolve(), name, component) for name in normalized]
    comparisons = [_compare_interfaces(results[0], item) for item in results[1:]]
    identity = {
        "references": normalized,
        "new_name": new_name,
        "adapter_sha256": adapter.get("adapter_sha256"),
        "source_files": sorted({file["path"]: file.get("sha256") for file in index.get("files", [])}.items()),
    }
    return {
        "schema_version": 1,
        "generator_version": __version__,
        "template_type": "framework_component_reference_template",
        "template_id": "template-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16],
        "software": adapter.get("software", {}),
        "adapter_sha256": adapter.get("adapter_sha256"),
        "component": component_name,
        "new_scheme_name": new_name,
        "references": results,
        "interface_comparisons": comparisons,
        "implementation_chain": [
            {"step": position, **item} for position, item in enumerate(component.get("integration_steps", []), 1)
        ],
        "limitations": [
            "The checklist identifies integration locations but does not invent scientific equations or compatibility rules.",
            "Compatibility occurrences are candidates requiring human interpretation of their surrounding logic.",
            "A direct call and INTENT metadata do not prove all runtime dataflow or mutations.",
        ],
    }


def template_to_markdown(template: dict) -> str:
    software = template.get("software", {}).get("display_name", "Model framework")
    lines = [f"# {software} {template['component']} implementation reference", ""]
    if template.get("new_scheme_name"):
        lines.extend([f"Proposed scheme: `{template['new_scheme_name']}`", ""])
    lines.extend([f"Template ID: `{template['template_id']}`", "", "## Reference schemes", ""])
    for ref in template["references"]:
        interface = ref["primary_interface"] or {"argument_count": "unresolved"}
        lines.extend(
            [
                f"### {ref['display_name']}", "",
                f"- Selector: `{ref['selector']['name']}={ref['selector']['value']}`",
                f"- Symbol: `{ref['symbolic_constant']['symbol']}={ref['symbolic_constant']['value']}`",
                f"- Registry package: `{ref['registry_package']['name']}` ({ref['registry_package']['status']})",
                f"- Primary entry: `{ref['primary_entry']}` ({interface['argument_count']} arguments)",
                f"- Driver entry calls: {len(ref['driver']['entry_calls'])}",
                f"- Initialization: `{ref['initialization']['status']}`",
                f"- Compatibility-check candidates: {len(ref['compatibility_check_candidates'])}", "",
            ]
        )
    if template["interface_comparisons"]:
        lines.extend(["## Interface comparison", ""])
        for comparison in template["interface_comparisons"]:
            lines.extend(
                [
                    f"- `{comparison['left']}` versus `{comparison['right']}`: {comparison['common_argument_count']} common arguments",
                    f"- Only `{comparison['left']}`: {', '.join(comparison['only_left']) or 'none'}",
                    f"- Only `{comparison['right']}`: {', '.join(comparison['only_right']) or 'none'}",
                    f"- Position differences: {len(comparison['position_differences'])}",
                    f"- INTENT differences: {len(comparison['intent_differences'])}", "",
                ]
            )
    lines.extend(["## Implementation chain", ""])
    for item in template["implementation_chain"]:
        lines.append(f"{item['step']}. **{item['location']}** — {item['requirement']}")
    lines.extend(["", "## Safety limitations", ""] + [f"- {item}" for item in template["limitations"]])
    return "\n".join(lines) + "\n"
