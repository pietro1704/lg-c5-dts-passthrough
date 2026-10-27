# SPDX-License-Identifier: GPL-2.0-only
"""Pure EDID transformation; preserves unrelated audio and video data."""
import re

def parse_dump(text):
    rows=[]
    for line in text.splitlines():
        values=re.findall(r'\[0x([0-9a-fA-F]{2})\]',line)
        if len(values)==17:rows.extend(int(value,16) for value in values[1:])
    data=bytes(rows);at=data.find(bytes.fromhex('00ffffffffffff00'))
    if at<0 or len(data)<at+512:raise ValueError('missing full 512-byte EDID')
    raw=data[at:at+512];validate(raw);return raw

def validate(raw):
    if len(raw)!=512 or raw[:8]!=bytes.fromhex('00ffffffffffff00') or raw[126]>3:
        raise ValueError('wrong EDID identity/length/extension count')
    for offset in range(0,512,128):
        if sum(raw[offset:offset+128])%256:raise ValueError('bad EDID checksum')

def data_blocks(block):
    end=block[2] or 4
    if not 4<=end<=127:raise ValueError('invalid CTA DTD boundary')
    result=[];position=4
    while position<end:
        length=block[position]&31
        if not length or position+length+1>end:raise ValueError('invalid CTA data block')
        result.append(block[position:position+length+1]);position+=length+1
    return result

def advertised_formats(raw):
    validate(raw);formats=[]
    for number in range(1,raw[126]+1):
        block=raw[number*128:(number+1)*128]
        if block[0]!=2:continue
        for entry in data_blocks(block):
            if entry[0]>>5==1:
                if (len(entry)-1)%3:raise ValueError('invalid audio descriptor length')
                formats.extend((entry[i]>>3)&15 for i in range(1,len(entry),3))
    return formats

def add_receiver_dts(raw,sad):
    validate(raw)
    if len(sad)!=3 or (sad[0]>>3)&15!=7:raise ValueError('only receiver-proven DTS core SAD accepted')
    if 7 in advertised_formats(raw):return raw
    candidate=bytearray(raw)
    for number in range(1,raw[126]+1):
        start=number*128;block=raw[start:start+128]
        if block[0]!=2:continue
        entries=data_blocks(block);audio=[i for i,entry in enumerate(entries) if entry[0]>>5==1]
        if len(audio)!=1:continue
        index=audio[0];entry=entries[index]
        if len(entry)-1+3>31:raise ValueError('audio data block full')
        entries[index]=bytes([(entry[0]&224)|((entry[0]&31)+3)])+entry[1:]+sad
        dbc=b''.join(entries);dtd_offset=block[2] or 4;tail=block[dtd_offset:127]
        last_nonzero=max((i+1 for i,value in enumerate(tail) if value),default=0)
        used=((last_nonzero+17)//18)*18
        if used>len(tail) or 4+len(dbc)+used>127:raise ValueError('no safe CTA padding space')
        changed=bytearray(128);changed[:4]=block[:4];changed[2]=4+len(dbc)
        changed[4:4+len(dbc)]=dbc
        changed[changed[2]:changed[2]+used]=tail[:used]
        changed[127]=(-sum(changed[:127]))&255
        candidate[start:start+128]=changed;result=bytes(candidate);validate(result)
        if advertised_formats(result)!=advertised_formats(raw)+[7]:
            # Existing order is retained; new SAD appended to the sole ADB.
            old=advertised_formats(raw);new=advertised_formats(result)
            if sorted(new)!=sorted(old+[7]):raise ValueError('unrelated audio descriptor changed')
        return result
    raise ValueError('no unique existing CTA audio block')

def wire_text(raw):
    validate(raw)
    text=''.join('0x%02x '%value for value in raw)
    assert len(text)==2560 and re.fullmatch(r'(?:0x[0-9a-f]{2} ){512}',text)
    return text.encode('ascii')


def supported_sad(capabilities):
    """Advertise only the core 5.1/48kHz transport implemented by this version."""
    for entry in capabilities:
        if entry.get('audioCodingType')!='DTS':continue
        try:sad=bytes.fromhex(entry['hexSAD'])
        except (KeyError,ValueError,TypeError):continue
        if len(sad)==3 and (sad[0]>>3)&15==7 and (sad[0]&7)+1>=6 and sad[1]&4 and sad[2]>=192:
            return bytes([0x3d,0x04,0xc0])
    raise ValueError('receiver does not advertise the supported DTS core transport')


def constrain_dts(raw,sad):
    if 7 not in advertised_formats(raw):return add_receiver_dts(raw,sad)
    candidate=bytearray(raw);locations=[]
    for number in range(1,raw[126]+1):
        start=number*128;block=raw[start:start+128]
        if block[0]!=2:continue
        position=4
        for entry in data_blocks(block):
            if entry[0]>>5==1:
                for i in range(1,len(entry),3):
                    if (entry[i]>>3)&15==7:locations.append((start,position+i,entry[i:i+3]))
            position+=len(entry)
    if len(locations)!=1:raise ValueError('ambiguous existing DTS descriptor')
    start,position,old=locations[0]
    if (old[0]&7)+1<6 or not old[1]&4 or old[2]<192:
        raise ValueError('existing DTS descriptor lacks the supported transport')
    candidate[start+position:start+position+3]=sad
    candidate[start+127]=(-sum(candidate[start:start+127]))&255
    result=bytes(candidate);validate(result);return result
