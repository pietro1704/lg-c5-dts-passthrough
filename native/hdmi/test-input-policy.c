#include <assert.h>
#include <stdio.h>
#include "module/input-policy.h"
int main(void)
{
 assert(c5_hdmi_bind_check(1,1,10,10,0,0,1,1)==C5_BIND_OK);
 assert(c5_hdmi_bind_check(2,2,10,10,10,0,1,1)==C5_BIND_OK);
 assert(c5_hdmi_bind_check(2,2,20,20,10,1,1,1)==C5_BIND_OK);
 assert(c5_hdmi_bind_check(2,2,20,20,10,0,1,1)==C5_BIND_BUSY);
 assert(c5_hdmi_bind_check(1,2,10,10,10,1,1,1)==C5_BIND_STALE);
 /* Same address was reused after destroy: an old generation cannot bind it. */
 assert(c5_hdmi_bind_check(2,3,10,10,10,1,1,1)==C5_BIND_STALE);
 assert(c5_hdmi_bind_check(3,3,10,10,10,1,1,1)==C5_BIND_OK);
 assert(c5_hdmi_bind_check(3,3,10,10,10,1,0,1)==C5_BIND_ACTOR);
 assert(c5_hdmi_bind_check(3,3,10,10,10,1,1,0)==C5_BIND_SOURCE);
 assert(c5_hdmi_bind_check(3,3,0,0,10,1,1,1)==C5_BIND_SOURCE);
 assert(c5_hdmi_bind_check(3,3,10,20,10,1,1,1)==C5_BIND_SOURCE);
 assert(c5_hdmi_bind_check(~0u,~0u,10,10,10,1,1,1)==C5_BIND_OVERFLOW);
 puts("PASS: bind success, idempotence, cancellation, actor, foreign source, stale generation and address reuse");
 return 0;
}
