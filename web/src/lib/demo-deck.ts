import type {
  DeckButton,
  DeckButtonColor,
  DeckIconName,
  DeckSnapshot,
} from './contracts'

interface DemoSession {
  id: string
  providerId: string
  label: string
  detail: string
  icon: DeckIconName
  color: DeckButtonColor
  selected?: boolean
}

export const demoSessions = [
  {
    id: 'cursor:debug',
    providerId: 'cursor',
    label: 'Done',
    detail: 'Cursor',
    icon: 'bug',
    color: 'done',
    selected: false,
  },
  {
    id: 'cursor:log-analysis',
    providerId: 'cursor',
    label: 'Waiting',
    detail: 'Cursor',
    icon: 'eye',
    color: 'waiting',
    selected: false,
  },
  {
    id: 'demo:working',
    providerId: 'demo',
    label: 'Working',
    detail: 'Local agent',
    icon: 'code',
    color: 'working',
    selected: false,
  },
  {
    id: 'demo:lazy-coding',
    providerId: 'demo',
    label: 'Idle',
    detail: 'Local agent',
    icon: 'robot',
    color: 'idle',
    selected: false,
  },
  {
    id: 'demo:crash',
    providerId: 'demo',
    label: 'Error',
    detail: 'Local agent',
    icon: 'fire',
    color: 'error',
    selected: false,
  },
  {
    id: 'claude-code:vibecoding',
    providerId: 'claude-code',
    label: 'Claude',
    detail: 'Claude Code',
    icon: 'claude',
    color: 'idle',
    selected: false,
  },
  {
    id: 'cursor:monkeycoding',
    providerId: 'cursor',
    label: 'Cursor',
    detail: 'Cursor',
    icon: 'cursor',
    color: 'idle',
    selected: true,
  },
] as const satisfies readonly DemoSession[]

const sessionButtons: DeckButton[] = demoSessions.map((session, position) => ({
  id: `session:${session.id}`,
  position,
  kind: 'session',
  label: session.label,
  detail: session.detail,
  icon: session.icon,
  color: session.color,
  selected: session.selected,
  enabled: true,
  confidence: 'observed',
  provider_id: session.providerId,
  session_id: session.id,
  action: null,
}))

const availableButtons: DeckButton[] = Array.from(
  { length: 10 - sessionButtons.length },
  (_, offset) => {
    const position = offset + sessionButtons.length
    return {
      id: `empty:${position}`,
      position,
      kind: 'empty',
      label: 'Available',
      detail: 'No session',
      icon: 'plus',
      color: 'control',
      selected: false,
      enabled: true,
      confidence: 'observed',
      provider_id: null,
      session_id: null,
      action: 'choose_new_provider',
    }
  },
)

const controlButtons: DeckButton[] = [
  {
    id: 'control:refresh',
    position: 10,
    kind: 'control',
    label: 'Refresh',
    detail: 'Reorder by activity',
    icon: 'arrows-clockwise',
    color: 'control',
    selected: false,
    enabled: true,
    confidence: 'observed',
    provider_id: null,
    session_id: null,
    action: 'refresh_sessions',
  },
  {
    id: 'command:11:accept',
    position: 11,
    kind: 'control',
    label: 'Accept',
    detail: '',
    icon: 'check',
    color: 'control',
    selected: false,
    enabled: true,
    confidence: 'observed',
    provider_id: 'cursor',
    session_id: 'cursor:monkeycoding',
    action: 'execute_command',
    command_id: 'accept',
  },
  {
    id: 'command:12:commit_push',
    position: 12,
    kind: 'control',
    label: 'Commit Push',
    detail: '',
    icon: 'git-commit',
    color: 'control',
    selected: false,
    enabled: true,
    confidence: 'observed',
    provider_id: 'cursor',
    session_id: 'cursor:monkeycoding',
    action: 'execute_command',
    command_id: 'commit_push',
  },
  {
    id: 'command:13:create_pr',
    position: 13,
    kind: 'control',
    label: 'Open PR',
    detail: '',
    icon: 'git-pull-request',
    color: 'control',
    selected: false,
    enabled: true,
    confidence: 'observed',
    provider_id: 'cursor',
    session_id: 'cursor:monkeycoding',
    action: 'execute_command',
    command_id: 'create_pr',
  },
  {
    id: 'control:new',
    position: 14,
    kind: 'control',
    label: 'New',
    detail: 'Create agent',
    icon: 'plus',
    color: 'control',
    selected: false,
    enabled: true,
    confidence: 'observed',
    provider_id: null,
    session_id: null,
    action: 'choose_new_provider',
  },
]

export const demoSnapshot: DeckSnapshot = {
  revision: 1,
  observed_at_ms: 1_700_000_000_000,
  source: 'development demo fixture',
  read_only: true,
  selected_session_id: 'cursor:monkeycoding',
  page: 1,
  page_count: 1,
  has_previous: false,
  has_next: false,
  buttons: [...sessionButtons, ...availableButtons, ...controlButtons],
}
