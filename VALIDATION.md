# Validation against the mandatory challenge targets

Generated 2026-11-14 07:57 UTC by `tools/validate_targets.py` against the live NASA/IPAC SPHEREx archive (field of view 4.0′, default settings unless noted).

Display-pixel convention: x grows to the west, y grows to the south (north up, east left). Motion values are in arcsec on the sky, measured between the first and last epoch stacks with a flux-weighted centroid (SPHEREx pixels are 6.15″, so sub-pixel motions are close to the measurement limit).

| Target | Images found / used | Epochs | Data read | Time | Peak S/N at predicted position (per epoch) | Result |
|---|---|---|---|---|---|---|
| Barnard's Star | 358 / 160 | 3 | cache | 6 s | 93048, 112582, 87075 | ✅ detected in every epoch; moved 10.4″ (predicted 10.2″) |
| Epsilon Indi B | 460 / 160 | 4 | 3.9 MB | 23 s | 1821, 1808, 1327, 1390 | ✅ detected in every epoch; moved 6.3″ (predicted 6.5″) |
| WISE 0855 (D5+D6) | 258 / 93 | 3 | 2.1 MB | 14 s | 7, 29, 34 | ✅ detected in every epoch; moved 8.2″ (predicted 8.0″) |
| RW Aurigae | 335 / 160 | 3 | 2.8 MB | 23 s | 74004, 73659, 58780 | ✅ detected in every epoch; aperture flux varies ×1.51 between epochs |
| R Draconis | 539 / 160 | 3 | 3.9 MB | 27 s | 44335, 57069, 71179 | ✅ detected in every epoch; core saturated, so no aperture photometry; brightest unsaturated pixel varies ×1.23 between epochs (indicative only) |

## Per-epoch details

### Barnard's Star

Catalogue position (J2000) 269.452077 +4.693365, proper motion (-801.6, 10362.4) mas/yr; SPHEREx-epoch centre 269.446222 +4.768801.

| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |
|---|---|---|---|---|---|---|
| 1 | 60913.5 | 54 | (18.93, 19.94) | (18.80, 19.92) | 93047.8 | 5.24e+03 |
| 2 | 61117.1 | 54 | (19.00, 19.00) | (18.96, 18.99) | 112581.9 | 5.88e+03 |
| 3 | 61273.7 | 52 | (19.06, 18.28) | (19.17, 18.28) | 87075.2 | 6.34e+03 |

### Epsilon Indi B

Catalogue position (J2000) 331.044147 -56.782844, proper motion (3982.0, -2466.8) mas/yr; SPHEREx-epoch centre 331.096232 -56.800501.

| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |
|---|---|---|---|---|---|---|
| 1 | 60801.3 | 40 | (19.28, 18.82) | (19.17, 18.88) | 1821.5 | 25 |
| 2 | 60957.5 | 48 | (19.01, 19.00) | (19.03, 19.15) | 1807.5 | 21 |
| 3 | 61155.4 | 44 | (18.66, 19.21) | (18.57, 19.25) | 1326.6 | 23 |
| 4 | 61310.8 | 28 | (18.38, 19.38) | (18.38, 19.53) | 1389.7 | 28.1 |

> 1 epoch frame(s) lack some of those detectors (marked with a warning on the timeline), so their brightness is not directly comparable. For strict photometry select a single detector.

### WISE 0855 (D5+D6)

Catalogue position (J2000) 133.818908 -7.247048, proper motion (-8123.7, 673.2) mas/yr; SPHEREx-epoch centre 133.760061 -7.242206.

| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |
|---|---|---|---|---|---|---|
| 1 | 60801.1 | 11 | (18.30, 19.06) | (18.50, 18.73) | 7.4 | 0.396 |
| 2 | 60995.5 | 46 | (19.01, 19.00) | (19.25, 18.90) | 29.4 | 0.558 |
| 3 | 61161.2 | 36 | (19.61, 18.95) | (19.83, 18.76) | 34.0 | 0.491 |

### RW Aurigae

Catalogue position (J2000) 76.956526 +30.401438, proper motion (7.2, -20.3) mas/yr; SPHEREx-epoch centre 76.956587 +30.401290.

| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |
|---|---|---|---|---|---|---|
| 1 | 60927.7 | 54 | (19.00, 19.00) | (19.09, 19.01) | 74004.4 | 1.23e+03 |
| 2 | 61101.4 | 54 | (19.00, 19.00) | (19.08, 19.04) | 73659.1 | 1.06e+03 |
| 3 | 61294.1 | 52 | (19.00, 19.00) | (19.02, 19.03) | 58779.7 | 814 |

### R Draconis

Catalogue position (J2000) 248.167611 +66.754979, proper motion (-20.6, 6.1) mas/yr; SPHEREx-epoch centre 248.167234 +66.755024.

| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |
|---|---|---|---|---|---|---|
| 1 | 60842.1 | 54 | (19.00, 19.00) | (19.07, 19.13) | 44335.5 | nan |
| 2 | 61009.5 | 54 | (19.00, 19.00) | (18.97, 19.06) | 57068.6 | 3.47e+04 |
| 3 | 61205.7 | 52 | (19.00, 19.00) | (18.97, 19.05) | 71178.6 | nan |
