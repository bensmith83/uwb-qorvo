#include "framepoll.h"

fp_emit_t frame_poll_select(int have_cap, int fresh, int dlen, int sts_on,
                            int have_rng, int enc_changed)
{
    /* A staged capture is never dropped, whatever else happened. */
    if (have_cap)
    {
        return FP_CAP;
    }
    /* An STS/SP3 no-data reception -> ranging telemetry, not a 0-byte frame. */
    if (fresh && dlen == 0 && sts_on)
    {
        return FP_RANGING;
    }
    /* Any other fresh ring frame (matches uwb_feed's prior `else if (fresh)`). */
    if (fresh)
    {
        return FP_CLEAN;
    }
    if (have_rng)
    {
        return FP_RNG;
    }
    if (enc_changed)
    {
        return FP_ENC;
    }
    return FP_NONE;
}
