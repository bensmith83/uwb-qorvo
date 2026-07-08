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

/* SoftDevice owns RTC0 and TIMER0; FreeRTOS ticks on RTC1. The Qorvo HAL
 * picks RTC2 when it's enabled (HAL_RTC.c: "SD using 0; FreeRTOS using 1")
 * and TIMER1 when it's enabled (HAL_timer.c TIMERC_ID: "SD using 0").
 *
 * CRITICAL: the vendor sdk_config defines the LEGACY driver keys
 * (RTC_ENABLED/TIMER_ENABLED), and integration/nrfx/legacy/
 * apply_old_config.h then OVERRIDES every NRFX_*_ENABLED with the legacy
 * value — so the legacy instance keys below are the ones that matter;
 * the NRFX_* ones are set for consistency only. Missing this put TIMERC
 * on the SD's TIMER0 -> SoftDevice assert when the listener started. */
#define RTC0_ENABLED 0
#define RTC2_ENABLED 1
#define NRFX_RTC0_ENABLED 0
#define NRFX_RTC2_ENABLED 1
#define TIMER0_ENABLED 0
#define TIMER1_ENABLED 1
#define NRFX_TIMER0_ENABLED 0
#define NRFX_TIMER1_ENABLED 1

/* The vendor config enables the nRF52840 SPIM3 anomaly-198 workaround,
 * which writes the undocumented POWER register 0x40000E00 around EVERY
 * SPIM3 (DW3110) transfer. Under the SoftDevice that's a protected-
 * peripheral write -> NRF_FAULT_ID_APP_MEMACC on the first SPI transfer
 * after SD enable (== listener start). This chip is an nRF52833 — the
 * 52840 anomaly doesn't apply; turn the workaround off. */
#define NRFX_SPIM3_NRF52840_ANOMALY_198_WORKAROUND_ENABLED 0
#define SPIM3_NRF52840_ANOMALY_198_WORKAROUND_ENABLED 0

#endif /* APP_CONFIG_H */
