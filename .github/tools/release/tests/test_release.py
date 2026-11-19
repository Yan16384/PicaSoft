import importlib.util, json, os, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import common
spec = importlib.util.spec_from_file_location("release_plan", TOOLS / "plan.py")
plan = importlib.util.module_from_spec(spec); spec.loader.exec_module(plan)
spec = importlib.util.spec_from_file_location("release_package", TOOLS / "package.py")
pack = importlib.util.module_from_spec(spec); spec.loader.exec_module(pack)

class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root=Path(os.getenv("PICA_TEST_OUTPUT", str(common.ROOT/".release-output/tests"))); cls.temp_root.mkdir(parents=True,exist_ok=True)
    def test_changed_directory_isolated(self):
        self.assertEqual(plan.select("push", "refs/heads/main", "abc", "all", ["Pica软件合集/PicaCrop/PicaCrop.py"]), ["picacrop"])
    def test_multiple_and_chinese_directories(self):
        self.assertEqual(plan.select("push", "refs/heads/main", "abc", "all", ["Pica软件合集/markdown制作器/source/paper_export.py", "Pica软件合集/PicaPhoto/mobile/app.js"]), ["picaphoto", "picamarkdown"])
    def test_common_change_rebuilds_all(self):
        self.assertEqual(len(plan.select("push", "refs/heads/main", "abc", "all", [".github/tools/release/build.py"])), 4)
    def test_docs_do_not_release(self):
        self.assertEqual(plan.select("push", "refs/heads/main", "abc", "all", ["README.md"]), [])
    def test_first_push_and_manual(self):
        self.assertEqual(len(plan.select("push", "refs/heads/main", "0"*40, "all")), 4)
        self.assertEqual(plan.select("workflow_dispatch", "refs/heads/main", "", "picahotkeytool"), ["picahotkeytool"])
    def test_tags_validated(self):
        self.assertEqual(plan.select("push", "refs/tags/picacrop-v2.0.1-beta.1", "", ""), ["picacrop"])
        with self.assertRaises(ValueError): plan.select("push", "refs/tags/picacrop-vbad", "", "")
    def test_registry_has_real_build_inputs(self):
        for cfg in common.projects().values():
            self.assertTrue((common.ROOT/cfg["directory"]/cfg["entry"]).is_file())
            self.assertTrue((common.ROOT/cfg["requirements"]).is_file())
    def test_source_excludes_binaries_and_other_apps(self):
        files = list(pack.source_files("picamarkdown")); names = [n for _,n in files]
        self.assertIn("Pica软件合集/markdown制作器/source/paper_export.py", names)
        self.assertIn(".github/tools/release/build.py", names)
        self.assertFalse(any(n.endswith(".exe") or n.startswith("Pica软件合集/PicaCrop/") for n in names))
    def test_missing_photo_apk_blocks_release(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            output=Path(tmp); (output/"windows").mkdir(); (output/"windows/PicaPhoto.exe").write_bytes(b"MZfixture")
            with self.assertRaisesRegex(RuntimeError, "Android APK"):
                pack.package("picaphoto", output)
    def test_package_includes_dependencies_and_checksum(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            output=Path(tmp); (output/"windows").mkdir(); (output/"windows/PicaCrop.exe").write_bytes(b"MZfixture")
            with patch.dict(os.environ, {"GITHUB_REF":"refs/tags/picacrop-v2.0.1"}, clear=False): pack.package("picacrop", output)
            with zipfile.ZipFile(output/"PicaCrop-source.zip") as z:
                self.assertIn("Pica软件合集/PicaCrop/requirements.txt", z.namelist()); self.assertIn(".github/tools/release/build.py", z.namelist())
            self.assertIn("windows/PicaCrop.exe", (output/"SHA256SUMS.txt").read_text())

if __name__ == "__main__": unittest.main()
