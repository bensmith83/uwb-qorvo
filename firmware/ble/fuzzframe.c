/* See fuzzframe.h. Pure logic (host-testable): no SDK includes.
 *
 * UWB Fuzzer malformed-frame builders (bead uwb-qorvo-1hu.15). AUTHORIZED
 * security-research tooling — own/authorized devices only, opcode-triggered
 * emission only. See docs/EXPERIMENTS.md for the ethics/scope note. */

#include "fuzzframe.h"

#include <string.h>

/*
 * A well-formed 802.15.4 data-frame header the builders start from:
 *   FCF = 0x8841  (little-endian bytes 0x41 0x88): Data frame, PAN-ID
 *                 compression, short dest + short src addressing
 *   seq = 0x01
 *   dest PAN 0xDECA, dest short 0xFFFF (broadcast), src short 0x0001
 * Frame type bits (0..2) = 1 (Data); dest/src addr modes = 2 (short).
 */
static const uint8_t BASE_HDR[] = {
    0x41, 0x88,       /* FCF (LE) */
    0x01,             /* sequence number */
    0xCA, 0xDE,       /* destination PAN id */
    0xFF, 0xFF,       /* destination short address (broadcast) */
    0x01, 0x00,       /* source short address (PAN compressed -> no src PAN) */
};
static const uint8_t BASE_PAYLOAD[] = {0xDE, 0xAD, 0xBE, 0xEF};

uint16_t fuzz_fcs(const uint8_t *data, uint16_t len)
{
    /* CRC-16/KERMIT == IEEE 802.15.4 FCS */
    uint16_t crc = 0;
    for (uint16_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (int b = 0; b < 8; b++)
        {
            crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0x8408) : (uint16_t)(crc >> 1);
        }
    }
    return crc;
}

/* Assemble BASE_HDR + BASE_PAYLOAD + FCS into f->buf. Returns body length
 * (everything except the 2 FCS octets) so callers can locate the FCS. */
static uint16_t build_valid(fuzz_frame_t *f)
{
    memset(f, 0, sizeof *f);
    f->sts_sp = -1;
    uint16_t n = 0;
    memcpy(f->buf + n, BASE_HDR, sizeof BASE_HDR);
    n += (uint16_t)sizeof BASE_HDR;
    memcpy(f->buf + n, BASE_PAYLOAD, sizeof BASE_PAYLOAD);
    n += (uint16_t)sizeof BASE_PAYLOAD;
    uint16_t body = n;
    uint16_t fcs = fuzz_fcs(f->buf, body);
    f->buf[n++] = (uint8_t)(fcs & 0xFF); /* FCS low octet first */
    f->buf[n++] = (uint8_t)(fcs >> 8);
    f->len = n;
    return body;
}

void fuzz_build_bad_crc(fuzz_frame_t *f)
{
    /* valid frame, then corrupt the FCS so it can never match the body */
    uint16_t body = build_valid(f);
    f->buf[body] ^= 0xFF;     /* flip both FCS octets */
    f->buf[body + 1] ^= 0xFF;
}

void fuzz_build_invalid_frametype(fuzz_frame_t *f)
{
    /* set the FCF frame-type field (bits 0..2) to 7 (Reserved), then RECOMPUTE
     * the FCS so the frame is malformed only in the frame type. */
    build_valid(f);
    f->buf[0] = (uint8_t)((f->buf[0] & ~FCF_FRAMETYPE_MASK) | FCF_FRAMETYPE_RESERVED);
    uint16_t body = (uint16_t)(f->len - 2);
    uint16_t fcs = fuzz_fcs(f->buf, body);
    f->buf[body] = (uint8_t)(fcs & 0xFF);
    f->buf[body + 1] = (uint8_t)(fcs >> 8);
}

void fuzz_build_oversized_phr(fuzz_frame_t *f)
{
    /* a valid PSDU, but prefixed with a PHR whose length field is larger than
     * the real payload AND larger than the legal 127-octet maximum. */
    fuzz_frame_t inner;
    build_valid(&inner);

    memset(f, 0, sizeof *f);
    f->sts_sp = -1;
    f->has_phr = 1;
    f->phr = 200; /* > real PSDU (~15) and > FUZZ_PSDU_MAX (127) */
    f->buf[0] = f->phr;
    memcpy(f->buf + 1, inner.buf, inner.len);
    f->len = (uint16_t)(inner.len + 1);
}

void fuzz_build_truncated_mac(fuzz_frame_t *f)
{
    /* FCF still declares short dest + src addressing, but the frame is cut off
     * right after the sequence number — the addressing fields (and FCS) are
     * missing, so a parser runs off the end of the buffer. */
    memset(f, 0, sizeof *f);
    f->sts_sp = -1;
    f->buf[0] = BASE_HDR[0]; /* FCF low  (addr modes intact) */
    f->buf[1] = BASE_HDR[1]; /* FCF high */
    f->buf[2] = BASE_HDR[2]; /* sequence number */
    f->len = 3;              /* truncated: < 11 octet minimum */
}

void fuzz_build_illegal_sts(fuzz_frame_t *f)
{
    /* a plausible frame carried with an INCONSISTENT STS packet config: the SP
     * mode says an STS field is present (SP2 = STS between PHR and payload) but
     * the STS length is zero — an illegal/undecodable combination. */
    build_valid(f);
    f->sts_sp = 2;      /* SP2: STS present */
    f->sts_len = 0;     /* ...yet zero-length STS -> inconsistent */
    f->sts_illegal = 1;
}

int fuzz_build(int case_id, fuzz_frame_t *f)
{
    switch (case_id)
    {
    case FUZZ_BAD_CRC:
        fuzz_build_bad_crc(f);
        return 0;
    case FUZZ_INVALID_FRAMETYPE:
        fuzz_build_invalid_frametype(f);
        return 0;
    case FUZZ_OVERSIZED_PHR:
        fuzz_build_oversized_phr(f);
        return 0;
    case FUZZ_TRUNCATED_MAC:
        fuzz_build_truncated_mac(f);
        return 0;
    case FUZZ_ILLEGAL_STS:
        fuzz_build_illegal_sts(f);
        return 0;
    default:
        return -1;
    }
}

/* --- emission seam ------------------------------------------------------- */

static fuzz_tx_fn s_tx;
static fuzz_listener_fn s_pause;
static fuzz_listener_fn s_resume;

void fuzz_set_hooks(fuzz_tx_fn tx, fuzz_listener_fn pause,
                    fuzz_listener_fn resume)
{
    s_tx = tx;
    s_pause = pause;
    s_resume = resume;
}

int fuzz_tx(int case_id)
{
    fuzz_frame_t f;
    if (fuzz_build(case_id, &f) != 0)
    {
        return -1; /* unknown case: transmit nothing, listener untouched */
    }
    /* half-duplex: quiesce the passive listener, key the radio once, resume */
    if (s_pause)
    {
        s_pause();
    }
    if (s_tx)
    {
        (void)s_tx(f.buf, f.len);
    }
    if (s_resume)
    {
        s_resume(); /* back to IDLE (listening) */
    }
    return 0;
}

int fuzz_cli(const char *args)
{
    if (!args)
    {
        return -1;
    }
    while (*args == ' ' || *args == '\t')
    {
        args++;
    }
    if (*args < '0' || *args > '9')
    {
        return -1; /* missing / non-numeric case id */
    }
    int id = 0;
    for (const char *p = args; *p; p++)
    {
        if (*p < '0' || *p > '9')
        {
            return -1; /* trailing junk -> reject */
        }
        id = id * 10 + (*p - '0');
        if (id >= 1000)
        {
            return -1; /* absurd -> reject before overflow */
        }
    }
    if (id >= FUZZ_CASE_COUNT)
    {
        return -1;
    }
    if (fuzz_tx(id) != 0)
    {
        return -1;
    }
    return id;
}
