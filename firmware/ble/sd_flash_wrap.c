/*
 * SoftDevice-safe config save (BLE build). Link with:
 *   -Wl,--wrap=save_bssConfig
 *
 * The vendor saves NVM config with direct NVMC writes
 * (config.c: nrf_nvmc_page_erase(&__fconfig_start) + write_bytes), called
 * from inside CMD_ENTER_CRITICAL() (f_save et al). Direct NVMC is illegal
 * while the SD radio is active, and calling sd_flash_* from that critical
 * section asserts the SoftDevice. So with the SD enabled we DEFER: the
 * wrap snapshots the rconfig block (with its CRC, mirroring the real
 * save_bssConfig) and returns; uwb_feed_flash_poll() — called from the
 * BLE notify task every 500 ms — performs the erase+write via sd_flash_*,
 * waiting on the NRF_EVT_FLASH_OPERATION_* SoC events dispatched by
 * nrf_sdh. The "ok" reply thus precedes the actual flash write by ~1 s.
 *
 * Pre-SoftDevice (boot-time restore path) falls through to the real
 * implementation.
 */

#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "deca_error.h"
#include "nrf_sdh.h"
#include "nrf_sdh_soc.h"
#include "nrf_soc.h"

#define FLASH_PAGE_SIZE 4096u
#define SAVE_BUF_MAX 0x400u /* FCONFIG_SIZE */

extern uint8_t __fconfig_start[];
extern uint8_t __rconfig_start[];
extern uint8_t __rconfig_end[];
extern uint8_t __rconfig_crc_end[];
extern uint16_t calc_crc16(const uint8_t *data, uint16_t len);
extern error_e __real_save_bssConfig(void);

static uint8_t m_buf[SAVE_BUF_MAX] __attribute__((aligned(4)));
static volatile uint16_t m_pending; /* bytes to write, 0 = idle */
static volatile int m_op_result;    /* 0 pending, 1 success, -1 error */

static void soc_evt_handler(uint32_t evt_id, void *p_context)
{
    (void)p_context;
    if (evt_id == NRF_EVT_FLASH_OPERATION_SUCCESS)
    {
        m_op_result = 1;
    }
    else if (evt_id == NRF_EVT_FLASH_OPERATION_ERROR)
    {
        m_op_result = -1;
    }
}
NRF_SDH_SOC_OBSERVER(m_flash_obs, 0, soc_evt_handler, NULL);

error_e __wrap_save_bssConfig(void)
{
    if (!nrf_sdh_is_enabled())
    {
        return __real_save_bssConfig();
    }
    uint16_t len = (uint16_t)(__rconfig_end - __rconfig_start);
    uint16_t total = (uint16_t)(__rconfig_crc_end - __rconfig_start);
    if (total > SAVE_BUF_MAX)
    {
        return _ERR;
    }
    /* mirror the real save: CRC lives in the .rconfig_crc slot right
     * after the config block and is written out with it */
    uint16_t crc = calc_crc16((uint8_t *)__rconfig_start, len);
    memcpy(m_buf, __rconfig_start, total);
    memcpy(m_buf + len, &crc, sizeof crc);
    m_pending = total;
    return _NO_ERR;
}

/* run one flash step from task context; returns when idle again */
void uwb_feed_flash_poll(void)
{
    if (m_pending == 0 || !nrf_sdh_is_enabled())
    {
        return;
    }
    uint16_t total = m_pending;

    m_op_result = 0;
    if (sd_flash_page_erase((uint32_t)__fconfig_start / FLASH_PAGE_SIZE) !=
        NRF_SUCCESS)
    {
        return; /* busy — retry on the next tick */
    }
    for (int i = 0; i < 1000 && m_op_result == 0; i++)
    {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (m_op_result != 1)
    {
        return;
    }

    m_op_result = 0;
    if (sd_flash_write((uint32_t *)__fconfig_start, (uint32_t *)m_buf,
                       (total + 3u) / 4u) != NRF_SUCCESS)
    {
        return;
    }
    for (int i = 0; i < 1000 && m_op_result == 0; i++)
    {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    if (m_op_result == 1)
    {
        m_pending = 0;
    }
}
