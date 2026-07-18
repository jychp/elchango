import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { strToU8, zipSync, type Zippable } from "fflate";
import { v5 as uuidv5 } from "uuid";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(
  root,
  "com.jychp.elchango.sdPlugin",
  "elchango-mk2.streamDeckProfile",
);
const profileID = uuidv5("com.jychp.elchango.profile", uuidv5.DNS);
const defaultPageID = uuidv5(
  "com.jychp.elchango.profile.default",
  uuidv5.DNS,
);
const deckPageID = uuidv5(
  "com.jychp.elchango.profile.deck",
  uuidv5.DNS,
);
const actionUUID = "com.jychp.elchango.key";
const archiveRoot = `${profileID}.sdProfile`;
const archiveTimestamp = new Date("2026-01-01T00:00:00Z");

const actions = Object.fromEntries(
  Array.from({ length: 3 }, (_, row) =>
    Array.from({ length: 5 }, (_, column) => [
      `${column},${row}`,
      action(column, row),
    ]),
  ).flat(),
);

const files: Zippable = {};
addJSON(files, `${archiveRoot}/manifest.json`, {
  AppIdentifier: "*",
  Device: { Model: "20GBA9901", UUID: "" },
  Name: "elChango",
  Pages: {
    Current: deckPageID,
    Default: defaultPageID,
    Pages: [deckPageID],
  },
  Version: "3.0",
});
addJSON(
  files,
  `${archiveRoot}/Profiles/${defaultPageID.toUpperCase()}/manifest.json`,
  pageManifest({}),
);
addJSON(
  files,
  `${archiveRoot}/Profiles/${deckPageID.toUpperCase()}/manifest.json`,
  pageManifest(actions),
);

writeFileSync(output, zipSync(files, { level: 0 }));

function action(column: number, row: number): Record<string, unknown> {
  return {
    ActionID: uuidv5(
      `com.jychp.elchango.profile.action.${column}.${row}`,
      uuidv5.DNS,
    ),
    LinkedTitle: true,
    Name: "Deck Key",
    Plugin: {
      Name: "elChango",
      UUID: "com.jychp.elchango",
      Version: "0.2.0.0",
    },
    Resources: null,
    Settings: {},
    State: 0,
    States: [
      {
        FontFamily: "",
        FontSize: 9,
        FontStyle: "",
        FontUnderline: false,
        OutlineThickness: 2,
        ShowTitle: false,
        TitleAlignment: "middle",
        TitleColor: "#ffffff",
      },
    ],
    UUID: actionUUID,
  };
}

function pageManifest(
  pageActions: Record<string, unknown>,
): Record<string, unknown> {
  return {
    Controllers: [{ Actions: pageActions, Type: "Keypad" }],
    Icon: "",
    Name: "",
  };
}

function addJSON(
  files: Zippable,
  path: string,
  value: unknown,
): void {
  files[path] = [
    strToU8(stableJSON(value)),
    { level: 0, mtime: archiveTimestamp },
  ];
}

function stableJSON(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJSON).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(
      ([left], [right]) => left.localeCompare(right),
    );
    return `{${entries
      .map(([key, entry]) => `${JSON.stringify(key)}:${stableJSON(entry)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}
