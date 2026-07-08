/*
 * C port of uwb_explorer/webmodel.py DetectorState + blecodec.encode_state.
 * Pure logic, no SDK dependencies — unit-tested on the host against the
 * Python implementation (tests/test_c_detector.py).
 */
#ifndef DETECTOR_H
#define DETECTOR_H

#include <stdint.h>

#define DET_PAYLOAD_MAX 128

typedef struct
{
    uint32_t sfdd; /* SW SFD-detection counter (monotonic uint32) */
    uint32_t phe;  /* chip PHY-header-error counter (12-bit, wraps) */
    uint32_t crcb; /* chip CRC-bad counter (12-bit, wraps) */
    uint32_t crcg; /* chip CRC-good counter (12-bit, wraps) */
} det_counts_t;

typedef struct
{
    int primed; /* first poll only sets the baseline */
    det_counts_t prev;
    uint32_t hits;    /* events this poll */
    uint32_t total;   /* cumulative events */
    uint32_t peak;    /* max single-poll hits */
    uint32_t decoded; /* CRC-good frames this poll */
} detector_t;

void det_init(detector_t *d);
void det_update(detector_t *d, const det_counts_t *cur);
/* channel/pcode < 0 encode as JSON null; returns payload length */
int det_encode(const detector_t *d, const char *status, int channel,
               int pcode, char *buf, int cap);

#endif /* DETECTOR_H */
