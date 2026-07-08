/* stdin/stdout harness so pytest can drive detector.c on the host.
 * Protocol: see tests/test_c_detector.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "detector.h"

int main(void)
{
    detector_t det;
    det_init(&det);
    char line[128];
    while (fgets(line, sizeof line, stdin))
    {
        if (line[0] == 'U')
        {
            det_counts_t c;
            if (sscanf(line + 1, "%u %u %u %u",
                       &c.sfdd, &c.phe, &c.crcb, &c.crcg) == 4)
            {
                det_update(&det, &c);
            }
        }
        else if (line[0] == 'E')
        {
            char status[32], chan[16], pcode[16];
            if (sscanf(line + 1, "%31s %15s %15s", status, chan, pcode) == 3)
            {
                char buf[DET_PAYLOAD_MAX];
                int ch = strcmp(chan, "null") ? atoi(chan) : -1;
                int pc = strcmp(pcode, "null") ? atoi(pcode) : -1;
                det_encode(&det, status, ch, pc, buf, sizeof buf);
                puts(buf);
            }
        }
    }
    return 0;
}
