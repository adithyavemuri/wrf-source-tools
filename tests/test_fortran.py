import tempfile
import unittest
import subprocess
from pathlib import Path

from wrf_source_tools.fortran import create_fortran_index, extract_fortran_file


SOURCE = """\
module module_demo
  use iso_fortran_env, only: rk => real64, int32
  real, private :: module_value
contains
  subroutine advance(state, tendency, enabled)
    real(kind=rk), intent(inout), dimension(:) :: state
    real(kind=rk), intent(in) :: tendency(:)
    logical, optional, intent(in) :: enabled
    integer :: index = 1
#if ENABLE_HELPER
    call helper(state, scale=tendency(index))
#endif
    if (present(enabled)) then
      call state%update(index)
    end if
  end subroutine advance

  pure function magnitude(value) result(answer)
    real, intent(in) :: value
    real :: answer
    answer = abs(value)
  end function magnitude
end module module_demo
"""


class FortranExtractorTests(unittest.TestCase):
    def test_whole_tree_prefers_git_tracked_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracked = root / "tracked.F90"
            generated = root / "generated.f90"
            tracked.write_text("subroutine tracked\nend subroutine tracked\n")
            generated.write_text("subroutine generated\nend subroutine generated\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "tracked.F90"], check=True)

            result = create_fortran_index(root)

            self.assertEqual(result["source_selection"], "git_tracked_recursive")
            self.assertEqual([item["path"] for item in result["files"]], ["tracked.F90"])

    def test_extracts_scoped_structure_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module_demo.F90"
            source.write_text(SOURCE)
            result = extract_fortran_file(source, root)

            self.assertEqual([item["name"] for item in result["modules"]], ["module_demo"])
            self.assertEqual([item["name"] for item in result["procedures"]], ["advance", "magnitude"])
            advance = result["procedures"][0]
            self.assertEqual([item["name"] for item in advance["arguments"]], ["state", "tendency", "enabled"])
            self.assertEqual(advance["end_line"], 16)

            declarations = {item["name"]: item for item in result["declarations"]}
            self.assertEqual(declarations["state"]["intent"], "inout")
            self.assertEqual(declarations["state"]["dimensions"], ":")
            self.assertEqual(declarations["tendency"]["dimensions"], ":")
            self.assertEqual(declarations["index"]["initializer"], "1")
            self.assertEqual(declarations["answer"]["scope_id"], result["procedures"][1]["id"])

            imported = result["imports"][0]
            self.assertTrue(imported["only"])
            self.assertEqual(imported["items"][0], {"position": 1, "local_name": "rk", "source_name": "real64"})

            self.assertEqual([item["target"] for item in result["calls"]], ["helper", "state%update"])
            self.assertEqual(result["calls"][0]["arguments"][1]["keyword"], "scale")
            self.assertEqual(result["calls"][0]["condition"], "ENABLE_HELPER")
            self.assertEqual(result["calls"][1]["dispatch"], "type_bound")
            self.assertEqual(result["calls"][1]["scope_id"], advance["id"])

    def test_indexes_only_inventory_fortran_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module_demo.F90"
            source.write_text(SOURCE)
            (root / "ignored.F90").write_text("subroutine ignored\nend\n")
            inventory = {"files": [{"path": "module_demo.F90"}]}
            result = create_fortran_index(root, inventory)
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(result["totals"]["procedures"], 2)

    def test_cpp_inside_continued_signature_does_not_split_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "conditional.F90"
            source.write_text(
                "subroutine conditional(first, &\n"
                "#if FEATURE\n"
                "  optional_value, &\n"
                "#endif\n"
                "  last)\n"
                "integer, intent(in) :: first, last\n"
                "end subroutine conditional\n"
            )
            result = extract_fortran_file(source, root)
            arguments = result["procedures"][0]["arguments"]
            self.assertEqual([item["name"] for item in arguments], ["first", "optional_value", "last"])

    def test_cpp_alternate_signatures_are_variants_not_nested_procedures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "variants.F90"
            source.write_text(
                "#if FEATURE\n"
                "subroutine configured(first, extra)\n"
                "#else\n"
                "subroutine configured(first)\n"
                "#endif\n"
                "integer, intent(in) :: first\n"
                "end subroutine configured\n"
            )
            result = extract_fortran_file(source, root)
            self.assertEqual(len(result["procedures"]), 1)
            procedure = result["procedures"][0]
            self.assertEqual(len(procedure["signature_variants"]), 2)
            self.assertEqual([item["name"] for item in procedure["arguments"]], ["first", "extra"])
            self.assertEqual(procedure["end_line"], 7)

    def test_records_runtime_if_and_select_case_conditions_on_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "conditions.F90"
            source.write_text(
                "subroutine choose(config_flags)\n"
                "if (config_flags%diff_opt .eq. 2) then\n"
                "select case(config_flags%km_opt)\n"
                "case (2, 5)\n"
                "call selected()\n"
                "case (1)\n"
                "call alternative()\n"
                "end select\n"
                "end if\n"
                "end subroutine choose\n"
            )
            result = extract_fortran_file(source, root)
            selected = next(call for call in result["calls"] if call["target"] == "selected")
            self.assertEqual([item["kind"] for item in selected["runtime_conditions"]], ["if", "case"])
            self.assertEqual(selected["runtime_conditions"][1]["values"], ["2", "5"])


if __name__ == "__main__":
    unittest.main()
