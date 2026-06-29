""" In-process mock of RP_PLL.RP_PLL_device.
No real TCP socket is opened; all register IO is stored in a dict.
Usage:
    python main.py --mock
"""
import types
import numpy as np
from SuperLaserLand_JD_RP import phaseReadoutDriver


_PHASE_BUFFER = np.zeros(phaseReadoutDriver.number_of_chunks, dtype=phaseReadoutDriver.data_dtype)
_PHASE_BUFFER['sync_bytes'] = phaseReadoutDriver.sync_bytes
_PHASE_BUFFER = _PHASE_BUFFER.tobytes()  # freeze as bytes; type intentionally changes


# ---------------------------------------------------------------------------
# clkw_status absolute address
# bd2absolute = lambda x: x + FPGA_BASE_ADDR_XADC  (= 0x80000000)
# "clkw_status": bd2absolute(0x00020000 + 0x04)
# ---------------------------------------------------------------------------
_FPGA_BASE_ADDR_XADC = 0x80000000
_CLKW_STATUS_ADDR    = _FPGA_BASE_ADDR_XADC + 0x00020000 + 0x04   # 0x80020004


# ---------------------------------------------------------------------------
# Canned file contents
# ---------------------------------------------------------------------------
_HARDWARE_TXT = b'has_mixer_board=1\nhas_dds=1\n'
_MAC_TXT      = b'eth0      Link encap:Ethernet  HWaddr DE:AD:BE:EF:CA:FE  \n'


# ---------------------------------------------------------------------------
# Mock device
# ---------------------------------------------------------------------------
class MockRP_PLL_device:
    """
    Drop-in replacement for RP_PLL.RP_PLL_device used in demo/test mode.

    All writes are stored in self.registers (address -> value) so that
    mock behaviour can be expanded later by inspecting written values.
    All reads return 0 except for the handful of addresses that need a
    specific value to avoid spinning waits or division-by-zero.
    """

    def __init__(self):
        self.valid_socket = False
        self.registers    = {}   # absolute address -> last written uint32

        # Stub out the sock attribute used by the firmware-upload path in
        # connection_widget.py (sock.shutdown / sock.close).  Those code
        # paths are disabled in the GUI when --mock is active anyway.
        self.sock = types.SimpleNamespace(
            shutdown=lambda *a: None,
            close=lambda: None,
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def OpenTCPConnection(self, host=None, port=None):
        self.valid_socket = True

    def CloseTCPConnection(self):
        self.valid_socket = False

    # ------------------------------------------------------------------
    # Register writes — stored in dict for later inspection/expansion
    # ------------------------------------------------------------------

    def write_uint32(self, address, value):
        self.registers[address] = int(value)

    def write_repeat_uint32(self, address, values, iSleepUs=0):
        for v in values:
            self.registers[address] = int(v)

    # ------------------------------------------------------------------
    # Register reads
    # ------------------------------------------------------------------

    def read_uint32(self, address):
        if address == _CLKW_STATUS_ADDR:
            # setADCclockPLL calls _waitForReg("clkw_status", 0x1).
            # Return the expected value immediately so it doesn't spin.
            return 0x1
        return self.registers.get(address, 0)

    # ------------------------------------------------------------------
    # Bulk data reads
    # ------------------------------------------------------------------

    def read_Zynq_buffer_int16(self, n_samples):
        """ADC / IQ data — flat zero trace is fine for demo purposes."""
        return bytes(2 * n_samples)

    def read_repeat(self, address, n_repeats):
        """Phase-logger bulk read.
        Both readData() and peakLatestPhases() always request the full
        buffer (len(_PHASE_BUFFER)), so we can return
        the correctly-formatted sync-byte buffer unconditionally.
        If a different size is ever requested, return correctly-sized zeros."""
        size = n_repeats * 4
        if size == len(_PHASE_BUFFER):
            return _PHASE_BUFFER
        # Fallback: fill with zeros (sync-byte check will warn, not crash)
        return bytes(size)

    # ------------------------------------------------------------------
    # Remote file I/O
    # ------------------------------------------------------------------

    def read_file_from_remote(self, strFilenameRemote):
        if strFilenameRemote == '/opt/hardware.txt':
            return _HARDWARE_TXT
        if strFilenameRemote == '/opt/macaddress.txt':
            return _MAC_TXT
        return b''

    def write_file_on_remote(self, strFilenameLocal, strFilenameRemote, file_data=None):
        pass

    def send_shell_command(self, strCommand):
        pass
