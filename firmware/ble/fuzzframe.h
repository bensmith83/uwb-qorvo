#ifndef FUZZFRAME_H
#define FUZZFRAME_H

#include <stdint.h>

/*
 * UWB Fuzzer — malformed-802.15.4z-frame BUILDERS (bead uwb-qorvo-1hu.15).
 *
 * ETHICS / SCOPE: authorized security-research tooling. These builders
 * deliberately produce MALFORMED IEEE 802.15.4z frames for robustness /
 * conformance testing of UWB receivers. Emission is opcode-triggered ONLY
 * (serial CLI `fuzztx <case_id>`, experiment opcode `XZ1`) and is intended
 * for use on the operator's OWN or explicitly authorized devices. See
 * docs/EXPERIMENTS.md. No frame leaves the radio unless fuzz_tx() is called.
 *
 * Pure logic, no SDK includes — host-tested byte-for-byte against the Python
 * oracle in tests/test_fuzzframe.py, exactly like framefmt.c / detector.c.
 * The radio-TX and listener pause/resume are behind an installable seam
 * (fuzz_set_hooks) so the host tests can mock the emission and assert on the
 * BUILDER output and the half-duplex ordering.
 */

/* Fuzz-case catalog, ordered by id (SHARED contract — the Python
 * FuzzerController uses the same ids; do not renumber). */
#define FUZZ_BAD_CRC 0           /* valid frame, corrupted FCS */
#define FUZZ_INVALID_FRAMETYPE 1 /* reserved frame-type in the FCF */
#define FUZZ_OVERSIZED_PHR 2     /* PHR length field > real/legal payload */
#define FUZZ_TRUNCATED_MAC 3     /* MAC header cut short (addr fields missing) */
#define FUZZ_ILLEGAL_STS 4       /* illegal/inconsistent STS config */
#define FUZZ_CASE_COUNT 5

/* Largest builder output: 1 PHR byte + 127-octet max PSDU, rounded up. */
#define FUZZ_FRAME_MAX 160

/* Legal maximum 802.15.4 PSDU (PHR length field must not exceed this). */
#define FUZZ_PSDU_MAX 127

/* IEEE 802.15.4 frame type field (FCF bits 0..2). Type 7 is Reserved. */
#define FCF_FRAMETYPE_MASK 0x07
#define FCF_FRAMETYPE_RESERVED 0x07

/* One built fuzz frame plus the out-of-band PHY facets a pure byte buffer
 * cannot express (the PHR length field the PHY would emit, and the STS
 * packet configuration). */
typedef struct
{
    uint8_t buf[FUZZ_FRAME_MAX]; /* frame bytes to transmit */
    uint16_t len;                /* number of valid bytes in buf */
    uint8_t phr;                 /* PHR length field the PHY should send */
    uint8_t has_phr;             /* 1 if buf is prefixed with a PHR byte */
    int sts_sp;                  /* STS packet config SP mode 0..3, -1 = n/a */
    int sts_len;                 /* STS length (512-chip blocks), 0 = none */
    uint8_t sts_illegal;         /* 1 = STS config deliberately illegal */
} fuzz_frame_t;

/* 802.15.4 FCS (CRC-16/KERMIT: poly 0x1021 reflected, init 0, no final XOR).
 * Transmitted low octet first. */
uint16_t fuzz_fcs(const uint8_t *data, uint16_t len);

/* One builder per fuzz case. Each fills *f with a frame malformed in exactly
 * the way its name says (see docs/EXPERIMENTS.md for the catalog). */
void fuzz_build_bad_crc(fuzz_frame_t *f);
void fuzz_build_invalid_frametype(fuzz_frame_t *f);
void fuzz_build_oversized_phr(fuzz_frame_t *f);
void fuzz_build_truncated_mac(fuzz_frame_t *f);
void fuzz_build_illegal_sts(fuzz_frame_t *f);

/* Dispatch by case id. Returns 0 and fills *f on success, -1 for an unknown
 * id (f left untouched). */
int fuzz_build(int case_id, fuzz_frame_t *f);

/*
 * Emission seam. The real firmware installs a radio-TX callback plus the
 * listener pause/resume so the fuzzer is half-duplex (the passive LISTENER2
 * sniffer must be paused while we drive the radio TX). Host tests install
 * capturing stubs. All three may be NULL (default: no emission).
 */
typedef int (*fuzz_tx_fn)(const uint8_t *buf, uint16_t len);
typedef void (*fuzz_listener_fn)(void);
void fuzz_set_hooks(fuzz_tx_fn tx, fuzz_listener_fn pause,
                    fuzz_listener_fn resume);

/*
 * Emit ONE malformed frame of the given case, then return to IDLE:
 *   pause listener -> build case -> radio TX once -> resume listener.
 * Returns 0 on success, -1 for an unknown case id (nothing is transmitted).
 * The build + half-duplex ordering happen even if no TX hook is installed
 * (so the logic stays host-testable); the actual RF TX is the installed hook.
 */
int fuzz_tx(int case_id);

/*
 * Serial-CLI handler for `fuzztx <case_id>`. `args` is the text after the
 * command word (e.g. "2"); leading spaces are skipped. Returns the dispatched
 * case id on success, or -1 if the argument is missing / not a known case id.
 * The vendor CLI command table routes "fuzztx" here.
 */
int fuzz_cli(const char *args);

#endif /* FUZZFRAME_H */
