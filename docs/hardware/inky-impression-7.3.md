# Inky Impression 7.3" — implementation notes

Condensed from Pimoroni's [Getting Started with Inky Impression](https://learn.pimoroni.com/article/getting-started-with-inky-impression) guide and the [`pimoroni/inky`](https://github.com/pimoroni/inky) library source (MIT).

For the full library and examples, run `scripts/fetch-references.sh`. It clones the library into `reference/inky/`, which is git-ignored.

## Panel variants (both 800×480)

| Variant | Driver module | EEPROM variant | `--type` for `inky.auto` fallback | Colours |
|---|---|---|---|---|
| 7-colour "Gallery Palette" | `inky/inky_ac073tc1a.py` | 20 | `7colour` / `impressions73` | BLACK 0, WHITE 1, GREEN 2, BLUE 3, RED 4, YELLOW 5, ORANGE 6, CLEAN 7 |
| Spectra 6 | `inky/inky_e673.py` | 22, 26 | `spectra73` | BLACK 0, WHITE 1, YELLOW 2, RED 3, BLUE 5, GREEN 6 (no orange, index 4 unused). Supports `speed=NORMAL/MEDIUM/FAST`; faster speeds reduce saturation. |

⚠ The colour **index numbers differ between variants**, so always use the constants on the display object (`inky.RED`) and never literal ints. In inky-dash, map these through the theme.

Exact RGB values the quantiser maps to: `SATURATED_PALETTE` / `DESATURATED_PALETTE` in each driver module. `set_image(img, saturation=s)` blends between the two palettes.

### How `set_image` treats an image on the Spectra 6 (`inky_e673.py`)

| Image | What the driver does |
|---|---|
| **RGB** (or anything not `P`) | Always quantised with **Floyd–Steinberg dithering** against the blended palette. At `saturation=0.5` no RGB value except black is an exact palette colour. Even at 1.0, white is `(161,164,165)`. So flat RGB fills and RGB text get speckles. |
| **`P` with exactly 6 palette colours** | Dithering is **off**. Indices are taken as sequential 0–5 in the order BLACK, WHITE, YELLOW, RED, BLUE, GREEN and remapped to the panel's raw values (`[0,1,2,3,5,6]`). |
| `P` with no palette set | Gets the 7-entry pure-colour palette, then goes through the same quantise step. |

Two consequences for inky-dash:
- Draw UI in mode `P` with a 6-colour palette, and only send photos as RGB.
- In a `P` image, use **sequential indices 0–5**. The driver constants are raw panel values for `set_pixel` (`inky.BLUE` = 5, `inky.GREEN` = 6). Used as `P` indices, `inky.BLUE` shows green. Black, white, yellow and red happen to be the same in both numberings.

## Pi setup

1. Raspberry Pi OS Bookworm or later. Enable SSH and Wi-Fi in Raspberry Pi Imager.
2. `sudo raspi-config` → Interface Options → enable **SPI** and **I2C**. I2C is used to read the EEPROM for auto-detection.
3. `sudo apt update && sudo apt upgrade` (and `sudo apt install git` on Lite).
4. Pimoroni's installer (`git clone https://github.com/pimoroni/inky && cd inky && ./install.sh`) creates the venv `~/.virtualenvs/pimoroni`. inky-dash uses **its own venv** instead and installs `inky` from PyPI. You only need the Pimoroni installer to try their examples.
5. `ModuleNotFoundError: No module named 'inky'` → the venv isn't active.

## Minimal API

```python
from inky.auto import auto
from PIL import Image, ImageDraw

inky = auto()                      # auto(ask_user=True, verbose=True) to fall back to CLI args
print(inky.resolution)             # (800, 480)

img = Image.new("P", inky.resolution, inky.WHITE)   # or an RGB image of the same size
draw = ImageDraw.Draw(img)
draw.rectangle((50, 50, 200, 200), fill=inky.YELLOW)
draw.text((20, 20), "Hello", inky.BLACK)

inky.set_border(inky.WHITE)
inky.set_image(img, saturation=0.5)   # RGB images are quantised + dithered to the palette
inky.show()                           # blocks until the busy pin releases (tens of seconds)
```

From `examples/7color/image.py`, photos are resized to the panel and fed in. Some drivers don't accept `saturation`:

```python
image = Image.open(path).resize(inky.resolution)
try:
    inky.set_image(image, saturation=0.5)
except TypeError:
    inky.set_image(image)
inky.show()
```

## Buttons (from `examples/7color/buttons.py`)

BCM GPIO **5, 6, 16, 24** = buttons **A, B, C, D** (top to bottom). On the 13.3" model, C is GPIO 25. These are active-low with a pull-up, so watch for a falling edge.

```python
import gpiod, gpiodevice
from gpiod.line import Bias, Direction, Edge

BUTTONS = [5, 6, 16, 24]
LABELS = ["A", "B", "C", "D"]
INPUT = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.FALLING)
chip = gpiodevice.find_chip_by_platform()
OFFSETS = [chip.line_offset_from_id(i) for i in BUTTONS]
request = chip.request_lines(consumer="inky-dash-buttons", config=dict.fromkeys(OFFSETS, INPUT))

while True:
    for event in request.read_edge_events():   # blocking
        print(LABELS[OFFSETS.index(event.line_offset)])
```

Spectra models also have a shine-through **LED** on the back, on BCM GPIO **13** (`examples/spectra6/led.py`).

## Refresh and cleaning
- `show()` blocks while the panel refreshes. The driver waits up to 32 s for the refresh itself, plus setup and power on/off.
- Every refresh is a full refresh. There is no partial update.
- `examples/clean.py` is written for the Inky pHAT: 3 cycles of red, black and white, so 9 full refreshes (about 5 min here). It also fills the image pixel by pixel in Python, which is slow on a Zero. inky-dash uses its own clean routine with a configurable cycle count.

## Image tips (Pimoroni)
- There is no backlight, so images look darker and duller than on a monitor. Boost contrast and saturation before sending.
- Images with a limited palette of bright colours give the best results.
- For UI, draw with exact palette colours to avoid dithering on text.

## Useful upstream examples (in `reference/inky/examples/`)

| File | Shows |
|---|---|
| `7color/hello_world.py`, `spectra6/hello_world.py` | Text with a font |
| `7color/image.py`, `spectra6/image.py` | Photo display with saturation |
| `7color/advanced/dither.py` | Custom dithering |
| `7color/colour-palette.py`, `stripes.py` | Palette test patterns |
| `7color/graph.py` | Drawing a chart |
| `7color/buttons.py`, `spectra6/buttons.py` | GPIO buttons |
| `7color/clear.py`, `clean.py` | Clear and anti-ghosting |
| `7color/html/` | HTML → screenshot → Inky (needs `firefox-esr`) |
| `spectra6/comics/` | Fetch from a web API and display |
| `identify.py` | Prints the detected display variant |
| `inky/mock.py` | Tkinter simulator (only older Impression resolutions, so we use our own FileBackend) |
