# Third-party notices

Niibot Studio itself is released under the **MIT** licence (see [LICENSE](LICENSE)).
Everything it bundles is free for commercial and private use; the notices below are kept
for the components whose licences require attribution.

| Component | Used for | Licence | Home |
| --- | --- | --- | --- |
| Lucide (`web/vendor/lucide.min.js`) | UI icons + 1,861 insertable cliparts | ISC | https://lucide.dev |
| Material Design Icons (`web/vendor/mdi-icons.json`) | 7,447 insertable cliparts | Apache-2.0 | https://pictogrammers.com/library/mdi/ |
| Roboto / system fonts | rendered with the fonts installed on the machine | system licences | — |
| FastAPI, Starlette, Uvicorn | local HTTP server | MIT / BSD-3-Clause | https://fastapi.tiangolo.com |
| pydantic | data models | MIT | https://docs.pydantic.dev |
| Pillow | raster rendering | HPND (Python Imaging Library licence) | https://python-pillow.org |
| bleak | Bluetooth LE transport | MIT | https://github.com/hbldh/bleak |
| qrcode | QR code rendering | BSD-3-Clause | https://github.com/lincolnloop/python-qrcode |
| python-barcode | 1D barcode rendering | MIT | https://github.com/WhyNotHugo/python-barcode |
| pypdfium2 | PDF rasterising for the Windows queue | Apache-2.0 / BSD-3-Clause | https://github.com/pypdfium2-team/pypdfium2 |
| PyInstaller | standalone Windows build | GPL-2.0 **with bootloader exception** | https://pyinstaller.org |

## Notes

* The MIT licence of Niibot Studio does not relicense the components above — each keeps its
  own terms, all of which permit redistribution inside a bundled application.
* PyInstaller's exception explicitly allows packaging and distributing the resulting
  executable under any licence, including MIT.
* Icons are inserted into labels as rasterised PNGs; the Lucide and MDI licences allow this
  without restrictions, attribution is provided here and in the README.
* Fonts are **not** redistributed: the app indexes the fonts already installed on the user's
  machine and optionally reads `.ttf`/`.otf` files the user drops into `data/fonts`.
