# Spatial & Mapping Engine Plan — Unidentified RF Cross-Checking

## 1. Mathematical Algorithms & Spatial Proximity

### 1.1 Spatial Distance Estimation & Filtering
For a given link $(AP \leftrightarrow Peer)$ perturbed in area $A$:
1. **Direct Estimated Distance**:
   $$d_{person} = 10^{\frac{P_{tx} - RSSI_{person}}{10 \cdot n}}$$
   Match condition: $d_{person} \le \text{threshold}$ (default $3.5\text{ m}$) or $RSSI \ge -60\text{ dBm}$.

2. **Geometric 2D Grid Distance**:
   If AP coordinates $(x_{ap}, y_{ap})$ and person coordinates $(x_p, y_p)$ are known on floor $F$:
   $$D(p, AP) = \sqrt{(x_p - x_{ap})^2 + (y_p - y_{ap})^2}$$
   Match condition: $D(p, AP) \le R_{\text{match}}$ ($3.5\text{ m}$).

### 1.2 Wall-Bleed Spatial Arbitration
When link $L$ in area $A_1$ is disturbed, but no person is localized in $A_1$:
1. Search all persons localized on floor $F$.
2. For each person $p$ in adjacent area $A_2$:
   Compute physical Euclidean distance $D(p, AP_{L})$.
   If $D(p, AP_{L}) \le R_{\text{match}}$, attribute the disturbance to $p$ as wall-bleed.
   Suppresses false intruder alerts without corrupting $p$'s current area assignment.

### 1.3 Asynchronous Sliding Coincidence Window
To accommodate polling delays $\Delta t$:
$$t_{\text{disturb}} - \tau_{\text{coincidence}} \le t_{\text{person\_seen}} \le t_{\text{disturb}} + \tau_{\text{coincidence}}$$
where $\tau_{\text{coincidence}} = 15.0\text{ s}$.

### 1.4 State Machine & Classification Logic
For each area $A$:
- Let $M_A \in \{0, 1\}$ be active RF disturbance.
- Let $V_A = \{p \mid \text{Person } p \text{ localized in } A \text{ and home}\}$.
- Let $C_A \subseteq V_A$ be persons matching spatial proximity or coincidence window to the disturbed link(s).

Classification:
- If $M_A = 0$ and $|V_A| = 0$: `none`, `unidentified_motion = False`
- If $M_A = 0$ and $|V_A| > 0$: `verified`, `unidentified_motion = False`
- If $M_A = 1$ and $|C_A| > 0$:
  - If $|V_A| = |C_A|$: `verified`, `unidentified_motion = False`, trigger `p.corroborate_rf_motion()` for $p \in C_A$.
  - If $|V_A| > |C_A|$: `verified` (or `mixed` if distant occupants), `unidentified_motion = False`.
- If $M_A = 1$ and $|C_A| = 0$:
  - If wall-bleed person $p_{wb}$ found: `verified`, `unidentified_motion = False`, trigger `p_{wb}.corroborate_rf_motion()`.
  - Else: `unidentified` (or `mixed` if $|V_A| > 0$ but all distant), `unidentified_motion = True`.
