/* SPDX-License-Identifier: GPL-2.0-only
 * Adapted from dakenf/lg-g5-dts, tools/dts-hd-route/break_bridge.h,
 * commit ac5f3e3e1d7f034ad1f35c43ae7c722aeae798ae; C5 addresses rederived.
 */
/* Exact-build temporary registration of the missing stock BRK handlers. */
#include <asm/debug-monitors.h>
#include <linux/rcupdate.h>
#include <linux/rculist.h>

#define BREAK_LIST ((struct list_head *)0xffffffc011a54090UL)
#define STOCK_BREAK ((struct break_hook *)0xffffffc011a562a8UL)
#define STOCK_STEP ((struct break_hook *)0xffffffc011a562c8UL)
static struct break_hook bridge_break = {
    .imm = 4, .fn = (void *)0xffffffc01108b410UL,
};
static struct break_hook bridge_step = {
    .imm = 6, .fn = (void *)0xffffffc01108b300UL,
};
typedef void (*break_registration_fn)(struct break_hook *);
static bool bridge_active;
static void bridge_remove(void)
{
    if (!bridge_active) return;
    ((break_registration_fn)0xffffffc01008e654UL)(&bridge_step);
    ((break_registration_fn)0xffffffc01008e654UL)(&bridge_break);
    synchronize_rcu();
    bridge_active = false;
}
static int bridge_install(void)
{
    struct break_hook *h;
    unsigned int count = 0;
    bool conflict = false;
    BUILD_BUG_ON(offsetof(struct break_hook, fn) != 0x10);
    BUILD_BUG_ON(offsetof(struct break_hook, imm) != 0x18);
    BUILD_BUG_ON(sizeof(struct break_hook) != 0x20);
    if (READ_ONCE(STOCK_BREAK->node.next) || READ_ONCE(STOCK_BREAK->node.prev) ||
        READ_ONCE(STOCK_STEP->node.next) || READ_ONCE(STOCK_STEP->node.prev) ||
        STOCK_BREAK->fn != bridge_break.fn || STOCK_BREAK->imm != 4 ||
        STOCK_STEP->fn != bridge_step.fn || STOCK_STEP->imm != 6)
        return -EINVAL;
    rcu_read_lock();
    list_for_each_entry_rcu(h, BREAK_LIST, node) {
        if (++count > 32 || (4 & ~h->mask) == h->imm ||
            (6 & ~h->mask) == h->imm) { conflict = true; break; }
    }
    rcu_read_unlock();
    if (conflict) return -EBUSY;
    ((break_registration_fn)0xffffffc01008e604UL)(&bridge_break);
    ((break_registration_fn)0xffffffc01008e604UL)(&bridge_step);
    bridge_active = true;
    pr_info("dts_core_route: temporarily registered missing stock BRK handlers\n");
    return 0;
}

/* Deliberately minimal non-inlined target; exercises displaced instruction and
 * software-step handling without taking an audio lock or touching a device. */
extern int dts_probe_self_target(int);
asm(".pushsection .text\n"
    ".align 2\n"
    ".type dts_probe_self_target, %function\n"
    "dts_probe_self_target:\n"
    "add w0, w0, #1\n"
    "ret\n"
    ".size dts_probe_self_target, .-dts_probe_self_target\n"
    ".popsection\n");
static unsigned int self_hits;
static bool self_skip;
static int self_pre(struct kprobe *p, struct pt_regs *r)
{
    (void)p;
    self_hits++;
    r->regs[0] = 10;
    if (self_skip) { r->pc += 4; return 1; }
    return 0;
}
NOKPROBE_SYMBOL(self_pre);
static int bridge_selftest(void)
{
    struct kprobe probe = {.addr = (void *)dts_probe_self_target,
                           .pre_handler = self_pre};
    int result, ret = register_kprobe(&probe);
    if (ret) return ret;
    result = dts_probe_self_target(7);
    if (result == 11 && self_hits == 1) {
        self_skip = true;
        result = dts_probe_self_target(7);
    }
    unregister_kprobe(&probe);
    pr_info("dts_core_route: isolated selftest hits=%u result=%d expected=10 (PC redirect)\n",
            self_hits, result);
    return self_hits == 2 && result == 10 ? 0 : -EIO;
}
