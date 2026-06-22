# SGT1001 CyberRock-Token — Python Communication Tool

Python driver for the [SandGrain SGT1001](https://sandgrain.eu) immutable IoT authentication IC, targeting the **Axelera antelao RK3588** board.

## Hardware setup

The SGT1001 is connected to the host via an **STM32 USB-serial bridge** (`/dev/ttyACM0`).  
The STM32 exposes an AT-command SPI passthrough:

```
AT+SPI=<hex MOSI frame>\r\n  →  <00 status><hex MISO frame>\r
```

Direct `/dev/spidev*` access is not available on this board (other SPI buses are disabled in the device tree).

## Quick start

```bash
# 1. Create environment and install dependencies
python3 -m venv sgenv
source sgenv/bin/activate
pip install -r requirements.txt

# 2. Make sure /dev/ttyACM0 is accessible
#    (root: chmod a+rw /dev/ttyACM0  or  usermod -aG dialout $USER)

# 3. Run
python3 sgt1001_comm.py --iface serial probe
```

## Commands

| Command | Description |
|---------|-------------|
| `probe` | Read TID + run BIST + test authentication (default) |
| `id` | Mode 1 — read 256-bit Token Identity |
| `bist` | Mode 255 — built-in self-test |
| `auth [HEX64]` | Mode 3 — HMAC challenge/response (random CW if omitted) |
| `sgtin` | Mode 0 — GS1 SGTIN-198 raw bytes |

```bash
python3 sgt1001_comm.py --iface serial id
python3 sgt1001_comm.py --iface serial bist
python3 sgt1001_comm.py --iface serial auth deadbeef...  # 64 hex chars
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--iface` | `auto` | `serial` (STM32 bridge) or `spi` (direct spidev) |
| `--port` | `/dev/ttyACM0` | Serial port for the STM32 bridge |
| `--hz` | `1000000` | SPI clock in Hz (max 10 MHz) |
| `--bus` / `--dev` | `0` / `0` | spidev bus and CS (for direct SPI only) |

## Expected output

```
============================================================
SGT1001 probe — running ID, BIST, and one auth
============================================================
Mode 1  Token ID (TID) : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000

Mode 255  BIST result : NOT_PROVISIONED (HMAC key not yet loaded)
          TID         : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000
          BRW         : 00000000000000000000000000000000 (zeros = no HMAC key)
          BEK         : 00000000000000000000000000000000

Mode 3  TID      : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000
        CW sent  : <random 32 bytes>
        CW echo  : 0000...  ← zeros: chip not provisioned with HMAC key
        RW (HMAC): 0000...

────────────────────────────────────────────────────────────
Summary:
  Link  : OK — chip is responding
  BIST  : FAIL
  Auth  : CW mismatch / not authenticated
```

**TID reading is confirmed working.** BIST and HMAC authentication return zeros because the chip's secret key has not yet been provisioned via the CyberRock-Cloud platform.

## SGT1001 operating modes

| Mode | Header | Description |
|------|--------|-------------|
| 0 | `0x00000000` | GS1 SGTIN-198 identification |
| 1 | `0x01000000` | Token Identity (256-bit TID) |
| 3 | `0x03000800` | Token Authentication (HMAC challenge/response) |
| 5 | `0x05000800` | Host Authentication |
| 6 | `0x06000800` | Host Authentication + Ephemeral Key |
| 7 | `0x07000800` | Token Authentication + Ephemeral Key |
| 255 | `0xFF000000` | Built-In Self-Test |

## References

- SGT1001 datasheet (v1.0, January 2026)
- SandGrain CyberRock-Cloud platform
