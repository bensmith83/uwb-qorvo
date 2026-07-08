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
