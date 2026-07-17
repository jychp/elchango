<script lang="ts">
  import { onMount } from 'svelte'
  import DeckKey from './lib/DeckKey.svelte'
  import type { DeckButton, DeckIntentResponse, DeckSnapshot } from './lib/contracts'

  type ConnectionState = 'connecting' | 'connected' | 'stale' | 'error'

  const POLL_INTERVAL_MS = 1_000
  const STALE_AFTER_MS = 3_000
  const SLOT_COUNT = 15

  let snapshot = $state.raw<DeckSnapshot | null>(null)
  let connectionState = $state<ConnectionState>('connecting')
  let errorMessage = $state('')
  let pendingButtonId = $state<string | null>(null)
  let deckActionError = $state('')

  const slots = $derived.by((): Array<DeckButton | null> => {
    const positionOffset = snapshot?.buttons.some((button) => button.position === 0) ? 0 : 1

    return Array.from(
      { length: SLOT_COUNT },
      (_, index) =>
        snapshot?.buttons.find((button) => button.position === index + positionOffset) ?? null,
    )
  })

  const statusLabel = $derived(
    connectionState === 'connecting'
      ? 'Connecting'
      : connectionState === 'connected'
        ? 'Connected'
        : connectionState === 'stale'
          ? 'Stale'
          : 'Connection error',
  )

  function installSnapshot(nextSnapshot: DeckSnapshot): void {
    if (
      !Array.isArray(nextSnapshot.buttons) ||
      typeof nextSnapshot.observed_at_ms !== 'number' ||
      typeof nextSnapshot.page !== 'number' ||
      typeof nextSnapshot.page_count !== 'number' ||
      typeof nextSnapshot.has_previous !== 'boolean' ||
      typeof nextSnapshot.has_next !== 'boolean'
    ) {
      throw new Error('Snapshot response is invalid')
    }

    if (snapshot && nextSnapshot.revision < snapshot.revision) return

    snapshot = nextSnapshot
    connectionState =
      Date.now() - nextSnapshot.observed_at_ms > STALE_AFTER_MS ? 'stale' : 'connected'
    errorMessage = ''
  }

  async function responseErrorMessage(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as unknown

      if (body && typeof body === 'object') {
        const { error, message } = body as Record<string, unknown>

        if (typeof error === 'string' && error.trim()) return error
        if (typeof message === 'string' && message.trim()) return message
      }
    } catch {
      // Use the status fallback when the response body is not JSON.
    }

    return `Deck action failed (${response.status})`
  }

  async function activateButton(button: DeckButton | null): Promise<void> {
    const currentSnapshot = snapshot

    if (
      pendingButtonId !== null ||
      !currentSnapshot ||
      !button ||
      !button.enabled
    ) {
      return
    }

    pendingButtonId = button.id
    deckActionError = ''

    try {
      let response: Response

      if (button.kind === 'session') {
        if (typeof button.session_id !== 'string' || !button.session_id) {
          throw new Error('Session button is missing its session identifier')
        }

        response = await fetch('/api/focus', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: button.session_id,
            revision: currentSnapshot.revision,
          }),
        })
      } else {
        response = await fetch('/api/intent', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            button_id: button.id,
            revision: currentSnapshot.revision,
          }),
        })
      }

      if (!response.ok) {
        throw new Error(await responseErrorMessage(response))
      }

      if (button.kind !== 'session') {
        const intent = (await response.json()) as DeckIntentResponse
        if (intent.snapshot !== undefined) installSnapshot(intent.snapshot)
      }

      deckActionError = ''
    } catch (error) {
      deckActionError = error instanceof Error ? error.message : 'Deck action failed'
    } finally {
      pendingButtonId = null
    }
  }

  onMount(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeRequest: AbortController | undefined

    async function poll(): Promise<void> {
      activeRequest = new AbortController()

      try {
        const response = await fetch('/api/snapshot', {
          headers: { Accept: 'application/json' },
          signal: activeRequest.signal,
        })

        if (!response.ok) {
          throw new Error(`Snapshot request failed (${response.status})`)
        }

        const nextSnapshot = (await response.json()) as DeckSnapshot

        if (!stopped) {
          installSnapshot(nextSnapshot)
        }
      } catch (error) {
        if (!stopped && !(error instanceof DOMException && error.name === 'AbortError')) {
          connectionState = 'error'
          errorMessage = error instanceof Error ? error.message : 'Snapshot unavailable'
        }
      } finally {
        if (!stopped) {
          timer = setTimeout(poll, POLL_INTERVAL_MS)
        }
      }
    }

    void poll()

    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      activeRequest?.abort()
    }
  })
</script>

<svelte:head>
  <title>elChango control surface</title>
  <meta
    name="description"
    content="Local control surface for monitored AI coding agent sessions."
  />
</svelte:head>

<main>
  <section class="instrument" aria-labelledby="deck-title">
    <header class="instrument__header">
      <div>
        <p class="eyebrow">Local agent surface</p>
        <h1 id="deck-title">elChango</h1>
      </div>

      <div class="connection" aria-live="polite">
        <span class={['connection__light', `connection__light--${connectionState}`]}></span>
        <span>{statusLabel}</span>
      </div>
    </header>

    <div class="deck-grid" aria-label="Stream Deck keys">
      {#each slots as button, index (`slot-${index}`)}
        <DeckKey
          {button}
          slot={index}
          busy={pendingButtonId !== null && pendingButtonId === button?.id}
          onactivate={() => void activateButton(button)}
        />
      {/each}
    </div>

    {#if connectionState === 'error'}
      <p class="instrument__error" role="status">{errorMessage}</p>
    {/if}

    {#if deckActionError}
      <p class="instrument__error" role="status">{deckActionError}</p>
    {/if}
  </section>
</main>

<style>
  main {
    display: grid;
    min-height: 100svh;
    place-items: center;
    padding: clamp(1rem, 4vw, 3rem);
  }

  .instrument {
    width: min(100%, 62rem);
    box-sizing: border-box;
    padding: clamp(1rem, 2.6vw, 2rem);
    border: 1px solid #34383d;
    border-radius: clamp(1rem, 2vw, 1.6rem);
    background: #0c0e10;
    box-shadow:
      0 1.25rem 3.5rem rgb(0 0 0 / 48%),
      inset 0 1px 0 #454a50;
  }

  .instrument__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: clamp(0.8rem, 2vw, 1.3rem);
  }

  .eyebrow {
    margin: 0 0 0.15rem;
    color: #78818a;
    font-family: var(--font-mono);
    font-size: 0.65rem;
    letter-spacing: 0.13em;
    text-transform: uppercase;
  }

  h1 {
    margin: 0;
    color: #f1f3f4;
    font-size: clamp(1.3rem, 3vw, 1.85rem);
    font-weight: 680;
    letter-spacing: -0.04em;
  }

  .connection {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: #a8afb5;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
  }

  .connection__light {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: #69717a;
  }

  .connection__light--connected {
    background: #55b677;
  }

  .connection__light--stale {
    background: #dda23d;
  }

  .connection__light--error {
    background: #dc5d59;
  }

  .deck-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: clamp(0.45rem, 1.4vw, 1rem);
    padding: clamp(0.65rem, 1.8vw, 1.25rem);
    border: 1px solid #25282c;
    border-radius: clamp(0.8rem, 1.5vw, 1.2rem);
    background: #070809;
  }

  .instrument__error {
    margin: 0.75rem 0 0;
    color: #d87976;
    font-family: var(--font-mono);
    font-size: 0.7rem;
  }

  @media (max-width: 38rem) {
    main {
      padding: 0.6rem;
    }

    .instrument {
      padding: 0.8rem;
      border-radius: 0.9rem;
    }
  }
</style>
