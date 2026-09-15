import tempfile
import unittest
from pathlib import Path

from wrf_source_tools.fortran import create_fortran_index
from wrf_source_tools.integration import create_surface_implementation_template, template_to_markdown


class SurfaceImplementationTemplateTests(unittest.TestCase):
    def test_compares_driver_interfaces_and_integration_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("phys", "frame", "Registry", "run", "share"):
                (root / folder).mkdir()
            (root / "phys/module_surface_driver.F").write_text(
                "module module_surface_driver\nuse module_sf_mynn\nuse module_sf_sfclayrev\ncontains\n"
                "subroutine surface_driver(sf_sfclay_physics,a,b,c)\ninteger :: sf_sfclay_physics\nreal :: a,b,c\n"
                "select case(sf_sfclay_physics)\ncase(mynnsfcscheme)\ncall sfclay_mynn(a,b)\n"
                "case(sfclayrevscheme)\ncall sfclayrev(a,c)\nend select\nend subroutine surface_driver\nend module\n"
            )
            (root / "phys/module_sf_mynn.F").write_text(
                "module module_sf_mynn\ncontains\nsubroutine sfclay_mynn(a,b)\nreal,intent(in)::a\nreal,intent(out)::b\n"
                "end subroutine\nsubroutine mynn_sf_init_driver(flag)\nlogical::flag\nend subroutine\nend module\n"
            )
            (root / "phys/module_sf_sfclayrev.F").write_text(
                "module module_sf_sfclayrev\ncontains\nsubroutine sfclayrev(a,c)\nreal,intent(in)::a\nreal,intent(inout)::c\n"
                "end subroutine\nend module\n"
            )
            (root / "phys/module_physics_init.F").write_text(
                "subroutine phy_init(flag)\nuse module_sf_mynn\nlogical::flag\ncall mynn_sf_init_driver(flag)\nend subroutine\n"
            )
            (root / "frame/module_state_description.F").write_text(
                "INTEGER, PARAMETER :: sfclayrevscheme = 1\nINTEGER, PARAMETER :: mynnsfcscheme = 5\n"
            )
            (root / "Registry/Registry.EM_COMMON").write_text(
                "package sfclayrevscheme sf_sfclay_physics==1 - -\npackage mynnsfcscheme sf_sfclay_physics==5 - -\n"
            )
            (root / "run/README.namelist").write_text(
                "sf_sfclay_physics surface layer\n = 1, Revised MM5\n = 5, MYNN surface layer\n"
            )
            (root / "share/module_check_a_mundo.F").write_text(
                "if (x .ne. sfclayrevscheme) stop\nif (x .ne. mynnsfcscheme) stop\n"
            )
            files = [
                "phys/module_surface_driver.F", "phys/module_sf_mynn.F", "phys/module_sf_sfclayrev.F",
                "phys/module_physics_init.F",
            ]
            index = create_fortran_index(root, {"files": [{"path": item} for item in files]})
            result = create_surface_implementation_template(index, root, ["mynn", "revised-mm5"], "new_surface")

            self.assertEqual(result["new_scheme_name"], "new_surface")
            self.assertEqual([item["primary_interface"]["argument_count"] for item in result["references"]], [2, 2])
            self.assertEqual(result["references"][0]["initialization"]["status"], "found")
            self.assertEqual(result["references"][1]["initialization"]["status"], "not_applicable")
            self.assertEqual([len(item["namelist_option"]["evidence"]) for item in result["references"]], [1, 1])
            self.assertEqual(result["interface_comparisons"][0]["only_left"], ["b"])
            self.assertEqual(result["interface_comparisons"][0]["only_right"], ["c"])
            self.assertIn("Implementation chain", template_to_markdown(result))


if __name__ == "__main__":
    unittest.main()
