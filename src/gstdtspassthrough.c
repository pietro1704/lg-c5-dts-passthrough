/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Experimental transport bridge. It parses and packs DTS; it never decodes.
 * Rank NONE deliberately prevents automatic use by native applications.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <gst/gst.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/mem.h>
#include <libavutil/opt.h>
#include <string.h>
#include "native-session.h"
#include "ready-protocol.h"
#include "release-policy.h"

#ifndef DTS_NATIVE_SESSION
#define DTS_NATIVE_SESSION 0
#endif
#define DTS_PREPARATION_TIMEOUT_US (1500 * 1000)

typedef struct {
  GstElement parent;
  GstPad *sink, *src;
  AVCodecParserContext *parser;
  AVCodecContext *codec;
  AVFormatContext *mux;
  AVIOContext *io;
  GByteArray *encoded;
  GstEvent *segment;
  GstElement *native_sink;
  gchar *saved_app_type;
  gboolean saved_pcm_bypass, saved_pcm_multi_ch;
  gboolean need_segment, configured, hd, silent_probe, file_test;
  gint flushing, source_channels, carrier_rate, carrier_channels, profile;
  guint64 next_pts, frames, input_bytes;
  gint64 mux_pts;
  gint session_fd, kernel_lease_fd;
  gboolean session_ready, session_requested;
  guint ready_epoch;
  GQueue held_buffers;
  gsize held_bytes;
  GMutex file_lock;
  gint stream_epoch, user_paused, regate, lease_needs_resume, control_broken;
  struct dts_ready_parser reply;
  gint64 session_deadline;
  gint64 next_prepare;
  gboolean preparation_started;
  guint preparation_epoch, preparation_feeds;
  GstElement *preparation_sink;
  gpointer preparation_handle;
  gint preparation_lipsync;
} DtsPass;
typedef struct { GstElementClass parent; } DtsPassClass;
G_DEFINE_TYPE(DtsPass, dts_pass, GST_TYPE_ELEMENT)

static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-dts; audio/x-unknown, codec-id=(string)A_DTS"));
static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-raw, format=(string)S16LE, layout=(string)interleaved, "
                   "rate=(int)[1,384000], channels=(int){2,8}"));

static gboolean restore_preparation(DtsPass *s,gboolean apply);
static gboolean preparation_allowed(DtsPass *s,guint epoch);

static void end_native_session(DtsPass *s) {
  restore_preparation(s,FALSE);
  g_mutex_lock(&s->file_lock);
  if (s->kernel_lease_fd >= 0) {
    ioctl(s->kernel_lease_fd, C5_LEASE_REVOKE, 0);
    close(s->kernel_lease_fd); s->kernel_lease_fd = -1;
  }
  if (s->session_fd >= 0) { close(s->session_fd); s->session_fd = -1; }
  s->reply.used = 0;
  g_atomic_int_set(&s->control_broken,FALSE);
  g_mutex_unlock(&s->file_lock);
}

static gboolean bump_epoch(DtsPass *s) {
  gint value;
  do { value=g_atomic_int_get(&s->stream_epoch); if(value==G_MAXINT)return FALSE; }
  while(!g_atomic_int_compare_and_exchange(&s->stream_epoch,value,value+1));
  return TRUE;
}

static void suspend_session(DtsPass *s, gboolean advance) {
  gchar cancel[48];gint length;
  g_mutex_lock(&s->file_lock);
  if(s->kernel_lease_fd>=0)ioctl(s->kernel_lease_fd,C5_LEASE_SUSPEND,0);
  g_atomic_int_set(&s->lease_needs_resume,TRUE);
  if(advance && bump_epoch(s) && s->session_fd>=0) {
    length=g_snprintf(cancel,sizeof(cancel),"CANCEL %u\n",(guint)g_atomic_int_get(&s->stream_epoch));
    /* Cancellation is never allowed to wait for the peer. */
    if(send(s->session_fd,cancel,length,MSG_DONTWAIT|MSG_NOSIGNAL)!=length)
      g_atomic_int_set(&s->control_broken,TRUE);
  }
  g_atomic_int_set(&s->regate,TRUE);
  g_mutex_unlock(&s->file_lock);
}

static void sync_epoch(DtsPass *s) {
  if(g_atomic_int_compare_and_exchange(&s->regate,TRUE,FALSE)) {
    s->session_requested=FALSE;s->session_ready=FALSE;
  }
}

static void clear_transport(DtsPass *s) {
  restore_preparation(s,FALSE);
  while (!g_queue_is_empty(&s->held_buffers))
    gst_buffer_unref(g_queue_pop_head(&s->held_buffers));
  s->held_bytes = 0;
  suspend_session(s,TRUE);
  sync_epoch(s);
  if (s->native_sink) {
    g_object_set(s->native_sink, "pcm-bypass", s->saved_pcm_bypass,
        "pcm-multi-ch", s->saved_pcm_multi_ch, NULL);
    if (s->saved_app_type) {
      g_object_set(s->native_sink, "app-type", s->saved_app_type, NULL);
      g_clear_pointer(&s->saved_app_type, g_free);
    }
    gst_object_unref(s->native_sink);
    s->native_sink = NULL;
  }
  if (s->mux) {
    s->mux->pb = NULL;
    avformat_free_context(s->mux);
    s->mux = NULL;
  }
  if (s->io) {
    av_freep(&s->io->buffer);
    avio_context_free(&s->io);
  }
  s->configured = FALSE;
  s->session_ready = FALSE;
  g_byte_array_set_size(s->encoded, 0);
}

static void reset_stream(DtsPass *s) {
  clear_transport(s);
  if (s->parser) av_parser_close(s->parser);
  s->parser = NULL;
  avcodec_free_context(&s->codec);
  s->next_pts = GST_CLOCK_TIME_NONE;
  s->frames = 0;
  s->input_bytes = 0;
  s->need_segment = s->segment != NULL;
}

static gboolean ensure_parser(DtsPass *s) {
  if (s->parser && s->codec) return TRUE;
  s->parser = av_parser_init(AV_CODEC_ID_DTS);
  s->codec = avcodec_alloc_context3(NULL);
  if (!s->parser || !s->codec) return FALSE;
  s->codec->codec_id = AV_CODEC_ID_DTS;
  s->codec->codec_type = AVMEDIA_TYPE_AUDIO;
  return TRUE;
}

static int collect(void *opaque, uint8_t *data, int count) {
  DtsPass *s = opaque;
  if (count < 0 || count > 4 * 1024 * 1024 ||
      s->encoded->len > 4 * 1024 * 1024 - (guint)count)
    return AVERROR(ENOMEM);
  g_byte_array_append(s->encoded, data, count);
  return count;
}

static void probe_native_sink(DtsPass *s) {
  GstObject *root = gst_object_ref(s), *parent;
  GstIterator *it;
  GValue item = G_VALUE_INIT;
  gchar *requested = NULL;
  guint mask = 0;
  gboolean jp4k = g_file_test("/tmp/codex-dts-passthrough/probe-jp4k", G_FILE_TEST_EXISTS);
  while ((parent = gst_object_get_parent(root))) {
    gst_object_unref(root);
    root = parent;
  }
  if (!GST_IS_BIN(root)) { gst_object_unref(root); return; }
  if (g_file_get_contents("/tmp/codex-dts-passthrough/probe-sink-flags", &requested, NULL, NULL)) {
    mask = (guint)g_ascii_strtoull(requested, NULL, 10);
    g_free(requested);
  }
  it = gst_bin_iterate_recurse(GST_BIN(root));
  while (gst_iterator_next(it, &item) == GST_ITERATOR_OK) {
    GstElement *element = g_value_get_object(&item);
    if (g_object_class_find_property(G_OBJECT_GET_CLASS(element), "pcm-bypass")) {
      gboolean bypass, multi, mixer;
      guint index;
      gchar *line;
      g_object_get(element, "pcm-bypass", &bypass, "pcm-multi-ch", &multi,
          "mixer", &mixer, "index", &index, NULL);
      line = g_strdup_printf("sink=%s pcm-bypass=%d pcm-multi-ch=%d mixer=%d index=%u requested-mask=%u\n",
          GST_OBJECT_NAME(element), bypass, multi, mixer, index, mask);
      g_file_set_contents("/tmp/codex-dts-passthrough/sink-properties.log", line, -1, NULL);
      g_free(line);
      if ((mask > 0 && mask <= 3) || jp4k) {
        s->native_sink = gst_object_ref(element);
        s->saved_pcm_bypass = bypass;
        s->saved_pcm_multi_ch = multi;
        if (mask > 0 && mask <= 3)
          g_object_set(element, "pcm-bypass", (gboolean)((mask & 1) != 0),
              "pcm-multi-ch", (gboolean)((mask & 2) != 0), NULL);
        if (jp4k) {
          g_object_get(element, "app-type", &s->saved_app_type, NULL);
          g_object_set(element, "app-type", "JP4K", NULL);
        }
      }
      g_value_reset(&item);
      break;
    }
    g_value_reset(&item);
  }
  if (G_VALUE_TYPE(&item)) g_value_unset(&item);
  gst_iterator_free(it);
  gst_object_unref(root);
}

static gboolean configure(DtsPass *s, gboolean hd, gint rate, gint profile) {
  AVDictionary *options = NULL;
  AVStream *stream;
  GstCaps *caps;
  const gchar *label = hd
      ? (profile == FF_PROFILE_DTS_HD_MA ? "dts_hd_ma" : "dts_hd") : "dts";
  gchar *probe_label = NULL;
  gint carrier_rate = hd ? rate * 4 : rate;
  guint carrier_codec = 1;
  guint64 mask = hd ? G_GUINT64_CONSTANT(0xc3f) : G_GUINT64_CONSTANT(3);
  int result;
  if (rate <= 0 || carrier_rate > 384000 || (hd && rate * 16 > 768000))
    return FALSE;
  if (!hd && s->session_fd >= 0) end_native_session(s);
  clear_transport(s);
  if (hd && DTS_NATIVE_SESSION && !s->file_test && !s->silent_probe) {
    gint connection=-1,lease=-1,resumed=0;
    if(s->session_fd<0) {
      connection=session_open(&s->flushing);
      if(connection<0)return FALSE;
      lease=kernel_lease_open();
      if(lease<0){close(connection);return FALSE;}
    }
    /* A user pause may hold preroll, but FLUSH/READY always cancels it.
     * Recheck under the same mutex used by FLUSH_START before activation. */
    for(;;) {
      while(g_atomic_int_get(&s->user_paused) && !g_atomic_int_get(&s->flushing))g_usleep(10000);
      g_mutex_lock(&s->file_lock);
      if(g_atomic_int_get(&s->user_paused) && !g_atomic_int_get(&s->flushing)) {
        g_mutex_unlock(&s->file_lock);continue;
      }
      if(g_atomic_int_get(&s->flushing))resumed=-1;
      else if(connection>=0) {
        resumed=ioctl(lease,C5_LEASE_RESUME,(guint)g_atomic_int_get(&s->stream_epoch));
        if(!resumed){s->session_fd=connection;s->kernel_lease_fd=lease;connection=lease=-1;}
      } else if(g_atomic_int_get(&s->lease_needs_resume))
        resumed=ioctl(s->kernel_lease_fd,C5_LEASE_RESUME,(guint)g_atomic_int_get(&s->stream_epoch));
      if(!resumed)g_atomic_int_set(&s->lease_needs_resume,FALSE);
      g_mutex_unlock(&s->file_lock);break;
    }
    if(lease>=0)close(lease);
    if(connection>=0)close(connection);
    if(resumed){end_native_session(s);return FALSE;}
  }
  if(g_atomic_int_get(&s->stream_epoch)==G_MAXINT)return FALSE;
  if (hd && s->silent_probe && !s->file_test) probe_native_sink(s);
  s->hd = hd;
  s->profile = profile;
  s->mux_pts = 0;
  s->carrier_rate = carrier_rate;
  s->carrier_channels = hd ? 8 : 2;
  if (avformat_alloc_output_context2(&s->mux, NULL, "spdif", NULL) < 0 || !s->mux)
    return FALSE;
  s->io = avio_alloc_context(av_malloc(32768), 32768, 1, s, NULL, collect, NULL);
  if (!s->io || !s->io->buffer) return FALSE;
  s->mux->pb = s->io;
  s->mux->flags |= AVFMT_FLAG_CUSTOM_IO;
  stream = avformat_new_stream(s->mux, NULL);
  if (!stream) return FALSE;
  stream->codecpar->codec_type = AVMEDIA_TYPE_AUDIO;
  stream->codecpar->codec_id = AV_CODEC_ID_DTS;
  stream->codecpar->sample_rate = rate;
  stream->codecpar->channels = s->source_channels;
  stream->codecpar->profile = profile;
  stream->time_base = (AVRational){1, rate};
  if (hd) {
    av_dict_set_int(&options, "dtshd_rate", rate * 16, 0);
    av_dict_set_int(&options, "dtshd_fallback_time", 0, 0);
  }
  result = avformat_write_header(s->mux, &options);
  av_dict_free(&options);
  if (result < 0) return FALSE;
  if (hd && s->silent_probe) {
    gchar *probe = NULL;
    if (g_file_get_contents("/tmp/codex-dts-passthrough/probe-codec", &probe, NULL, NULL)) {
      guint64 requested = g_ascii_strtoull(probe, NULL, 10);
      if (requested == 5 || requested == 21 || requested == 23) carrier_codec = requested;
      g_free(probe);
    }
    if (g_file_get_contents("/tmp/codex-dts-passthrough/probe-label", &probe_label, NULL, NULL)) {
      g_strstrip(probe_label);
      if (g_str_equal(probe_label, "truehd") || g_str_equal(probe_label, "mat"))
        label = probe_label;
    }
  }
  caps = gst_caps_new_simple("audio/x-raw",
      "format", G_TYPE_STRING, "S16LE",
      "layout", G_TYPE_STRING, "interleaved",
      "rate", G_TYPE_INT, carrier_rate,
      "channels", G_TYPE_INT, s->carrier_channels,
      "channel-mask", GST_TYPE_BITMASK, mask,
      "es", G_TYPE_BOOLEAN, TRUE,
      "codec", G_TYPE_UINT, carrier_codec,
      "codec-str", G_TYPE_STRING, label,
      "data-rate", G_TYPE_INT, carrier_rate,
      "data-channels", G_TYPE_INT, s->carrier_channels,
      "data-bps", G_TYPE_INT, 16,
      NULL);
  g_free(probe_label);
  result = gst_pad_set_caps(s->src, caps);
  gst_caps_unref(caps);
  if (!result) return FALSE;
  s->configured = TRUE;
  s->need_segment = s->segment != NULL;
  return TRUE;
}

/* Verify EVERY byte, including the HD extension, before releasing a burst.
 * FFmpeg can silently fall back to core on overflow. Such a burst is rejected.
 */
static gboolean payload_is_complete(DtsPass *s, const uint8_t *packet, gint size) {
  const guint8 *out = s->encoded->data;
  guint offset = s->hd ? 20 : 8;
  gboolean original_le = size >= 4 &&
      ((packet[0] == 0xfe && packet[1] == 0x7f) ||
       (packet[0] == 0xff && packet[1] == 0x1f));
  guint i;
  gboolean preamble = s->encoded->len >= 8 &&
      out[0] == 0x72 && out[1] == 0xf8 && out[2] == 0x1f && out[3] == 0x4e;
  if (!s->hd && !preamble && s->encoded->len == (guint)size &&
      size == s->parser->duration * 4) {
    /* At full DTS-CD bitrate the frame fills the complete carrier period.
     * IEC packing deliberately omits its preamble rather than truncate DTS.
     */
    offset = 0;
  } else if (!preamble) {
    return FALSE;
  }
  if (s->encoded->len < offset + (guint)size) return FALSE;
  if (s->hd) {
    if ((out[4] & 0x1f) != 0x11 ||
        (((guint)out[19] << 8) | out[18]) != (guint)size) return FALSE;
  } else if (offset && ((out[4] & 0x1f) < 0x0b || (out[4] & 0x1f) > 0x0d)) {
    return FALSE;
  }
  for (i = 0; i < (guint)size; ++i) {
    guint at = original_le && !s->hd ? offset + i : (offset + i) ^ 1;
    if (at >= s->encoded->len || out[at] != packet[i]) return FALSE;
  }
  return TRUE;
}


/* All private ABI access is restricted to one exact sink in this pipeline,
 * after matching the firmware library, instance size, negotiated caps and the
 * current kernel lease. Neutral data bypasses GStreamer preroll, so the common
 * A/V clock and every original DTS buffer/PTS remain untouched until READY. */
static gboolean preparation_allowed(DtsPass *s,guint epoch) {
  gboolean ok;
  g_mutex_lock(&s->file_lock);
  ok=!g_atomic_int_get(&s->flushing) && !g_atomic_int_get(&s->user_paused) &&
     epoch==(guint)g_atomic_int_get(&s->stream_epoch) && s->kernel_lease_fd>=0 &&
     ioctl(s->kernel_lease_fd,C5_LEASE_CHECK,epoch)==0;
  g_mutex_unlock(&s->file_lock);
  if(ok && s->preparation_sink) {
    void *current=NULL;
    memcpy(&current,(char *)s->preparation_sink+0x200,sizeof(current));
    ok=current==s->preparation_handle && epoch==s->preparation_epoch;
  }
  return ok;
}

static gboolean restore_preparation(DtsPass *s,gboolean apply) {
  gboolean ok=TRUE;void *ao=NULL,*lib=NULL;
  if(!s->preparation_sink)return TRUE;
  GST_STATE_LOCK(s->preparation_sink);
  memcpy(&ao,(char *)s->preparation_sink+0x200,sizeof(ao));
  if(ao==s->preparation_handle) {
    lib=dlopen("/usr/lib/gstreamer-1.0/libgstlgaudiosink.so",RTLD_NOW|RTLD_NOLOAD);
    int (*set)(void *,int)=lib?(int (*)(void *,int))dlsym(lib,"lgao_set_lipsync_mode"):NULL;
    int (*play)(void *)=lib?(int (*)(void *))dlsym(lib,"lgao_play"):NULL;
    /* set restores only the saved context field. On cancellation/teardown do
     * not start playback; the platform owns stopping and destroying its sink. */
    ok=set && play && !set(ao,s->preparation_lipsync);
    if(ok && apply)ok=preparation_allowed(s,s->preparation_epoch) &&
        !play(ao) && preparation_allowed(s,s->preparation_epoch);
  } else ok=!apply; /* never dereference or overwrite a replaced handle */
  GST_STATE_UNLOCK(s->preparation_sink);
  if(lib)dlclose(lib);
  gst_object_unref(s->preparation_sink);
  s->preparation_sink=NULL;s->preparation_handle=NULL;
  s->preparation_started=FALSE;s->preparation_epoch=s->preparation_feeds=0;
  s->next_prepare=0;
  return ok;
}

static gboolean prepare_owned_renderer(DtsPass *s) {
  GstObject *root=gst_object_ref(s),*parent;
  GstIterator *it;GstIteratorResult status;
  GValue item=G_VALUE_INIT;GstElement *sink=NULL;
  GTypeQuery query={0};gchar *contents=NULL,*digest=NULL;gsize length=0;
  void *library=NULL,*ao=NULL;
  int (*start)(void *)=NULL;
  int (*play)(void *)=NULL;
  int (*feed)(void *,void *,unsigned int,unsigned long long,int)=NULL;
  int (*getmode)(void *,int *)=NULL;int (*setmode)(void *,int)=NULL;
  unsigned char silence[32768]={0};
  GstBuffer *first=g_queue_peek_head(&s->held_buffers);
  guint64 pts=first?GST_BUFFER_PTS(first):GST_CLOCK_TIME_NONE;
  guint count=0,epoch=(guint)g_atomic_int_get(&s->stream_epoch);
  GstPad *sinkpad=NULL;gboolean caps_ready=FALSE,result=FALSE;gint64 until;
  while((parent=gst_object_get_parent(root))){gst_object_unref(root);root=parent;}
  if(!GST_IS_BIN(root))goto out;
  it=gst_bin_iterate_recurse(GST_BIN(root));
  while((status=gst_iterator_next(it,&item))==GST_ITERATOR_OK) {
    GstElement *element=g_value_get_object(&item);
    if(!strcmp(G_OBJECT_TYPE_NAME(element),"GstLgAudioRender")) {
      count++;if(!sink)sink=gst_object_ref(element);
    }
    g_value_reset(&item);
  }
  if(G_VALUE_TYPE(&item))g_value_unset(&item);
  gst_iterator_free(it);
  if(status!=GST_ITERATOR_DONE)goto out; /* changing/ambiguous tree: no writes */
  if(!count){result=TRUE;goto out;} /* no vendor sink in isolated appsink tests */
  if(count!=1 || sizeof(void *)!=4)goto out;
  g_type_query(G_OBJECT_TYPE(sink),&query);
  if(query.instance_size!=0x318)goto out;
  if(!g_file_get_contents("/usr/lib/gstreamer-1.0/libgstlgaudiosink.so",&contents,&length,NULL))goto out;
  digest=g_compute_checksum_for_data(G_CHECKSUM_SHA256,(guchar *)contents,length);
  if(strcmp(digest,"90dba3ef32704ce4096bf1e6045d2879d0c2192d507bc04d1e8e98437f420a43"))goto out;
  library=dlopen("/usr/lib/gstreamer-1.0/libgstlgaudiosink.so",RTLD_NOW|RTLD_NOLOAD);
  if(!library)goto out;
  start=(int (*)(void *))dlsym(library,"lgao_start_renderer");
  feed=(int (*)(void *,void *,unsigned int,unsigned long long,int))dlsym(library,"lgao_feed");
  play=(int (*)(void *))dlsym(library,"lgao_play");
  getmode=(int (*)(void *,int *))dlsym(library,"lgao_get_lipsync_mode");
  setmode=(int (*)(void *,int))dlsym(library,"lgao_set_lipsync_mode");
  if(!start || !feed || !play || !getmode || !setmode)goto out;
  sinkpad=gst_element_get_static_pad(sink,"sink");
  if(!sinkpad)goto out;
  until=g_get_monotonic_time()+DTS_PREPARATION_TIMEOUT_US;
  do {
    GstCaps *caps=gst_pad_get_current_caps(sinkpad);
    if(caps && gst_caps_get_size(caps)==1) {
      const GstStructure *st=gst_caps_get_structure(caps,0);
      const gchar *codec=gst_structure_get_string(st,"codec-str");
      gint channels=0,rate=0;gboolean es=FALSE;
      gst_structure_get_int(st,"channels",&channels);gst_structure_get_int(st,"rate",&rate);
      gst_structure_get_boolean(st,"es",&es);
      caps_ready=codec && !strcmp(codec,"dts_hd_ma") && channels==8 && rate==s->carrier_rate && es;
    }
    if(caps)gst_caps_unref(caps);
    if(caps_ready || !preparation_allowed(s,epoch))break;
    g_usleep(10000);
  } while(g_get_monotonic_time()<until);
  if(!caps_ready)goto out;
  GST_STATE_LOCK(sink);
  if(preparation_allowed(s,epoch)) {
    memcpy(&ao,(char *)sink+0x200,sizeof(ao));
    if(ao && (!s->preparation_sink ||
       (s->preparation_sink==sink && s->preparation_handle==ao && s->preparation_epoch==epoch))) {
      if(!s->preparation_sink) {
        if(getmode(ao,&s->preparation_lipsync) || s->preparation_lipsync<0 || s->preparation_lipsync>2)goto unlock;
        s->preparation_sink=gst_object_ref(sink);s->preparation_handle=ao;s->preparation_epoch=epoch;
        if(!preparation_allowed(s,epoch) || setmode(ao,2))goto unlock;
      }
      if(!s->preparation_started) {
        if(!preparation_allowed(s,epoch) || play(ao) ||
           !preparation_allowed(s,epoch) || start(ao))goto unlock;
        s->preparation_started=TRUE;
      }
      if(s->preparation_feeds>=16 || !preparation_allowed(s,epoch))goto unlock;
      if(feed(ao,silence,sizeof(silence),pts,1)!=(int)sizeof(silence))goto unlock;
      s->preparation_feeds++;
      result=preparation_allowed(s,epoch);
    }
  }
unlock:
  GST_STATE_UNLOCK(sink);
out:
  if(sinkpad)gst_object_unref(sinkpad);
  if(library)dlclose(library);
  g_free(digest);g_free(contents);if(sink)gst_object_unref(sink);gst_object_unref(root);
  return result;
}

static GstFlowReturn request_route(DtsPass *s) {
  gchar request[48];gint length;ssize_t sent=0;
  sync_epoch(s);
  if(g_atomic_int_get(&s->control_broken)){end_native_session(s);return GST_FLOW_ERROR;}
  if(s->session_ready || s->session_requested || g_atomic_int_get(&s->user_paused))return GST_FLOW_OK;
  g_mutex_lock(&s->file_lock);
  if(g_atomic_int_get(&s->flushing)){g_mutex_unlock(&s->file_lock);return GST_FLOW_FLUSHING;}
  if(g_atomic_int_get(&s->user_paused)){g_mutex_unlock(&s->file_lock);return GST_FLOW_OK;}
  sync_epoch(s);
  length=g_snprintf(request,sizeof(request),"PREROLL %u\n",(guint)g_atomic_int_get(&s->stream_epoch));
  if(s->session_fd>=0)sent=send(s->session_fd,request,length,MSG_NOSIGNAL|MSG_DONTWAIT);
  if(sent==length){s->session_requested=TRUE;s->session_deadline=g_get_monotonic_time()+DTS_PREPARATION_TIMEOUT_US;}
  g_mutex_unlock(&s->file_lock);
  if(sent!=length){end_native_session(s);return GST_FLOW_ERROR;}
  return GST_FLOW_OK;
}

static GstFlowReturn native_receive_ready(DtsPass *s, gboolean wait) {
  guint processed = 0;
  do {
    unsigned char byte;
    ssize_t n;
    guint preparation_epoch=(guint)g_atomic_int_get(&s->stream_epoch);
    if (g_atomic_int_get(&s->flushing)) {restore_preparation(s,FALSE);return GST_FLOW_FLUSHING;}
    if (g_atomic_int_get(&s->user_paused)) {
      restore_preparation(s,FALSE);
      if(!wait)return GST_FLOW_OK;
      g_usleep(10000);continue;
    }
    if(request_route(s)!=GST_FLOW_OK)goto failed;
    /* Downstream preroll can block this thread past the preparation deadline
     * after the supervisor has already queued READY. Consume available replies
     * first; the deadline bounds waiting, not scheduling of a received proof.
     * native_drain still checks the current kernel lease before every buffer. */
    n = recv(s->session_fd, &byte, 1, MSG_DONTWAIT);
    if (n == 1) {
      guint expected_epoch=(guint)g_atomic_int_get(&s->stream_epoch);
      gint status = dts_ready_byte(&s->reply, byte, expected_epoch);
      if (status < 0) goto failed;
      if (status == 1) { if(!restore_preparation(s,TRUE))goto preparation_failed; s->ready_epoch=expected_epoch;s->session_ready = TRUE; return GST_FLOW_OK; }
      if (!wait && ++processed >= 64) return GST_FLOW_OK;
      continue;
    }
    if (n == 0 || (errno != EAGAIN && errno != EINTR)) goto failed;
    if (g_get_monotonic_time() >= s->session_deadline) goto failed;
    if(g_get_monotonic_time()>=s->next_prepare) {
      if(!prepare_owned_renderer(s))goto preparation_failed;
      s->next_prepare=g_get_monotonic_time()+100000;
    }
    if (!wait) return GST_FLOW_OK;
    g_usleep(10000);
    continue;
preparation_failed:
    restore_preparation(s,FALSE);
    if(g_atomic_int_get(&s->flushing))return GST_FLOW_FLUSHING;
    /* Preparation can race a user pause/seek after the loop's first guard.
     * Keep originals for a fresh epoch instead of reporting a decoder error. */
    if(g_atomic_int_get(&s->user_paused) ||
       preparation_epoch!=(guint)g_atomic_int_get(&s->stream_epoch)) {
      if(!wait)return GST_FLOW_OK;
      continue;
    }
    goto failed;
  } while (TRUE);
failed:
  end_native_session(s);
  GST_ELEMENT_ERROR(s, STREAM, FORMAT, ("DTS-HD session route was not confirmed"), (NULL));
  return GST_FLOW_ERROR;
}

static enum dts_release release_status(DtsPass *s) {
  guint checked_epoch;gint kernel_ok;struct pollfd peer;
  sync_epoch(s);
  if(g_atomic_int_get(&s->flushing))return DTS_RELEASE_FLUSH;
  if(g_atomic_int_get(&s->user_paused) || !s->session_ready)return DTS_RELEASE_HOLD;
  g_mutex_lock(&s->file_lock);
  checked_epoch=(guint)g_atomic_int_get(&s->stream_epoch);
  kernel_ok=s->kernel_lease_fd>=0 && ioctl(s->kernel_lease_fd,C5_LEASE_CHECK,checked_epoch)==0;
  peer.fd=s->session_fd;peer.events=POLLIN;peer.revents=0;
  g_mutex_unlock(&s->file_lock);
  gint peer_ok=peer.fd>=0 && poll(&peer,1,0)>=0 && !(peer.revents&(POLLHUP|POLLERR|POLLNVAL));
  return dts_release_policy(g_atomic_int_get(&s->flushing),g_atomic_int_get(&s->user_paused),
    g_atomic_int_get(&s->regate),s->session_ready,s->ready_epoch,
    (guint)g_atomic_int_get(&s->stream_epoch),checked_epoch,kernel_ok,peer_ok);
}

static GstFlowReturn native_drain(DtsPass *s) {
  while (!g_queue_is_empty(&s->held_buffers)) {
    enum dts_release permission=release_status(s);
    GstBuffer *buffer;GstFlowReturn result;
    if(permission==DTS_RELEASE_FLUSH)return GST_FLOW_FLUSHING;
    if(permission==DTS_RELEASE_HOLD){s->session_ready=FALSE;s->session_requested=FALSE;return GST_FLOW_OK;} /* retain originals for new READY */
    if(permission==DTS_RELEASE_ERROR){end_native_session(s);return GST_FLOW_ERROR;}
    buffer=g_queue_peek_head(&s->held_buffers);
    result=gst_pad_push(s->src,gst_buffer_ref(buffer));
    if(result!=GST_FLOW_OK)return result;
    g_queue_pop_head(&s->held_buffers);
    s->held_bytes-=gst_buffer_get_size(buffer);gst_buffer_unref(buffer);
  }
  return GST_FLOW_OK;
}

static GstFlowReturn push_frame(DtsPass *s, uint8_t *data, gint size) {
  gboolean hd = s->codec->profile == FF_PROFILE_DTS_HD_MA ||
      s->codec->profile == FF_PROFILE_DTS_HD_HRA;
  gint rate = s->codec->sample_rate;
  gint carrier_rate = hd ? rate * 4 : rate;
  AVPacket *packet;
  GstBuffer *buffer;
  GstClockTime pts, duration;
  gint result;
  if (g_atomic_int_get(&s->flushing)) return GST_FLOW_FLUSHING;
  sync_epoch(s);
  if (s->session_fd >= 0 && s->session_ready) {
    enum dts_release permission=release_status(s);
    if(permission==DTS_RELEASE_FLUSH)return GST_FLOW_FLUSHING;
    if(permission==DTS_RELEASE_HOLD){s->session_ready=FALSE;s->session_requested=FALSE;}
    if(permission==DTS_RELEASE_ERROR) {
      end_native_session(s);
      GST_ELEMENT_ERROR(s,STREAM,FORMAT,("DTS-HD session protection ended"),(NULL));
      return GST_FLOW_ERROR;
    }
  }
  if (hd && !s->file_test && !s->silent_probe && !DTS_NATIVE_SESSION) {
    GST_ELEMENT_ERROR(s, STREAM, FORMAT,
        ("DTS-HD native output routing is still under validation"),
        ("Refusing to send HBR payload through an unverified PCM route"));
    return GST_FLOW_ERROR;
  }
  if (s->codec->profile == FF_PROFILE_DTS_EXPRESS || s->parser->duration <= 0)
    goto unsupported;
  if (!s->configured || s->hd != hd || s->carrier_rate != carrier_rate ||
      s->profile != s->codec->profile ||
      (hd && DTS_NATIVE_SESSION && !s->file_test && !s->silent_probe &&
       (s->session_fd<0 || s->kernel_lease_fd<0))) {
    if (!configure(s, hd, rate, s->codec->profile)) goto unsupported;
  }
  if(hd && DTS_NATIVE_SESSION && !s->file_test && !s->silent_probe &&
      (s->session_fd<0 || s->kernel_lease_fd<0))return GST_FLOW_ERROR;
  packet = av_packet_alloc();
  if (!packet || av_new_packet(packet, size) < 0) {
    av_packet_free(&packet);
    return GST_FLOW_ERROR;
  }
  memcpy(packet->data, data, size);
  packet->stream_index = 0;
  packet->pts = packet->dts = s->mux_pts;
  packet->duration = s->parser->duration;
  s->mux_pts += s->parser->duration;
  g_byte_array_set_size(s->encoded, 0);
  result = av_write_frame(s->mux, packet);
  avio_flush(s->io);
  av_packet_free(&packet);
  if (result < 0 || s->io->error < 0 ||
      !payload_is_complete(s, data, size)) {
    GST_ELEMENT_ERROR(s, STREAM, FORMAT,
        ("DTS payload was not preserved; refusing a core-only fallback"),
        ("profile=%d input=%d packed=%u", s->codec->profile, size, s->encoded->len));
    return GST_FLOW_ERROR;
  }
  if (s->need_segment) {
    if (!gst_pad_push_event(s->src, gst_event_ref(s->segment)))
      return GST_FLOW_ERROR;
    s->need_segment = FALSE;
  }
  pts = s->parser->pts == AV_NOPTS_VALUE
      ? s->next_pts : (guint64)s->parser->pts;
  duration = gst_util_uint64_scale(s->encoded->len, GST_SECOND,
      (guint64)s->carrier_rate * s->carrier_channels * 2);
  buffer = gst_buffer_new_allocate(NULL, s->encoded->len, NULL);
  if (!buffer) return GST_FLOW_ERROR;
  gst_buffer_fill(buffer, 0, s->encoded->data, s->encoded->len);
  if (s->silent_probe &&
      !g_file_test("/tmp/codex-dts-passthrough/RELEASE_PROBE", G_FILE_TEST_EXISTS))
    gst_buffer_memset(buffer, 0, 0, s->encoded->len);
  /* C5 ES consumes BE16 carrier words. The LE mux output was checked
   * against the entire original DTS packet above, including the MA extension.
   * Change only transport byte order in this explicitly enabled ES probe. */
  if (hd && s->silent_probe &&
      g_file_test("/tmp/codex-dts-passthrough/probe-es-buffers", G_FILE_TEST_EXISTS) &&
      g_file_test("/tmp/codex-dts-passthrough/probe-es-be", G_FILE_TEST_EXISTS)) {
    GstMapInfo map = GST_MAP_INFO_INIT;
    gsize i;
    if ((s->encoded->len & 1) || !gst_buffer_map(buffer, &map, GST_MAP_WRITE)) {
      gst_buffer_unref(buffer);
      return GST_FLOW_ERROR;
    }
    for (i = 0; i < map.size; i += 2) {
      guint8 first = map.data[i];
      map.data[i] = map.data[i + 1];
      map.data[i + 1] = first;
    }
    gst_buffer_unmap(buffer, &map);
  }
  if (hd && s->session_fd >= 0) {
    GstMapInfo map = GST_MAP_INFO_INIT;
    gsize i;
    if ((s->encoded->len & 1) || !gst_buffer_map(buffer, &map, GST_MAP_WRITE)) {
      gst_buffer_unref(buffer); return GST_FLOW_ERROR;
    }
    for (i = 0; i < map.size; i += 2) {
      guint8 byte = map.data[i]; map.data[i] = map.data[i+1]; map.data[i+1] = byte;
    }
    gst_buffer_unmap(buffer, &map);
    GST_BUFFER_FLAG_SET(buffer, (GstBufferFlags)(1u << 21));
  }
  GST_BUFFER_PTS(buffer) = pts;
  GST_BUFFER_DURATION(buffer) = duration;
  /* This exact LG sink routes bit 21 to LGAO_DATA_ES. Caps "es=true"
   * alone does not select its compressed-data appsrc; unmarked buffers
   * go through the PCM resampler. Diagnostic-only until hardware proven.
   */
  if (hd && s->silent_probe &&
      g_file_test("/tmp/codex-dts-passthrough/probe-es-buffers", G_FILE_TEST_EXISTS))
    GST_BUFFER_FLAG_SET(buffer, (GstBufferFlags)(1u << 21));
  if (s->frames == 0) GST_BUFFER_FLAG_SET(buffer, GST_BUFFER_FLAG_DISCONT);
  if (GST_CLOCK_TIME_IS_VALID(pts)) s->next_pts = pts + duration;
  ++s->frames;
  if (hd && s->session_fd >= 0 && !s->session_ready) {
    if (s->held_bytes + gst_buffer_get_size(buffer) > 32 * 1024 * 1024) {
      gst_buffer_unref(buffer); return GST_FLOW_ERROR;
    }
    g_queue_push_tail(&s->held_buffers, buffer);
    s->held_bytes += gst_buffer_get_size(buffer);
    /* Prepare the owned renderer with neutral data outside GStreamer preroll.
     * READY restores the original lipsync mode before releasing any originals.
     * Never add latency or change the shared A/V clock or original PTS. */
    result = native_receive_ready(s, TRUE);
    if (result != GST_FLOW_OK) return result;
    return s->session_ready ? native_drain(s) : GST_FLOW_OK;
  }
  return gst_pad_push(s->src, buffer);
unsupported:
  if(g_atomic_int_get(&s->flushing))return GST_FLOW_FLUSHING;
  GST_ELEMENT_ERROR(s, STREAM, FORMAT,
      ("Unsupported DTS transport; audio will not be decoded or downmixed"),
      ("profile=%d sample-rate=%d", s->codec->profile, rate));
  return GST_FLOW_ERROR;
}

static GstFlowReturn finish_flow(DtsPass *s,GstFlowReturn flow) {
  if(flow<0 && flow!=GST_FLOW_FLUSHING) {
    end_native_session(s);
    while(!g_queue_is_empty(&s->held_buffers))gst_buffer_unref(g_queue_pop_head(&s->held_buffers));
    s->held_bytes=0;
  }
  return flow;
}

static GstFlowReturn chain(GstPad *pad, GstObject *parent, GstBuffer *buffer) {
  DtsPass *s = (DtsPass *)parent;
  GstMapInfo map;
  uint8_t *padded;
  gint left;
  guint offset = 0;
  int64_t pts;
  GstFlowReturn flow = GST_FLOW_OK;
  (void)pad;
  if (g_atomic_int_get(&s->flushing)) {
    gst_buffer_unref(buffer);
    return GST_FLOW_FLUSHING;
  }
  if (GST_BUFFER_FLAG_IS_SET(buffer, GST_BUFFER_FLAG_DISCONT)) reset_stream(s);
  if (!ensure_parser(s) || !gst_buffer_map(buffer, &map, GST_MAP_READ)) {
    gst_buffer_unref(buffer);
    return GST_FLOW_ERROR;
  }
  if (map.size > 16 * 1024 * 1024) {
    gst_buffer_unmap(buffer, &map);
    gst_buffer_unref(buffer);
    return GST_FLOW_ERROR;
  }
  padded = av_mallocz(map.size + AV_INPUT_BUFFER_PADDING_SIZE);
  if (!padded) {
    gst_buffer_unmap(buffer, &map);
    gst_buffer_unref(buffer);
    return GST_FLOW_ERROR;
  }
  memcpy(padded, map.data, map.size);
  left = map.size;
  s->input_bytes += map.size;
  gst_buffer_unmap(buffer, &map);
  pts = GST_BUFFER_PTS_IS_VALID(buffer) ? (int64_t)GST_BUFFER_PTS(buffer) : AV_NOPTS_VALUE;
  /* The parser can omit the first timestamp when accumulating split packets.
   * Preserve the valid input origin instead of disabling sink clock sync. */
  if(!GST_CLOCK_TIME_IS_VALID(s->next_pts) && pts!=AV_NOPTS_VALUE)
    s->next_pts=(guint64)pts;
  while (left > 0 && flow == GST_FLOW_OK) {
    uint8_t *frame = NULL;
    gint frame_size = 0, consumed;
    s->codec->profile = FF_PROFILE_UNKNOWN;
    s->codec->sample_rate = 0;
    consumed = av_parser_parse2(s->parser, s->codec, &frame, &frame_size,
        padded + offset, left, pts, AV_NOPTS_VALUE, -1);
    pts = AV_NOPTS_VALUE;
    if (consumed < 0 || (!consumed && !frame_size)) {
      flow = GST_FLOW_ERROR;
      break;
    }
    if (frame_size) flow = push_frame(s, frame, frame_size);
    left -= consumed;
    offset += consumed;
  }
  av_free(padded);
  gst_buffer_unref(buffer);
  return finish_flow(s,flow);
}

static gboolean event(GstPad *pad, GstObject *parent, GstEvent *e) {
  DtsPass *s = (DtsPass *)parent;
  switch (GST_EVENT_TYPE(e)) {
    case GST_EVENT_STREAM_START:
      reset_stream(s);
      gst_event_replace(&s->segment, NULL);
      g_atomic_int_set(&s->flushing, FALSE);
      break;
    case GST_EVENT_CAPS: {
      GstCaps *caps;
      gst_event_parse_caps(e, &caps);
      reset_stream(s);
      gst_structure_get_int(gst_caps_get_structure(caps, 0),
          "channels", &s->source_channels);
      gst_event_unref(e);
      return TRUE;
    }
    case GST_EVENT_SEGMENT:
      gst_event_replace(&s->segment, e);
      s->need_segment = TRUE;
      gst_event_unref(e);
      return TRUE;
    case GST_EVENT_FLUSH_START:
      g_atomic_int_set(&s->flushing, TRUE);
      suspend_session(s,FALSE);
      break;
    case GST_EVENT_FLUSH_STOP:
      GST_PAD_STREAM_LOCK(pad);
      reset_stream(s);
      g_atomic_int_set(&s->flushing, FALSE);
      GST_PAD_STREAM_UNLOCK(pad);
      break;
    case GST_EVENT_EOS:
      if (s->parser && s->codec) {
        uint8_t *frame = NULL;
        gint size = 0;
        s->codec->profile = FF_PROFILE_UNKNOWN;
        s->codec->sample_rate = 0;
        av_parser_parse2(s->parser, s->codec, &frame, &size, NULL, 0,
            AV_NOPTS_VALUE, AV_NOPTS_VALUE, -1);
        if (size && finish_flow(s,push_frame(s, frame, size)) != GST_FLOW_OK) {
          gst_event_unref(e);
          return FALSE;
        }
      }
      while (!g_queue_is_empty(&s->held_buffers)) {
        sync_epoch(s);
        if ((!s->session_ready && finish_flow(s,native_receive_ready(s,TRUE))!=GST_FLOW_OK) ||
            finish_flow(s,native_drain(s))!=GST_FLOW_OK) {
          gst_event_unref(e);return FALSE;
        }
        if(!g_queue_is_empty(&s->held_buffers))g_usleep(10000);
      }
      if (s->input_bytes && !s->frames) {
        GST_ELEMENT_ERROR(s, STREAM, FORMAT, ("No complete valid DTS frames"), (NULL));
        gst_event_unref(e);
        return FALSE;
      }
      break;
    default: break;
  }
  return gst_pad_push_event(s->src, e);
}

static GstStateChangeReturn change_state(GstElement *element, GstStateChange transition) {
  DtsPass *s=(DtsPass *)element;
  GstStateChangeReturn result;
  if(transition==GST_STATE_CHANGE_PAUSED_TO_READY) {
    g_atomic_int_set(&s->flushing,TRUE);
    g_atomic_int_set(&s->user_paused,FALSE);
    suspend_session(s,FALSE);
  }
  if(transition==GST_STATE_CHANGE_PAUSED_TO_PLAYING && g_atomic_int_get(&s->user_paused)) {
    gint resumed=0;gboolean activated=FALSE;
    g_mutex_lock(&s->file_lock);
    if(!bump_epoch(s))resumed=-1;
    else if(s->kernel_lease_fd>=0 && !g_atomic_int_get(&s->flushing)) {
      resumed=ioctl(s->kernel_lease_fd,C5_LEASE_RESUME,(guint)g_atomic_int_get(&s->stream_epoch));
      activated=resumed==0;
    }
    if(!resumed) {
      if(activated)g_atomic_int_set(&s->lease_needs_resume,FALSE);
      g_atomic_int_set(&s->regate,TRUE);
      g_atomic_int_set(&s->user_paused,FALSE);
    }
    g_mutex_unlock(&s->file_lock);
    if(resumed)return GST_STATE_CHANGE_FAILURE;
  }
  result=GST_ELEMENT_CLASS(dts_pass_parent_class)->change_state(element,transition);
  if(result!=GST_STATE_CHANGE_FAILURE && transition==GST_STATE_CHANGE_PLAYING_TO_PAUSED) {
    g_atomic_int_set(&s->user_paused,TRUE);
    suspend_session(s,TRUE);
  }
  if(result!=GST_STATE_CHANGE_FAILURE && transition==GST_STATE_CHANGE_PAUSED_TO_READY) {
    reset_stream(s);end_native_session(s);
    gst_event_replace(&s->segment,NULL);
  }
  return result;
}

static void finalize(GObject *object) {
  DtsPass *s = (DtsPass *)object;
  reset_stream(s);
  end_native_session(s);
  gst_event_replace(&s->segment, NULL);
  g_byte_array_unref(s->encoded);
  g_mutex_clear(&s->file_lock);
  G_OBJECT_CLASS(dts_pass_parent_class)->finalize(object);
}

static void dts_pass_class_init(DtsPassClass *klass) {
  GstElementClass *element = GST_ELEMENT_CLASS(klass);
  G_OBJECT_CLASS(klass)->finalize = finalize;
  element->change_state = change_state;
  gst_element_class_set_static_metadata(element, "DTS bitstream transport",
      "Codec/Decoder/Audio", "Preserve DTS frames in IEC 61937/HBR; no decoding",
      "Experimental LG C5 investigation");
  gst_element_class_add_static_pad_template(element, &sink_template);
  gst_element_class_add_static_pad_template(element, &src_template);
}

static void dts_pass_init(DtsPass *s) {
  g_mutex_init(&s->file_lock);
  s->sink = gst_pad_new_from_static_template(&sink_template, "sink");
  s->src = gst_pad_new_from_static_template(&src_template, "src");
  gst_pad_set_chain_function(s->sink, chain);
  gst_pad_set_event_function(s->sink, event);
  gst_element_add_pad(GST_ELEMENT(s), s->sink);
  gst_element_add_pad(GST_ELEMENT(s), s->src);
  s->encoded = g_byte_array_new();
  s->source_channels = 2;
  s->session_fd = s->kernel_lease_fd = -1;
  s->file_test = !DTS_NATIVE_SESSION && g_strcmp0(g_getenv("DTS_PASSTHROUGH_FILE_TEST"), "1") == 0;
  s->silent_probe = !DTS_NATIVE_SESSION && g_file_test("/tmp/codex-dts-passthrough/SILENT_PROBE", G_FILE_TEST_EXISTS);
  if (s->silent_probe)
    GST_WARNING_OBJECT(s, "[DTS-PROBE] Output payload replaced with digital silence for routing test");
  s->next_pts = GST_CLOCK_TIME_NONE;
}

static gboolean plugin_init(GstPlugin *plugin) {
  return gst_element_register(plugin, "dtspassthrough", DTS_NATIVE_SESSION ? 310 : GST_RANK_NONE, dts_pass_get_type());
}

#ifndef PACKAGE
#define PACKAGE "lg-c5-dts-passthrough-experiment"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, dtspassthrough,
    "Experimental DTS passthrough transport", plugin_init, "0.0.1",
    "LGPL", PACKAGE, "https://github.com/lgstreamer/dts_restore")
