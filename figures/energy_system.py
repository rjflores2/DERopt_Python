"""
DERopt Energy System Diagram
==============================
Shows all modeled technologies and the two energy carriers (electricity bus,
hydrogen bus) with directed flow connections.

Run:    python figures/energy_system.py
Needs:  pip install matplotlib
Output: figures/energy_system.pdf  (vector, for reports)
        figures/energy_system.png  (300 dpi raster)
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent

plt.rcParams.update({"font.family": "sans-serif", "font.size": 9})

# ── Canvas ────────────────────────────────────────────────────────────────────
FIG_W, FIG_H = 14.0, 9.5
DX,    DY    = 14.0, 9.5
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor("white")
ax.set_xlim(0, DX)
ax.set_ylim(0, DY)
ax.axis("off")

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    "solar":      "#F9A825",
    "hydro":      "#0277BD",
    "diesel":     "#6D4C41",
    "grid":       "#37474F",
    "battery":    "#6A1B9A",
    "flow_bat":   "#4527A0",
    "load":       "#B71C1C",
    "pem_elec":   "#00695C",
    "alk_elec":   "#2E7D32",
    "fuel_cell":  "#E65100",
    "h2_stor":    "#1565C0",
    "elec_bus":   "#D84315",
    "h2_bus":     "#1B5E20",
    "arrow":      "#37474F",
    "dashed":     "#78909C",
}

# ── Bus y-positions ───────────────────────────────────────────────────────────
Y_E = 6.05          # electricity bus
Y_H = 2.55          # hydrogen bus

# ── Drawing helpers ───────────────────────────────────────────────────────────

def bus(y, x0, x1, label, color, label_right=True, ax=ax):
    """Draw a thick colored horizontal bus bar with label."""
    ax.plot([x0, x1], [y, y], color=color, linewidth=7,
            solid_capstyle="round", zorder=2, alpha=0.85)
    ax.plot([x0, x1], [y, y], color="white", linewidth=2.5,
            solid_capstyle="round", zorder=3, alpha=0.35)
    lx = (x1 + 0.18) if label_right else (x0 - 0.18)
    ha = "left" if label_right else "right"
    ax.text(lx, y, label, ha=ha, va="center", fontsize=10.5,
            color=color, fontweight="bold", zorder=4)


def tech_box(x, y, w, h, label, color, sub=None, fs=8.5, ax=ax):
    """Filled rounded technology box."""
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.07",
        linewidth=1.5,
        edgecolor=color,
        facecolor=color,
        alpha=0.84,
        zorder=5,
    )
    ax.add_patch(rect)
    if sub:
        ax.text(x + w / 2, y + h * 0.65, label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=6)
        ax.text(x + w / 2, y + h * 0.28, sub,
                ha="center", va="center", fontsize=fs - 1.5,
                color="white", alpha=0.92, zorder=6)
    else:
        ax.text(x + w / 2, y + h / 2, label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=6,
                multialignment="center")


def flow_arrow(x1, y1, x2, y2, color, bidir=False, lw=1.8, dashed=False, ax=ax):
    """Arrow representing energy flow between a technology and a bus."""
    style = "<->" if bidir else "->"
    ls = (0, (4, 3)) if dashed else "solid"
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=style,
            color=color,
            lw=lw,
            mutation_scale=13,
            connectionstyle="arc3,rad=0",
            linestyle=ls,
        ),
        zorder=7,
    )


def flow_label(x, y, text, color, ax=ax):
    ax.text(x, y, text, ha="center", va="center", fontsize=7.0,
            color=color, style="italic", zorder=8,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))


# ── Bus bars ──────────────────────────────────────────────────────────────────
bus(Y_E, x0=0.35, x1=13.0, label="Electricity Bus", color=C["elec_bus"])
bus(Y_H, x0=2.20, x1=10.20, label="Hydrogen Bus  (kWh-H₂ LHV)", color=C["h2_bus"])

# ── Title ─────────────────────────────────────────────────────────────────────
ax.text(7.0, 9.35, "HydroFlex — Technology Model: Energy Flow Diagram",
        ha="center", va="top", fontsize=14, fontweight="bold", color="#1A1A1A")
ax.text(7.0, 9.00, "Electricity and hydrogen carriers; arrows show energy flow direction; "
        "bidirectional arrows indicate storage dispatch",
        ha="center", va="top", fontsize=9.0, color="#555", style="italic")

# ─────────────────────────────────────────────────────────────────────────────
# ABOVE ELECTRICITY BUS — Generation sources
#   Solar PV, Hydrokinetic, Diesel Generator, Grid/Utility Import
#   Battery ES, Flow Battery (bidirectional storage)
# ─────────────────────────────────────────────────────────────────────────────
BW = 1.65   # generation box width
BH = 1.30   # generation box height
BY = Y_E + 0.50     # bottom y of generation boxes

GEN_SPECS = [
    (0.40,  "Solar PV",            C["solar"],    None),
    (2.25,  "Hydrokinetic",        C["hydro"],    None),
    (4.10,  "Diesel\nGenerator",   C["diesel"],   None),
    (5.90,  "Grid / Utility\nImport",  C["grid"], None),
]
for (xl, lbl, col, sub) in GEN_SPECS:
    tech_box(xl, BY, BW, BH, lbl, col, sub=sub)
    cx = xl + BW / 2
    flow_arrow(cx, BY, cx, Y_E + 0.06, color=col, lw=2.0)

# Storage (bidirectional — same row)
STOR_SPECS = [
    (8.30,  "Battery\nEnergy Storage",       C["battery"]),
    (10.20, "Flow Battery\nEnergy Storage",  C["flow_bat"]),
]
for (xl, lbl, col) in STOR_SPECS:
    tech_box(xl, BY, BW, BH, lbl, col)
    cx = xl + BW / 2
    flow_arrow(cx, BY, cx, Y_E + 0.06, color=col, bidir=True, lw=2.0)

# Category annotations
ax.text(4.45, BY + BH + 0.14,
        "Generation (sources)",
        ha="center", va="bottom", fontsize=8.5, color="#444", style="italic")
ax.text(9.93, BY + BH + 0.14,
        "Electricity Storage",
        ha="center", va="bottom", fontsize=8.5, color="#444", style="italic")

# ─────────────────────────────────────────────────────────────────────────────
# ELECTRICITY DEMAND — below electricity bus, right side
# ─────────────────────────────────────────────────────────────────────────────
DEM_X, DEM_W, DEM_H = 11.50, 2.00, 0.95
DEM_Y = Y_E - 0.40 - DEM_H
tech_box(DEM_X, DEM_Y, DEM_W, DEM_H, "Electricity\nDemand", C["load"], fs=8.5)
# Arrow from bus down to demand box top
flow_arrow(DEM_X + DEM_W / 2, Y_E - 0.06,
           DEM_X + DEM_W / 2, DEM_Y + DEM_H,
           color=C["load"], lw=2.0)

# ─────────────────────────────────────────────────────────────────────────────
# CONVERTERS — between the two buses
#   PEM Electrolyzer:   elec → H₂
#   Alkaline Electrolyzer:  elec → H₂
#   PEM Fuel Cell:      H₂ → elec
# ─────────────────────────────────────────────────────────────────────────────
CW = 2.00
CH = 1.05
CY = (Y_E + Y_H) / 2 - CH / 2      # vertically centred between buses

CONV_SPECS = [
    (1.90,  "PEM\nElectrolyzer",      C["pem_elec"],  "elec→H₂"),
    (4.30,  "Alkaline\nElectrolyzer", C["alk_elec"],  "elec→H₂"),
    (6.70,  "PEM Fuel Cell",          C["fuel_cell"], "H₂→elec"),
]

for (xl, lbl, col, direction) in CONV_SPECS:
    tech_box(xl, CY, CW, CH, lbl, col, sub=direction)
    cx = xl + CW / 2
    if direction == "elec→H₂":
        # Draw from electricity bus down to box top, and from box bottom down to H2 bus
        flow_arrow(cx, Y_E - 0.06, cx, CY + CH, color=col, lw=1.7)
        flow_arrow(cx, CY, cx, Y_H + 0.06, color=col, lw=1.7)
    else:
        # H₂ → elec: arrows from H2 bus up to box bottom, and from box top up to electricity bus
        flow_arrow(cx, Y_H + 0.06, cx, CY, color=col, lw=1.7)
        flow_arrow(cx, CY + CH, cx, Y_E - 0.06, color=col, lw=1.7)

# ─────────────────────────────────────────────────────────────────────────────
# COMPRESSED-GAS HYDROGEN STORAGE — below hydrogen bus
# ─────────────────────────────────────────────────────────────────────────────
H2S_W = 3.00
H2S_H = 1.05
H2S_X = 5.50    # left edge; center at 7.0
H2S_Y = Y_H - 0.40 - H2S_H

tech_box(H2S_X, H2S_Y, H2S_W, H2S_H,
         "Compressed-Gas\nH₂ Storage", C["h2_stor"],
         sub="kWh-H₂ LHV inventory")

# Bidirectional arrow to H2 bus
flow_arrow(H2S_X + H2S_W / 2, Y_H - 0.06,
           H2S_X + H2S_W / 2, H2S_Y + H2S_H,
           color=C["h2_stor"], bidir=True, lw=2.0)

# Compressor electricity draw (dashed arrow from electricity bus to H2 storage)
comp_x = H2S_X + 0.35
flow_arrow(comp_x, Y_E - 0.06, comp_x, H2S_Y + H2S_H * 0.85,
           color=C["dashed"], lw=1.2, dashed=True)
flow_label(comp_x - 0.55, (Y_E + H2S_Y + H2S_H) / 2,
           "compressor\n(elec)", C["dashed"])

# ─────────────────────────────────────────────────────────────────────────────
# Legend — flow direction key
# ─────────────────────────────────────────────────────────────────────────────
LEG_X, LEG_Y = 0.35, 1.55
ax.text(LEG_X, LEG_Y + 0.55, "Flow key:", fontsize=8.0, color="#444",
        fontweight="bold", va="bottom")
# one-way source
ax.annotate("", xy=(LEG_X + 0.85, LEG_Y + 0.35), xytext=(LEG_X, LEG_Y + 0.35),
            arrowprops=dict(arrowstyle="->", color="#444", lw=1.5, mutation_scale=10))
ax.text(LEG_X + 0.95, LEG_Y + 0.35, "source / sink", fontsize=7.8, color="#444", va="center")
# bidirectional
ax.annotate("", xy=(LEG_X + 0.85, LEG_Y + 0.05), xytext=(LEG_X, LEG_Y + 0.05),
            arrowprops=dict(arrowstyle="<->", color="#444", lw=1.5, mutation_scale=10))
ax.text(LEG_X + 0.95, LEG_Y + 0.05, "bidirectional (storage)", fontsize=7.8, color="#444", va="center")
# dashed
ax.plot([LEG_X, LEG_X + 0.85], [LEG_Y - 0.25, LEG_Y - 0.25],
        color=C["dashed"], lw=1.5, linestyle=(0, (4, 3)))
ax.annotate("", xy=(LEG_X + 0.85, LEG_Y - 0.25), xytext=(LEG_X + 0.65, LEG_Y - 0.25),
            arrowprops=dict(arrowstyle="->", color=C["dashed"], lw=1.2, mutation_scale=8))
ax.text(LEG_X + 0.95, LEG_Y - 0.25, "auxiliary elec draw", fontsize=7.8,
        color=C["dashed"], va="center")

# ─────────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────────
plt.tight_layout(pad=0.2)
fig.savefig(OUT / "energy_system.pdf", bbox_inches="tight")
fig.savefig(OUT / "energy_system.png", dpi=300, bbox_inches="tight")
print("Saved:  figures/energy_system.pdf   figures/energy_system.png")
