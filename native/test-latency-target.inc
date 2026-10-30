/* Isolated actual-GStreamer teardown regression, no media or kernel lease.
 * Append this fixture to a private copy of src/gstdtspassthrough.c and build
 * with build-plugin.sh for the target. Load that test-only library using
 * ctypes.CDLL in a fresh target Python process, call c5_test_latency_teardown,
 * and require return code 0 under an outer subprocess timeout. Do not install
 * the test library as a playback plugin. The production build omits this file.
 * No configuration or teardown may advertise synthetic preparation latency.
 */
static GstBusSyncReply teardown_probe_bus(GstBus *bus,GstMessage *message,gpointer data) {
  int *counts=data;(void)bus;
  if(GST_MESSAGE_TYPE(message)==GST_MESSAGE_LATENCY) {
    counts[0]++;
    if(counts[1])counts[2]++;
  }
  gst_message_unref(message);
  return GST_BUS_DROP;
}
__attribute__((visibility("default"))) int c5_test_latency_teardown(void) {
  int counts[3]={0,0,0};
  GstElement *element;GstBus *bus;
  gst_init(NULL,NULL);
  element=g_object_new(dts_pass_get_type(),NULL);
  bus=gst_bus_new();gst_element_set_bus(element,bus);
  gst_bus_set_sync_handler(bus,teardown_probe_bus,counts,NULL);
  if(gst_element_set_state(element,GST_STATE_PAUSED)==GST_STATE_CHANGE_FAILURE)return -1;
  counts[1]=1;
  if(gst_element_set_state(element,GST_STATE_READY)==GST_STATE_CHANGE_FAILURE)return -3;
  counts[1]=0;
  gst_element_set_state(element,GST_STATE_NULL);
  gst_element_set_bus(element,NULL);gst_bus_set_sync_handler(bus,NULL,NULL,NULL);
  gst_object_unref(bus);gst_object_unref(element);
  return counts[2];
}
