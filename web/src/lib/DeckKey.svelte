<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity'
  import DeckIcon from './DeckIcon.svelte'
  import type { DeckButton } from './contracts'

  interface Props {
    button: DeckButton | null
    slot: number
    busy?: boolean
    onactivate?: () => void
    onlongpress?: () => void
  }

  let { button, slot, busy = false, onactivate, onlongpress }: Props = $props()

  const LONG_PRESS_MS = 650
  const SYNTHETIC_CLICK_WINDOW_MS = 500

  let activePointerId: number | null = null
  let activePointerTarget: HTMLButtonElement | null = null
  let pointerStartedAt = 0
  let suppressClickUntil = 0
  let lastPointerActivationAt = 0
  const canceledPointerIds = new SvelteSet<number>()
  let keyboardKey: 'Enter' | ' ' | null = null
  let keyboardLongPressTriggered = false
  let keyboardLongPressTimer: ReturnType<typeof setTimeout> | undefined

  const isBlank = $derived(
    button !== null && !button.enabled && !button.label && !button.detail,
  )

  const accessibleName = $derived(
    button
      ? [button.label, button.detail].filter(Boolean).join(', ') ||
          `Empty key ${slot + 1}`
      : `Unavailable key ${slot + 1}`,
  )

  const helpId = $derived(`deck-key-help-${slot}`)
  const shortActionEligible = $derived(
    !busy && button?.enabled === true && onactivate !== undefined,
  )
  const longPressEligible = $derived(
    !busy && button !== null && onlongpress !== undefined,
  )
  const actionable = $derived(shortActionEligible || longPressEligible)
  const interactionHelp = $derived(
    shortActionEligible && longPressEligible
      ? 'Press and release Enter or Space to activate. Press and hold to open customization.'
      : longPressEligible
        ? 'Short action unavailable. Press and hold Enter or Space to open customization.'
        : '',
  )

  function activate(): void {
    if (shortActionEligible) onactivate?.()
  }

  function longPress(): void {
    if (longPressEligible) onlongpress?.()
  }

  function resetPointer(): void {
    activePointerId = null
    activePointerTarget = null
    pointerStartedAt = 0
  }

  function isInside(target: HTMLButtonElement, event: PointerEvent): boolean {
    const rect = target.getBoundingClientRect()
    return (
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom
    )
  }

  function cancelPointer(event: PointerEvent): void {
    if (event.pointerId !== activePointerId) return

    const target = activePointerTarget
    canceledPointerIds.add(event.pointerId)
    suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
    resetPointer()

    if (target?.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId)
    }
  }

  function handlePointerDown(event: PointerEvent): void {
    if (
      !actionable ||
      !event.isPrimary ||
      event.button !== 0 ||
      activePointerId !== null
    )
      return

    const target = event.currentTarget as HTMLButtonElement
    canceledPointerIds.delete(event.pointerId)
    activePointerId = event.pointerId
    activePointerTarget = target
    pointerStartedAt = performance.now()
    target.setPointerCapture(event.pointerId)
  }

  function handlePointerUp(event: PointerEvent): void {
    if (canceledPointerIds.delete(event.pointerId)) return
    if (event.pointerId !== activePointerId) return

    const now = performance.now()
    const duration = now - pointerStartedAt
    const target = event.currentTarget as HTMLButtonElement
    const releasedInside = isInside(target, event)
    const duplicateActivation =
      lastPointerActivationAt !== 0 &&
      now - lastPointerActivationAt <= SYNTHETIC_CLICK_WINDOW_MS
    suppressClickUntil = now + SYNTHETIC_CLICK_WINDOW_MS
    resetPointer()

    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId)
    }

    if (!releasedInside || duplicateActivation) return

    if (duration >= LONG_PRESS_MS && longPressEligible) {
      lastPointerActivationAt = now
      longPress()
    } else if (shortActionEligible) {
      lastPointerActivationAt = now
      activate()
    }
  }

  function handlePointerMove(event: PointerEvent): void {
    if (
      event.pointerId === activePointerId &&
      !isInside(event.currentTarget as HTMLButtonElement, event)
    ) {
      cancelPointer(event)
    }
  }

  function handleClick(event: MouseEvent): void {
    if (
      event.detail > 0 ||
      (suppressClickUntil !== 0 && performance.now() <= suppressClickUntil)
    ) {
      event.preventDefault()
      return
    }

    activate()
  }

  function handleDoubleClick(event: MouseEvent): void {
    event.preventDefault()
  }

  function resetKeyboard(): void {
    if (keyboardLongPressTimer !== undefined)
      clearTimeout(keyboardLongPressTimer)
    keyboardLongPressTimer = undefined
    keyboardKey = null
    keyboardLongPressTriggered = false
  }

  function handleKeyDown(event: KeyboardEvent): void {
    if (!actionable || (event.key !== 'Enter' && event.key !== ' ')) return

    event.preventDefault()
    if (event.repeat || keyboardKey !== null) return
    keyboardKey = event.key
    keyboardLongPressTriggered = false

    if (longPressEligible) {
      keyboardLongPressTimer = setTimeout(() => {
        keyboardLongPressTimer = undefined
        keyboardLongPressTriggered = true
        suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
        longPress()
      }, LONG_PRESS_MS)
    }
  }

  function handleKeyUp(event: KeyboardEvent): void {
    if (event.key !== keyboardKey) return

    event.preventDefault()
    const shouldActivate = !keyboardLongPressTriggered && shortActionEligible
    resetKeyboard()
    suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
    if (shouldActivate) activate()
  }

  function handleBlur(): void {
    resetKeyboard()
  }
</script>

<svelte:window onpointercancel={cancelPointer} />

<button
  type="button"
  class={[
    'deck-key',
    button?.kind === 'session' && `deck-key--${button.color}`,
    (button?.kind === 'control' ||
      (button?.kind === 'empty' && button.enabled)) &&
      'deck-key--control',
    !button && 'deck-key--vacant',
    isBlank && 'deck-key--vacant',
    (!button || !button.enabled) && 'deck-key--disabled',
    actionable && 'deck-key--actionable',
  ]}
  aria-disabled={!actionable}
  aria-busy={busy || undefined}
  aria-describedby={longPressEligible ? helpId : undefined}
  aria-label={accessibleName}
  aria-pressed={button?.kind === 'session' ? button.selected : undefined}
  data-confidence={button?.confidence}
  data-disabled={!button || !button.enabled}
  onpointerdown={handlePointerDown}
  onpointermove={handlePointerMove}
  onpointerup={handlePointerUp}
  onpointercancel={cancelPointer}
  onlostpointercapture={cancelPointer}
  onclick={handleClick}
  ondblclick={handleDoubleClick}
  onkeydown={handleKeyDown}
  onkeyup={handleKeyUp}
  onblur={handleBlur}
>
  {#if button && !isBlank}
    <span class="deck-key__icon"><DeckIcon name={button.icon} /></span>
    <span class="deck-key__label">{button.label}</span>
  {/if}
  {#if longPressEligible}
    <span id={helpId} class="visually-hidden">{interactionHelp}</span>
  {/if}
</button>

<style>
  .deck-key {
    position: relative;
    display: flex;
    aspect-ratio: 1;
    min-width: 0;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: clamp(0.45rem, 1.2vw, 0.8rem);
    overflow: hidden;
    padding: clamp(0.45rem, 1.1vw, 0.75rem);
    border: 1px solid #3a3e44;
    border-radius: clamp(0.55rem, 1.2vw, 0.9rem);
    color: #d8dadd;
    background: #16181b;
    box-shadow:
      inset 0 0 0 2px #0e1012,
      0 2px 0 #050607;
    font: inherit;
    text-align: center;
    touch-action: manipulation;
    user-select: none;
    cursor: default;
    transition:
      border-color 120ms ease,
      box-shadow 120ms ease,
      transform 120ms ease;
  }

  .deck-key:focus-visible {
    z-index: 1;
    outline: 3px solid #f0b24a;
    outline-offset: 3px;
  }

  .deck-key--disabled {
    opacity: 0.54;
  }

  .deck-key--actionable {
    cursor: pointer;
  }

  .deck-key--actionable:active {
    transform: translateY(1px);
    box-shadow:
      inset 0 0 0 2px #0e1012,
      0 1px 0 #050607;
  }

  .deck-key--idle {
    --icon: #aeb7bf;
  }

  .deck-key--working {
    --icon: #78bced;
  }

  .deck-key--waiting {
    --icon: #edbd67;
  }

  .deck-key--done {
    --icon: #7ac994;
  }

  .deck-key--error {
    --icon: #e77b77;
  }

  .deck-key--unknown {
    --icon: #afa2c2;
  }

  .deck-key--control {
    --icon: #e0e4e7;
    background: #1c1f22;
  }

  .deck-key--vacant {
    background: #111315;
    border-color: #292c30;
  }

  .deck-key__icon {
    width: clamp(2rem, 5vw, 3.75rem);
    height: clamp(2rem, 5vw, 3.75rem);
    color: var(--icon, #8e969d);
  }

  .deck-key__label {
    width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #f2f3f4;
    font-size: clamp(0.62rem, 1.4vw, 0.87rem);
    font-weight: 650;
    letter-spacing: -0.01em;
    text-align: center;
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
  }

  @media (prefers-reduced-motion: reduce) {
    .deck-key {
      transition: none;
    }
  }
</style>
