// SPDX-License-Identifier: GPL-2.0-only
/* C5 only. Uses the already resident, previously tested BRK bridge.
 * Default observe only. No renderer, DSP, ALSA, output or boot writes. */
#undef CONFIG_ARM64_LSE_ATOMICS
#include <linux/module.h>
#include <linux/kprobes.h>
#include <linux/utsname.h>
#include <linux/jiffies.h>
#include <linux/sched.h>
#include <linux/pid.h>
#include <linux/slab.h>
#include <crypto/hash.h>
#include <asm/debug-monitors.h>
#include <linux/rculist.h>
#include "gate-policy.h"
#include "target.h"
static unsigned int owner_pid;
module_param(owner_pid,uint,0400);
static unsigned int control_pid;
module_param(control_pid,uint,0400);
static struct pid *owner,*controller;
static struct module *bridge_provider;
static unsigned int calls,matches,overrides,last_pid,last_index,last_codec;
static unsigned long last_caller;
module_param(calls,uint,0400);
module_param(matches,uint,0400);
module_param(overrides,uint,0400);
module_param(last_pid,uint,0400);
module_param(last_index,uint,0400);
module_param(last_codec,uint,0400);
module_param(last_caller,ulong,0400);
static unsigned long expires;
static bool pinned,hooks_live;
static unsigned int arm_seconds;
static int arm_set(const char *value,const struct kernel_param *parameter)
{
 unsigned int seconds;int ret=kstrtouint(value,10,&seconds);
 (void)parameter;
 if(ret || seconds>120)return -EINVAL;
 if(seconds && !hooks_live)return -EAGAIN;
 if(seconds && !owner_pid)return -EINVAL;
 if(seconds && !pinned){__module_get(THIS_MODULE);pinned=true;}
 WRITE_ONCE(expires,seconds?jiffies+seconds*HZ:0);
 WRITE_ONCE(arm_seconds,seconds);return 0;
}
static const struct kernel_param_ops arm_ops={.set=arm_set,.get=param_get_uint};
module_param_cb(arm_seconds,&arm_ops,&arm_seconds,0600);
static int gate_pre(struct kprobe *probe,struct pt_regs *r)
{
 unsigned long deadline=READ_ONCE(expires);unsigned int pid=task_tgid_nr(current);
 (void)probe;
 WRITE_ONCE(calls,READ_ONCE(calls)+1);
 WRITE_ONCE(last_pid,pid);WRITE_ONCE(last_index,(unsigned int)r->regs[22]);
 WRITE_ONCE(last_codec,(unsigned int)r->regs[19]);WRITE_ONCE(last_caller,r->regs[30]);
 if((task_tgid(current)!=owner && task_tgid(current)!=controller) || !c5_hdmi_gate_matches(r->regs[30],r->regs[22],r->regs[19],
  READ_ONCE(*(u32 *)0xffffffc01262a51cUL),
  READ_ONCE(*(u32 *)0xffffffc01262a520UL),task_tgid(current)==owner?owner_pid:control_pid,pid,1))return 0;
 WRITE_ONCE(matches,READ_ONCE(matches)+1);
 if(!deadline || time_after_eq(jiffies,deadline))return 0;
 /* Probe the stock RET after MOV w0,0. The normal RET simulator preserves LR. */
 r->regs[0]=1;
 WRITE_ONCE(overrides,READ_ONCE(overrides)+1);return 0;
}
NOKPROBE_SYMBOL(gate_pre);
static struct kprobe gate={.addr=(void *)C5_HDMI_GATE,.pre_handler=gate_pre};
extern int c5_hdmi_self_target(void);
asm(".pushsection .text\n.align 2\n.type c5_hdmi_self_target,%function\n"
 "c5_hdmi_self_target:\nmov w0,#0\nret\n.size c5_hdmi_self_target,.-c5_hdmi_self_target\n.popsection\n");
static int self_pre(struct kprobe *p,struct pt_regs *r)
{(void)p;r->regs[0]=9;return 0;}
NOKPROBE_SYMBOL(self_pre);
static int fingerprints(void)
{
 struct crypto_shash *tfm;struct shash_desc *desc;unsigned char bytes[64],digest[32];
 unsigned int i;int ret=0;
 tfm=crypto_alloc_shash("sha256",0,0);if(IS_ERR(tfm))return PTR_ERR(tfm);
 desc=kmalloc(sizeof(*desc)+crypto_shash_descsize(tfm),GFP_KERNEL);
 if(!desc){crypto_free_shash(tfm);return -ENOMEM;}desc->tfm=tfm;
 for(i=0;i<ARRAY_SIZE(c5_hdmi_checks);i++){
  ret=probe_kernel_read(bytes,(void *)c5_hdmi_checks[i].address,64);if(ret)break;
  ret=crypto_shash_digest(desc,bytes,64,digest);
  if(ret || memcmp(digest,c5_hdmi_checks[i].sha256,32)){ret=-EINVAL;break;}
 }
 kfree(desc);crypto_free_shash(tfm);return ret;
}
static int __init hdmi_init(void)
{
 struct break_hook *h;struct module *provider;unsigned int count=0,brk=0,step=0;int ret;
 struct kprobe test={.addr=(void *)((unsigned long)c5_hdmi_self_target+4),.pre_handler=self_pre};
 BUILD_BUG_ON(offsetof(struct kprobe,addr)!=0x28);
 BUILD_BUG_ON(offsetof(struct kprobe,pre_handler)!=0x40);
 BUILD_BUG_ON(offsetof(struct break_hook,fn)!=0x10);
 if(strcmp(init_utsname()->release,"5.4.268-329.ptl4tv.5") || !owner_pid || !control_pid || arm_seconds)return -EINVAL;
 ret=fingerprints();if(ret)return ret;
 /* Never add, remove, or replace the shared bridge. Require its tested pair. */
 preempt_disable();rcu_read_lock();
 list_for_each_entry_rcu(h,(struct list_head *)0xffffffc011a54090UL,node){
  if(++count>32)break;
  if((4 & ~h->mask)==h->imm || (6 & ~h->mask)==h->imm){
   /* __module_address is not exported on C5. Its exact implementation is
    * fingerprinted. Preemption and RCU keep the provider stable until pinned. */
   provider=((struct module *(*)(unsigned long))C5_MODULE_ADDRESS)((unsigned long)h);
   if(!provider || strcmp(provider->name,"c5_es_poc")){brk+=32;break;}
   if(!bridge_provider){if(!try_module_get(provider)){brk+=32;break;}bridge_provider=provider;}
   else if(provider!=bridge_provider){brk+=32;break;}
  }
  if((4 & ~h->mask)==h->imm){if(h->fn==(void *)0xffffffc01108b410UL)brk++;else brk+=32;}
  if((6 & ~h->mask)==h->imm){if(h->fn==(void *)0xffffffc01108b300UL)step++;else step+=32;}
 }
 rcu_read_unlock();preempt_enable();
 if(count>32 || brk!=1 || step!=1){ret=-EBUSY;goto fail;}
 ret=register_kprobe(&test);if(ret)goto fail;
 ret=c5_hdmi_self_target();unregister_kprobe(&test);if(ret!=9){ret=-EINVAL;goto fail;}
 owner=find_get_pid(owner_pid);controller=find_get_pid(control_pid);
 if(!owner || !controller){ret=-ESRCH;goto fail;}
 ret=register_kprobe(&gate);
 if(ret)goto fail;
 hooks_live=true;
 pr_info("c5_hdmi_broker_gate: observe only; RET test passed; bridge provider pinned\n");
 return 0;
fail:if(owner)put_pid(owner);if(controller)put_pid(controller);if(bridge_provider)module_put(bridge_provider);return ret;
}
static void __exit hdmi_exit(void){unregister_kprobe(&gate);put_pid(owner);put_pid(controller);module_put(bridge_provider);}
module_init(hdmi_init);module_exit(hdmi_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Exact C5 HDMI1/ADEC1 DTS core gate diagnostic; default observe only");
