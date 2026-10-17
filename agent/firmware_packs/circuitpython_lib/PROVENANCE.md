# Provenance

Adafruit's CircuitPython libraries, used by the generated `code.py` on boards with a DHT11 sensor or a WS2812
(NeoPixel) strip. Fetched 2026-10-17 from raw.githubusercontent.com at the release tags below; byte for byte
unchanged.

| File | Source | Tag | Commit | sha256 | Licence |
|---|---|---|---|---|---|
| `adafruit_dht.py` | https://github.com/adafruit/Adafruit_CircuitPython_DHT | 4.0.12 | 09726be2006c07a91351514b199087d46c5ee8b5 | 90c4f90cf1f4c33dfd27a492d85a8ec211ba57d431bb5eae7ef25a62d8d4d849 | MIT (LICENSE-adafruit_dht) |
| `neopixel.py` | https://github.com/adafruit/Adafruit_CircuitPython_NeoPixel | 6.4.2 | aa82c0724ccb68fbd94605c9f4f09b19c5d2919d | ecd9c8bc70675b65fd708f61c9c938590e4e83af2f937bbd01e4b313fe554e1f | MIT (LICENSE-neopixel) |
| `test_only/adafruit_pixelbuf.py` | https://github.com/adafruit/Adafruit_CircuitPython_Pixelbuf | 2.1.0 | 86bba3670d15b5c6deb3a7358955c8090a2c468b | 8d57de266ab071b86965f0dbd1ab7de529273dfd47b5cdb4adedb738a08a3235 | MIT (test_only/LICENSE-adafruit_pixelbuf) |

`adafruit_pixelbuf` is built into CircuitPython 8 and later, so it is not shipped in the board's `lib/`. The
pure-Python copy (same API) is only used when Etch runs `code.py` on the laptop to test it.

The test runs the real library code under CPython. Only the chip-level modules are stand-ins: `board`,
`digitalio`, `analogio`, `pulseio` (replays a recorded DHT11 pulse train for 23 C / 41 %) and `neopixel_write`
(records the bytes sent to the strip).
