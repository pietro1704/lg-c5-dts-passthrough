/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef C5_LEASE_STATE_H
#define C5_LEASE_STATE_H
/* Caller serializes every operation. This same state machine is compiled into
 * the kernel module and the host regression harness. */
struct c5_lease_state {
 unsigned int id,epoch,proof_epoch,routes;
 int opened,active;
};
static inline int c5_open_lease(struct c5_lease_state *s) {
 if(s->opened)return 0;
 s->opened=1;s->active=0;s->routes=0;s->proof_epoch=0;return 1;
}
static inline void c5_suspend_lease(struct c5_lease_state *s) {
 s->active=0;s->routes=0;s->proof_epoch=0;
}
static inline void c5_close_lease(struct c5_lease_state *s) {
 c5_suspend_lease(s);s->opened=0;
}
static inline int c5_resume_lease(struct c5_lease_state *s,unsigned int epoch) {
 if(!s->opened || !epoch || epoch<=s->epoch)return 0;
 s->epoch=epoch;s->active=1;s->routes=0;s->proof_epoch=0;return 1;
}
static inline int c5_check_lease(const struct c5_lease_state *s,unsigned int epoch) {
 return s->opened && s->active && epoch && s->epoch==epoch;
}
static inline int c5_observe_route(struct c5_lease_state *s) {
 if(!s->opened || !s->active || !s->epoch)return 0;
 s->routes++;s->proof_epoch=s->epoch;return 1;
}
#endif
