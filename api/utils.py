import secrets, re, time

def gen_flag() -> str:
    # 128-bit (16 bytes) random number -> 32 hexadecimal characters
    return f"FLAG{{{secrets.token_hex(16)}}}"

def current_tick(period_sec: int = 120) -> int:
    return int(time.time() // period_sec)

# Match the length above: 32 hexadecimal characters
FLAG_RE = re.compile(r"^FLAG\{[0-9a-f]{32}\}$")
