import tempfile
import unittest
from pathlib import Path

from wrf_source_tools.equations import create_equation_evidence, parse_expression
from wrf_source_tools.fortran import create_fortran_index
from wrf_source_tools.graph import create_scheme_graph


class EquationEvidenceTests(unittest.TestCase):
    def test_parses_operator_precedence_and_references(self):
        tree, status = parse_expression("old + dt * max(source, 0.0)")
        self.assertEqual(status, "parsed")
        self.assertEqual(tree["operator"], "+")
        self.assertEqual(tree["right"]["operator"], "*")
        self.assertEqual(tree["right"]["right"]["name"].lower(), "max")

    def test_extracts_scoped_backward_assignment_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scheme.F90"
            source.write_text(
                "module scheme\ncontains\n"
                "subroutine driver(tke,tendency)\nreal::tke,tendency\ncall step(tke,tendency)\nend subroutine\n"
                "subroutine step(tke,tendency)\nreal,intent(in)::tke\nreal,intent(inout)::tendency\nreal::tmp\ninteger::i\n"
                "tmp=max(tke,0.0)\ndo i=1,2\nif (tmp.gt.0.0) tendency=tendency+tmp\nend do\nend subroutine\nend module\n"
            )
            index = create_fortran_index(root, {"files": [{"path": "scheme.F90"}]})
            graph = create_scheme_graph(index, "TEST", root_name="driver", root_path="scheme.F90", entry_targets=["step"])
            first = create_equation_evidence(index, graph, root, ["tke", "tendency"])
            second = create_equation_evidence(index, graph, root, ["tendency", "tke"])
            self.assertEqual(first, second)
            self.assertEqual(first["assignment_count"], 2)
            targets = {item["target"]["base_name"] for item in first["assignments"]}
            self.assertEqual(targets, {"tmp", "tendency"})
            tendency = next(item for item in first["assignments"] if item["target"]["base_name"] == "tendency")
            self.assertEqual(tendency["assignment_class"], "self_update")
            self.assertEqual(tendency["target_declaration"]["intent"], "inout")
            self.assertTrue(tendency["evidence_id"].startswith("eq-"))
            self.assertEqual(tendency["runtime_conditions"][0]["kind"], "inline_if")
            self.assertEqual(tendency["loops"][0]["variable"].lower(), "i")
            self.assertEqual(first["unparsed_assignment_count"], 0)


if __name__ == "__main__":
    unittest.main()
