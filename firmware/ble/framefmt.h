#ifndef FRAMEFMT_H
#define FRAMEFMT_H

#include <stdint.h>

/* Bytes of frame payload included as hex in the summary "b" field; longer
 * frames are truncated with a trailing '+' (mirrors the vendor listener's
 * fast mode). Keeps the worst-case summary JSON inside one 128-byte
 * notification. The FULL frame is delivered separately as fragments (see
 * frame_frag_encode) so the phone can reassemble the whole thing. */
#define FRAME_HEX_MAX 16

/* Max frame bytes we capture and stream (802.15.4 PSDU max is 127). */
#define FRAME_FULL_MAX 127

/* Frame bytes carried per fragment notification. 40 B -> 80 hex chars;
 * with the fragment JSON envelope this stays inside one 128-byte
 * notification (see frame_frag_encode / tests/test_framefmt.py). */
#define FRAG_CHUNK 40

/*
 * Render one received UWB frame as compact JSON for the frame
 * characteristic (6e5f0003-...):
 *
 *   {"i":7,"n":12,"b":"41880CADDE","rsl":-79.50,"fsl":-81.20,
 *    "o":-3.25,"ts":"0x987654 32"}
 *
 *   i    frame sequence number (frames heard since boot)
 *   n    received length in bytes
 *   b    first FRAME_HEX_MAX bytes as hex, '+' appended if truncated
 *   rsl  received signal level, dBm (from rsl100, hundredths)
 *   fsl  first-path signal level, dBm (fsl-rsl gap hints LOS/NLOS)
 *   o    carrier frequency offset, ppm (from cfo_pphm, hundredths of ppm)
 *   ts   top 32 bits of the 40-bit raw RX timestamp (~4 ns units),
 *        rendered like the vendor's "TS4ns"
 *
 * Pure logic, no SDK includes — host-tested byte-for-byte against the
 * Python oracle in tests/test_framefmt.py.
 *
 * Returns the string length written (excluding NUL), 0 if cap too small.
 */
int frame_encode(const uint8_t *data, uint16_t len, const uint8_t ts[5],
                 int cfo_pphm, int rsl100, int fsl100, uint32_t seq,
                 int crc_ok, char *out, uint16_t cap);

/*
 * Render an "encrypted / undecodable energy" marker for the frame
 * characteristic when the radio saw receptions that never produced a
 * readable frame (e.g. an AirTag's STS-encrypted traffic — bad CRC,
 * STS-quality failures). Reports the hardware error/timeout counters so
 * the phone can show that UWB *was* heard, just not decodable:
 *
 *   {"i":42,"enc":1,"phe":0,"crcb":3,"stse":3,"to":0}
 *
 *   enc   always 1 (lets the app branch on frame vs encrypted-marker)
 *   phe   PHY header error count
 *   crcb  bad-CRC frame count (decoded but failed integrity)
 *   stse  STS error/warning count (the STS-encryption tell)
 *   to    SFD/preamble/RX timeouts (energy seen, frame never completed)
 *
 * Returns string length written (excluding NUL), 0 if cap too small.
 */
int frame_encode_encrypted(uint32_t seq, int phe, int crcb, int stse,
                           int to, char *out, uint16_t cap);

/* Number of FRAG_CHUNK-sized fragments a `len`-byte frame splits into
 * (0 for an empty frame). */
int frame_frag_count(uint16_t len);

/*
 * Render one fragment of a full frame's bytes as compact JSON for the
 * frame characteristic (6e5f0003-...), so the phone can reassemble frames
 * larger than one notification:
 *
 *   {"i":7,"p":0,"q":2,"b":"492B0100FF..."}
 *
 *   i    frame sequence number — ties fragments to the summary push and to
 *        each other; a change means a new frame (drop any partial reassembly)
 *   p    part index, 0-based
 *   q    total number of parts for this frame
 *   b    this part's bytes as hex (FRAG_CHUNK bytes, fewer on the last part)
 *
 * `part` selects the slice [part*FRAG_CHUNK, +FRAG_CHUNK). Returns the
 * string length written (excluding NUL), or 0 if `part` is out of range,
 * the frame is empty, or the buffer is too small.
 */
int frame_frag_encode(const uint8_t *data, uint16_t len, uint32_t seq,
                      int part, char *out, uint16_t cap);

#endif /* FRAMEFMT_H */
