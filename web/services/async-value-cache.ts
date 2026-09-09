/** Bounded, expiring cache. Failures are never cached; concurrent reads share work. */
export class AsyncValueCache<T> {
  private values = new Map<string, { value: T; expiresAt: number }>();
  private pending = new Map<string, Promise<T>>();
  constructor(private ttlMs: number, private capacity: number, private now = Date.now) {}
  async get(key: string, load: () => Promise<T>): Promise<T> {
    const cached = this.values.get(key);
    if (cached && cached.expiresAt > this.now()) return cached.value;
    this.values.delete(key);
    const running = this.pending.get(key);
    if (running) return running;
    const request = Promise.resolve().then(load).then(value => {
      while (this.values.size >= this.capacity) this.values.delete(this.values.keys().next().value!);
      this.values.set(key, { value, expiresAt: this.now() + this.ttlMs });
      return value;
    }).finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return request;
  }
}
