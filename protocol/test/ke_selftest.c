/* SHA-KE-1024 自检：与 Python 参考实现生成的向量比对 */
#include "../ks_ke1024.h"
#include <stdio.h>
#include <string.h>

static int failures = 0;

static void check(const char *label, const void *data, size_t len, const char *expect)
{
    static char hex[KE_SHA1024_HEX_LEN];
    ke_sha1024_hex(data, len, hex);
    if (strcmp(hex, expect) != 0) {
        printf("FAIL %s\n  want %s\n  got  %s\n", label, expect, hex);
        failures++;
    } else {
        printf("ok   %s\n", label);
    }
}

int main(void)
{
    static unsigned char buf[768];
    int i;

    check("empty", "", 0,
        "c4c3cc4ab16aff9d0ac4802468fd7dfac2785133349d14a4a4b74b3bb3c591a3"
        "1865db44b11337444fb97f6ce9f19f9ddad0428d3981d23841f2e130acf1dd5e"
        "5e4e494ac54153fd69709fb40e62778953892f4296349d07180eed5de7b3fefd"
        "538d446bb0200b139696df6364e2d935d333c68eb35f8f962b754e91b9f57ad2");

    check("abc", "abc", 3,
        "19e537f5fabe26188455cfc2c5aadca000122fbeb2fa4dd6a9145f790871a24a"
        "68b2035e1f84f158d7696ff5b4a7baad58920e254d00fce54b42ed6749cbe3ae"
        "8368b2f58e958a78e7e1d052a335d6d391e351cf1053c47515adf91f5c07cd14"
        "235a9c711eb7cd0f0e46cffa39b4fc0551718a26c7dea14b21c542c65ccf4422");

    check("KeShare protocol v1", "KeShare protocol v1", 19,
        "05d719ae6e3682fe3c7df34628f804f29d1d6198bd3d4dcea94ef08a688602e7"
        "1da8318d3453895c3783073318179cc76fce7d67e2b096f8843263a000f70a3b"
        "9f5a9cae1a1d2e9e5fc9ecd64e670e810cec1fe91f94c46d15f756ec3cf06db9"
        "5640aea23560b50beeaca73c9504da6f662df964686ecb56eeb5cc0115f3adb7");

    for (i = 0; i < (int)sizeof buf; i++) buf[i] = (unsigned char)(i & 0xff);
    check("768 bytes", buf, sizeof buf,
        "0438a990feafb49147b4bc5d1546cc5ca405da5d57f462f1d5e4209d50e636e"
        "5e0e1658f87696e2d509a10083423aa40930854cc8185e5376ba0a66df71c709"
        "99eb52c908a8418f12400a3cd73d9c62f35f4a42cf55deb52695d86fb049059b"
        "bab09faa0865a527a89b5b007b930ece89aebd0cf0b5bb899012709cce218d715");

    printf("%s (%d failures)\n", failures ? "FAILED" : "ALL OK", failures);
    return failures ? 1 : 0;
}
