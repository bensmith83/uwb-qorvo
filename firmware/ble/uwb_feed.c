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
#include "detector.h"
#include "driver_app_config.h"
#include "listener2.h"

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
