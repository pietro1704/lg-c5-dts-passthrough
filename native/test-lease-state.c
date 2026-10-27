#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include "module/lease-state.h"
int main(void) {
 struct c5_lease_state s={0};
 assert(!c5_check_lease(&s,1));assert(!c5_resume_lease(&s,1));
 assert(c5_open_lease(&s));assert(s.opened && !s.active);
 assert(!c5_open_lease(&s));assert(!c5_observe_route(&s));
 assert(!c5_resume_lease(&s,0));assert(c5_resume_lease(&s,1));
 assert(c5_check_lease(&s,1));assert(!s.proof_epoch && !s.routes);
 assert(c5_observe_route(&s));assert(s.proof_epoch==1 && s.routes==1);
 c5_suspend_lease(&s);
 assert(s.opened && !s.active);assert(!s.proof_epoch && !s.routes);
 assert(!c5_check_lease(&s,1));assert(!c5_resume_lease(&s,1));
 assert(c5_resume_lease(&s,2));assert(!s.proof_epoch && !s.routes);
 assert(!c5_check_lease(&s,1));assert(c5_check_lease(&s,2));
 assert(c5_observe_route(&s));assert(s.proof_epoch==2 && s.routes==1);
 c5_close_lease(&s);assert(!s.opened && !s.active);
 assert(!c5_check_lease(&s,2));assert(!c5_observe_route(&s));
 assert(c5_open_lease(&s));assert(!c5_resume_lease(&s,2));assert(c5_resume_lease(&s,UINT_MAX));
 c5_suspend_lease(&s);assert(!c5_resume_lease(&s,0));assert(!c5_resume_lease(&s,UINT_MAX));
 puts("PASS 30 assertions: inactive open, suspend/pause, fresh resume proof, replay and overflow");
}
