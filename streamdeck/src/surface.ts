import type { DeckApiClient } from "./client.js";
import type { DeckButton, DeckSnapshot } from "./contracts.js";
import {
  positionFromCoordinates,
  renderButton,
  renderOffline,
} from "./render.js";

const POLL_INTERVAL_MS = 1_000;
const MAX_RETRY_INTERVAL_MS = 15_000;
const SUCCESS_IMAGE = "static/imgs/actions/key/success.png";
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
    private readonly api: Pick<DeckApiClient, "snapshot" | "activate">,
    private readonly logger: SurfaceLogger,
    private readonly successFeedbackMs = SUCCESS_FEEDBACK_MS,
  ) {}

  register(port: KeyPort): void {
    const position = positionFromCoordinates(port.row, port.column);
    this.keys.set(port.id, { port, position });
    if (this.online && this.snapshotValue) {
      void this.renderKey(this.keys.get(port.id)!);
    } else {
      void this.setKeyImage(this.keys.get(port.id)!, renderOffline());
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
    const key = this.keys.get(keyId);
    const snapshot = this.snapshotValue;
    if (
      !key ||
      !this.online ||
      !snapshot ||
      this.activationInFlight
    ) {
      if (key) await key.port.showAlert();
      return;
    }
    const button = snapshot.buttons.find(
      (candidate) => candidate.position === key.position,
    );
    if (!button?.enabled) return;

    this.activationInFlight = true;
    await this.renderKey(key, "busy");
    try {
      const response = await this.api.activate(button.id, snapshot.revision);
      if (response.snapshot) await this.installSnapshot(response.snapshot);
      await this.showSuccess(key);
      await this.poll();
    } catch (error) {
      this.logger.error(errorMessage(error));
      await this.renderKey(key, "error");
      await key.port.showAlert();
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
      button ? renderButton(button, status) : renderOffline(),
    );
  }

  private async renderAllOffline(): Promise<void> {
    const image = renderOffline();
    await Promise.all(
      [...this.keys.values()].map((key) => this.setKeyImage(key, image)),
    );
  }

  private async setKeyImage(key: VisibleKey, image: string): Promise<void> {
    if (key.renderedImage === image) return;
    await key.port.setImage(image);
    key.renderedImage = image;
  }

  private async showSuccess(key: VisibleKey): Promise<void> {
    await this.setKeyImage(key, SUCCESS_IMAGE);
    await delay(this.successFeedbackMs);
    if (this.keys.get(key.port.id) === key) {
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
