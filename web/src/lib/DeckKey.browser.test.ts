import { flushSync, mount, unmount } from 'svelte'
import { afterEach, describe, expect, test, vi } from 'vitest'
import DeckKey from './DeckKey.svelte'
import type { DeckButton } from './contracts'

const sessionButton: DeckButton = {
  id: 'cursor:session-1',
  position: 0,
  kind: 'session',
  label: 'Safety work',
  detail: 'Cursor',
  icon: 'cursor',
  color: 'idle',
  selected: true,
  enabled: true,
  confidence: 'observed',
  provider_id: 'cursor',
  session_id: 'session-1',
  action: null,
}

afterEach(() => {
  vi.useRealTimers()
  document.body.replaceChildren()
})

describe('DeckKey', () => {
  test('opens a long-press picker from the keyboard when the short action is disabled', () => {
    vi.useFakeTimers()
    const onactivate = vi.fn()
    const onlongpress = vi.fn()
    const component = mount(DeckKey, {
      target: document.body,
      props: {
        button: { ...sessionButton, enabled: false },
        slot: 0,
        onactivate,
        onlongpress,
      },
    })
    const key = requiredKey()

    expect(key.getAttribute('aria-disabled')).toBe('false')
    expect(key.getAttribute('aria-describedby')).toBe('deck-key-help-0')
    expect(key.textContent).toContain(
      'Short action unavailable. Press and hold Enter or Space to open customization.',
    )

    key.dispatchEvent(
      new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }),
    )
    vi.advanceTimersByTime(650)
    flushSync()
    key.dispatchEvent(
      new KeyboardEvent('keyup', { bubbles: true, key: 'Enter' }),
    )

    expect(onlongpress).toHaveBeenCalledTimes(1)
    expect(onactivate).not.toHaveBeenCalled()
    unmount(component)
  })

  test('activates only for an inside pointer release', () => {
    const onactivate = vi.fn()
    const component = mount(DeckKey, {
      target: document.body,
      props: { button: sessionButton, slot: 0, onactivate },
    })
    const key = requiredKey()
    installPointerCaptureStub(key)
    const rect = key.getBoundingClientRect()

    dispatchPointer(key, 'pointerdown', rect.left + 5, rect.top + 5)
    dispatchPointer(key, 'pointerup', rect.left + 5, rect.top + 5)
    expect(onactivate).toHaveBeenCalledTimes(1)

    dispatchPointer(key, 'pointerdown', rect.left + 5, rect.top + 5, 2)
    dispatchPointer(key, 'pointermove', rect.right + 5, rect.bottom + 5, 2)
    dispatchPointer(key, 'pointerup', rect.left + 5, rect.top + 5, 2)
    expect(onactivate).toHaveBeenCalledTimes(1)
    unmount(component)
  })

  test('renders the Codex Desktop provider icon', () => {
    const component = mount(DeckKey, {
      target: document.body,
      props: {
        button: {
          ...sessionButton,
          id: 'codex:thread-1',
          icon: 'codex',
          detail: 'Codex',
          provider_id: 'codex',
          session_id: 'thread-1',
        },
        slot: 0,
      },
    })
    const key = requiredKey()
    const path = key.querySelector('svg path')
    expect(path?.getAttribute('d')).toMatch(/^M22\.2819 9\.8211/)
    unmount(component)
  })

  test('cancels pointer cancellation and lost capture', () => {
    const onactivate = vi.fn()
    const canceledComponent = mount(DeckKey, {
      target: document.body,
      props: { button: sessionButton, slot: 0, onactivate },
    })
    const key = requiredKey()
    installPointerCaptureStub(key)
    const rect = key.getBoundingClientRect()

    dispatchPointer(key, 'pointerdown', rect.left + 5, rect.top + 5)
    dispatchPointerCancellation(key, 'pointercancel', 1)
    expect(onactivate).not.toHaveBeenCalled()
    unmount(canceledComponent)

    const lostCaptureComponent = mount(DeckKey, {
      target: document.body,
      props: { button: sessionButton, slot: 0, onactivate },
    })
    const nextKey = requiredKey()
    installPointerCaptureStub(nextKey)
    const nextRect = nextKey.getBoundingClientRect()

    dispatchPointer(
      nextKey,
      'pointerdown',
      nextRect.left + 5,
      nextRect.top + 5,
      2,
    )
    dispatchPointerCancellation(nextKey, 'lostpointercapture', 2)
    expect(onactivate).not.toHaveBeenCalled()
    unmount(lostCaptureComponent)
  })

  test('suppresses the second pointer activation and synthetic clicks in a double click', () => {
    const onactivate = vi.fn()
    const component = mount(DeckKey, {
      target: document.body,
      props: { button: sessionButton, slot: 0, onactivate },
    })
    const key = requiredKey()
    installPointerCaptureStub(key)
    const rect = key.getBoundingClientRect()

    dispatchPointer(key, 'pointerdown', rect.left + 5, rect.top + 5)
    dispatchPointer(key, 'pointerup', rect.left + 5, rect.top + 5)
    key.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
    dispatchPointer(key, 'pointerdown', rect.left + 5, rect.top + 5, 2)
    dispatchPointer(key, 'pointerup', rect.left + 5, rect.top + 5, 2)
    key.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 }))
    key.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 }))

    expect(onactivate).toHaveBeenCalledTimes(1)
    unmount(component)
  })
})

function requiredKey(): HTMLButtonElement {
  const key = document.body.querySelector<HTMLButtonElement>('button')
  if (!key) throw new Error('Deck key was not rendered')
  return key
}

function dispatchPointer(
  target: HTMLElement,
  type: string,
  clientX: number,
  clientY: number,
  pointerId = 1,
): void {
  target.dispatchEvent(
    new PointerEvent(type, {
      bubbles: true,
      button: 0,
      clientX,
      clientY,
      isPrimary: true,
      pointerId,
    }),
  )
}

function dispatchPointerCancellation(
  target: HTMLElement,
  type: 'pointercancel' | 'lostpointercapture',
  pointerId: number,
): void {
  const event = new Event(type, { bubbles: true })
  Object.defineProperty(event, 'pointerId', { value: pointerId })
  target.dispatchEvent(event)
}

function installPointerCaptureStub(key: HTMLButtonElement): void {
  const capturedPointers = new Set<number>()
  key.setPointerCapture = (pointerId) => capturedPointers.add(pointerId)
  key.hasPointerCapture = (pointerId) => capturedPointers.has(pointerId)
  key.releasePointerCapture = (pointerId) => capturedPointers.delete(pointerId)
}
