const RIGHTS_TYPE_TOKENS = new Set(["DERECHO", "DERECHOS", "RIGHT", "RIGHTS"]);
const ORDINARY_SALES_MARKERS = new Set([
  "ACCIONES",
  "STOCK",
  "STOCKS",
  "SHARE",
  "SHARES",
]);
const RIGHTS_WARNING_MARKERS = new Set([
  "RIGHTS_AMOUNT",
  "DERECHOS_WITH_QUANTITY",
]);
const AMOUNT_FIELD_KEYS = new Set([
  "source_derechos_amount",
  "source_derechos_eur",
  "derechos",
  "rights_amount",
  "rights_amount_eur",
  "source_rights_amount",
  "derechos_amount",
  "derechos_eur",
  "derechos_net",
  "rights_eur",
  "rights_net",
  "importe_en_derechos",
  "importe_derechos",
  "scrip_amount",
]);
const RIGHTS_FLAG_KEYS = new Set(["is_rights_sale", "is_derechos_sale", "rights_sale"]);
const TYPE_FIELD_KEYS = new Set([
  "sales_type",
  "sales_type_raw",
  "ca_leg_type",
  "ca_event_type",
  "event_type",
  "leg_type",
]);
const SOURCE_PAYLOAD_KEYS = new Set([
  "source_row",
  "source_payload",
  "source_data",
  "raw_source",
]);

function normalizeIdentifier(value) {
  return String(value)
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function normalizeMarker(value) {
  return normalizeIdentifier(value).toUpperCase();
}

function isRightsMarker(value) {
  const marker = normalizeMarker(value);
  return marker !== "" && marker.split("_").some((token) => RIGHTS_TYPE_TOKENS.has(token));
}

function isSuspiciousAmount(value) {
  if (value == null || (typeof value === "string" && value.trim() === "")) {
    return false;
  }
  if (typeof value === "boolean") return true;
  if (typeof value === "number") {
    return !Number.isFinite(value) || value !== 0;
  }
  if (typeof value === "bigint") return value !== 0n;
  if (typeof value !== "string") return true;

  let compact = value.trim();
  if (compact.includes(",")) compact = compact.replace(/\./g, "").replace(/,/g, ".");
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(compact)) {
    return true;
  }
  const amount = Number(compact);
  return !Number.isFinite(amount) || amount !== 0;
}

function isSuspiciousRightsFlag(value) {
  if (value == null || value === "") return false;
  if (value === true) return true;
  if (value === false) return false;
  const marker = normalizeMarker(value);
  if (["TRUE", "YES", "SI", "1"].includes(marker)) return true;
  return !["FALSE", "NO", "0"].includes(marker);
}

function typeFieldIsAssociated(key, value) {
  const marker = normalizeMarker(value);
  if (!marker) return false;
  if (isRightsMarker(value)) return true;
  return (key === "sales_type" || key === "sales_type_raw")
    && !ORDINARY_SALES_MARKERS.has(marker);
}

function hasSuspiciousRightsField(value, seen = new WeakSet(), inSourcePayload = false) {
  if (value == null || typeof value !== "object") return false;
  if (seen.has(value)) return true;
  seen.add(value);

  for (const [rawKey, fieldValue] of Object.entries(value)) {
    const key = normalizeIdentifier(rawKey);
    if (AMOUNT_FIELD_KEYS.has(key) && isSuspiciousAmount(fieldValue)) return true;
    if (RIGHTS_FLAG_KEYS.has(key) && isSuspiciousRightsFlag(fieldValue)) return true;
    if (TYPE_FIELD_KEYS.has(key) && typeFieldIsAssociated(key, fieldValue)) return true;
    if (
      inSourcePayload
      && (key.includes("derecho") || key.includes("rights"))
      && isSuspiciousAmount(fieldValue)
    ) return true;
    if (hasSuspiciousRightsField(
      fieldValue,
      seen,
      inSourcePayload || SOURCE_PAYLOAD_KEYS.has(key),
    )) return true;
  }
  return false;
}

export function isUnsupportedRightsWarning(warning) {
  const type = normalizeMarker(
    typeof warning === "string" ? warning : warning?.type,
  );
  return RIGHTS_WARNING_MARKERS.has(type) || isRightsMarker(type);
}

export function isUnsupportedRightsMovement(movement) {
  if (movement == null || typeof movement !== "object" || Array.isArray(movement)) {
    return true;
  }
  const warnings = [
    ...(Array.isArray(movement.warnings) ? movement.warnings : []),
    ...(Array.isArray(movement.movement_warnings) ? movement.movement_warnings : []),
  ];
  return warnings.some(isUnsupportedRightsWarning) || hasSuspiciousRightsField(movement);
}
