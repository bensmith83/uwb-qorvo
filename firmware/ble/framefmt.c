/* See framefmt.h. Pure logic (host-testable): stdio only, no SDK. */

#include "framefmt.h"

#include <stdio.h>

/* "-79.50" from hundredths (-7950), sign kept even when |v| < 100 */
static int fmt100(char *out, int v)
{
    unsigned a = (unsigned)(v < 0 ? -v : v);
    return sprintf(out, "%s%u.%02u", v < 0 ? "-" : "", a / 100, a % 100);
}

int frame_encode(const uint8_t *data, uint16_t len, const uint8_t ts[5],
                 int cfo_pphm, int rsl100, int fsl100, uint32_t seq,
                 char *out, uint16_t cap)
{
    /* worst case: 32 hex chars + '+' + 3 signed fixed-points + fixed keys
     * ≈ 110 bytes; refuse anything that could overflow */
    if (cap < 128)
    {
        return 0;
    }

    char *p = out;
    p += sprintf(p, "{\"i\":%lu,\"n\":%u,\"b\":\"",
                 (unsigned long)seq, (unsigned)len);
    unsigned nhex = len < FRAME_HEX_MAX ? len : FRAME_HEX_MAX;
    for (unsigned i = 0; i < nhex; i++)
    {
        p += sprintf(p, "%02X", data[i]);
    }
    if (len > FRAME_HEX_MAX)
    {
        *p++ = '+';
    }
    p += sprintf(p, "\",\"rsl\":");
    p += fmt100(p, rsl100);
    p += sprintf(p, ",\"fsl\":");
    p += fmt100(p, fsl100);
    p += sprintf(p, ",\"o\":");
    p += fmt100(p, cfo_pphm);
    p += sprintf(p, ",\"ts\":\"0x%02X%02X%02X%02X\"}",
                 ts[4], ts[3], ts[2], ts[1]);
    return (int)(p - out);
}
