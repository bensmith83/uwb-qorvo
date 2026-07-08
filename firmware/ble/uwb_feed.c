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
static int m_scanning; /* 1 while auto-sweep is hunting a preamble code */

/* Re-register the LISTENER app so the DW3110 picks up get_dwt_config()
 * changes (channel / preamble code). Must run in task context, never in
 * an SD-event callback. Shared by autostart, manual set, and auto-sweep. */
static void listener_restart(void)
{
    listener_set_mode(2);
    app_definition_t *app_ptr = (app_definition_t *)&helpers_app_listener[0];
    EventManagerRegisterApp((void *)&app_ptr);
}

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
    listener_restart();
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
/*
 * Control (characteristic 6e5f0004) + auto-sweep.
 *
 * The BLE observer only records requests (volatile flags); the notify
 * task applies them — reconfiguring the radio and restarting the
 * listener must not run in SD-event context.
 *
 * Auto-sweep is the reason the app can see AirTag *bytes* at all: the
 * DW3110 only decodes frames whose preamble code matches the
 * transmitter, and Apple uses code 10/11/12 on channel 9 while our
 * default is 9 (proven by the code-sweep capture — silent on 9, full
 * frames on 10). So in AUTO mode we dwell on each of {9,10,11,12}, watch
 * the receive counters, and lock onto whichever code actually pulls
 * frames; if it goes quiet we resume sweeping — like a BLE sniffer
 * hopping to find a talker. Channel stays under manual control (5/9).
 */
static const uint8_t SWEEP_CODES[] = {9, 10, 11, 12};
#define SWEEP_N (sizeof SWEEP_CODES / sizeof SWEEP_CODES[0])
#define DWELL_TICKS 5       /* ~2.5 s per code at the 500 ms tick */
#define LOCK_THRESHOLD 3    /* receptions in a dwell that mean "found it" */
#define UNLOCK_SILENCE 12   /* ~6 s of nothing -> resume sweeping */

static volatile int m_pending_chan;  /* 5 or 9 */
static volatile int m_pending_code;  /* 9..12, forces manual */
static volatile int m_pending_auto;  /* 1 = auto on, -1 = manual/off */

static int m_auto = 1;               /* default: sweep like a sniffer */
static int m_sweep_idx;
static unsigned m_dwell;
static uint32_t m_activity_mark;

int uwb_feed_is_scanning(void) { return m_scanning; }

void uwb_feed_request_channel(int ch)
{
    if (ch == 5 || ch == 9)
    {
        m_pending_chan = ch;
    }
}

void uwb_feed_request_code(int code)
{
    if (code >= 9 && code <= 12)
    {
        m_pending_code = code;
    }
}

void uwb_feed_request_auto(int on) { m_pending_auto = on ? 1 : -1; }

/* total frame-stage receptions so far — the "did this code hear
 * anything" signal. Wrong preamble code barely moves it; the right one
 * surges (headers + CRC-good/bad + STS energy). */
static uint32_t rx_activity(listener_info_t *info)
{
    return info->event_counts_sfd_detect + info->event_counts.PHE +
           info->event_counts.CRCG + info->event_counts.CRCB +
           info->event_counts.STSE;
}

static void set_code(dwt_config_t *cfg, int code)
{
    cfg->txCode = code;
    cfg->rxCode = code;
    listener_restart();
}

void uwb_feed_control_poll(void)
{
    dwt_config_t *cfg = get_dwt_config();
    if (cfg == NULL)
    {
        return;
    }

    /* apply pending requests first (manual overrides the sweep) */
    if (m_pending_auto != 0)
    {
        m_auto = (m_pending_auto == 1);
        m_pending_auto = 0;
        m_dwell = 0;
        if (m_auto)
        {
            m_scanning = 1;
        }
        else
        {
            m_scanning = 0;
        }
    }
    if (m_pending_chan != 0)
    {
        int ch = m_pending_chan;
        m_pending_chan = 0;
        if (deca_to_chan(cfg->chan) != ch)
        {
            cfg->chan = chan_to_deca(ch);
            listener_restart();
            m_dwell = 0; /* channel changed — re-evaluate this code */
        }
    }
    if (m_pending_code != 0)
    {
        int code = m_pending_code;
        m_pending_code = 0;
        m_auto = 0;
        m_scanning = 0;
        if (cfg->txCode != code)
        {
            set_code(cfg, code);
        }
    }

    if (!m_auto)
    {
        return;
    }

    listener_info_t *info = getListenerInfoPtr();
    if (info == NULL)
    {
        return;
    }
    uint32_t act = rx_activity(info);

    if (!m_scanning)
    {
        /* locked: hold the code until it goes quiet for a while */
        if (act != m_activity_mark)
        {
            m_activity_mark = act;
            m_dwell = 0;
        }
        else if (++m_dwell >= UNLOCK_SILENCE)
        {
            m_scanning = 1;
            m_dwell = 0;
        }
        return;
    }

    /* scanning: dwell on the current code, lock if it hears enough */
    if (m_dwell == 0)
    {
        m_activity_mark = act; /* baseline for this code */
    }
    if (act - m_activity_mark >= LOCK_THRESHOLD)
    {
        m_scanning = 0; /* found a talker on this code */
        m_activity_mark = act;
        m_dwell = 0;
        return;
    }
    if (++m_dwell >= DWELL_TICKS)
    {
        m_dwell = 0;
        m_sweep_idx = (m_sweep_idx + 1) % SWEEP_N;
        if (cfg->txCode != SWEEP_CODES[m_sweep_idx])
        {
            set_code(cfg, SWEEP_CODES[m_sweep_idx]);
        }
    }
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
    /* "scan" while auto-sweep is hunting a preamble code; the app shows
     * "scanning code k…" and the k field cycles 9->10->11->12 */
    const char *status = m_scanning ? "scan" : (m_live ? "live" : "waiting");
    return (uint16_t)det_encode(&m_det, status, chan, pcode, buf, cap);
}
