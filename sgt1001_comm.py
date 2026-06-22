#!/usr/bin/env python3
"""
sgt1001_comm.py — SGT1001 CyberRock-Token communication tool

Supports two physical interfaces:
  1. Direct SPI via /dev/spidevX.Y (if spidev is enabled in kernel/DT)
  2. STM32 USB-serial bridge via /dev/ttyACM0 (transparent SPI passthrough)

The STM32 bridge implements a simple transparent protocol:
  - Write N bytes  → forwarded as MOSI over SPI (CSN asserted for the whole frame)
  - Read back N bytes → MISO data from the SGT1001

Usage:
    python3 sgt1001_comm.py                        # probe (ID + BIST + auth)
    python3 sgt1001_comm.py --iface serial id      # Mode 1: 256-bit Token ID
    python3 sgt1001_comm.py --iface serial bist    # Mode 255: built-in self-test
    python3 sgt1001_comm.py --iface serial auth    # Mode 3: challenge/response (random CW)
    python3 sgt1001_comm.py --iface spi id         # direct SPI via /dev/spidev0.0

Options:
    --iface serial|spi   interface (default: auto-detect)
    --port  /dev/ttyACMx serial port (default: /dev/ttyACM0)
    --baud  N            baud rate (default: 115200, ignored for USB CDC)
    --bus   N            SPI bus number (default: 0)
    --dev   N            SPI device number (default: 0)
    --hz    N            SPI clock in Hz, <= 10 MHz (default: 1000000)
"""

import os, sys, time, argparse

# ── SGT1001 mode definitions ────────────────────────────────────────────────
# (header_bytes, total_frame_bytes)
MODES = {
    0:   (bytes.fromhex("00000000"), 30),   # GS1 SGTIN-198
    1:   (bytes.fromhex("01000000"), 37),   # Token Identification
    3:   (bytes.fromhex("03000800"), 87),   # Token Authentication
    5:   (bytes.fromhex("05000800"), 87),   # Host Authentication
    6:   (bytes.fromhex("06000800"), 103),  # Host Auth + Ephemeral Key
    7:   (bytes.fromhex("07000800"), 103),  # Token Auth + Ephemeral Key
    255: (bytes.fromhex("ff000000"), 72),   # Built-In Self-Test
}

ZERO32 = bytes(32)
FF32   = b"\xff" * 32


# ── Interface classes ────────────────────────────────────────────────────────

class SpidevInterface:
    """Direct kernel SPI via spidev."""

    def __init__(self, bus=0, dev=0, hz=1_000_000):
        import spidev
        self._spi = spidev.SpiDev()
        self._spi.open(bus, dev)
        self._spi.mode          = 0b00   # CPOL=0, CPHA=0
        self._spi.max_speed_hz  = hz
        self._spi.lsbfirst      = False  # MSB first

    def transact(self, tx: bytes) -> bytes:
        return bytes(self._spi.xfer2(list(tx)))

    def close(self):
        self._spi.close()


class SerialBridgeInterface:
    """STM32 USB-serial bridge via /dev/ttyACM0 using AT+SPI= protocol.

    Protocol (discovered by probing):
      → AT+SPI=<hex-encoded MOSI frame>\\r\\n
      ← <00 status byte as 2 hex chars><hex-encoded MISO frame>\\r
    """

    def __init__(self, port="/dev/ttyACM0", baud=115200):
        import serial
        self._s = serial.Serial(port, baud, timeout=5)
        time.sleep(0.05)
        self._s.reset_input_buffer()

    def transact(self, tx: bytes) -> bytes:
        self._s.reset_input_buffer()
        cmd = "AT+SPI=" + tx.hex() + "\r\n"
        self._s.write(cmd.encode())
        self._s.flush()

        # Response: <status_hex><miso_hex>\r
        resp = self._s.read_until(b"\r", size=(len(tx) + 1) * 2 + 4)
        resp = resp.rstrip(b"\r\n")

        if len(resp) < 2:
            raise IOError(
                f"Serial bridge: empty response. "
                "Check /dev/ttyACM0 permissions and STM32 connection."
            )

        # First 2 chars = status byte hex (00 = OK)
        status = int(resp[:2], 16)
        if status != 0:
            raise IOError(f"STM32 bridge error status: 0x{status:02x}")

        miso_hex = resp[2:].decode("ascii", errors="replace")
        if len(miso_hex) != len(tx) * 2:
            raise IOError(
                f"Serial bridge: expected {len(tx) * 2} MISO hex chars, "
                f"got {len(miso_hex)}: {miso_hex!r}"
            )
        return bytes.fromhex(miso_hex)

    def close(self):
        self._s.close()


def auto_interface(args):
    """Pick the best available interface."""
    if args.iface == "spi":
        return SpidevInterface(args.bus, args.dev, args.hz)
    if args.iface == "serial":
        return SerialBridgeInterface(args.port, args.baud)

    # Auto-detect: prefer spidev, fall back to serial
    spidev_path = f"/dev/spidev{args.bus}.{args.dev}"
    if os.path.exists(spidev_path):
        print(f"[auto] using spidev: {spidev_path}")
        return SpidevInterface(args.bus, args.dev, args.hz)

    if os.path.exists(args.port):
        print(f"[auto] using serial bridge: {args.port}")
        return SerialBridgeInterface(args.port, args.baud)

    sys.exit(
        f"No SPI interface found.\n"
        f"  - Direct SPI:  {spidev_path} not present (enable spidev in DT)\n"
        f"  - Serial bridge: {args.port} not present\n"
        f"Use --iface spi|serial to override."
    )


# ── Low-level transaction builder ────────────────────────────────────────────

def build_tx(mode: int, challenge: bytes = None) -> bytes:
    header, length = MODES[mode]
    tx = bytearray(length)
    tx[0:4] = header
    if challenge is not None:
        if len(challenge) != 32:
            raise ValueError("challenge must be exactly 32 bytes (256 bits)")
        # CW occupies bytes 5-36 (after 4-byte header + 1-byte gap)
        tx[5:37] = challenge
    return bytes(tx)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_id(iface) -> bytes:
    rx = iface.transact(build_tx(1))
    tid = rx[5:37]
    print("Mode 1  Token ID (TID) :", tid.hex())
    if tid in (ZERO32, FF32):
        print("  !! all zeros or all 0xFF — check wiring, power, SPI mode/speed")
    return tid


def cmd_bist(iface) -> bool:
    rx = iface.transact(build_tx(255))
    tid    = rx[5:37]
    brw    = rx[38:54]
    bek    = rx[54:70]
    result = rx[71] if len(rx) > 71 else 0xFF

    verdict = {
        0x50: "PASS ✓",
        0x70: "FAIL ✗",
        0x00: "NOT_PROVISIONED (HMAC key not yet loaded)",
    }.get(result, f"UNKNOWN 0x{result:02X}")
    print("Mode 255  BIST result :", verdict)
    print("          TID         :", tid.hex())
    print("          BRW         :", brw.hex(), "(zeros = no HMAC key)" if brw == bytes(16) else "")
    print("          BEK         :", bek.hex())
    return result == 0x50


def cmd_auth(iface, challenge: bytes) -> bool:
    rx    = iface.transact(build_tx(3, challenge))
    tid   = rx[5:37]
    echo  = rx[38:70]
    rw    = rx[71:87]
    match = echo == challenge

    print("Mode 3  TID      :", tid.hex())
    print("        CW sent  :", challenge.hex())
    print("        CW echo  :", echo.hex(), "  ← match" if match else "  ← zeros: chip not provisioned with HMAC key")
    print("        RW (HMAC):", rw.hex())
    return match


def cmd_sgtin(iface):
    rx = iface.transact(build_tx(0))
    print("Mode 0  SGTIN-198 raw :", rx.hex())


def cmd_probe(iface):
    print("=" * 60)
    print("SGT1001 probe — running ID, BIST, and one auth")
    print("=" * 60)

    tid    = cmd_id(iface)
    print()
    ok_bist = cmd_bist(iface)
    print()
    cw     = os.urandom(32)
    ok_auth = cmd_auth(iface, cw)
    print()

    alive = tid not in (ZERO32, FF32)
    print("─" * 60)
    print("Summary:")
    print("  Link  :", "OK — chip is responding" if alive else "FAIL — no valid ID")
    print("  BIST  :", "PASS" if ok_bist else "FAIL")
    print("  Auth  :", "CW echoed correctly" if ok_auth else "CW mismatch / not authenticated")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="SGT1001 CyberRock-Token communication tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--iface", choices=["auto", "spi", "serial"], default="auto")
    p.add_argument("--port", default="/dev/ttyACM0",  help="serial port for STM32 bridge")
    p.add_argument("--baud", type=int, default=115200, help="baud rate (USB CDC ignores this)")
    p.add_argument("--bus",  type=int, default=0,      help="SPI bus number (spidev)")
    p.add_argument("--dev",  type=int, default=0,      help="SPI device CS (spidev)")
    p.add_argument("--hz",   type=int, default=1_000_000, help="SPI clock Hz (max 10 MHz)")

    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("probe",  help="run ID + BIST + auth check (default)")
    sub.add_parser("id",     help="Mode 1: read 256-bit Token ID")
    sub.add_parser("bist",   help="Mode 255: built-in self-test")
    a = sub.add_parser("auth", help="Mode 3: HMAC challenge/response")
    a.add_argument("challenge", nargs="?",
                   help="64 hex chars (256 bits); random if omitted")
    sub.add_parser("sgtin",  help="Mode 0: GS1 SGTIN-198 raw bytes")

    args = p.parse_args()

    iface = auto_interface(args)
    try:
        if args.cmd == "id":
            cmd_id(iface)
        elif args.cmd == "bist":
            cmd_bist(iface)
        elif args.cmd == "auth":
            cw = bytes.fromhex(args.challenge) if args.challenge else os.urandom(32)
            cmd_auth(iface, cw)
        elif args.cmd == "sgtin":
            cmd_sgtin(iface)
        else:
            cmd_probe(iface)
    finally:
        iface.close()


if __name__ == "__main__":
    main()
