/* stdin/stdout harness for framepoll.c (see tests/test_framepoll.py). */
#include <stdio.h>

#include "framepoll.h"

static const char *emit_name(fp_emit_t e)
{
    switch (e)
    {
    case FP_NONE:    return "NONE";
    case FP_CAP:     return "CAP";
    case FP_RANGING: return "RANGING";
    case FP_CLEAN:   return "CLEAN";
    case FP_RNG:     return "RNG";
    case FP_ENC:     return "ENC";
    }
    return "?";
}

int main(void)
{
    char kind[4];
    while (scanf("%3s", kind) == 1)
    {
        if (kind[0] == 'S')
        {
            int hc, fr, dl, sts, hr, enc;
            if (scanf(" %d %d %d %d %d %d",
                      &hc, &fr, &dl, &sts, &hr, &enc) != 6)
                break;
            printf("%s\n", emit_name(
                frame_poll_select(hc, fr, dl, sts, hr, enc)));
        }
    }
    return 0;
}
