# Provenance

| Item | Source | Commit | Licence |
|---|---|---|---|
| `lib/FTServo/` (LICENSE, README.md, library.properties, src/*) | https://github.com/ftservo/FTServo_Arduino (Feetech's official Arduino servo library) | 64922cda46e56b21b8c1d9e830d936a1941645ae | MIT (see lib/FTServo/LICENSE) |
| Keyword spotting approach (Arduino library export, `run_classifier`, `signal_t`) | Edge Impulse, https://github.com/edgeimpulse/example-standalone-inferencing-pico | 5ae72c5aafb9a556070b47e655db8687317ed975 | BSD-3-Clause-Clear (only the API is used; no code copied, model not included) |
| SO-101 arm (servo order, STS3215 servos) | https://github.com/TheRobotStudio/SO-ARM100 | a758567c3978dfeefe282ede0500085a48fe8f78 | Apache-2.0 |
| `etch_voice_arm/etch_voice_arm.ino`, this README | written for Etch | | |

The FTServo files were fetched from raw.githubusercontent.com at the commit above and are byte for byte unchanged.
ESP-SR was not used: it targets ESP32-S3 chips, and this board uses an RP2040 (Pico).
