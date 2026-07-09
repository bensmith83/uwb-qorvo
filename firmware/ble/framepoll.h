#ifndef FRAMEPOLL_H
#define FRAMEPOLL_H

/*
 * Emit-priority decision for uwb_feed_frame_poll(), split out of uwb_feed.c
 * so it is pure and host-testable (tests/test_framepoll.py) — no SDK includes.
 *
 * Per 500 ms tick the poll can have several things to report; this picks one,
 * in priority order. The load-bearing rule is that a captured frame (have_cap)
 * always wins: Apple interleaves rare CRC-good SP0 data frames among the SP3
 * ranging flood, and the 16-slot RX ring overwrites them long before the poll
 * can sample the newest entry, so each is staged at arrival and must not be
 * buried by the flood. have_cap covers both producers into one buffer:
 * CRC-good data from the OK ISR, and (with F1) CRC-failed data from the error
 * ISR.
 */
typedef enum
{
    FP_NONE = 0, /* nothing new this tick */
    FP_CAP,      /* a staged captured frame (OK-path data, or F1 CRC-fail) */
    FP_RANGING,  /* newest ring frame is SP3/STS no-data, in an STS mode */
    FP_CLEAN,    /* newest ring frame is fresh (clean/among-flood reception) */
    FP_RNG,      /* SP3 ranging telemetry staged from the error ISR */
    FP_ENC       /* failed-reception energy changed */
} fp_emit_t;

/*
 * have_cap      a new staged capture since the last poll (OK data or F1 fail)
 * fresh         the RX ring head advanced since the last poll
 * dlen          byte length of the newest ring frame (0 for SP3/no-data)
 * sts_on        listener is in an STS mode (stsMode != OFF)
 * have_rng      new ranging telemetry staged since the last poll
 * enc_changed   the failed-reception energy counter moved
 */
fp_emit_t frame_poll_select(int have_cap, int fresh, int dlen, int sts_on,
                            int have_rng, int enc_changed);

#endif /* FRAMEPOLL_H */
