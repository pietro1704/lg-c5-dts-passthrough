/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef C5_HDMI_ROUTE_POLICY_H
#define C5_HDMI_ROUTE_POLICY_H
static inline int c5_hdmi_route_matches(unsigned int index,unsigned int mask,
 unsigned int type,unsigned int codec,unsigned int open,unsigned int started,
 unsigned int main_input,unsigned int main_port,int owner_alive,int enabled)
{
 return enabled && owner_alive && index==1 && mask==0x20 && type==3 && codec==48 &&
  open==1 && started==1 && main_input==1 && main_port==1;
}
#endif
