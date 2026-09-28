/**
 * Dependency-free exact grid index for nearest-point lookup.
 *
 * Deliberately a port of `spatial.GridPointIndex`: same cell math, same ring
 * pruning, so both runtimes answer identical queries. Coordinates must already
 * be in a metric system (metres), not degrees.
 */
export interface GridIndex {
  readonly cellSize: number;
  readonly xs: Float64Array;
  readonly ys: Float64Array;
  readonly cells: ReadonlyMap<string, number[]>;
  readonly minCellX: number;
  readonly minCellY: number;
  readonly maxCellX: number;
  readonly maxCellY: number;
  readonly count: number;
}

export function buildGridIndex(xs: Float64Array, ys: Float64Array, cellSize: number): GridIndex {
  if (!(cellSize > 0)) throw new Error("Размер ячейки индекса должен быть больше нуля");
  if (xs.length !== ys.length) throw new Error("Индекс требует одинаковое число X и Y");
  const cells = new Map<string, number[]>();
  let minCellX = Number.MAX_SAFE_INTEGER;
  let minCellY = Number.MAX_SAFE_INTEGER;
  let maxCellX = Number.MIN_SAFE_INTEGER;
  let maxCellY = Number.MIN_SAFE_INTEGER;

  for (let index = 0; index < xs.length; index += 1) {
    const cellX = Math.floor(xs[index] / cellSize);
    const cellY = Math.floor(ys[index] / cellSize);
    const key = `${cellX},${cellY}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(index);
    else cells.set(key, [index]);
    if (cellX < minCellX) minCellX = cellX;
    if (cellY < minCellY) minCellY = cellY;
    if (cellX > maxCellX) maxCellX = cellX;
    if (cellY > maxCellY) maxCellY = cellY;
  }

  return { cellSize, xs, ys, cells, minCellX, minCellY, maxCellX, maxCellY, count: xs.length };
}

function cellKey(cellX: number, cellY: number): string {
  return `${cellX},${cellY}`;
}

function cellXOf(index: GridIndex, x: number): number {
  return Math.floor(x / index.cellSize);
}

function cellYOf(index: GridIndex, y: number): number {
  return Math.floor(y / index.cellSize);
}

/** Euclidean distance from (x, y) to the closest point of a cell. */
function cellLowerBound(index: GridIndex, cellX: number, cellY: number, x: number, y: number): number {
  const minX = cellX * index.cellSize;
  const maxX = minX + index.cellSize;
  const minY = cellY * index.cellSize;
  const maxY = minY + index.cellSize;
  const dx = Math.max(minX - x, 0, x - maxX);
  const dy = Math.max(minY - y, 0, y - maxY);
  // sqrt(dx²+dy²), а не Math.hypot: hypot считает аккуратнее и отличается
  // от Python в последних битах, а паритет с spatial.py проверяется точно.
  return Math.sqrt(dx * dx + dy * dy);
}

/** Smallest distance from (x, y) to any point inside the given ring of cells. */
function ringLowerBound(index: GridIndex, centerX: number, centerY: number, ring: number, x: number, y: number): number {
  let minimum = Number.POSITIVE_INFINITY;
  for (let dx = -ring; dx <= ring; dx += 1) {
    for (let dy = -ring; dy <= ring; dy += 1) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
      minimum = Math.min(minimum, cellLowerBound(index, centerX + dx, centerY + dy, x, y));
    }
  }
  return minimum;
}

function scanCell(
  index: GridIndex,
  cellX: number,
  cellY: number,
  x: number,
  y: number,
  limit: number,
): { index: number; distance: number } | null {
  const bucket = index.cells.get(cellKey(cellX, cellY));
  if (!bucket) return null;
  let best = -1;
  let bestDistance = limit;
  for (const point of bucket) {
    const distance = Math.sqrt(
      (index.xs[point] - x) * (index.xs[point] - x) + (index.ys[point] - y) * (index.ys[point] - y),
    );
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return best < 0 ? null : { index: best, distance: bestDistance };
}

/**
 * Exact nearest point within `maxRadius` (inclusive). Returns null when
 * nothing is in range, so callers can raise a precise error instead of
 * silently snapping to a far away point.
 */
export function gridNearest(
  index: GridIndex,
  x: number,
  y: number,
  maxRadius = Number.POSITIVE_INFINITY,
): { index: number; distance: number } | null {
  if (index.count === 0) return null;
  if (!(maxRadius >= 0)) return null;
  const centerX = cellXOf(index, x);
  const centerY = cellYOf(index, y);
  let best: { index: number; distance: number } | null = null;
  // Параллельно best: «уже найденное расстояние». Им же отсекаем
  // кандидатов, поэтому значение строго уменьшается.
  let bestDistance = Number.POSITIVE_INFINITY;

  if (Number.isFinite(maxRadius)) {
    const radiusCells = Math.floor(maxRadius / index.cellSize) + 1;
    for (let dx = -radiusCells; dx <= radiusCells; dx += 1) {
      for (let dy = -radiusCells; dy <= radiusCells; dy += 1) {
        const candidate = scanCell(index, centerX + dx, centerY + dy, x, y, bestDistance);
        if (candidate) {
          best = candidate;
          bestDistance = candidate.distance;
        }
      }
    }
    // Граница включительная: точка ровно на maxRadius подходит.
    return best !== null && bestDistance <= maxRadius ? best : null;
  }

  const maxRing = Math.max(
    Math.abs(index.minCellX - centerX),
    Math.abs(index.minCellY - centerY),
    Math.abs(index.maxCellX - centerX),
    Math.abs(index.maxCellY - centerY),
  );
  for (let ring = 0; ring <= maxRing; ring += 1) {
    for (let dx = -ring; dx <= ring; dx += 1) {
      for (let dy = -ring; dy <= ring; dy += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
        const candidate = scanCell(index, centerX + dx, centerY + dy, x, y, bestDistance);
        if (candidate) {
          best = candidate;
          bestDistance = candidate.distance;
        }
      }
    }
    if (best && ring < maxRing && ringLowerBound(index, centerX, centerY, ring + 1, x, y) > bestDistance) {
      break;
    }
  }
  return best;
}

/** All point indices within `radius` (inclusive), ascending by index. */
export function gridWithinRadius(index: GridIndex, x: number, y: number, radius: number): number[] {
  if (!(radius >= 0)) throw new Error("Радиус поиска должен быть неотрицательным");
  if (index.count === 0) return [];
  const minX = Math.floor((x - radius) / index.cellSize);
  const maxX = Math.floor((x + radius) / index.cellSize);
  const minY = Math.floor((y - radius) / index.cellSize);
  const maxY = Math.floor((y + radius) / index.cellSize);
  const radiusSquared = radius * radius;
  const found: number[] = [];
  for (let cellX = minX; cellX <= maxX; cellX += 1) {
    for (let cellY = minY; cellY <= maxY; cellY += 1) {
      const bucket = index.cells.get(cellKey(cellX, cellY));
      if (!bucket) continue;
      for (const point of bucket) {
        const dx = index.xs[point] - x;
        const dy = index.ys[point] - y;
        if (dx * dx + dy * dy <= radiusSquared) found.push(point);
      }
    }
  }
  return found.sort((left, right) => left - right);
}
