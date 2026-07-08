/*
 * Boot breadcrumbs for the GCC-built CLI firmware (boot-hang bisection).
 *
 * The Pi sandbox can't keep a gdb server alive, so instead of breakpoints we
 * write stage markers into a reserved RAM window and read them back after the
 * hang with one-shot OpenOCD (`dump_image`).  Link with:
 *
 *   -Wl,--wrap=SystemInit -Wl,--wrap=BoardInit -Wl,--wrap=AppConfigInit
 *   -Wl,--wrap=EventManagerInit -Wl,--wrap=board_interface_init
 *   -Wl,--wrap=uwb_init -Wl,--wrap=DefaultTaskInit -Wl,--wrap=FlushTaskInit
 *   -Wl,--wrap=ControlTaskInit -Wl,--wrap=osKernelStart
 *
 * and shrink RAM LENGTH in the linker script from 0x20000 to 0x1FFE0 so the
 * window below is outside stack/heap/bss.
 *
 * Window layout (8 words at 0x2001FFE0):
 *   [0] last stage marker (0xC0DExxxx; even = entered, odd = returned)
 *   [1] aux value (uwb_init return code)
 *   [2] fault magic: 0xDEADFA11 = HardFault, 0x0BADAE00 = app_error
 *   [3] faulting PC
 *   [4] stacked LR / app_error id
 *   [5] SCB->CFSR
 *   [6] SCB->HFSR
 *   [7] SCB->BFAR / app_error info
 */

#include <stdint.h>

#define BREAD ((volatile uint32_t *)0x2001FFE0u)

static inline void mark(uint32_t v)
{
    BREAD[0] = v;
}

/* --- stage markers around every init call in main() ------------------- */

#define WRAP_VOID(fn, code)                       \
    extern void __real_##fn(void);                \
    void __wrap_##fn(void)                        \
    {                                             \
        mark(0xC0DE0000u + (code));               \
        __real_##fn();                            \
        mark(0xC0DE0001u + (code));               \
    }

WRAP_VOID(SystemInit, 0x02)
WRAP_VOID(BoardInit, 0x10)
WRAP_VOID(AppConfigInit, 0x20)
WRAP_VOID(EventManagerInit, 0x30)
WRAP_VOID(board_interface_init, 0x40)
WRAP_VOID(DefaultTaskInit, 0x60)
WRAP_VOID(FlushTaskInit, 0x70)
WRAP_VOID(ControlTaskInit, 0x80)

extern int __real_uwb_init(void);
int __wrap_uwb_init(void)
{
    mark(0xC0DE0050u);
    int r = __real_uwb_init();
    BREAD[1] = (uint32_t)r;
    mark(0xC0DE0051u);
    return r;
}

extern int __real_osKernelStart(void);
int __wrap_osKernelStart(void)
{
    mark(0xC0DE0090u);
#ifdef BLE_BUILD
    /* SoftDevice + GATT + advertising come up right before the scheduler */
    extern void ble_app_init(void);
    ble_app_init();
    mark(0xC0DE0091u);
#endif
    return __real_osKernelStart();
}

/* let other modules stash one diagnostic word (e.g. the app RAM base the
 * SoftDevice actually requires) where SWD dumps can see it */
void bread_note(uint32_t v)
{
    BREAD[1] = v;
}

/* runs from __libc_init_array: proves crt0 + data/bss init completed */
__attribute__((constructor)) static void bread_ctor(void)
{
    mark(0xC0DE0001u);
}

/* --- capture-and-spin fault handlers ----------------------------------- */

void bread_hardfault_c(uint32_t *frame)
{
    BREAD[2] = 0xDEADFA11u;
    BREAD[3] = frame[6]; /* stacked PC */
    BREAD[4] = frame[5]; /* stacked LR */
    BREAD[5] = *(volatile uint32_t *)0xE000ED28u; /* CFSR */
    BREAD[6] = *(volatile uint32_t *)0xE000ED2Cu; /* HFSR */
    BREAD[7] = *(volatile uint32_t *)0xE000ED38u; /* BFAR */
    for (;;)
        ;
}

__attribute__((naked)) void HardFault_Handler(void)
{
    __asm volatile(
        "tst lr, #4        \n"
        "ite eq            \n"
        "mrseq r0, msp     \n"
        "mrsne r0, psp     \n"
        "b bread_hardfault_c\n");
}

/* Overrides the SDK's __WEAK handler (app_error_weak.c), which would
 * otherwise NVIC_SystemReset() in a release build -> invisible boot loop. */
void app_error_fault_handler(uint32_t id, uint32_t pc, uint32_t info)
{
    BREAD[2] = 0x0BADAE00u;
    BREAD[3] = pc;
    BREAD[4] = id;
    BREAD[7] = info;
    __asm volatile("cpsid i");
    for (;;)
        ;
}
