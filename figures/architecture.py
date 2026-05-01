"""
DERopt Software Architecture Diagram
=====================================
Run:    python figures/architecture.py
Needs:  pip install matplotlib
Output: figures/architecture.pdf  (vector, for reports)
        figures/architecture.png  (300 dpi raster)
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent

plt.rcParams.update({"font.family": "sans-serif", "font.size": 9})

# ── Canvas ────────────────────────────────────────────────────────────────────
FIG_W, FIG_H = 10.0, 11.5          # inches
DX,    DY    = 10.0, 11.5          # data-space dimensions (1 unit ≈ 1 inch)
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor("white")
ax.set_xlim(0, DX)
ax.set_ylim(0, DY)
ax.axis("off")

# ── Palette ───────────────────────────────────────────────────────────────────
C_INPUT   = "#1565C0"   # deep blue
C_LOADING = "#2E7D32"   # dark green
C_MODEL   = "#6A1B9A"   # purple
C_TECH    = "#7B1FA2"   # medium purple
C_UTILITY = "#0D47A1"   # navy
C_SOLVER  = "#BF360C"   # deep orange-red
C_DC      = "#1B5E20"   # very dark green
C_ARROW   = "#455A64"


# ── Drawing helpers ───────────────────────────────────────────────────────────

def layer_bg(x, y, w, h, label, color, ax=ax):
    """Labeled translucent background rectangle for a pipeline layer."""
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.12",
        linewidth=1.8,
        edgecolor=color,
        facecolor=color,
        alpha=0.09,
        zorder=1,
    )
    ax.add_patch(rect)
    ax.text(x + 0.18, y + h - 0.18, label,
            ha="left", va="top", fontsize=9.5,
            color=color, fontweight="bold", style="italic", zorder=2)


def box(x, y, w, h, top_label, color, sub=None, fs=8.5, ax=ax):
    """Filled rounded box with white label (and optional smaller sub-label)."""
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.08",
        linewidth=1.2,
        edgecolor=color,
        facecolor=color,
        alpha=0.88,
        zorder=3,
    )
    ax.add_patch(rect)
    if sub:
        ax.text(x + w / 2, y + h * 0.64, top_label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=4)
        ax.text(x + w / 2, y + h * 0.28, sub,
                ha="center", va="center", fontsize=fs - 1.8,
                color="white", alpha=0.92, zorder=4, multialignment="center")
    else:
        ax.text(x + w / 2, y + h / 2, top_label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=4, multialignment="center")


def arrow(x, y_start, y_end, label="", ax=ax):
    """Vertical down-arrow between layers with an optional side label."""
    ax.annotate(
        "", xy=(x, y_end), xytext=(x, y_start),
        arrowprops=dict(
            arrowstyle="->", color=C_ARROW,
            lw=2.0, mutation_scale=14,
            connectionstyle="arc3,rad=0",
        ),
        zorder=8,
    )
    if label:
        ax.text(x + 0.18, (y_start + y_end) / 2, label,
                ha="left", va="center", fontsize=8.0, color=C_ARROW)


def mini_arrow(x, y_start, y_end, ax=ax):
    """Short connector arrow inside a layer (loader → DataContainer)."""
    ax.annotate(
        "", xy=(x, y_end), xytext=(x, y_start),
        arrowprops=dict(
            arrowstyle="->", color=C_ARROW,
            lw=1.2, mutation_scale=10,
            connectionstyle="arc3,rad=0",
        ),
        zorder=6,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Title
# ─────────────────────────────────────────────────────────────────────────────
ax.text(5.0, 11.25, "DERopt — Software Architecture",
        ha="center", va="top", fontsize=15, fontweight="bold", color="#1A1A1A")
ax.text(5.0, 10.90, "Pyomo optimization model for DER selection and sizing  ·  Gurobi solver",
        ha="center", va="top", fontsize=9.5, color="#555555", style="italic")

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — Configuration & Inputs    y = [8.8, 10.55]
# ─────────────────────────────────────────────────────────────────────────────
L1y, L1h = 8.80, 1.55
layer_bg(0.25, L1y, 9.50, L1h, "Configuration & Inputs", C_INPUT)

box(0.45, L1y + 0.28, 2.60, L1h - 0.50,
    "Data Files", C_INPUT, fs=9.0,
    sub="CSV / XLSX\nload · solar · hydro\nOpenEI utility rates")

box(3.25, L1y + 0.28, 6.30, L1h - 0.50,
    "CaseConfig", C_INPUT, fs=9.0,
    sub="energy_load · solar_path · hydrokinetic_path · utility_mode\n"
        "utility_tariffs · technology_parameters · financials · time_subset")

# ─────────────────────────────────────────────────────────────────────────────
# Arrow: Config → Data Loading
# ─────────────────────────────────────────────────────────────────────────────
arrow(5.0, L1y, L1y - 0.45, label="case config + file paths")

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — Data Loading    y = [5.90, 8.35]
# ─────────────────────────────────────────────────────────────────────────────
L2y, L2h = 5.90, 2.45
layer_bg(0.25, L2y, 9.50, L2h, "Data Loading   (run/build_run_data.py)", C_LOADING)

LDR_W = 2.15
LDR_H = 0.90
LDR_GAP = 0.10
ldr_y = L2y + 1.38     # bottom of loader boxes

loaders = [
    "Energy Load\nLoader",
    "Solar Resource\nLoader",
    "Hydro Resource\nLoader",
    "Utility Rate Loader\n(OpenEI / raw CSV)",
]
for i, lbl in enumerate(loaders):
    lx = 0.45 + i * (LDR_W + LDR_GAP)
    box(lx, ldr_y, LDR_W, LDR_H, lbl, C_LOADING, fs=8.0)
    cx = lx + LDR_W / 2
    mini_arrow(cx, ldr_y, ldr_y - 0.32)     # → DataContainer

DC_y = L2y + 0.28
DC_h = 0.72
box(0.45, DC_y, 9.05, DC_h,
    "DataContainer   ·   indices   ·   timeseries (kWh per timestep)   ·   "
    "static metadata   ·   import_prices_by_node   ·   utility_rate_by_node",
    C_DC, fs=8.2)

# ─────────────────────────────────────────────────────────────────────────────
# Arrow: Data Loading → Model Assembly
# ─────────────────────────────────────────────────────────────────────────────
arrow(5.0, L2y, L2y - 0.42, label="DataContainer")

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — Model Assembly    y = [1.60, 5.48]
# ─────────────────────────────────────────────────────────────────────────────
L3y, L3h = 1.60, 3.88
layer_bg(0.25, L3y, 9.50, L3h, "Model Assembly   (model/core.py  +  technologies/)", C_MODEL)

# core.py — top of layer
CORE_y = L3y + L3h - 0.80
box(0.45, CORE_y, 9.05, 0.58,
    "model/core.py   ·   Sets: T (time steps), NODES   ·   "
    "Electricity Balance   ·   Hydrogen Balance (kWh-H₂ LHV)   ·   "
    "Objective: minimize total annual cost",
    C_MODEL, fs=8.2)

# Registry header label
REG_y = CORE_y - 0.28
ax.text(4.975, REG_y, "Technology REGISTRY  (opt-in via technology_parameters)",
        ha="center", va="top", fontsize=8.2, color=C_TECH, style="italic")

# Technology boxes — two rows
TECH_H = 0.80
TECH_GAP = 0.09

ROW1 = ["solar_pv", "battery\nstorage", "flow\nbattery", "diesel\ngenerator", "hydrokinetic"]
ROW1_W = (9.05 - TECH_GAP * (len(ROW1) - 1)) / len(ROW1)
row1_y = REG_y - 0.28 - TECH_H
for i, lbl in enumerate(ROW1):
    box(0.45 + i * (ROW1_W + TECH_GAP), row1_y, ROW1_W, TECH_H, lbl, C_TECH, fs=7.8)

ROW2 = ["PEM\nelectrolyzer", "alkaline\nelectrolyzer", "PEM\nfuel cell", "H₂\nstorage"]
ROW2_W = (9.05 - TECH_GAP * (len(ROW2) - 1)) / len(ROW2)
row2_y = row1_y - TECH_H - 0.12
for i, lbl in enumerate(ROW2):
    box(0.45 + i * (ROW2_W + TECH_GAP), row2_y, ROW2_W, TECH_H, lbl, C_TECH, fs=7.8)

# Block interface note
ax.text(4.975, row2_y - 0.14,
        "Each block exposes:  electricity_source/sink_term  ·  "
        "hydrogen_source/sink_term  ·  objective_contribution",
        ha="center", va="top", fontsize=7.5, color=C_TECH, style="italic")

# Utility block — bottom of layer
UTIL_y = L3y + 0.25
box(0.45, UTIL_y, 9.05, 0.60,
    "Utility Block   (utilities/electricity_import_export)   ·   "
    "grid import variable   ·   energy cost ($/kWh)   ·   "
    "demand charges   ·   fixed customer charges",
    C_UTILITY, fs=8.0)

# ─────────────────────────────────────────────────────────────────────────────
# Arrow: Model Assembly → Solve & Results
# ─────────────────────────────────────────────────────────────────────────────
arrow(5.0, L3y, L3y - 0.40, label="Pyomo ConcreteModel")

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4 — Solve & Results    y = [0.20, 1.20]
# ─────────────────────────────────────────────────────────────────────────────
L4y, L4h = 0.20, 1.00
layer_bg(0.25, L4y, 9.50, L4h, "Solve & Results", C_SOLVER)

box(0.45, L4y + 0.18, 2.10, L4h - 0.35,
    "Gurobi\n(LP / MILP)", C_SOLVER, fs=9.2)

box(2.75, L4y + 0.18, 6.75, L4h - 0.35,
    "extract_solution   ·   cost decomposition by technology   ·   "
    "equipment diagnostics   ·   optional CSV export",
    C_SOLVER, fs=8.2)

# ─────────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────────
plt.tight_layout(pad=0.2)
fig.savefig(OUT / "architecture.pdf", bbox_inches="tight")
fig.savefig(OUT / "architecture.png", dpi=300, bbox_inches="tight")
print("Saved:  figures/architecture.pdf   figures/architecture.png")
