/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Experimental transport bridge. It parses and packs DTS; it never decodes.
 * Rank NONE deliberately prevents automatic use by native applications.
 */
#include <gst/gst.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/mem.h>
#include <libavutil/opt.h>
#include <string.h>

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

static void clear_transport(DtsPass *s) {
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
  clear_transport(s);
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
  if (hd && !s->file_test && !s->silent_probe) {
    GST_ELEMENT_ERROR(s, STREAM, FORMAT,
        ("DTS-HD native output routing is still under validation"),
        ("Refusing to send HBR payload through an unverified PCM route"));
    return GST_FLOW_ERROR;
  }
  if (s->codec->profile == FF_PROFILE_DTS_EXPRESS || s->parser->duration <= 0)
    goto unsupported;
  if (!s->configured || s->hd != hd || s->carrier_rate != carrier_rate ||
      s->profile != s->codec->profile) {
    if (!configure(s, hd, rate, s->codec->profile)) goto unsupported;
  }
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
  return gst_pad_push(s->src, buffer);
unsupported:
  GST_ELEMENT_ERROR(s, STREAM, FORMAT,
      ("Unsupported DTS transport; audio will not be decoded or downmixed"),
      ("profile=%d sample-rate=%d", s->codec->profile, rate));
  return GST_FLOW_ERROR;
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
  return flow;
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
        if (size && push_frame(s, frame, size) != GST_FLOW_OK) {
          gst_event_unref(e);
          return FALSE;
        }
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
  GstStateChangeReturn result =
      GST_ELEMENT_CLASS(dts_pass_parent_class)->change_state(element, transition);
  if (result != GST_STATE_CHANGE_FAILURE && transition == GST_STATE_CHANGE_PAUSED_TO_READY) {
    reset_stream((DtsPass *)element);
    gst_event_replace(&((DtsPass *)element)->segment, NULL);
  }
  return result;
}

static void finalize(GObject *object) {
  DtsPass *s = (DtsPass *)object;
  reset_stream(s);
  gst_event_replace(&s->segment, NULL);
  g_byte_array_unref(s->encoded);
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
  s->sink = gst_pad_new_from_static_template(&sink_template, "sink");
  s->src = gst_pad_new_from_static_template(&src_template, "src");
  gst_pad_set_chain_function(s->sink, chain);
  gst_pad_set_event_function(s->sink, event);
  gst_element_add_pad(GST_ELEMENT(s), s->sink);
  gst_element_add_pad(GST_ELEMENT(s), s->src);
  s->encoded = g_byte_array_new();
  s->source_channels = 2;
  s->file_test = g_strcmp0(g_getenv("DTS_PASSTHROUGH_FILE_TEST"), "1") == 0;
  s->silent_probe = g_file_test("/tmp/codex-dts-passthrough/SILENT_PROBE", G_FILE_TEST_EXISTS);
  if (s->silent_probe)
    GST_WARNING_OBJECT(s, "[DTS-PROBE] Output payload replaced with digital silence for routing test");
  s->next_pts = GST_CLOCK_TIME_NONE;
}

static gboolean plugin_init(GstPlugin *plugin) {
  return gst_element_register(plugin, "dtspassthrough", GST_RANK_NONE, dts_pass_get_type());
}

#ifndef PACKAGE
#define PACKAGE "lg-c5-dts-passthrough-experiment"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, dtspassthrough,
    "Experimental DTS passthrough transport", plugin_init, "0.0.1",
    "LGPL", PACKAGE, "https://github.com/lgstreamer/dts_restore")
