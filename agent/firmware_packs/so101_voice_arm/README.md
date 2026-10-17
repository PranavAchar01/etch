# VOICE ARM firmware (SO-101)

**Untested on hardware.** None of this has run on a real board or a real arm yet. The sketch and the
vendored servo library compile cleanly against stub headers on a desktop compiler (a syntax and type check
only). It has not been built with the Pico toolchain, flashed, or tried on the bus. Start with the arm
unloaded and the speeds low.

## What is here

| Path | What it is |
|---|---|
| `../main.py`, `../test.json` | Etch's own bring-up firmware (MicroPython). It lights D1 while the mic hears sound, and it passed Etch's firmware test in simulation. Flash it first to check the mic and LED. |
| `etch_voice_arm/etch_voice_arm.ino` | The arm firmware (Arduino, arduino-pico core). It listens in 1 s windows, runs an Edge Impulse keyword model, and moves the arm on `punch`, `jab`, `wave` or `rest`. |
| `lib/FTServo/` | Feetech's own servo library, vendored unchanged (see PROVENANCE.md). `SMS_STS` drives the STS3215 servos in the SO-101. |

## How the bus works on this board

The STS3215 servos share one data wire (SERVO_DATA on J2). The 74HC126 (U2) does the half-duplex switch:
gate 1 puts the Pico's TX (GP0) on the wire while TX_EN (GP2) is high, and gate 2 hands the wire back to
the Pico's RX (GP1) while RX_EN (GP3) is high. The sketch flips these in the two hooks Feetech's library
calls around every packet (`rFlushSCS` before sending, `wFlushSCS` after), so the library itself is untouched.

## Build steps

1. Arduino IDE or arduino-cli with the `earlephilhower/arduino-pico` core, board "Raspberry Pi Pico".
2. Copy `lib/FTServo` into your Arduino `libraries` folder.
3. The keyword model is **not included**. Make a free Edge Impulse project with the labels
   `punch`, `jab`, `wave`, `rest`, `noise` and `unknown`, at 16 kHz with 1 s windows. Record the samples
   through this board's MAX4466 if you can, so the model hears the same mic. Then export it as an
   **Arduino library** named `etch_voice_arm_inferencing` and add it to the IDE.
4. Open `etch_voice_arm/etch_voice_arm.ino`, build and flash.

**Without the model (stub mode).** The sketch checks for `etch_voice_arm_inferencing.h` with `__has_include`.
If the Edge Impulse library is not installed, it still builds, and a stub loop takes keywords typed in the
serial monitor instead (115200 baud, type `punch`, `jab`, `wave` or `rest` and press enter). Use it to check
the servo bus and calibrate the poses before training the model. Once the library is installed, the same
sketch switches to listening on the mic with no edits.

## Before the arm moves

- Power: the servos run from the barrel jack (J1, 7.4 V into VSERVO, with C1 470 uF as bulk). The Pico
  runs from its USB. The grounds are tied on the board. Never feed 7.4 V to the Pico.
- Servo IDs must be 1 to 6 in the SO-101 order (shoulder_pan, shoulder_lift, elbow_flex, wrist_flex,
  wrist_roll, gripper), at the STS3215 default of 1 Mbaud.
- The poses in `POSES[]` are **placeholders** in raw ticks (2048 = centre). Calibrate your arm first
  (for example with LeRobot's calibration) and edit them, otherwise a joint can hit its limit.
- `MIN_CONFIDENCE` (0.80) sets how sure the model must be before the arm moves.
