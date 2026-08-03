
import pytest

from SuperLaserLand_mock import SuperLaserLand_mock



def test_setup_writes_various():
    sl = SuperLaserLand_mock()

    test_list = [
        (sl.setup_ADC0_write, 0), 
        (sl.setup_ADC1_write, 1), 
        (sl.setup_DDC0_write, 2), 
        (sl.setup_DDC1_write, 3), 
        # (sl.setup_VNA_write, 4), 
        (sl.setup_counter_write, 5), 
        (sl.setup_DAC0_write, 6), 
        (sl.setup_DAC1_write, 7), 
        (sl.setup_DAC2_write, 8), 
        # (sl.setup_CRASH_MONITOR_write, 2**4), 
        # (sl.setup_IN10_write, 2**4 + 2**3), 
        ]

    Num_samples = 100

    for (func, expected_selector) in test_list:
        func(Num_samples)

        assert(sl.last_selector == expected_selector)
        assert(sl.Num_samples_read == Num_samples)

        Num_samples = Num_samples + 1


def test_ddc_phase_direct_select_register_bits():
    sl = SuperLaserLand_mock()

    captured = []
    sl.send_bus_cmd_16bits = lambda addr, val: captured.append((addr, val))

    # Default: phase_direct_select not passed -> bits 4 and 5 stay clear.
    sl.set_ddc_filter(0, filter_select=0, angle_select=0)
    assert(captured[-2][1] & (1 << 4) == 0)
    sl.set_ddc_filter(1, filter_select=0, angle_select=0)
    assert(captured[-2][1] & (1 << 5) == 0)

    # Channel 0 phase-direct bit (bit 4).
    sl.set_ddc_filter(0, filter_select=0, angle_select=0, phase_direct_select=1)
    assert(captured[-2][1] & (1 << 4) != 0)
    assert(sl.ddc0_phase_direct_select == 1)

    # Channel 1 phase-direct bit (bit 5).
    sl.set_ddc_filter(1, filter_select=0, angle_select=0, phase_direct_select=1)
    assert(captured[-2][1] & (1 << 5) != 0)
    assert(sl.ddc1_phase_direct_select == 1)

