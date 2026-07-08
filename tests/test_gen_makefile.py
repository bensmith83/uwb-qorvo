"""Tests for firmware/gen_makefile.py linker-script generation.

Regression for the boot-hang root cause found 2026-07-07: the generated
linker script placed .fconfig inline in .text, but the app erases the
flash page containing __fconfig_start at runtime (config.c:
nrf_nvmc_page_erase(&__fconfig_start)), destroying its own code and the
.dw_drivers table.  The vendor SES placement gives .fconfig a dedicated
4KB page at 0x1E000 (FCONFIG_START), vectors at 0x0, and code from
0x1F000 (INIT_START).  The generated script must match.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware"))
import gen_makefile  # noqa: E402

MDK = os.path.join(
    os.path.dirname(__file__), "..", ".fwbuild",
    "DWM3001CDK-DW3_QM33_SDK-FreeRTOS", "SDK_BSP", "Nordic",
    "NORDIC_SDK_17_1_0", "modules", "nrfx", "mdk")

needs_sdk = pytest.mark.skipif(
    not os.path.isdir(MDK), reason="vendor SDK not extracted (.fwbuild)")


@needs_sdk
class TestLinkerScript:
    @pytest.fixture(scope="class")
    def ld(self):
        return gen_makefile.build_linker_script(MDK)

    def test_fconfig_gets_its_own_flash_page(self, ld):
        # dedicated MEMORY region, page-aligned, matching SES FCONFIG_START
        assert re.search(
            r"FCONFIG \(r\w*\) : ORIGIN = 0x1[eE]000, LENGTH = 0x1000", ld)
        # .fconfig output section placed in that region, nothing else there
        sec = re.search(r"\.fconfig[^{]*\{(.*?)\}\s*>\s*FCONFIG", ld, re.S)
        assert sec, ".fconfig must be an output section in FCONFIG"
        assert "__fconfig_start" in sec.group(1)
        assert "KEEP(*(.fconfig" in sec.group(1)

    def test_fconfig_not_inside_text(self, ld):
        text = re.search(r"\.text :\s*\{(.*?)\}\s*>\s*FLASH", ld, re.S)
        assert text, "couldn't find .text section"
        assert ".fconfig" not in text.group(1)
        assert "__fconfig_start" not in text.group(1)

    def test_code_starts_after_fconfig_page(self, ld):
        # vendor: INIT_START=0x1F000; erasing the fconfig page must never
        # touch code
        assert re.search(
            r"FLASH \(rx\) : ORIGIN = 0x1[fF]000, LENGTH = 0x61000", ld)

    def test_vectors_stay_at_zero(self, ld):
        assert re.search(
            r"VECTORS \(rx\) : ORIGIN = 0x0, LENGTH = 0x1000", ld)
        sec = re.search(r"\.isr_vector[^{]*\{(.*?)\}\s*>\s*VECTORS", ld, re.S)
        assert sec, ".isr_vector must be an output section in VECTORS"
        assert "KEEP(*(.isr_vector))" in sec.group(1)
        # and .text must no longer contain the vectors
        text = re.search(r"\.text :\s*\{(.*?)\}\s*>\s*FLASH", ld, re.S)
        assert ".isr_vector" not in text.group(1)

    def test_custom_tables_still_kept(self, ld):
        # the other SES tables stay in code flash with KEEP
        for tab in (".dw_drivers", ".known_commands_anytime",
                    ".config_entry", ".rconfig"):
            assert f"KEEP(*({tab}" in ld


EMPROJ = os.path.join(
    os.path.dirname(__file__), "..", ".fwbuild",
    "DWM3001CDK-DW3_QM33_SDK-FreeRTOS", "Projects", "DW3_QM33_SDK",
    "FreeRTOS", "DWM3001CDK", "ses",
    "DWM3001CDK-DW3_QM33_SDK_CLI-FreeRTOS.emProject")

needs_proj = pytest.mark.skipif(
    not os.path.isfile(EMPROJ), reason="vendor SDK not extracted (.fwbuild)")


@needs_proj
class TestBuildVariants:
    """gen_makefile must support a BLE/SoftDevice build variant: extra app
    sources, extra defines, --wrap flags, and an app placed after the S113
    SoftDevice (vectors at the SD flash end, RAM base above SD RAM)."""

    def gen(self, tmp_path, *extra):
        argv = [EMPROJ, str(tmp_path)] + list(extra)
        assert gen_makefile.main(argv) == 0
        mk = open(os.path.join(tmp_path, "Makefile")).read()
        ld = open(os.path.join(tmp_path, "merged.ld")).read()
        return mk, ld

    def test_default_build_unchanged(self, tmp_path):
        mk, ld = self.gen(tmp_path)
        assert "cli-firmware" in mk
        assert re.search(r"VECTORS \(rx\) : ORIGIN = 0x0,", ld)

    def test_extra_sources_defines_wraps_target(self, tmp_path):
        mk, ld = self.gen(
            tmp_path,
            "--src", "/x/ble_service.c", "--src", "/x/uwb_feed.c",
            "--define", "BLE_BUILD=1", "--define", "NRF_SDH_ENABLED=1",
            "--wrap", "nrf_nvmc_page_erase",
            "--target", "ble-firmware")
        assert "/x/ble_service.c" in mk and "/x/uwb_feed.c" in mk
        assert "-DBLE_BUILD=1" in mk and "-DNRF_SDH_ENABLED=1" in mk
        assert "-Wl,--wrap=nrf_nvmc_page_erase" in mk
        assert "TARGET = ble-firmware" in mk
        # extra sources must land BEFORE the rules so OBJ picks them up
        assert mk.index("/x/ble_service.c") < mk.index("all: $(TARGET).hex")

    def test_softdevice_map_moves_vectors_and_ram(self, tmp_path):
        mk, ld = self.gen(tmp_path, "--app-base", "0x1c000",
                          "--ram-base", "0x20002400")
        # app vector table at the SoftDevice flash end, own page
        assert re.search(
            r"VECTORS \(rx\) : ORIGIN = 0x1c000, LENGTH = 0x1000", ld)
        # fconfig page and code base unchanged (vendor map)
        assert re.search(
            r"FCONFIG \(r\w*\) : ORIGIN = 0x1[eE]000, LENGTH = 0x1000", ld)
        assert re.search(
            r"FLASH \(rx\) : ORIGIN = 0x1f000, LENGTH = 0x61000", ld)
        # app RAM starts above the SoftDevice's RAM, length shrinks to match
        assert re.search(
            r"RAM \(rwx\) : ORIGIN = 0x20002400, LENGTH = 0x1dc00", ld)

    def test_extra_include_dirs_prepended(self, tmp_path):
        # SoftDevice headers must shadow same-named no-SD headers
        # (nrf_error.h/nrf_soc.h/nrf_nvic.h in drivers_nrf/nrf_soc_nosd),
        # so --inc dirs go FIRST on the include path.
        mk, ld = self.gen(tmp_path, "--inc", "/sdk/s113/headers",
                          "--inc", "/repo/firmware/ble")
        assert "-I/sdk/s113/headers" in mk
        assert "-I/repo/firmware/ble" in mk
        assert mk.index("-I/sdk/s113/headers") < mk.index("nrf_soc_nosd")
        assert (mk.index("-I/sdk/s113/headers")
                < mk.index("-I/repo/firmware/ble"))

    def test_sdh_observer_sections_present(self, tmp_path):
        # nrf_sdh registers observers via named flash sections; without these
        # the SoftDevice dispatch tables are empty and BLE events vanish.
        _, ld = self.gen(tmp_path)
        for sec in ("sdh_soc_observers", "sdh_ble_observers",
                    "sdh_req_observers", "sdh_state_observers",
                    "sdh_stack_observers"):
            assert f"__start_{sec}" in ld and f"KEEP(*(SORT(.{sec}*)))" in ld
        assert "__start_fs_data" in ld  # fstorage_sd RAM section
