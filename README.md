# C64U-Screenshot

Capture screenshots from a running Ultimate 64 via its Web API.

**Author:** Garland Glessner <gglessner@gmail.com>  
**License:** [GNU General Public License v3.0](LICENSE)

## Features

- Captures the current screen state from an Ultimate 64 over the network
- Supports all C64 graphics modes:
  - Standard Text Mode
  - Multicolor Text Mode
  - Extended Background Color Mode (ECM)
  - Hi-Res Bitmap Mode
  - Multicolor Bitmap Mode
- Hardware sprite capture with correct positioning, colors, and expansion
- Accurate RSEL/CSEL display blanking
- Automatic border rendering
- Complete PETSCII character set support
- **ROM bypass** for capturing graphics stored under Kernal ROM ($E000-$FFFF)

## Requirements

- Python 3.6+
- Ultimate 64 with network connectivity and Web API enabled

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```
python C64U-Screenshot.py <IP_ADDRESS> [output.png] [options]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `IP_ADDRESS` | IP address of the Ultimate 64 |
| `output.png` | Output filename (default: screenshot.png) |

### Options

| Option | Description |
|--------|-------------|
| `--help` | Show help message and exit |
| `--no-border` | Don't add border around screen |
| `--nosprites` | Don't include hardware sprites in the capture |
| `--upscale=N` | Upscale output by factor N (e.g. `--upscale=2` for 2x) |
| `--no-rom-bypass` | Disable ROM bypass (faster, but fails on VIC bank 3) |
| `--password=XXX` | API password if required |

### Examples

```bash
# Basic screenshot
python C64U-Screenshot.py 192.168.1.100

# Save to specific file
python C64U-Screenshot.py 192.168.1.100 myscreen.png

# Upscale 2x for sharper image
python C64U-Screenshot.py 192.168.1.100 myscreen.png --upscale=2

# Without sprites
python C64U-Screenshot.py 192.168.1.100 myscreen.png --nosprites

# Without border
python C64U-Screenshot.py 192.168.1.100 myscreen.png --no-border
```

## How It Works

### Basic Screenshot Process

1. **Freeze** - Pauses the Ultimate 64 machine via the REST API
2. **Read VIC-II State** - Reads registers at $D000-$D02F to determine:
   - Graphics mode (BMM, ECM, MCM flags)
   - Screen/character/bitmap memory offsets
   - Border and background colors
   - Sprite configuration
3. **Read CIA2** - Reads $DD00 to determine VIC bank (0-3)
4. **Read Memory** - Fetches screen memory, color RAM, and character/bitmap data
5. **Read Sprites** - For each enabled sprite, reads 64 bytes of sprite data
6. **Render** - Draws the screen based on detected mode using the VICE color palette
7. **Overlay Sprites** - Composites sprites with correct priority and expansion
8. **Apply Blanking** - Handles RSEL/CSEL display blanking based on scroll values
9. **Add Border** - Surrounds the image with the border color (optional)
10. **Resume** - Unfreezes the C64 so it continues running

### ROM Bypass Technique

When the VIC-II uses bank 3 ($C000-$FFFF) with graphics data at $E000-$FFFF, the Ultimate 64's DMA controller reads Kernal ROM instead of the underlying RAM. This tool uses an innovative NMI-based bypass:

#### The Problem

The C64's memory map at $E000-$FFFF can contain either:
- **Kernal ROM** (the default, visible to CPU and DMA)
- **RAM** (hidden under ROM, only visible when ROMs are banked out via $01)

The VIC-II chip always sees RAM at these addresses (it ignores the banking register), but the Ultimate 64's DMA reads through the standard memory map, seeing ROM instead.

#### The Solution

1. **Backup Critical Memory**
   - Cassette buffer at $0340 (where we inject code)
   - Buffer area at $4000-$5FFF (where we copy data)
   - Zero page pointers $FB-$FE
   - NMI vector at $0318-$0319
   - Completion marker at $02

2. **Inject 6502 Machine Code** (61 bytes at $0340)
   ```
   ; Save registers and $01
   PHA / TXA / PHA / TYA / PHA
   LDA $01 / PHA
   
   ; Bank out ROMs ($01 = $34 = RAM everywhere, I/O visible)
   LDA #$34 / STA $01
   
   ; Setup pointers: $FB/$FC = source, $FD/$FE = destination
   LDA #<$E000 / STA $FB / LDA #>$E000 / STA $FC
   LDA #<$4000 / STA $FD / LDA #>$4000 / STA $FE
   
   ; Copy 8KB (32 pages)
   LDX #32
   .outer: LDY #0
   .inner: LDA ($FB),Y / STA ($FD),Y / INY / BNE .inner
   INC $FC / INC $FE / DEX / BNE .outer
   
   ; Restore $01 and set completion marker
   PLA / STA $01
   LDA #$42 / STA $02
   
   ; Restore registers and jump to original NMI handler
   PLA / TAY / PLA / TAX / PLA
   JMP $XXXX  ; original handler does RTI
   ```

3. **Patch NMI Vector** - Point $0318-$0319 to our routine at $0340

4. **Trigger NMI** - Configure CIA2 Timer A to fire immediately:
   - Set timer to 2 cycles
   - Enable Timer A NMI ($DD0D = $81)
   - Start timer with force load ($DD0E = $11)

5. **Resume & Wait** - Let the C64 run for ~500ms to execute the copy

6. **Verify & Read** - Check completion marker ($02 = $42), read buffer at $4000

7. **Restore Everything** - Put back all original memory contents

This technique is transparent to the running program - it resumes exactly where it left off.

## Supported Screen Modes

| Mode | BMM | ECM | MCM | Resolution | Colors |
|------|-----|-----|-----|------------|--------|
| Standard Text | 0 | 0 | 0 | 40x25 chars | 16 fg, 1 bg |
| Multicolor Text | 0 | 0 | 1 | 40x25 chars | 4 per char |
| Extended Background | 0 | 1 | 0 | 40x25 chars | 16 fg, 4 bg |
| Hi-Res Bitmap | 1 | 0 | 0 | 320x200 | 2 per 8x8 cell |
| Multicolor Bitmap | 1 | 0 | 1 | 160x200 | 4 per 4x8 cell |

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Author

Garland Glessner - gglessner@gmail.com
