import configparser,unittest
from configuration import native_configuration
SOURCE='[rank]\nac3_audiodec=290\n[sw_decoder]\navdec_dca=290\navdec_ac3=0\n[default_sink]\naudio=audiosink\n[downmix]\ncenter=0.75\n'
class Configuration(unittest.TestCase):
    def test_original_decoder_disabled_and_passthrough_priority(self):
        output=native_configuration(SOURCE)
        parsed=configparser.ConfigParser();parsed.read_string(output)
        self.assertEqual(parsed['sw_decoder']['avdec_dca'],'0')
        self.assertEqual(parsed['sw_decoder']['dtspassthrough'],'310')
    def test_exact_legacy_probe_without_enabling_decoder(self):
        output=native_configuration(SOURCE)
        self.assertIn('avdec_dca=290',output)
        self.assertTrue(all(not line.startswith('avdec_dca=290') for line in output.splitlines()))
    def test_unrelated_dolby_and_sink_policy_preserved(self):
        parsed=configparser.ConfigParser();parsed.read_string(native_configuration(SOURCE))
        self.assertEqual(parsed['rank']['ac3_audiodec'],'290')
        self.assertEqual(parsed['default_sink']['audio'],'audiosink')
        self.assertEqual(parsed['downmix']['center'],'0.75')
    def test_idempotence(self):
        first=native_configuration(SOURCE);self.assertEqual(first,native_configuration(first))
    def test_missing_or_ambiguous_policy_fails(self):
        for source in ('[rank]\ncodec=1\n',SOURCE+'[sw_decoder]\ncodec=2\n'):
            with self.assertRaises((ValueError,configparser.Error)):native_configuration(source)
if __name__=='__main__':unittest.main()
