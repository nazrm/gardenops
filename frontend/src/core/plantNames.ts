import type { Plant } from "./models";

export function buildPlantNameMap(
  plants: Plant[],
): Map<string, string> {
  const map = new Map<string, string>();
  for (const p of plants) {
    map.set(p.plt_id, p.name);
  }
  return map;
}
