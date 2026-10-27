#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include "module/lease-policy.h"
int main(void) {
 unsigned int port,other;
 assert(c5_next_id(0)==1);
 assert(c5_next_id(UINT_MAX-1)==UINT_MAX);
 assert(c5_next_id(UINT_MAX)==0);
 for(port=0;port<2;port++) {
  other=1-port;
  assert(c5_scope_matches(1,port,port,1,1,1,1));
  assert(!c5_scope_matches(1,port,other,1,1,1,1));
  assert(!c5_scope_matches(0,port,port,1,1,1,1));
  assert(!c5_scope_matches(1,port,port,0,1,1,1));
  assert(!c5_scope_matches(1,port,port,1,0,1,1));
  assert(!c5_scope_matches(1,port,port,1,1,0,1));
  assert(!c5_scope_matches(1,port,port,1,1,1,0));
 }
 assert(!c5_scope_matches(1,2,2,1,1,1,1));
 for(port=0;port<4;port++) {
  unsigned long old=c5_cookie(port,100),fresh=c5_cookie(port,101);
  assert(c5_cookie_slot(old)==port);
  assert(c5_cookie_matches(old,100));
  assert(!c5_cookie_matches(old,101));
  assert(!c5_cookie_matches(fresh,100));
  assert(c5_cookie_matches(fresh,101));
 }
 assert(c5_cookie(4,1)==0);
 assert(c5_cookie(0,0)==0);
 assert(c5_cookie_slot(0)==4);
 assert(!c5_cookie_matches(0,0));
 puts("PASS 42 assertions: native ports 0/1, wrong source/identity, revoke generation, exhaustion");
}
