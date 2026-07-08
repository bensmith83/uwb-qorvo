#include "detector.h"

#include <stdio.h>
#include <string.h>

void det_init(detector_t *d)
{
    memset(d, 0, sizeof *d);
}

static uint32_t clamped_delta(uint32_t cur, uint32_t prev)
{
    /* counter decreases (listener restart / 12-bit chip-counter wrap) must
     * not read as activity — mirror webmodel.py's max(0, cur - prev) */
    return cur > prev ? cur - prev : 0;
}

void det_update(detector_t *d, const det_counts_t *cur)
{
    if (!d->primed)
    {
        d->primed = 1;
        d->prev = *cur;
        d->hits = 0;
        d->decoded = 0;
        return;
    }
    uint32_t dc = clamped_delta(cur->crcg, d->prev.crcg);
    uint32_t hits = clamped_delta(cur->sfdd, d->prev.sfdd) +
                    clamped_delta(cur->phe, d->prev.phe) +
                    clamped_delta(cur->crcb, d->prev.crcb) + dc;
    d->prev = *cur;
    d->hits = hits;
    d->decoded = dc;
    d->total += hits;
    if (hits > d->peak)
    {
        d->peak = hits;
    }
}

static const char *det_level(const detector_t *d)
{
    if (d->hits == 0)
    {
        return "idle";
    }
    if (d->hits < 10)
    {
        return "low";
    }
    if (d->hits < 100)
    {
        return "medium";
    }
    return "high";
}

/* channel/pcode are small ints or null; format once here */
static void fmt_opt_int(char *out, int v)
{
    if (v < 0)
    {
        strcpy(out, "null");
    }
    else
    {
        sprintf(out, "%d", v);
    }
}

int det_encode(const detector_t *d, const char *status, int channel,
               int pcode, char *buf, int cap)
{
    char c[16], k[16];
    fmt_opt_int(c, channel);
    fmt_opt_int(k, pcode);
    int n = snprintf(buf, cap,
                     "{\"s\":\"%s\",\"l\":\"%s\",\"h\":%lu,\"t\":%lu,"
                     "\"p\":%lu,\"d\":%lu,\"c\":%s,\"k\":%s}",
                     status, det_level(d), (unsigned long)d->hits,
                     (unsigned long)d->total, (unsigned long)d->peak,
                     (unsigned long)d->decoded, c, k);
    return n < cap ? n : cap - 1;
}
