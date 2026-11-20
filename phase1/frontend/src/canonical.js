/** Deterministic serialization of parsed JSON; array order is part of identity. */
export function canonicalJSON(value) {
  return JSON.stringify(
    value === null || typeof value !== 'object'
      ? value
      : Array.isArray(value)
        ? value.map((item) => JSON.parse(canonicalJSON(item)))
        : Object.fromEntries(
            Object.keys(value)
              .sort()
              .map((key) => [key, JSON.parse(canonicalJSON(value[key]))]),
          ),
  );
}
