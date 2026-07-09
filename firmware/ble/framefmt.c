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
                 int crc_ok, char *out, uint16_t cap)
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
    p += sprintf(p, ",\"ts\":\"0x%02X%02X%02X%02X\",\"crc\":%d}",
                 ts[4], ts[3], ts[2], ts[1], crc_ok ? 1 : 0);
    return (int)(p - out);
}

int frame_encode_encrypted(uint32_t seq, int phe, int crcb, int stse,
                           int to, char *out, uint16_t cap)
{
    if (cap < 64)
    {
        return 0;
    }
    return sprintf(out,
                   "{\"i\":%lu,\"enc\":1,\"phe\":%d,\"crcb\":%d,"
                   "\"stse\":%d,\"to\":%d}",
                   (unsigned long)seq, phe, crcb, stse, to);
}

int frame_encode_ranging(uint32_t seq, const uint8_t ts[5], int rsl100,
                         int fsl100, int sts_q, char *out, uint16_t cap)
{
    if (cap < 96)
    {
        return 0;
    }
    char *p = out;
    p += sprintf(p, "{\"i\":%lu,\"rng\":1,\"rsl\":", (unsigned long)seq);
    p += fmt100(p, rsl100);
    p += sprintf(p, ",\"fsl\":");
    p += fmt100(p, fsl100);
    p += sprintf(p, ",\"ts\":\"0x%02X%02X%02X%02X\",\"q\":%d}",
                 ts[4], ts[3], ts[2], ts[1], sts_q);
    return (int)(p - out);
}

int frame_frag_count(uint16_t len)
{
    return len > 0 ? (len + FRAG_CHUNK - 1) / FRAG_CHUNK : 0;
}

int frame_frag_encode(const uint8_t *data, uint16_t len, uint32_t seq,
                      int part, char *out, uint16_t cap)
{
    /* envelope (~37) + 2*FRAG_CHUNK hex; must fit one notification */
    if (cap < 128)
    {
        return 0;
    }
    int q = frame_frag_count(len);
    if (part < 0 || part >= q)
    {
        return 0;
    }
    unsigned start = (unsigned)part * FRAG_CHUNK;
    unsigned end = start + FRAG_CHUNK;
    if (end > len)
    {
        end = len;
    }
    char *p = out;
    p += sprintf(p, "{\"i\":%lu,\"p\":%d,\"q\":%d,\"b\":\"",
                 (unsigned long)seq, part, q);
    for (unsigned i = start; i < end; i++)
    {
        p += sprintf(p, "%02X", data[i]);
    }
    p += sprintf(p, "\"}");
    return (int)(p - out);
}
