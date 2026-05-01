"""
DERopt Technology Block Interface and Aggregation Diagram
===========================================================
Shows the internal anatomy of a technology block (left) and how each block's
exposed interface terms are collected into the electricity balance, hydrogen
balance, and objective (right).

Run:    python figures/technology_block.py
Needs:  pip install matplotlib
Output: figures/technology_block.pdf   (vector)
        figures/technology_block.png   (300 dpi)
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent
plt.rcParams.update({"font.family": "sans-serif", "font.size": 9})

FIG_W, FIG_H = 15.5, 9.5
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor("white")
ax.set_xlim(0, 15.5)
ax.set_ylim(0,  9.5)
ax.axis("off")

# ── Palette ───────────────────────────────────────────────────────────────────
C_BLOCK = "#6A1B9A"   # block boundary / header
C_PARAM = "#4A148C"   # technoeconomic parameters
C_VAR   = "#1A237E"   # decision variables
C_CON   = "#01579B"   # constraints
C_ESRC  = "#E65100"   # electricity_source_term  (orange)
C_ESNK  = "#1565C0"   # electricity_sink_term    (blue)
C_HSRC  = "#006064"   # hydrogen_source_term     (dark teal)
C_HSNK  = "#2E7D32"   # hydrogen_sink_term       (dark green)
C_OBJ   = "#880E4F"   # objective_contribution   (plum/crimson)
C_AGG_E = "#BF360C"   # electricity balance box
C_AGG_H = "#1B5E20"   # hydrogen balance box
C_AGG_O = "#4A148C"   # objective box


# ── Helpers ───────────────────────────────────────────────────────────────────

def fbox(x, y, w, h, label, color, sub=None, fs=8.5, alpha=0.88):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.07",
        lw=1.4, edgecolor=color, facecolor=color, alpha=alpha, zorder=4))
    if sub:
        ax.text(x + w / 2, y + h * 0.66, label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=5, multialignment="center")
        ax.text(x + w / 2, y + h * 0.26, sub,
                ha="center", va="center", fontsize=fs - 1.8,
                color="white", alpha=0.92, zorder=5, multialignment="center")
    else:
        ax.text(x + w / 2, y + h / 2, label,
                ha="center", va="center", fontsize=fs,
                color="white", fontweight="bold", zorder=5, multialignment="center")


def down_arr(x, y_start, y_end, color, lw=1.3):
    ax.annotate("", xy=(x, y_end), xytext=(x, y_start),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw,
                                mutation_scale=9, connectionstyle="arc3,rad=0"), zorder=7)


def flow_arr(x1, y1, x2, y2, color, lw=1.5, rad=0.0):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw,
                                mutation_scale=10,
                                connectionstyle=f"arc3,rad={rad}"), zorder=8)


# ─────────────────────────────────────────────────────────────────────────────
# TITLE
# ─────────────────────────────────────────────────────────────────────────────
ax.text(7.75, 9.35,
        "DERopt — Technology Block Interface and Cost Aggregation",
        ha="center", va="top", fontsize=14, fontweight="bold", color="#1A1A1A")

# ─────────────────────────────────────────────────────────────────────────────
# LEFT PANEL — Generic Block Anatomy   x = [0.20, 5.45]
# ─────────────────────────────────────────────────────────────────────────────
PANEL_X, PANEL_W = 0.20, 5.25

# Outer boundary
ax.add_patch(FancyBboxPatch(
    (PANEL_X, 0.45), PANEL_W, 8.45, boxstyle="round,pad=0.12",
    lw=2.0, edgecolor=C_BLOCK, facecolor=C_BLOCK, alpha=0.07, zorder=1))
ax.text(PANEL_X + PANEL_W / 2, 8.77,
        "Technology Block  (Pyomo Block)",
        ha="center", va="top", fontsize=10.5,
        color=C_BLOCK, fontweight="bold", style="italic", zorder=2)

# ── Inputs ────────────────────────────────────────────────────────────────────
fbox(0.38, 7.55, 2.10, 1.08, "technology_\nparameters",
     C_PARAM, fs=8.0,
     sub="capital cost · O&M rate\nefficiency · capacity limits\nexisting assets · formulation")
fbox(2.62, 7.55, 2.65, 1.08, "financials",
     C_PARAM, fs=8.0,
     sub="debt / equity terms\namortization factor\n(annualizes adopted-capacity capex)")

down_arr(1.43, 7.55, 7.32, C_PARAM)
down_arr(3.95, 7.55, 7.32, C_PARAM)

# ── ① Technoeconomic Parameters ───────────────────────────────────────────────
fbox(0.38, 6.38, 4.90, 0.83, "①  Technoeconomic Parameters  (Pyomo Param)",
     C_PARAM, fs=8.2,
     sub="capital cost/kW(h)  ·  O&M rate  ·  round-trip efficiency  ·  "
         "C-rate limits  ·  existing capacity  ·  fuel cost  ·  big-M coefficients")
down_arr(2.83, 6.38, 6.16, C_VAR)

# ── ② Decision Variables ──────────────────────────────────────────────────────
fbox(0.38, 5.20, 4.90, 0.85, "②  Decision Variables  (Pyomo Var)",
     C_VAR, fs=8.2,
     sub="capacity_adopted[node]  ·  dispatch[node, t]  ·  "
         "SOC[node, t]  ·  commitment on/off[node, t]  (binary / integer in MILP)")
down_arr(2.83, 5.20, 4.97, C_CON)

# ── ③ Constraints ─────────────────────────────────────────────────────────────
fbox(0.38, 4.00, 4.90, 0.86, "③  Constraints  (Pyomo Constraint)",
     C_CON, fs=8.2,
     sub="energy / SOC dynamics  ·  C-rate / power bounds  ·  "
         "adoption limits  ·  efficiency linkage  ·  commitment logic")

# ── Separator ─────────────────────────────────────────────────────────────────
ax.plot([0.38, 5.28], [3.68, 3.68], color=C_BLOCK, lw=1.2,
        linestyle=(0, (5, 4)), zorder=3)
ax.text(2.83, 3.56, "Exposed Interface  (iterated by model/core.py)",
        ha="center", va="top", fontsize=8.5,
        color=C_BLOCK, fontweight="bold", style="italic", zorder=3)

# ── Interface term pills ───────────────────────────────────────────────────────
PILL_H  = 0.36
PILL_X  = 0.38
PILL_W  = 4.90
PILL_SPACING = 0.11

PILLS = [
    (C_ESRC, "electricity_source_term [node, t]",
     "generation, discharge power — feeds electricity balance sources      optional"),
    (C_ESNK, "electricity_sink_term [node, t]",
     "charging, electrolysis load — feeds electricity balance sinks         optional"),
    (C_HSRC, "hydrogen_source_term [node, t]",
     "H₂ production, storage release — feeds hydrogen balance sources      optional"),
    (C_HSNK, "hydrogen_sink_term [node, t]",
     "H₂ consumption, storage charge — feeds hydrogen balance sinks         optional"),
    (C_OBJ,  "objective_contribution",
     "annualized capital + O&M + fuel + energy/demand charges              required"),
]

y_pill = 3.26
for (col, lbl, note) in PILLS:
    fbox(PILL_X, y_pill, PILL_W, PILL_H, lbl, col, fs=7.8)
    ax.text(PILL_X + PILL_W + 0.10, y_pill + PILL_H / 2, note,
            ha="left", va="center", fontsize=6.3, color=col, zorder=5)
    y_pill -= (PILL_H + PILL_SPACING)

# ─────────────────────────────────────────────────────────────────────────────
# Vertical separator
# ─────────────────────────────────────────────────────────────────────────────
ax.plot([5.85, 5.85], [0.45, 9.0], color="#CCCCCC", lw=1.0,
        linestyle="--", zorder=1)

# ─────────────────────────────────────────────────────────────────────────────
# RIGHT PANEL — Instances + Core Aggregation
# ─────────────────────────────────────────────────────────────────────────────

# ── Aggregation boxes  x = [11.20, 15.20] ─────────────────────────────────────
AGG_X = 11.20
AGG_W =  4.00

# Electricity balance (top)
EL_Y0, EL_H = 5.65, 3.10
fbox(AGG_X, EL_Y0, AGG_W, EL_H, "Electricity Balance",
     C_AGG_E, fs=9.5,
     sub="∀ node n, time t :\n"
         "Σ electricity_source_term[n, t]\n"
         "= Σ electricity_sink_term[n, t]\n"
         "   +  electricity_load[n, t]")

# Hydrogen balance (middle)
HY_Y0, HY_H = 2.95, 2.40
fbox(AGG_X, HY_Y0, AGG_W, HY_H, "Hydrogen Balance",
     C_AGG_H, fs=9.5,
     sub="∀ node n, time t :\n"
         "Σ hydrogen_source_term[n, t]\n"
         "= Σ hydrogen_sink_term[n, t]")

# Objective (bottom)
OB_Y0, OB_H = 0.35, 2.30
fbox(AGG_X, OB_Y0, AGG_W, OB_H, "Objective  (minimize)",
     C_AGG_O, fs=9.5,
     sub="Σ  objective_contribution\n"
         "over all registered blocks\n\n"
         "= Σ ( annualized capital\n"
         "    + fixed & variable O&M\n"
         "    + fuel cost\n"
         "    + energy & demand charges )")

# Aggregation box centers (used as arrow targets)
EL_CY = EL_Y0 + EL_H / 2   # 7.20
HY_CY = HY_Y0 + HY_H / 2   # 4.15
OB_CY = OB_Y0 + OB_H / 2   # 1.50

# ── Technology instance column  x = [6.05, 9.65] ──────────────────────────────
INST_X  =  6.05
INST_W  =  3.60
INST_H  =  1.10
INST_GAP = 0.17

# Each entry: (display label, box color, [(term_color, agg_y_center), ...])
# Term order determines arrow offset within block center to reduce overlap.
TECHS = [
    ("Solar PV · Hydrokinetic\n· Diesel Generator",
     "#F57F17",
     [(C_ESRC, EL_CY), (C_OBJ, OB_CY)]),

    ("Battery ES\n· Flow Battery",
     "#6A1B9A",
     [(C_ESRC, EL_CY), (C_ESNK, EL_CY), (C_OBJ, OB_CY)]),

    ("Grid / Utility Block",
     "#37474F",
     [(C_ESRC, EL_CY), (C_OBJ, OB_CY)]),

    ("PEM Electrolyzer\n· Alkaline Electrolyzer",
     "#00695C",
     [(C_ESNK, EL_CY), (C_HSRC, HY_CY), (C_OBJ, OB_CY)]),

    ("PEM Fuel Cell",
     "#E65100",
     [(C_ESRC, EL_CY), (C_HSNK, HY_CY), (C_OBJ, OB_CY)]),

    ("Compressed-Gas H₂ Storage",
     "#1565C0",
     [(C_HSRC, HY_CY), (C_HSNK, HY_CY), (C_ESNK, EL_CY), (C_OBJ, OB_CY)]),
]

# Compute block bottom y-positions (stack from top)
Y_TOP = EL_Y0 + EL_H   # 8.75 — align with top of electricity balance box
block_bots = [Y_TOP - (i + 1) * INST_H - i * INST_GAP for i in range(len(TECHS))]

for i, ((lbl, col, terms), bot) in enumerate(zip(TECHS, block_bots)):
    center_y = bot + INST_H / 2
    fbox(INST_X, bot, INST_W, INST_H, lbl, col, fs=8.5)

    # Draw one colored flow arrow per interface term this block contributes.
    # Spread start y-positions within the block to reduce arrow overlap.
    n = len(terms)
    y_offsets = [(j - (n - 1) / 2) * 0.10 for j in range(n)]
    # Spread end y-positions at the agg box edge too (group by target).
    # Small extra offset so arrows to same agg box fan out slightly.
    for j, (t_col, agg_cy) in enumerate(terms):
        sy = center_y + y_offsets[j]
        # End y: spread arrows from the same block across the agg box face
        ey = agg_cy + (i - (len(TECHS) - 1) / 2) * 0.18 + y_offsets[j] * 0.4
        # Curvature: gentle S-curve helps separate overlapping paths
        rad = 0.08 * y_offsets[j]
        flow_arr(INST_X + INST_W, sy, AGG_X, ey, t_col, lw=1.5, rad=rad)

# ── "core.py loop" annotation ─────────────────────────────────────────────────
ax.text((INST_X + INST_W + AGG_X) / 2, Y_TOP + 0.08,
        "model/core.py iterates REGISTRY blocks\nand sums all exposed terms",
        ha="center", va="bottom", fontsize=7.8,
        color="#555555", style="italic",
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#CCCCCC", alpha=0.85))

# ── Color-key legend (bottom-right of right panel) ────────────────────────────
LEG_X  = 6.05
LEG_Y  = 0.12
KEY = [
    (C_ESRC, "electricity_source_term"),
    (C_ESNK, "electricity_sink_term"),
    (C_HSRC, "hydrogen_source_term"),
    (C_HSNK, "hydrogen_sink_term"),
    (C_OBJ,  "objective_contribution"),
]
ax.text(LEG_X, LEG_Y + 0.55, "Flow arrow color key:",
        fontsize=7.8, color="#444", fontweight="bold", va="bottom")
for k, (col, lbl) in enumerate(KEY):
    col_x = LEG_X + (k % 3) * 1.72
    row_y = LEG_Y + 0.35 - (k // 3) * 0.32
    ax.plot([col_x, col_x + 0.30], [row_y, row_y],
            color=col, lw=2.5, solid_capstyle="round")
    ax.text(col_x + 0.37, row_y, lbl,
            fontsize=7.2, color=col, va="center")

# ─────────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────────
plt.tight_layout(pad=0.2)
fig.savefig(OUT / "technology_block.pdf", bbox_inches="tight")
fig.savefig(OUT / "technology_block.png", dpi=300, bbox_inches="tight")
print("Saved:  figures/technology_block.pdf   figures/technology_block.png")
