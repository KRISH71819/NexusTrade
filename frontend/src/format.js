export function money(value, digits = 2) {
  const amount = Number(value || 0);
  return `Rs ${amount.toLocaleString("en-IN", {
    maximumFractionDigits: amount >= 1000 ? 0 : digits,
    minimumFractionDigits: amount >= 1000 ? 0 : digits,
  })}`;
}

export function compactMoney(value) {
  const amount = Number(value || 0);
  if (amount >= 10000000) return `Rs ${(amount / 10000000).toFixed(2)}Cr`;
  if (amount >= 100000) return `Rs ${(amount / 100000).toFixed(2)}L`;
  if (amount >= 1000) return `Rs ${(amount / 1000).toFixed(1)}K`;
  return `Rs ${amount.toFixed(0)}`;
}

export function percent(value, digits = 2) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

export function probability(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

export function sentiment(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
}

export function volume(value) {
  const number = Number(value || 0);
  if (number >= 10000000) return `${(number / 10000000).toFixed(2)}Cr`;
  if (number >= 100000) return `${(number / 100000).toFixed(2)}L`;
  if (number >= 1000) return `${(number / 1000).toFixed(1)}K`;
  return number.toLocaleString("en-IN");
}

export function dateTime(value) {
  if (!value) return "--";
  const date =
    typeof value === "number"
      ? new Date(value > 10000000000 ? value : value * 1000)
      : new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function featureName(key) {
  return String(key).replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function featureValue(value) {
  if (typeof value === "number") {
    return Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(2);
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value ?? "--");
}

export function signedClass(value) {
  return Number(value || 0) >= 0 ? "positive" : "negative";
}

export function actionClass(action = "HOLD") {
  return String(action).toLowerCase();
}

export function normalizeTicker(ticker = "") {
  return String(ticker).replace(".NS", "").toUpperCase();
}
