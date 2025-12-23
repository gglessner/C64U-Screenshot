#!/usr/bin/env python3
"""
C64U-Screenshot - Ultimate 64 Screenshot Capture Tool
Captures a screenshot from a running Ultimate 64 via the Web API.

Supports all C64 graphics modes including cases where graphics data is stored
under ROM (VIC bank 3 at $E000-$FFFF). Uses an NMI-based ROM bypass technique
to read RAM hidden under Kernal ROM.

Author: Garland Glessner <gglessner@gmail.com>
License: GNU General Public License v3.0
Repository: https://github.com/gglessner/C64U-Screenshot

Usage: python C64U-Screenshot.py <IP_ADDRESS> [output.png] [options]
"""

__author__ = "Garland Glessner"
__email__ = "gglessner@gmail.com"
__license__ = "GPL-3.0"

import sys
import time
import requests
from PIL import Image, ImageDraw

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

# Buffer location for ROM copy (using $4000-$5FFF, 8KB in VIC bank 1)
# This area is typically safe as it's not commonly used for screen memory
COPY_BUFFER = 0x4000
COPY_BUFFER_SIZE = 0x2000  # 8KB

# Stub routine location (a small area for our injected code)
STUB_ADDR = 0x0340  # Cassette buffer area, usually safe


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
    
    def write_memory(self, address, data):
        """Write memory to the C64 via DMA"""
        url = f"{self.base_url}/v1/machine:writemem"
        params = {"address": f"{address:X}"}
        headers = dict(self.headers)
        headers["Content-Type"] = "application/octet-stream"
        try:
            response = requests.post(url, params=params, data=data, headers=headers, timeout=5)
            return response.status_code == 200
        except:
            return False
    
class VICIIState:
    """VIC-II chip state extracted from registers"""
    
    def __init__(self, vic_regs, cia2_data_port):
        self.raw_regs = vic_regs
        
        d011 = vic_regs[0x11]
        self.yscroll = d011 & 0x07
        self.rsel = (d011 >> 3) & 1
        self.den = (d011 >> 4) & 1
        self.bmm = (d011 >> 5) & 1
        self.ecm = (d011 >> 6) & 1
        
        d016 = vic_regs[0x16]
        self.xscroll = d016 & 0x07
        self.csel = (d016 >> 3) & 1
        self.mcm = (d016 >> 4) & 1
        
        d018 = vic_regs[0x18]
        self.char_mem_offset = ((d018 >> 1) & 0x07) * 0x800
        self.screen_mem_offset = ((d018 >> 4) & 0x0F) * 0x400
        self.bitmap_mem_offset = ((d018 >> 3) & 1) * 0x2000
        
        vic_bank_bits = cia2_data_port & 0x03
        self.vic_bank = (3 - vic_bank_bits) * 0x4000
        
        self.screen_mem_addr = self.vic_bank + self.screen_mem_offset
        self.char_mem_addr = self.vic_bank + self.char_mem_offset
        self.bitmap_mem_addr = self.vic_bank + self.bitmap_mem_offset
        
        self.border_color = vic_regs[0x20] & 0x0F
        self.background_color = vic_regs[0x21] & 0x0F
        self.background_color1 = vic_regs[0x22] & 0x0F
        self.background_color2 = vic_regs[0x23] & 0x0F
        self.background_color3 = vic_regs[0x24] & 0x0F
        
        self.sprite_multicolor0 = vic_regs[0x25] & 0x0F
        self.sprite_multicolor1 = vic_regs[0x26] & 0x0F
    
    def get_mode_name(self):
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


def check_rom_overlap(address, length):
    """Check if an address range overlaps with ROM areas"""
    end_address = address + length - 1
    
    # Kernal ROM: $E000-$FFFF
    if address <= 0xFFFF and end_address >= 0xE000:
        overlap_start = max(address, 0xE000)
        overlap_end = min(end_address, 0xFFFF)
        return ("KERNAL", overlap_start, overlap_end, 0xE000, 0x2000)
    
    # BASIC ROM: $A000-$BFFF  
    if address <= 0xBFFF and end_address >= 0xA000:
        overlap_start = max(address, 0xA000)
        overlap_end = min(end_address, 0xBFFF)
        return ("BASIC", overlap_start, overlap_end, 0xA000, 0x2000)
    
    return None


def generate_copy_stub(src_addr, dst_addr, length, jmp_target=None):
    """
    Generate 6502 machine code to copy memory with ROMs banked out.
    
    This routine:
    1. Saves registers and $01
    2. Banks out ROMs (sets $01 to $34)
    3. Copies 'length' bytes from src_addr to dst_addr
    4. Restores $01 and registers
    5. Sets completion marker
    6. Jumps to jmp_target (original NMI handler) or loops if None
    
    Returns: bytes of machine code
    """
    code = []
    
    # Save registers on stack
    code.extend([0x48])                    # PHA - save A
    code.extend([0x8A])                    # TXA
    code.extend([0x48])                    # PHA - save X  
    code.extend([0x98])                    # TYA
    code.extend([0x48])                    # PHA - save Y
    
    # Save current $01 value
    code.extend([0xA5, 0x01])              # LDA $01
    code.extend([0x48])                    # PHA
    
    # Bank out all ROMs: $01 = $34 (RAM everywhere, I/O visible)
    code.extend([0xA9, 0x34])              # LDA #$34
    code.extend([0x85, 0x01])              # STA $01
    
    # Set up zero page pointers for copy
    # $FB/$FC = source, $FD/$FE = destination
    code.extend([0xA9, src_addr & 0xFF])   # LDA #<src
    code.extend([0x85, 0xFB])              # STA $FB
    code.extend([0xA9, (src_addr >> 8) & 0xFF])  # LDA #>src
    code.extend([0x85, 0xFC])              # STA $FC
    
    code.extend([0xA9, dst_addr & 0xFF])   # LDA #<dst
    code.extend([0x85, 0xFD])              # STA $FD
    code.extend([0xA9, (dst_addr >> 8) & 0xFF])  # LDA #>dst
    code.extend([0x85, 0xFE])              # STA $FE
    
    # Calculate number of pages to copy
    num_pages = (length + 255) // 256
    
    # Outer loop counter in X (pages)
    code.extend([0xA2, num_pages])         # LDX #num_pages
    
    # Copy loop (outer: pages, inner: bytes)
    code.extend([0xA0, 0x00])              # LDY #$00  (inner loop: byte index)
    code.extend([0xB1, 0xFB])              # LDA ($FB),Y  - load from source
    code.extend([0x91, 0xFD])              # STA ($FD),Y  - store to dest
    code.extend([0xC8])                    # INY
    code.extend([0xD0, 0xF9])              # BNE inner_loop (-7)
    
    # Increment high bytes of pointers
    code.extend([0xE6, 0xFC])              # INC $FC (source high byte)
    code.extend([0xE6, 0xFE])              # INC $FE (dest high byte)
    
    # Decrement page counter
    code.extend([0xCA])                    # DEX
    code.extend([0xD0, 0xF0])              # BNE outer_loop (-16)
    
    # Restore $01
    code.extend([0x68])                    # PLA
    code.extend([0x85, 0x01])              # STA $01
    
    # Set completion marker
    code.extend([0xA9, 0x42])              # LDA #$42 (marker value)
    code.extend([0x85, 0x02])              # STA $02 (store marker at $02)
    
    # Restore registers (in reverse order of saving)
    code.extend([0x68])                    # PLA - restore Y
    code.extend([0xA8])                    # TAY
    code.extend([0x68])                    # PLA - restore X
    code.extend([0xAA])                    # TAX
    code.extend([0x68])                    # PLA - restore A
    
    if jmp_target is not None:
        # Jump to original handler (e.g., Kernal NMI handler)
        # This lets the original handler do proper RTI
        code.extend([0x4C, jmp_target & 0xFF, (jmp_target >> 8) & 0xFF])
    else:
        # Loop forever - wait to be re-frozen
        loop_addr = len(code)
        loop_target = STUB_ADDR + loop_addr
        code.extend([0x4C, loop_target & 0xFF, (loop_target >> 8) & 0xFF])
    
    return bytes(code)


def read_memory_via_copy(api, src_addr, length, vic):
    """
    Read memory that might be under ROM by injecting a copy routine.
    
    This is the core of the ROM bypass functionality:
    1. Save the current state at key memory locations
    2. Inject a copy routine that banks out ROMs and copies data
    3. Inject a JMP to the copy routine at a safe interrupt vector
    4. Resume briefly to execute the copy
    5. Re-freeze and read the copied data
    6. Restore all modified memory
    """
    print(f"  ROM bypass: Copying ${src_addr:04X}-${src_addr+length-1:04X} to buffer at ${COPY_BUFFER:04X}")
    
    # Step 1: Back up memory we'll modify
    # - The stub area (copy routine)
    # - The buffer area (where we'll copy to)
    # - Zero page locations we use ($FB-$FE)
    # - The NMI vector area (we'll use this to trigger our code)
    
    print("  Backing up memory...")
    
    # Save stub area
    stub_backup = api.read_memory(STUB_ADDR, 128)
    if stub_backup is None:
        print("  Error: Failed to backup stub area")
        return None
    
    # Save buffer area
    buffer_backup = api.read_memory(COPY_BUFFER, length)
    if buffer_backup is None:
        print("  Error: Failed to backup buffer area")
        return None
    
    # Save zero page pointers
    zp_backup = api.read_memory(0xFB, 4)
    if zp_backup is None:
        print("  Error: Failed to backup zero page")
        return None
    
    # Save marker location
    marker_backup = api.read_memory(0x02, 1)
    
    # Use NMI vector ($0318-$0319) - NMI is non-maskable, harder to disable
    nmi_vector_backup = api.read_memory(0x0318, 2)
    if nmi_vector_backup is None:
        print("  Error: Failed to backup NMI vector")
        return None
    
    # Get the original NMI handler address so we can jump to it when done
    original_nmi_handler = nmi_vector_backup[0] + (nmi_vector_backup[1] << 8)
    print(f"  Original NMI handler: ${original_nmi_handler:04X}")
    
    # Also backup CIA2 state for NMI triggering
    cia2_icr_backup = api.read_memory(0xDD0D, 1)
    cia2_timer_backup = api.read_memory(0xDD04, 3)  # Timer A low, high, control
    
    try:
        # Step 2: Generate copy routine that jumps to original NMI handler when done
        print("  Injecting copy routine...")
        copy_code = generate_copy_stub(src_addr, COPY_BUFFER, length, jmp_target=original_nmi_handler)
        print(f"    Stub size: {len(copy_code)} bytes at ${STUB_ADDR:04X}")
        print(f"    Copy: ${src_addr:04X} -> ${COPY_BUFFER:04X}, {length} bytes ({(length+255)//256} pages)")
        print(f"    Will jump to original handler at ${original_nmi_handler:04X} when done")
        
        if not api.write_memory(STUB_ADDR, copy_code):
            print("  Error: Failed to write copy routine")
            return None
        
        # Step 3: Modify NMI vector to point to our routine
        nmi_vector = bytes([STUB_ADDR & 0xFF, (STUB_ADDR >> 8) & 0xFF])
        if not api.write_memory(0x0318, nmi_vector):
            print("  Error: Failed to modify NMI vector")
            return None
        
        # Clear the completion marker
        if not api.write_memory(0x02, bytes([0x00])):
            print("  Error: Failed to clear marker")
            return None
        
        # Step 4: Configure CIA2 to trigger NMI via Timer A
        # CIA2 is at $DD00-$DD0F
        # $DD04-$DD05: Timer A value
        # $DD0E: Timer A control
        # $DD0D: Interrupt Control Register (ICR)
        print("  Triggering NMI via CIA2 timer...")
        
        # First, acknowledge any pending interrupts by reading ICR
        api.read_memory(0xDD0D, 1)
        
        # Set timer to very short value
        api.write_memory(0xDD04, bytes([0x02, 0x00]))  # Timer = 2 cycles
        
        # Enable Timer A NMI: write $81 to ICR (bit 7=set, bit 0=Timer A)
        api.write_memory(0xDD0D, bytes([0x81]))
        
        # Start Timer A with force load: $11 = bit 0 (start) + bit 4 (force load)
        api.write_memory(0xDD0E, bytes([0x11]))
        
        # Step 5: Resume and wait for copy to complete
        print("  Executing copy routine...")
        if not api.resume():
            print("  Error: Failed to resume machine")
            return None
        
        # Wait for the copy to complete
        # 8KB copy at 1MHz takes ~100ms, so 500ms should be plenty
        time.sleep(0.5)
        
        # Now pause and check if it completed
        api.pause()
        
        marker = api.read_memory(0x02, 1)
        
        if marker and marker[0] == 0x42:
            print("  Copy complete (marker verified)")
            print("  Original NMI handler returned control to program")
        else:
            print(f"  Warning: Copy may have issues (marker={marker[0] if marker else 'None':02X}, expected 42)")
        
        # Step 5: Read the copied data from buffer
        print("  Reading copied data from buffer...")
        copied_data = api.read_memory(COPY_BUFFER, length)
        
        if copied_data is None:
            print("  Error: Failed to read copied data")
            return None
        
        return copied_data
        
    finally:
        # Step 6: Restore all modified memory
        print("  Restoring original memory...")
        
        # Make sure we're paused
        api.pause()
        
        # Disable CIA2 Timer A NMI
        api.write_memory(0xDD0D, bytes([0x01]))  # Clear Timer A NMI enable
        
        # Restore CIA2 timer state
        if cia2_timer_backup:
            api.write_memory(0xDD04, cia2_timer_backup)
        
        # Restore NMI vector
        api.write_memory(0x0318, nmi_vector_backup)
        
        # Restore zero page
        api.write_memory(0xFB, zp_backup)
        
        # Restore marker
        if marker_backup:
            api.write_memory(0x02, marker_backup)
        
        # Restore stub area
        api.write_memory(STUB_ADDR, stub_backup)
        
        # Restore buffer area
        api.write_memory(COPY_BUFFER, buffer_backup)
        
        print("  Memory restored")


def read_memory_smart(api, address, length, vic):
    """
    Smart memory read that automatically handles ROM-shadowed areas.
    Uses the copy routine bypass when necessary.
    """
    overlap = check_rom_overlap(address, length)
    
    if overlap is None:
        # No ROM overlap, read directly
        return api.read_memory(address, length)
    
    rom_name, overlap_start, overlap_end, rom_base, rom_size = overlap
    print(f"  Detected {rom_name} ROM overlap at ${overlap_start:04X}-${overlap_end:04X}")
    
    # Use the ROM bypass copy routine
    return read_memory_via_copy(api, address, length, vic)


class SpriteInfo:
    """Information about a single sprite"""
    
    def __init__(self, sprite_num, vic_regs, sprite_pointer, vic_bank):
        self.num = sprite_num
        x_low = vic_regs[sprite_num * 2]
        x_msb = (vic_regs[0x10] >> sprite_num) & 1
        self.x = x_low + (x_msb * 256)
        self.y = vic_regs[sprite_num * 2 + 1]
        self.enabled = (vic_regs[0x15] >> sprite_num) & 1
        self.y_expand = (vic_regs[0x17] >> sprite_num) & 1
        self.priority = (vic_regs[0x1B] >> sprite_num) & 1
        self.multicolor = (vic_regs[0x1C] >> sprite_num) & 1
        self.x_expand = (vic_regs[0x1D] >> sprite_num) & 1
        self.color = vic_regs[0x27 + sprite_num] & 0x0F
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
    sprites = []
    for i in range(8):
        sprite = SpriteInfo(i, vic_regs, sprite_pointers[i], vic_bank)
        sprites.append(sprite)
    return sprites


def render_sprite(sprite, sprite_data, vic):
    base_width = 24
    base_height = 21
    width = base_width * (2 if sprite.x_expand else 1)
    height = base_height * (2 if sprite.y_expand else 1)
    
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pixels = img.load()
    
    sprite_color = C64_PALETTE[sprite.color]
    mc0 = C64_PALETTE[vic.sprite_multicolor0]
    mc1 = C64_PALETTE[vic.sprite_multicolor1]
    
    for row in range(21):
        row_data = sprite_data[row * 3: row * 3 + 3]
        if len(row_data) < 3:
            continue
        bits = (row_data[0] << 16) | (row_data[1] << 8) | row_data[2]
        
        if sprite.multicolor:
            for col in range(12):
                bit_pair = (bits >> (22 - col * 2)) & 0x03
                if bit_pair == 0:
                    color = None
                elif bit_pair == 1:
                    color = mc0
                elif bit_pair == 2:
                    color = sprite_color
                else:
                    color = mc1
                
                if color is not None:
                    x_base = col * 2
                    for dx in range(2 * (2 if sprite.x_expand else 1)):
                        for dy in range(2 if sprite.y_expand else 1):
                            px = x_base * (2 if sprite.x_expand else 1) + dx
                            py = row * (2 if sprite.y_expand else 1) + dy
                            if 0 <= px < width and 0 <= py < height:
                                pixels[px, py] = (*color, 255)
        else:
            for col in range(24):
                bit = (bits >> (23 - col)) & 1
                if bit:
                    for dx in range(2 if sprite.x_expand else 1):
                        for dy in range(2 if sprite.y_expand else 1):
                            px = col * (2 if sprite.x_expand else 1) + dx
                            py = row * (2 if sprite.y_expand else 1) + dy
                            if 0 <= px < width and 0 <= py < height:
                                pixels[px, py] = (*sprite_color, 255)
    
    screen_x = sprite.x - 24
    screen_y = sprite.y - 50
    return img, screen_x, screen_y


def overlay_sprites(screen_img, sprites, sprite_data_list, vic, front_only=False):
    result = screen_img.convert('RGBA')
    
    for i in range(7, -1, -1):
        sprite = sprites[i]
        if not sprite.enabled:
            continue
        if front_only and sprite.priority == 1:
            continue
        
        sprite_data = sprite_data_list[i]
        if sprite_data is None:
            continue
        
        sprite_img, x, y = render_sprite(sprite, sprite_data, vic)
        
        if x < screen_img.width and y < screen_img.height:
            if x + sprite_img.width > 0 and y + sprite_img.height > 0:
                result.paste(sprite_img, (x, y), sprite_img)
    
    return result.convert('RGB')


def render_standard_text_mode(vic, screen_mem, color_mem, char_rom):
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for row in range(25):
        for col in range(40):
            screen_pos = row * 40 + col
            char_code = screen_mem[screen_pos]
            color_idx = color_mem[screen_pos] & 0x0F
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
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for row in range(25):
        for col in range(40):
            screen_pos = row * 40 + col
            char_code = screen_mem[screen_pos]
            color_byte = color_mem[screen_pos]
            color_idx = color_byte & 0x0F
            is_multicolor = (color_byte & 0x08) != 0
            char_offset = char_code * 8
            
            for y in range(8):
                byte = char_rom[char_offset + y]
                
                if is_multicolor:
                    for x in range(4):
                        bits = (byte >> (6 - x * 2)) & 0x03
                        if bits == 0:
                            c = C64_PALETTE[vic.background_color]
                        elif bits == 1:
                            c = C64_PALETTE[vic.background_color1]
                        elif bits == 2:
                            c = C64_PALETTE[vic.background_color2]
                        else:
                            c = C64_PALETTE[color_idx & 0x07]
                        pixels[col * 8 + x * 2, row * 8 + y] = c
                        pixels[col * 8 + x * 2 + 1, row * 8 + y] = c
                else:
                    for x in range(8):
                        if byte & (0x80 >> x):
                            pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[color_idx]
                        else:
                            pixels[col * 8 + x, row * 8 + y] = C64_PALETTE[vic.background_color]
    return img


def render_hires_bitmap_mode(vic, bitmap_mem, screen_mem):
    img = Image.new('RGB', (320, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for char_row in range(25):
        for char_col in range(40):
            screen_pos = char_row * 40 + char_col
            color_byte = screen_mem[screen_pos]
            fg_color = (color_byte >> 4) & 0x0F
            bg_color = color_byte & 0x0F
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
    img = Image.new('RGB', (160, 200), C64_PALETTE[vic.background_color])
    pixels = img.load()
    
    for char_row in range(25):
        for char_col in range(40):
            screen_pos = char_row * 40 + char_col
            color_byte = screen_mem[screen_pos]
            color1 = (color_byte >> 4) & 0x0F
            color2 = color_byte & 0x0F
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
                    else:
                        c = C64_PALETTE[color3]
                    pixels[char_col * 4 + x, char_row * 8 + y] = c
    
    img = img.resize((320, 200), Image.NEAREST)
    return img


def render_ecm_mode(vic, screen_mem, color_mem, char_rom):
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
            char_code = screen_byte & 0x3F
            bg_select = (screen_byte >> 6) & 0x03
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
    if vic.rsel == 1 and vic.csel == 1:
        return img
    
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
    w, h = img.size
    new_w = w + border_size * 2
    new_h = h + border_size * 2
    bordered = Image.new('RGB', (new_w, new_h), C64_PALETTE[border_color])
    bordered.paste(img, (border_size, border_size))
    return bordered


def get_embedded_charset():
    """Return the standard C64 character ROM (uppercase/graphics set)"""
    chars = {
        0: [0x3C, 0x66, 0x6E, 0x6E, 0x60, 0x62, 0x3C, 0x00],
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
        27: [0x3C, 0x30, 0x30, 0x30, 0x30, 0x30, 0x3C, 0x00],
        28: [0x0C, 0x12, 0x30, 0x7C, 0x30, 0x62, 0xFC, 0x00],
        29: [0x3C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x3C, 0x00],
        30: [0x00, 0x08, 0x1C, 0x3E, 0x08, 0x08, 0x00, 0x00],
        31: [0x00, 0x10, 0x30, 0x7F, 0x30, 0x10, 0x00, 0x00],
        32: [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        33: [0x18, 0x18, 0x18, 0x18, 0x00, 0x00, 0x18, 0x00],
        34: [0x66, 0x66, 0x66, 0x00, 0x00, 0x00, 0x00, 0x00],
        35: [0x66, 0x66, 0xFF, 0x66, 0xFF, 0x66, 0x66, 0x00],
        36: [0x18, 0x3E, 0x60, 0x3C, 0x06, 0x7C, 0x18, 0x00],
        37: [0x62, 0x66, 0x0C, 0x18, 0x30, 0x66, 0x46, 0x00],
        38: [0x3C, 0x66, 0x3C, 0x38, 0x67, 0x66, 0x3F, 0x00],
        39: [0x06, 0x0C, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00],
        40: [0x0C, 0x18, 0x30, 0x30, 0x30, 0x18, 0x0C, 0x00],
        41: [0x30, 0x18, 0x0C, 0x0C, 0x0C, 0x18, 0x30, 0x00],
        42: [0x00, 0x66, 0x3C, 0xFF, 0x3C, 0x66, 0x00, 0x00],
        43: [0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00],
        44: [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30],
        45: [0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00, 0x00],
        46: [0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00],
        47: [0x00, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x00],
        48: [0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00],
        49: [0x18, 0x18, 0x38, 0x18, 0x18, 0x18, 0x7E, 0x00],
        50: [0x3C, 0x66, 0x06, 0x0C, 0x30, 0x60, 0x7E, 0x00],
        51: [0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00],
        52: [0x06, 0x0E, 0x1E, 0x66, 0x7F, 0x06, 0x06, 0x00],
        53: [0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00],
        54: [0x3C, 0x66, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00],
        55: [0x7E, 0x66, 0x0C, 0x18, 0x18, 0x18, 0x18, 0x00],
        56: [0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00],
        57: [0x3C, 0x66, 0x66, 0x3E, 0x06, 0x66, 0x3C, 0x00],
        58: [0x00, 0x00, 0x18, 0x00, 0x00, 0x18, 0x00, 0x00],
        59: [0x00, 0x00, 0x18, 0x00, 0x00, 0x18, 0x18, 0x30],
        60: [0x0E, 0x18, 0x30, 0x60, 0x30, 0x18, 0x0E, 0x00],
        61: [0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00, 0x00, 0x00],
        62: [0x70, 0x18, 0x0C, 0x06, 0x0C, 0x18, 0x70, 0x00],
        63: [0x3C, 0x66, 0x06, 0x0C, 0x18, 0x00, 0x18, 0x00],
        64: [0x00, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00],
        65: [0x08, 0x1C, 0x3E, 0x7F, 0x7F, 0x1C, 0x3E, 0x00],
        66: [0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18],
        67: [0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF],
        68: [0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00],
        69: [0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0, 0xF0],
        70: [0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA],
        71: [0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F],
        72: [0x00, 0x00, 0x00, 0x00, 0xAA, 0x55, 0xAA, 0x55],
        73: [0x0F, 0x07, 0x03, 0x01, 0x00, 0x00, 0x00, 0x00],
        74: [0x55, 0xAA, 0x55, 0xAA, 0x00, 0x00, 0x00, 0x00],
        75: [0x00, 0x00, 0x00, 0x00, 0x01, 0x03, 0x07, 0x0F],
        76: [0x00, 0x00, 0x00, 0x00, 0x80, 0xC0, 0xE0, 0xF0],
        77: [0xF0, 0xE0, 0xC0, 0x80, 0x00, 0x00, 0x00, 0x00],
        78: [0x01, 0x03, 0x07, 0x0F, 0x1F, 0x3F, 0x7F, 0xFF],
        79: [0x80, 0xC0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE, 0xFF],
        80: [0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE0, 0xC0, 0x80],
        81: [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF],
        82: [0xFF, 0x7F, 0x3F, 0x1F, 0x0F, 0x07, 0x03, 0x01],
        83: [0x3C, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C],
        84: [0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0],
        85: [0x18, 0x18, 0x7E, 0xFF, 0xFF, 0x18, 0x3C, 0x00],
        86: [0x00, 0x00, 0x00, 0x00, 0xF0, 0xF0, 0xF0, 0xF0],
        87: [0x0F, 0x0F, 0x0F, 0x0F, 0x00, 0x00, 0x00, 0x00],
        88: [0x00, 0x00, 0x00, 0x00, 0x0F, 0x0F, 0x0F, 0x0F],
        89: [0xF8, 0xF0, 0xE0, 0xC0, 0x80, 0x00, 0x00, 0x00],
        90: [0xF0, 0xF0, 0xF0, 0xF0, 0x00, 0x00, 0x00, 0x00],
        91: [0x00, 0x66, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C, 0x18],
        92: [0x00, 0x00, 0x00, 0x80, 0xC0, 0xE0, 0xF0, 0xF8],
        93: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x18, 0x18, 0x18],
        94: [0x00, 0x3C, 0x42, 0x42, 0x42, 0x42, 0x3C, 0x00],
        95: [0x18, 0x3C, 0x7E, 0xFF, 0x7E, 0x3C, 0x18, 0x00],
        96: [0x00, 0x00, 0x00, 0x01, 0x03, 0x07, 0x0F, 0x1F],
        97: [0x1F, 0x0F, 0x07, 0x03, 0x01, 0x00, 0x00, 0x00],
        98: [0x00, 0x00, 0x7F, 0x36, 0x36, 0x36, 0x63, 0x00],
        99: [0xFF, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF],
        100: [0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03],
        101: [0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x01],
        102: [0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA],
        103: [0x01, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0],
        104: [0x00, 0x00, 0x00, 0x00, 0xC0, 0xC0, 0xC0, 0xC0],
        105: [0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00],
        106: [0x00, 0x00, 0x00, 0x00, 0x03, 0x03, 0x03, 0x03],
        107: [0xC0, 0xC0, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x00],
        108: [0x03, 0x03, 0x03, 0x03, 0x00, 0x00, 0x00, 0x00],
        109: [0x00, 0x00, 0x00, 0xFF, 0xFF, 0x18, 0x18, 0x18],
        110: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x00, 0x00, 0x00],
        111: [0x18, 0x18, 0x18, 0x1F, 0x1F, 0x18, 0x18, 0x18],
        112: [0x18, 0x18, 0x18, 0xF8, 0xF8, 0x00, 0x00, 0x00],
        113: [0x00, 0x00, 0x00, 0xF8, 0xF8, 0x18, 0x18, 0x18],
        114: [0x00, 0x00, 0x00, 0x1F, 0x1F, 0x18, 0x18, 0x18],
        115: [0x18, 0x18, 0x18, 0x1F, 0x1F, 0x00, 0x00, 0x00],
        116: [0x18, 0x18, 0x18, 0xF8, 0xF8, 0x18, 0x18, 0x18],
        117: [0x18, 0x18, 0x18, 0xFF, 0xFF, 0x18, 0x18, 0x18],
        118: [0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C, 0x3C],
        119: [0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00],
        120: [0x00, 0x00, 0x00, 0x00, 0x3C, 0x3C, 0x3C, 0x3C],
        121: [0x3C, 0x3C, 0x3C, 0x3C, 0x00, 0x00, 0x00, 0x00],
        122: [0x00, 0x00, 0x00, 0x00, 0x3C, 0x3C, 0x3C, 0x3C],
        123: [0x3C, 0x3C, 0x3C, 0x3C, 0x00, 0x00, 0x00, 0x00],
        124: [0x00, 0x00, 0xFC, 0xFC, 0x3C, 0x3C, 0x3C, 0x3C],
        125: [0x3C, 0x3C, 0x3C, 0x3C, 0x3F, 0x3F, 0x00, 0x00],
        126: [0x00, 0x7E, 0x66, 0x66, 0x66, 0x66, 0x00, 0x00],
        127: [0x08, 0x1C, 0x3E, 0x7F, 0x3E, 0x1C, 0x08, 0x00],
    }
    
    charset = bytearray(2048)
    for code, pattern in chars.items():
        if code < 128:
            offset = code * 8
            for i, byte in enumerate(pattern):
                charset[offset + i] = byte
    
    for i in range(1024):
        charset[1024 + i] = charset[i]
    
    return bytes(charset)


def capture_screenshot(ip_address, output_file="screenshot.png", add_border_flag=True, 
                       password=None, include_sprites=True, upscale=1, use_rom_bypass=True):
    """Main function to capture a screenshot from the Ultimate 64"""
    
    api = Ultimate64API(ip_address, password)
    
    print(f"Connecting to Ultimate 64 at {ip_address}...")
    print(f"ROM bypass mode: {'ENABLED' if use_rom_bypass else 'disabled'}")
    
    print("Freezing machine...")
    if not api.pause():
        print("Warning: Failed to pause machine (may already be paused)")
    
    try:
        print("Reading VIC-II registers...")
        vic_regs = api.read_memory(0xD000, 0x30)
        if vic_regs is None:
            print("Error: Failed to read VIC-II registers")
            return False
        
        print("Reading CIA2 for VIC bank selection...")
        cia2_data = api.read_memory(0xDD00, 1)
        if cia2_data is None:
            print("Error: Failed to read CIA2")
            return False
        
        vic = VICIIState(vic_regs, cia2_data[0])
        vic.dump_info()
        
        print("Reading Color RAM...")
        color_mem = api.read_memory(0xD800, 1000)
        if color_mem is None:
            print("Error: Failed to read color RAM")
            return False
        
        # Read screen memory - use smart read if ROM bypass enabled
        print(f"Reading Screen Memory at ${vic.screen_mem_addr:04X}...")
        if use_rom_bypass:
            screen_mem = read_memory_smart(api, vic.screen_mem_addr, 1024, vic)
        else:
            screen_mem = api.read_memory(vic.screen_mem_addr, 1024)
        
        if screen_mem is None:
            print("Error: Failed to read screen memory")
            return False
        
        if vic.bmm:
            print(f"Reading Bitmap Memory at ${vic.bitmap_mem_addr:04X}...")
            if use_rom_bypass:
                bitmap_mem = read_memory_smart(api, vic.bitmap_mem_addr, 8000, vic)
            else:
                bitmap_mem = api.read_memory(vic.bitmap_mem_addr, 8000)
            
            if bitmap_mem is None:
                print("Error: Failed to read bitmap memory")
                return False
            char_rom = None
        else:
            char_addr_in_bank = vic.char_mem_offset
            uses_char_rom = False
            
            if vic.vic_bank == 0x0000 or vic.vic_bank == 0x8000:
                if char_addr_in_bank >= 0x1000 and char_addr_in_bank < 0x2000:
                    uses_char_rom = True
            
            if uses_char_rom:
                print("Reading Character ROM...")
                print("  Using embedded C64 character ROM")
                char_rom = get_embedded_charset()
            else:
                print(f"Reading Character Memory at ${vic.char_mem_addr:04X}...")
                if use_rom_bypass:
                    char_rom = read_memory_smart(api, vic.char_mem_addr, 2048, vic)
                else:
                    char_rom = api.read_memory(vic.char_mem_addr, 2048)
                
                if char_rom is None:
                    print("Error: Failed to read character memory")
                    return False
            
            bitmap_mem = None
        
        print("Rendering screen...")
        
        if vic.bmm and vic.mcm:
            img = render_multicolor_bitmap_mode(vic, bitmap_mem, screen_mem, color_mem)
        elif vic.bmm and not vic.mcm:
            img = render_hires_bitmap_mode(vic, bitmap_mem, screen_mem)
        elif vic.ecm:
            img = render_ecm_mode(vic, screen_mem, color_mem, char_rom)
        elif vic.mcm:
            img = render_multicolor_text_mode(vic, screen_mem, color_mem, char_rom)
        else:
            img = render_standard_text_mode(vic, screen_mem, color_mem, char_rom)
        
        if include_sprites:
            print("Processing sprites...")
            sprite_pointers = screen_mem[0x3F8:0x400]
            sprites = get_sprite_info(vic_regs, sprite_pointers, vic.vic_bank)
            
            enabled_count = 0
            for sprite in sprites:
                if sprite.enabled:
                    print(f"  {sprite}")
                    enabled_count += 1
            
            if enabled_count == 0:
                print("  No sprites enabled")
            else:
                print(f"  {enabled_count} sprite(s) enabled")
            
            sprite_data_list = []
            for sprite in sprites:
                if sprite.enabled:
                    data = api.read_memory(sprite.data_addr, 64)
                    sprite_data_list.append(data)
                else:
                    sprite_data_list.append(None)
            
            img = overlay_sprites(img, sprites, sprite_data_list, vic, front_only=False)
        
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
        
        if add_border_flag:
            img = add_border(img, vic.border_color)
        
        if upscale > 1:
            new_width = img.width * upscale
            new_height = img.height * upscale
            img = img.resize((new_width, new_height), Image.NEAREST)
            print(f"Upscaled to {new_width}x{new_height} ({upscale}x)")
        
        img.save(output_file)
        print(f"Screenshot saved to {output_file}")
        
        return True
        
    finally:
        print("Resuming machine...")
        if api.resume():
            print("Machine resumed successfully")
        else:
            print("Warning: Failed to resume machine")


def print_help():
    print("C64U-Screenshot - Capture screenshots from Ultimate 64")
    print("")
    print("Captures the current screen from a running C64 program via the Ultimate 64")
    print("Web API. Supports all graphics modes and hardware sprites.")
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
    print("  --upscale=N      Upscale output by factor N (e.g. --upscale=2)")
    print("  --no-rom-bypass  Disable ROM bypass (faster, but fails on VIC bank 3)")
    print("  --password=XXX   API password if required")
    print("")
    print("Examples:")
    print("  python C64U-Screenshot.py 192.168.1.100")
    print("  python C64U-Screenshot.py 192.168.1.100 myscreen.png --upscale=2")


def main():
    if len(sys.argv) < 2 or "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0 if "--help" in sys.argv or "-h" in sys.argv else 1)
    
    ip_address = sys.argv[1]
    output_file = "screenshot.png"
    add_border_flag = True
    password = None
    include_sprites = True
    upscale = 1
    use_rom_bypass = True
    
    for arg in sys.argv[2:]:
        if arg == "--no-border":
            add_border_flag = False
        elif arg == "--nosprites":
            include_sprites = False
        elif arg == "--no-rom-bypass":
            use_rom_bypass = False
        elif arg.startswith("--upscale="):
            try:
                upscale = int(arg.split("=", 1)[1])
                if upscale < 1:
                    upscale = 1
            except ValueError:
                print(f"Error: Invalid upscale value: {arg}")
                print("Usage: --upscale=N where N is a positive integer")
                sys.exit(1)
        elif arg.startswith("--password="):
            password = arg.split("=", 1)[1]
        elif arg.startswith("--"):
            print(f"Error: Unknown option '{arg}'")
            if arg in ["--scale", "--upscale", "-s"] or arg.startswith("--scale"):
                print("Did you mean: --upscale=N (e.g. --upscale=2)")
            elif arg in ["--sprites", "--sprite"]:
                print("Sprites are enabled by default. Use --nosprites to disable.")
            elif arg in ["--border", "--noborder"]:
                print("Did you mean: --no-border")
            elif arg in ["--bypass", "--rom-bypass"]:
                print("ROM bypass is enabled by default. Use --no-rom-bypass to disable.")
            else:
                print("Use --help for a list of valid options.")
            sys.exit(1)
        else:
            output_file = arg
    
    valid_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff']
    ext = output_file[output_file.rfind('.'):].lower() if '.' in output_file else ''
    if ext not in valid_extensions:
        print(f"Error: Output file '{output_file}' has invalid or missing extension.")
        print(f"Valid extensions: {', '.join(valid_extensions)}")
        sys.exit(1)
    
    success = capture_screenshot(ip_address, output_file, add_border_flag, password, 
                                 include_sprites, upscale, use_rom_bypass)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

