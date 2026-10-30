/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef C5_HDMI_INPUT_POLICY_H
#define C5_HDMI_INPUT_POLICY_H
enum c5_bind_result {C5_BIND_OK,C5_BIND_ACTOR,C5_BIND_STALE,C5_BIND_SOURCE,C5_BIND_BUSY,C5_BIND_OVERFLOW};
static inline enum c5_bind_result c5_hdmi_bind_check(unsigned int expected,
 unsigned int generation,unsigned long next,unsigned long live_input,
 unsigned long previous,int invalidated,int actor,int source)
{
 if(!actor)return C5_BIND_ACTOR;
 if(expected!=generation)return C5_BIND_STALE;
 if(!next || next!=live_input || !source)return C5_BIND_SOURCE;
 if(previous && previous!=next && !invalidated)return C5_BIND_BUSY;
 if(generation==~0u)return C5_BIND_OVERFLOW;
 return C5_BIND_OK;
}
#endif
