/*
 * BLE peripheral for the board-only UWB explorer (BLE_BUILD variant).
 *
 * Mirrors the Pi peripheral (uwb_explorer/ble.py) so the iOS app works
 * unchanged: advertises as "UWB" with service 6e5f0001-b5a3-f393-e0a9-
 * e50e24dcca9e and one read+notify characteristic (6e5f0002-...) carrying
 * the compact JSON from uwb_ble_payload() every 500 ms.
 *
 * ble_app_init() is called just before osKernelStart (breadcrumb.c's
 * __wrap_osKernelStart under BLE_BUILD): SoftDevice on, GATT up,
 * advertising started, notify task created.
 */

#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "app_error.h"
#include "ble.h"
#include "ble_advdata.h"
#include "ble_srv_common.h"
#include "nrf_ble_gatt.h"
#include "nrf_sdh.h"
#include "nrf_sdh_ble.h"
#include "nrf_sdh_freertos.h"

#define DEVICE_NAME "UWB" /* short: name + 128-bit UUID must fit 31 B adv */
#define APP_BLE_CONN_CFG_TAG 1
#define APP_BLE_OBSERVER_PRIO 3
#define ADV_INTERVAL 160        /* 100 ms, in 0.625 ms units */
#define NOTIFY_PERIOD_MS 500    /* uwb_explorer/ble.py default interval */
#define PAYLOAD_MAX 128         /* fits one notification at MTU 131 */

/* vendor-specific base: 6e5fxxxx-b5a3-f393-e0a9-e50e24dcca9e (LE) */
static ble_uuid128_t const m_base_uuid = {
    {0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
     0x93, 0xF3, 0xA3, 0xB5, 0x00, 0x00, 0x5F, 0x6E}};
#define UUID_SERVICE 0x0001
#define UUID_CHAR 0x0002

/* provided by uwb_feed.c: writes the current compact-JSON state */
extern uint16_t uwb_ble_payload(char *buf, uint16_t cap);
extern void uwb_feed_autostart(void);
extern void uwb_feed_flash_poll(void); /* deferred SD-safe config save */
/* breadcrumb.c: stores a diagnostic word readable over SWD */
extern void bread_note(uint32_t v);

NRF_BLE_GATT_DEF(m_gatt);

static uint8_t m_uuid_type;
static uint16_t m_service_handle;
static ble_gatts_char_handles_t m_char_handles;
static volatile uint16_t m_conn_handle = BLE_CONN_HANDLE_INVALID;

static uint8_t m_adv_handle = BLE_GAP_ADV_SET_HANDLE_NOT_SET;
static uint8_t m_enc_advdata[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
static ble_gap_adv_data_t m_adv_data = {
    .adv_data = {.p_data = m_enc_advdata, .len = 0},
    .scan_rsp_data = {.p_data = NULL, .len = 0},
};

static void advertising_start(void)
{
    ret_code_t err = sd_ble_gap_adv_start(m_adv_handle, APP_BLE_CONN_CFG_TAG);
    if (err != NRF_SUCCESS && err != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err);
    }
}

static void ble_evt_handler(ble_evt_t const *p_ble_evt, void *p_context)
{
    (void)p_context;
    switch (p_ble_evt->header.evt_id)
    {
    case BLE_GAP_EVT_CONNECTED:
        m_conn_handle = p_ble_evt->evt.gap_evt.conn_handle;
        break;
    case BLE_GAP_EVT_DISCONNECTED:
        m_conn_handle = BLE_CONN_HANDLE_INVALID;
        advertising_start();
        break;
    case BLE_GATTS_EVT_SYS_ATTR_MISSING:
        sd_ble_gatts_sys_attr_set(p_ble_evt->evt.gatts_evt.conn_handle,
                                  NULL, 0, 0);
        break;
    case BLE_GAP_EVT_PHY_UPDATE_REQUEST:
    {
        /* iOS requests 2M PHY right after connecting; an unanswered
         * request times out the LL procedure and drops the link */
        ble_gap_phys_t const phys = {
            .rx_phys = BLE_GAP_PHY_AUTO,
            .tx_phys = BLE_GAP_PHY_AUTO,
        };
        (void)sd_ble_gap_phy_update(p_ble_evt->evt.gap_evt.conn_handle,
                                    &phys);
        break;
    }
    case BLE_GAP_EVT_SEC_PARAMS_REQUEST:
        /* open characteristics, no pairing/bonding */
        (void)sd_ble_gap_sec_params_reply(
            p_ble_evt->evt.gap_evt.conn_handle,
            BLE_GAP_SEC_STATUS_PAIRING_NOT_SUPP, NULL, NULL);
        break;
    default:
        break;
    }
}
NRF_SDH_BLE_OBSERVER(m_ble_observer, APP_BLE_OBSERVER_PRIO,
                     ble_evt_handler, NULL);

static void services_init(void)
{
    ble_uuid128_t base = m_base_uuid;
    APP_ERROR_CHECK(sd_ble_uuid_vs_add(&base, &m_uuid_type));

    ble_uuid_t service_uuid = {.uuid = UUID_SERVICE, .type = m_uuid_type};
    APP_ERROR_CHECK(sd_ble_gatts_service_add(
        BLE_GATTS_SRVC_TYPE_PRIMARY, &service_uuid, &m_service_handle));

    static char init_payload[PAYLOAD_MAX];
    uint16_t init_len = uwb_ble_payload(init_payload, sizeof init_payload);

    ble_add_char_params_t p;
    memset(&p, 0, sizeof p);
    p.uuid = UUID_CHAR;
    p.uuid_type = m_uuid_type;
    p.max_len = PAYLOAD_MAX;
    p.init_len = init_len;
    p.p_init_value = (uint8_t *)init_payload;
    p.is_var_len = true;
    p.char_props.read = 1;
    p.char_props.notify = 1;
    p.read_access = SEC_OPEN;
    p.cccd_write_access = SEC_OPEN;
    APP_ERROR_CHECK(characteristic_add(m_service_handle, &p, &m_char_handles));
}

static void advertising_init(void)
{
    ble_uuid_t adv_uuid = {.uuid = UUID_SERVICE, .type = m_uuid_type};
    ble_advdata_t advdata;
    memset(&advdata, 0, sizeof advdata);
    advdata.name_type = BLE_ADVDATA_FULL_NAME;
    advdata.flags = BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE;
    advdata.uuids_complete.uuid_cnt = 1;
    advdata.uuids_complete.p_uuids = &adv_uuid;

    m_adv_data.adv_data.len = sizeof m_enc_advdata;
    APP_ERROR_CHECK(ble_advdata_encode(&advdata, m_adv_data.adv_data.p_data,
                                       &m_adv_data.adv_data.len));

    ble_gap_adv_params_t adv_params;
    memset(&adv_params, 0, sizeof adv_params);
    adv_params.properties.type =
        BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED;
    adv_params.interval = ADV_INTERVAL;
    adv_params.duration = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
    adv_params.primary_phy = BLE_GAP_PHY_1MBPS;
    adv_params.filter_policy = BLE_GAP_ADV_FP_ANY;
    APP_ERROR_CHECK(sd_ble_gap_adv_set_configure(&m_adv_handle, &m_adv_data,
                                                 &adv_params));
}

static void notify_task(void *arg)
{
    (void)arg;
    static char buf[PAYLOAD_MAX];
    /* let the default task run its startup hook first, then start the
     * UWB listener (same path as the CLI's LISTENER2 command) */
    vTaskDelay(pdMS_TO_TICKS(2000));
    uwb_feed_autostart();
    for (;;)
    {
        vTaskDelay(pdMS_TO_TICKS(NOTIFY_PERIOD_MS));
        uwb_feed_flash_poll();
        uint16_t len = uwb_ble_payload(buf, sizeof buf);
        uint16_t conn = m_conn_handle;
        if (conn != BLE_CONN_HANDLE_INVALID)
        {
            ble_gatts_hvx_params_t hvx;
            memset(&hvx, 0, sizeof hvx);
            hvx.handle = m_char_handles.value_handle;
            hvx.type = BLE_GATT_HVX_NOTIFICATION;
            hvx.p_len = &len;
            hvx.p_data = (uint8_t *)buf;
            /* CCCD off / not subscribed yet is fine — just skip */
            (void)sd_ble_gatts_hvx(conn, &hvx);
        }
        else
        {
            ble_gatts_value_t v = {
                .len = len, .offset = 0, .p_value = (uint8_t *)buf};
            (void)sd_ble_gatts_value_set(BLE_CONN_HANDLE_INVALID,
                                         m_char_handles.value_handle, &v);
        }
    }
}

/* The vendor app brings up its whole peripheral stack BEFORE the SoftDevice
 * exists, so at sd_softdevice_enable() time the NVIC is full of config the
 * SD rejects with NRF_ERROR_SDM_INCORRECT_INTERRUPT_CONFIGURATION (0x1001):
 * IRQs of SD-owned peripherals enabled, and priorities on SD-reserved
 * levels (0, 1, 4, 5).  Disable the former (the SD reclaims them; clock and
 * power events come back through nrf_sdh_soc observers) and remap the
 * latter onto app-legal levels. */
static void nvic_sanitize_for_sd(void)
{
    static const IRQn_Type sd_owned[] = {
        POWER_CLOCK_IRQn, RADIO_IRQn, RTC0_IRQn,  TIMER0_IRQn,
        RNG_IRQn,         ECB_IRQn,   CCM_AAR_IRQn,
        SWI2_EGU2_IRQn,   SWI4_EGU4_IRQn, SWI5_EGU5_IRQn,
    };
    for (unsigned i = 0; i < sizeof sd_owned / sizeof sd_owned[0]; i++)
    {
        NVIC_DisableIRQ(sd_owned[i]);
        NVIC_ClearPendingIRQ(sd_owned[i]);
    }
    for (IRQn_Type irq = (IRQn_Type)0; irq < (IRQn_Type)48; irq++)
    {
        unsigned j;
        for (j = 0; j < sizeof sd_owned / sizeof sd_owned[0]; j++)
        {
            if (sd_owned[j] == irq)
            {
                break;
            }
        }
        if (j < sizeof sd_owned / sizeof sd_owned[0])
        {
            continue;
        }
        uint32_t prio = NVIC_GetPriority(irq);
        if (prio == 0 || prio == 1)
        {
            NVIC_SetPriority(irq, 2);
        }
        else if (prio == 4 || prio == 5)
        {
            NVIC_SetPriority(irq, 6);
        }
    }
}

void ble_app_init(void)
{
    nvic_sanitize_for_sd();
    APP_ERROR_CHECK(nrf_sdh_enable_request());

    uint32_t ram_start = 0;
    APP_ERROR_CHECK(
        nrf_sdh_ble_default_cfg_set(APP_BLE_CONN_CFG_TAG, &ram_start));
    ret_code_t err = nrf_sdh_ble_enable(&ram_start);
    bread_note(ram_start); /* required app RAM base — read via SWD to tune */
    APP_ERROR_CHECK(err);

    APP_ERROR_CHECK(nrf_ble_gatt_init(&m_gatt, NULL));

    ble_gap_conn_sec_mode_t sec_mode;
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&sec_mode);
    APP_ERROR_CHECK(sd_ble_gap_device_name_set(
        &sec_mode, (uint8_t const *)DEVICE_NAME, strlen(DEVICE_NAME)));

    services_init();
    advertising_init();
    advertising_start();

    nrf_sdh_freertos_init(NULL, NULL);

    if (xTaskCreate(notify_task, "blefeed", 256, NULL, 2, NULL) != pdPASS)
    {
        APP_ERROR_HANDLER(0);
    }
}
