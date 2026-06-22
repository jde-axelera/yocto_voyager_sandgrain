# SGT1001 CyberRock-Token — Python Communication Tool

A Python script to communicate with the **SandGrain SGT1001** — an immutable hardware authentication IC — running on the **Axelera antelao RK3588** board.

---

## What is the SGT1001?

The SGT1001 is a small 8-pin IC that acts as a **hardware identity anchor** for an IoT device. Think of it like a hardware serial number that cannot be cloned or tampered with. It is not a microcontroller and has no firmware — its identity and cryptographic secret are permanently burned in silicon at the factory.

Its two main jobs are:

1. **Identify itself** — it holds a unique 256-bit Token ID (TID) that is globally unique, read-only, and hard-coded.
2. **Prove it is genuine** — it can respond to a cryptographic challenge using HMAC-SHA256, so a server can verify that the device carrying this chip is the real one (not a clone).

The interface to the chip is **SPI Mode 0** (CPOL=0, CPHA=0), MSB-first, up to 10 MHz.

---

## Physical connection on this board

### What you might expect

On a typical embedded system (Raspberry Pi, STM32 Nucleo, etc.) the SGT1001 would be connected directly to a hardware SPI peripheral, accessible in Linux as `/dev/spidev0.0` or similar. You would then talk to it directly with a Python SPI library.

### What is actually happening here

On the Axelera antelao board (Rockchip RK3588), the available SPI buses are either already used (SPI2 is taken by the onboard power management IC) or disabled in the device tree. The SGT1001 is therefore **not directly reachable from Linux** via a spidev node.

Instead, there is a small **STM32 microcontroller** wired between the RK3588 and the SGT1001:

```
RK3588 (Linux host)
        │
        │ USB (CDC/ACM — appears as /dev/ttyACM0, a virtual serial port)
        │
   STM32 MCU   ←── bridges USB serial ↔ SPI
        │
        │ SPI (CPOL=0, CPHA=0, MSB-first)
        │
    SGT1001 IC
```

The STM32 acts as a **transparent SPI bridge**: it receives a command over the USB serial port, performs the SPI transaction with the SGT1001, and sends the result back over USB serial.

### The AT command protocol

The STM32 firmware uses a simple ASCII-based command protocol (similar to the classic Hayes AT modem commands):

**Sending a frame:**
```
AT+SPI=<MOSI bytes as hex string>\r\n
```

**Receiving the response:**
```
<00 status byte><MISO bytes as hex string>\r
```

Example — sending a 4-byte Mode 1 header followed by 33 zero bytes (37 bytes total) to read the Token ID:

```
→  AT+SPI=01000000000000000000000000000000000000000000000000000000000000000000000000\r\n
←  000000000036e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000\r
```

The first two hex characters of the response (`00`) are a status byte (0x00 = OK). The remaining 74 hex characters (37 bytes) are the raw MISO data clocked back from the SGT1001.

---

## SPI framing — how the SGT1001 protocol works

Every interaction with the SGT1001 follows the same pattern:

1. Pull **CSN low** (chip select, active low) — this wakes the chip from deep sleep.
2. Clock a fixed number of bytes over SPI. MOSI carries the command/data; MISO carries the response simultaneously (full-duplex).
3. Pull **CSN high** — the chip goes back to deep sleep (consuming only ~1 nA at 3.3 V).

The MOSI frame always starts with a **4-byte mode header** that tells the chip what operation to perform. The rest of the frame is either zeros (padding) or a challenge word, depending on the mode. The chip starts clocking out meaningful data on MISO after the header is received (from clock cycle 40 onward).

The full frame must be sent in a single uninterrupted CSN-low session. If CSN goes high mid-frame, the transaction is aborted.

---

## SGT1001 operating modes

Each mode is selected by the first 4 bytes (32 bits) sent on MOSI. All headers are transmitted **MSB first**.

| Mode | 4-byte Header | Total frame length | Description |
|------|---------------|-------------------|-------------|
| 0 | `0x00000000` | 30 bytes (238 clk) | GS1 SGTIN-198 barcode-compatible identification |
| 1 | `0x01000000` | 37 bytes (296 clk) | Token Identity — read the 256-bit TID |
| 3 | `0x03000800` | 87 bytes (696 clk) | Token Authentication — HMAC challenge/response |
| 5 | `0x05000800` | 87 bytes (696 clk) | Host Authentication |
| 6 | `0x06000800` | 103 bytes (824 clk) | Host Authentication + Ephemeral Key generation |
| 7 | `0x07000800` | 103 bytes (824 clk) | Token Authentication + Ephemeral Key generation |
| 255 | `0xFF000000` | 72 bytes (576 clk) | Built-In Self-Test (BIST) |

### Mode 1 — Token Identity

The simplest mode. Send the 4-byte header + 33 zero bytes (37 bytes total). Starting at clock cycle 40, the chip clocks out its 256-bit (32-byte) unique Token ID on MISO.

```
MOSI: [01 00 00 00] [00 00 00 00 ... 00]  ← 4-byte header + 33 zero bytes
MISO: [xx xx xx xx] [00] [TID 32 bytes ]  ← first 4 bytes irrelevant, 1 gap byte, then TID
       ^CLK 1-32    ^gap  ^CLK 41-296
```

The TID is at MISO bytes 5–36 (0-indexed). Bytes 0–3 are whatever the chip outputs while it receives the header (not useful). Byte 4 is a gap byte.

### Mode 3 — Token Authentication (HMAC challenge/response)

Used to prove the chip is genuine. The host sends a random 32-byte **Challenge Word (CW)**. The chip uses its internal secret key and the CW to compute an HMAC-SHA256 digest, and returns the first 128 bits of that digest as the **Response Word (RW)**. A trusted server that knows the chip's secret key can independently compute the same RW and verify it matches.

```
MOSI: [03 00 08 00] [00] [CW — 32 bytes] [00 ... 00]
MISO: [xx xx xx xx] [00] [TID 32 bytes ] [00] [CW echo 32 bytes] [00] [RW 16 bytes]
       ^header       ^gap  ^CLK 41-296    ^gap  ^CLK 305-560      ^gap  ^CLK 569-696
```

The chip echoes the CW back (so you can verify it was received correctly) and then outputs the HMAC result.

### Mode 255 — Built-In Self-Test (BIST)

The chip runs its own internal test. It uses its TID as a challenge, runs the HMAC engine, and reports a pass/fail byte:

- `0x50` — PASS, all internal checks passed
- `0x70` — FAIL, internal error detected
- `0x00` — chip not yet provisioned with an HMAC key

---

## Setting up and running the script

### What is a Python virtual environment?

A virtual environment (`venv`) is an isolated Python installation folder. It keeps this project's dependencies (like `pyserial`) separate from the system Python and other projects. It works exactly like having a project-specific library folder — you activate it before use and deactivate when done.

### Step 1 — Create the environment

```bash
cd ~/sandgrain

# Create the isolated environment in a folder called "sgenv"
python3 -m venv sgenv

# Activate it (you must do this every new terminal session)
source sgenv/bin/activate

# Your prompt will now show (sgenv) to confirm it is active
```

### Step 2 — Install dependencies

```bash
# pyserial is the only dependency — it lets Python talk to /dev/ttyACM0
pip install -r requirements.txt
```

### Step 3 — Serial port permissions

The STM32 appears as `/dev/ttyACM0`. By default Linux restricts access to this device to the `dialout` group. If you get a "Permission denied" error, fix it with:

```bash
# Temporary fix (resets after reboot or device reconnect):
chmod a+rw /dev/ttyACM0

# Permanent fix (requires root, takes effect after re-login):
usermod -aG dialout $USER
```

### Step 4 — Run

```bash
# Full probe: reads TID, runs BIST, tests authentication
python3 sgt1001_comm.py --iface serial probe

# Read only the Token ID
python3 sgt1001_comm.py --iface serial id

# Run the built-in self-test
python3 sgt1001_comm.py --iface serial bist

# Run authentication with a random challenge word
python3 sgt1001_comm.py --iface serial auth

# Run authentication with a specific 64-character hex challenge word
python3 sgt1001_comm.py --iface serial auth deadbeef01020304...
```

---

## Command reference

| Subcommand | SGT1001 Mode | Frame size | What it does |
|------------|-------------|------------|--------------|
| `probe` | 1 + 255 + 3 | — | Runs id, bist, and auth in sequence (default) |
| `id` | 1 | 37 bytes | Reads the 256-bit Token Identity |
| `bist` | 255 | 72 bytes | Runs the Built-In Self-Test |
| `auth [HEX64]` | 3 | 87 bytes | HMAC challenge/response; random CW if omitted |
| `sgtin` | 0 | 30 bytes | Reads GS1 SGTIN-198 barcode data (raw bytes) |

### Script options

| Option | Default | Description |
|--------|---------|-------------|
| `--iface auto\|serial\|spi` | `auto` | `serial` = STM32 USB bridge (this board); `spi` = direct `/dev/spidev*` |
| `--port /dev/ttyACMx` | `/dev/ttyACM0` | Serial port the STM32 appears on |
| `--baud N` | `115200` | Baud rate (USB CDC ignores this, included for completeness) |
| `--hz N` | `1000000` | SPI clock frequency in Hz; max 10 MHz per datasheet |
| `--bus N` / `--dev N` | `0` / `0` | SPI bus number and chip-select (only used with `--iface spi`) |

---

## Expected output and what it means

```
============================================================
SGT1001 probe — running ID, BIST, and one auth
============================================================
Mode 1  Token ID (TID) : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000
```

This is the chip's globally unique 256-bit identity, read directly from silicon. The first 22 bytes are unique to this chip; the trailing zeros are unused bit fields padded to 256 bits.

```
Mode 255  BIST result : NOT_PROVISIONED (HMAC key not yet loaded)
          TID         : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000
          BRW         : 00000000000000000000000000000000 (zeros = no HMAC key)
          BEK         : 00000000000000000000000000000000
```

The chip's HMAC secret key has not yet been loaded via the CyberRock-Cloud platform, so the authentication engine outputs zeros. This is expected on an unprovisioned chip. The TID is still correct — identity reading does not require provisioning.

```
Mode 3  TID      : e32c31201ffc18d0f042e348a983869bee5ae94fcb396c000000000000000000
        CW sent  : 8363084e776fe38dad84115b6db39504...  (random 32 bytes)
        CW echo  : 0000...  ← zeros: chip not provisioned with HMAC key
        RW (HMAC): 0000...

Summary:
  Link  : OK — chip is responding
  BIST  : FAIL
  Auth  : CW mismatch / not authenticated
```

**"Link: OK"** is the important result — the SPI communication path (RK3588 → USB → STM32 → SPI → SGT1001) is fully working. BIST and Auth will work once the chip is provisioned with its secret key via CyberRock-Cloud.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Permission denied: /dev/ttyACM0` | User not in `dialout` group | `chmod a+rw /dev/ttyACM0` (as root) |
| `No such file: /dev/ttyACM0` | STM32 not connected or not enumerated | Check USB cable; run `dmesg \| grep ttyACM` |
| TID = `0000...0000` or `ffff...ffff` | SPI not clocking; CSN or MOSI wiring issue | Check STM32 ↔ SGT1001 wiring and power |
| TID changes between reads | Noise or SPI timing issue | Lower clock with `--hz 500000` |
| BIST = NOT_PROVISIONED | Chip has no HMAC key loaded | Normal for a new chip; requires CyberRock-Cloud setup |

---

## References

- SGT1001 datasheet (v1.0, January 2026) — SandGrain B.V.
- SandGrain CyberRock-Cloud platform documentation
- FIPS 198-1 — HMAC standard
- FIPS 180-4 — SHA-256 standard
