#include <assert.h>
#include <stdio.h>
#include "../src/release-policy.h"
int main(void) {
 assert(dts_release_policy(0,0,0,1,7,7,7,1,1)==DTS_RELEASE_ALLOW);
 /* Pause lands after sync_epoch, before CHECK: EPIPE is a hold, not an error. */
 assert(dts_release_policy(0,1,1,1,7,8,7,0,1)==DTS_RELEASE_HOLD);
 /* First drain push blocks, pause/resume advances epoch before next push. */
 assert(dts_release_policy(0,0,1,1,7,9,7,1,1)==DTS_RELEASE_HOLD);
 assert(dts_release_policy(0,0,0,1,7,9,9,1,1)==DTS_RELEASE_HOLD);
 assert(dts_release_policy(0,0,0,0,9,9,9,1,1)==DTS_RELEASE_HOLD);
 assert(dts_release_policy(1,0,0,1,7,7,7,0,1)==DTS_RELEASE_FLUSH);
 assert(dts_release_policy(0,0,0,1,9,9,9,0,1)==DTS_RELEASE_ERROR);
 assert(dts_release_policy(0,0,0,1,9,9,9,1,0)==DTS_RELEASE_ERROR);
 assert(dts_release_policy(0,0,0,1,9,9,9,1,1)==DTS_RELEASE_ALLOW);
 puts("PASS 9 assertions: pause between sync/CHECK, drain rearm, genuine revoke and fresh proof");
}
