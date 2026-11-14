# NASA Space Apps Challenge 2026: Planet X & SPHEREx Web Tool Architecture

## 1. Executive Summary & Challenge Overview
* **Challenge Title:** Planet X and SPHEREx
* **Mission Context:** NASA’s SPHEREx mission (launched 2025) maps the entire sky every six months across 102 near-infrared bands, capturing over a billion celestial objects.
* **Core Objective:** Build a public-facing web tool that accepts sky coordinates (Right Ascension and Declination), retrieves time-series sky patch cutouts, and displays them dynamically (e.g., as a time-lapse animation or interactive viewer) to help volunteers easily discover moving objects like asteroids, comets, brown dwarfs, and variable stars.

---

## 2. Mandatory Test Targets
Your tool must be validated and tested against the following astronomical test cases:
1. **Barnard’s Star:** High proper-motion star.
2. **Epsilon Indi B:** A pair of brown dwarfs orbiting a star.
3. **WISE 0855:** Fast-moving brown dwarf ($RA: 08^h 55^m 10.831^s, Dec: -07^\circ 14' 42.53''$).
4. **RW Aurigae:** Variable star system.
5. **R Draconis:** Pulsating variable star.

---

## 3. Recommended Technology Stack

| Architecture Layer | Recommended Tech / Tools | Primary Responsibility |
| :--- | :--- | :--- |
| **Frontend UI** | **React.js** or **Vue.js** (Tailwind CSS) | Interactive controls, dynamic sliders, timeline animation player, and responsive layout. |
| **Sky Visualization Engine** | **Aladin Lite API** (CDS) or **WebGL Canvas** | Rendering high-resolution sky maps, managing zoom/pan, coordinate overlays, and multi-epoch frames. |
| **Backend API Service** | **Python (FastAPI)** | High-speed API endpoints for handling coordinate inputs, background job dispatching, and sky image transformations. |
| **Astronomical Processing** | **Astropy**, **Astroquery**, **NumPy**, **SunPy / SciPy** | Parsing FITS files, coordinate transformations (`SkyCoord`), extracting image cutouts (`Cutout2D`), signal processing, and frame subtraction. |

---

## 4. Software Architecture & System Modules

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      User Interface                         │
 │  - RA/Dec Input & Target Search                             │
 │  - Wavelength/Band Sliders (102 SPHEREx Bands)             │
 │  - Time-Lapse Player (Play/Pause, FPS, Frame Scrubber)      │
 └──────────────────────────────┬──────────────────────────────┘
                                │ API Request (RA, Dec, FOV, Epochs, Bands)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                   Backend Processing Engine                 │
 │  - Coordinate Resolver (Astropy SkyCoord)                   │
 │  - Data Fetcher (NASA IRSA Archive / SPHEREx FITS Cutouts)  │
 │  - Image Processor (Band Stacking, Epoch Subtraction)      │
 └──────────────────────────────┬──────────────────────────────┘
                                │ Standardized Cutout Frames / JSON Metadata
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │               Astronomical Rendering Canvas                 │
 │  - Multi-Epoch Frame Overlay & Motion Animation             │
 │  - On-the-Fly Spectra Plotting                              │
 │  - Survey Overlay (Gaia, WISE, DSS Context Layers)          │
 └─────────────────────────────────────────────────────────────┘
```

### Module Breakdown

#### Module A: Coordinate Resolution & URL State Management
* **Function:** Accepts raw celestial coordinates (RA/Dec in degrees or sexagesimal) or common target names (e.g., "WISE 0855").
* **Key Feature:** Embeds parameters directly into the browser URL (e.g., `?ra=133.795&dec=-7.245&fov=0.1`). This allows easy sharing and embedding on external websites.

#### Module B: Data Pipeline & Band Processing (Backend)
* **Function:** Queries SPHEREx data repositories (IRSA) for specific sky patches across different time epochs.
* **Key Operations:**
  * **Band Stacking:** Sums image data from SPHEREx’s detector arrays to increase the signal-to-noise ratio (SNR) for faint objects.
  * **Image Subtraction:** Calculates epoch-difference images ($Epoch_2 - Epoch_1$) to cancel out stationary stars and instantly highlight moving or variable objects.

#### Module C: Interactive Time-Lapse & Motion Viewer (Frontend)
* **Function:** Renders sequential frames of a target patch of sky as a video loop.
* **Controls:**
  * Play / Pause toggle.
  * Frame rate control slider (1 fps – 30 fps).
  * Field of view (FOV) selector to trade off resolution for speed/bandwidth.

#### Module D: Advanced Features (Hackathon Differentiators)
* **On-the-Fly Spectra Generation:** Clicking any pixel/object in the view extracts and plots its intensity across all 102 near-infrared bands, aiding in brown dwarf or planet candidate identification.
* **Multi-Mission Context Layers:** Toggling layer overlays from external surveys (like Gaia or WISE) for cross-validation of moving targets.
* **Metadata & Contamination Flags:** Displays diagnostic data such as detector noise, cosmic ray flags, and background sky levels.

---

## 5. Step-by-Step Hackathon Roadmap

* **Phase 1: Environment Setup & Target Verification**
  * Initialize repository and project structure.
  * Write basic Python scripts using `astropy` and `astroquery` to retrieve cutouts for the mandatory test objects (e.g., *WISE 0855*).
* **Phase 2: Core Backend Engine**
  * Create FastAPI endpoints to return sky frames (PNG or raw arrays) based on incoming coordinates and epoch selections.
  * Implement image subtraction and band aggregation functions.
* **Phase 3: Interactive Viewer & UI**
  * Build the web interface using React/Vue and Tailwind CSS.
  * Integrate Aladin Lite or an HTML5 Canvas loop for frame playback and coordinate overlays.
* **Phase 4: Advanced Features & Optimization**
  * Add URL parameter state management.
  * Build the spectrum chart generator (using Chart.js or Plotly.js).
  * Test system responsiveness across various Field of View sizes.
* **Phase 5: Final Testing & Presentation**
  * Verify functionality against all 5 required test targets.
  * Deploy backend and frontend to live cloud hosting.
  * Record a 30-second demonstration video showing a moving object being detected using the tool.