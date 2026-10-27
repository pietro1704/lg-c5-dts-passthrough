#include <assert.h>
#include <stdio.h>
#include "../src/ready-protocol.h"
static int feed(struct dts_ready_parser *p,const char *s,unsigned long long epoch) {
 int result=0;
 for(;*s;s++){result=dts_ready_byte(p,*s,epoch);if(result)return result;}
 return result;
}
int main(void) {
 struct dts_ready_parser p={0};
 assert(feed(&p,"REA",1)==0);
 assert(feed(&p,"DY 1\n",1)==1);
 assert(feed(&p,"READY 1\n",2)==0);
 assert(feed(&p,"READY 2\n",2)==1);
 assert(feed(&p,"READY ",2)==0);
 assert(feed(&p,"2\n",3)==0); /* flush during a fragmented old reply */
 assert(feed(&p,"READY 3\n",3)==1);
 const char *bad[]={"READY 4\n","READY 0\n","READY 03\n","READY -1\n","READY +3\n","READY 3 x\n","READY\n","ERROR 3\n","READY 18446744073709551616\n"};
 for(unsigned int i=0;i<sizeof(bad)/sizeof(*bad);i++){p.used=0;assert(feed(&p,bad[i],3)==-1);}
 p.used=0;assert(feed(&p,"READY 18446744073709551615\n",ULLONG_MAX)==1);
 p.used=0;for(int i=0;i<47;i++)assert(dts_ready_byte(&p,'A',1)==0);
 assert(dts_ready_byte(&p,'A',1)==-1);
 p.used=0;assert(dts_ready_byte(&p,0,1)==-1);
 puts("PASS 66 assertions: fragmented reply, seek epoch, stale/future identity, malformed and overflow");
}
