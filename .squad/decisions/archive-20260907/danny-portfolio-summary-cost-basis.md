# Contrato de implementación: Resumen de cartera con base de coste ajustada

**Fecha:** 2026-09-06
**Autor:** Danny (Lead Architect)
**Estado:** PROPOSED — contrato de implementación pendiente de aprobación
**Impacto:** Backend `holdings_service.py`, modelos, frontend `PortfolioHoldingsTable.tsx`, tipos TS, tests

---

## 1. Problema actual

El campo `current_invested_eur` se calcula como:

```
current_invested_eur = total_invested_eur − total_sales_eur
```

Donde `total_sales_eur` es el **ingreso neto de venta** (gross − comisiones).
Esto mezcla dos conceptos incompatibles: _base de coste acumulada_ y _flujo de caja por ventas_.

El usuario pide que **current invested refleje la base de coste remanente** tras eliminar el **coste de adquisición de las acciones vendidas**, no los ingresos de venta.

---

## 2. Método de base de coste: FIFO (First In, First Out)

### Decisión: FIFO

Se usa **FIFO** (_First In, First Out_) para determinar qué lotes de compra se consumen al vender acciones. El precio medio mostrado (`avg_cost_basis_eur`) es la **media ponderada de los lotes FIFO restantes en cartera**, no un promedio simple sobre todas las compras históricas.

**Justificación:**
- FIFO es el método más intuitivo para el usuario: "vendo primero lo que compré primero".
- Es determinista dado un orden cronológico de movimientos.
- Produce un `avg_cost_basis_eur` que refleja el coste real de las acciones que **aún se poseen**, no de todas las que se compraron.
- FIFO es el método por defecto de la normativa fiscal española (art. 37.2 LIRPF) y la mayoría de brokers europeos, aunque este cálculo **no aplica la regla anti-lavado de 2 meses** y por tanto **no sirve para declaración fiscal directamente**.

**Limitaciones explícitas (DEBEN documentarse en tooltip):**
- No aplica la regla anti-lavado de 2 meses (art. 33.5.f LIRPF). Para fines fiscales, consultar asesor.
- Requiere ordenación cronológica estricta por `trade_date` (y por `id` como desempate).
- Si existen adquisiciones INCOMPLETE (coste cero), se añaden como lotes de coste 0 a la cola FIFO; al consumirse, su coste asignado es 0.
- El inventario negativo (venta antes de compra) genera warning y se asigna coste 0 al exceso.

### Algoritmo FIFO por security_id

```
Estado por security_id:
  lots = []              # cola FIFO de (remaining_qty, cost_per_share)
  unpaid_shares = 0      # acciones sin coste conocido (BUY INCOMPLETE)
  cost_basis_sold = 0    # acumulador de coste asignado a ventas

Para cada movimiento (ordenado por trade_date ASC, luego por id ASC):

  BUY (COMPLETE):
    cost_per_share = (gross_eur + commission_eur) / qty
    lots.append( (qty, cost_per_share) )

  BUY (INCOMPLETE):
    lots.append( (qty, 0) )     # lote con coste 0
    unpaid_shares += qty

  SELL ACCIONES:
    remaining_to_sell = qty
    sale_cost = 0
    while remaining_to_sell > 0 AND lots no vacío:
      lot = lots[0]
      consumed = min(remaining_to_sell, lot.remaining_qty)
      sale_cost += consumed × lot.cost_per_share
      lot.remaining_qty -= consumed
      remaining_to_sell -= consumed
      if lot.remaining_qty == 0:
        lots.pop(0)           # lote agotado
    # Si remaining_to_sell > 0 → inventario negativo, coste 0
    cost_basis_sold += sale_cost
    total_shares -= qty

  SELL DERECHOS:
    No afecta lots, ni total_shares, ni cost_basis_sold.
    Solo genera ingresos.

  TRANSFER_IN:
    Si carried_cost > 0 AND qty > 0:
      cost_per_share = carried_cost / qty
      lots.append( (qty, cost_per_share) )

  TRANSFER_OUT:
    # Consume lotes FIFO igual que una venta, pero no genera proceeds
    remaining_to_remove = qty
    cost_removed = 0
    while remaining_to_remove > 0 AND lots no vacío:
      lot = lots[0]
      consumed = min(remaining_to_remove, lot.remaining_qty)
      cost_removed += consumed × lot.cost_per_share
      lot.remaining_qty -= consumed
      remaining_to_remove -= consumed
      if lot.remaining_qty == 0:
        lots.pop(0)
    total_shares -= qty
    # cost_removed se traslada al TRANSFER_IN del destino

  DIVIDEND:
    Solo acumula dividendos netos. No afecta lotes ni coste.

Campos derivados:
  remaining_cost_basis = Σ (lot.remaining_qty × lot.cost_per_share) para cada lot en lots
  avg_cost_basis = remaining_cost_basis / Σ lot.remaining_qty   (null si no quedan lotes)
```

---

## 3. Campos y fórmulas exactas

### 3.1 Campos del summary (portfolio-wide)

| Campo API | Etiqueta ES | Fórmula | Notas |
|---|---|---|---|
| `total_purchase_outflow_eur` | **Total compras** | Σ (gross + comisión) de todos los BUY COMPLETE | Flujo de caja bruto de compra; excluye TRANSFER_IN y BUY INCOMPLETE |
| `cost_basis_sold_eur` | **Coste vendido** | Σ cost_sold por cada SELL ACCIONES (FIFO) | Base de coste asignada a acciones vendidas según FIFO |
| `remaining_cost_basis_eur` | **Inversión actual** | Σ (lot.remaining_qty × lot.cost_per_share) sobre todos los lotes restantes | = coste total de los lotes FIFO aún en cartera |
| `total_sale_proceeds_eur` | **Ingresos por ventas** | Σ (gross − comisión) de SELL ACCIONES + SELL DERECHOS | Flujo de caja neto recibido por ventas |
| `rights_proceeds_eur` | **Ingresos por derechos** | Σ (gross − comisión) de SELL DERECHOS | Subconjunto de total_sale_proceeds; opcional, desglose informativo |
| `realized_result_eur` | **Resultado realizado** | `total_sale_proceeds_eur − cost_basis_sold_eur + rights_proceeds_eur` | Ganancia/pérdida cerrada. Los derechos aportan ingreso sin consumir coste |
| `total_dividends_eur` | **Dividendos netos** | Σ net_eur de DIVIDEND | Sin cambio respecto al actual |
| `total_securities` | **Valores** | count(holdings) | Sin cambio |
| `has_incomplete_cost_basis` | _(warning flag)_ | `true` si algún security tiene `unpaid_shares > 0` | Señal para mostrar aviso global |

**Nota sobre `realized_result_eur`:** Los ingresos por derechos (DERECHOS) se incluyen como ganancia realizada porque generan flujo de caja sin reducir la posición en acciones. No tienen coste de adquisición asignado (no consumen acciones del pool).

### 3.2 Campos per-holding (HoldingItem)

| Campo API | Fórmula |
|---|---|
| `total_purchase_outflow_eur` | Σ (gross + comisión) BUY COMPLETE para este security |
| `cost_basis_sold_eur` | Σ coste FIFO asignado a SELL ACCIONES |
| `remaining_cost_basis_eur` | Σ (lot.remaining_qty × lot.cost_per_share) lotes restantes |
| `avg_cost_basis_eur` | `remaining_cost_basis / Σ lot.remaining_qty` (null si no quedan lotes con acciones) |
| `total_sale_proceeds_eur` | Σ (gross − comisión) SELL (ACCIONES + DERECHOS) |
| `rights_proceeds_eur` | Σ (gross − comisión) SELL DERECHOS |
| `realized_result_eur` | `total_sale_proceeds_eur − cost_basis_sold_eur` |
| `total_dividends_eur` | Sin cambio |
| `cost_basis_status` | `"INCOMPLETE"` si unpaid_shares > 0, `"COMPLETE"` otherwise |

---

## 4. Comportamiento por tipo de movimiento

| Movimiento | Afecta lotes FIFO | Afecta total_shares | Afecta total_sale_proceeds | Afecta cost_basis_sold |
|---|---|---|---|---|
| BUY (COMPLETE) | +lote(qty, cost/share) | +qty | — | — |
| BUY (INCOMPLETE) | +lote(qty, 0) | +qty | — | — |
| SELL ACCIONES | consume lotes FIFO | −qty | +(gross−comisión) | +coste FIFO consumido |
| SELL DERECHOS | — | — | +(gross−comisión) | — |
| TRANSFER_IN | +lote(qty, carried/share) | +qty | — | — |
| TRANSFER_OUT | consume lotes FIFO | −qty | — | — |
| DIVIDEND | — | — | — | — |
| Soft-delete | Excluido completamente | | | |
| SUPERSEDED (correction) | Excluido completamente (el replacement lo sustituye) | | | |
| Inventario negativo | Warning; lotes vacíos, exceso se vende a coste 0 | | | |

### 4.1 Correcciones y borrados

- Los movimientos con `deleted_at` se excluyen del cómputo (ya implementado).
- Los movimientos `correction_status == "SUPERSEDED"` se excluyen (ya implementado por `get_all_movements_for_holdings()`).
- El movimiento replacement (ACTIVE) se procesa normalmente.

### 4.2 Inventario negativo

Si en el momento de una SELL ACCIONES no quedan lotes FIFO suficientes:
- Se consumen todos los lotes disponibles (extrayendo su coste).
- Las acciones restantes (`qty − Σ lot.remaining_qty`) se asignan **coste 0**.
- Se emite warning `NEGATIVE_INVENTORY` como ya existe.
- El avg_cost queda como media de los lotes restantes (que pueden ser cero → null).

---

## 5. Comisiones: dentro del coste y fuera de los ingresos

| Concepto | Tratamiento |
|---|---|
| Comisión de compra | **Incluida** en pool_cost (gross + comisión = coste total de adquisición) |
| Comisión de venta | **Deducida** de sale_proceeds (gross − comisión = ingreso neto) |
| Comisión de transferencia | **No** afecta pool_cost ni sale_proceeds; es gasto operativo separado |

Esto es consistente con el diseño actual y con la convención contable estándar.

---

## 6. Global vs per-security vs per-account

### 6.1 Agregación actual (se mantiene)

- Holdings se agregan **por security_id** globalmente (cross-account).
- El filtro por `account_id` restringe movimientos antes del cómputo.
- **Los summary totals son la suma de todos los per-holding values.**

### 6.2 Transferencias entre cuentas

- `TRANSFER_OUT` en cuenta origen: consume lotes FIFO del origen (extrae coste proporcional).
- `TRANSFER_IN` en cuenta destino: añade un nuevo lote con el carried_cost.
- **Cuando se computan holdings sin filtro de cuenta** (global), TRANSFER_IN y TRANSFER_OUT se cancelan mutuamente en el mismo security. El coste global no cambia.
- **Cuando se filtra por cuenta**, las transferencias sí modifican los lotes de esa cuenta.
- Los transfers **nunca** aparecen en purchase_outflow ni sale_proceeds.

---

## 7. Frontend: jerarquía de tarjetas y etiquetas

### 7.1 Summary bar — nueva estructura

```
┌──────────────────────────────────────────────────────────────┐
│  Inversión actual     Resultado realizado    Dividendos      │
│  €48,230.15           +€3,412.00  ▲          €1,245.80       │
│                                                              │
│  ── desglose ──                                              │
│  Total compras        Coste vendido      Ingresos ventas     │
│  €62,500.00           €14,269.85         €17,681.85          │
│                                 (inc. derechos: €890.00)     │
│                                                              │
│  Valores: 12          ⚠ 2 valores con coste incompleto       │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Etiquetas y tooltips

| Campo | Etiqueta corta | Tooltip |
|---|---|---|
| `remaining_cost_basis_eur` | **Inversión actual** | "Base de coste de las acciones que aún posees (coste de los lotes restantes tras aplicar FIFO a las ventas)." |
| `realized_result_eur` | **Resultado realizado** | "Ganancia o pérdida cerrada: ingresos por ventas de acciones y derechos menos el coste FIFO de las acciones vendidas. No válido para fines fiscales (no aplica regla anti-lavado)." |
| `total_dividends_eur` | **Dividendos netos** | "Dividendos netos recibidos (tras retenciones)." |
| `total_purchase_outflow_eur` | **Total compras** | "Desembolso total en compras de acciones (principal + comisiones)." |
| `cost_basis_sold_eur` | **Coste vendido** | "Coste de adquisición asignado a las acciones vendidas (método FIFO: primero comprado, primero vendido)." |
| `total_sale_proceeds_eur` | **Ingresos ventas** | "Dinero recibido por ventas de acciones y derechos (bruto − comisiones)." |
| `rights_proceeds_eur` | **Inc. derechos** | "Parte de ingresos procedente de venta de derechos de suscripción." |

### 7.3 Jerarquía visual

1. **Fila principal (grande, siempre visible):** Inversión actual · Resultado realizado · Dividendos netos
2. **Fila secundaria (más pequeña, desglose):** Total compras · Coste vendido · Ingresos ventas (con nota de derechos si > 0)
3. **Indicadores:** Valores · Warning global si hay coste incompleto
4. El color verde/rojo en Resultado realizado según signo (positivo = verde, negativo = rojo).
5. **Inversión actual** ya NO puede ser negativa por diseño (remaining_cost ≥ 0 siempre que los lotes no estén vacíos). Eliminar la lógica de `currentInvestedNegative` del frontend.

---

## 8. Estrategia de campos API — compatibilidad hacia atrás

### 8.1 Campos nuevos (AÑADIR)

| Campo | Nivel | Descripción |
|---|---|---|
| `total_purchase_outflow_eur` | summary + holding | Flujo de caja de compra |
| `cost_basis_sold_eur` | summary + holding | Coste FIFO asignado a vendidas |
| `remaining_cost_basis_eur` | summary + holding | Base de coste remanente |
| `total_sale_proceeds_eur` | summary + holding | Ingresos netos por ventas |
| `rights_proceeds_eur` | summary + holding | Desglose derechos |
| `realized_result_eur` | summary + holding | Resultado realizado |
| `has_incomplete_cost_basis` | summary | Flag global de coste incompleto |

### 8.2 Campos existentes — redefinición y aliases

| Campo existente | Acción | Valor post-cambio |
|---|---|---|
| `total_purchases_eur` | **MANTENER como alias** de `total_purchase_outflow_eur` | Mismo valor que antes (sin cambio de rotura) |
| `total_sales_eur` | **MANTENER como alias** de `total_sale_proceeds_eur` | Mismo valor que antes |
| `total_invested_eur` (summary) | **MANTENER como alias** de `total_purchase_outflow_eur` | Mismo valor que antes |
| `total_invested_eur` (holding) | **MANTENER como alias** de `total_purchase_outflow_eur` | Mismo valor que antes |
| `current_invested_eur` | **REDEFINIR** → `remaining_cost_basis_eur` | ⚠️ **BREAKING CHANGE**: antes era `purchases − sale_proceeds`, ahora es coste FIFO restante. Valores distintos |
| `avg_cost_basis_eur` | **REDEFINIR** → media ponderada de lotes FIFO restantes | Antes era promedio simple sobre todas las compras; ahora refleja solo las acciones en cartera |

### 8.3 Estrategia de migración del breaking change

`current_invested_eur` cambia de semántica. Opciones:

**Opción recomendada:** Redefinir `current_invested_eur` directamente con la nueva fórmula. No hay consumidores externos; el frontend se actualiza en el mismo PR. Los tests se actualizan con los nuevos valores esperados. Se documenta el cambio en el PR.

Los aliases antiguos (`total_purchases_eur`, `total_sales_eur`, `total_invested_eur`) siguen devolviendo exactamente el mismo valor numérico que antes, por lo que no son breaking.

---

## 9. Matriz de aceptación (tests)

### 9.1 Tests unitarios — nuevos escenarios

| # | Escenario | Movimientos | Aserciones clave |
|---|---|---|---|
| S1 | Solo compra | BUY 100@€10 (€5 fee) | `remaining_cost = 1005`, `cost_sold = 0`, `realized = 0`, `avg = 10.05` |
| S2 | Compra + venta parcial (1 lote) | BUY 100@€10 (€5 fee) → SELL 30@€15 (€3 fee) | Lote: cost/share=10.05. FIFO consume 30 del lote. `cost_sold = 30×10.05 = 301.50`, `remaining = 70×10.05 = 703.50`, `sale_proceeds = 450−3 = 447`, `realized = 447 − 301.50 = 145.50`, `avg = 10.05` |
| S3 | Compra + venta total | BUY 100@€10 (€0 fee) → SELL 100@€15 (€0 fee) | `remaining = 0`, `cost_sold = 1000`, `realized = 1500 − 1000 = 500`, `avg = null` |
| S4 | Dos compras a distinto precio + venta FIFO | BUY 100@€10 (€0), BUY 50@€20 (€0) → SELL 60@€18 (€0) | Lotes: [100@€10, 50@€20]. FIFO consume 60 del primer lote. `cost_sold = 60×10 = 600`. Lotes restantes: [40@€10, 50@€20]. `remaining = 400+1000 = 1400`. `avg = 1400/90 = 15.56`. `sale_proceeds = 1080`, `realized = 1080−600 = 480` |
| S5 | Solo DERECHOS | BUY 100@€10 → SELL DERECHOS 20@€5 | `remaining = 1000` (sin cambio), `cost_sold = 0`, `rights_proceeds = 100`, `sale_proceeds = 100`, `realized = 100`, `total_shares = 100` |
| S6 | ACCIONES + DERECHOS | BUY 100@€10 → SELL 30 ACCIONES@€15 → SELL DERECHOS 10@€5 | FIFO: `cost_sold = 30×10 = 300`, `remaining = 700`, `sale_proceeds = 450+50 = 500`, `rights = 50`, `realized = 500 − 300 = 200` |
| S7 | BUY INCOMPLETE + venta | BUY 50@€0 (INCOMPLETE) + BUY 50@€10 → SELL 70@€15 | Lotes FIFO: [50@€0, 50@€10]. Venta 70: consume 50@€0 (coste 0) + 20@€10 (coste 200). `cost_sold = 200`. Lote restante: [30@€10]. `remaining = 300`. Warning INCOMPLETE |
| S8 | Transfer preserva base | BUY 100@€10 acct-A → TRANSFER_OUT 40 acct-A → TRANSFER_IN 40 acct-B (carried=400) | Global: `remaining = 1000` (sin cambio). Filtro acct-A: 600 (60 acciones restantes del lote original). Filtro acct-B: 400 |
| S9 | Inventario negativo | SELL 30@€15 (sin compra previa) | `cost_sold = 0`, `remaining = 0`, `sale_proceeds = 450`. Warning NEGATIVE_INVENTORY |
| S10 | Multi-security aggregation | AAPL: BUY+SELL. TEF: BUY only | Summary = sum de ambos remaining, cost_sold, etc. |
| S11 | Backward compat aliases | BUY 100@€10 (€5 fee) | `total_purchases_eur == total_purchase_outflow_eur`, `total_sales_eur == total_sale_proceeds_eur`, `total_invested_eur == total_purchase_outflow_eur` |
| S12 | FIFO consume lote más antiguo | BUY 100@€10, BUY 100@€20 → SELL 50@€25 | FIFO consume 50 del primer lote (100@€10). `cost_sold = 50×10 = 500`. Lotes restantes: [50@€10, 100@€20]. `remaining = 500+2000 = 2500`. `avg = 2500/150 = 16.67` |
| S13 | Venta total deja avg null | BUY 100@€10 → SELL 100@€12 | Lotes vacíos, `avg_cost_basis_eur = null`, `remaining = 0` |
| S14 | Soft-delete excluida | BUY + BUY(deleted) + SELL | Solo BUY activo genera lote |
| S15 | Correction (SUPERSEDED) | BUY(SUPERSEDED) + BUY(replacement) + SELL | Solo replacement genera lote |
| S16 | Tres compras escalonadas + venta cruzando lotes | BUY 50@€8, BUY 50@€12, BUY 50@€16 → SELL 80@€20 | FIFO consume 50@€8 + 30@€12. `cost_sold = 400+360 = 760`. Lotes: [20@€12, 50@€16]. `remaining = 240+800 = 1040`. `avg = 1040/70 = 14.86` |

### 9.2 Tests a actualizar

Todos los tests existentes en `TestSummaryTotals` que verifican `current_invested_eur` deben actualizarse:

- `test_purchases_and_sales`: antes `665.00` (purchases − sale_proceeds), ahora debe reflejar coste FIFO restante
- `test_sales_exceed_purchases`: antes `-300.00`, ahora `remaining = 0.00` (lotes FIFO agotados)
- `test_multi_security_aggregation`: recalcular con FIFO

Tests de `avg_cost_basis_eur` que asumen promedio simple sobre todas las compras:
- `test_avg_cost_basis_independent_of_sells`: antes avg no cambiaba con ventas → ahora sí cambia con FIFO (el lote más antiguo se consume)

### 9.3 Tests de regresión que NO deben cambiar

- Share counts (ACCIONES decrementa, DERECHOS no)
- Dividend accumulation
- Transfer share/cost preservation
- Warning generation
- Account filtering
- `total_purchases_eur` / `total_sales_eur` / `total_invested_eur` (mismos valores numéricos)

---

## 10. Orden de implementación sugerido

1. **Backend: algoritmo FIFO** en `holdings_service.py` — cola de lotes `(remaining_qty, cost_per_share)` por security, iteración ordenada por `trade_date` + `id`.
2. **Backend: nuevos campos** en respuesta y modelo Pydantic.
3. **Tests: nuevos S1–S15** + actualización de tests existentes.
4. **Frontend: tipos TS** — añadir nuevos campos a `HoldingsSummary` y `HoldingEntry`.
5. **Frontend: summary bar** — nueva jerarquía visual con etiquetas en español.
6. **Frontend: tabla** — actualizar columna "Invested" para mostrar `remaining_cost_basis_eur`.

---

## 11. Fuera de alcance (explícito)

- **Regla anti-lavado fiscal.** FIFO aquí no aplica la regla de 2 meses (art. 33.5.f LIRPF). Para declaración fiscal, consultar asesor.
- **Precio de mercado actual / P&L no realizado.** Requiere integración con proveedor de precios (futura).
- **Dividendos en acciones (scrip/DRIP).** El modelo los soporta como BUY con coste, que generará un lote FIFO normal.
- **Coste de transferencia como gasto.** Las comisiones de transferencia no se asignan a ningún campo del resumen en esta iteración.
