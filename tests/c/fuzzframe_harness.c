/* stdin/stdout harness for fuzzframe.c (see tests/test_fuzzframe.py).
 *
 * Protocol (one command per line):
 *   Z <case_id>   -> build case, print the builder output + PHY facets:
 *                    "<hex|-> <len> <phr> <has_phr> <sts_sp> <sts_len> <illegal>"
 *   X <argstr>    -> run the CLI path fuzz_cli(argstr) with capturing hooks
 *                    installed; print "<rc> <order|-> <hex|-> <len>", where
 *                    order is the pause/tx/resume call sequence ("PTR").
 *                    argstr "-" means the empty string.
 */
#include <stdio.h>
#include <string.h>

#include "fuzzframe.h"

/* --- capturing emission hooks (verify half-duplex ordering + TX bytes) --- */
static char g_order[8];
static uint8_t g_tx_buf[FUZZ_FRAME_MAX];
static uint16_t g_tx_len;

static void cap_reset(void)
{
    g_order[0] = '\0';
    g_tx_len = 0;
}
static void cap_append(char c)
{
    size_t n = strlen(g_order);
    if (n + 1 < sizeof g_order)
    {
        g_order[n] = c;
        g_order[n + 1] = '\0';
    }
}
static void cap_pause(void) { cap_append('P'); }
static void cap_resume(void) { cap_append('R'); }
static int cap_tx(const uint8_t *buf, uint16_t len)
{
    cap_append('T');
    if (len > FUZZ_FRAME_MAX)
        len = FUZZ_FRAME_MAX;
    memcpy(g_tx_buf, buf, len);
    g_tx_len = len;
    return 0;
}

static void put_hex(const uint8_t *buf, uint16_t len)
{
    if (len == 0)
    {
        fputs("-", stdout);
        return;
    }
    for (uint16_t i = 0; i < len; i++)
        printf("%02X", buf[i]);
}

int main(void)
{
    char kind[4], arg[64];
    while (scanf("%3s", kind) == 1)
    {
        if (kind[0] == 'Z')
        {
            int id;
            if (scanf(" %d", &id) != 1)
                break;
            fuzz_frame_t f;
            memset(&f, 0, sizeof f);
            int rc = fuzz_build(id, &f);
            if (rc != 0)
            {
                puts("ERR");
                continue;
            }
            put_hex(f.buf, f.len);
            printf(" %u %u %u %d %d %u\n", (unsigned)f.len, (unsigned)f.phr,
                   (unsigned)f.has_phr, f.sts_sp, f.sts_len,
                   (unsigned)f.sts_illegal);
        }
        else if (kind[0] == 'X')
        {
            if (scanf(" %63s", arg) != 1)
                break;
            const char *a = (strcmp(arg, "-") == 0) ? "" : arg;
            cap_reset();
            fuzz_set_hooks(cap_tx, cap_pause, cap_resume);
            int rc = fuzz_cli(a);
            printf("%d ", rc);
            fputs(g_order[0] ? g_order : "-", stdout);
            fputc(' ', stdout);
            put_hex(g_tx_buf, g_tx_len);
            printf(" %u\n", (unsigned)g_tx_len);
        }
        else
        {
            break;
        }
    }
    return 0;
}
