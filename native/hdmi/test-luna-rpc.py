from pathlib import Path
import importlib.util,json,sys,unittest
spec=importlib.util.spec_from_file_location('rpc',Path(__file__).with_name('luna_rpc.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_child_has_controlling_terminal_even_with_captured_parent_output(self):
        code='import os,json;f=os.open("/dev/tty",os.O_RDWR);os.close(f);print(json.dumps({"tty":os.isatty(0) and os.isatty(1)}))'
        self.assertEqual(json.loads(m.terminal_command([sys.executable,'-c',code])),{'tty':True})
    def test_nonzero_exit_and_timeout_are_never_success(self):
        with self.assertRaises(RuntimeError):m.terminal_command([sys.executable,'-c','raise SystemExit(3)'])
        with self.assertRaises(TimeoutError):m.terminal_command([sys.executable,'-c','import time;time.sleep(5)'],.1)
    def test_unapproved_endpoint_never_starts_a_client(self):
        with self.assertRaises(ValueError):m.luna('luna://unknown/mutate',{})

if __name__=='__main__':unittest.main()
