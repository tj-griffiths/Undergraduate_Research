# Undergraduate Research

**Thomas Griffiths** — University of Washington

Two independent experimental and computational physics research projects
conducted as an undergraduate at the University of Washington, one in ion
trap quantum computing and one in astrophysical numerical methods.

---

## `Ion/` — Characterizing Vapor Pressure Profiles for BaMg Alloy

Ion Trap Research Group, University of Washington
Supervisor: Prof. Boris Blinov

**Published:** J. Gunnell, T. Griffiths, and B. B. Blinov, *"Barium Magnesium
Alloy as Source of Atomic Ba for Ion Trapping,"* [arXiv:2603.20956](https://arxiv.org/abs/2603.20956)
(2026).

Elemental barium is the preferred ion species for many trapped-ion quantum
computing platforms due to its long-lived metastable states and accessible
transition wavelengths. However, it is highly reactive in air — oxidizing
within minutes during apparatus assembly — which complicates ion trap
construction and ion loading protocols. This project characterizes a
barium-magnesium alloy (BaMg, 20% Ba / 80% Mg) as an air-stable alternative
barium source for resistively-heated oven loading.

Under high vacuum conditions ($< 10^{-6}$ mbar), vapor pressure profiles were
measured for both the BaMg alloy and a pure barium control using a Stanford
Research Systems RGA200 residual gas analyzer, with temperature monitored
at each heating step via a K-type thermocouple in differential configuration.

**Headline result:** Barium vapor onset was detected at 530–600°C for both
materials, with comparable peak partial pressures of $2$–$2.5 \times 10^{-7}$
Torr. The BaMg alloy produced equivalent vapor without requiring inert
atmosphere handling. Subsequent trapping experiments by the group successfully
loaded $^{138}\text{Ba}^+$ ions from a BaMg oven source, confirming viability as
a practical substitute. The full study is published on arXiv (above) and
also available in `Ion/BaMg_work.pdf`.

### Notebooks

- **`Thermocouple_Test.ipynb`** — Preliminary comparison of two thermocouple
  hot-junction placements to determine which gives a cleaner temperature
  reading during oven heating. Saves averaged temperature-vs-amperage profiles
  to `circ1.npy` / `circ2.npy`.
- **`RGA_Scans.ipynb`** — Main analysis: processes four RGA heating-cycle scans
  from `RGAData/` (three BaMg alloy runs, one pure barium control), compares
  against Honig–Kramer literature vapor pressure curves, and produces all
  figures from the paper.

### Repository layout

```
Ion/
    Thermocouple_Test.ipynb
    RGA_Scans.ipynb
    RGAData/
        rga2.txt    # BaMg alloy run 1
        rga3.txt    # BaMg alloy run 2
        rga4.txt    # BaMg alloy run 3
        rga5.txt    # Pure barium control
    Results/
        circ1.npy   # Circuit 1 averaged temperature profile
        circ2.npy   # Circuit 2 averaged temperature profile
    BaMg_report.pdf
```

**Website:** [UW Ion Trap Group](https://sites.google.com/view/iontrap/)

---

## `N-Body/` — Correction Methods to the Leapfrog Numerical Integrator

University of Washington Astronomy Department
Affiliated with the N-Body Shop group

The second-order leapfrog integrator is standard in astrophysical simulations
due to its symplectic nature — it conserves a shadow Hamiltonian exactly over
long integrations. However, this property breaks down when the force is
discontinuous, as occurs in self-interacting dark matter simulations where
particles experience abrupt force transitions at collision boundaries. The
standard integrator exhibits asymmetric drift at these crossings, causing
phase-space distortion that accumulates over multiple orbits.

Two correction methods are developed and tested, both triggered when a
boundary crossing is detected ($x_i \cdot x_{i+1} \leq 0$):

**Velocity correction** — solves for the crossing time
$t_c = -x_i/v_{i+1/2} + t_i$ and applies split velocity kicks:

$$v_b = v_i + \frac{-x_i}{v_{i+1/2}} \cdot F(x_i), \qquad
v_{i+1} = v_b + \left(\frac{x_i}{v_{i+1/2}} + dt\right) F(x_{i+1})$$

**Timestep correction** — splits the timestep at the crossing point:

$$\Delta t_1 = -x_i/v_{i+1}, \quad \Delta t_2 = dt - \Delta t_1, \qquad
v_{i+1} = v_i + F(x_i) \Delta t_1 + F(x_{i+1}) \Delta t_2$$

Both are tested against an absolute-value force ($F = -g\,\text{sign}(x)$,
true discontinuity) and an arctangent force
($F = -g\,\frac{2}{\pi}\arctan(x/x_s)$, smoothed approximation with
sharpness controlled by $x_s$). The full study is available in
`N-Body/Correction_Leapfrog.pdf`.

**Headline result:** Both corrections improve trajectory accuracy and
energy conservation for the discontinuous (absolute-value) force at practical
stepsizes. In the continuous regime ($x_s = 1$), however, both corrections
exhibit systematic energy drift — the boundary detection becomes ambiguous
for gradual force transitions and the corrections over-compensate. Future
work targets a symplectic correction that combines trajectory accuracy with
the long-term energy stability required for astrophysical simulations.

### Notebooks

- **`Harmonic_oscillator.ipynb`** — Leapfrog validation on the simple
  harmonic oscillator, where an exact analytic solution provides a true
  error ground truth. Confirms $O(h^2)$ convergence before applying the
  integrator to discontinuous forces. Extended to 2D Lissajous trajectories.
- **`Bouncing_ball.ipynb`** — Second test model: the standard uncorrected
  leapfrog applied to both force types, characterizing drift, convergence,
  error accumulation, and stability before the corrections are introduced.
- **`Corrections.ipynb`** — Main analysis notebook. Implements both
  correction methods and produces all three paper figures: energy conservation
  across a dt × $x_s$ parameter grid (Fig. 1), position/velocity convergence
  vs. stepsize (Fig. 2), and maximum energy error vs. stepsize (Fig. 3).

### Repository layout

```
N-Body/
    Harmonic_oscillator.ipynb
    Bouncing_ball.ipynb
    Corrections.ipynb
    Correction_Leapfrog.pdf
```

**Website:** [UW N-Body Shop](https://astro.washington.edu/n-body-shop)