import type { NetworkPayload, ValidationResult } from "./types";

export async function validateNetwork(
  network: NetworkPayload,
): Promise<ValidationResult> {
  const response = await fetch("/api/v1/network/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(network),
  });

  if (!response.ok) {
    throw new Error("Сервер вернул ошибку проверки сети");
  }
  return response.json() as Promise<ValidationResult>;
}
