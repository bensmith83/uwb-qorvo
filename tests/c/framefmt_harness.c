/* stdin/stdout harness for framefmt.c (see tests/test_framefmt.py). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "framefmt.h"

static int hexval(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    return -1;
}

static int parse_hex(const char *s, uint8_t *out, int cap)
{
    int n = 0;
    if (strcmp(s, "-") == 0)
        return 0;
    while (s[0] && s[1] && n < cap)
    {
        out[n++] = (uint8_t)(hexval(s[0]) * 16 + hexval(s[1]));
        s += 2;
    }
    return n;
}

int main(void)
{
    char kind[4], hex[512], tshex[16];
    while (scanf("%3s", kind) == 1)
    {
        char out[256];
        if (kind[0] == 'F')
        {
            int cfo, rsl, fsl, crc;
            unsigned long seq;
            if (scanf(" %511s %15s %d %d %d %lu %d",
                      hex, tshex, &cfo, &rsl, &fsl, &seq, &crc) != 7)
                break;
            uint8_t data[256], ts[5] = {0};
            int n = parse_hex(hex, data, sizeof data);
            parse_hex(tshex, ts, sizeof ts);
            frame_encode(data, (uint16_t)n, ts, cfo, rsl, fsl,
                         (uint32_t)seq, crc, out, sizeof out);
        }
        else if (kind[0] == 'S')
        {
            unsigned long seq;
            int phe, crcb, stse, to;
            if (scanf(" %lu %d %d %d %d", &seq, &phe, &crcb, &stse, &to) != 5)
                break;
            frame_encode_encrypted((uint32_t)seq, phe, crcb, stse, to,
                                   out, sizeof out);
        }
        else if (kind[0] == 'G')
        {
            /* one fragment: G <hexbytes|-> <seq> <part> */
            unsigned long seq;
            int part;
            if (scanf(" %511s %lu %d", hex, &seq, &part) != 3)
                break;
            uint8_t data[256];
            int n = parse_hex(hex, data, sizeof data);
            int r = frame_frag_encode(data, (uint16_t)n, (uint32_t)seq, part,
                                      out, sizeof out);
            if (r <= 0)
                strcpy(out, "");
        }
        else
        {
            break;
        }
        puts(out);
    }
    return 0;
}
