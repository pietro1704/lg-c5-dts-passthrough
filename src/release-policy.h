/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef DTS_RELEASE_POLICY_H
#define DTS_RELEASE_POLICY_H
/* Evaluated again after CHECK and between every queued buffer transfer. */
enum dts_release {DTS_RELEASE_ERROR=-1,DTS_RELEASE_HOLD=0,DTS_RELEASE_ALLOW=1,DTS_RELEASE_FLUSH=2};
static inline enum dts_release dts_release_policy(int flushing,int paused,int regate,
 int ready,unsigned int proof_epoch,unsigned int current_epoch,unsigned int checked_epoch,
 int kernel_ok,int peer_ok) {
 if(flushing)return DTS_RELEASE_FLUSH;
 if(paused || regate || !ready || proof_epoch!=current_epoch || checked_epoch!=current_epoch)
  return DTS_RELEASE_HOLD;
 if(!kernel_ok || !peer_ok)return DTS_RELEASE_ERROR;
 return DTS_RELEASE_ALLOW;
}
#endif
