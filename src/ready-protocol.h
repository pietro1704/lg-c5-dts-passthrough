/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef DTS_READY_PROTOCOL_H
#define DTS_READY_PROTOCOL_H
#include <string.h>
#include <limits.h>
struct dts_ready_parser {char line[48];unsigned int used;};
/* 1 = current epoch ready, 0 = incomplete/stale, -1 = malformed/future epoch. */
static inline int dts_ready_byte(struct dts_ready_parser *p,unsigned char byte,
                                unsigned long long expected) {
 unsigned int i;unsigned long long value=0;
 if(!expected)return -1;
 if(byte!='\n') {
  if(byte<32 || byte>126 || p->used>=sizeof(p->line)-1)return -1;
  p->line[p->used++]=byte;return 0;
 }
 if(p->used<7 || memcmp(p->line,"READY ",6) || p->line[6]=='0')return -1;
 for(i=6;i<p->used;i++) {
  unsigned int digit=(unsigned char)p->line[i]-'0';
  if(digit>9 || value>(ULLONG_MAX-digit)/10)return -1;
  value=value*10+digit;
 }
 p->used=0;
 return value>expected?-1:value==expected?1:0;
}
#endif
