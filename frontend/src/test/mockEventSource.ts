/** Minimal controllable stand-in for the browser EventSource. */
export class MockEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];

  readyState = MockEventSource.CONNECTING;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;

  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }

  static latest(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }

  static reset() {
    MockEventSource.instances = [];
  }

  open() {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  emit(data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }

  /** Simulates a dropped connection; `fatal` means the browser gave up. */
  fail(fatal = false) {
    this.readyState = fatal ? MockEventSource.CLOSED : MockEventSource.CONNECTING;
    this.onerror?.(new Event("error"));
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }
}
