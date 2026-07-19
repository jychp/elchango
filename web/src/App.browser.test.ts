import { flushSync, mount, unmount } from 'svelte'
import { afterEach, describe, expect, test, vi } from 'vitest'
import App from './App.svelte'
import type {
  DeckActivateResponse,
  DeckButton,
  DeckSnapshot,
} from './lib/contracts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
  document.body.replaceChildren()
})

describe('App demo mode', () => {
  test('renders the deterministic inert deck without API requests', async () => {
    vi.useFakeTimers()
    window.history.replaceState({}, '', '/?demo=true')
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const component = mount(App, { target: document.body })

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Connected'),
    )
    await vi.waitFor(() =>
      expect(document.body.querySelectorAll('button')).toHaveLength(15),
    )

    const expectedSessions = [
      ['Debug', 'Cursor', 'bug', 'done'],
      ['Log analysis', 'Cursor', 'eye', 'waiting'],
      ['Vibecoding', 'Claude Code', 'claude', 'working'],
      ['Monkeycoding', 'Cursor', 'cursor', 'working'],
      ['Lazy Coding', 'Local agent', 'robot', 'idle'],
    ] as const

    for (const [label, detail, icon, color] of expectedSessions) {
      const key = requiredButton(`${label}, ${detail}`)
      expect(key.dataset.icon).toBe(icon)
      expect(key.classList).toContain(`deck-key--${color}`)
      expect(key.classList).not.toContain('deck-key--disabled')
      expect(key.getAttribute('aria-disabled')).toBe('true')
    }

    await vi.advanceTimersByTimeAsync(5_000)
    for (const key of document.body.querySelectorAll('button')) key.click()

    expect(fetchMock).not.toHaveBeenCalled()
    unmount(component)
  })
})

describe('App safety behavior', () => {
  test('times out polling and recovers on the next successful snapshot', async () => {
    vi.useFakeTimers()
    let snapshotRequests = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        snapshotRequests += 1
        if (snapshotRequests === 1)
          return abortablePendingResponse(init?.signal)
        return Promise.resolve(
          jsonResponse(snapshot({ revision: 2, label: 'Recovered' })),
        )
      }),
    )
    const component = mount(App, { target: document.body })

    await vi.advanceTimersByTimeAsync(3_000)
    flushSync()
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Snapshot request timed out'),
    )

    await vi.advanceTimersByTimeAsync(1_000)
    flushSync()
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Connected'),
    )
    expect(document.body.textContent).toContain('Recovered')
    unmount(component)
  })

  test('rejects malformed snapshots and recovers without replacing the last valid deck', async () => {
    vi.useFakeTimers()
    const responses: unknown[] = [
      snapshot({ revision: 2, label: 'Current' }),
      { revision: 3, buttons: [] },
      snapshot({ revision: 3, label: 'Recovered' }),
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(responses.shift()))),
    )
    const component = mount(App, { target: document.body })

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Current'),
    )

    await vi.advanceTimersByTimeAsync(1_000)
    flushSync()
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain(
        'snapshot must contain exactly 15 buttons',
      ),
    )
    expect(document.body.textContent).toContain('Current')

    await vi.advanceTimersByTimeAsync(1_000)
    flushSync()
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Connected'),
    )
    expect(document.body.textContent).toContain('Recovered')
    unmount(component)
  })

  test('ignores a stale snapshot', async () => {
    vi.useFakeTimers()
    const responses = [
      snapshot({ revision: 4, observedAt: Date.now(), label: 'Current' }),
      snapshot({ revision: 3, observedAt: Date.now() + 1_000, label: 'Stale' }),
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(responses.shift()))),
    )
    const component = mount(App, { target: document.body })

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Current'),
    )
    await vi.advanceTimersByTimeAsync(1_000)
    flushSync()

    expect(document.body.textContent).toContain('Current')
    expect(document.body.textContent).not.toContain('Stale')
    unmount(component)
  })

  test('serializes actions while a request is pending', async () => {
    const initial = snapshot({ revision: 1, label: 'Run' })
    let resolveAction: ((response: Response) => void) | undefined
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/snapshot')) {
        return Promise.resolve(jsonResponse(initial))
      }
      if (init?.method === 'POST') {
        return new Promise<Response>((resolve) => {
          resolveAction = resolve
        })
      }
      throw new Error(`Unexpected request: ${String(input)}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const component = mount(App, { target: document.body })
    await vi.waitFor(() => expect(document.body.textContent).toContain('Run'))
    const key = requiredButton('Run, Cursor')

    key.click()
    key.click()
    await vi.waitFor(() => expect(postCalls(fetchMock)).toHaveLength(1))

    expect(postCalls(fetchMock)).toHaveLength(1)
    resolveAction?.(
      jsonResponse(
        actionResponse(
          snapshot({ revision: 2, observedAt: Date.now() + 1, label: 'Done' }),
        ),
      ),
    )
    await vi.waitFor(() => expect(document.body.textContent).toContain('Done'))
    unmount(component)
  })

  test('times out an action and allows a later retry', async () => {
    vi.useFakeTimers()
    const initial = snapshot({ revision: 1, label: 'Run' })
    let actionRequests = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).startsWith('/api/snapshot')) {
          return Promise.resolve(jsonResponse(initial))
        }
        actionRequests += 1
        if (actionRequests === 1) return abortablePendingResponse(init?.signal)
        return Promise.resolve(
          jsonResponse(
            actionResponse(
              snapshot({
                revision: 2,
                observedAt: Date.now() + 1,
                label: 'Retried',
              }),
            ),
          ),
        )
      }),
    )
    const component = mount(App, { target: document.body })
    await vi.waitFor(() => expect(document.body.textContent).toContain('Run'))
    const key = requiredButton('Run, Cursor')

    key.click()
    await vi.advanceTimersByTimeAsync(10_000)
    flushSync()
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Deck action timed out'),
    )

    key.click()
    await vi.waitFor(() => expect(actionRequests).toBe(2))
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('Retried'),
    )
    unmount(component)
  })
})

function snapshot({
  revision,
  observedAt = Date.now(),
  label,
}: {
  revision: number
  observedAt?: number
  label: string
}): DeckSnapshot {
  return {
    revision,
    observed_at_ms: observedAt,
    source: 'test',
    read_only: false,
    selected_session_id: 'session-1',
    page: 0,
    page_count: 1,
    has_previous: false,
    has_next: false,
    buttons: Array.from({ length: 15 }, (_, position) =>
      position === 0 ? activeButton(label) : emptyButton(position),
    ),
  }
}

function activeButton(label: string): DeckButton {
  return {
    id: 'cursor:session-1',
    position: 0,
    kind: 'session',
    label,
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
}

function emptyButton(position: number): DeckButton {
  return {
    id: `empty:${position}`,
    position,
    kind: 'empty',
    label: '',
    detail: '',
    icon: 'plus',
    color: 'control',
    selected: false,
    enabled: false,
    confidence: 'observed',
    provider_id: null,
    session_id: null,
    action: null,
  }
}

function actionResponse(nextSnapshot: DeckSnapshot): DeckActivateResponse {
  return {
    accepted: true,
    action: 'focus_session',
    snapshot: nextSnapshot,
    focus: { accepted: true },
  }
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function abortablePendingResponse(
  signal?: AbortSignal | null,
): Promise<Response> {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener(
      'abort',
      () => reject(new DOMException('The operation was aborted', 'AbortError')),
      { once: true },
    )
  })
}

function requiredButton(name: string): HTMLButtonElement {
  const button = Array.from(document.body.querySelectorAll('button')).find(
    (candidate) => candidate.getAttribute('aria-label') === name,
  )
  if (!button) throw new Error(`Button "${name}" was not rendered`)
  return button
}

function postCalls(mock: ReturnType<typeof vi.fn>): unknown[][] {
  return mock.mock.calls.filter(
    ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
  )
}
