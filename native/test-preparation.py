"""Exercise the actual firmware adapter functions with modeled vendor calls.

The host substitutes GStreamer tree/caps plumbing, firmware lookup, and the
lease ioctl. The function bodies (including all mutation/cancellation guards)
come from the production plugin. No device, firmware, or media is used.
--emit writes the same C fixture for an additional ARM32 target run.
"""
from pathlib import Path
import argparse,subprocess,tempfile
root=Path(__file__).resolve().parent.parent
source=(root/'src/gstdtspassthrough.c').read_text()
functions=[]
for name in ('preparation_allowed','restore_preparation','prepare_owned_renderer'):
    start=source.index('static gboolean '+name+'(DtsPass *s,') if name!='prepare_owned_renderer' else source.index('static gboolean '+name+'(DtsPass *s) {')
    # Skip the forward declaration, if present.
    if source.index(';',start)<source.index('{',start):
        start=source.index('static gboolean '+name+'(',source.index(';',start)+1)
    end=source.index('\n}\n',start)+3
    functions.append(source[start:end].replace('sizeof(void *)','fixture_pointer_size'))
prefix=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
typedef int gboolean,gint,GMutex,GstIteratorResult;
typedef unsigned guint;typedef uint64_t guint64;typedef int64_t gint64;
typedef char gchar;typedef unsigned char guchar;typedef size_t gsize;
typedef struct {unsigned char memory[792];} GstElement;
typedef GstElement GstObject;
typedef struct {void *object;} GValue;
typedef struct {unsigned instance_size;} GTypeQuery;
typedef struct {int index;} GstIterator;
typedef struct {int unused;} GstCaps,GstStructure,GstPad;
typedef struct {uint64_t pts;} GstBuffer;
typedef struct {
 GstElement parent;
 int flushing,user_paused,stream_epoch,kernel_lease_fd,carrier_rate;
 GMutex file_lock;GstBuffer *held_buffers;
 GstElement *preparation_sink;void *preparation_handle;
 int preparation_lipsync,preparation_started;
 guint preparation_epoch,preparation_feeds;gint64 next_prepare;
} DtsPass;
#define TRUE 1
#define FALSE 0
#define G_VALUE_INIT {0}
#define GST_CLOCK_TIME_NONE UINT64_MAX
#define GST_BUFFER_PTS(b) ((b)->pts)
#define GST_ITERATOR_OK 1
#define GST_ITERATOR_DONE 0
#define GST_IS_BIN(p) ((p)==&tree)
#define GST_BIN(p) (p)
#define G_VALUE_TYPE(v) ((v)->object!=NULL)
#define G_OBJECT_TYPE_NAME(p) "GstLgAudioRender"
#define G_OBJECT_TYPE(p) (p)
#define G_CHECKSUM_SHA256 0
#define RTLD_NOW 1
#define RTLD_NOLOAD 2
#define C5_LEASE_CHECK 3
#define DTS_PREPARATION_TIMEOUT_US 1500000
struct device {int mode,sync,starts,plays,feeds,writes;};
static struct device original,replacement;
static GstElement sink,tree;static GstPad pad;
static GstCaps caps;static GstIterator iterator;static DtsPass *owner;
static int fixture_pointer_size,count,instance_size,library_ok,open_ok,refs,locks;
static int allowed,fault,calls,cancel_at,replace_at,epoch_at,missing_symbol,iterator_status;
static int open_count,close_count,channels,rate,es;static gint64 now,caps_at;
static void put_handle(void *h){memcpy(sink.memory+0x200,&h,sizeof(h));}
static void event(void){calls++;if(calls==cancel_at)owner->flushing=1;if(calls==replace_at)put_handle(&replacement);if(calls==epoch_at)owner->stream_epoch++;}
static void state_lock(void *p){assert(p==&sink);locks++;}
static void state_unlock(void *p){assert(p==&sink && locks==1);locks--;}
#define GST_STATE_LOCK(p) state_lock(p)
#define GST_STATE_UNLOCK(p) state_unlock(p)
static int getmode(void *h,int *m){assert(locks==1);event();*m=((struct device *)h)->mode;return fault==1?-1:0;}
static int setmode(void *h,int m){assert(locks==1);event();((struct device *)h)->mode=m;((struct device *)h)->writes++;return fault==2?-1:0;}
static int play(void *h){struct device *d=h;assert(locks==1);event();d->sync=d->mode;d->plays++;return fault==3?-1:0;}
static int start(void *h){assert(locks==1);event();((struct device *)h)->starts++;return fault==4?-1:0;}
static int feed(void *h,void *data,unsigned length,unsigned long long pts,int type){
 assert(locks==1 && length==32768 && pts==900000000 && type==1);
 for(unsigned i=0;i<length;i++)assert(!((unsigned char *)data)[i]);
 event();((struct device *)h)->feeds++;return fault==5?-1:fault==6?(int)length-1:(int)length;
}
static void *dlopen(const char *path,int flags){(void)path;assert(flags==(RTLD_NOW|RTLD_NOLOAD));if(!open_ok)return NULL;open_count++;return &tree;}
static int dlclose(void *p){assert(p==&tree);close_count++;return 0;}
static void *dlsym(void *p,const char *name){
 assert(p==&tree);if(missing_symbol)return NULL;
 if(!strcmp(name,"lgao_get_lipsync_mode"))return getmode;
 if(!strcmp(name,"lgao_set_lipsync_mode"))return setmode;
 if(!strcmp(name,"lgao_play"))return play;
 if(!strcmp(name,"lgao_start_renderer"))return start;
 if(!strcmp(name,"lgao_feed"))return feed;
 abort();
}
static void *gst_object_ref(void *p){refs++;return p;}
static void gst_object_unref(void *p){assert(p);refs--;}
static GstObject *gst_object_get_parent(void *p){if(p==owner){refs++;return &tree;}return NULL;}
static GstIterator *gst_bin_iterate_recurse(void *p){assert(p==&tree);iterator.index=0;return &iterator;}
static GstIteratorResult gst_iterator_next(GstIterator *it,GValue *v){if(it->index++<count){v->object=&sink;return GST_ITERATOR_OK;}return iterator_status;}
static void gst_iterator_free(GstIterator *p){(void)p;}
static void *g_value_get_object(GValue *v){return v->object;}
static void g_value_reset(GValue *v){v->object=NULL;}
static void g_value_unset(GValue *v){v->object=NULL;}
static void g_type_query(void *p,GTypeQuery *q){assert(p==&sink);q->instance_size=instance_size;}
static int g_file_get_contents(const char *p,char **data,size_t *length,void *error){(void)p;(void)error;*data=strdup("fixture");*length=7;return 1;}
static char *g_compute_checksum_for_data(int kind,unsigned char *data,size_t length){(void)kind;(void)data;(void)length;return strdup(library_ok?"90dba3ef32704ce4096bf1e6045d2879d0c2192d507bc04d1e8e98437f420a43":"wrong");}
static void g_free(void *p){free(p);}
static GstPad *gst_element_get_static_pad(void *p,const char *name){assert(p==&sink && !strcmp(name,"sink"));refs++;return &pad;}
static GstCaps *gst_pad_get_current_caps(void *p){assert(p==&pad);return now>=caps_at?&caps:NULL;}
static int gst_caps_get_size(void *p){assert(p==&caps);return 1;}
static const GstStructure *gst_caps_get_structure(void *p,int n){(void)n;assert(p==&caps);return (void *)&caps;}
static const char *gst_structure_get_string(const GstStructure *p,const char *name){(void)p;assert(!strcmp(name,"codec-str"));return "dts_hd_ma";}
static int gst_structure_get_int(const GstStructure *p,const char *name,int *v){(void)p;*v=!strcmp(name,"channels")?channels:rate;return 1;}
static int gst_structure_get_boolean(const GstStructure *p,const char *name,int *v){(void)p;(void)name;*v=es;return 1;}
static void gst_caps_unref(void *p){(void)p;}
static void *g_queue_peek_head(GstBuffer **p){return *p;}
static int g_atomic_int_get(int *p){return *p;}
static void g_mutex_lock(void *p){(void)p;}
static void g_mutex_unlock(void *p){(void)p;}
static int ioctl(int fd,unsigned request,unsigned e){assert(fd==4 && request==C5_LEASE_CHECK);return allowed && e==(unsigned)owner->stream_epoch?0:-1;}
static gint64 g_get_monotonic_time(void){return now;}
static void g_usleep(unsigned n){now+=n;}
'''
suffix=r'''
static GstBuffer first={900000000};
static void reset(DtsPass *s,int mode){
 memset(s,0,sizeof(*s));s->stream_epoch=3;s->kernel_lease_fd=4;s->carrier_rate=192000;s->held_buffers=&first;
 owner=s;original=(struct device){.mode=mode,.sync=mode};replacement=(struct device){.mode=1};put_handle(&original);
 fixture_pointer_size=4;count=1;instance_size=792;library_ok=open_ok=allowed=1;
 refs=locks=fault=calls=cancel_at=replace_at=epoch_at=missing_symbol=open_count=close_count=0;
 iterator_status=GST_ITERATOR_DONE;channels=8;rate=192000;es=1;now=caps_at=0;
}
static void clean(DtsPass *s){restore_preparation(s,FALSE);assert(!s->preparation_sink && !s->preparation_started && refs==0 && locks==0 && open_count==close_count);}
static void run_tests(void){
 DtsPass s;
 for(int mode=0;mode<=2;mode++){
  reset(&s,mode);caps_at=200000;assert(prepare_owned_renderer(&s));assert(now==caps_at && original.sync==2);
  assert(prepare_owned_renderer(&s));assert(original.starts==1 && original.feeds==2);
  assert(restore_preparation(&s,TRUE));assert(original.mode==mode && original.sync==mode);clean(&s);
  assert(prepare_owned_renderer(&s));assert(original.starts==2);clean(&s);assert(original.mode==mode);
 }
 for(int f=1;f<=6;f++){
  reset(&s,1);fault=f;assert(!prepare_owned_renderer(&s));clean(&s);assert(original.mode==1);
 }
 for(int at=1;at<=5;at++){
  reset(&s,1);cancel_at=at;assert(!prepare_owned_renderer(&s));clean(&s);assert(original.mode==1 && original.feeds==(at==5));
  reset(&s,1);replace_at=at;assert(!prepare_owned_renderer(&s));clean(&s);assert(replacement.writes==0 && replacement.feeds==0);
  reset(&s,1);epoch_at=at;assert(!prepare_owned_renderer(&s));clean(&s);assert(original.mode==1);
 }
 reset(&s,1);assert(prepare_owned_renderer(&s));owner->user_paused=1;assert(!restore_preparation(&s,TRUE));assert(original.mode==1 && original.plays==1);clean(&s);
 reset(&s,1);assert(prepare_owned_renderer(&s));put_handle(&replacement);assert(!restore_preparation(&s,TRUE));assert(replacement.writes==0);clean(&s);
 reset(&s,1);for(int i=0;i<16;i++)assert(prepare_owned_renderer(&s));assert(!prepare_owned_renderer(&s));clean(&s);assert(original.feeds==16);
 for(int which=0;which<11;which++){
  reset(&s,1);
  switch(which){case 0:count=2;break;case 1:fixture_pointer_size=8;break;case 2:instance_size=791;break;
   case 3:library_ok=0;break;case 4:open_ok=0;break;case 5:missing_symbol=1;break;case 6:channels=2;break;
   case 7:rate=48000;break;case 8:es=0;break;case 9:allowed=0;break;case 10:iterator_status=2;break;}
  assert(!prepare_owned_renderer(&s));clean(&s);assert(original.writes==0 && original.feeds==0 && now<=DTS_PREPARATION_TIMEOUT_US);
 }
 reset(&s,1);count=0;assert(prepare_owned_renderer(&s));clean(&s);assert(!open_count);
 puts("PASS actual native preparation: caps wait, clock gate, restore, repeat, failure/cancel, epoch/handle/ABI/library identity, bounded neutral feed");
}
__attribute__((visibility("default"))) int c5_test_native_preparation(void){run_tests();return 0;}
int main(void){return c5_test_native_preparation();}
'''
parser=argparse.ArgumentParser();parser.add_argument('--emit',type=Path);args=parser.parse_args()
fixture=prefix+'\n'.join(functions)+suffix
if args.emit:
    args.emit.write_text(fixture)
else:
    with tempfile.TemporaryDirectory(prefix='c5-native-preparation-') as directory:
        path=Path(directory);(path/'test.c').write_text(fixture)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(path/'test.c'),'-o',str(path/'test')],check=True)
        subprocess.run([str(path/'test')],check=True)
