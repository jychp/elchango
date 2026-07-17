import {
  parseActivationResponse,
  parseDeckSnapshot,
  type DeckActivationResponse,
  type DeckSnapshot,
} from "./contracts.js";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8765";
const REQUEST_TIMEOUT_MS = 2_000;

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
  ) {}

  async snapshot(): Promise<DeckSnapshot> {
    const url = new URL("/api/snapshot", this.endpoint);
    url.searchParams.set("client_id", this.clientId);
    const response = await this.request(url, { method: "GET" });
    return parseDeckSnapshot(await response.json());
  }

  async activate(
    buttonId: string,
    revision: number,
  ): Promise<DeckActivationResponse> {
    const url = new URL("/api/activate", this.endpoint);
    const response = await this.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: this.clientId,
        button_id: buttonId,
        revision,
      }),
    });
    return parseActivationResponse(await response.json());
  }

  private async request(
    url: URL,
    init: RequestInit,
  ): Promise<Response> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await this.fetcher(url, {
        ...init,
        headers: {
          Accept: "application/json",
          ...init.headers,
        },
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new DeckApiError(
          await responseError(response),
          response.status,
        );
      }
      return response;
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

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as unknown;
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
  return `elChango service returned HTTP ${response.status}`;
}
