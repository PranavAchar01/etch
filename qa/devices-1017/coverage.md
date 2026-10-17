# Device catalog coverage (10-17)

Every device in engine/parts/devices.json composed from blocks (agent/blocks.py) and run through the assembly loop's pre-build gate offline (no LLM, no KiCad): 4392/4392 pass (100.0%). 329 hand-written base devices, 4063 modifier variants; 4266 distinct compositions checked in 900 s.

Gate per device: every piece in the library and the piece index; no lonely net as composed; the live fix-up chain (_normalize_pins through _analog) adds or drops no part; no LED stand-ins, ref mismatches, lonely nets, missing sound source or wiring lint after it; the generated MicroPython builds and passes its firmware test; firmware benchmarks generate; to_spec loads through etchlib (parts, pins). Rails are GND, 3V3 and VBUS only (Pico VSYS never used). Not run offline: KiCad place/route, DRC, ERC, ngspice.

Parts per passing board: min 5, median 14, max 110.

| Category | Pass | Devices | Rate |
|---|---|---|---|
| home and smart home | 382 | 382 | 100% |
| audio and music | 319 | 319 | 100% |
| toys and games | 270 | 270 | 100% |
| lighting | 265 | 265 | 100% |
| keyboards and input | 259 | 259 | 100% |
| tools and measurement | 254 | 254 | 100% |
| robots and vehicles | 242 | 242 | 100% |
| environment and weather | 201 | 201 | 100% |
| misc | 190 | 190 | 100% |
| health and fitness | 187 | 187 | 100% |
| office and desk | 158 | 158 | 100% |
| security | 155 | 155 | 100% |
| display and clock | 154 | 154 | 100% |
| wearables | 153 | 153 | 100% |
| communication and radio | 150 | 150 | 100% |
| garden and agriculture | 148 | 148 | 100% |
| holiday and decor | 139 | 139 | 100% |
| trackers and finders | 131 | 131 | 100% |
| automotive | 115 | 115 | 100% |
| kitchen | 112 | 112 | 100% |
| pets and animals | 104 | 104 | 100% |
| education and kits | 96 | 96 | 100% |
| energy and power | 81 | 81 | 100% |
| accessibility and assistive | 77 | 77 | 100% |
| storage and logging | 50 | 50 | 100% |
