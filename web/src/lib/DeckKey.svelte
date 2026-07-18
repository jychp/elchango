<script lang="ts">
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
  let pointerStartedAt = 0
  let suppressClickUntil = 0
  let keyboardKey: 'Enter' | ' ' | null = null

  const isBlank = $derived(
    button !== null && !button.enabled && !button.label && !button.detail,
  )

  const accessibleName = $derived(
    button
      ? [button.label, button.detail].filter(Boolean).join(', ') || `Empty key ${slot + 1}`
      : `Unavailable key ${slot + 1}`,
  )

  const shortActionEligible = $derived(button?.enabled === true && onactivate !== undefined)
  const longPressEligible = $derived(button !== null && onlongpress !== undefined)
  const actionable = $derived(shortActionEligible || longPressEligible)

  function activate(): void {
    if (shortActionEligible) onactivate?.()
  }

  function longPress(): void {
    if (longPressEligible) onlongpress?.()
  }

  function resetPointer(): void {
    activePointerId = null
    pointerStartedAt = 0
  }

  function handlePointerDown(event: PointerEvent): void {
    if (!actionable || !event.isPrimary || event.button !== 0 || activePointerId !== null) return

    const target = event.currentTarget as HTMLButtonElement
    activePointerId = event.pointerId
    pointerStartedAt = performance.now()
    target.setPointerCapture(event.pointerId)
  }

  function handlePointerUp(event: PointerEvent): void {
    if (event.pointerId !== activePointerId) return

    const duration = performance.now() - pointerStartedAt
    suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
    resetPointer()

    const target = event.currentTarget as HTMLButtonElement
    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId)
    }

    if (duration >= LONG_PRESS_MS && longPressEligible) longPress()
    else activate()
  }

  function handlePointerCancel(event: PointerEvent): void {
    if (event.pointerId !== activePointerId) return

    suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
    resetPointer()
  }

  function handleClick(event: MouseEvent): void {
    if (performance.now() <= suppressClickUntil) {
      event.preventDefault()
      return
    }

    activate()
  }

  function handleKeyDown(event: KeyboardEvent): void {
    if (!shortActionEligible || (event.key !== 'Enter' && event.key !== ' ')) return

    event.preventDefault()
    if (event.repeat || keyboardKey !== null) return
    keyboardKey = event.key
  }

  function handleKeyUp(event: KeyboardEvent): void {
    if (event.key !== keyboardKey) return

    event.preventDefault()
    keyboardKey = null
    suppressClickUntil = performance.now() + SYNTHETIC_CLICK_WINDOW_MS
    activate()
  }

  function handleBlur(): void {
    keyboardKey = null
  }
</script>

<button
  type="button"
  class={[
    'deck-key',
    button?.kind === 'session' && `deck-key--${button.color}`,
    (button?.kind === 'control' || (button?.kind === 'empty' && button.enabled)) &&
      'deck-key--control',
    !button && 'deck-key--vacant',
    isBlank && 'deck-key--vacant',
    (!button || !button.enabled) && 'deck-key--disabled',
    actionable && 'deck-key--actionable',
  ]}
  aria-disabled={!button || !button.enabled}
  aria-busy={busy || undefined}
  aria-label={accessibleName}
  aria-pressed={button?.kind === 'session' ? button.selected : undefined}
  data-confidence={button?.confidence}
  data-disabled={!button || !button.enabled}
  onpointerdown={handlePointerDown}
  onpointerup={handlePointerUp}
  onpointercancel={handlePointerCancel}
  onclick={handleClick}
  onkeydown={handleKeyDown}
  onkeyup={handleKeyUp}
  onblur={handleBlur}
>
  {#if button && !isBlank}
    <span class="deck-key__icon"><DeckIcon name={button.icon} /></span>
    <span class="deck-key__label">{button.label}</span>
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

  @media (prefers-reduced-motion: reduce) {
    .deck-key {
      transition: none;
    }
  }
</style>
