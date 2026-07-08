#ifndef FRAMEFMT_H
#define FRAMEFMT_H

#include <stdint.h>

/* Bytes of frame payload included as hex in the "b" field; longer frames
 * are truncated with a trailing '+' (mirrors the vendor listener's fast
 * mode). Keeps the worst-case JSON inside one 128-byte notification. */
#define FRAME_HEX_MAX 16

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

#endif /* FRAMEFMT_H */
