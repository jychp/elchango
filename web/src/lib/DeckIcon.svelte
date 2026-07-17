<script lang="ts">
  import type { DeckIconName } from './contracts'

  interface Props {
    name: DeckIconName
  }

  let { name }: Props = $props()
</script>

<svg viewBox="0 0 24 24" aria-hidden="true">
  {#if name === 'repo'}
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
    <path d="M4 5.5v15M8 7h7" />
  {:else if name === 'terminal'}
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="m7 9 3 3-3 3M13 15h4" />
  {:else if name === 'plus'}
    <path d="M12 5v14M5 12h14" />
  {:else if name === 'arrow-left'}
    <path d="m15 5-7 7 7 7" />
  {:else if name === 'arrow-right'}
    <path d="m9 5 7 7-7 7" />
  {:else}
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v5M12 16.5v.5" />
  {/if}
</svg>

<style>
  svg {
    display: block;
    width: 100%;
    height: 100%;
    fill: none;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 1.7;
  }
</style>
