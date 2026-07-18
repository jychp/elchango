import type { DeckApiClient } from "./client.js";
import type { DeckButton, DeckSnapshot } from "./contracts.js";
import { positionFromCoordinates, renderButton } from "./render.js";

const POLL_INTERVAL_MS = 1_000;
const MAX_RETRY_INTERVAL_MS = 15_000;
const SUCCESS_IMAGE = "static/imgs/actions/key/success.png";
const FAILURE_IMAGE = "static/imgs/actions/key/failure.png";
const OFFLINE_IMAGE = "static/imgs/actions/key/offline.png";
const SUCCESS_FEEDBACK_MS = 500;

export interface KeyPort {
  readonly id: string;
  readonly row: number;
  readonly column: number;
  setImage(image: string): Promise<void>;
  showAlert(): Promise<void>;
}

export interface SurfaceLogger {
  error(message: string): void;
}

interface VisibleKey {
  port: KeyPort;
  position: number;
  renderedImage?: string;
  feedbackImage: string | undefined;
}

export class StreamDeckSurface {
  private readonly keys = new Map<string, VisibleKey>();
  private snapshotValue: DeckSnapshot | null = null;
  private online = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private retryIntervalMs = POLL_INTERVAL_MS;
  private pollInFlight = false;
  private activationInFlight = false;

  constructor(
    private readonly api: Pick<
      DeckApiClient,
      "snapshot" | "activate" | "longPress"
    >,
    private readonly logger: SurfaceLogger,
    private readonly successFeedbackMs = SUCCESS_FEEDBACK_MS,
  ) {}

  register(port: KeyPort): void {
    const position = positionFromCoordinates(port.row, port.column);
    this.keys.set(port.id, { port, position, feedbackImage: undefined });
    if (this.online && this.snapshotValue) {
      void this.renderKey(this.keys.get(port.id)!);
    } else {
      void this.setKeyImage(this.keys.get(port.id)!, OFFLINE_IMAGE);
    }
    this.schedule(0);
  }

  unregister(keyId: string): void {
    this.keys.delete(keyId);
    if (this.keys.size === 0) {
      this.stopTimer();
      this.snapshotValue = null;
      this.online = false;
    }
  }

  async activate(keyId: string): Promise<void> {
    await this.dispatch(
      keyId,
      false,
      (buttonId, revision) => this.api.activate(buttonId, revision),
    );
  }

  async longPress(keyId: string): Promise<void> {
    await this.dispatch(
      keyId,
      true,
      (buttonId, revision) => this.api.longPress(buttonId, revision),
    );
  }

  private async dispatch(
    keyId: string,
    allowDisabled: boolean,
    request: (
      buttonId: string,
      revision: number,
    ) => ReturnType<DeckApiClient["activate"]>,
  ): Promise<void> {
    const key = this.keys.get(keyId);
    const snapshot = this.snapshotValue;
    if (
      !key ||
      !this.online ||
      !snapshot ||
      this.activationInFlight
    ) {
      if (key) await this.showFailure(key);
      return;
    }
    const button = snapshot.buttons.find(
      (candidate) => candidate.position === key.position,
    );
    if (!button || (!button.enabled && !allowDisabled)) return;
    if (
      allowDisabled &&
      button.kind !== "session" &&
      !(button.position >= 11 &&
        button.position <= 13 &&
        button.action === "execute_command")
    ) {
      return;
    }

    this.activationInFlight = true;
    await this.renderKey(key, "busy");
    try {
      const response = await request(button.id, snapshot.revision);
      if (response.snapshot) await this.installSnapshot(response.snapshot);
      await this.showSuccess(key);
      await this.poll();
    } catch (error) {
      this.logger.error(errorMessage(error));
      await this.showFailure(key);
      this.schedule(POLL_INTERVAL_MS);
    } finally {
      this.activationInFlight = false;
    }
  }

  async refreshNow(): Promise<void> {
    await this.poll();
  }

  private async poll(): Promise<void> {
    if (this.pollInFlight || this.keys.size === 0) return;
    this.pollInFlight = true;
    this.stopTimer();
    try {
      const snapshot = await this.api.snapshot();
      this.online = true;
      this.retryIntervalMs = POLL_INTERVAL_MS;
      await this.installSnapshot(snapshot);
      this.schedule(POLL_INTERVAL_MS);
    } catch (error) {
      this.online = false;
      this.logger.error(errorMessage(error));
      this.snapshotValue = null;
      try {
        await this.renderAllOffline();
      } finally {
        this.schedule(this.retryIntervalMs);
        this.retryIntervalMs = Math.min(
          MAX_RETRY_INTERVAL_MS,
          this.retryIntervalMs * 2,
        );
      }
    } finally {
      this.pollInFlight = false;
    }
  }

  private async installSnapshot(snapshot: DeckSnapshot): Promise<void> {
    if (
      this.snapshotValue &&
      snapshot.revision < this.snapshotValue.revision &&
      snapshot.observed_at_ms <= this.snapshotValue.observed_at_ms
    ) {
      return;
    }
    this.snapshotValue = snapshot;
    await this.renderAll();
  }

  private async renderAll(): Promise<void> {
    await Promise.all([...this.keys.values()].map((key) => this.renderKey(key)));
  }

  private async renderKey(
    key: VisibleKey,
    status: "ready" | "busy" | "error" = "ready",
  ): Promise<void> {
    const button = this.snapshotValue?.buttons.find(
      (candidate) => candidate.position === key.position,
    );
    await this.setKeyImage(
      key,
      button ? renderButton(button, status) : OFFLINE_IMAGE,
    );
  }

  private async renderAllOffline(): Promise<void> {
    await Promise.all(
      [...this.keys.values()].map((key) =>
        this.setKeyImage(key, OFFLINE_IMAGE),
      ),
    );
  }

  private async setKeyImage(key: VisibleKey, image: string): Promise<void> {
    if (key.feedbackImage && image !== key.feedbackImage) return;
    if (key.renderedImage === image) return;
    await key.port.setImage(image);
    key.renderedImage = image;
  }

  private async showSuccess(key: VisibleKey): Promise<void> {
    await this.showFeedback(key, SUCCESS_IMAGE);
  }

  private async showFailure(key: VisibleKey): Promise<void> {
    await this.showFeedback(key, FAILURE_IMAGE);
  }

  private async showFeedback(key: VisibleKey, image: string): Promise<void> {
    key.feedbackImage = image;
    await this.setKeyImage(key, image);
    await delay(this.successFeedbackMs);
    if (this.keys.get(key.port.id) === key && key.feedbackImage === image) {
      key.feedbackImage = undefined;
      await this.renderKey(key);
    }
  }

  private schedule(delayMs: number): void {
    if (this.keys.size === 0) return;
    this.stopTimer();
    this.timer = setTimeout(() => {
      void this.poll();
    }, delayMs);
  }

  private stopTimer(): void {
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }
}

export function buttonAtPosition(
  snapshot: DeckSnapshot,
  position: number,
): DeckButton | undefined {
  return snapshot.buttons.find((button) => button.position === position);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "unknown Stream Deck error";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
