import assert from "node:assert/strict";
import test from "node:test";

import { DeckApiClient } from "../src/client.js";
import {
  parseDeckSnapshot,
  type DeckActivationResponse,
  type DeckButton,
  type DeckSnapshot,
} from "../src/contracts.js";
import {
  positionFromCoordinates,
  renderButton,
  renderOffline,
} from "../src/render.js";
import {
  StreamDeckSurface,
  type KeyPort,
} from "../src/surface.js";

test("coordinates map the MK.2 grid to deck positions", () => {
  assert.equal(positionFromCoordinates(0, 0), 0);
  assert.equal(positionFromCoordinates(1, 4), 9);
  assert.equal(positionFromCoordinates(2, 4), 14);
  assert.throws(() => positionFromCoordinates(3, 0), /unsupported/);
});

test("button rendering centers only the icon and title with state color", () => {
  const image = decodeSvg(renderButton({
    ...buttonAt(0),
    label: "<agent>",
    detail: "must not render",
    color: "working",
  }));

  assert.match(image, /&lt;agent&gt;/);
  assert.match(image, /#4ba3e3/);
  assert.match(image, /text-anchor="middle"/);
  assert.doesNotMatch(image, /must not render/);
  assert.doesNotMatch(image, /stroke=/);
  assert.doesNotMatch(image, /<svg[^>]+<svg/);
  assert.match(image, /transform="translate\(42 20\) scale/);
  assert.match(decodeSvg(renderOffline()), /Offline/);
});

test("snapshot parser requires the full fixed deck", () => {
  assert.throws(
    () => parseDeckSnapshot({ ...snapshot(1), buttons: [] }),
    /exactly 15/,
  );
  const duplicatePosition = snapshot(1);
  duplicatePosition.buttons[14] = {
    ...duplicatePosition.buttons[14]!,
    position: 13,
  };
  assert.throws(
    () => parseDeckSnapshot(duplicatePosition),
    /each position from 0 through 14 exactly once/,
  );
  const outOfGridPosition = snapshot(1);
  outOfGridPosition.buttons[14] = {
    ...outOfGridPosition.buttons[14]!,
    position: 15,
  };
  assert.throws(
    () => parseDeckSnapshot(outOfGridPosition),
    /each position from 0 through 14 exactly once/,
  );
  assert.equal(parseDeckSnapshot(snapshot(1)).revision, 1);
});

test("API client sends client identity and unified activation", async () => {
  const requests: Array<{
    url: string;
    init: RequestInit | undefined;
  }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: input.toString(), init });
    const body =
      init?.method === "POST"
        ? { accepted: true, action: "focus_session" }
        : snapshot(4);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new DeckApiClient(
    "streamdeck",
    "http://127.0.0.1:8765",
    fetcher,
  );

  await client.snapshot();
  await client.activate("session:one", 4);

  assert.match(requests[0]!.url, /client_id=streamdeck/);
  assert.equal(requests[1]!.url, "http://127.0.0.1:8765/api/activate");
  assert.deepEqual(JSON.parse(requests[1]!.init!.body as string), {
    client_id: "streamdeck",
    button_id: "session:one",
    revision: 4,
  });
});

test("API client keeps its timeout active while reading the body", async () => {
  const fetcher: typeof fetch = async (_input, init) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        init?.signal?.addEventListener(
          "abort",
          () => controller.error(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      },
    });
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new DeckApiClient(
    "streamdeck",
    "http://127.0.0.1:8765",
    fetcher,
    10,
  );

  await assert.rejects(client.snapshot(), /request timed out/);
});

test("surface renders once and activates the button at its position", async () => {
  const activated: Array<[string, number]> = [];
  const api = {
    async snapshot(): Promise<DeckSnapshot> {
      return snapshot(2);
    },
    async activate(
      buttonId: string,
      revision: number,
    ): Promise<DeckActivationResponse> {
      activated.push([buttonId, revision]);
      return {
        accepted: true,
        action: "focus_session",
        snapshot: snapshot(3),
      };
    },
  };
  const images: string[] = [];
  const feedback: string[] = [];
  const key: KeyPort = {
    id: "key-1",
    row: 0,
    column: 0,
    async setImage(image) {
      images.push(image);
    },
    async showAlert() {
      feedback.push("alert");
    },
  };
  const surface = new StreamDeckSurface(
    api,
    {
      error(message) {
        assert.fail(message);
      },
    },
    0,
  );

  surface.register(key);
  await new Promise((resolve) => setTimeout(resolve, 10));
  const renderedAfterPoll = images.length;
  await surface.refreshNow();
  assert.equal(images.length, renderedAfterPoll);

  await surface.activate("key-1");

  assert.deepEqual(activated, [["session:0", 2]]);
  assert.deepEqual(feedback, []);
  assert.ok(
    images.includes("static/imgs/actions/key/success.png"),
    "custom success logo should replace native showOk feedback",
  );
  assert.match(decodeSvg(images.at(-1)!), /Agent 0/);
  surface.unregister("key-1");
});

test("surface rejects stale responses but accepts a newer service epoch", async () => {
  const snapshots = [
    snapshotWith(5, 200, "Current"),
    snapshotWith(4, 100, "Stale"),
    snapshotWith(1, 300, "Restarted"),
  ];
  const images: string[] = [];
  const surface = new StreamDeckSurface(
    {
      async snapshot() {
        return snapshots.shift()!;
      },
      async activate() {
        throw new Error("not used");
      },
    },
    { error: assert.fail },
    0,
  );
  const key = recordingKey(images, []);

  surface.register(key);
  await new Promise((resolve) => setTimeout(resolve, 10));
  await surface.refreshNow();
  assert.doesNotMatch(decodeSvg(images.at(-1)!), /Stale/);

  await surface.refreshNow();
  assert.match(decodeSvg(images.at(-1)!), /Restarted/);
  surface.unregister(key.id);
});

test("surface renders offline and recovers after service failure", async () => {
  let available = false;
  const images: string[] = [];
  const errors: string[] = [];
  const surface = new StreamDeckSurface(
    {
      async snapshot() {
        if (!available) throw new Error("connection refused");
        return snapshot(1);
      },
      async activate() {
        throw new Error("not used");
      },
    },
    { error: (message) => errors.push(message) },
  );
  const key = recordingKey(images, []);

  surface.register(key);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.match(decodeSvg(images.at(-1)!), /Offline/);
  assert.deepEqual(errors, ["connection refused"]);

  available = true;
  await surface.refreshNow();
  assert.match(decodeSvg(images.at(-1)!), /Agent 0/);
  surface.unregister(key.id);
});

test("surface serializes key activations", async () => {
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let activations = 0;
  const feedback: string[] = [];
  const surface = new StreamDeckSurface(
    {
      async snapshot() {
        return snapshot(1);
      },
      async activate() {
        activations += 1;
        await pending;
        return { accepted: true, action: "focus_session" };
      },
    },
    { error: assert.fail },
    0,
  );
  const key = recordingKey([], feedback);
  surface.register(key);
  await new Promise((resolve) => setTimeout(resolve, 10));

  const first = surface.activate(key.id);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await surface.activate(key.id);
  release!();
  await first;

  assert.equal(activations, 1);
  assert.deepEqual(feedback, ["alert"]);
  surface.unregister(key.id);
});

function snapshot(revision: number): DeckSnapshot {
  return snapshotWith(revision, Date.now(), "Agent 0");
}

function snapshotWith(
  revision: number,
  observedAtMs: number,
  firstLabel: string,
): DeckSnapshot {
  return {
    revision,
    observed_at_ms: observedAtMs,
    source: "test",
    read_only: true,
    selected_session_id: "session-0",
    page: 1,
    page_count: 1,
    has_previous: false,
    has_next: false,
    buttons: Array.from({ length: 15 }, (_, position) => ({
      ...buttonAt(position),
      ...(position === 0 ? { label: firstLabel } : {}),
    })),
  };
}

function recordingKey(images: string[], feedback: string[]): KeyPort {
  return {
    id: "key-1",
    row: 0,
    column: 0,
    async setImage(image) {
      images.push(image);
    },
    async showAlert() {
      feedback.push("alert");
    },
  };
}

function decodeSvg(image: string): string {
  const prefix = "data:image/svg+xml;base64,";
  assert.ok(image.startsWith(prefix));
  return Buffer.from(image.slice(prefix.length), "base64").toString("utf8");
}

function buttonAt(position: number): DeckButton {
  if (position < 10) {
    return {
      id: `session:${position}`,
      position,
      kind: "session",
      label: `Agent ${position}`,
      detail: "",
      icon: "cursor",
      color: position === 0 ? "working" : "idle",
      selected: position === 0,
      enabled: true,
      confidence: "observed",
      provider_id: "cursor",
      session_id: `session-${position}`,
    };
  }
  return {
    id: `control:${position}`,
    position,
    kind: "control",
    label: position === 14 ? "New" : "Refresh",
    detail: "",
    icon: position === 14 ? "plus" : "arrows-clockwise",
    color: "control",
    selected: false,
    enabled: position === 10 || position === 14,
    confidence: "observed",
    provider_id: position === 14 ? "cursor" : null,
    action: position === 14 ? "new_session" : "refresh_sessions",
  };
}
