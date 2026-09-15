import tempfile
import unittest
from pathlib import Path

from wrf_source_tools.manifest import create_manifest


class ManifestTests(unittest.TestCase):
    def test_requires_recognizable_wrf_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                create_manifest(Path(directory))

    def test_reads_compiled_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main").mkdir()
            (root / "Registry").mkdir()
            build = root / "build-manifest.txt"
            build.write_text("wrf_version=4.7.1\nparallelism=dmpar\n")
            result = create_manifest(root, build)
            self.assertEqual(result["compiled_build"]["wrf_version"], "4.7.1")
            self.assertTrue(result["policy"]["source_read_only"])


if __name__ == "__main__":
    unittest.main()
