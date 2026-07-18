export type DeckButtonKind = "session" | "control" | "empty";
export type DeckButtonColor =
  | "idle"
  | "working"
  | "waiting"
  | "done"
  | "error"
  | "unknown"
  | "control";
export type DeckIconName =
  | "cursor"
  | "claude"
  | "plus"
  | "arrow-left"
  | "arrow-right"
  | "arrows-clockwise";
export type DeckButtonConfidence =
  | "observed"
  | "candidate"
  | "persisted"
  | "unknown";
export type DeckAction =
  | "new_session"
  | "refresh_sessions"
  | "previous_page"
  | "next_page"
  | "focus_session";

export interface DeckButton {
  id: string;
  position: number;
  kind: DeckButtonKind;
  label: string;
  detail: string;
  icon: DeckIconName;
  color: DeckButtonColor;
  selected: boolean;
  enabled: boolean;
  confidence: DeckButtonConfidence;
  provider_id: string | null;
  session_id?: string | null;
  action?: DeckAction | null;
}

export interface DeckSnapshot {
  revision: number;
  observed_at_ms: number;
  source: string;
  read_only: boolean;
  selected_session_id: string | null;
  page: number;
  page_count: number;
  has_previous: boolean;
  has_next: boolean;
  buttons: DeckButton[];
}

export interface DeckActivationResponse {
  accepted: boolean;
  action: DeckAction;
  snapshot?: DeckSnapshot;
  focus?: Record<string, unknown>;
  launch?: Record<string, unknown>;
}

const BUTTON_KINDS = new Set<DeckButtonKind>([
  "session",
  "control",
  "empty",
]);
const BUTTON_COLORS = new Set<DeckButtonColor>([
  "idle",
  "working",
  "waiting",
  "done",
  "error",
  "unknown",
  "control",
]);
const ICON_NAMES = new Set<DeckIconName>([
  "cursor",
  "claude",
  "plus",
  "arrow-left",
  "arrow-right",
  "arrows-clockwise",
]);
const CONFIDENCE_VALUES = new Set<DeckButtonConfidence>([
  "observed",
  "candidate",
  "persisted",
  "unknown",
]);
const ACTION_VALUES = new Set<DeckAction>([
  "new_session",
  "refresh_sessions",
  "previous_page",
  "next_page",
  "focus_session",
]);

export function parseDeckSnapshot(value: unknown): DeckSnapshot {
  const snapshot = objectValue(value, "snapshot");
  const buttons = snapshot.buttons;
  if (!Array.isArray(buttons) || buttons.length !== 15) {
    throw new Error("snapshot must contain exactly 15 buttons");
  }
  const parsedButtons = buttons.map(parseDeckButton);
  const positions = new Set(parsedButtons.map((button) => button.position));
  if (
    positions.size !== 15 ||
    parsedButtons.some((button) => button.position < 0 || button.position >= 15)
  ) {
    throw new Error(
      "snapshot buttons must occupy each position from 0 through 14 exactly once",
    );
  }

  return {
    revision: integerValue(snapshot.revision, "snapshot.revision"),
    observed_at_ms: integerValue(
      snapshot.observed_at_ms,
      "snapshot.observed_at_ms",
    ),
    source: stringValue(snapshot.source, "snapshot.source"),
    read_only: booleanValue(snapshot.read_only, "snapshot.read_only"),
    selected_session_id: nullableString(
      snapshot.selected_session_id,
      "snapshot.selected_session_id",
    ),
    page: integerValue(snapshot.page, "snapshot.page"),
    page_count: integerValue(snapshot.page_count, "snapshot.page_count"),
    has_previous: booleanValue(
      snapshot.has_previous,
      "snapshot.has_previous",
    ),
    has_next: booleanValue(snapshot.has_next, "snapshot.has_next"),
    buttons: parsedButtons,
  };
}

export function parseActivationResponse(
  value: unknown,
): DeckActivationResponse {
  const response = objectValue(value, "activation response");
  const action = stringValue(response.action, "activation response.action");
  if (!ACTION_VALUES.has(action as DeckAction)) {
    throw new Error("activation response.action is unsupported");
  }
  const snapshot =
    response.snapshot === undefined
      ? undefined
      : parseDeckSnapshot(response.snapshot);

  return {
    accepted: booleanValue(
      response.accepted,
      "activation response.accepted",
    ),
    action: action as DeckAction,
    ...(snapshot === undefined ? {} : { snapshot }),
    ...(isRecord(response.focus) ? { focus: response.focus } : {}),
    ...(isRecord(response.launch) ? { launch: response.launch } : {}),
  };
}

function parseDeckButton(value: unknown, index: number): DeckButton {
  const button = objectValue(value, `button ${index}`);
  const kind = stringValue(button.kind, `button ${index}.kind`);
  const color = stringValue(button.color, `button ${index}.color`);
  const icon = stringValue(button.icon, `button ${index}.icon`);
  const confidence = stringValue(
    button.confidence,
    `button ${index}.confidence`,
  );
  if (!BUTTON_KINDS.has(kind as DeckButtonKind)) {
    throw new Error(`button ${index}.kind is unsupported`);
  }
  if (!BUTTON_COLORS.has(color as DeckButtonColor)) {
    throw new Error(`button ${index}.color is unsupported`);
  }
  if (!ICON_NAMES.has(icon as DeckIconName)) {
    throw new Error(`button ${index}.icon is unsupported`);
  }
  if (!CONFIDENCE_VALUES.has(confidence as DeckButtonConfidence)) {
    throw new Error(`button ${index}.confidence is unsupported`);
  }

  return {
    id: stringValue(button.id, `button ${index}.id`),
    position: integerValue(button.position, `button ${index}.position`),
    kind: kind as DeckButtonKind,
    label: stringValue(button.label, `button ${index}.label`),
    detail: stringValue(button.detail, `button ${index}.detail`),
    icon: icon as DeckIconName,
    color: color as DeckButtonColor,
    selected: booleanValue(button.selected, `button ${index}.selected`),
    enabled: booleanValue(button.enabled, `button ${index}.enabled`),
    confidence: confidence as DeckButtonConfidence,
    provider_id: nullableString(
      button.provider_id,
      `button ${index}.provider_id`,
    ),
    ...(button.session_id === undefined
      ? {}
      : {
          session_id: nullableString(
            button.session_id,
            `button ${index}.session_id`,
          ),
        }),
    ...(button.action === undefined
      ? {}
      : {
          action: parseOptionalAction(button.action, index),
        }),
  };
}

function parseOptionalAction(
  value: unknown,
  index: number,
): DeckAction | null {
  if (value === null) return null;
  const action = stringValue(value, `button ${index}.action`);
  if (!ACTION_VALUES.has(action as DeckAction)) {
    throw new Error(`button ${index}.action is unsupported`);
  }
  return action as DeckAction;
}

function objectValue(
  value: unknown,
  field: string,
): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${field} must be an object`);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field} must be a string`);
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  return stringValue(value, field);
}

function integerValue(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value)) {
    throw new Error(`${field} must be an integer`);
  }
  return value as number;
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${field} must be a boolean`);
  }
  return value;
}
