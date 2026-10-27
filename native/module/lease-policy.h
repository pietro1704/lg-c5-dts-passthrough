/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef C5_LEASE_POLICY_H
#define C5_LEASE_POLICY_H
static inline unsigned int c5_next_id(unsigned int previous) {
 return previous==~0u?0:previous+1;
}
static inline unsigned long c5_cookie(unsigned int slot,unsigned int id) {
 return id && slot<4?((unsigned long)id<<8)|(slot+1):0;
}
static inline unsigned int c5_cookie_slot(unsigned long cookie) {
 unsigned int slot=(cookie&255)-1;
 return slot<4 && (cookie>>8)!=0?slot:4;
}
static inline int c5_cookie_matches(unsigned long cookie,unsigned int id) {
 return c5_cookie_slot(cookie)<4 && id && (cookie>>8)==id;
}
static inline int c5_scope_matches(unsigned int source,unsigned int port,
 unsigned int index,int active,int verified,int live,int es) {
 return source==1 && port<=1 && index==port && active && verified && live && es;
}
#endif
