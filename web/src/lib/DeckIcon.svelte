<script lang="ts">
  import ArticleIcon from 'phosphor-svelte/lib/ArticleIcon'
  import ArrowLeftIcon from 'phosphor-svelte/lib/ArrowLeftIcon'
  import ArrowRightIcon from 'phosphor-svelte/lib/ArrowRightIcon'
  import ArrowsClockwiseIcon from 'phosphor-svelte/lib/ArrowsClockwiseIcon'
  import BellIcon from 'phosphor-svelte/lib/BellIcon'
  import BugIcon from 'phosphor-svelte/lib/BugIcon'
  import CheckIcon from 'phosphor-svelte/lib/CheckIcon'
  import ChecksIcon from 'phosphor-svelte/lib/ChecksIcon'
  import CodeIcon from 'phosphor-svelte/lib/CodeIcon'
  import DatabaseIcon from 'phosphor-svelte/lib/DatabaseIcon'
  import EyeIcon from 'phosphor-svelte/lib/EyeIcon'
  import FireIcon from 'phosphor-svelte/lib/FireIcon'
  import FireExtinguisherIcon from 'phosphor-svelte/lib/FireExtinguisherIcon'
  import FireTruckIcon from 'phosphor-svelte/lib/FireTruckIcon'
  import FlaskIcon from 'phosphor-svelte/lib/FlaskIcon'
  import GitBranchIcon from 'phosphor-svelte/lib/GitBranchIcon'
  import GitCommitIcon from 'phosphor-svelte/lib/GitCommitIcon'
  import GitPullRequestIcon from 'phosphor-svelte/lib/GitPullRequestIcon'
  import GlobeIcon from 'phosphor-svelte/lib/GlobeIcon'
  import MagnifyingGlassIcon from 'phosphor-svelte/lib/MagnifyingGlassIcon'
  import PackageIcon from 'phosphor-svelte/lib/PackageIcon'
  import PlusIcon from 'phosphor-svelte/lib/PlusIcon'
  import RobotIcon from 'phosphor-svelte/lib/RobotIcon'
  import RocketIcon from 'phosphor-svelte/lib/RocketIcon'
  import ShieldCheckIcon from 'phosphor-svelte/lib/ShieldCheckIcon'
  import ShieldIcon from 'phosphor-svelte/lib/ShieldIcon'
  import TerminalIcon from 'phosphor-svelte/lib/TerminalIcon'
  import TestTubeIcon from 'phosphor-svelte/lib/TestTubeIcon'
  import WrenchIcon from 'phosphor-svelte/lib/WrenchIcon'
  import type { DeckIconName } from './contracts'

  type PhosphorIconName = Exclude<DeckIconName, 'cursor' | 'claude'>

  const phosphorIcons = {
    plus: PlusIcon,
    'arrow-left': ArrowLeftIcon,
    'arrow-right': ArrowRightIcon,
    'arrows-clockwise': ArrowsClockwiseIcon,
    robot: RobotIcon,
    terminal: TerminalIcon,
    code: CodeIcon,
    bug: BugIcon,
    wrench: WrenchIcon,
    rocket: RocketIcon,
    shield: ShieldIcon,
    database: DatabaseIcon,
    globe: GlobeIcon,
    package: PackageIcon,
    'git-branch': GitBranchIcon,
    flask: FlaskIcon,
    check: CheckIcon,
    'git-pull-request': GitPullRequestIcon,
    'git-commit': GitCommitIcon,
    article: ArticleIcon,
    fire: FireIcon,
    'fire-extinguisher': FireExtinguisherIcon,
    'fire-truck': FireTruckIcon,
    'magnifying-glass': MagnifyingGlassIcon,
    bell: BellIcon,
    'test-tube': TestTubeIcon,
    checks: ChecksIcon,
    eye: EyeIcon,
    'shield-check': ShieldCheckIcon,
  } satisfies Record<PhosphorIconName, typeof PlusIcon>

  interface Props {
    name: DeckIconName
  }

  let { name }: Props = $props()
  let PhosphorIcon = $derived(
    name === 'cursor' || name === 'claude' ? null : phosphorIcons[name],
  )
</script>

{#if name === 'cursor'}
  <svg viewBox="0 0 466.73 532.09" fill="currentColor" aria-hidden="true" focusable="false">
    <path
      d="M457.43,125.94L244.42,2.96c-6.84-3.95-15.28-3.95-22.12,0L9.3,125.94c-5.75,3.32-9.3,9.46-9.3,16.11v247.99c0,6.65,3.55,12.79,9.3,16.11l213.01,122.98c6.84,3.95,15.28,3.95,22.12,0l213.01-122.98c5.75-3.32,9.3-9.46,9.3-16.11v-247.99c0-6.65-3.55-12.79-9.3-16.11h-.01ZM444.05,151.99l-205.63,356.16c-1.39,2.4-5.06,1.42-5.06-1.36v-233.21c0-4.66-2.49-8.97-6.53-11.31L24.87,145.67c-2.4-1.39-1.42-5.06,1.36-5.06h411.26c5.84,0,9.49,6.33,6.57,11.39h-.01Z"
    />
  </svg>
{:else if name === 'claude'}
  <svg viewBox="0 0 100 100" fill="currentColor" aria-hidden="true" focusable="false">
    <path
      d="m19.6 66.5 19.7-11 .3-1-.3-.5h-1l-3.3-.2-11.2-.3L14 53l-9.5-.5-2.4-.5L0 49l.2-1.5 2-1.3 2.9.2 6.3.5 9.5.6 6.9.4L38 49.1h1.6l.2-.7-.5-.4-.4-.4L29 41l-10.6-7-5.6-4.1-3-2-1.5-2-.6-4.2 2.7-3 3.7.3.9.2 3.7 2.9 8 6.1L37 36l1.5 1.2.6-.4.1-.3-.7-1.1L33 25l-6-10.4-2.7-4.3-.7-2.6c-.3-1-.4-2-.4-3l3-4.2L28 0l4.2.6L33.8 2l2.6 6 4.1 9.3L47 29.9l2 3.8 1 3.4.3 1h.7v-.5l.5-7.2 1-8.7 1-11.2.3-3.2 1.6-3.8 3-2L61 2.6l2 2.9-.3 1.8-1.1 7.7L59 27.1l-1.5 8.2h.9l1-1.1 4.1-5.4 6.9-8.6 3-3.5L77 13l2.3-1.8h4.3l3.1 4.7-1.4 4.9-4.4 5.6-3.7 4.7-5.3 7.1-3.2 5.7.3.4h.7l12-2.6 6.4-1.1 7.6-1.3 3.5 1.6.4 1.6-1.4 3.4-8.2 2-9.6 2-14.3 3.3-.2.1.2.3 6.4.6 2.8.2h6.8l12.6 1 3.3 2 1.9 2.7-.3 2-5.1 2.6-6.8-1.6-16-3.8-5.4-1.3h-.8v.4l4.6 4.5 8.3 7.5L89 80.1l.5 2.4-1.3 2-1.4-.2-9.2-7-3.6-3-8-6.8h-.5v.7l1.8 2.7 9.8 14.7.5 4.5-.7 1.4-2.6 1-2.7-.6-5.8-8-6-9-4.7-8.2-.5.4-2.9 30.2-1.3 1.5-3 1.2-2.5-2-1.4-3 1.4-6.2 1.6-8 1.3-6.4 1.2-7.9.7-2.6v-.2H49L43 72l-9 12.3-7.2 7.6-1.7.7-3-1.5.3-2.8L24 86l10-12.8 6-7.9 4-4.6-.1-.5h-.3L17.2 77.4l-4.7.6-2-2 .2-3 1-1 8-5.5Z"
    />
  </svg>
{:else if PhosphorIcon}
  <PhosphorIcon size="100%" weight="bold" aria-hidden="true" />
{/if}

<style>
  svg {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
