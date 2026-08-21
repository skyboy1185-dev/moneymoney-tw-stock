let scanTail: Promise<void> = Promise.resolve();

/**
 * Keep network-heavy whole-market scans serialized inside the frontend
 * process. This protects the HTTP listener from socket exhaustion while an
 * automation job is downloading historical data for many symbols.
 */
export function withScanLoadLock<T>(task: () => Promise<T>): Promise<T> {
  const result = scanTail.then(task, task);
  scanTail = result.then(() => undefined, () => undefined);
  return result;
}
