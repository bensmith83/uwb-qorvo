#!/usr/bin/env python3
"""Generate a GCC Makefile from a Qorvo QM33 SDK SES .emProject.

The Qorvo DWM3001CDK app only ships SEGGER Embedded Studio projects, and
SES's `emBuild` has no ARM-host port, so it can't build on a Raspberry Pi.
This parses the .emProject (source list, include dirs, preprocessor defines,
prebuilt libs) and emits a Makefile that builds with arm-none-eabi-gcc,
swapping SES's startup/crt0 for the nRF5 SDK's GCC startup + linker script.

    python3 firmware/gen_makefile.py <path/to/xxx.emProject> <out_dir>

Then: cd <out_dir> && make
"""

from __future__ import annotations

import html
import os
import re
import sys

# SES-specific startup we must NOT compile under GCC (replaced by the SDK's).
SES_ONLY = ("thumb_crt0.s", "ses_startup_nrf52833.s", "ses_startup_nrf_common.s")

# The app registers CLI commands / DW drivers / config via custom linker-section
# tables that SES declares in flash_placement.xml. We translate them to GNU ld
# and MERGE them into a copy of the SDK's nrf_common.ld (INSERT-with-`-T` is
# unreliable in this binutils). KEEP guards them against --gc-sections; the
# start/end symbols must bracket each table exactly as SES did.

# Injected inside the .text output section (FLASH), before __etext.
FLASH_TABLES = r"""
        /* --- Qorvo QM33 SDK custom tables (from SES flash_placement.xml) --- */
        . = ALIGN(4);
        __fconfig_start = .;
        KEEP(*(.fconfig .fconfig.*))
        . = ALIGN(4);
        __dw_drivers_start = .;
        KEEP(*(.dw_drivers .dw_drivers.*))
        __dw_drivers_end = .;
        . = ALIGN(4);
        __known_commands_start = .;
        KEEP(*(.known_commands_anytime .known_commands_anytime.*))
        __known_commands_app_start = .;
        KEEP(*(.known_commands_app .known_commands_app.*))
        __known_app_subcommands_start = .;
        KEEP(*(.known_app_subcommands .known_app_subcommands.*))
        __known_commands_ilde_start = .;
        KEEP(*(.known_commands_ilde .known_commands_ilde.*))
        __known_commands_service_start = .;
        KEEP(*(.known_commands_service .known_commands_service.*))
        __known_commands_end = .;
        . = ALIGN(4);
        __known_apps_start = .;
        KEEP(*(.known_apps .known_apps.*))
        __known_apps_end = .;
        . = ALIGN(4);
        __config_entry_start = .;
        KEEP(*(.config_entry .config_entry.*))
        __config_entry_end = .;
"""

# Injected as its own NOLOAD section in RAM, after .bss.
RAM_TABLES = r"""
    .rconfig (NOLOAD) :
    {
        . = ALIGN(4);
        __rconfig_start = .;
        KEEP(*(.rconfig .rconfig.*))
        . = ALIGN(4);
        __rconfig_end = .;
        KEEP(*(.rconfig_crc .rconfig_crc.*))
        . = ALIGN(4);
        __rconfig_crc_end = .;
    } > RAM
"""


def build_linker_script(mdk: str) -> str:
    """Merge our custom tables into the SDK's linker script (nrf52833_xxaa.ld +
    nrf_common.ld) and return the combined text."""
    xxaa = open(os.path.join(mdk, "nrf52833_xxaa.ld")).read()
    common = open(os.path.join(mdk, "nrf_common.ld")).read()
    # flash tables: inside .text, right after the .eh_frame keep (before } > FLASH)
    common = re.sub(r'(KEEP\(\*\(\.eh_frame\*\)\))',
                    r'\1\n' + FLASH_TABLES, common, count=1)
    # ram tables: right after the .bss section closes
    common = re.sub(r'(__bss_end__\s*=\s*\.;\s*\}\s*>\s*RAM)',
                    r'\1\n' + RAM_TABLES, common, count=1)
    return xxaa.replace('INCLUDE "nrf_common.ld"', common)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    emproj, out_dir = sys.argv[1], sys.argv[2]
    ses_dir = os.path.dirname(os.path.abspath(emproj))
    xml = open(emproj, encoding="utf-8", errors="replace").read()

    def resolve(p: str) -> str:
        return os.path.normpath(os.path.join(ses_dir, p))

    # --- sources ---
    files = []
    seen = set()
    for m in re.findall(r'file_name="([^"]+)"', xml):
        if m in seen:
            continue
        seen.add(m)
        files.append(m)

    c_srcs, s_srcs, libs = [], [], []
    for f in files:
        base = os.path.basename(f)
        if base in SES_ONLY or f.startswith("$(StudioDir)"):
            continue
        if f.endswith(".c"):
            c_srcs.append(resolve(f))
        elif f.endswith(".s") or f.endswith(".S"):
            s_srcs.append(resolve(f))
        elif f.endswith(".a"):
            libs.append(resolve(f))

    # --- SDK GCC startup + system (replace the SES startup) ---
    # find the NORDIC_SDK mdk dir from an existing include/source
    mdk = None
    for cand in c_srcs + [resolve(i) for i in re.findall(r'c_user_include_directories="([^"]*)"', xml)]:
        idx = cand.find("NORDIC_SDK_17_1_0")
        if idx != -1:
            mdk = os.path.join(cand[:idx], "NORDIC_SDK_17_1_0", "modules", "nrfx", "mdk")
            break
    if not mdk or not os.path.isdir(mdk):
        print("ERROR: could not locate the nRF5 SDK mdk dir", file=sys.stderr)
        return 1
    s_srcs = [os.path.join(mdk, "gcc_startup_nrf52833.S")]  # GCC startup only
    system_c = os.path.join(mdk, "system_nrf52833.c")
    if system_c not in c_srcs:
        c_srcs.append(system_c)

    # --- include dirs ---
    incs = []
    for blob in re.findall(r'c_user_include_directories="([^"]*)"', xml):
        for i in html.unescape(blob).split(";"):
            i = i.strip()
            if not i or i.startswith("$(StudioDir)"):
                continue
            r = resolve(i)
            if r not in incs:
                incs.append(r)
    if mdk not in incs:
        incs.append(mdk)
    cmsis = os.path.join(os.path.dirname(mdk), "..", "..", "components", "toolchain", "cmsis", "include")
    cmsis = os.path.normpath(cmsis)
    if os.path.isdir(cmsis) and cmsis not in incs:
        incs.append(cmsis)

    # --- defines ---
    dblob = re.findall(r'c_preprocessor_definitions="([^"]*)"', xml)
    defines = [d.strip() for d in html.unescape(dblob[0]).split(";") if d.strip()] if dblob else []
    for extra in ("BOARD_CUSTOM", "FLOAT_ABI_HARD", "CONFIG_GPIO_AS_PINRESET", "__HEAP_SIZE=8192", "__STACK_SIZE=8192"):
        key = extra.split("=")[0]
        if not any(x.split("=")[0] == key for x in defines):
            defines.append(extra)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "merged.ld"), "w") as f:
        f.write(build_linker_script(mdk))
    ld = "merged.ld"

    mk = os.path.join(out_dir, "Makefile")
    with open(mk, "w") as f:
        f.write(_render(c_srcs, s_srcs, libs, incs, defines, ld, mdk))
    print(f"wrote {mk}")
    print(f"  {len(c_srcs)} C, {len(s_srcs)} asm, {len(libs)} libs, {len(incs)} include dirs")
    return 0


def _render(c_srcs, s_srcs, libs, incs, defines, ld, mdk) -> str:
    cpu = "-mcpu=cortex-m4 -mthumb -mabi=aapcs -mfloat-abi=hard -mfpu=fpv4-sp-d16"
    L = []
    L.append("# Generated by firmware/gen_makefile.py — do not edit by hand.")
    L.append("PREFIX = arm-none-eabi-")
    L.append("CC = $(PREFIX)gcc")
    L.append("OBJCOPY = $(PREFIX)objcopy")
    L.append("SIZE = $(PREFIX)size")
    L.append("TARGET = cli-firmware")
    L.append(f"CPU = {cpu}")
    L.append("OPT = -O2 -g3")
    L.append("")
    L.append("INCLUDES = \\")
    for i in incs:
        L.append(f"  -I{i} \\")
    L.append("")
    L.append("DEFINES = \\")
    for d in defines:
        d = d.replace('"', '\\"')  # keep quoted string values (e.g. UWBMAC_BUF_PLATFORM_H)
        L.append(f"  -D{d} \\")
    L.append("")
    L.append("CFLAGS = $(CPU) $(OPT) $(INCLUDES) $(DEFINES) \\")
    L.append("  -Wall -ffunction-sections -fdata-sections -fno-strict-aliasing \\")
    L.append("  -fno-builtin -fshort-enums --std=gnu11")
    L.append("ASFLAGS = $(CPU) $(OPT) $(DEFINES) -x assembler-with-cpp")
    L.append(f"LDSCRIPT = {ld}")
    L.append(f"LDFLAGS = $(CPU) -L{mdk} -T$(LDSCRIPT) \\")
    L.append("  --specs=nano.specs -Wl,--gc-sections -Wl,-Map=$(TARGET).map \\")
    # --whole-archive on the vendor libs so their linker-section registrations
    # (.dw_drivers driver structs, etc.) are pulled in; KEEP + gc-sections then
    # retains the tables and drops the rest.
    L.append("  -Wl,--start-group -Wl,--whole-archive $(LIBS) -Wl,--no-whole-archive \\")
    L.append("  -lc -lm -lnosys -Wl,--end-group")
    L.append("")
    L.append("LIBS = \\")
    for lib in libs:
        L.append(f"  {lib} \\")
    L.append("")
    csrc = " \\\n  ".join(c_srcs)
    ssrc = " \\\n  ".join(s_srcs)
    L.append(f"C_SRC = \\\n  {csrc}")
    L.append(f"S_SRC = \\\n  {ssrc}")
    L.append("")
    L.append("OBJ = $(addprefix obj/,$(notdir $(C_SRC:.c=.o))) $(addprefix obj/,$(notdir $(S_SRC:.S=.o)))")
    L.append("VPATH = $(sort $(dir $(C_SRC)) $(dir $(S_SRC)))")
    L.append("")
    L.append("all: $(TARGET).hex")
    L.append("obj:; mkdir -p obj")
    L.append("obj/%.o: %.c | obj\n\t$(CC) $(CFLAGS) -c $< -o $@")
    L.append("obj/%.o: %.S | obj\n\t$(CC) $(ASFLAGS) -c $< -o $@")
    L.append("$(TARGET).elf: $(OBJ)\n\t$(CC) $(OBJ) $(LDFLAGS) -o $@\n\t$(SIZE) $@")
    L.append("$(TARGET).hex: $(TARGET).elf\n\t$(OBJCOPY) -O ihex $< $@")
    L.append("clean:; rm -rf obj $(TARGET).elf $(TARGET).hex $(TARGET).map")
    L.append(".PHONY: all clean")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
