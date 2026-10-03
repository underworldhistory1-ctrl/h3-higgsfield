import importlib.util,pathlib,tempfile,unittest,subprocess,shutil
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class V2PromotionInstallTests(unittest.TestCase):
 def test_runtime_is_copied_and_existing_data_preserved(self):
  installer=load('v2_windows_installer','deploy/install_windows.py')
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);target=root/'custom_nodes/h3_studio'
   installer.copy_runtime(root)
   for name in ('h3_continuation.py','h3_queue_bridge.py','web/lab-ui.js','web/h3/prompt-compiler.js',*[p.relative_to(ROOT).as_posix() for p in (ROOT/'h3_lab').glob('*.py')]):self.assertTrue((target/name).is_file(),name)
   (target/'web/lab-ui.js').write_text('old lab ui')
   output=root/'output/lab_storage';output.mkdir(parents=True);(output/'project.json').write_text('keep project')
   installer.copy_runtime(root)
   self.assertEqual((output/'project.json').read_text(),'keep project')
   backups=list((target/'.h3-backup').glob('*/web/lab-ui.js'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'old lab ui')
 def test_duplicate_v2_copy_is_refused(self):
  installer=load('v2_windows_installer2','deploy/install_windows.py')
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/'custom_nodes/h3_studio_v2').mkdir(parents=True)
   with self.assertRaisesRegex(RuntimeError,'duplicate H3'):installer.copy_runtime(root)
 def test_packager_contains_runtime_and_pinned_context_dependency(self):
  package=load('v2_package','deploy/build_final_folder.py');installer=load('v2_installer3','deploy/install_windows.py')
  self.assertTrue(set(installer.RUNTIME_FILES).issubset(package.SOURCE_FILES))
  for name in package.SOURCE_FILES:self.assertTrue((ROOT/name).is_file(),name)
  pin='361624fb406b63eb6694442eac6c895fc1533a70'
  for name in ('deploy/bootstrap_h3_server.sh','deploy/install_windows.py','Dockerfile.salad'):self.assertIn(pin,(ROOT/name).read_text())
 def test_linux_inventory_handles_windows_line_endings(self):
  bash=shutil.which('bash') or ('C:/Program Files/Git/bin/bash.exe' if pathlib.Path('C:/Program Files/Git/bin/bash.exe').is_file() else None)
  if not bash:self.skipTest('Bash unavailable')
  script=(ROOT/'deploy/bootstrap_h3_server.sh').read_text()
  snippet=script[script.index('mapfile -t V2_RUNTIME_FILES'):script.index('RUNTIME_FILES+=("${V2_RUNTIME_FILES[@]}")')]
  snippet=snippet.replace(' < "$PROJECT_ROOT/deploy/v2_runtime_files.txt"','')+'printf "%s\\n" "${V2_RUNTIME_FILES[@]}"'
  result=subprocess.run([bash,'-c',snippet],input=b'h3_continuation.py\r\nweb/lab-ui.js\r\n',capture_output=True,check=True)
  self.assertEqual(result.stdout.decode().splitlines(),['h3_continuation.py','web/lab-ui.js'])
 def test_verifier_rejects_disabled_inference_and_missing_context(self):
  verifier=load('v2_verify','deploy/verify_h3_server.py')
  good={'ready':True,'guides_ready':True,'continuation_ready':True,'add_guide':True,'continuation_nodes':dict.fromkeys(('MiniMaxH3GeneratedAVMaskedContext','MiniMaxH3ExistingVideoMaskedContext','H3LabLoadContext','H3LabTrimAV'),True)}
  self.assertEqual(verifier.v2_problems(good),[])
  self.assertTrue(verifier.v2_problems(dict(good,inference_enabled=False)))
  self.assertTrue(verifier.v2_problems(dict(good,continuation_nodes={})))
if __name__=='__main__':unittest.main()
