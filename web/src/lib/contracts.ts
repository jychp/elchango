export type DeckButtonKind = 'session' | 'control' | 'empty'

export type DeckIconName =
  | 'cursor'
  | 'claude'
  | 'plus'
  | 'arrow-left'
  | 'arrow-right'
  | 'arrows-clockwise'
  | 'robot'
  | 'terminal'
  | 'code'
  | 'bug'
  | 'wrench'
  | 'rocket'
  | 'shield'
  | 'database'
  | 'globe'
  | 'package'
  | 'git-branch'
  | 'flask'
  | 'check'
  | 'git-pull-request'
  | 'git-commit'
  | 'article'

export type DeckButtonColor =
  | 'idle'
  | 'working'
  | 'waiting'
  | 'done'
  | 'error'
  | 'unknown'
  | 'control'

export type DeckButtonConfidence = 'observed' | 'candidate' | 'persisted' | 'unknown'

export type DeckAction =
  | 'choose_new_provider'
  | 'cancel_new_session'
  | 'new_session'
  | 'refresh_sessions'
  | 'previous_page'
  | 'next_page'
  | 'choose_session_icon'
  | 'set_session_icon'
  | 'choose_slot_command'
  | 'set_slot_command'
  | 'cancel_picker'
  | 'previous_picker_page'
  | 'next_picker_page'
  | 'execute_command'

export interface DeckButton {
  id: string
  position: number
  kind: DeckButtonKind
  label: string
  detail: string
  icon: DeckIconName
  color: DeckButtonColor
  selected: boolean
  enabled: boolean
  confidence: DeckButtonConfidence
  provider_id: string | null
  session_id?: string | null
  action?: DeckAction | null
}

export interface DeckSnapshot {
  revision: number
  observed_at_ms: number
  source: string
  read_only: boolean
  selected_session_id: string | null
  page: number
  page_count: number
  has_previous: boolean
  has_next: boolean
  buttons: DeckButton[]
}

export interface DeckActivateRequest {
  client_id: 'web'
  button_id: string
  revision: number
}

export interface DeckActivateResponse {
  accepted: boolean
  action?: DeckAction | 'focus_session'
  snapshot?: DeckSnapshot
  focus?: Record<string, unknown>
  launch?: Record<string, unknown>
}
