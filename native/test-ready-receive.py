"""Exercise the plugin's actual READY receive loop with real Unix sockets.

The renderer may block the streaming thread while READY is already queued.
Only GStreamer plumbing, the monotonic clock and the already-sent request are
stubbed; receive/parsing/deadline/cancellation code comes from the plugin.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
source = (root / 'src/gstdtspassthrough.c').read_text()
start = source.index('static GstFlowReturn native_receive_ready(')
end = source.index('\n}\n', start) + 3
function = source[start:end]
entry_start = source.index('    /* Prepare the owned renderer')
entry_end = source.index('\n  }', entry_start)
entry = 'static GstFlowReturn native_entry(DtsPass *s) {\n GstFlowReturn result;\n' + source[entry_start:entry_end] + '\n}\n'
prefix = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>
#include "ready-protocol.h"
typedef unsigned int guint;
typedef int gint, gboolean, GstFlowReturn;
typedef long long gint64;
#define TRUE 1
#define FALSE 0
#define GST_FLOW_OK 0
#define GST_FLOW_ERROR -1
#define GST_FLOW_FLUSHING -2
#define GST_ELEMENT_ERROR(...) ((void)0)
typedef struct {
 int flushing,user_paused,session_fd,session_ready,session_requested,stream_epoch,ready_epoch;
 long long session_deadline,next_prepare;
 struct dts_ready_parser reply;
} DtsPass;
static long long clock_now;
static int closed,restore_error,prepare_error,prepare_race,restore_race,wait_peer;
static DtsPass *waiting_state;
static int g_atomic_int_get(int *p){return *p;}
static long long g_get_monotonic_time(void){return clock_now;}
static void g_usleep(int n){
 clock_now+=n;
 if(waiting_state){waiting_state->user_paused=0;waiting_state->stream_epoch++;
  assert(write(wait_peer,"READY 4\n",8)==8);waiting_state=NULL;}
}
static int restore_preparation(DtsPass *s,int apply){
 if(apply && restore_race==1)s->user_paused=1;
 if(apply && restore_race==2)s->stream_epoch++;
 if(apply && restore_race==3)s->flushing=1;
 return !restore_error && !(apply && restore_race);
}
static int prepare_owned_renderer(DtsPass *s){
 if(prepare_race==1)s->user_paused=1;
 if(prepare_race==2)s->stream_epoch++;
 if(prepare_race==3)s->flushing=1;
 return !prepare_error && !prepare_race && !s->user_paused && !s->flushing;
}
static GstFlowReturn native_drain(DtsPass *s){assert(s->session_ready && !s->user_paused);return GST_FLOW_OK;}
static GstFlowReturn request_route(DtsPass *s){(void)s;return GST_FLOW_OK;}
static void end_native_session(DtsPass *s){closed++;close(s->session_fd);s->session_fd=-1;}
'''
suffix = r'''
static void check(const char *message, int expired, int flushing, int paused,
                  int peer_closed, int expected, int ready) {
 int pair[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0);
 DtsPass s={.session_fd=pair[0],.stream_epoch=3,.session_deadline=100,
            .flushing=flushing,.user_paused=paused};
 clock_now=expired?101:50;closed=0;
 if(message)assert(write(pair[1],message,strlen(message))==(ssize_t)strlen(message));
 if(peer_closed)close(pair[1]);
 assert(native_receive_ready(&s,FALSE)==expected);
 assert(s.session_ready==ready);
 if(ready)assert(s.ready_epoch==3);
 assert(closed==(expected==GST_FLOW_ERROR));
 if(s.session_fd>=0)close(s.session_fd);
 if(!peer_closed)close(pair[1]);
}
int main(void) {
 /* Actual first-buffer entry: an already-paused producer must enter the wait
  * loop, never bypass its pause guard and mistake preparation denial for failure. */
 int pair[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0);
 DtsPass paused={.session_fd=pair[0],.stream_epoch=3,.session_deadline=1500000,.user_paused=1};
 clock_now=0;closed=0;waiting_state=&paused;wait_peer=pair[1];
 assert(native_entry(&paused)==GST_FLOW_OK && paused.ready_epoch==4 && !closed);
 close(pair[0]);close(pair[1]);
 /* READY arrived while downstream blocked the stream past its deadline. */
 check("READY 3\n",1,0,0,0,GST_FLOW_OK,1);
 check("READY 3\n",0,0,0,0,GST_FLOW_OK,1);
 check(NULL,1,0,0,0,GST_FLOW_ERROR,0);
 check(NULL,0,0,0,0,GST_FLOW_OK,0);
 check("READY 2\n",1,0,0,0,GST_FLOW_ERROR,0);
 check("READY 2\nREADY 3\n",1,0,0,0,GST_FLOW_OK,1);
 check("READY 4\n",0,0,0,0,GST_FLOW_ERROR,0);
 check("READY 3",1,0,0,0,GST_FLOW_ERROR,0);
 check("INVALID\n",0,0,0,0,GST_FLOW_ERROR,0);
 check("READY 3\n",1,1,0,0,GST_FLOW_FLUSHING,0);
 check("READY 3\n",1,0,1,0,GST_FLOW_OK,0);
 check(NULL,0,0,0,1,GST_FLOW_ERROR,0);
 restore_error=1;check("READY 3\n",0,0,0,0,GST_FLOW_ERROR,0);restore_error=0;
 prepare_error=1;check(NULL,0,0,0,0,GST_FLOW_ERROR,0);prepare_error=0;
 /* A user pause/seek can land inside preparation after the loop's guard.
  * Retain originals and wait for a fresh epoch; cancellation is not decode failure. */
 prepare_race=1;check(NULL,0,0,0,0,GST_FLOW_OK,0);
 prepare_race=2;check(NULL,0,0,0,0,GST_FLOW_OK,0);
 prepare_race=3;check(NULL,0,0,0,0,GST_FLOW_FLUSHING,0);prepare_race=0;
 restore_race=1;check("READY 3\n",0,0,0,0,GST_FLOW_OK,0);
 restore_race=2;check("READY 3\n",0,0,0,0,GST_FLOW_OK,0);
 restore_race=3;check("READY 3\n",0,0,0,0,GST_FLOW_FLUSHING,0);restore_race=0;
 puts("PASS real READY receive loop: queued reply, deadline, epoch, flush, pause, disconnect");
}
'''
with tempfile.TemporaryDirectory(prefix='c5-ready-receive-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(prefix + function + entry + suffix)
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I', str(root / 'src'),
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
