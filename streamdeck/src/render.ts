import type { DeckButton, DeckIconName } from "./contracts.js";

const SIGNAL_COLORS = {
  idle: "#77818b",
  working: "#4ba3e3",
  waiting: "#e4a83c",
  done: "#57b77a",
  error: "#de5c58",
  unknown: "#8b7da0",
  control: "#adb4ba",
} as const;
const CONTROL_ICON_COLOR = "#e0e4e7";
const CURSOR_PATH =
  "M457.43,125.94L244.42,2.96c-6.84-3.95-15.28-3.95-22.12,0L9.3,125.94c-5.75,3.32-9.3,9.46-9.3,16.11v247.99c0,6.65,3.55,12.79,9.3,16.11l213.01,122.98c6.84,3.95,15.28,3.95,22.12,0l213.01-122.98c5.75-3.32,9.3-9.46,9.3-16.11v-247.99c0-6.65-3.55-12.79-9.3-16.11h-.01ZM444.05,151.99l-205.63,356.16c-1.39,2.4-5.06,1.42-5.06-1.36v-233.21c0-4.66-2.49-8.97-6.53-11.31L24.87,145.67c-2.4-1.39-1.42-5.06,1.36-5.06h411.26c5.84,0,9.49,6.33,6.57,11.39h-.01Z";

// Regular-weight paths from @phosphor-icons/core 2.1.1.
const PHOSPHOR_PATHS = {
  plus: "M224,128a8,8,0,0,1-8,8H136v80a8,8,0,0,1-16,0V136H40a8,8,0,0,1,0-16h80V40a8,8,0,0,1,16,0v80h80A8,8,0,0,1,224,128Z",
  "arrow-left":
    "M224,128a8,8,0,0,1-8,8H59.31l58.35,58.34a8,8,0,0,1-11.32,11.32l-72-72a8,8,0,0,1,0-11.32l72-72a8,8,0,0,1,11.32,11.32L59.31,120H216A8,8,0,0,1,224,128Z",
  "arrow-right":
    "M221.66,133.66l-72,72a8,8,0,0,1-11.32-11.32L196.69,136H40a8,8,0,0,1,0-16H196.69L138.34,61.66a8,8,0,0,1,11.32-11.32l72,72A8,8,0,0,1,221.66,133.66Z",
  "arrows-clockwise":
    "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z",
} as const;

export function renderButton(
  button: DeckButton,
  status: "ready" | "busy" | "error" = "ready",
): string {
  const blank = !button.enabled && !button.label && !button.detail;
  const background = blank ? "#111315" : "#16181b";
  const opacity = button.enabled ? 1 : 0.5;
  const feedbackColor =
    status === "error"
      ? SIGNAL_COLORS.error
      : status === "busy"
        ? SIGNAL_COLORS.working
        : undefined;
  const iconColor =
    feedbackColor ??
    (button.kind === "session"
      ? SIGNAL_COLORS[button.color]
      : CONTROL_ICON_COLOR);
  const label = escapeXml(truncate(button.label, 16));

  return svgDataUri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
  <rect width="144" height="144" fill="${background}"/>
  <g opacity="${opacity}">
    ${iconSvg(button.icon, iconColor)}
    ${label ? `<text x="72" y="126" text-anchor="middle" fill="#f2f3f4" font-family="-apple-system,system-ui,sans-serif" font-size="14" font-weight="650">${label}</text>` : ""}
  </g>
</svg>`);
}

export function renderOffline(): string {
  return svgDataUri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">
  <rect x="3" y="3" width="138" height="138" rx="15" fill="#111315" stroke="#3a3e44" stroke-width="2"/>
  <path d="M46 52l52 40M98 52L46 92" fill="none" stroke="#de5c58" stroke-width="7" stroke-linecap="round"/>
  <text x="72" y="116" text-anchor="middle" fill="#d8dadd" font-family="-apple-system,system-ui,sans-serif" font-size="14" font-weight="650">Offline</text>
</svg>`);
}

export function positionFromCoordinates(row: number, column: number): number {
  if (
    !Number.isInteger(row) ||
    !Number.isInteger(column) ||
    row < 0 ||
    row >= 3 ||
    column < 0 ||
    column >= 5
  ) {
    throw new Error(`unsupported Stream Deck coordinates: ${row},${column}`);
  }
  return row * 5 + column;
}

function iconSvg(icon: DeckIconName, color: string): string {
  if (icon === "cursor") {
    return `<g transform="translate(42 20) scale(0.128553 0.135316)" fill="${color}"><path d="${CURSOR_PATH}"/></g>`;
  }
  return `<g transform="translate(36 18) scale(0.28125)" fill="${color}"><path d="${PHOSPHOR_PATHS[icon]}"/></g>`;
}

function truncate(value: string, maximum: number): string {
  return value.length <= maximum
    ? value
    : `${value.slice(0, maximum - 1)}…`;
}

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function svgDataUri(svg: string): string {
  return `data:image/svg+xml;base64,${Buffer.from(svg, "utf8").toString("base64")}`;
}
