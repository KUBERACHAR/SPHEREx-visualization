# Planet X and SPHEREx

## 1. High-Level Software Architecture
Your application needs to bridge heavy scientific data storage (NASA/IRSA archives containing FITS format files with 102 infrared bands) and a seamless, interactive browser experience for everyday users.

* Frontend (The Client): A responsive web application that renders interactive sky maps, handles coordinate inputs (Right Ascension and Declination), lets users adjust sliders (wavelength bands, field of view, date sweeps), and plays "time-lapse videos" of sky patches.

* Backend (The Processing Engine): A lightweight API service that accepts user coordinates and parameters, queries the SPHEREx data archives (using Python and Astropy), processes image cutouts, stacks or filters wavelength bands, and sends optimized image frames (PNG/JPEG or cutout data) back to the frontend.

* Visualization Engine: Integrates specialized web mapping tools like Aladin Lite (developed by CDS Strasbourg) or custom HTML5/WebGL canvas animation loops to handle fast image frame-switching for motion detection.

---

## 2. Recommended Technology Stack

| Tier | Technologies / Tools | Purpose |
| :--- | :--- | :--- |
| **Frontend Framework** | **React.js** (Tailwind CSS) | Fast UI development, state management for sliders, inputs, and dynamic layout. |
| **Sky Visualization** | **Aladin Lite API** (CDS) or **WebGL Canvas** | Built specifically for astronomy web applications to render sky surveys, pan, zoom, and overlay coordinates. |
| **Backend API Service** | **Python (FastAPI)** | High-performance asynchronous API framework ideal for handling data processing requests and scientific library integration. |
| **Astronomical Processing** | **Astropy**, **Astroquery**, **NumPy**, **SunPy / SciPy** | Parsing FITS files, coordinate transformations (`SkyCoord`), extracting image cutouts (`Cutout2D`), signal processing, and frame subtraction. |

---

## 3. Core Software Modules to Design
* A. Input & Coordinate Resolution Module
Feature: Accepts user input for coordinates (Right Ascension & Declination) or target names (e.g., Barnard’s Star, WISE 0855).

    * Implementation logic: Uses Astropy’s coordinate package to resolve text names into precise sky degrees and manages URL parameter routing (so users can share custom links with coordinates embedded in the URL).

* B. Data Retrieval & Processing Pipeline (The Backend Engine)
Feature: Interfaces with NASA/IRSA data repositories where SPHEREx multi-extension FITS (MEF) files are stored.

    * Implementation logic:
        * Query data overlapping the requested coordinates and date ranges.

        * Download or stream targeted image cutouts.

        * Band Management: Handle SPHEREx's 102 near-infrared bands. Allow users to select specific bands, sum detector arrays to improve signal-to-noise ratio, or subtract images taken at different epochs to instantly highlight moving objects (asteroids, comets, or brown dwarfs).

* C. Time-Lapse & Animation Player (The Frontend UI)
Feature: Displays sequential snapshots of a sky patch like a video player.

    * Implementation logic: A custom UI control bar with Play, Pause, Timeline scrubber, and Frame Rate (fps) adjustments. When the user hits play, it loops through chronological epochs of the same sky patch so fast-moving objects (like WISE 0855 or near-Earth asteroids) pop out visually.

* D. Advanced Bonus Features (To Stand Out)
On-the-Fly Spectra Generation: Allow users to click on a detected moving object in the image frame to plot its flux/brightness across the 102 infrared wavelengths.

    * Multi-Mission Overlays: Integrate web layers from Gaia or WISE missions to provide multi-wavelength context.

    * Metadata & Contamination Flags: Display warning flags or telemetry data alongside the image viewer.

---