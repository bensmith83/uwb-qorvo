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
/* ble_app.c: notify-only push (fragments); returns the SD hvx result so we
 * can stop when the HVN queue is full and resume next tick. 0 == success. */
extern uint32_t ble_frame_notify(const uint8_t *data, uint16_t len);

extern const app_definition_t helpers_app_listener[];

/* SWD-readable diagnostics window (documented in full below where the
 * other fields are written). */
#define DIAG3 ((volatile uint32_t *)0x2001FF80u)

/* Clean capture-path counters — a plain BSS global (zeroed at startup), read
 * over SWD via its .map address. DIAG3[6]/[7] collide with ble_app's push
 * counters and the whole reserved 0x2001FF80 window is already carved up
 * (DIAG3 / DIAG2 / BLE-evt / boot), so this lives in ordinary RAM. Answers
 * "does an AirTag reception carry any bytes to capture?":
 *   [0] total non-OK (err+timeout) callbacks
 *   [1] callbacks whose datalength > 0  (i.e. something IS capturable)
 *   [2] max datalength seen
 *   [3] frames actually stored (capture on AND datalength > 0) */
volatile uint32_t g_capdiag[4];
#define CAPDIAG g_capdiag

/*
 * CRC-fail frame capture (control command "F1"/"F0", off by default).
 *
 * AirTag / Nearby-Interaction frames are STS-secured and fail the CRC on
 * a passive listener, so the vendor's OK callback never queues them and
 * the byte card only ever sees the rare clean frame. But the DW3110 still
 * holds the received bytes on a CRC error — so we intercept the RX error
 * callback and read them out before the vendor re-arms RX.
 *
 * The interception uses --wrap=listener2_configure_uwb (build-ble.sh):
 * that function is defined in listener2_dw3000.c and CALLED from
 * listener2.c — different translation units, so unlike the same-TU
 * send_to_pc case, the linker wrap actually takes. We slot our own error
 * callback in and chain to the vendor's. Reading dwt_readrxdata /
 * timestamp / RSSI in the RX ISR is the same work the OK path already
 * does, so no new SD-timing risk. */
static volatile int m_capture_fail;
static volatile uint32_t m_fail_seq;
static volatile uint16_t m_fail_len;
static volatile uint8_t m_fail_data[FRAME_FULL_MAX];
static volatile uint8_t m_fail_ts[5];
static volatile int m_fail_rsl, m_fail_fsl;

/* SP3/STS ranging telemetry. Apple's precision-find frames are STS-secured
 * with no data payload, so there are no bytes to capture — but each
 * reception still carries a timestamp and an STS-quality metric. When the
 * listener is in an STS mode we stage those (cheap register reads only; the
 * RSSI float math is done later in task context) so the app can show a
 * ranging-telemetry card instead of a blank/0-byte frame. */
static volatile uint32_t m_rng_seq;
static volatile uint8_t m_rng_ts[5];
static volatile int m_rng_q;

void uwb_feed_request_capture(int on) { m_capture_fail = on ? 1 : 0; }

static dwt_cb_t m_real_rx_err, m_real_rx_to;

/* Grab whatever the DW3110 holds on a non-OK reception (CRC error OR
 * timeout) — any event that carries a datalength. Diagnostics in
 * DIAG3[6]/[7]:
 *   [6] total non-OK callbacks seen (err + timeout)
 *   [7] (callbacks-with-datalength<<16) | last datalength seen */
static void capture_grab(const dwt_cb_data_t *rxd)
{
    if (rxd == NULL)
    {
        return;
    }
    DIAG3[6]++;
    uint16_t withdata = (uint16_t)(DIAG3[7] >> 16);
    if (rxd->datalength > 0)
    {
        withdata++;
    }
    DIAG3[7] = ((uint32_t)withdata << 16) | (rxd->datalength & 0xFFFFu);

    /* clean counters (see CAPDIAG doc) — does the reception carry bytes? */
    CAPDIAG[0]++;
    if (rxd->datalength > 0)
    {
        CAPDIAG[1]++;
        if (rxd->datalength > CAPDIAG[2])
        {
            CAPDIAG[2] = rxd->datalength;
        }
    }

    if (m_capture_fail && rxd->datalength > 0)
    {
        uint16_t n = rxd->datalength;
        if (n > FRAME_FULL_MAX)
        {
            n = FRAME_FULL_MAX;
        }
        dwt_readrxdata((uint8_t *)m_fail_data, n, 0);
        listener2_readrxtimestamp((uint8_t *)m_fail_ts);
        int rsl, fsl;
        listener2_rssi_cal(&rsl, &fsl);
        m_fail_rsl = rsl;
        m_fail_fsl = fsl;
        m_fail_len = rxd->datalength;
        m_fail_seq++;
        CAPDIAG[3]++;
    }

    /* SP3/STS ranging telemetry: when in an STS mode, stage timing +
     * STS-quality for every reception (cheap reads only — no float RSSI
     * math here; frame_poll computes levels in task context). */
    dwt_config_t *cfg = get_dwt_config();
    if (cfg != NULL && cfg->stsMode != DWT_STS_MODE_OFF)
    {
        int16_t q = 0;
        listener2_readstsquality(&q);
        listener2_readrxtimestamp((uint8_t *)m_rng_ts);
        m_rng_q = q;
        m_rng_seq++;
    }
}

static void capture_rx_err_cb(const dwt_cb_data_t *rxd)
{
    capture_grab(rxd);
    if (m_real_rx_err != NULL)
    {
        m_real_rx_err(rxd);
    }
}

static void capture_rx_to_cb(const dwt_cb_data_t *rxd)
{
    capture_grab(rxd);
    if (m_real_rx_to != NULL)
    {
        m_real_rx_to(rxd);
    }
}

extern void __real_listener2_configure_uwb(dwt_cb_t ok, dwt_cb_t to,
                                           dwt_cb_t err);
void __wrap_listener2_configure_uwb(dwt_cb_t ok, dwt_cb_t to, dwt_cb_t err)
{
    m_real_rx_err = err;
    m_real_rx_to = to;
    __real_listener2_configure_uwb(ok, capture_rx_to_cb, capture_rx_err_cb);
}

static detector_t m_det;
static int m_live;
static unsigned m_tick;
static int m_scanning; /* 1 while auto-sweep is hunting a preamble code */

/*
 * Diagnostics window at 0x2001FF80 (clear of the boot/fault window at
 * 0x2001FFE0; RAM shrunk to 0x1FF80, zeroed by tools/flash.sh). Persists
 * across watchdog resets:
 *   [0] 0xF00D0000 | head<<8 | tail   (frame ring)
 *   [1] sfd_detect count last seen
 *   [2] 0xE5E5.... enc/hvx push errors (from ble_frame_push)
 *   [3] listener_restart call count
 *   [4] 0xB2 << 24 | in_restart<<16 | current preamble code<<8 | mode
 *       (in_restart=1 means a fault hit DURING a listener restart)
 *   [5] rx_activity at the last restart
 *   [6] total RX-error callbacks (reception diagnostics)
 *   [7] (errors-with-data<<16) | last datalength seen
 */

/* Re-register the LISTENER app so the DW3110 picks up get_dwt_config()
 * changes (channel / preamble code). Must run in task context, never in
 * an SD-event callback. Shared by autostart, manual set, and auto-sweep. */
static void listener_restart(void)
{
    DIAG3[3]++;
    DIAG3[4] |= 0x00010000u; /* in_restart */
    listener_set_mode(2);
    app_definition_t *app_ptr = (app_definition_t *)&helpers_app_listener[0];
    EventManagerRegisterApp((void *)&app_ptr);
    DIAG3[4] &= ~0x00010000u; /* restart returned cleanly */
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
    /* Boot straight onto Apple's UWB preamble code (10 was the strongest
     * lock in the code-sweep capture; default is 9, on which the AirTag is
     * silent). Setting it BEFORE the first listener start means no restart
     * is needed — and restarts are what assert the SoftDevice, so we avoid
     * them by default. Auto-sweep (which must restart to hop codes) is
     * opt-in via the app toggle. */
    dwt_config_t *cfg = get_dwt_config();
    if (cfg != NULL)
    {
        cfg->txCode = 10;
        cfg->rxCode = 10;
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
#define DWELL_TICKS 6       /* ~3 s per code at the 500 ms tick */
#define LOCK_THRESHOLD 1    /* ANY reception on a code -> lock (don't
                             * restart while traffic is present) */
#define UNLOCK_SILENCE 40   /* ~20 s of nothing -> resume sweeping (ride
                             * through the gaps between AirTag bursts) */

static volatile int m_pending_chan;  /* 5 or 9 */
static volatile int m_pending_code;  /* 9..12, forces manual */
static volatile int m_pending_auto;  /* 1 = auto on, -1 = manual/off */
static volatile int m_pending_sts = -1; /* 0..3 STS mode, -1 = none */

static int m_auto = 0;               /* default: MANUAL on code 10 (stable,
                                      * no restarts); auto-sweep is opt-in
                                      * because hopping codes asserts the SD */
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

/* STS mode: 0=OFF (SP0, plain frames — the proven default), 1=STS+data
 * (SP1), 2=data+STS (SP2), 3=STS-no-data (SP3 ranging). Matching Apple's
 * mode is what could let the receiver decode the STS frames' structure
 * instead of aborting mid-frame. Experimental — changing it restarts the
 * listener. */
void uwb_feed_request_sts(int mode)
{
    if (mode >= 0 && mode <= 3)
    {
        m_pending_sts = mode;
    }
}

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
    if (m_pending_sts >= 0)
    {
        int mode = m_pending_sts;
        m_pending_sts = -1;
        if (cfg->stsMode != mode)
        {
            cfg->stsMode = mode;
            listener_restart();
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
    DIAG3[4] = 0xB2000000u | (DIAG3[4] & 0x00010000u) |
               ((cfg->txCode & 0xFFu) << 8) | (m_scanning ? 1u : 0u);

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

    /* scanning: dwell on the current code, lock the instant it hears
     * ANYTHING — critically, we must NOT restart the listener while
     * traffic is arriving (that is what asserts the SoftDevice), so lock
     * beats switching whenever activity is present. */
    if (m_dwell == 0)
    {
        m_activity_mark = act; /* baseline for this code (post-restart) */
    }
    else if (act - m_activity_mark >= LOCK_THRESHOLD)
    {
        m_scanning = 0; /* found a talker on this code — stop restarting */
        m_activity_mark = act;
        m_dwell = 0;
        return;
    }
    if (++m_dwell >= DWELL_TICKS)
    {
        /* dwell expired with the code silent — safe to hop. Re-check
         * activity once more so we never restart on top of a frame that
         * just landed. */
        if (rx_activity(info) != m_activity_mark)
        {
            m_scanning = 0; /* late arrival — lock instead of hopping */
            m_activity_mark = rx_activity(info);
            m_dwell = 0;
            return;
        }
        m_dwell = 0;
        m_sweep_idx = (m_sweep_idx + 1) % SWEEP_N;
        DIAG3[5] = act;
        if (cfg->txCode != SWEEP_CODES[m_sweep_idx])
        {
            set_code(cfg, SWEEP_CODES[m_sweep_idx]);
        }
    }
}

/* frame-path diagnostics live in the DIAG3 window (see top of file):
 * DIAG3[0] = ring head/tail, DIAG3[1] = sfd count */
#define FRAMEDIAG DIAG3

/* Full-frame fragment streamer. The summary push carries only the first
 * FRAME_HEX_MAX bytes (to fit one notification), so we ALSO stream the
 * whole frame as FRAG_CHUNK-sized fragment notifications the phone
 * reassembles by seq. A few go per 500 ms tick, self-paced by the HVN
 * queue: stop on the first non-success (queue full / not connected) and
 * resume next tick. A newer frame restarts the stream, and the phone drops
 * any partial reassembly when the seq changes. */
#define STREAM_PER_TICK 3
/* The one full-frame buffer, shared by the summary encode and the streamer
 * (frame_poll fills it from the RX ring or the CRC-fail capture, then both
 * read it). One buffer, not several, keeps the app RAM under RAM_BASE. */
static uint8_t m_stream_buf[FRAME_FULL_MAX];
static uint16_t m_stream_len;
static uint32_t m_stream_seq;
static int m_stream_part;   /* next part to send */
static int m_stream_parts;  /* total parts, 0 = idle */

/* Begin streaming whatever is already in m_stream_buf (bytes copied in by
 * the caller). Restarts at part 0 — a newer frame supersedes any in-flight
 * one, and the phone drops the partial reassembly on the seq change. */
static void stream_set(uint16_t len, uint32_t seq)
{
    if (len > FRAME_FULL_MAX)
    {
        len = FRAME_FULL_MAX;
    }
    m_stream_len = len;
    m_stream_seq = seq;
    m_stream_part = 0;
    m_stream_parts = frame_frag_count(len);
}

static void stream_pump(char *json, uint16_t cap)
{
    for (int sent = 0;
         m_stream_parts > 0 && m_stream_part < m_stream_parts &&
         sent < STREAM_PER_TICK;
         sent++)
    {
        int n = frame_frag_encode(m_stream_buf, m_stream_len, m_stream_seq,
                                  m_stream_part, json, cap);
        if (n <= 0)
        {
            m_stream_parts = 0; /* defensive: never loop forever */
            break;
        }
        if (ble_frame_notify((const uint8_t *)json, (uint16_t)n) != 0)
        {
            break; /* HVN queue full or not connected — resume next tick */
        }
        m_stream_part++;
    }
}

void uwb_feed_frame_poll(void)
{
    listener_info_t *info = getListenerInfoPtr();
    if (info == NULL)
    {
        return;
    }

    static uint16_t last_head;
    static uint32_t last_enc_seq;
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
        /* copy the WHOLE frame into the shared buffer while the ring entry
         * is safe (inside the critical section); the summary reads its
         * first bytes and the streamer sends the rest */
        memcpy(m_stream_buf, p->msg.data,
               dlen < FRAME_FULL_MAX ? dlen : FRAME_FULL_MAX);
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
    /* fire the encrypted-energy marker on ANY failed-reception activity,
     * not just CRC/STS — AirTag energy often shows first as header
     * errors or timeouts, and we want the card to light up regardless */
    enc_seq = (uint32_t)info->event_counts_sfd_detect + (uint32_t)phe +
              (uint32_t)crcb + (uint32_t)stse + (uint32_t)to;
    taskEXIT_CRITICAL();

    /* snapshot the latest CRC-failed capture, if enabled. Only the small
     * metadata is grabbed here; the full bytes are copied out of
     * m_fail_data in the have_fail branch below (only when we'll use them),
     * so we don't clobber a fresh frame already staged in m_stream_buf. */
    static uint32_t last_fail_seq;
    uint8_t fts[5];
    uint16_t flen = 0;
    int frsl = 0, ffsl = 0;
    uint32_t fseq = 0;
    int have_fail = 0;
    if (m_capture_fail)
    {
        taskENTER_CRITICAL();
        fseq = m_fail_seq;
        if (fseq != last_fail_seq)
        {
            have_fail = 1;
            flen = m_fail_len;
            memcpy(fts, (const void *)m_fail_ts, sizeof fts);
            frsl = m_fail_rsl;
            ffsl = m_fail_fsl;
        }
        taskEXIT_CRITICAL();
    }

    /* snapshot the latest SP3/STS ranging telemetry (staged in the ISR) */
    static uint32_t last_rng_seq;
    uint8_t rts[5];
    int rq = 0;
    uint32_t rseq = 0;
    int have_rng = 0;
    taskENTER_CRITICAL();
    rseq = m_rng_seq;
    if (rseq != last_rng_seq)
    {
        have_rng = 1;
        memcpy(rts, (const void *)m_rng_ts, sizeof rts);
        rq = m_rng_q;
    }
    taskEXIT_CRITICAL();

    static char json[160];
    dwt_config_t *poll_cfg = get_dwt_config();
    int sts_on = (poll_cfg != NULL && poll_cfg->stsMode != DWT_STS_MODE_OFF);
    if (fresh && dlen == 0 && sts_on)
    {
        /* an STS/SP3 frame that completed via the OK path but carries no
         * payload — surface it as ranging telemetry, not a 0-byte frame */
        int16_t q = 0;
        listener2_readstsquality(&q);
        int nn = frame_encode_ranging(seq, ts, rsl100, fsl100, q, json,
                                      sizeof json);
        if (nn > 0)
        {
            ble_frame_push(json, (uint16_t)nn);
        }
    }
    else if (fresh)
    {
        /* a genuinely clean (CRC-good) frame — rare for encrypted traffic */
        int cfo_pphm =
            (int)((float)cfo * (CLOCK_OFFSET_PPM_TO_RATIO * 1e6 * 100));
        /* fresh frame already sits in m_stream_buf (copied in the critical
         * section above); summary reads its head, streamer sends the rest */
        int n = frame_encode(m_stream_buf, dlen, ts, cfo_pphm, rsl100, fsl100,
                             seq, 1, json, sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
        stream_set(dlen, seq); /* stream the whole frame too */
    }
    else if (have_fail)
    {
        /* CRC-failed frame captured off the error path: real bytes (header
         * decodes; STS body is ciphertext), flagged crc:0. Copy the full
         * frame out of the ISR capture buffer now (fresh didn't run, so
         * m_stream_buf is free to reuse). */
        last_fail_seq = fseq;
        taskENTER_CRITICAL();
        memcpy(m_stream_buf, (const void *)m_fail_data,
               flen < FRAME_FULL_MAX ? flen : FRAME_FULL_MAX);
        taskEXIT_CRITICAL();
        int n = frame_encode(m_stream_buf, flen, fts, 0, frsl, ffsl, fseq, 0,
                             json, sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
        stream_set(flen, fseq); /* stream the whole frame too */
    }
    else if (have_rng)
    {
        /* SP3/STS ranging frame: no payload bytes exist, but surface the
         * timing + signal + STS-quality telemetry (computed here in task
         * context, not the ISR). This is all a passive listener can get
         * from Apple's secured ranging. */
        last_rng_seq = rseq;
        int rsl = 0, fsl = 0;
        listener2_rssi_cal(&rsl, &fsl);
        int n = frame_encode_ranging(rseq, rts, rsl, fsl, rq, json,
                                     sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
    }
    else if (enc_seq != last_enc_seq)
    {
        /* no frame bytes this tick, but the radio logged failed
         * receptions — surface the energy signature so the card isn't
         * blank (this is the default AirTag view with capture off) */
        last_enc_seq = enc_seq;
        int n = frame_encode_encrypted(enc_seq, phe, crcb, stse, to,
                                       json, sizeof json);
        if (n > 0)
        {
            ble_frame_push(json, (uint16_t)n);
        }
    }

    /* push a few fragments of the staged full frame (started this tick or a
     * previous one); reuses the json scratch buffer, self-paced by the queue */
    stream_pump(json, sizeof json);
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
