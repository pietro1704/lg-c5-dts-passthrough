/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef C5_HDMI_GATE_POLICY_H
#define C5_HDMI_GATE_POLICY_H
static inline int c5_hdmi_gate_matches(unsigned long caller, unsigned int index,
 unsigned int codec, unsigned int main_input, unsigned int main_port,
 unsigned int expected_pid, unsigned int actual_pid, int active)
{
 return active && caller==0xffffffc010cce210UL && index==1 && codec==48 &&
  main_input==1 && main_port==1 && expected_pid && expected_pid==actual_pid;
}
#endif
