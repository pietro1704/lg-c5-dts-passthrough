#include <assert.h>
#include "module/gate-policy.h"
#include "module/route-policy.h"
#include "module/input-policy.h"
int main(void) {
 assert(c5_hdmi_gate_matches(0xffffffc010cce210UL,1,51,1,1,20,20,1));
 assert(!c5_hdmi_gate_matches(0xffffffc010cce210UL,1,48,1,1,20,20,1));
 assert(!c5_hdmi_gate_matches(0xffffffc010cce210UL,1,51,1,1,20,21,1));
 assert(!c5_hdmi_gate_matches(0xffffffc010cce210UL,0,51,1,1,20,20,1));
 assert(c5_hdmi_route_matches(1,0x20,3,51,1,1,1,1,1,1));
 assert(!c5_hdmi_route_matches(1,0x20,3,48,1,1,1,1,1,1));
 assert(!c5_hdmi_route_matches(1,0x20,3,51,1,0,1,1,1,1));
 assert(!c5_hdmi_route_matches(1,0x20,3,51,1,1,1,1,0,1));
 assert(c5_hdmi_bind_check(1,1,10,10,0,0,1,1)==C5_BIND_OK);
 assert(c5_hdmi_bind_check(1,2,10,10,10,1,1,1)==C5_BIND_STALE);
 return 0;
}
