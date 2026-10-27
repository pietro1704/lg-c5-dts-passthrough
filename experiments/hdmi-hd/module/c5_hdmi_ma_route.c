// SPDX-License-Identifier: GPL-2.0-only
/* Exact C5, DTS core HDMI1/ADEC1/eARC PoC. No new shared BRK bridge.
 * Default observe only; resident after arming. PCM/Dolby/HD excluded. */
#undef CONFIG_ARM64_LSE_ATOMICS
#include <linux/module.h>
#include <linux/kprobes.h>
#include <linux/utsname.h>
#include <linux/jiffies.h>
#include <linux/pid.h>
#include <linux/sched.h>
#include <linux/slab.h>
#include <crypto/hash.h>
#include "route-policy.h"
#include "input-policy.h"
#include "route-target.h"
static unsigned int owner_pid;
static unsigned int control_pid,input_generation=1;
static unsigned long input_cookie;
module_param(owner_pid,uint,0400);
module_param(control_pid,uint,0400);
module_param(input_cookie,ulong,0400);
module_param(input_generation,uint,0400);
static struct pid *owner,*controller;
static DEFINE_SPINLOCK(binding_lock);
static unsigned long expires,protected_output;
static bool pinned,invalidated,hooks_live;
static unsigned int arm_seconds,calls,matches,overrides,status_hits;
module_param(calls,uint,0400);
module_param(matches,uint,0400);
module_param(overrides,uint,0400);
module_param(status_hits,uint,0400);
module_param(invalidated,bool,0400);
static int arm_set(const char *value,const struct kernel_param *param)
{
 unsigned int seconds;int ret=kstrtouint(value,10,&seconds);(void)param;
 if(ret || seconds>120)return -EINVAL;
 if(seconds && (!hooks_live || READ_ONCE(invalidated) || !READ_ONCE(input_cookie)))return -EAGAIN;
 if(seconds && !pinned){__module_get(THIS_MODULE);pinned=true;}
 WRITE_ONCE(expires,seconds?jiffies+seconds*HZ:0);WRITE_ONCE(arm_seconds,seconds);return 0;
}
static const struct kernel_param_ops arm_ops={.set=arm_set,.get=param_get_uint};
module_param_cb(arm_seconds,&arm_ops,&arm_seconds,0600);
static int bind_set(const char *text,const struct kernel_param *parameter)
{
 unsigned int expected,words[24];unsigned long next,current_input,flags;char extra;
 enum c5_bind_result result;int ret=0;(void)parameter;
 if(!hooks_live || task_tgid(current)!=controller)return -EPERM;
 if(sscanf(text,"%u %lx %c",&expected,&next,&extra)!=2)return -EINVAL;
 current_input=READ_ONCE(*(unsigned long *)0xffffffc01262a228UL);
 if(!next || next!=current_input || probe_kernel_read(words,(void *)next,sizeof(words)))return -EINVAL;
 spin_lock_irqsave(&binding_lock,flags);
 result=c5_hdmi_bind_check(expected,input_generation,next,
  READ_ONCE(*(unsigned long *)0xffffffc01262a228UL),input_cookie,invalidated,
  task_tgid(current)==controller,words[0x2c/4]==2 && words[0x30/4]==1);
 if(result!=C5_BIND_OK) {
  const int errors[]={0,-EPERM,-ESTALE,-EINVAL,-EBUSY,-EOVERFLOW};ret=errors[result];
 } else if(next!=input_cookie || invalidated) {
  input_cookie=next;input_generation++;invalidated=false;expires=0;protected_output=0;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return ret;
}
static const struct kernel_param_ops bind_ops={.set=bind_set};
module_param_cb(bind_input,&bind_ops,NULL,0200);
static bool source_valid(void)
{
 struct task_struct *task;bool alive;unsigned int words[24];unsigned long ptr;
 if(READ_ONCE(invalidated))return false;
 rcu_read_lock();task=pid_task(owner,PIDTYPE_PID);alive=task && !READ_ONCE(task->exit_state);rcu_read_unlock();
 if(!alive)return false;
 ptr=READ_ONCE(*(unsigned long *)0xffffffc01262a228UL);
 if(ptr!=input_cookie || probe_kernel_read(words,(void *)ptr,sizeof(words)))return false;
 if(words[0x2c/4]!=2 || words[0x30/4]!=1 ||
    READ_ONCE(*(unsigned long *)0xffffffc01262a228UL)!=ptr)return false;
 return c5_hdmi_route_matches(1,0x20,3,
  READ_ONCE(*(u32 *)0xffffffc01262a2acUL),
  READ_ONCE(*(u8 *)0xffffffc01262a2a0UL),
  READ_ONCE(*(u8 *)0xffffffc01262a2a2UL),
  READ_ONCE(*(u32 *)0xffffffc01262a51cUL),
  READ_ONCE(*(u32 *)0xffffffc01262a520UL),alive,1);
}
static int route_pre(struct kprobe *p,struct pt_regs *r)
{
 unsigned long descriptor=r->regs[1],output,deadline,flags;(void)p;
 WRITE_ONCE(calls,READ_ONCE(calls)+1);
 if(r->regs[0]!=1 || !descriptor || READ_ONCE(*(u32 *)(descriptor+0x14))!=0x20)return 0;
 output=READ_ONCE(*(unsigned long *)(descriptor+0x18));
 if(!output || READ_ONCE(*(u32 *)(output+0x2c))!=3)return 0;
 spin_lock_irqsave(&binding_lock,flags);
 if(!source_valid())goto done;
 WRITE_ONCE(matches,READ_ONCE(matches)+1);
 deadline=READ_ONCE(expires);if(!deadline || time_after_eq(jiffies,deadline))goto done;
 WRITE_ONCE(protected_output,output);r->regs[2]=0;r->regs[3]=1;r->regs[4]=0;
 WRITE_ONCE(overrides,READ_ONCE(overrides)+1);
done:spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(route_pre);
static int status_pre(struct kprobe *p,struct pt_regs *r)
{
 unsigned long flags;int ret=0;
 (void)p;
 /* Preserve core status protection after the arm deadline for this still-live
  * owned input. Stop creating routes at expiry; PCM/source loss stops this. */
 if(r->regs[6]!=7 || !r->regs[19])return 0;
 if(READ_ONCE(*(u32 *)(r->regs[19]+0x2c))!=3)return 0;
 spin_lock_irqsave(&binding_lock,flags);
 if(r->regs[19]==protected_output && source_valid()) {
  r->pc=C5_CORE_STATUS_ACCEPT;WRITE_ONCE(status_hits,READ_ONCE(status_hits)+1);ret=1;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return ret;
}
NOKPROBE_SYMBOL(status_pre);
static const char hd_label[]="dts-hd", unknown_label[]="unknown";
static int name_pre(struct kprobe *p,struct pt_regs *r)
{
 (void)p;
 if(r->regs[4]>5 && r->regs[19] && r->regs[19]==READ_ONCE(protected_output)) {
  r->regs[7]=(unsigned long)(r->regs[4]==7?hd_label:unknown_label);
  r->pc=C5_HDMI_HD_NAME+4;return 1;
 }
 return 0;
}
NOKPROBE_SYMBOL(name_pre);
static int destroy_pre(struct kprobe *p,struct pt_regs *r)
{
 unsigned long flags;(void)p;spin_lock_irqsave(&binding_lock,flags);
 if(r->regs[0]==input_cookie && !invalidated) {
  invalidated=true;expires=0;if(input_generation!=~0u)input_generation++;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(destroy_pre);
static struct kprobe route={.addr=(void *)C5_CORE_ROUTE,.pre_handler=route_pre};
static struct kprobe status={.addr=(void *)C5_CORE_STATUS,.pre_handler=status_pre};
static struct kprobe destroy={.addr=(void *)C5_INPUT_DESTROY,.pre_handler=destroy_pre};
static struct kprobe name_guard={.addr=(void *)C5_HDMI_HD_NAME,.pre_handler=name_pre};
static struct kprobe *probes[]={&name_guard,&status,&route,&destroy};
extern int c5_route_self_target(int);
asm(".pushsection .text\n.align 2\n.type c5_route_self_target,%function\n"
 "c5_route_self_target:\ncmp w0,#4\nb.ls 1f\nmov w0,#8\nret\n1:mov w0,#9\nret\n"
 ".size c5_route_self_target,.-c5_route_self_target\n.popsection\n");
static bool self_skip;
static int self_pre(struct kprobe *p,struct pt_regs *r)
{(void)p;if(self_skip){r->pc=(unsigned long)c5_route_self_target+16;return 1;}return 0;}
NOKPROBE_SYMBOL(self_pre);
static int fingerprints(void)
{
 struct crypto_shash *tfm;struct shash_desc *desc;unsigned char bytes[64],digest[32];
 unsigned int i;int ret=0;
 tfm=crypto_alloc_shash("sha256",0,0);if(IS_ERR(tfm))return PTR_ERR(tfm);
 desc=kmalloc(sizeof(*desc)+crypto_shash_descsize(tfm),GFP_KERNEL);
 if(!desc){crypto_free_shash(tfm);return -ENOMEM;}desc->tfm=tfm;
 for(i=0;i<ARRAY_SIZE(c5_core_checks);i++){
  ret=probe_kernel_read(bytes,(void *)c5_core_checks[i].address,64);if(ret)break;
  ret=crypto_shash_digest(desc,bytes,64,digest);
  if(ret || memcmp(digest,c5_core_checks[i].sha256,32)){ret=-EINVAL;break;}
 }
 kfree(desc);crypto_free_shash(tfm);return ret;
}
static int __init route_init(void)
{
 int ret;struct kprobe test={.addr=(void *)((unsigned long)c5_route_self_target+4),.pre_handler=self_pre};
 if(strcmp(init_utsname()->release,"5.4.268-329.ptl4tv.5") || !owner_pid || !control_pid || input_cookie || arm_seconds)return -EINVAL;
 ret=fingerprints();if(ret)return ret;
 owner=find_get_pid(owner_pid);controller=find_get_pid(control_pid);
 if(!owner || !controller){ret=-ESRCH;goto fail;}
 /* source_valid rejects PCM, so validate identity only after a real DTS attempt. */
 ret=register_kprobe(&test);if(ret)goto fail;
 if(c5_route_self_target(3)!=9 || c5_route_self_target(5)!=8)ret=-EINVAL;
 self_skip=true;if(c5_route_self_target(5)!=9)ret=-EINVAL;
 unregister_kprobe(&test);if(ret)goto fail;
 ret=register_kprobes(probes,ARRAY_SIZE(probes));if(ret)goto fail;
 hooks_live=true;
 pr_info("c5_hdmi_ma_route: unbound/observe only; conditional branch/self redirect passed\n");return 0;
fail:if(owner)put_pid(owner);if(controller)put_pid(controller);return ret;
}
static void __exit route_exit(void){unregister_kprobes(probes,ARRAY_SIZE(probes));put_pid(owner);put_pid(controller);}
module_init(route_init);module_exit(route_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Exact C5 HDMI DTS core bypass trial, observe only by default");
