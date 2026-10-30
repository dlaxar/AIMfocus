<template>
  <article class="chat-turn">
    <p class="question">{{ turn.question }}</p>

    <div class="reply">
      <p v-if="turn.answer" class="answer">
        <span v-if="turn.answer.state === 'not_documented'" class="answer-tag">Not documented</span>
        {{ turn.answer.answer }}<StateTag :state="turn.answer.state" :note="turn.answer.note" />
        <SourceButton v-if="turn.answer.sources?.length" :sources="turn.answer.sources" />
      </p>
      <div v-else-if="!turn.error" class="answer thinking">
        <AIMfocusChase />
      </div>
      <p v-if="turn.error" class="error" role="alert">{{ turn.error }}</p>

      <!-- Cards: the variables that answer the question, rendered like the screen -->
      <div v-if="blocks.length" class="cards">
        <div v-for="b in blocks" :key="b.key" class="block" :class="`block-${b.kind}`">
          <component :is="BLOCKS[b.kind]" :variables="b.variables" />
        </div>
      </div>
      <p v-else-if="!turn.done" class="cards-hint muted">Looking for cards…</p>
    </div>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import StateTag from './StateTag.vue'
import SourceButton from './SourceButton.vue'
import ValueTable from './ValueTable.vue'
import DataTable from './DataTable.vue'
import ListGroup from './ListGroup.vue'
import TrendGroup from './TrendGroup.vue'
import ImageGroup from './ImageGroup.vue'
import AIMfocusChase from '../aimfocus-chase/AIMfocusChase.vue'
import { groupIntoBlocks } from './layout.js'

// One question with its text answer and cards.
// turn: { id, question, answer: chat reply | null, cards: ScreenVariable[], done, error }
const props = defineProps({
  turn: { type: Object, required: true },
})

const BLOCKS = { values: ValueTable, tables: DataTable, lists: ListGroup, trends: TrendGroup, images: ImageGroup }

const blocks = computed(() => groupIntoBlocks(
  [...props.turn.cards].sort((a, b) => a.priority - b.priority),
  `chat${props.turn.id}`,
))
</script>

<style scoped>
.chat-turn {
  display: flex;
  flex-direction: column;
  gap: 10px;
  scroll-margin-top: 80px;
}

.question {
  align-self: flex-end;
  max-width: 80%;
  margin: 0;
  padding: 8px 14px;
  border-radius: 16px 16px 4px 16px;
  background: var(--accent);
  color: #fff;
  font-size: 0.92rem;
  line-height: 1.45;
  white-space: pre-wrap;
}

.reply {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.answer {
  margin: 0;
  font-size: 0.92rem;
  line-height: 1.5;
}
.answer :deep(.source-btn-wrap) {
  margin-left: 4px;
  vertical-align: -2px;
}
.answer-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  vertical-align: 1px;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
}
.thinking {
  max-width: 320px;
}

.cards {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--row-line);
}
.block + .block {
  margin-top: 14px;
}
.cards-hint {
  margin: 10px 0 0;
  font-size: 0.8rem;
}

.error { margin: 6px 0 0; color: #ce2c31; font-size: 0.88rem; }
.muted { color: var(--muted); }
</style>
