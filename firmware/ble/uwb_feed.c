/*
 * Feeds the BLE characteristic from the on-chip LISTENER2 counters.
 *
 * Mirrors the Pi pipeline (uwb_explorer/web.py board_loop + webmodel.py):
 * fold the LSTAT counters into the detector once per second, notify the
 * latest snapshot every 500 ms.  The counters live in the listener's info
 * struct, written from the DW3xxx IRQ callback — copy them under a critical
 * section exactly like the vendor's LSTAT command does (listener_fn2.c).
 */

#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "EventManager.h"
#include "app.h"
#include "deca_device_api.h"
#include "detector.h"
#include "driver_app_config.h"
#include "framefmt.h"
#include "listener2.h"
#include "translate.h"

/* ble_app.c: notify one frame-detail JSON on the 6e5f0003 characteristic */
extern void ble_frame_push(const char *json, uint16_t len);

extern const app_definition_t helpers_app_listener[];

static detector_t m_det;
static int m_live;
static unsigned m_tick;

/* Start LISTENER2 exactly like the CLI's "LISTENER2" command (f_listen2),
 * unless the user saved their own default app in NVM. Called once from the
 * notify task after the scheduler and default task are up. */
void uwb_feed_autostart(void)
{
    det_init(&m_det);
    /* the factory default app is the idle "STOP" shell (never NULL) —
     * only respect a default the user explicitly saved to something else */
    const app_definition_t *def = AppGetDefaultEvent();
    if (def != NULL && def->app_name != NULL &&
        strcmp(def->app_name, "STOP") != 0)
    {
        return;
    }
    listener_set_mode(2);
    app_definition_t *app_ptr = (app_definition_t *)&helpers_app_listener[0];
    EventManagerRegisterApp((void *)&app_ptr);
}

/* Mirror the newest received frame onto the BLE frame characteristic.
 *
 * (A --wrap on the vendor's per-frame USB reporter does NOT work here:
 * send_to_pc_listener_info is defined and called inside the same
 * translation unit, so the linker never sees the call to redirect.)
 *
 * Instead, sample the listener's RX ring directly: rx_listener_cb fills
 * rxPcktBuf.buf[head] and THEN bumps head, and consumed entries aren't
 * erased — so buf[(head-1) & mask] is always the most recently completed
 * reception. Called from the 500 ms notify tick; pushes only when a new
 * frame arrived since the last tick (the USB path still streams every
 * frame). Copy under taskENTER_CRITICAL like the vendor's LSTAT does. */
/* Channel switch (control characteristic 6e5f0004): the BLE observer
 * just records the request; the notify task applies it — changing the
 * config and restarting the listener must not run in SD-event context.
 * Preamble code 9 (PRF64) is valid on both channel 5 and 9, so only the
 * channel changes. */
static volatile int m_pending_chan;

void uwb_feed_request_channel(int ch)
{
    if (ch == 5 || ch == 9)
    {
        m_pending_chan = ch;
    }
}

void uwb_feed_channel_poll(void)
{
    int ch = m_pending_chan;
    if (ch == 0)
    {
        return;
    }
    m_pending_chan = 0;
    dwt_config_t *cfg = get_dwt_config();
    if (cfg == NULL || deca_to_chan(cfg->chan) == ch)
    {
        return;
    }
    cfg->chan = chan_to_deca(ch);
    /* restart the listener exactly like autostart: defaultTask
     * terminates the running app and starts this one on the new channel */
    listener_set_mode(2);
    app_definition_t *app_ptr = (app_definition_t *)&helpers_app_listener[0];
    EventManagerRegisterApp((void *)&app_ptr);
}

/* temporary frame-path diagnostics, readable via SWD dump_image:
 * 0x2001FFF4 = 0xF00D0000 | head<<8 | tail
 * 0x2001FFF8 = sfd_detect count seen by the poll
 * 0x2001FFFC = 0xE5E50000 | value_set err<<8 | hvx err (from ble_frame_push)
 * (these are the boot window's fault CFSR/HFSR/BFAR slots — unused unless
 * a fault fires, in which case the fault wins and that's fine) */
#define FRAMEDIAG ((volatile uint32_t *)0x2001FFF4u)

void uwb_feed_frame_poll(void)
{
    listener_info_t *info = getListenerInfoPtr();
    if (info == NULL)
    {
        return;
    }

    static uint16_t last_head;
    static uint32_t last_enc_seq;
    uint8_t data[FRAME_HEX_MAX];
    uint8_t ts[5];
    uint16_t dlen;
    int16_t cfo;
    int rsl100, fsl100;
    uint32_t seq;
    /* encrypted/undecodable energy: bad-CRC + STS-error + timeout counts */
    int phe, crcb, stse, to;
    uint32_t enc_seq;

    taskENTER_CRITICAL();
    uint16_t head = info->rxPcktBuf.head;
    FRAMEDIAG[0] = 0xF00D0000u | ((head & 0xFFu) << 8) |
                   (info->rxPcktBuf.tail & 0xFFu);
    FRAMEDIAG[1] = info->event_counts_sfd_detect;
    int fresh = (head != last_head);
    if (fresh)
    {
        last_head = head;
        rx_listener_pckt_t *p =
            &info->rxPcktBuf.buf[(head - 1) & (EVENT_BUF_L_SIZE - 1)];
        dlen = (uint16_t)p->rxDataLen;
        memcpy(data, p->msg.data,
               dlen < sizeof data ? dlen : sizeof data);
        memcpy(ts, p->timeStamp, sizeof ts);
        cfo = p->clock_offset;
        rsl100 = p->rsl100;
        fsl100 = p->fsl100;
        seq = info->event_counts_sfd_detect;
    }
    phe = info->event_counts.PHE;
    crcb = info->event_counts.CRCB;
    stse = info->event_counts.STSE;
    to = info->event_counts.SFDTO + info->event_counts.PTO +
         info->event_counts.RTO;
    enc_seq = (uint32_t)crcb + (uint32_t)stse;
    taskEXIT_CRITICAL();

    static char json[160];
    if (fresh)
    {
        int cfo_pphm =
            (int)((float)cfo * (CLOCK_OFFSET_PPM_TO_RATIO * 1e6 * 100));
        int n = frame_encode(data, dlen, ts, cfo_pphm, rsl100, fsl100, seq,
                             json, sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
    }
    else if (enc_seq != last_enc_seq)
    {
        /* no readable frame this tick, but the radio logged failed
         * receptions (STS-encrypted traffic like an AirTag) — surface
         * that so the card shows energy instead of staying blank */
        last_enc_seq = enc_seq;
        int n = frame_encode_encrypted(enc_seq, phe, crcb, stse, to,
                                       json, sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
    }
}

uint16_t uwb_ble_payload(char *buf, uint16_t cap)
{
    listener_info_t *info = getListenerInfoPtr();

    /* fold at 1 Hz (every 2nd 500ms tick), like web.py's poll loop */
    if (info != NULL && (m_tick++ % 2) == 0)
    {
        det_counts_t c;
        taskENTER_CRITICAL();
        c.sfdd = info->event_counts_sfd_detect;
        c.phe = info->event_counts.PHE;
        c.crcb = info->event_counts.CRCB;
        c.crcg = info->event_counts.CRCG;
        taskEXIT_CRITICAL();
        det_update(&m_det, &c);
        m_live = 1;
    }

    int chan = -1, pcode = -1;
    dwt_config_t *cfg = get_dwt_config();
    if (cfg != NULL)
    {
        chan = cfg->chan;
        pcode = cfg->txCode;
    }
    return (uint16_t)det_encode(&m_det, m_live ? "live" : "waiting", chan,
                                pcode, buf, cap);
}
