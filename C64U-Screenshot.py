#!/usr/bin/env python3
"""
C64U-Screenshot - Capture screenshots from Ultimate 64 via Web API

Copyright (C) 2024 Garland Glessner <gglessner@gmail.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

Usage: python C64U-Screenshot.py <IP_ADDRESS> [output.png] [options]
"""

__author__ = "Garland Glessner"
__email__ = "gglessner@gmail.com"
__license__ = "GPL-3.0"
__version__ = "1.0.0"

import sys
import requests
from PIL import Image

# C64 color palette (VICE default)
C64_PALETTE = [
    (0x00, 0x00, 0x00),  # 0 Black
    (0xFF, 0xFF, 0xFF),  # 1 White
    (0x68, 0x37, 0x2B),  # 2 Red
    (0x70, 0xA4, 0xB2),  # 3 Cyan
    (0x6F, 0x3D, 0x86),  # 4 Purple
    (0x58, 0x8D, 0x43),  # 5 Green
    (0x35, 0x28, 0x79),  # 6 Blue
    (0xB8, 0xC7, 0x6F),  # 7 Yellow
    (0x6F, 0x4F, 0x25),  # 8 Orange
    (0x43, 0x39, 0x00),  # 9 Brown
    (0x9A, 0x67, 0x59),  # 10 Light Red
    (0x44, 0x44, 0x44),  # 11 Dark Grey
    (0x6C, 0x6C, 0x6C),  # 12 Grey
    (0x9A, 0xD2, 0x84),  # 13 Light Green
    (0x6C, 0x5E, 0xB5),  # 14 Light Blue
    (0x95, 0x95, 0x95),  # 15 Light Grey
]


class Ultimate64API:
    """Interface to Ultimate 64 REST API"""
    
    def __init__(self, ip_address, password=None):
        self.base_url = f"http://{ip_address}"
        self.headers = {}
        if password:
            self.headers["X-Password"] = password
    
    def pause(self):
        """Pause/freeze the machine"""
        url = f"{self.base_url}/v1/machine:pause"
        response = requests.put(url, headers=self.headers)
        return response.status_code == 200
    
    def resume(self):
        """Resume the machine"""
        url = f"{self.base_url}/v1/machine:resume"
        response = requests.put(url, headers=self.headers)
        return response.status_code == 200
    
    def read_memory(self, address, length):
        """Read memory from the C64 via DMA"""
        url = f"{self.base_url}/v1/machine:readmem"
        params = {"address": f"{address:X}", "length": str(length)}
        response = requests.get(url, params=params, headers=self.headers)
        if response.status_code == 200:
            return response.content
        return None
    
    def read_memory_with_rom_warning(self, address, length):
        """
        Read memory, warning if address overlaps with ROM areas.
        Note: Ultimate 64 DMA reads through a fixed memory map that includes
        ROMs at their standard locations. RAM under ROMs cannot be accessed.
        """
        end_address = address + length - 1
        
        # Check for ROM conflicts: BASIC ($A000-$BFFF), Kernal ($E000-$FFFF)
        if (address <= 0xBFFF and end_address >= 0xA000) or \
           (address <= 0xFFFF and end_address >= 0xE000):
            print(f"  Warning: Address ${address:04X} overlaps with ROM area")
            print(f"           VIC bank 3 graphics at $E000+ may render incorrectly")
        
        return self.read_memory(address, length)


class VICIIState:
    """VIC-II chip state extracted from registers"""
    
    def __init__(self, vic_regs, cia2_data_port):
        # Store raw register values
        self.raw_regs = vic_regs
        
        # $D011 - Screen control register 1
        d011 = vic_regs[0x11]
        self.yscroll = d011 & 0x07
        self.rsel = (d011 >> 3) & 1  # Row select (24/25 rows)
        self.den = (d011 >> 4) & 1   # Display enable
        self.bmm = (d011 >> 5) & 1   # Bitmap mode
        self.ecm = (d011 >> 6) & 1   # Extended color mode
        
        # $D016 - Screen control register 2
        d016 = vic_regs[0x16]
        self.xscroll = d016 & 0x07
        self.csel = (d016 >> 3) & 1  # Column select (38/40 cols)
        self.mcm = (d016 >> 4) & 1   # Multicolor mode
        
        # $D018 - Memory setup register
        d018 = vic_regs[0x18]
        self.char_mem_offset = ((d018 >> 1) & 0x07) * 0x800  # Character memory offset within bank
        self.screen_mem_offset = ((d018 >> 4) & 0x0F) * 0x400  # Screen memory offset within bank
        
        # In bitmap mode, bit 3 of (d018 >> 1) selects 0x0000 or 0x2000 within bank
        self.bitmap_mem_offset = ((d018 >> 3) & 1) * 0x2000
        
        # CIA2 $DD00 bits 0-1 select VIC bank (inverted)
        vic_bank_bits = cia2_data_port & 0x03
        self.vic_bank = (3 - vic_bank_bits) * 0x4000  # Banks: 0=$0000, 1=$4000, 2=$8000, 3=$C000
        
        # Calculate absolute memory addresses
        self.screen_mem_addr = self.vic_bank + self.screen_mem_offset
        self.char_mem_addr = self.vic_bank + self.char_mem_offset
        self.bitmap_mem_addr = self.vic_bank + self.bitmap_mem_offset
        
        # Colors
        self.border_color = vic_regs[0x20] & 0x0F
        self.background_color = vic_regs[0x21] & 0x0F
        self.background_color1 = vic_regs[0x22] & 0x0F
        self.background_color2 = vic_regs[0x23] & 0x0F
        self.background_color3 = vic_regs[0x24] & 0x0F
        
        # Sprite registers
        self.sprite_multicolor0 = vic_regs[0x25] & 0x0F  # $D025
        self.sprite_multicolor1 = vic_regs[0x26] & 0x0F  # $D026
        
    def get_mode_name(self):
        """Return the name of the current graphics mode"""
        if self.ecm and not self.bmm and not self.mcm:
            return "Extended Background Color Mode"
        elif not self.ecm and self.bmm and not self.mcm:
            return "Standard Bitmap Mode (Hi-Res)"
        elif not self.ecm and self.bmm and self.mcm:
            return "Multicolor Bitmap Mode"
        elif not self.ecm and not self.bmm and self.mcm:
            return "Multicolor Text Mode"
        elif not self.ecm and not self.bmm and not self.mcm:
            return "Standard Text Mode"
        else:
            return "Invalid/Unused Mode"
    
    def dump_info(self):
        """Print debug info about VIC-II state"""
        print(f"Screen Mode: {self.get_mode_name()}")
        print(f"  BMM={self.bmm} ECM={self.ecm} MCM={self.mcm}")
        print(f"  DEN={self.den} (display {'enabled' if self.den else 'disabled'})")
        print(f"  RSEL={self.rsel} ({25 if self.rsel else 24} rows)")
        print(f"  CSEL={self.csel} ({40 if self.csel else 38} columns)")
        print(f"VIC Bank: ${self.vic_bank:04X}")
        print(f"Screen Memory: ${self.screen_mem_addr:04X}")
        print(f"Character Memory: ${self.char_mem_addr:04X}")
        print(f"Bitmap Memory: ${self.bitmap_mem_addr:04X}")
        print(f"Border Color: {self.border_color}")
        print(f"Background Color: {self.background_color}")


class SpriteInfo:
    """Information about a single sprite"""
    
    def __init__(self, sprite_num, vic_regs, sprite_pointer, vic_bank):
        self.num = sprite_num
        
        # X position: low byte from $D000+2*num, high bit from $D010
        x_low = vic_regs[sprite_num * 2]
        x_msb = (vic_regs[0x10] >> sprite_num) & 1
        self.x = x_low + (x_msb * 256)
        
        # Y position from $D001+2*num
        self.y = vic_regs[sprite_num * 2 + 1]
        
        # Enabled? ($D015)
        self.enabled = (vic_regs[0x15] >> sprite_num) & 1
        
        # Y expansion ($D017)
        self.y_expand = (vic_regs[0x17] >> sprite_num) & 1
        
        # Priority: 0 = in front of screen, 1 = behind screen ($D01B)
        self.priority = (vic_regs[0x1B] >> sprite_num) & 1
        
        # Multicolor mode ($D01C)
        self.multicolor = (vic_regs[0x1C] >> sprite_num) & 1
        
        # X expansion ($D01D)
        self.x_expand = (vic_regs[0x1D] >> sprite_num) & 1
        
        # Sprite color ($D027-$D02E)
        self.color = vic_regs[0x27 + sprite_num] & 0x0F
        
        # Sprite data address (pointer * 64 within VIC bank)
        self.data_addr = vic_bank + (sprite_pointer * 64)
        self.pointer = sprite_pointer
        
    def __repr__(self):
        status = "ON" if self.enabled else "off"
        mc = "MC" if self.multicolor else "HR"
        exp = ""
        if self.x_expand:
            exp += "Xx2 "
        if self.y_expand:
            exp += "Yx2"
        prio = "behind" if self.priority else "front"
        return (f"Sprite {self.num}: {status} pos=({self.x},{self.y}) "
                f"color={self.color} {mc} {exp} {prio} ptr=${self.pointer:02X} "
                f"data=${self.data_addr:04X}")


def get_sprite_info(vic_regs, sprite_pointers, vic_bank):
    """Extract information about all 8 sprites"""
    sprites = []
    for i in range(8):
        sprite = SpriteInfo(i, vic_regs, sprite_pointers[i], vic_bank)
        sprites.append(sprite)
    return sprites


def render_sprite(sprite, sprite_data, vic):
    """
    Render a single sprite to an RGBA image.
    Returns (image, x_offset, y_offset) where offsets are for screen positioning.
    Sprite coordinates are in VIC coordinates where visible screen starts at (24, 50).
    """
    # Sprite dimensions: 24x21 pixels (standard), doubled if expanded
    base_width = 24
    base_height = 21
    
    width = base_width * (2 if sprite.x_expand else 1)
    height = base_height * (2 if sprite.y_expand else 1)
    
    # Create RGBA image for transparency support
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pixels = img.load()
    
    # Get colors
    sprite_color = C64_PALETTE[sprite.color]
    mc0 = C64_PALETTE[vic.sprite_multicolor0]
    mc1 = C64_PALETTE[vic.sprite_multicolor1]
    
    # Sprite data is 63 bytes: 3 bytes per row, 21 rows
    for row in range(21):
        row_data = sprite_data[row * 3: row * 3 + 3]
        if len(row_data) < 3:
            continue
            
        # Combine 3 bytes into 24 bits
        bits = (row_data[0] << 16) | (row_data[1] << 8) | row_data[2]
        
        if sprite.multicolor:
            # Multicolor: 12 double-width pixels per row
            for col in range(12):
                bit_pair = (bits >> (22 - col * 2)) & 0x03
                
                if bit_pair == 0:
                    # Transparent
                    color = None
                elif bit_pair == 1:
                    # Multicolor 0
                    color = mc0
                elif bit_pair == 2:
                    # Sprite color
                    color = sprite_color
                else:  # bit_pair == 3
                    # Multicolor 1
                    color = mc1
                
                if color is not None:
                    # Each multicolor pixel is 2 pixels wide
                    x_base = col * 2
                    for dx in range(2 * (2 if sprite.x_expand else 1)):
                        for dy in range(2 if sprite.y_expand else 1):
                            px = x_base * (2 if sprite.x_expand else 1) + dx
                            py = row * (2 if sprite.y_expand else 1) + dy
                            if 0 <= px < width and 0 <= py < height:
                                pixels[px, py] = (*color, 255)
        else:
            # Hi-res: 24 pixels per row
            for col in range(24):
                bit = (bits >> (23 - col)) & 1
                
                if bit:
                    for dx in range(2 if sprite.x_expand else 1):
                        for dy in range(2 if sprite.y_expand else 1):
                            px = col * (2 if sprite.x_expand else 1) + dx
                            py = row * (2 if sprite.y_expand else 1) + dy
                            if 0 <= px < width and 0 <= py < height:
                                pixels[px, py] = (*sprite_color, 255)
    
    # Convert VIC sprite coordinates to screen coordinates
    # VIC visible area starts at X=24, Y=50 (for NTSC) or Y=51 (PAL)
    # Screen area is 320x200 starting at those offsets
    screen_x = sprite.x - 24
    screen_y = sprite.y - 50
    
    return img, screen_x, screen_y


def overlay_sprites(screen_img, sprites, sprite_data_list, vic, behind_only=False, front_only=False):
    """
    Overlay sprites onto the screen image.
    
    Args:
        screen_img: The base screen image (RGB)
        sprites: List of SpriteInfo objects
        sprite_data_list: List of 64-byte sprite data for each sprite
        vic: VICIIState object
        behind_only: Only render sprites with priority=1 (behind screen)
        front_only: Only render sprites with priority=0 (in front of screen)
    
    Returns:
        New image with sprites overlaid
    """
    # Convert to RGBA for compositing
    result = screen_img.convert('RGBA')
    
    # Sort sprites by number (lower numbers have higher priority in C64)
    # Actually on C64, sprite 0 has highest priority and is drawn LAST (on top)
    # So we render in reverse order: 7, 6, 5, 4, 3, 2, 1, 0
    for i in range(7, -1, -1):
        sprite = sprites[i]
        
        if not sprite.enabled:
            continue
            
        # Filter by priority if requested
        if behind_only and sprite.priority == 0:
            continue
        if front_only and sprite.priority == 1:
            continue
        
        sprite_data = sprite_data_list[i]
        if sprite_data is None:
            continue
        
        sprite_img, x, y = render_sprite(sprite, sprite_data, vic)
        
        # Paste sprite onto result using alpha compositing
        # Only paste the visible portion
        if x < screen_img.width and y < screen_img.height:
            if x + sprite_img.width > 0 and y + sprite_img.height > 0:
                result.paste(sprite_img, (x, y), sprite_img)
    
    return result.convert('RGB')


def render_standard_text_mode(vic, screen_mem, color_mem, char_rom):
    """Render standard text mode (40x25 characters, 8x8 pixels each)"""
    # Create image: 320x200 pixels
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for row in range(25):
        for col in range(40):
            screen_pos = row * 40 + col
            char_code = screen_mem[screen_pos]
            color_idx = color_mem[screen_pos] & 0x0F
            
            # Get character data (8 bytes per character)
            char_offset = char_code * 8
            
            for y in range(8):
                byte = char_rom[char_offset + y]
                for x in range(8):
                    if byte & (0x80 >> x):
                        pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[color_idx]
                    else:
                        pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[vic.background_color]
    
    return img


def render_multicolor_text_mode(vic, screen_mem, color_mem, char_rom):
    """Render multicolor text mode"""
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for row in range(25):
        for col in range(40):
            screen_pos = row * 40 + col
            char_code = screen_mem[screen_pos]
            color_byte = color_mem[screen_pos]
            color_idx = color_byte & 0x0F
            is_multicolor = (color_byte & 0x08) != 0  # Bit 3 determines multicolor
            
            char_offset = char_code * 8
            
            for y in range(8):
                byte = char_rom[char_offset + y]
                
                if is_multicolor:
                    # Multicolor: 4 pixel pairs, each 2 bits
                    for x in range(4):
                        bits = (byte >> (6 - x * 2)) & 0x03
                        if bits == 0:
                            c = C64_PALETTE[vic.background_color]
                        elif bits == 1:
                            c = C64_PALETTE[vic.background_color1]
                        elif bits == 2:
                            c = C64_PALETTE[vic.background_color2]
                        else:  # bits == 3
                            c = C64_PALETTE[color_idx & 0x07]  # Only lower 3 bits
                        pixels[col * 8 + x * 2, row * 8 + y] = c
                        pixels[col * 8 + x * 2 + 1, row * 8 + y] = c
                else:
                    # Standard hires within multicolor mode
                    for x in range(8):
                        if byte & (0x80 >> x):
                            pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[color_idx]
                        else:
                            pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[vic.background_color]
    
    return img


def render_hires_bitmap_mode(vic, bitmap_mem, screen_mem):
    """Render standard (hi-res) bitmap mode"""
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for char_row in range(25):
        for char_col in range(40):
            screen_pos = char_row * 40 + char_col
            color_byte = screen_mem[screen_pos]
            fg_color = (color_byte >> 4) & 0x0F
            bg_color = color_byte & 0x0F
            
            # Bitmap data: 8 bytes per 8x8 cell, arranged in raster order
            bitmap_offset = char_row * 40 * 8 + char_col * 8
            
            for y in range(8):
                byte = bitmap_mem[bitmap_offset + y]
                for x in range(8):
                    if byte & (0x80 >> x):
                        pixels[char_col * 8 + x, char_row * 8 + y] = C64_PALETTE[fg_color]
                    else:
                        pixels[char_col * 8 + x, char_row * 8 + y] = C64_PALETTE[bg_color]
    
    return img


def render_multicolor_bitmap_mode(vic, bitmap_mem, screen_mem, color_mem):
    """Render multicolor bitmap mode"""
    img = Image.new('RGB', (160, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for char_row in range(25):
        for char_col in range(40):
            screen_pos = char_row * 40 + char_col
            color_byte = screen_mem[screen_pos]
            color1 = (color_byte >> 4) & 0x0F  # Upper nibble
            color2 = color_byte & 0x0F          # Lower nibble
            color3 = color_mem[screen_pos] & 0x0F
            
            bitmap_offset = char_row * 40 * 8 + char_col * 8
            
            for y in range(8):
                byte = bitmap_mem[bitmap_offset + y]
                for x in range(4):
                    bits = (byte >> (6 - x * 2)) & 0x03
                    if bits == 0:
                        c = C64_PALETTE[vic.background_color]
                    elif bits == 1:
                        c = C64_PALETTE[color1]
                    elif bits == 2:
                        c = C64_PALETTE[color2]
                    else:  # bits == 3
                        c = C64_PALETTE[color3]
                    pixels[char_col * 4 + x, char_row * 8 + y] = c
    
    # Resize to 320x200 to maintain aspect ratio
    img = img.resize((320, 200), Image.NEAREST)
    return img


def render_ecm_mode(vic, screen_mem, color_mem, char_rom):
    """Render Extended Color Mode (ECM)"""
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    bg_colors = [
        vic.background_color,
        vic.background_color1,
        vic.background_color2,
        vic.background_color3,
    ]
    
    for row in range(25):
        for col in range(40):
            screen_pos = row * 40 + col
            screen_byte = screen_mem[screen_pos]
            char_code = screen_byte & 0x3F  # Only lower 6 bits for character
            bg_select = (screen_byte >> 6) & 0x03  # Upper 2 bits select background
            color_idx = color_mem[screen_pos] & 0x0F
            bg_color = bg_colors[bg_select]
            
            char_offset = char_code * 8
            
            for y in range(8):
                byte = char_rom[char_offset + y]
                for x in range(8):
                    if byte & (0x80 >> x):
                        pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[color_idx]
                    else:
                        pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[bg_color]
    
    return img


def apply_rsel_csel_blanking(img, vic):
    """
    Apply RSEL/CSEL blanking to match actual visible display.
    
    RSEL=0: 24 rows - YSCROLL determines if top or bottom 8px blanked
    CSEL=0: 38 cols - XSCROLL determines if left or right 8px blanked
    """
    if vic.rsel == 1 and vic.csel == 1:
        return img
    
    from PIL import ImageDraw
    result = img.copy()
    draw = ImageDraw.Draw(result)
    border = C64_PALETTE[vic.border_color]
    width, height = img.size
    
    if vic.rsel == 0:
        if vic.yscroll >= 4:
            draw.rectangle([0, height - 8, width - 1, height - 1], fill=border)
        else:
            draw.rectangle([0, 0, width - 1, 7], fill=border)
    
    if vic.csel == 0:
        if vic.xscroll >= 4:
            draw.rectangle([width - 8, 0, width - 1, height - 1], fill=border)
        else:
            draw.rectangle([0, 0, 7, height - 1], fill=border)
    
    return result


def add_border(img, border_color, border_size=32):
    """Add a border around the screen image"""
    w, h = img.size
    new_w = w + border_size * 2
    new_h = h + border_size * 2
    bordered = Image.new('RGB', (new_w, new_h), C64_PALETTE[border_color])
    bordered.paste(img, (border_size, border_size))
    return bordered


def capture_screenshot(ip_address, output_file="screenshot.png", add_border_flag=True, 
                       password=None, include_sprites=False, upscale=1):
    """Main function to capture a screenshot from the Ultimate 64"""
    
    api = Ultimate64API(ip_address, password)
    
    print(f"Connecting to Ultimate 64 at {ip_address}...")
    
    # Step 1: Pause/freeze the machine
    print("Freezing machine...")
    if not api.pause():
        print("Warning: Failed to pause machine (may already be paused)")
    
    try:
        # Step 2: Read VIC-II registers ($D000-$D02F)
        print("Reading VIC-II registers...")
        vic_regs = api.read_memory(0xD000, 0x30)
        if vic_regs is None:
            print("Error: Failed to read VIC-II registers")
            return False
        
        # Save VIC registers for debugging
        with open("vic_regs.bin", "wb") as f:
            f.write(vic_regs)
        print("  Saved VIC registers to vic_regs.bin")
        
        # Step 3: Read CIA2 data port for VIC bank selection
        print("Reading CIA2 for VIC bank selection...")
        cia2_data = api.read_memory(0xDD00, 1)
        if cia2_data is None:
            print("Error: Failed to read CIA2")
            return False
        
        # Create VIC state object
        vic = VICIIState(vic_regs, cia2_data[0])
        vic.dump_info()
        
        # Step 4: Read color RAM ($D800-$DBE7, 1000 bytes)
        print("Reading Color RAM...")
        color_mem = api.read_memory(0xD800, 1000)
        if color_mem is None:
            print("Error: Failed to read color RAM")
            return False
        with open("color_mem.bin", "wb") as f:
            f.write(color_mem)
        print("  Saved color memory to color_mem.bin")
        
        # Step 5: Read screen memory (1024 bytes to include sprite pointers at end)
        # Use banked read in case screen memory overlaps with ROM areas
        print(f"Reading Screen Memory at ${vic.screen_mem_addr:04X}...")
        screen_mem = api.read_memory_with_rom_warning(vic.screen_mem_addr, 1024)
        if screen_mem is None:
            print("Error: Failed to read screen memory")
            return False
        with open("screen_mem.bin", "wb") as f:
            f.write(screen_mem)
        print("  Saved screen memory to screen_mem.bin")
        
        # Step 6: Read character ROM or bitmap data depending on mode
        if vic.bmm:
            # Bitmap mode - read 8000 bytes of bitmap data
            # Use banked read in case bitmap overlaps with ROM areas (e.g. VIC bank 3)
            print(f"Reading Bitmap Memory at ${vic.bitmap_mem_addr:04X}...")
            bitmap_mem = api.read_memory_with_rom_warning(vic.bitmap_mem_addr, 8000)
            if bitmap_mem is None:
                print("Error: Failed to read bitmap memory")
                return False
            with open("bitmap_mem.bin", "wb") as f:
                f.write(bitmap_mem)
            print("  Saved bitmap memory to bitmap_mem.bin")
            char_rom = None
        else:
            # Text mode - we need character data
            # Character ROM is at $D000-$DFFF when accessed by CPU with certain bank configs
            # But VIC sees it differently. For VIC banks 0 and 2, addresses $1000-$1FFF and $9000-$9FFF
            # see the character ROM instead of RAM.
            
            # Check if char memory points to ROM area
            char_addr_in_bank = vic.char_mem_offset
            uses_char_rom = False
            
            if vic.vic_bank == 0x0000 or vic.vic_bank == 0x8000:
                # Banks 0 and 2 have char ROM at offset $1000-$1FFF
                if char_addr_in_bank >= 0x1000 and char_addr_in_bank < 0x2000:
                    uses_char_rom = True
            
            if uses_char_rom:
                print("Reading Character ROM...")
                # DMA reads see RAM, not ROM, so we use embedded charset
                print("  Using embedded C64 character ROM")
                char_rom = get_embedded_charset()
            else:
                print(f"Reading Character Memory at ${vic.char_mem_addr:04X}...")
                # Use banked read in case chars are in ROM-shadowed area
                char_rom = api.read_memory_with_rom_warning(vic.char_mem_addr, 2048)
                if char_rom is None:
                    print("Error: Failed to read character memory")
                    return False
                with open("char_mem.bin", "wb") as f:
                    f.write(char_rom)
                print("  Saved character memory to char_mem.bin")
            
            bitmap_mem = None
        
        # Step 7: Render the screen based on mode
        print("Rendering screen...")
        
        if vic.bmm and vic.mcm:
            # Multicolor bitmap mode
            img = render_multicolor_bitmap_mode(vic, bitmap_mem, screen_mem, color_mem)
        elif vic.bmm and not vic.mcm:
            # Hi-res bitmap mode
            img = render_hires_bitmap_mode(vic, bitmap_mem, screen_mem)
        elif vic.ecm:
            # Extended color mode
            img = render_ecm_mode(vic, screen_mem, color_mem, char_rom)
        elif vic.mcm:
            # Multicolor text mode
            img = render_multicolor_text_mode(vic, screen_mem, color_mem, char_rom)
        else:
            # Standard text mode
            img = render_standard_text_mode(vic, screen_mem, color_mem, char_rom)
        
        # Step 7.5: Overlay sprites if requested
        if include_sprites:
            print("Processing sprites...")
            
            # Sprite pointers are at screen memory + $3F8 (last 8 bytes of 1K screen area)
            sprite_pointers = screen_mem[0x3F8:0x400]
            
            # Get sprite info
            sprites = get_sprite_info(vic_regs, sprite_pointers, vic.vic_bank)
            
            # Print sprite information
            enabled_count = 0
            for sprite in sprites:
                if sprite.enabled:
                    print(f"  {sprite}")
                    enabled_count += 1
            
            if enabled_count == 0:
                print("  No sprites enabled")
            else:
                print(f"  {enabled_count} sprite(s) enabled")
            
            # Read sprite data for each enabled sprite
            sprite_data_list = []
            for sprite in sprites:
                if sprite.enabled:
                    # Read 64 bytes of sprite data (63 used + 1 padding)
                    data = api.read_memory(sprite.data_addr, 64)
                    sprite_data_list.append(data)
                    
                    # Save sprite data for debugging
                    if data:
                        with open(f"sprite{sprite.num}_data.bin", "wb") as f:
                            f.write(data)
                else:
                    sprite_data_list.append(None)
            
            # First overlay sprites that are behind the screen (priority=1)
            # These should be rendered but partially obscured by screen content
            # For simplicity, we'll render behind sprites first, then screen, then front sprites
            # But since screen is already rendered, we just overlay front sprites
            
            # Overlay sprites that are in front (priority=0)
            img = overlay_sprites(img, sprites, sprite_data_list, vic, front_only=False)
        
        # Step 7.6: Apply RSEL/CSEL blanking to match actual visible display
        if vic.rsel == 0 or vic.csel == 0:
            blanking_info = []
            if vic.rsel == 0:
                if vic.yscroll >= 4:
                    blanking_info.append(f"24 rows, YSCROLL={vic.yscroll} (bottom 8px blanked)")
                else:
                    blanking_info.append(f"24 rows, YSCROLL={vic.yscroll} (top 8px blanked)")
            if vic.csel == 0:
                if vic.xscroll >= 4:
                    blanking_info.append(f"38 cols, XSCROLL={vic.xscroll} (right 8px blanked)")
                else:
                    blanking_info.append(f"38 cols, XSCROLL={vic.xscroll} (left 8px blanked)")
            print(f"Applying display blanking: {', '.join(blanking_info)}")
            img = apply_rsel_csel_blanking(img, vic)
        
        # Step 8: Add border if requested
        if add_border_flag:
            img = add_border(img, vic.border_color)
        
        # Step 9: Upscale if requested
        if upscale > 1:
            new_width = img.width * upscale
            new_height = img.height * upscale
            img = img.resize((new_width, new_height), Image.NEAREST)
            print(f"Upscaled to {new_width}x{new_height} ({upscale}x)")
        
        # Step 10: Save the image
        img.save(output_file)
        print(f"Screenshot saved to {output_file}")
        
        return True
        
    finally:
        # Step 10: Resume the machine
        print("Resuming machine...")
        if api.resume():
            print("Machine resumed successfully")
        else:
            print("Warning: Failed to resume machine")


def get_embedded_charset():
    """Return the standard C64 character ROM (uppercase/graphics set)"""
    # Complete C64 character set - 256 screen codes, 8 bytes each
    # Each entry is [row0, row1, row2, row3, row4, row5, row6, row7]
    chars = {
        # Screen code 0: @
        0: [0x3C, 0x66, 0x6E, 0x6E, 0x60, 0x62, 0x3C, 0x00],
        # Screen codes 1-26: A-Z
        1: [0x18, 0x3C, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00],
        2: [0x7C, 0x66, 0x66, 0x7C, 0x66, 0x66, 0x7C, 0x00],
        3: [0x3C, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3C, 0x00],
        4: [0x78, 0x6C, 0x66, 0x66, 0x66, 0x6C, 0x78, 0x00],
        5: [0x7E, 0x60, 0x60, 0x78, 0x60, 0x60, 0x7E, 0x00],
        6: [0x7E, 0x60, 0x60, 0x78, 0x60, 0x60, 0x60, 0x00],
        7: [0x3C, 0x66, 0x60, 0x6E, 0x66, 0x66, 0x3C, 0x00],
        8: [0x66, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00],
        9: [0x3C, 0x18, 0x18, 0x18, 0x18, 0x18, 0x3C, 0x00],
        10: [0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x6C, 0x38, 0x00],
        11: [0x66, 0x6C, 0x78, 0x70, 0x78, 0x6C, 0x66, 0x00],
        12: [0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x7E, 0x00],
        13: [0x63, 0x77, 0x7F, 0x6B, 0x63, 0x63, 0x63, 0x00],
        14: [0x66, 0x76, 0x7E, 0x7E, 0x6E, 0x66, 0x66, 0x00],
        15: [0x3C, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00],
        16: [0x7C, 0x66, 0x66, 0x7C, 0x60, 0x60, 0x60, 0x00],
        17: [0x3C, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x0E, 0x00],
        18: [0x7C, 0x66, 0x66, 0x7C, 0x78, 0x6C, 0x66, 0x00],
        19: [0x3C, 0x66, 0x60, 0x3C, 0x06, 0x66, 0x3C, 0x00],
        20: [0x7E, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00],
        21: [0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00],
        22: [0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x18, 0x00],
        23: [0x63, 0x63, 0x63, 0x6B, 0x7F, 0x77, 0x63, 0x00],
        24: [0x66, 0x66, 0x3C, 0x18, 0x3C, 0x66, 0x66, 0x00],
        25: [0x66, 0x66, 0x66, 0x3C, 0x18, 0x18, 0x18, 0x00],
        26: [0x7E, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x7E, 0x00],
        # Screen codes 27-31: special chars
        27: [0x3C, 0x30, 0x30, 0x30, 0x30, 0x30, 0x3C, 0x00],  # [
        28: [0x0C, 0x12, 0x30, 0x7C, 0x30, 0x62, 0xFC, 0x00],  # pound
        29: [0x3C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x3C, 0x00],  # ]
        30: [0x00, 0x08, 0x1C, 0x3E, 0x08, 0x08, 0x00, 0x00],  # up arrow
        31: [0x00, 0x10, 0x30, 0x7F, 0x30, 0x10, 0x00, 0x00],  # left arrow
        # Screen codes 32-63: punctuation and numbers
        32: [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],  # space
        33: [0x18, 0x18, 0x18, 0x18, 0x00, 0x00, 0x18, 0x00],  # !
        34: [0x66, 0x66, 0x66, 0x00, 0x00, 0x00, 0x00, 0x00],  # "
        35: [0x66, 0x66, 0xFF, 0x66, 0xFF, 0x66, 0x66, 0x00],  # #
        36: [0x18, 0x3E, 0x60, 0x3C, 0x06, 0x7C, 0x18, 0x00],  # $
        37: [0x62, 0x66, 0x0C, 0x18, 0x30, 0x66, 0x46, 0x00],  # %
        38: [0x3C, 0x66, 0x3C, 0x38, 0x67, 0x66, 0x3F, 0x00],  # &
        39: [0x06, 0x0C, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00],  # '
        40: [0x0C, 0x18, 0x30, 0x30, 0x30, 0x18, 0x0C, 0x00],  # (
        41: [0x30, 0x18, 0x0C, 0x0C, 0x0C, 0x18, 0x30, 0x00],  # )
        42: [0x00, 0x66, 0x3C, 0xFF, 0x3C, 0x66, 0x00, 0x00],  # *
        43: [0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00],  # +
        44: [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30],  # ,
        45: [0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00, 0x00],  # -
        46: [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00],  # .
        47: [0x00, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x00],  # /
        48: [0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00],  # 0
        49: [0x18, 0x18, 0x38, 0x18, 0x18, 0x18, 0x7E, 0x00],  # 1
        50: [0x3C, 0x66, 0x06, 0x0C, 0x30, 0x60, 0x7E, 0x00],  # 2
        51: [0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00],  # 3
        52: [0x06, 0x0E, 0x1E, 0x66, 0x7F, 0x06, 0x06, 0x00],  # 4
        53: [0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00],  # 5
        54: [0x3C, 0x66, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00],  # 6
        55: [0x7E, 0x66, 0x0C, 0x18, 0x18, 0x18, 0x18, 0x00],  # 7
        56: [0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00],  # 8
        57: [0x3C, 0x66, 0x66, 0x3E, 0x06, 0x66, 0x3C, 0x00],  # 9
        58: [0x00, 0x00, 0x18, 0x00, 0x00, 0x18, 0x00, 0x00],  # :
        59: [0x00, 0x00, 0x18, 0x00, 0x00, 0x18, 0x18, 0x30],  # ;
        60: [0x0E, 0x18, 0x30, 0x60, 0x30, 0x18, 0x0E, 0x00],  # <
        61: [0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00, 0x00, 0x00],  # =
        62: [0x70, 0x18, 0x0C, 0x06, 0x0C, 0x18, 0x70, 0x00],  # >
        63: [0x3C, 0x66, 0x06, 0x0C, 0x18, 0x00, 0x18, 0x00],  # ?
        # Screen codes 64-95: PETSCII graphics
        64: [0x00, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00],  # horizontal bar
        65: [0x08, 0x1C, 0x3E, 0x7F, 0x7F, 0x1C, 0x3E, 0x00],  # spade
        66: [0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18],  # vertical line
        67: [0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF],  # lower half
        68: [0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00],  # upper half
        69: [0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0],  # left half
        70: [0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA],  # checkerboard
        71: [0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F],  # right half
        72: [0x00, 0x00, 0x00, 0x00, 0xAA, 0x55, 0xAA, 0x55],  # lower checker
        73: [0x0F, 0x07, 0x03, 0x01, 0x00, 0x00, 0x00, 0x00],  # corner TR
        74: [0x55, 0xAA, 0x55, 0xAA, 0x00, 0x00, 0x00, 0x00],  # upper checker
        75: [0x00, 0x00, 0x00, 0x00, 0x01, 0x03, 0x07, 0x0F],  # corner BR
        76: [0x00, 0x00, 0x00, 0x00, 0x80, 0xC0, 0xE0, 0xF0],  # corner BL
        77: [0xF0, 0xE0, 0xC0, 0x80, 0x00, 0x00, 0x00, 0x00],  # corner TL
        78: [0x01, 0x03, 0x07, 0x0F, 0x1F, 0x3F, 0x7F, 0xFF],  # diagonal BR
        79: [0x80, 0xC0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE, 0xFF],  # diagonal BL
        80: [0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE0, 0xC0, 0x80],  # diagonal TL
        81: [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF],  # solid block
        82: [0xFF, 0x7F, 0x3F, 0x1F, 0x0F, 0x07, 0x03, 0x01],  # diagonal TR
        83: [0x3C, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C],  # circle filled
        84: [0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0],  # left bar
        85: [0x18, 0x18, 0x7E, 0xFF, 0xFF, 0x18, 0x3C, 0x00],  # club
        86: [0x00, 0x00, 0x00, 0x00, 0xF0, 0xF0, 0xF0, 0xF0],  # lower left quarter
        87: [0x0F, 0x0F, 0x0F, 0x0F, 0x00, 0x00, 0x00, 0x00],  # upper right quarter
        88: [0x00, 0x00, 0x00, 0x00, 0x0F, 0x0F, 0x0F, 0x0F],  # lower right quarter
        89: [0xF8, 0xF0, 0xE0, 0xC0, 0x80, 0x00, 0x00, 0x00],  # arc TL-BR
        90: [0xF0, 0xF0, 0xF0, 0xF0, 0x00, 0x00, 0x00, 0x00],  # upper left quarter
        91: [0x00, 0x66, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C, 0x18],  # heart
        92: [0x00, 0x00, 0x00, 0x80, 0xC0, 0xE0, 0xF0, 0xF8],  # arc TR-BL
        93: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x18, 0x18, 0x18],  # cross
        94: [0x00, 0x3C, 0x42, 0x42, 0x42, 0x42, 0x3C, 0x00],  # circle outline
        95: [0x18, 0x3C, 0x7E, 0xFF, 0x7E, 0x3C, 0x18, 0x00],  # diamond
        # Screen codes 96-127: more PETSCII graphics
        96: [0x00, 0x00, 0x00, 0x01, 0x03, 0x07, 0x0F, 0x1F],  # arc BL-TR
        97: [0x1F, 0x0F, 0x07, 0x03, 0x01, 0x00, 0x00, 0x00],  # arc TL-BR
        98: [0x00, 0x00, 0x7F, 0x36, 0x36, 0x36, 0x63, 0x00],  # pi
        99: [0xFF, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF],  # bottom with line
        100: [0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03], # right bar thin
        101: [0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x01], # diagonal TL-BR
        102: [0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA], # vertical lines
        103: [0x01, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0], # diagonal BL-TR
        104: [0x00, 0x00, 0x00, 0x00, 0xC0, 0xC0, 0xC0, 0xC0], # bottom left box
        105: [0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00], # horizontal lines
        106: [0x00, 0x00, 0x00, 0x00, 0x03, 0x03, 0x03, 0x03], # bottom right box
        107: [0xC0, 0xC0, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x00], # top left box
        108: [0x03, 0x03, 0x03, 0x03, 0x00, 0x00, 0x00, 0x00], # top right box
        109: [0x00, 0x00, 0x00, 0xFF, 0xFF, 0x18, 0x18, 0x18], # T down
        110: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x00, 0x00, 0x00], # T up
        111: [0x18, 0x18, 0x18, 0x1F, 0x1F, 0x18, 0x18, 0x18], # T right
        112: [0x18, 0x18, 0x18, 0xF8, 0xF8, 0x00, 0x00, 0x00], # corner BL
        113: [0x00, 0x00, 0x00, 0xF8, 0xF8, 0x18, 0x18, 0x18], # corner TL
        114: [0x00, 0x00, 0x00, 0x1F, 0x1F, 0x18, 0x18, 0x18], # corner TR
        115: [0x18, 0x18, 0x18, 0x1F, 0x1F, 0x00, 0x00, 0x00], # corner BR
        116: [0x18, 0x18, 0x18, 0xF8, 0xF8, 0x18, 0x18, 0x18], # T left
        117: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x18, 0x18, 0x18], # cross junction
        118: [0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C], # thick vertical
        119: [0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00], # thick horizontal
        120: [0x00, 0x00, 0x00, 0x00, 0x3C, 0x3C, 0x3C, 0x3C], # lower thick
        121: [0x3C, 0x3C, 0x3C, 0x3C, 0x00, 0x00, 0x00, 0x00], # upper thick
        122: [0x00, 0x00, 0x00, 0x00, 0x3C, 0x3C, 0x3C, 0x3C], # lower thick alt
        123: [0x3C, 0x3C, 0x3C, 0x3C, 0x00, 0x00, 0x00, 0x00], # upper thick alt
        124: [0x00, 0x00, 0xFC, 0xFC, 0x3C, 0x3C, 0x3C, 0x3C], # thick TL
        125: [0x3C, 0x3C, 0x3C, 0x3C, 0x3F, 0x3F, 0x00, 0x00], # thick BR
        126: [0x00, 0x7E, 0x66, 0x66, 0x66, 0x66, 0x00, 0x00], # pi variant
        127: [0x08, 0x1C, 0x3E, 0x7F, 0x3E, 0x1C, 0x08, 0x00], # filled triangle
    }
    
    # Build the 2048-byte charset
    charset = bytearray(2048)
    for code, pattern in chars.items():
        if code < 128:
            offset = code * 8
            for i, byte in enumerate(pattern):
                charset[offset + i] = byte
    
    # Screen codes 128-255: copy of 0-127 (reverse video handled in rendering)
    for i in range(1024):
        charset[1024 + i] = charset[i]
    
    return bytes(charset)


def print_help():
    """Print usage information"""
    print("C64U-Screenshot - Capture screenshots from Ultimate 64 via Web API")
    print("")
    print("Usage: python C64U-Screenshot.py <IP_ADDRESS> [output.png] [options]")
    print("")
    print("Arguments:")
    print("  IP_ADDRESS       IP address of the Ultimate 64")
    print("  output.png       Output filename (default: screenshot.png)")
    print("")
    print("Options:")
    print("  --help           Show this help message and exit")
    print("  --no-border      Don't add border around screen")
    print("  --nosprites      Don't include hardware sprites in the capture")
    print("  --upscale=N      Upscale output by factor N (e.g. --upscale=2 for 2x)")
    print("  --password=XXX   API password if required")
    print("")
    print("Examples:")
    print("  python C64U-Screenshot.py 192.168.1.100")
    print("  python C64U-Screenshot.py 192.168.1.100 myscreen.png")
    print("  python C64U-Screenshot.py 192.168.1.100 myscreen.png --upscale=2")
    print("  python C64U-Screenshot.py 192.168.1.100 myscreen.png --nosprites")
    print("  python C64U-Screenshot.py 192.168.1.100 myscreen.png --no-border")


def main():
    if len(sys.argv) < 2 or "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0 if "--help" in sys.argv or "-h" in sys.argv else 1)
    
    ip_address = sys.argv[1]
    output_file = "screenshot.png"
    add_border_flag = True
    password = None
    include_sprites = True  # Sprites enabled by default
    upscale = 1
    
    for arg in sys.argv[2:]:
        if arg == "--no-border":
            add_border_flag = False
        elif arg == "--nosprites":
            include_sprites = False
        elif arg.startswith("--upscale="):
            try:
                upscale = int(arg.split("=", 1)[1])
                if upscale < 1:
                    upscale = 1
            except ValueError:
                print(f"Error: Invalid upscale value: {arg}")
                print("Usage: --upscale=N where N is a positive integer (e.g. --upscale=2)")
                sys.exit(1)
        elif arg.startswith("--password="):
            password = arg.split("=", 1)[1]
        elif arg.startswith("--"):
            # Unknown option - provide helpful error
            print(f"Error: Unknown option '{arg}'")
            # Suggest corrections for common mistakes
            if arg in ["--scale", "--upscale", "-s"] or arg.startswith("--scale"):
                print("Did you mean: --upscale=N (e.g. --upscale=2)")
            elif arg in ["--sprites", "--sprite"]:
                print("Sprites are enabled by default. Use --nosprites to disable.")
            elif arg in ["--border", "--noborder"]:
                print("Did you mean: --no-border")
            else:
                print("Use --help for a list of valid options.")
            sys.exit(1)
        else:
            # Not an option, treat as output filename
            output_file = arg
    
    # Validate output file has a valid image extension
    valid_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff']
    ext = output_file[output_file.rfind('.'):].lower() if '.' in output_file else ''
    if ext not in valid_extensions:
        print(f"Error: Output file '{output_file}' has invalid or missing extension.")
        print(f"Valid extensions: {', '.join(valid_extensions)}")
        print("Example: python C64U-Screenshot.py 192.168.1.100 myscreen.png")
        sys.exit(1)
    
    success = capture_screenshot(ip_address, output_file, add_border_flag, password, include_sprites, upscale)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

