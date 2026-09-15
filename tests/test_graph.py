import tempfile
import unittest
from pathlib import Path

from wrf_source_tools.fortran import create_fortran_index
from wrf_source_tools.graph import (
    create_scheme_graph,
    expand_index_for_graph,
    graph_to_dot,
    graph_to_svg,
)


class SchemeGraphTests(unittest.TestCase):
    def test_isolates_scheme_and_maps_variable_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physics = root / "phys"
            physics.mkdir()
            (physics / "module_pbl_driver.F").write_text(
                "module module_pbl_driver\n"
                "use module_mynn\n"
                "use module_ysu\n"
                "contains\n"
                "subroutine pbl_driver(qke)\n"
                "real, intent(inout) :: qke\n"
                "call mynn_entry(qke)\n"
                "call ysu(qke)\n"
                "end subroutine pbl_driver\n"
                "end module module_pbl_driver\n"
            )
            (physics / "module_mynn.F90").write_text(
                "module module_mynn\n"
                "contains\n"
                "subroutine mynn_entry(tke)\n"
                "real, intent(inout) :: tke\n"
                "call helper(tke)\n"
                "end subroutine mynn_entry\n"
                "subroutine helper(value)\n"
                "real, intent(inout) :: value\n"
                "end subroutine helper\n"
                "end module module_mynn\n"
            )
            (physics / "module_ysu.F90").write_text(
                "module module_ysu\ncontains\nsubroutine ysu(value)\nreal :: value\nend subroutine ysu\nend module module_ysu\n"
            )
            inventory = {
                "files": [
                    {"path": "phys/module_pbl_driver.F"},
                    {"path": "phys/module_mynn.F90"},
                    {"path": "phys/module_ysu.F90"},
                ]
            }
            index = create_fortran_index(root, inventory)
            graph = create_scheme_graph(index, "MYNN", max_depth=5, variable="qke")

            names = {node["name"].lower() for node in graph["nodes"]}
            self.assertEqual(names, {"pbl_driver", "mynn_entry", "helper"})
            self.assertNotIn("ysu", names)
            self.assertEqual(graph["node_count"], 3)
            self.assertEqual(graph["edge_count"], 2)
            first_edge = next(edge for edge in graph["edges"] if edge["target_syntax"] == "mynn_entry")
            self.assertEqual(first_edge["argument_mappings"][0]["caller_expression"], "qke")
            self.assertEqual(first_edge["argument_mappings"][0]["callee_dummy"], "tke")
            self.assertTrue(first_edge["variable_mappings"])
            self.assertIn("digraph scheme_graph", graph_to_dot(graph))
            self.assertIn("<svg", graph_to_svg(graph))

    def test_explicit_entries_support_scheme_names_that_differ_from_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physics = root / "dyn_em"
            physics.mkdir()
            (physics / "module_step.F90").write_text(
                "module module_step\ncontains\n"
                "subroutine timestep(tke)\nreal :: tke\n"
                "call nonlocal_flux(tke)\ncall unrelated(tke)\n"
                "end subroutine timestep\n"
                "subroutine nonlocal_flux(value)\nreal :: value\nend subroutine nonlocal_flux\n"
                "subroutine unrelated(value)\nreal :: value\nend subroutine unrelated\n"
                "end module module_step\n"
            )
            inventory = {"files": [{"path": "dyn_em/module_step.F90"}]}
            index = create_fortran_index(root, inventory)
            graph = create_scheme_graph(
                index,
                "SMS-3DTKE",
                root_name="timestep",
                root_path="dyn_em/module_step.F90",
                entry_targets=["nonlocal_flux"],
            )
            self.assertEqual({node["name"] for node in graph["nodes"]}, {"timestep", "nonlocal_flux"})
            self.assertEqual(graph["entry_targets"], ["nonlocal_flux"])

    def test_configuration_excludes_other_select_case_branches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module_options.F90"
            source.write_text(
                "module module_options\ncontains\n"
                "subroutine root_step()\ncall dispatcher()\nend subroutine root_step\n"
                "subroutine dispatcher()\nselect case(config_flags%km_opt)\n"
                "case (5)\ncall sms_path()\ncase (1)\ncall alternative_path()\nend select\n"
                "end subroutine dispatcher\n"
                "subroutine sms_path()\nend subroutine sms_path\n"
                "subroutine alternative_path()\nend subroutine alternative_path\n"
                "end module module_options\n"
            )
            index = create_fortran_index(root, {"files": [{"path": "module_options.F90"}]})
            graph = create_scheme_graph(
                index,
                "SMS-3DTKE",
                root_name="root_step",
                root_path="module_options.F90",
                entry_targets=["dispatcher"],
                configuration={"km_opt": "5"},
            )
            names = {node["name"] for node in graph["nodes"]}
            self.assertIn("sms_path", names)
            self.assertNotIn("alternative_path", names)
            self.assertTrue(any(item["target"] == "alternative_path" for item in graph["excluded_calls"]))
            guarded = next(edge for edge in graph["edges"] if edge["target_syntax"] == "sms_path")
            self.assertEqual(guarded["configuration_predicates"], ["km_opt=5"])
            self.assertIn("Configuration: km_opt=5", graph_to_dot(graph))
            self.assertIn("Configuration: km_opt=5", graph_to_svg(graph))

    def test_configuration_resolves_symbolic_case_constants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module_wind.F90"
            source.write_text(
                "module module_wind\ncontains\n"
                "subroutine root_step()\nselect case(windfarm_opt)\n"
                "case (fitchscheme)\ncall dragforce()\n"
                "case (mavscheme)\ncall dragforce_mav()\nend select\n"
                "end subroutine root_step\n"
                "subroutine dragforce()\nend subroutine dragforce\n"
                "subroutine dragforce_mav()\nend subroutine dragforce_mav\n"
                "end module module_wind\n"
            )
            index = create_fortran_index(root, {"files": [{"path": "module_wind.F90"}]})
            graph = create_scheme_graph(
                index,
                "FITCH",
                root_name="root_step",
                root_path="module_wind.F90",
                entry_targets=["dragforce"],
                configuration={"windfarm_opt": "1", "fitchscheme": "1", "mavscheme": "2"},
            )
            self.assertEqual({node["name"] for node in graph["nodes"]}, {"root_step", "dragforce"})

    def test_expands_missing_imported_boundary_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module_main.F90").write_text(
                "module module_main\nuse module_boundary\ncontains\n"
                "subroutine root_step()\ncall dispatcher()\nend subroutine root_step\n"
                "subroutine dispatcher()\ncall boundary_service()\nend subroutine dispatcher\n"
                "end module module_main\n"
            )
            (root / "module_boundary.F90").write_text(
                "module module_boundary\ncontains\nsubroutine boundary_service()\n"
                "end subroutine boundary_service\nend module module_boundary\n"
            )
            index = create_fortran_index(root, {"files": [{"path": "module_main.F90"}]})
            initial = create_scheme_graph(
                index, "TEST", root_name="root_step", root_path="module_main.F90", entry_targets=["dispatcher"]
            )
            self.assertEqual(initial["unresolved_call_count"], 1)
            expanded, boundary_files = expand_index_for_graph(index, root, initial)
            final = create_scheme_graph(
                expanded,
                "TEST",
                root_name="root_step",
                root_path="module_main.F90",
                entry_targets=["dispatcher"],
                stop_files=boundary_files,
            )
            self.assertEqual(final["unresolved_call_count"], 0)
            self.assertIn("module_boundary.F90", boundary_files)


if __name__ == "__main__":
    unittest.main()
