# FNC-100 Quick Start

# Hardware Setup

## Power and Network

Required connections:

- Ethernet to router with DHCP
- 5 V power supply
- 7 V power supply
- USB 5 V power (for control interface)
- Control PC on the same network

Connection order:

1.  Power the router.
2.  Connect Ethernet between router and FNC‑100.
3.  Connect all power supplies.

## Power Supply Notes

- Internal fans must run for proper cooling.
- Do not operate the unit with only some power supplies connected.
- Power connectors differ:

| Supply  | Connector       |
|---------|-----------------|
| 5 V     | 2.1 mm barrel   |
| 7 V     | 2.5 mm barrel   |

The connectors are intentionally different to avoid mix‑ups.

## RF Connections

Each channel uses the same topology and shares a common reference clock.

Typical path:

- Reference oscillator → FNC‑100 ref input
- Photodetector → channel RF input
- Channel RF output → RF power amplifier → AOM

Optional but recommended:

- BPF or LPF filtering in RF paths

Channels 2, 3, and 4 follow the same wiring as Channel 1.

## Practical Tips

- Use an SMA torque wrench or 5/16 in wrench for easier connector access.
- If using only two channels, prefer **channels 1 and 3** to reduce spurs.

# Software Setup

## Requirements

Install:

- WinPython64 3.7.2: [Download link on Sourceforge](https://sourceforge.net/projects/winpython/files/WinPython_3.7/3.7.2.0/Winpython64-3.7.2.0.exe/download)
- Control software repository (**Important, select the `4Ch-counter` branch**): [Github link](https://github.com/jddes/Frequency-comb-DPLL/tree/4Ch-counter)

## Launching the GUI

Open a WinPython console and navigate inside the repository:

```shell
cd Frequency-comb-DPLL/digital_servo_python_gui
```

Then start the program:

```shell
python main.py
```

## Connecting to the Device

1.  Enter the device IP address manually, or use auto‑discovery.
2.  Click **Connect to device**.
3.  Open the **Config** tab once connected.

## Device Configuration

Inside the **Config** tab:

1.  Enter the correct **reference frequency**.
2.  Configure settings for each active channel.
3.  Click **Commit settings to device**.

The **Summary** tab displays all four channels simultaneously.

## Phase Lock

Inside a channel tab:

Enable **Lock phase** to align the measured phase with the X‑axis.

# Minimal Startup Checklist

1.  Router powered with DHCP.
2.  Ethernet connected to the FNC‑100.
3.  All power supplies connected.
4.  Reference oscillator connected.
5.  RF input/output connected for active channels.
6.  Install WinPython.
7.  Launch the GUI (`main.py`).
8.  Connect to the device.
9.  Enter reference frequency and channel parameters.
10. Commit settings.
11. Enable phase lock if required.
