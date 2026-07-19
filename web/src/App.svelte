<script lang="ts">
  import { onMount } from 'svelte'
  import elChangoLogo from './assets/elchango-logo.png'
  import DeckKey from './lib/DeckKey.svelte'
  import type {
    DeckActivateRequest,
    DeckButton,
    DeckSnapshot,
  } from './lib/contracts'
  import { parseDeckActivateResponse, parseDeckSnapshot } from './lib/contracts'
  import { fetchWithTimeout, isAbortError } from './lib/http'

  type ConnectionState = 'connecting' | 'connected' | 'stale' | 'error'

  const WEB_CLIENT_ID = 'web'
  const POLL_INTERVAL_MS = 1_000
  const POLL_TIMEOUT_MS = 3_000
  const ACTION_TIMEOUT_MS = 10_000
  const STALE_AFTER_MS = 3_000
  const SLOT_COUNT = 15

  let snapshot = $state.raw<DeckSnapshot | null>(null)
  let connectionState = $state<ConnectionState>('connecting')
  let errorMessage = $state('')
  let pendingButtonId = $state<string | null>(null)
  let deckActionError = $state('')
  let activeActionRequest: AbortController | null = null

  const slots = $derived.by((): Array<DeckButton | null> => {
    return Array.from(
      { length: SLOT_COUNT },
      (_, index) =>
        snapshot?.buttons.find((button) => button.position === index) ?? null,
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
      snapshot &&
      (nextSnapshot.revision < snapshot.revision ||
        (nextSnapshot.revision === snapshot.revision &&
          nextSnapshot.observed_at_ms <= snapshot.observed_at_ms))
    ) {
      return
    }

    snapshot = nextSnapshot
    connectionState =
      Date.now() - nextSnapshot.observed_at_ms > STALE_AFTER_MS
        ? 'stale'
        : 'connected'
    errorMessage = ''
  }

  async function responseErrorMessage(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as unknown

      if (body && typeof body === 'object') {
        const { error, message, details } = body as Record<string, unknown>

        if (typeof error === 'string' && error.trim()) return error
        if (typeof message === 'string' && message.trim()) return message
        if (details && typeof details === 'object') {
          const { message: detailsMessage } = details as Record<string, unknown>

          if (typeof detailsMessage === 'string' && detailsMessage.trim())
            return detailsMessage
        }
      }
    } catch {
      // Use the status fallback when the response body is not JSON.
    }

    return `Deck action failed (${response.status})`
  }

  async function performButtonAction(
    button: DeckButton | null,
    endpoint: '/api/activate' | '/api/long-press',
  ): Promise<void> {
    const currentSnapshot = snapshot

    if (
      pendingButtonId !== null ||
      !currentSnapshot ||
      !button ||
      (!button.enabled && endpoint !== '/api/long-press')
    ) {
      return
    }

    pendingButtonId = button.id
    deckActionError = ''
    const actionRequest = new AbortController()
    activeActionRequest = actionRequest

    try {
      const request: DeckActivateRequest = {
        client_id: WEB_CLIENT_ID,
        button_id: button.id,
        revision: currentSnapshot.revision,
      }
      const response = await fetchWithTimeout(
        endpoint,
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request),
          signal: actionRequest.signal,
        },
        ACTION_TIMEOUT_MS,
        'Deck action timed out',
      )

      if (!response.ok) {
        throw new Error(await responseErrorMessage(response))
      }

      const result = parseDeckActivateResponse(await response.json())
      if (result.snapshot !== undefined) installSnapshot(result.snapshot)

      deckActionError = ''
    } catch (error) {
      if (!isAbortError(error)) {
        deckActionError =
          error instanceof Error ? error.message : 'Deck action failed'
      }
    } finally {
      if (activeActionRequest === actionRequest) activeActionRequest = null
      pendingButtonId = null
    }
  }

  function longPressHandler(
    button: DeckButton | null,
  ): (() => void) | undefined {
    const supportsLongPress =
      button !== null &&
      ((button.kind === 'session' && button.session_id != null) ||
        (button.position >= 11 &&
          button.position <= 13 &&
          button.action === 'execute_command'))

    return supportsLongPress
      ? () => void performButtonAction(button, '/api/long-press')
      : undefined
  }

  onMount(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeRequest: AbortController | undefined

    async function poll(): Promise<void> {
      activeRequest = new AbortController()

      try {
        const response = await fetchWithTimeout(
          `/api/snapshot?client_id=${encodeURIComponent(WEB_CLIENT_ID)}`,
          {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
            signal: activeRequest.signal,
          },
          POLL_TIMEOUT_MS,
          'Snapshot request timed out',
        )

        if (!response.ok) {
          throw new Error(`Snapshot request failed (${response.status})`)
        }

        const nextSnapshot = parseDeckSnapshot(await response.json())

        if (!stopped) {
          installSnapshot(nextSnapshot)
        }
      } catch (error) {
        if (!stopped && !isAbortError(error)) {
          connectionState = 'error'
          errorMessage =
            error instanceof Error ? error.message : 'Snapshot unavailable'
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
      activeActionRequest?.abort()
    }
  })
</script>

<svelte:head>
  <title>elChango</title>
  <meta
    name="description"
    content="Local control surface for monitored AI coding agent sessions."
  />
</svelte:head>

<main>
  <img class="project-logo" src={elChangoLogo} alt="elChango monkey logo" />

  <section class="instrument" aria-labelledby="deck-title">
    <header class="instrument__header">
      <h1 id="deck-title"><span aria-hidden="true">🐒</span> elChango</h1>

      <div class="connection" aria-live="polite">
        <span
          class={['connection__light', `connection__light--${connectionState}`]}
        ></span>
        <span>{statusLabel}</span>
      </div>
    </header>

    <div class="deck-grid" aria-label="Stream Deck keys">
      {#each slots as button, index (`slot-${index}`)}
        <DeckKey
          {button}
          slot={index}
          busy={pendingButtonId !== null && pendingButtonId === button?.id}
          onactivate={() => void performButtonAction(button, '/api/activate')}
          onlongpress={longPressHandler(button)}
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
    align-content: center;
    justify-items: center;
    padding: clamp(1rem, 4vw, 3rem);
  }

  .project-logo {
    display: block;
    width: clamp(8rem, 20vw, 13rem);
    height: auto;
    margin-bottom: clamp(1.25rem, 3vw, 2.25rem);
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
