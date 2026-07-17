<script lang="ts">
  import DeckIcon from './DeckIcon.svelte'
  import type { DeckButton } from './contracts'

  interface Props {
    button: DeckButton | null
    slot: number
    busy?: boolean
    onactivate?: () => void
  }

  let { button, slot, busy = false, onactivate }: Props = $props()

  const isBlank = $derived(
    button !== null && !button.enabled && !button.label && !button.detail,
  )

  const accessibleName = $derived(
    button
      ? [button.label, button.detail].filter(Boolean).join(', ') || `Empty key ${slot + 1}`
      : `Unavailable key ${slot + 1}`,
  )

  const actionable = $derived(button?.enabled === true && onactivate !== undefined)

  function handleActivate(): void {
    if (actionable) onactivate?.()
  }
</script>

<button
  type="button"
  class={[
    'deck-key',
    button && `deck-key--${button.color}`,
    button?.selected && 'deck-key--selected',
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
  onclick={handleActivate}
>
  {#if button && !isBlank}
    <span class="deck-key__topline">
      <span class="deck-key__icon"><DeckIcon name={button.icon} /></span>
      {#if button.kind === 'session'}
        <span class="deck-key__signal" aria-hidden="true"></span>
      {/if}
    </span>
    <span class="deck-key__copy">
      <span class="deck-key__label">{button.label}</span>
      {#if button.detail}
        <span class="deck-key__detail">{button.detail}</span>
      {/if}
    </span>
  {/if}
</button>

<style>
  .deck-key {
    position: relative;
    display: flex;
    aspect-ratio: 1;
    min-width: 0;
    flex-direction: column;
    justify-content: space-between;
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
    text-align: left;
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

  .deck-key--selected {
    border-color: #dce3e8;
    box-shadow:
      inset 0 0 0 2px #0e1012,
      0 0 0 2px #dce3e8,
      0 2px 0 #050607;
  }

  .deck-key--idle {
    --signal: #77818b;
    --icon: #aeb7bf;
  }

  .deck-key--working {
    --signal: #4ba3e3;
    --icon: #78bced;
  }

  .deck-key--waiting {
    --signal: #e4a83c;
    --icon: #edbd67;
  }

  .deck-key--done {
    --signal: #57b77a;
    --icon: #7ac994;
  }

  .deck-key--error {
    --signal: #de5c58;
    --icon: #e77b77;
  }

  .deck-key--unknown {
    --signal: #8b7da0;
    --icon: #afa2c2;
  }

  .deck-key--control {
    --signal: #adb4ba;
    --icon: #e0e4e7;
    background: #1c1f22;
  }

  .deck-key--vacant {
    background: #111315;
    border-color: #292c30;
  }

  .deck-key__topline {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
  }

  .deck-key__icon {
    width: clamp(1.1rem, 2.3vw, 1.75rem);
    height: clamp(1.1rem, 2.3vw, 1.75rem);
    color: var(--icon, #8e969d);
  }

  .deck-key__signal {
    width: 0.45rem;
    height: 0.45rem;
    margin-top: 0.15rem;
    border-radius: 50%;
    background: var(--signal, #77818b);
  }

  .deck-key__copy {
    display: grid;
    min-width: 0;
    gap: 0.15rem;
  }

  .deck-key__label,
  .deck-key__detail {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .deck-key__label {
    color: #f2f3f4;
    font-size: clamp(0.62rem, 1.4vw, 0.87rem);
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .deck-key__detail {
    color: #8f979f;
    font-family: var(--font-mono);
    font-size: clamp(0.48rem, 1vw, 0.66rem);
    letter-spacing: 0.01em;
  }

  @media (prefers-reduced-motion: reduce) {
    .deck-key {
      transition: none;
    }
  }
</style>
