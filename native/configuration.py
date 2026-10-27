# SPDX-License-Identifier: GPL-2.0-only
import configparser
import io

COMPAT='# Legacy Nuvio probe: avdec_dca=290. DTS is supplied by dtspassthrough; avdec_dca stays disabled.\n'

def native_configuration(original):
    parser=configparser.ConfigParser(interpolation=None,strict=True)
    parser.optionxform=str
    parser.read_string(original)
    if not parser.has_section('sw_decoder'):
        raise ValueError('missing native software-decoder policy section')
    for section in parser.sections():
        if parser.has_option(section,'avdec_dca'):parser.set(section,'avdec_dca','0')
    parser.set('sw_decoder','dtspassthrough','310')
    parser.set('sw_decoder','avdec_dca','0')
    output=io.StringIO();parser.write(output,space_around_delimiters=False)
    return COMPAT+output.getvalue()
