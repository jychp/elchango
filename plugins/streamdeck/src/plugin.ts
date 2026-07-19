import streamDeck, { action, SingletonAction } from "@elgato/streamdeck";
import type {
  KeyDownEvent,
  KeyUpEvent,
  WillAppearEvent,
  WillDisappearEvent,
} from "@elgato/streamdeck";

import { DeckApiClient } from "./client.js";
import { KeyPressController } from "./key-press.js";
import { StreamDeckSurface } from "./surface.js";

const ACTION_UUID = "com.jychp.elchango.key";
const surface = new StreamDeckSurface(new DeckApiClient("streamdeck"), {
  error(message) {
    streamDeck.logger.error(message);
  },
});
const keyPresses = new KeyPressController(
  (keyId) => surface.activate(keyId),
  (keyId) => surface.longPress(keyId),
);

@action({ UUID: ACTION_UUID })
class DeckKeyAction extends SingletonAction {
  override onWillAppear(event: WillAppearEvent): void {
    if (!event.action.isKey()) return;
    const action = event.action;
    const coordinates = action.coordinates;
    if (!coordinates) return;
    surface.register({
      id: action.id,
      row: coordinates.row,
      column: coordinates.column,
      setImage: (image) => action.setImage(image),
      showAlert: () => action.showAlert(),
    });
  }

  override onWillDisappear(event: WillDisappearEvent): void {
    keyPresses.cancel(event.action.id);
    surface.unregister(event.action.id);
  }

  override onKeyDown(event: KeyDownEvent): void {
    keyPresses.keyDown(event.action.id);
  }

  override onKeyUp(event: KeyUpEvent): Promise<void> {
    return keyPresses.keyUp(event.action.id);
  }
}

streamDeck.actions.registerAction(new DeckKeyAction());
await streamDeck.connect();
