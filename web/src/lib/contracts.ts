export type DeckButtonKind = 'session' | 'control' | 'empty'

export type DeckIconName =
  | 'repo'
  | 'terminal'
  | 'plus'
  | 'action'

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
  | 'new_session'
  | 'stop_session'
  | 'primary_action'
  | 'secondary_action'
  | null

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
  session_id?: string | null
  action?: DeckAction
}

export interface DeckSnapshot {
  revision: number
  observed_at_ms: number
  source: string
  read_only: boolean
  selected_session_id: string | null
  buttons: DeckButton[]
}
