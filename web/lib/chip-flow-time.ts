const TAIPEI_ISO_WALL_CLOCK =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/;

/**
 * Lightweight Charts treats numeric timestamps as UTC and has no built-in
 * time-zone support. Convert the Taipei ISO wall clock into a UTC-shaped
 * timestamp so its UTC axis labels display the intended Taiwan market time.
 */
export function toTaipeiChartTimestamp(snapshotTime: string): number {
  const match = TAIPEI_ISO_WALL_CLOCK.exec(snapshotTime);
  if (!match) {
    throw new RangeError(`Invalid chip-flow snapshot time: ${snapshotTime}`);
  }
  const [, year, month, day, hour, minute, second = "0"] = match;
  return Math.floor(Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  ) / 1_000);
}
