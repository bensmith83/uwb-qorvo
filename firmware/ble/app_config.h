/*
 * sdk_config overrides for the BLE build (included via -DUSE_APP_CONFIG;
 * every entry in the vendor sdk_config.h is #ifndef-guarded, so defining a
 * key here wins without editing the 11.9k-line vendor file).
 *
 * Pairs with: -DSOFTDEVICE_PRESENT -DS113 -DBLE_STACK_SUPPORT_REQD and the
 * S113 memory map (app vectors @0x1C000, RAM base above the SD reservation).
 */
#ifndef APP_CONFIG_H
#define APP_CONFIG_H

/* --- SoftDevice handler --- */
#define NRF_SDH_ENABLED 1
#define NRF_SDH_BLE_ENABLED 1
#define NRF_SDH_SOC_ENABLED 1
/* polling model: nrf_sdh_freertos.c owns SD_EVT_IRQHandler (task notify);
 * model 0 would make nrf_sdh.c define a second one -> duplicate symbol */
#define NRF_SDH_DISPATCH_MODEL 2

/* one peripheral link, one vendor-specific UUID base (6e5f....) */
#define NRF_SDH_BLE_PERIPHERAL_LINK_COUNT 1
#define NRF_SDH_BLE_CENTRAL_LINK_COUNT 0
#define NRF_SDH_BLE_TOTAL_LINK_COUNT 1
#define NRF_SDH_BLE_VS_UUID_COUNT 1

/* payload is ~70 B compact JSON; 131 lets one notification carry 128 B */
#define NRF_SDH_BLE_GATT_MAX_MTU_SIZE 131
#define NRF_BLE_GATT_ENABLED 1

/* SoftDevice owns RTC0 and FreeRTOS ticks on RTC1; the Qorvo HAL picks
 * RTC2 when it's enabled (HAL_RTC.c: "SD using 0; FreeRTOS using 1"). */
#define NRFX_RTC0_ENABLED 0
#define NRFX_RTC2_ENABLED 1

#endif /* APP_CONFIG_H */
