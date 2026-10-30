<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="floating"
      class="popover"
      :class="{ wide }"
      role="dialog"
      @mouseenter="$emit('enter')"
      @mouseleave="$emit('leave')"
    >
      <slot />
    </div>
  </Teleport>
</template>

<script setup>
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { computePosition, autoUpdate, offset, flip, shift, size } from '@floating-ui/dom'

// Floating panel next to `anchor` (an element, or a virtual element with getBoundingClientRect).
const props = defineProps({
  open: { type: Boolean, default: false },
  anchor: { type: Object, default: null },
  placement: { type: String, default: 'bottom-start' },
  wide: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'enter', 'leave'])

const floating = ref(null)
let cleanup = null

function place() {
  if (!props.anchor || !floating.value) return
  computePosition(props.anchor, floating.value, {
    placement: props.placement,
    strategy: 'fixed',
    middleware: [
      offset(6),
      flip({ padding: 12 }),
      shift({ padding: 12 }),
      size({
        padding: 12,
        apply({ availableHeight, elements }) {
          elements.floating.style.maxHeight = `${Math.max(160, availableHeight)}px`
        },
      }),
    ],
  }).then(({ x, y }) => {
    if (floating.value) Object.assign(floating.value.style, { left: `${x}px`, top: `${y}px` })
  })
}

function onPointerDown(e) {
  if (floating.value?.contains(e.target)) return
  if (props.anchor instanceof Element && props.anchor.contains(e.target)) return
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}

function teardown() {
  cleanup?.()
  cleanup = null
  document.removeEventListener('pointerdown', onPointerDown, true)
  document.removeEventListener('keydown', onKey)
}

watch(
  () => [props.open, props.anchor],
  async ([open]) => {
    teardown()
    if (!open) return
    await nextTick()
    if (!floating.value || !props.anchor) return
    cleanup = props.anchor instanceof Element
      ? autoUpdate(props.anchor, floating.value, place)
      : (place(), null)
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKey)
  },
  { immediate: true },
)

onBeforeUnmount(teardown)
</script>

<style scoped>
.popover {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 100;
  width: max-content;
  max-width: min(420px, calc(100vw - 24px));
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 28px rgba(28, 32, 36, 0.16);
  padding: 12px 14px;
  font-size: 0.85rem;
  line-height: 1.45;
  color: var(--text);
  text-align: left;
}
.popover.wide {
  max-width: min(520px, calc(100vw - 24px));
}
</style>
