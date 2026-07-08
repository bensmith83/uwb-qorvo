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
    char hex[512], tshex[16];
    int cfo, rsl, fsl;
    unsigned long seq;
    while (scanf("F %511s %15s %d %d %d %lu",
                 hex, tshex, &cfo, &rsl, &fsl, &seq) == 6)
    {
        uint8_t data[256], ts[5] = {0};
        int n = parse_hex(hex, data, sizeof data);
        parse_hex(tshex, ts, sizeof ts);
        char out[256];
        frame_encode(data, (uint16_t)n, ts, cfo, rsl, fsl,
                     (uint32_t)seq, out, sizeof out);
        puts(out);
    }
    return 0;
}
