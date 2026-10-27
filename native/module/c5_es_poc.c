// SPDX-License-Identifier: GPL-2.0-only
/* Exact C5 build only. Resident after first renderer: reboot to remove safely.
 * No audio routing is changed without a live, authorized process/file lease. */
#undef CONFIG_ARM64_LSE_ATOMICS
#include <linux/module.h>
#include <crypto/hash.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/kprobes.h>
#include <linux/utsname.h>
#include <linux/pid.h>
#include <linux/sched.h>
#include <linux/spinlock.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/ioctl.h>
#include "target.h"
#include "lease-policy.h"
#include "lease-state.h"
#include "build-id.h"
static char *session_build=C5_SESSION_BUILD;
module_param(session_build,charp,0400);
#include "break_bridge.h"
#define SLOTS 4
#define API _IO('D',0x51)
#define REVOKE _IO('D',0x52)
#define CHECK _IO('D',0x53)
#define RESUME _IO('D',0x54)
#define SUSPEND _IO('D',0x55)
#define VERSION 0x435502
struct session {
 struct pid *pid;
 unsigned int tgid,index,captures,statuses,invalidations;
 struct c5_lease_state lease;
 unsigned long renderer;
 bool index_valid;
};
static struct session sessions[SLOTS];
static DEFINE_SPINLOCK(binding_lock);
static unsigned int serial;
static unsigned long protected_output;
static bool hooks_live,lease_registered,resident;
static unsigned int session_protocol=3;
module_param(session_protocol,uint,0400);
static bool selftest_only=true;
module_param(selftest_only,bool,0400);
static bool lease_api_only;
module_param(lease_api_only,bool,0400);
static bool enable_route=true;
module_param(enable_route,bool,0600);
static bool enable_hd_status=true;
module_param(enable_hd_status,bool,0600);
static const char hd_name[]="dts-hd",unknown_name[]="unknown";
static bool alive(const struct session *s) {
 struct task_struct *task;bool result;
 rcu_read_lock();task=s->pid?pid_task(s->pid,PIDTYPE_PID):NULL;
 result=task && !READ_ONCE(task->exit_state);
 rcu_read_unlock();return result;
}
static bool es_valid(const struct session *s) {
 return s->renderer && READ_ONCE(*(u8 *)(s->renderer+0x4c))==1 &&
  READ_ONCE(*(u8 *)(s->renderer+0x4d))==0 &&
  READ_ONCE(*(unsigned long *)(s->renderer+0x18))!=0;
}
static struct session *own_session(void) {
 unsigned int i;
 for(i=0;i<SLOTS;i++)
  if(sessions[i].lease.active && sessions[i].pid==task_tgid(current))return &sessions[i];
 return NULL;
}
static void bind_renderer(struct session *s,unsigned long renderer) {
 if(!s->renderer) {
  s->renderer=renderer;s->captures++;s->statuses=0;s->lease.proof_epoch=0;
  /* Removing BRK dispatch while other native pipelines run has not been
   * established safe. Keep this module resident until reboot after real use. */
  if(!resident){__module_get(THIS_MODULE);resident=true;}
 }
}
static int capture_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags;struct session *s;(void)probe;
 spin_lock_irqsave(&binding_lock,flags);s=own_session();
 if(s && r->regs[0])bind_renderer(s,r->regs[0]);
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(capture_pre);
static int index_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags;unsigned int index;struct session *s;(void)probe;
 /* This getter is also used for non-renderer devices. Accept only the exact
  * bypass-creation caller, verified saved LR, owner and native index 0/1. */
 if(!r->regs[0] || !r->regs[19] ||
    READ_ONCE(*(unsigned long *)(r->sp+8))!=C5_BYPASS_INDEX_AFTER)return 0;
 /* x0 is the verified block-info result before stock reads preferred DSP
  * processor at +0x1c. The native renderer index is instead at +0x18. */
 index=READ_ONCE(*(u32 *)(r->regs[0]+0x18));if(index>1)return 0;
 spin_lock_irqsave(&binding_lock,flags);s=own_session();
 if(s && (!s->renderer || s->renderer==r->regs[19])) {
  bind_renderer(s,r->regs[19]);s->index=index;s->index_valid=true;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(index_pre);
static int destroy_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags;unsigned int i;(void)probe;
 if(!r->regs[0])return 0;
 spin_lock_irqsave(&binding_lock,flags);
 for(i=0;i<SLOTS;i++)if(sessions[i].renderer==r->regs[0]) {
  sessions[i].renderer=0;sessions[i].index_valid=false;sessions[i].invalidations++;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(destroy_pre);
static struct session *selected_session(void) {
 unsigned int i,port=READ_ONCE(*(u32 *)(C5_SNDOUT+0x1b8));
 struct session *selected=NULL;
 if(READ_ONCE(*(u32 *)(C5_SNDOUT+0x1b4))!=1 || port>1)return NULL;
 for(i=0;i<SLOTS;i++) {
  struct session *s=&sessions[i];
  if(c5_scope_matches(1,port,s->index,s->lease.active,s->index_valid,alive(s),s->lease.active && alive(s) && es_valid(s))) {
   if(selected)return NULL; /* Ambiguous identity must not change routing. */
   selected=s;
  }
 }
 return selected;
}
static int route_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags,out=r->regs[1],output;struct session *s;(void)probe;
 if(!enable_route || !out || r->regs[0]!=0 || READ_ONCE(*(u32 *)(out+0x14))!=0x20)return 0;
 output=READ_ONCE(*(unsigned long *)(out+0x18));
 if(!output || READ_ONCE(*(u32 *)(output+0x2c))!=3)return 0;
 spin_lock_irqsave(&binding_lock,flags);s=selected_session();
 if(s) {
  protected_output=output;r->regs[2]=0;r->regs[3]=1;r->regs[4]=0;c5_observe_route(&s->lease);
 }
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
NOKPROBE_SYMBOL(route_pre);
static int status_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags;struct session *s;bool match=false;(void)probe;
 if(!enable_hd_status || r->regs[6]!=7)return 0;
 spin_lock_irqsave(&binding_lock,flags);s=selected_session();
 if(s && protected_output && r->regs[19]==protected_output &&
    READ_ONCE(*(u32 *)(protected_output+0x2c))==3) {
  r->pc=C5_STATUS_ACCEPT;s->statuses++;match=true;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return match?1:0;
}
NOKPROBE_SYMBOL(status_pre);
static int name_pre(struct kprobe *probe,struct pt_regs *r) {
 unsigned long flags;bool match;(void)probe;
 if(r->regs[4]<=5)return 0;
 spin_lock_irqsave(&binding_lock,flags);
 match=protected_output && r->regs[19]==protected_output;
 if(match){r->regs[7]=(unsigned long)(r->regs[4]==7?hd_name:unknown_name);r->pc=C5_STATUS_NAME+4;}
 spin_unlock_irqrestore(&binding_lock,flags);return match?1:0;
}
NOKPROBE_SYMBOL(name_pre);
static int grant_set(const char *value,const struct kernel_param *parameter) {
 unsigned int pid,i,slot=SLOTS;struct pid *next,*previous;unsigned long flags;int ret;
 (void)parameter;ret=kstrtouint(value,10,&pid);if(ret || !pid)return -EINVAL;
 if(!c5_next_id(READ_ONCE(serial)))return -EOVERFLOW;
 next=find_get_pid(pid);if(!next)return -ESRCH;
 rcu_read_lock();ret=pid_task(next,PIDTYPE_PID)?0:-ESRCH;rcu_read_unlock();
 if(ret){put_pid(next);return ret;}
 spin_lock_irqsave(&binding_lock,flags);
 for(i=0;i<SLOTS;i++)if(sessions[i].pid==next) {slot=i;break;}
 if(!c5_next_id(serial))ret=-EOVERFLOW;
 else if(slot<SLOTS && sessions[slot].lease.opened)ret=-EBUSY;
 else {
  if(slot==SLOTS)for(i=0;i<SLOTS;i++)if(!sessions[i].lease.opened){slot=i;break;}
  if(slot==SLOTS)ret=-EBUSY;
  else {
   previous=sessions[slot].pid;
   memset(&sessions[slot],0,sizeof(sessions[slot]));
   sessions[slot].pid=next;sessions[slot].tgid=pid;
   serial=c5_next_id(serial);sessions[slot].lease.id=serial;
   spin_unlock_irqrestore(&binding_lock,flags);
   if(previous)put_pid(previous);return 0;
  }
 }
 spin_unlock_irqrestore(&binding_lock,flags);put_pid(next);return ret;
}
static const struct kernel_param_ops grant_ops={.set=grant_set};
module_param_cb(grant_pid,&grant_ops,NULL,0200);
static int state_get(char *buffer,const struct kernel_param *parameter) {
 unsigned long flags;unsigned int i,n=0;(void)parameter;
 spin_lock_irqsave(&binding_lock,flags);
 n=scnprintf(buffer,PAGE_SIZE,"main %u %u %u\n",READ_ONCE(*(u32 *)(C5_SNDOUT+0x1b4)),READ_ONCE(*(u32 *)(C5_SNDOUT+0x1b8)),resident);
 for(i=0;i<SLOTS;i++) {
  struct session *s=&sessions[i];
  n+=scnprintf(buffer+n,PAGE_SIZE-n,"%u %u %u %u %u %u %u %u %u %u %u %u %u %u\n",i,s->tgid,
   s->lease.active,s->lease.id,s->index_valid?s->index:~0u,s->renderer!=0,s->lease.active && alive(s) && es_valid(s),
   s->captures,s->lease.routes,s->statuses,s->invalidations,s->lease.opened,s->lease.epoch,s->lease.proof_epoch);
 }
 spin_unlock_irqrestore(&binding_lock,flags);return n;
}
static const struct kernel_param_ops state_ops={.get=state_get};
module_param_cb(session_state,&state_ops,NULL,0400);
static struct session *handle_session(struct file *file) {
 unsigned long value=(unsigned long)file->private_data;
 unsigned int slot=c5_cookie_slot(value);
 if(slot>=SLOTS || !c5_cookie_matches(value,sessions[slot].lease.id))return NULL;
 return &sessions[slot];
}
static int lease_open(struct inode *inode,struct file *file) {
 unsigned long flags;unsigned int i;int ret=-EPERM;(void)inode;
 spin_lock_irqsave(&binding_lock,flags);
 for(i=0;i<SLOTS;i++)if(sessions[i].pid==task_tgid(current) && alive(&sessions[i])) {
  if(sessions[i].lease.opened)ret=-EBUSY;
  else {c5_open_lease(&sessions[i].lease);file->private_data=(void *)c5_cookie(i,sessions[i].lease.id);ret=0;}
  break;
 }
 spin_unlock_irqrestore(&binding_lock,flags);return ret;
}
static int lease_release(struct inode *inode,struct file *file) {
 unsigned long flags;struct session *s;(void)inode;
 spin_lock_irqsave(&binding_lock,flags);s=handle_session(file);
 if(s)c5_close_lease(&s->lease);
 spin_unlock_irqrestore(&binding_lock,flags);return 0;
}
static long lease_ioctl(struct file *file,unsigned int command,unsigned long argument) {
 unsigned long flags;struct session *s;long ret;(void)argument;
 spin_lock_irqsave(&binding_lock,flags);s=handle_session(file);
 if(!s || !s->lease.opened)ret=-EPIPE;
 else if(s->pid!=task_tgid(current) || !alive(s))ret=-EPERM;
 else if(command==API)ret=VERSION;
 else if(command==REVOKE || command==SUSPEND){c5_suspend_lease(&s->lease);ret=0;}
 else if(command==RESUME) {
  ret=argument<=~0u && c5_resume_lease(&s->lease,(unsigned int)argument)?0:-EINVAL;
 }
 else if(command==CHECK)ret=argument<=~0u && c5_check_lease(&s->lease,(unsigned int)argument)?0:-EPIPE;
 else ret=-ENOTTY;
 spin_unlock_irqrestore(&binding_lock,flags);return ret;
}
static const struct file_operations lease_fops={.owner=THIS_MODULE,.open=lease_open,
 .release=lease_release,.unlocked_ioctl=lease_ioctl,.compat_ioctl=lease_ioctl,.llseek=no_llseek};
static struct miscdevice lease_device={.minor=MISC_DYNAMIC_MINOR,.name="c5_dts_session",.fops=&lease_fops,.mode=0600};
static struct kprobe capture_probe={.addr=(void *)C5_ES_HEAD,.pre_handler=capture_pre};
static struct kprobe index_probe={.addr=(void *)C5_INDEX_METADATA,.pre_handler=index_pre};
static struct kprobe destroy_probe={.addr=(void *)C5_RENDERER_DESTROY,.pre_handler=destroy_pre};
static struct kprobe route_probe={.addr=(void *)C5_SET_OUTPUT,.pre_handler=route_pre};
static struct kprobe status_probe={.addr=(void *)C5_STATUS_COMPARE,.pre_handler=status_pre};
static struct kprobe name_probe={.addr=(void *)C5_STATUS_NAME,.pre_handler=name_pre};
static struct kprobe *probes[]={&name_probe,&status_probe,&destroy_probe,&capture_probe,&index_probe,&route_probe};
static int check_fingerprints(void) {
 struct crypto_shash *tfm;struct shash_desc *desc;
 unsigned int i;unsigned char digest[32],bytes[64];int ret=0;
 tfm=crypto_alloc_shash("sha256",0,0);if(IS_ERR(tfm))return PTR_ERR(tfm);
 desc=kmalloc(sizeof(*desc)+crypto_shash_descsize(tfm),GFP_KERNEL);
 if(!desc){crypto_free_shash(tfm);return -ENOMEM;}
 desc->tfm=tfm;
 for(i=0;i<ARRAY_SIZE(c5_checks);i++) {
  ret=probe_kernel_read(bytes,(const void *)c5_checks[i].address,sizeof(bytes));
  if(ret)break;
  ret=crypto_shash_digest(desc,bytes,sizeof(bytes),digest);
  if(ret || memcmp(digest,c5_checks[i].sha256,32)){ret=-EINVAL;break;}
 }
 kfree(desc);crypto_free_shash(tfm);return ret;
}
static int __init c5_init(void) {
 int ret;
 BUILD_BUG_ON(offsetof(struct kprobe,addr)!=0x28);
 BUILD_BUG_ON(offsetof(struct kprobe,pre_handler)!=0x40);
 BUILD_BUG_ON(offsetof(struct kprobe,flags)!=0x80);
 if(strcmp(init_utsname()->release,"5.4.268-329.ptl4tv.5"))return -EINVAL;
 ret=check_fingerprints();if(ret)return ret;
 if(lease_api_only) {
  if(selftest_only)return -EINVAL;
  ret=misc_register(&lease_device);if(!ret)lease_registered=true;
  return ret; /* No BRK handlers or audio probes in the isolated API/clock bench. */
 }
 ret=bridge_install();if(ret)return ret;
 ret=bridge_selftest();if(ret)goto fail;
 if(selftest_only)return 0;
 ret=register_kprobes(probes,ARRAY_SIZE(probes));if(ret)goto fail;hooks_live=true;
 ret=misc_register(&lease_device);
 if(ret){unregister_kprobes(probes,ARRAY_SIZE(probes));hooks_live=false;goto fail;}
 lease_registered=true;pr_info("c5_es_poc: resident-capable process leases api=%x\n",VERSION);return 0;
fail:bridge_remove();return ret;
}
static void __exit c5_exit(void) {
 unsigned int i;
 if(lease_registered)misc_deregister(&lease_device);
 if(hooks_live)unregister_kprobes(probes,ARRAY_SIZE(probes));
 bridge_remove();for(i=0;i<SLOTS;i++)if(sessions[i].pid)put_pid(sessions[i].pid);
}
module_init(c5_init);module_exit(c5_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Exact C5 native DTS-HD leases; resident after audio use until reboot");
