import tempfile
import unittest
from pathlib import Path

from wrf_source_tools.inventory import create_pbl_inventory


class InventoryTests(unittest.TestCase):
    def test_discovers_content_and_path_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "phys").mkdir()
            (root / "Registry").mkdir()
            (root / "phys" / "module_bl_example.F").write_text(
                "subroutine pbl_driver(pblh)\nreal :: pblh\nend\n"
            )
            (root / "phys" / "surface_tools").write_text("first\nsecond\n")
            (root / "Registry" / "Registry.EM_COMMON").write_text(
                "rconfig integer bl_pbl_physics namelist,physics 1 0 -\n"
            )
            (root / "Registry" / "registry.unrelated").write_text(
                "state real unrelated ij misc 1 - - description units\n"
            )
            (root / "unrelated.F").write_text("subroutine unrelated\nend\n")

            result = create_pbl_inventory(root)
            paths = {record["path"] for record in result["files"]}
            self.assertIn("phys/module_bl_example.F", paths)
            self.assertIn("Registry/Registry.EM_COMMON", paths)
            surface_record = next(
                record for record in result["files"] if record["path"] == "phys/surface_tools"
            )
            self.assertEqual(surface_record["line_count"], 2)
            self.assertNotIn("Registry/registry.unrelated", paths)
            self.assertNotIn("unrelated.F", paths)


if __name__ == "__main__":
    unittest.main()
