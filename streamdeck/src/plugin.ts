import streamDeck, {
  action,
  KeyDownEvent,
  SingletonAction,
  WillAppearEvent,
  WillDisappearEvent,
} from "@elgato/streamdeck";

import { DeckApiClient } from "./client.js";
import { StreamDeckSurface } from "./surface.js";

const ACTION_UUID = "com.jychp.elchango.key";
const surface = new StreamDeckSurface(
  new DeckApiClient("streamdeck"),
  {
    error(message) {
      streamDeck.logger.error(message);
    },
  },
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
    surface.unregister(event.action.id);
  }

  override onKeyDown(event: KeyDownEvent): Promise<void> {
    return surface.activate(event.action.id);
  }
}

streamDeck.actions.registerAction(new DeckKeyAction());
await streamDeck.connect();
