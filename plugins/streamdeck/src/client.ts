import { constants } from "node:fs";
import { open } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  parseActivationResponse,
  parseDeckSnapshot,
  type DeckActivationResponse,
  type DeckSnapshot,
} from "./contracts.js";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8765";
const REQUEST_TIMEOUT_MS = 2_000;
const ACTION_TIMEOUT_MS = 10_000;
const CONTROL_TOKEN_PATH = path.join(
  os.homedir(),
  "Library",
  "Application Support",
  "elChango",
  "control-token",
);
const CONTROL_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

export class DeckApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "DeckApiError";
  }
}

export class DeckApiClient {
  constructor(
    readonly clientId: string,
    private readonly endpoint = DEFAULT_ENDPOINT,
    private readonly fetcher: typeof fetch = fetch,
    private readonly requestTimeoutMs = REQUEST_TIMEOUT_MS,
    private readonly tokenReader: () => Promise<string> = readControlToken,
    private readonly actionTimeoutMs = ACTION_TIMEOUT_MS,
  ) {}

  async snapshot(): Promise<DeckSnapshot> {
    const url = new URL("/api/snapshot", this.endpoint);
    url.searchParams.set("client_id", this.clientId);
    return parseDeckSnapshot(await this.request(url, { method: "GET" }));
  }

  async activate(
    buttonId: string,
    revision: number,
  ): Promise<DeckActivationResponse> {
    return this.postButtonAction("/api/activate", buttonId, revision);
  }

  async longPress(
    buttonId: string,
    revision: number,
  ): Promise<DeckActivationResponse> {
    return this.postButtonAction("/api/long-press", buttonId, revision);
  }

  private async postButtonAction(
    path: string,
    buttonId: string,
    revision: number,
  ): Promise<DeckActivationResponse> {
    const url = new URL(path, this.endpoint);
    return parseActivationResponse(
      await this.request(
        url,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            client_id: this.clientId,
            button_id: buttonId,
            revision,
          }),
        },
        this.actionTimeoutMs,
      ),
    );
  }

  private async request(
    url: URL,
    init: RequestInit,
    timeoutMs = this.requestTimeoutMs,
  ): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const token = await this.tokenReader();
      const response = await this.fetcher(url, {
        ...init,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          ...init.headers,
        },
        signal: controller.signal,
      });
      const body = await response.text();
      if (!response.ok) {
        throw new DeckApiError(
          responseError(response.status, body),
          response.status,
        );
      }
      return JSON.parse(body) as unknown;
    } catch (error) {
      if (error instanceof DeckApiError) throw error;
      if (error instanceof Error && error.name === "AbortError") {
        throw new DeckApiError("elChango service request timed out");
      }
      throw new DeckApiError(
        error instanceof Error
          ? error.message
          : "elChango service is unavailable",
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}

async function readControlToken(): Promise<string> {
  const handle = await open(
    CONTROL_TOKEN_PATH,
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  try {
    const information = await handle.stat();
    if (!information.isFile()) {
      throw new DeckApiError("elChango control token is not a regular file");
    }
    if ((information.mode & 0o777) !== 0o600) {
      throw new DeckApiError("elChango control token permissions are unsafe");
    }
    if (
      typeof process.getuid === "function" &&
      information.uid !== process.getuid()
    ) {
      throw new DeckApiError("elChango control token has an unexpected owner");
    }
    const token = (await handle.readFile("utf8")).trim();
    const decoded = Buffer.from(token, "base64url");
    if (
      !CONTROL_TOKEN_PATTERN.test(token) ||
      decoded.length !== 32 ||
      decoded.toString("base64url") !== token
    ) {
      throw new DeckApiError("elChango control token has an invalid format");
    }
    return token;
  } finally {
    await handle.close();
  }
}

function responseError(status: number, body: string): string {
  try {
    const payload = JSON.parse(body) as unknown;
    if (
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof payload.error === "string"
    ) {
      return payload.error;
    }
  } catch {
    // Fall back to the status when the response body is not JSON.
  }
  return `elChango service returned HTTP ${status}`;
}
