# Diseño: copia de seguridad y restauración de datos de usuario

**Fecha:** 2026-09-19  
**Autor:** Danny  
**Solicitado por:** dsanchor  
**Estado:** ACEPTADO PARA IMPLEMENTACIÓN POR FASES — diseño únicamente

## 1. Decisión ejecutiva

La nueva área **Configuración → Exportar / Importar** debe proteger los datos
creados o curados por el usuario y las referencias imprescindibles para
restaurarlos. No debe ser un volcado indiscriminado de Cosmos DB.

La copia recomendada incluye:

- cuentas;
- Security Master y metadatos manuales de símbolos;
- configuración/watchlist de símbolos;
- posiciones de opciones almacenadas, incluidas las manuales y paper;
- todos los movimientos del ledger, con historial completo de correcciones,
  reasignaciones, transferencias y acciones corporativas;
- planes de acción;
- configuración funcional de agentes, estrategias, scheduler y
  notificaciones, sin credenciales.

Las posiciones bursátiles/holdings no se exportan como objetos independientes:
se reconstruyen desde el ledger. Las posiciones de opciones embebidas en
`symbol_config.positions` sí se exportan porque contienen estado manual que el
ledger no puede reconstruir de forma completa.

El formato será un **ZIP versionado y autocontenido** con manifiesto JSON,
ficheros JSON por sección y checksums SHA-256. Los secretos, tokens, cachés,
logs, trazas y resultados generados se excluyen.

---

## 2. Fuentes verificadas

- Contenedores Cosmos y particiones:
  `backend/src/cosmos_db.py:35-146`,
  `backend/scripts/provision_cosmosdb.sh`.
- Familias del contenedor `symbols`:
  `backend/src/cosmos_db.py`,
  `backend/src/portfolio/cosmos_securities.py`,
  `backend/src/portfolio/symbol_config_sync.py`,
  `backend/src/options_chain_store.py`.
- Ledger, cuentas, correcciones, transferencias y acciones corporativas:
  `backend/src/portfolio/cosmos_portfolio.py`.
- Sesiones y documentos producidos por importación:
  `backend/src/portfolio/import_service.py`.
- Holdings calculados:
  `backend/src/portfolio/holdings_service.py`.
- Configuración persistida:
  `backend/src/cosmos_db.py:1894-2029`,
  `backend/web/app.py:6401-6815`,
  `backend/web/app.py:7221-7564`,
  `backend/config.yaml`.
- UI y contrato de posiciones manuales/paper:
  `frontend/src/components/AddPositionForm.tsx`,
  `frontend/src/types/symbol-detail.ts`,
  `frontend/src/types/portfolio.ts`.
- Contratos previos:
  `.squad/designs/portfolio-ledger-securities-unified-design.md`,
  `.squad/designs/option-movements-design.md`,
  `.squad/designs/fiscal-reports-design.md`.

---

## 3. Inventario real de persistencia

Las categorías usadas son:

- **AUTHORITATIVE_USER_DATA**: información introducida, corregida o curada por
  el usuario; perderla cambia su cartera o sus decisiones.
- **REQUIRED_REFERENCE_DATA**: identidad o metadatos necesarios para interpretar
  y restaurar datos autoritativos.
- **DERIVED_REBUILDABLE**: calculado desde datos autoritativos o recuperable
  desde proveedores.
- **EPHEMERAL_RUNTIME**: ejecución, observabilidad, caché o sesión temporal.

### 3.1 Contenedor `symbols` — partición `/symbol`

| Familia | Clasificación | Exportación | Motivo/regla |
|---|---|---:|---|
| `security_master` | REQUIRED_REFERENCE_DATA | Sí | Identidad canónica `MIC:TICKER`, nombre, MIC, divisa, ISIN/CUSIP/SEDOL, aliases, broker IDs y `provider_symbols`. Es referencia obligatoria y parte puede haber sido creada/corregida manualmente. |
| `symbol_config` — identidad, `display_name`, watchlist, Telegram por símbolo, pausa | AUTHORITATIVE_USER_DATA | Sí | Selecciones y configuración curadas por el usuario. |
| `symbol_config.positions` | AUTHORITATIVE_USER_DATA | Sí | Posiciones de opciones manuales o creadas deliberadamente desde una actividad; contienen lifecycle, notas, premium legado, links de roll y `is_paper`. |
| `symbol_config.enrichment` | DERIVED_REBUILDABLE | No | Datos de mercado/análisis regenerables. |
| `symbol_config.pricing_cache` | DERIVED_REBUILDABLE | No | Caché de precio regenerable. |
| `symbol_config.total_shares` | DERIVED_REBUILDABLE/legado | No | Las tenencias reales se calculan desde el ledger; no restaurar un contador potencialmente obsoleto. |
| marcadores `_auto_enrolled*` | EPHEMERAL_RUNTIME | No | Proveniencia operativa de inscripción; se recalcula o se omite. |
| `action_plan` | AUTHORITATIVE_USER_DATA | Sí | Título, objetivo, tipo, estado, prioridad y condiciones son curación del usuario. |
| `action_plan.agent_notes` | DERIVED_REBUILDABLE con valor histórico | No por defecto | Salida generada por agentes. Puede ofrecerse en una opción avanzada “incluir notas generadas”, fuera del preset recomendado. |
| `activity` | EPHEMERAL_RUNTIME | No | Actividad de agentes/alertas, incluso si `is_alert=true`. |
| `alert` legado | EPHEMERAL_RUNTIME | No | Documento legado de ejecución; no es configuración de notificación. |
| `position_snapshot` | DERIVED_REBUILDABLE | No | Observación generada por monitores. |
| `enrichment_history` | DERIVED_REBUILDABLE | No | Serie temporal calculada y con retención. |
| `price_forecast` | DERIVED_REBUILDABLE | No | Proyección generada; no confundir con posición manual/paper. |
| `options_chain` | DERIVED_REBUILDABLE | No | Caché de mercado por vencimiento. |
| `report` | DERIVED_REBUILDABLE | No | Informe generado por agente. |
| `technical_analysis` | DERIVED_REBUILDABLE | No | Análisis generado por agente. |
| `banner` | EPHEMERAL_RUNTIME | No | Resumen operativo del dashboard. |

### 3.2 Contenedor `portfolio` — partición `/account_id`

| Familia | Clasificación | Exportación | Motivo/regla |
|---|---|---:|---|
| `account` | AUTHORITATIVE_USER_DATA | Sí | Broker, nombre, divisa, descripción y estado de borrado. |
| `ledger_txn` | AUTHORITATIVE_USER_DATA | Sí, completo | Fuente autoritativa de BUY, SELL, DIVIDEND, opciones, transferencias y acciones corporativas. |

Para `ledger_txn`, “completo” incluye también documentos `SUPERSEDED`, `VOIDED`
o soft-deleted cuando sigan almacenados, porque son parte de la cadena de
auditoría. Se conservan:

- `corrects_movement_id`, `superseded_by`;
- `reassigned_from` y links de reasignación;
- pares/grupos de transferencia;
- `ca_group_id`, `ca_leg_type`, `ca_event_type`, `ca_group_seq`,
  `replaces_ca_group_id`, `superseded_by_ca_group_id`;
- `option_position_id` y metadatos del enlace;
- `cost_basis_status`;
- `fx.rate` y `fx.rate_source`, incluidas correcciones `MANUAL` o `BROKER`;
- withholding de origen/destino;
- `import_source`, `batch_id`, `session_id`, `idempotency_hash`,
  `source_row_index` y `source_row`.

`source_row` se considera proveniencia autoritativa del movimiento importado.
Se incluye en la copia completa, aunque el manifiesto debe advertir que puede
contener información financiera procedente del fichero del broker.

No se han verificado familias persistidas independientes para holdings,
posiciones bursátiles, correcciones FX, ajustes de coste o auditorías: esas
semánticas viven en campos y enlaces de `ledger_txn`.

### 3.3 Contenedor `settings` — partición `/id`

| Documento | Clasificación | Exportación | Motivo/regla |
|---|---|---:|---|
| `app-config` | AUTHORITATIVE_USER_DATA, filtrado | Sí | Scheduler, parámetros de agentes/estrategias, listas DGI, modelos/proveedores por función y preferencias funcionales. |
| `tv-health` | EPHEMERAL_RUNTIME | No | Estado operativo del proveedor, errores y timestamps. |

Del `app-config` se exporta una **allowlist** de configuración funcional, no el
documento bruto. Incluye, cuando existan:

- toggles y cron de scheduler;
- `summary_agent`, `plan_monitor`, `banner_agent`, `dps_scorer`;
- `options_chain_scheduler`, `portfolio_enrichment`, `symbol_pricing`,
  `price_forecast`, `best_options_scheduler`;
- parámetros curados de `dgi_screener`;
- `ai_function_overrides` de provider/model;
- `context` y parámetros funcionales no secretos;
- `telegram.enabled`, pero no sus credenciales.

Se excluyen siempre `azure`, `gemini.api_key`, `cosmosdb`, claves API,
endpoints privados, tokens, passwords, connection strings y cualquier clave
que coincida con la denylist de secretos.

### 3.4 Otros contenedores

| Contenedor/familia | Clasificación | Exportación |
|---|---|---:|
| `import_sessions` / `import_session` | EPHEMERAL_RUNTIME | No |
| `telemetry` / métricas | EPHEMERAL_RUNTIME | No |
| `agent_traces` / `agent_trace` | EPHEMERAL_RUNTIME | No |
| `calendar` / earnings y ex-dividend descargados | DERIVED_REBUILDABLE | No |
| `dgi_screener` / `dgi_top` | DERIVED_REBUILDABLE | No |
| `dgi_screener` / `daily_snapshot` | DERIVED_REBUILDABLE | No |

No existe en el repositorio una entidad de **override manual de calendario**.
Si se añade en el futuro, deberá clasificarse como AUTHORITATIVE_USER_DATA y
no mezclarse con los eventos descargados.

---

## 4. Posiciones: qué se exporta y qué se reconstruye

### 4.1 Holdings/posiciones de acciones

No existe un documento autoritativo de holdings. `holdings_service.py` calcula
acciones, coste, dividendos, warnings y resultados a partir de `ledger_txn`.

**Decisión:** no exportar snapshots ni DTOs de holdings. Restaurar el ledger
completo y reconstruirlos. Tras la importación se ejecuta reconciliación y se
comparan totales con los declarados en el manifiesto.

### 4.2 Posiciones de opciones reales

Las posiciones de opciones viven embebidas en `symbol_config.positions`.
Aunque los importes económicos deben proceder del ledger de opciones, el
ledger no puede reconstruir por sí solo de forma completa:

- `position_id`;
- estado `active`, `closed` o `rolled`;
- `opened_at`, `closed_at`, `close_reason`;
- `rolled_from` / `rolled_to`;
- notas;
- datos legacy de `source`;
- `is_paper`.

Además, el enlace `ledger_txn.option_position_id` es opcional y deliberadamente
manual. Por tanto:

**Decisión:** exportar las posiciones estructurales de opciones y sus IDs
estables, junto con el ledger y sus enlaces.

### 4.3 Paper positions y proyecciones

- Una posición con `is_paper=true` es una simulación creada explícitamente por
  el usuario: se exporta como **AUTHORITATIVE_USER_DATA**, etiquetada
  `position_kind: "paper"`.
- Una posición sin `is_paper` se exporta como `position_kind: "real"`.
- `price_forecast`, `position_snapshot`, enrichment y análisis técnicos son
  proyecciones/observaciones generadas: se excluyen.

El preset recomendado incluye paper positions porque son trabajo manual; la UI
custom permite excluirlas.

---

## 5. Qué faltaba en la lista inicial del usuario

### Debe guardarse, verificado en el repositorio

1. **Cuentas**: necesarias para restaurar particiones y referencias del ledger.
2. **Security Master**: identidad MIC:TICKER, metadatos y overrides de proveedor.
3. **Watchlist y configuración por símbolo**: agentes seguidos, notificación
   Telegram por símbolo y pausas configuradas.
4. **Posiciones de opciones manuales y paper**: no reconstruibles completamente
   desde movimientos.
5. **Planes de acción**: contenido y estado curados por el usuario.
6. **Configuración funcional global**: scheduler, agentes, estrategias,
   parámetros DGI/forecast y selección provider/model.
7. **Configuración de notificaciones sin secretos**: enabled global y por
   símbolo. El único canal verificado es Telegram.
8. **Cadena íntegra de auditoría del ledger**: correcciones, voids,
   reasignaciones, transferencias y acciones corporativas agrupadas.
9. **Correcciones FX manuales/broker**: como parte de cada movimiento.
10. **Completitud de coste**: `cost_basis_status` en movimientos BUY.
11. **Proveniencia del import**: IDs/hash/source row asociados a movimientos.

### No existe como entidad independiente

- watchlists nombradas o múltiples;
- múltiples canales de notificación;
- overrides manuales de calendario;
- tabla independiente de correcciones FX;
- configuración global separada de completitud de coste;
- documentos independientes de auditoría/corrección de cartera.

No deben inventarse secciones vacías para esos conceptos. El esquema podrá
añadirlas en versiones futuras.

### Debe excluirse

- actividades, alertas ejecutadas, trazas y telemetría;
- sesiones de importación, preguntas, previews y archivos temporales;
- options chain y demás cachés;
- enrichment, historial de enrichment y pricing cache;
- forecasts, snapshots, reportes, análisis técnicos y banner;
- resultados y snapshots DGI;
- calendario descargado de proveedores;
- salud del proveedor y timestamps “last run”;
- claves, tokens, chat IDs, endpoints/keys de Cosmos y credenciales AI.

---

## 6. Formato del archivo

### 6.1 Elección

Usar **ZIP**, no un único JSON:

- permite streaming y límites por sección;
- permite validar checksums antes de tocar Cosmos;
- evita cargar todo el backup en memoria;
- facilita evolución de esquema y diagnóstico;
- separa manifiesto, datos y reporte sin perder portabilidad.

Extensión recomendada: `.oil-backup.zip`.

### 6.2 Estructura

```text
option-income-lab-backup-20260919T071558Z.oil-backup.zip
├── manifest.json
├── data/
│   ├── accounts.json
│   ├── securities.json
│   ├── symbol-configs.json
│   ├── option-positions.json
│   ├── ledger-movements.json
│   ├── action-plans.json
│   └── app-settings.json
└── checksums.sha256
```

Cada fichero de datos usa un envelope:

```json
{
  "section": "ledger_movements",
  "schema_version": 1,
  "count": 42,
  "records": []
}
```

### 6.3 Manifiesto mínimo

```json
{
  "format": "option-income-lab-user-backup",
  "archive_version": 1,
  "schema_version": 1,
  "export_id": "uuid",
  "exported_at": "2026-09-19T07:15:58Z",
  "app": {
    "version": "0.1.0",
    "git_commit": "optional-build-commit"
  },
  "scope": {
    "preset": "recommended_full_user_backup",
    "sections": ["accounts", "securities", "symbol_configs"],
    "filters": {}
  },
  "sections": [
    {
      "name": "accounts",
      "path": "data/accounts.json",
      "schema_version": 1,
      "count": 3,
      "sha256": "..."
    }
  ],
  "relationships": {
    "identity": "security_id",
    "preserve_ids": true
  },
  "redactions": {
    "policy_version": 1,
    "excluded_paths": [
      "settings.telegram.bot_token",
      "settings.telegram.chat_id",
      "settings.azure",
      "settings.gemini.api_key",
      "settings.cosmosdb"
    ],
    "secrets_included": false
  }
}
```

`app.version` usa la versión declarada disponible (`frontend/package.json`
actualmente declara `0.1.0`) y `git_commit` debe proceder del build/deployment,
no de una lectura arbitraria en tiempo de importación.

### 6.4 Canonicalización y checksum

- UTF-8, JSON canónico para checksum: claves ordenadas, separadores compactos,
  timestamps UTC y decimales conservados como strings.
- SHA-256 por fichero y checksum adicional del propio manifiesto en
  `checksums.sha256`.
- No exportar metadatos Cosmos (`_rid`, `_self`, `_etag`, `_attachments`,
  `_ts`) ni TTL operativo.
- Conservar IDs de dominio y referencias; no conservar ETags.
- El importador rechaza paths inesperados, duplicados ZIP, path traversal,
  ficheros no declarados o límites excedidos.

### 6.5 Redacción

La exportación nunca incluye secretos, ni siquiera en el modo custom:

- Telegram bot token y chat ID;
- API keys de Azure/Gemini;
- endpoint/key/database credentials de Cosmos;
- secretos encontrados por nombre (`token`, `secret`, `password`, `api_key`,
  `connection_string`, `credential`) salvo allowlist explícita futura.

El archivo sigue conteniendo datos financieros sensibles. La primera versión
no promete cifrado; la UI debe mostrar una advertencia clara y recomendar
almacenamiento seguro. Cifrado con contraseña puede ser una fase posterior.

---

## 7. UI de Exportar

Nueva navegación: **Configuración → Exportar** y
**Configuración → Importar**, separadas de la importación CSV de cartera.

### 7.1 Presets

#### A. Copia completa recomendada

Seleccionado por defecto:

- cuentas;
- todos los movimientos y su auditoría;
- Security Master;
- symbol configs/watchlist;
- posiciones de opciones reales y paper;
- planes de acción;
- configuración funcional sin secretos.

Excluye siempre datos generados/runtime.

#### B. Solo cartera

- cuentas;
- ledger completo;
- Security Master alcanzable;
- symbol configs mínimos para los símbolos alcanzables;
- posiciones de opciones referenciadas por el ledger;
- cierre de dependencias y cadenas.

No incluye configuración global ni planes no relacionados.

#### C. Solo símbolos y configuración

- Security Master;
- symbol configs/watchlist;
- posiciones manuales/paper;
- planes de acción;
- opcionalmente configuración global funcional.

No incluye movimientos salvo que el usuario active “incluir movimientos
enlazados”; si no, los links de posición se mantienen, pero el manifiesto
declara que la cobertura económica no está incluida.

#### D. Personalizada

Checklist por secciones, con dependencias añadidas automáticamente y visibles:
“Se añadirán 3 cuentas y 12 securities requeridas”.

### 7.2 Reglas de cierre de dependencias

La UI no permite producir un archivo que el importador no pueda restaurar:

1. Un `ledger_txn` obliga a incluir su `account` y `security_master`.
2. Una posición obliga a incluir su `symbol_config` y `security_master`.
3. Un action plan obliga a incluir el símbolo/config/referencia.
4. Un movimiento con `option_position_id` obliga a incluir esa posición, si
   existe. Si la referencia ya está rota en origen, se exporta la incidencia
   como warning del manifiesto.
5. Una corrección/reasignación obliga a incluir la cadena completa, en ambas
   direcciones.
6. Una transferencia obliga a incluir ambos legs y ambas cuentas.
7. Un `ca_group_id` obliga a incluir todas sus legs y toda su cadena de
   corrección.
8. Si se selecciona un subconjunto por símbolo/cuenta/fecha, el cierre puede
   ampliar el rango para mantener cadenas completas; nunca recorta una cadena.
9. Settings son independientes y no fuerzan datos de cartera.

Antes de descargar, se muestra resumen con selección solicitada, dependencias
añadidas, recuentos, exclusiones y warnings.

---

## 8. Importación

### 8.1 Flujo

1. Cargar ZIP.
2. Validación puramente local/servidor sin escrituras.
3. Mostrar informe dry-run y conflictos.
4. Elegir política permitida.
5. Crear journal/backup de rollback de todos los documentos que se modificarán.
6. Aplicar por fases.
7. Reconciliar referencias y recalcular vistas.
8. Emitir informe humano y machine-readable.

Nunca se escribe al recibir el archivo. El primer paso obligatorio es
**Validar / simulación**.

### 8.2 Validación

Debe comprobar:

- ZIP, manifiesto, versión y compatibilidad;
- tamaño total, número de ficheros/registros y profundidad JSON;
- checksums;
- esquema por sección y campos desconocidos;
- IDs únicos dentro del archivo;
- referencias entre secciones;
- cuentas/security IDs/position IDs válidos;
- integridad de cadenas de corrección, transferencias y CA;
- ausencia de secretos;
- recuentos y totales de control;
- colisiones con el destino;
- invariantes del ledger y no negatividad cuando corresponda.

Una versión de esquema futura no compatible bloquea. Una versión antigua
compatible se transforma en memoria mediante migradores explícitos antes del
dry-run; el archivo original no se modifica.

### 8.3 Modos de importación

#### Validar / dry-run

Siempre disponible y recomendado. Cero escrituras. Produce el mismo plan y
conflictos que usaría la ejecución real.

#### Merge — omitir existentes

- Si clave e identidad existen y el hash canónico coincide: `SKIPPED_IDENTICAL`.
- Si existe una clave distinta: conflicto; no se sobrescribe.
- Crea solo registros ausentes cuya referencia sea válida.

Es el modo más seguro para backups repetidos.

#### Merge — actualizar seleccionados

Permite actualizar únicamente familias/fields autorizados tras mostrar diff:

- Security Master: metadatos editables, nunca identidad MIC:TICKER.
- Symbol config: campos curados y posiciones por `position_id`.
- Action plans: por `id`.
- Settings: por sección/campo.
- Accounts: campos mutables; `account_id` no cambia.

El ledger **no se actualiza in-place** por defecto. Para un ID existente:

- hash igual → skip idempotente;
- payload distinto → conflicto bloqueante;
- una corrección legítima debe venir como documento nuevo con su cadena, no
  como overwrite del pasado.

#### Reemplazo completo

No se habilita en la primera fase. Cosmos no ofrece una transacción atómica
entre contenedores ni entre múltiples particiones.

Solo podrá añadirse después con:

- modo mantenimiento y bloqueo de escrituras;
- backup automático del destino;
- validación completa previa;
- estrategia create-before-delete;
- journal persistente;
- verificación post-flight;
- rollback probado;
- restricción inicial a destino vacío o restauración del mismo `export_id`.

### 8.4 Conflictos

Clave lógica por familia:

- security: `security_id`;
- account: `account_id`;
- movement: `(account_id, id)`;
- position: `(security_id/symbol, position_id)`;
- symbol config: `security_id` con bridge de ticker;
- plan: `(symbol, id)`;
- settings: path funcional.

Estados del informe:

- `CREATE`;
- `SKIP_IDENTICAL`;
- `UPDATE_SAFE`;
- `CONFLICT_REQUIRES_CHOICE`;
- `BLOCKED_MISSING_REFERENCE`;
- `BLOCKED_INVARIANT`;
- `REDACTED_IGNORED`.

No usar “last write wins” implícito. `updated_at` informa al usuario, pero no
decide por sí solo.

### 8.5 Idempotencia

- Cada ejecución recibe `import_run_id`.
- Se registra `archive.export_id`, hash del archivo y resultado por registro.
- Repetir el mismo archivo con la misma política debe producir solo
  `SKIP_IDENTICAL`.
- IDs de dominio se preservan.
- La comparación ignora metadatos Cosmos y normaliza JSON/decimales.
- No regenerar IDs de movimientos, posiciones, planes ni grupos.

### 8.6 Estrategia Cosmos y rollback

Cosmos solo garantiza batch transaccional dentro de una misma partición y
contenedor; esta restauración escribe en hasta tres contenedores (`symbols`,
`portfolio`, `settings`) y muchas particiones. Por ello:

1. **Pre-flight global**: leer destino, validar referencias y construir plan.
2. **Backup de afectados**: guardar before-images de todo documento que se
   actualizaría; no hace falta para creates, pero se journaliza su identidad.
3. **Orden de creación**:
   security masters → accounts → symbol configs/positions → plans →
   movimientos → settings.
4. **Por partición**: usar transactional batch cuando varias escrituras
   relacionadas compartan partición.
5. **Create-before-delete**: no se borra nada en merge.
6. **Fallo**: detener nuevas escrituras y compensar en orden inverso:
   borrar creates de esta ejecución y restaurar before-images con control de
   ETag/ausencia de cambios externos.
7. **Concurrencia**: si cambia un ETag desde el pre-flight, abortar esa unidad
   y marcar conflicto; nunca pisar una edición concurrente.
8. **Post-flight**: recuentos, checksums lógicos, referencias, cadenas,
   recomputación de holdings y comparación de controles.

Un rollback compensatorio no equivale a atomicidad global. El informe debe
decir claramente `COMPLETED`, `ROLLED_BACK`, o
`PARTIAL_REQUIRES_ATTENTION`, con IDs exactos afectados.

### 8.7 Informe humano

Debe incluir:

- archivo, export date/version y compatibilidad;
- modo y política elegidos;
- resumen por sección: creados, omitidos, actualizados, bloqueados;
- dependencias añadidas o faltantes;
- conflictos con diff resumido y resolución;
- warnings de datos ya rotos en origen;
- validaciones de cartera antes/después;
- estado de rollback;
- acciones recomendadas.

Debe poder descargarse como JSON y mostrarse en lenguaje humano en la UI. No
debe contener secretos ni volcar documentos financieros completos.

---

## 9. Criterios de aceptación round-trip

Sobre una fixture representativa:

1. Exportar una copia completa, importar en una base vacía y reexportar.
2. Los hashes canónicos de todas las secciones autoritativas/referencia deben
   coincidir, ignorando timestamps operativos explícitamente documentados.
3. Mismos accounts, securities, watchlist/config, positions y action plans.
4. Mismos movimientos, incluidos inactivos, correcciones, reasignaciones,
   transferencias, CA groups, FX, withholding, cost basis y links.
5. Holdings recalculados iguales por security/account: shares, remaining cost
   basis, realized result, dividends y warnings de completitud.
6. Economics de opciones reconstruido desde los mismos movimientos enlazados.
7. Repetir la importación no crea duplicados ni cambia resultados.
8. Un checksum alterado bloquea antes de escribir.
9. Una referencia ausente bloquea o fuerza cierre de dependencia; nunca deja
   un dangling reference nuevo.
10. Un secreto sembrado en settings no aparece en el ZIP.
11. Actividades, trazas, telemetría, cachés, forecasts y calendario no aparecen.
12. Un fallo inducido a mitad de importación termina en rollback verificado o
    informe `PARTIAL_REQUIRES_ATTENTION`, nunca en éxito falso.

---

## 10. Copia automática diaria en Azure Blob Storage

### 10.1 Mecanismo de ejecución

El despliegue real usa una imagen backend en **Azure Container Apps**. El
proceso normal ejecuta FastAPI y el scheduler en el mismo contenedor
(`backend/run.py`), y la documentación obliga a mantener una sola réplica para
evitar duplicados. Ese scheduler usa la zona local del contenedor, no aplica la
clave `scheduler.timezone`, pierde ejecuciones durante reinicios/deploys y no
ofrece por sí mismo exclusión distribuida ni recuperación durable.

**Decisión:** la copia autoritativa automática se ejecutará como un
**Azure Container Apps Job programado**, separado del proceso API, reutilizando
la misma imagen backend con un entry point dedicado. No se añadirá a
`TaskRegistry`, no será una Azure Function y no dependerá de GitHub Actions.

Motivos:

- encaja con el runtime, imagen, red y despliegue de Container Apps ya usados;
- aísla CPU, memoria, timeout, reintentos y fallos del API;
- evita que un reinicio o escalado del API sea el mecanismo de calendario;
- permite identidad administrada y RBAC propios;
- evita introducir un segundo stack de hosting como Functions;
- GitHub Actions no es un scheduler operativo con identidad y red de runtime.

Los schedules de Container Apps Jobs se evalúan en UTC. El trigger de
infraestructura será diario, con cron `15 23 * * *`: Azure inicia el Job a las
23:15 UTC, que corresponde a las 00:15 de `Europe/Madrid` en horario estándar
y a las 01:15 durante el horario de verano. La comprobación interna de fecha
local, hora debida e idempotencia se conserva como barrera de seguridad para
reintentos o ejecuciones manuales, no como mecanismo de polling.

Configuración de producción: `backend/scripts/configure-backup.sh` define el
cron de Azure y las variables del entorno del Job. Ese entorno es la única
fuente de verdad en runtime; `AutomaticBackupConfig.from_environment()` lee
`BACKUP_ENABLED`, `BACKUP_TIMEZONE`, `BACKUP_LOCAL_TIME` y
`BACKUP_SCHEDULE_NAME`. `BACKUP_BLOB_CONTAINER` selecciona el contenedor.
`config.yaml` y la UI de Settings no configuran este Job. El Container App del
API no tiene acceso al control plane de Azure y, por tanto, no expone
configuración ni estado de la copia automática. La configuración y
monitorización se realizan sobre el Job y los artefactos Blob mediante Azure
Portal o CLI; Settings conserva únicamente exportación e importación manual.

`timezone` debe ser un nombre IANA, nunca una abreviatura ni un offset fijo.
El valor por defecto es `Europe/Madrid`; todos los timestamps persistidos se
guardan además en UTC. La identidad lógica de una ejecución programada es
`(schedule_name, fecha_local)`. El cron diario fijo evita editar la
infraestructura dos veces al año: se ejecuta a las 00:15 locales en horario
estándar y a las 01:15 en horario de verano. La barrera interna permite una
sola ejecución por fecha local, incluso ante reintentos o disparos manuales.
Una ejecución diaria omitida no se recupera mediante polling y no se recuperan
automáticamente días anteriores.

La definición del Job debe usar una sola réplica/completion, paralelismo 1, un
timeout explícito superior al máximo de exportación esperado y pocos
reintentos de plataforma. La exclusión real sigue residiendo en Blob, porque
también existen ejecuciones manuales y reintentos.

### 10.2 Hash lógico de cambio

No se comparan bytes ZIP, ETags, tamaños ni timestamps de blobs. Antes de
comprimir o cifrar se calcula `content_sha256` sobre el dataset lógico:

1. aplicar la misma allowlist/redacción y normalización del export;
2. eliminar metadatos Cosmos y campos volátiles de ejecución;
3. serializar cada registro como JSON canónico UTF-8, con claves ordenadas,
   separadores compactos, timestamps normalizados a UTC y decimales como
   strings;
4. ordenar cada sección por su clave lógica estable, nunca por el orden de
   consulta;
5. calcular el hash de cada sección;
6. calcular el hash global sobre versión de esquema, scope/preset y la lista
   ordenada de `(section_name, schema_version, count, section_hash)`.

Quedan fuera del hash global, como mínimo: `exported_at`, `export_id`,
`backup_run_id`, timestamps/estado del job, orden original de lectura,
metadatos y timestamps ZIP, compresión, nombre del archivo, metadata/tags de
Blob, ETags, nonce/IV y envelope del cifrado. Los datos autoritativos,
referencias, IDs, estados y settings funcionales sí participan.

El hash se compara con el último `content_sha256` exitoso, aunque el último run
haya sido manual. Si coincide, no se crea otro ZIP. Se registra un run
`NO_CHANGE`, se actualiza salud/última comprobación y se mantiene intacto el
puntero al último archivo cambiado. Este contrato requiere fixtures que
demuestren que reordenar consultas, cambiar la compresión, el run ID, la fecha
de export o el nonce no cambia el hash.

### 10.3 Layout, objetos y catálogo

Cuenta Storage general-purpose v2, contenedor privado
`user-data-backups`, sin acceso público:

```text
user-data-backups/
  v1/daily/2026/09/19/
    20260919T221501Z_<run-id>_<content-hash-12>.oil-backup.zip
  v1/control/
    lock
    latest.json
    health.json
  v1/runs/2026/09/
    <run-id>.json
  v1/monthly/
    2026-09.json
  v1/staging/
    <run-id>/...
```

- Los ZIP bajo `daily/` son inmutables: se crean con
  `If-None-Match: *`, nunca se sobrescriben ni renombran.
- `latest.json` referencia el último ZIP **cambiado y verificado** e incluye
  path, hash lógico, hash SHA-256 del archivo almacenado, tamaño, versión,
  counts, fecha local, `run_id` y timestamps UTC.
- Cada run escribe un registro pequeño append-only, incluso `NO_CHANGE`, sin
  documentos financieros ni logs. Los anchors mensuales apuntan a un ZIP
  existente; no duplican el contenido.
- Metadata del ZIP: formato, versión, `content_sha256`,
  `archive_sha256`, fecha local, `run_id`, estado de cifrado y counts
  resumidos. Tags indexables, limitados y no sensibles:
  `backupType=daily`, `schemaVersion`, `yearMonth`, `encrypted`,
  `retentionClass`.
- Ni metadata ni tags incluyen nombres de cuentas, símbolos, import rows,
  secretos ni payload financiero.

La subida usa block blob. Los bloques no confirmados o un blob de staging no
son una copia válida. Solo después de commit, lectura de propiedades y
verificación de tamaño/hash se actualiza `latest.json`. Staging se elimina tras
éxito y una regla de lifecycle purga huérfanos al día siguiente.

### 10.4 Concurrencia, condicionales e idempotencia

1. Adquirir un lease renovable sobre `v1/control/lock`; si ya existe un lease,
   finalizar como `ALREADY_RUNNING`, no iniciar otro export.
2. Leer `latest.json` y conservar su ETag.
3. Crear un `run_id`, pero usar `(trigger, fecha_local)` como clave idempotente
   para el run programado. Un reintento recupera el mismo resultado.
4. Generar y validar localmente el archivo completo antes de publicarlo.
5. Crear el ZIP final con `If-None-Match: *`. Un `412` en un reintento se
   considera éxito únicamente si hashes, tamaño y versión coinciden.
6. Actualizar `latest.json` con `If-Match` sobre el ETag leído. Si cambió,
   releer: si ya apunta al mismo run/hash, éxito; si apunta a una copia válida
   posterior, no retroceder el puntero; cualquier otra divergencia es
   conflicto.
7. Renovar el lease durante export/upload y liberarlo siempre que sea posible.

Los errores transitorios de Cosmos o Blob (`408`, `429`, timeouts y `5xx`) usan
backoff exponencial con jitter, respetan `Retry-After` y tienen presupuesto
acotado. Errores de autenticación/autorización, esquema, redacción o integridad
no se reintentan ciegamente. Las operaciones de crear objeto, registrar run y
actualizar puntero son reanudables e idempotentes.

Una exportación manual usa el mismo pipeline, lock, hash y layout, con
`trigger=manual`. Por defecto puede crear un ZIP aunque no haya cambios, porque
es una solicitud explícita, pero debe mostrar `same_content_as_latest=true`.
Una opción “solo si cambió” aplica exactamente la política diaria. Una
ejecución manual nunca permite que un run programado antiguo haga retroceder
`latest.json`.

### 10.5 Seguridad

- Autenticación de Blob mediante identidad administrada del Container Apps
  Job y `DefaultAzureCredential`; no connection string, account key ni SAS
  persistente.
- RBAC mínimo a nivel de contenedor: `Storage Blob Data Contributor` y un rol
  custom determinista cuya única DataAction es
  `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write`;
  Actions, NotActions y NotDataActions están vacíos. `AssignableScopes` debe ser
  el resource group, el scope válido más estrecho para una definición custom
  de Azure; ser asignable no concede acceso y la asignación efectiva se valida
  en el contenedor exacto y contra el ID determinista del rol. Cada reejecución
  normaliza y compara la definición; cualquier rol homónimo, obsoleto o más
  amplio falla cerrado y no se sobrescribe. El principal de despliegue necesita
  `Microsoft.Authorization/roleDefinitions/write` y
  `Microsoft.Authorization/roleAssignments/write`. La identidad de despliegue
  no se reutiliza como identidad de runtime.
- Contenedor y cuenta sin acceso anónimo; `allowBlobPublicAccess=false`,
  HTTPS-only, TLS moderno y, cuando la red de Container Apps esté integrada,
  firewall/private endpoint de Storage.
- Cifrado en reposo de Azure Storage siempre activo. CMK es opcional según
  requisitos de gobierno.
- Cifrado cliente del ZIP es opcional y recomendado si operadores pueden
  descargarlo. Debe usar cifrado autenticado/envelope con clave de Key Vault;
  el nonce aleatorio y el ciphertext nunca participan en `content_sha256`.
- El job no incluye secretos en el archivo. La redacción global y los canarios
  de secretos se ejecutan antes de hash/upload. Sus propias credenciales no se
  escriben en manifiestos, metadata, tags, health ni notificaciones.

La autenticación actual de Cosmos por clave es una deuda separada: el Job puede
heredarla inicialmente desde secretos de Container Apps, pero la evolución
recomendada es identidad administrada y RBAC de datos de Cosmos. En ningún caso
esa credencial forma parte del backup.

### 10.6 Retención y recuperación

Valores iniciales:

- conservar copias cambiadas diarias durante **35 días**;
- conservar **12 anchors mensuales**, cada uno apuntando a la última copia
  cambiada válida del mes;
- conservar registros de run/health durante 90 días;
- purgar staging no confirmado tras 1 día.

Si un mes no cambia el contenido, el anchor mensual puede referenciar la misma
copia que el mes anterior; no se sube otro ZIP solo para cumplir calendario.
El recolector no elimina un objeto mientras esté referenciado por un anchor
mensual vigente. La política debe probarse en modo informe antes de borrar.
Tras 35 días, los objetos diarios no anclados pueden pasar a Cool y eliminarse;
los mensuales pueden pasar a Cool/Archive según el RTO aceptado.

Habilitar **Blob versioning** para proteger `latest.json`, `health.json` y los
anchors, y **soft delete** de blobs y contenedor durante 14 días. Versioning no
reemplaza los objetos de backup ni la restauración probada. WORM/immutability
legal no se activa por defecto: dificulta corregir una exportación que hubiera
incluido datos indebidos y aumenta coste/operación. Solo se habilitará por un
requisito regulatorio explícito, con contenedor/cuenta separados y política de
redacción ya demostrada.

Recuperación: listar catálogo/anchors, seleccionar un objeto inmutable,
verificar hash del archivo, descifrar si aplica, validar checksums y ejecutar
el flujo de import dry-run existente. `latest.json` es una comodidad, no una
fuente única de verdad. Si está ausente o stale, se reconstruye desde registros
de run y blobs válidos sin modificar datos de usuario.

### 10.7 Matriz de fallos

| Caso | Resultado obligatorio |
|---|---|
| Sin cambios | `NO_CHANGE`; no ZIP; actualizar salud/run, no `latest.changed_at`. |
| Fallo al exportar/redactar/validar | No upload ni cambio de puntero; `FAILED_EXPORT`; alerta. |
| Fallo de upload | No puntero; limpiar o expirar staging; reintento idempotente; `FAILED_UPLOAD`. |
| Blob parcial/no confirmado | Nunca elegible para restore ni `latest`; lifecycle de staging. |
| `latest.json` stale/ausente | Reconstruir desde catálogo y verificar objeto; primera instalación sin copias crea baseline. |
| Falta backup previo pero hay blobs | Recuperar el más reciente válido; no asumir “sin cambios”. |
| No existe ninguna copia previa | Subir baseline aunque el dataset esté vacío, con counts explícitos. |
| Ejecución duplicada | Lease impide concurrencia; el segundo run termina `ALREADY_RUNNING` o reconoce el mismo resultado. |
| Job/deploy omitió el trigger diario | No hay polling ni catch-up automático; alertar y ejecutar manualmente si procede, usando la misma barrera de idempotencia. |
| Ejecución manual | Mismo pipeline y lock; upload explícito por defecto, sin retroceder `latest`. |
| Puntero falla tras upload | El ZIP queda válido pero no publicado; el reintento lo adopta y repara el puntero por CAS. |

### 10.8 Observabilidad

No se exportan logs. `health.json` y el registro de run mantienen solo estado
operativo mínimo:

- último intento y su trigger/estado/duración;
- última ejecución programada exitosa;
- último upload con cambio;
- `content_sha256` y `archive_sha256`;
- path/version/schema y si está cifrado;
- counts por sección, total de registros y bytes;
- número de reintentos y código de error sanitizado;
- siguiente fecha local esperada y antigüedad del último éxito.

Azure Monitor/Application Insights conserva métricas y logs operativos fuera
del backup. Alertas recomendadas: run fallido, ausencia de éxito programado
durante 26 horas, lease stale, fallo de integridad, puntero no reparable,
fallos repetidos de RBAC/red y retención fallida. `NO_CHANGE` no alerta; sí
cuenta como ejecución saludable. La notificación contiene IDs, estado y
counts, nunca payloads, símbolos, cuentas ni secretos.

---

## 11. Fases de entrega

### Fase 0 — Contrato y fixtures

- Schemas JSON versionados por sección.
- Canonicalización, redaction allowlist/denylist.
- Fixtures round-trip y casos corruptos.

### Fase 1 — Exportación segura

- Área Configuración con Exportar/Importar.
- Presets y custom con cierre de dependencias.
- ZIP, manifest, counts, checksums.
- Sin secretos y sin datos derivados/runtime.

### Fase 2 — Automatización Blob en sombra

- Extraer un servicio de export/hash reutilizable por UI y Job.
- Hash canónico determinista y pruebas contra campos volátiles/orden/nonce.
- Infraestructura Storage privada, identidad administrada, RBAC y Container
  Apps Job.
- Ejecución diaria en modo sombra: exportar, validar y registrar hash/health,
  sin publicar ZIP, para medir duración, coste y estabilidad.

### Fase 3 — Upload condicionado y recuperación operativa

- Lease, objetos inmutables, subida condicionada, catálogo, `latest` por CAS y
  reintentos idempotentes.
- Activar “solo si cambió”, baseline, no-change, barrera de idempotencia y trigger manual.
- Versioning, soft delete, lifecycle, anchors mensuales y alertas.
- Simulacros de puntero stale, upload parcial, duplicados, pérdida de red y
  restauración desde Blob.

### Fase 4 — Validación e import merge

- Upload seguro.
- Dry-run obligatorio.
- Merge/skip y create-only.
- Informe humano y JSON.
- Idempotencia y validación de referencias.

### Fase 5 — Updates controlados y rollback

- Diffs y update-safe por familia.
- Journal, ETags y compensación.
- Pruebas de fallo inducido.

### Fase 6 — Reemplazo completo, si se aprueba

- Solo tras resolver las decisiones abiertas y demostrar restore/rollback.
- Maintenance mode, backup previo, destino vacío/same-origin y post-flight.

### Fase futura opcional

- Cifrado del ZIP con contraseña.
- Notas generadas de planes como sección avanzada.
- Nuevas entidades manuales futuras, como calendar overrides, con migradores.

---

## 12. Riesgos principales

1. **Falsa atomicidad**: Cosmos no puede hacer rollback transaccional global.
2. **Documentos mixtos**: `symbol_config` mezcla datos manuales y derivados;
   exportar el documento bruto restauraría cachés obsoletas.
3. **Cadenas parciales**: omitir legs/correcciones rompe auditoría y cálculo.
4. **Identidad**: colisiones MIC:TICKER, ticker y account IDs no pueden
   resolverse con overwrite silencioso.
5. **Secretos**: `app-config` actualmente puede contener token/chat ID; requiere
   allowlist estricta, no simple eliminación posterior.
6. **Concurrencia**: cambios entre dry-run y apply requieren ETag y abort seguro.
7. **Datos sensibles**: el ZIP sin cifrar contiene historial financiero.
8. **Versionado**: restauraciones entre versiones requieren migradores
   explícitos y fixtures permanentes.
9. **Scheduler actual**: el cron in-process depende de una única réplica y de
   la zona del contenedor; no es una base durable para este backup.
10. **Hash incorrecto**: incluir timestamps, orden de consulta, ZIP o nonce
    produciría uploads diarios falsos; excluir datos autoritativos ocultaría
    cambios reales.
11. **Puntero mutable**: `latest` puede quedar detrás de un objeto ya subido;
    restore debe poder reconstruir catálogo y verificar hashes.
12. **Retención referenciada**: lifecycle no debe borrar una copia diaria aún
    usada como anchor mensual.

---

## 13. Decisiones de producto aún abiertas

1. ¿Las paper positions deben estar activadas por defecto en “Copia completa”?
   Recomendación: **sí**, porque son trabajo manual.
2. ¿Se permite exportar `action_plan.agent_notes` en una opción avanzada?
   Recomendación: **no en v1**; son generadas.
3. ¿Debe preservarse `source_row` completo o ofrecer redacción de columnas?
   Recomendación v1: preservarlo en copia completa con advertencia de
   sensibilidad; estudiar redacción configurable después.
4. ¿Se necesita cifrado con contraseña en v1?
   Recomendación: no bloquear v1, pero mostrar advertencia; priorizarlo si la
   copia va a salir del dispositivo.
5. ¿Debe existir “reemplazo completo”?
   Recomendación: no en v1; habilitar solo tras disponer de maintenance mode,
   journal y rollback probado.
6. ¿El backup automático debe usar cifrado cliente con Key Vault desde su
   primera activación?
   Recomendación: no bloquear la fase sombra; exigirlo antes de permitir
   descargas por operadores o si la política de datos lo requiere. El cifrado
   en reposo de Storage y el acceso privado sí son obligatorios desde el inicio.
