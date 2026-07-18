export const LONG_PRESS_DURATION_MS = 650;

interface PressState {
  timer: ReturnType<typeof setTimeout>;
  longPressStarted: boolean;
  longPressPromise?: Promise<void>;
}

export class KeyPressController {
  private readonly presses = new Map<string, PressState>();

  constructor(
    private readonly onShortRelease: (keyId: string) => Promise<void> | void,
    private readonly onLongPress: (keyId: string) => Promise<void> | void,
    private readonly durationMs = LONG_PRESS_DURATION_MS,
  ) {}

  keyDown(keyId: string): void {
    if (this.presses.has(keyId)) return;

    const state: PressState = {
      longPressStarted: false,
      timer: setTimeout(() => {
        state.longPressStarted = true;
        state.longPressPromise = Promise.resolve().then(() =>
          this.onLongPress(keyId),
        );
        void state.longPressPromise.catch(() => undefined);
      }, this.durationMs),
    };
    this.presses.set(keyId, state);
  }

  async keyUp(keyId: string): Promise<void> {
    const state = this.presses.get(keyId);
    if (!state) return;

    this.presses.delete(keyId);
    clearTimeout(state.timer);
    if (state.longPressStarted) {
      await state.longPressPromise;
      return;
    }
    await this.onShortRelease(keyId);
  }

  cancel(keyId: string): void {
    const state = this.presses.get(keyId);
    if (!state) return;
    clearTimeout(state.timer);
    this.presses.delete(keyId);
  }
}
