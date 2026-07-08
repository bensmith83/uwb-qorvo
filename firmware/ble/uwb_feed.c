/*
 * Feeds the BLE characteristic with UWB detector state.
 *
 * Phase 1 (bring-up): static "waiting" payload matching
 * uwb_explorer/blecodec.py's shape so the iOS app parses it.
 * Phase 3 wires this to the on-chip LISTENER2 counters.
 */

#include <stdint.h>
#include <string.h>

uint16_t uwb_ble_payload(char *buf, uint16_t cap)
{
    static const char waiting[] =
        "{\"s\":\"waiting\",\"l\":\"idle\",\"h\":0,\"t\":0,"
        "\"p\":0,\"d\":0,\"c\":null,\"k\":null}";
    uint16_t len = sizeof waiting - 1;
    if (len > cap)
    {
        len = cap;
    }
    memcpy(buf, waiting, len);
    return len;
}
