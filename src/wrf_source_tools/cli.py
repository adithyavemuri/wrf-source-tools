from __future__ import annotations

import argparse
from pathlib import Path

import json

from .adapter import load_adapter
from .equations import create_equation_evidence, equation_evidence_to_markdown
from .fortran import create_fortran_index
from .graph import create_scheme_graph, expand_index_for_graph, write_scheme_graph_outputs
from .integration import create_surface_implementation_template, template_to_markdown
from .inventory import create_pbl_inventory
from .manifest import create_manifest
from .util import write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wrf-source")
    subcommands = parser.add_subparsers(dest="command", required=True)

    bootstrap = subcommands.add_parser("bootstrap", help="Create manifest and PBL inventory")
    bootstrap.add_argument("--wrf", type=Path, required=True)
    bootstrap.add_argument("--build-manifest", type=Path)
    bootstrap.add_argument("--output", type=Path, default=Path("generated"))

    manifest = subcommands.add_parser("manifest", help="Record WRF source/build identity")
    manifest.add_argument("--wrf", type=Path, required=True)
    manifest.add_argument("--build-manifest", type=Path)
    manifest.add_argument("--output", type=Path, required=True)

    inventory = subcommands.add_parser("inventory", help="Create targeted PBL inventory")
    inventory.add_argument("--wrf", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)

    extract = subcommands.add_parser("extract", help="Extract deterministic Fortran structure")
    extract.add_argument("--wrf", type=Path, required=True)
    extract.add_argument("--inventory", type=Path)
    extract.add_argument("--output", type=Path, required=True)

    graph = subcommands.add_parser("graph", help="Create a scheme-isolated direct-call graph")
    graph.add_argument("--index", type=Path, required=True)
    graph.add_argument("--scheme", required=True)
    graph.add_argument("--root", default="pbl_driver")
    graph.add_argument("--root-path", default="phys/module_pbl_driver.F")
    graph.add_argument("--max-depth", type=int, default=8)
    graph.add_argument("--variable")
    graph.add_argument("--set", action="append", dest="configuration", metavar="NAME=VALUE")
    graph.add_argument("--wrf", type=Path, help="WRF root used for dependency expansion")
    graph.add_argument("--expand-dependencies", action="store_true")
    graph.add_argument(
        "--entry",
        action="append",
        dest="entry_targets",
        help="Exact root-call target to include; repeat for schemes with multiple entry calls",
    )
    graph.add_argument("--output-prefix", type=Path, required=True)

    template = subcommands.add_parser("implementation-template", help="Compare adapter-defined reference-scheme integration")
    template.add_argument("--index", type=Path, required=True)
    template.add_argument("--source-root", "--wrf", dest="source_root", type=Path, required=True)
    template.add_argument("--adapter", type=Path)
    template.add_argument("--component", required=True)
    template.add_argument("--reference", action="append", required=True)
    template.add_argument("--new-name")
    template.add_argument("--output-prefix", type=Path, required=True)

    equations = subcommands.add_parser("equations", help="Extract scoped assignments and expression evidence")
    equations.add_argument("--index", type=Path, required=True)
    equations.add_argument("--graph", type=Path, required=True)
    equations.add_argument("--source-root", type=Path, required=True)
    equations.add_argument("--variable", action="append", required=True)
    equations.add_argument("--procedure", action="append", help="Restrict to named graph procedures; repeat as needed")
    equations.add_argument("--output-prefix", type=Path, required=True)

    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command in {"bootstrap", "manifest"}:
        destination = arguments.output / "wrf-manifest.json" if arguments.command == "bootstrap" else arguments.output
        manifest = create_manifest(arguments.wrf, arguments.build_manifest)
        write_json(destination, manifest)
        print(f"Wrote {destination}")

    if arguments.command in {"bootstrap", "inventory"}:
        destination = arguments.output / "pbl-inventory.json" if arguments.command == "bootstrap" else arguments.output
        inventory = create_pbl_inventory(arguments.wrf)
        write_json(destination, inventory)
        print(f"Wrote {destination} ({inventory['file_count']} files)")

        if arguments.command == "bootstrap":
            destination = arguments.output / "fortran-index.json"
            index = create_fortran_index(arguments.wrf, inventory)
            write_json(destination, index)
            print(
                f"Wrote {destination} ({index['file_count']} Fortran files, "
                f"{index['totals']['procedures']} procedures)"
            )

    if arguments.command == "extract":
        inventory_data = None
        if arguments.inventory:
            inventory_data = json.loads(arguments.inventory.read_text())
        index = create_fortran_index(arguments.wrf, inventory_data)
        write_json(arguments.output, index)
        print(
            f"Wrote {arguments.output} ({index['file_count']} Fortran files, "
            f"{index['totals']['procedures']} procedures)"
        )

    if arguments.command == "graph":
        index = json.loads(arguments.index.read_text())
        configuration = {}
        for setting in arguments.configuration or []:
            if "=" not in setting:
                raise ValueError(f"Invalid --set value {setting!r}; expected NAME=VALUE")
            name, value = setting.split("=", 1)
            configuration[name.strip()] = value.strip()
        graph_data = create_scheme_graph(
            index,
            scheme=arguments.scheme,
            root_name=arguments.root,
            root_path=arguments.root_path,
            max_depth=arguments.max_depth,
            variable=arguments.variable,
            entry_targets=arguments.entry_targets,
            configuration=configuration,
        )
        if arguments.expand_dependencies:
            if arguments.wrf is None:
                raise ValueError("--expand-dependencies requires --wrf")
            expanded_index = index
            expanded_files: set[str] = set()
            for _ in range(5):
                expanded_index, new_files = expand_index_for_graph(expanded_index, arguments.wrf, graph_data)
                if not new_files:
                    break
                expanded_files.update(new_files)
                graph_data = create_scheme_graph(
                    expanded_index,
                    scheme=arguments.scheme,
                    root_name=arguments.root,
                    root_path=arguments.root_path,
                    max_depth=arguments.max_depth,
                    variable=arguments.variable,
                    entry_targets=arguments.entry_targets,
                    configuration=configuration,
                    stop_files=expanded_files,
                )
        paths = write_scheme_graph_outputs(graph_data, arguments.output_prefix, write_json)
        print(
            f"Wrote {paths['json']} and {paths['svg']} "
            f"({graph_data['node_count']} nodes, {graph_data['edge_count']} edges)"
        )

    if arguments.command == "implementation-template":
        index = json.loads(arguments.index.read_text())
        adapter = load_adapter(arguments.adapter)
        template_data = create_surface_implementation_template(
            index,
            arguments.source_root,
            references=arguments.reference,
            new_name=arguments.new_name,
            adapter=adapter,
            component_name=arguments.component,
        )
        json_path = arguments.output_prefix.with_suffix(".json")
        markdown_path = arguments.output_prefix.with_suffix(".md")
        write_json(json_path, template_data)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(template_to_markdown(template_data))
        print(f"Wrote {json_path} and {markdown_path} ({len(template_data['references'])} references)")

    if arguments.command == "equations":
        index = json.loads(arguments.index.read_text())
        graph_data = json.loads(arguments.graph.read_text())
        evidence = create_equation_evidence(
            index, graph_data, arguments.source_root, arguments.variable, procedure_names=arguments.procedure
        )
        json_path = arguments.output_prefix.with_suffix(".json")
        markdown_path = arguments.output_prefix.with_suffix(".md")
        write_json(json_path, evidence)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(equation_evidence_to_markdown(evidence))
        print(
            f"Wrote {json_path} and {markdown_path} "
            f"({evidence['assignment_count']} assignments, {evidence['unparsed_assignment_count']} unparsed)"
        )
