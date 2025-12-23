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

1. Pauses/freezes the Ultimate 64 machine
2. Reads VIC-II registers to determine current graphics mode
3. Reads CIA2 for VIC bank selection
4. Reads screen memory, color RAM, and character/bitmap data
5. Reads sprite data if sprites are enabled
6. Renders the screen based on the detected mode
7. Overlays sprites with correct priority and positioning
8. Applies display blanking based on RSEL/CSEL settings
9. Adds border (optional)
10. Saves the PNG image
11. Resumes the machine

## Known Limitations

**VIC Bank 3 with graphics at $E000-$FFFF**: When the VIC is using bank 3 ($C000-$FFFF) with bitmap or screen memory in the $E000-$FFFF range, the screenshot may render incorrectly. This is because the Ultimate 64's DMA controller reads through a fixed memory map that shows Kernal ROM at $E000-$FFFF, rather than the underlying RAM that the VIC actually uses. The tool will display a warning when this situation is detected.

## Debug Files

The tool saves intermediate data files for debugging:

- `vic_regs.bin` - Raw VIC-II register dump
- `color_mem.bin` - Color RAM contents
- `screen_mem.bin` - Screen memory contents
- `bitmap_mem.bin` - Bitmap data (bitmap modes only)
- `char_mem.bin` - Character data (custom character sets only)
- `sprite#_data.bin` - Sprite bitmap data for each enabled sprite

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Author

Garland Glessner - gglessner@gmail.com

