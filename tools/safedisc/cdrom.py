"""A CD-ROM drive on Windows NT, backed by a 2048-byte-sector ISO.

Answers the IOCTLs the SafeDisc loader sends to \\\\.\\D: : the SCSI address,
the TOC, media lock, and SCSI pass-through (READ(10) and friends).
"""
import os
import struct

import weak

IOCTL_SCSI_GET_ADDRESS = 0x41018
IOCTL_CDROM_MEDIA_REMOVAL = 0x24804
IOCTL_CDROM_READ_TOC = 0x24000
IOCTL_SCSI_PASS_THROUGH = 0x4D004
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014


class CdDrive:
    def __init__(self, iso, log=print):
        self.f = open(iso, 'rb')
        self.sectors = os.path.getsize(iso) // 2048
        self.log = log
        self.reads = []
        # LBAs to fail as unreadable, for modelling a disc's deliberately bad sectors.
        self.bad = set()

    def read_sectors(self, lba, count):
        self.f.seek(lba * 2048)
        return self.f.read(count * 2048)

    @staticmethod
    def msf(lba):
        lba += 150
        return lba // (75 * 60), (lba // 75) % 60, lba % 75

    def toc(self):
        def entry(track, lba, control=0x14):
            m, s, f = self.msf(lba)
            return struct.pack('<BBBB', 0, control, track, 0) + bytes([0, m, s, f])
        body = entry(1, 0) + entry(0xAA, self.sectors)
        return struct.pack('>H', len(body) + 2) + bytes([1, 1]) + body

    def ioctl(self, emu, code, inb, out_size):
        if code == IOCTL_SCSI_GET_ADDRESS:
            return struct.pack('<IBBBB', 8, 1, 0, 0, 0), 1
        if code == IOCTL_CDROM_MEDIA_REMOVAL:
            return b'', 1
        if code == IOCTL_CDROM_READ_TOC:
            return self.toc().ljust(out_size, b'\0'), 1
        if code in (IOCTL_SCSI_PASS_THROUGH_DIRECT, IOCTL_SCSI_PASS_THROUGH):
            return self.pass_through(emu, code, inb, out_size)
        self.log(f'  cdrom: unhandled ioctl {code:#x}')
        return None

    def pass_through(self, emu, code, inb, out_size):
        (length, status, path, target, lun, cdb_len, sense_len, data_in,
         xfer, timeout, buf, sense_off) = struct.unpack_from('<HBBBBBBB3xIIII', inb)
        cdb = inb[0x1C:0x1C + cdb_len]
        op = cdb[0]
        out = bytearray(inb.ljust(out_size, b'\0'))
        data = b''
        sense = None
        if op == 0x28:                       # READ(10)
            lba = struct.unpack('>I', cdb[2:6])[0]
            count = struct.unpack('>H', cdb[7:9])[0]
            self.reads.append((lba, count))
            cost = weak.read_cost(emu.os.weak, lba, count)
            emu.os.ms += cost
            if any(l in self.bad for l in range(lba, lba + count)):
                sense = (0x03, 0x11, 0x05)   # medium error, L-EC uncorrectable
            elif lba + count > self.sectors:
                sense = (0x05, 0x21, 0x00)   # LBA out of range
            else:
                data = self.read_sectors(lba, count)
            self.log(f'  cdrom: READ(10) lba={lba} count={count} {cost}ms'
                     f'{" -> sense %x/%x/%x" % sense if sense else ""}')
        elif op == 0x00:                     # TEST UNIT READY
            pass
        elif op == 0x25:                     # READ CAPACITY
            data = struct.pack('>II', self.sectors - 1, 2048)
        elif op == 0x43:                     # READ TOC
            data = self.toc()
        elif op == 0x12:                     # INQUIRY
            data = bytes([5, 0x80, 0, 0x32, 31, 0, 0, 0]) + b'VIRTUAL ' + b'CD-ROM          ' + b'1.00'
        elif op == 0xBE:                     # READ CD: no raw sectors in an ISO
            lba = struct.unpack('>I', cdb[2:6])[0]
            self.log(f'  cdrom: READ CD lba={lba} flags={cdb[9]:#x}')
            sense = (0x05, 0x20, 0x00)
        else:
            self.log(f'  cdrom: unhandled CDB {cdb.hex()}')
            sense = (0x05, 0x20, 0x00)       # invalid command
        if data and data_in == 1:
            data = data[:xfer]
            if code == IOCTL_SCSI_PASS_THROUGH_DIRECT:
                emu.write(buf, data)
            else:                            # buffered: data follows at DataBufferOffset
                out[buf:buf + len(data)] = data
            struct.pack_into('<I', out, 0x0C, len(data))
        elif sense:
            struct.pack_into('<I', out, 0x0C, 0)
        if sense:
            out[2] = 0x02                    # CHECK CONDITION
            s = bytearray(18)
            s[0], s[2], s[7], s[12], s[13] = 0x70, sense[0], 10, sense[1], sense[2]
            out[sense_off:sense_off + min(sense_len, 18)] = s[:sense_len]
        else:
            out[2] = 0
            out[7] = 0                       # no sense returned
        return bytes(out), 1
