<template>
  <div class="image-group">
    <section v-for="v in variables" :key="v.name" class="image-var" :class="v.state">
      <h4 class="image-head">
        <span class="var-label">{{ v.label }}</span>
        <StateTag :state="v.state" :note="v.note" />
        <ContradictionBadge :refs="unplacedContradictions(v)" />
      </h4>
      <div v-if="v.state === 'pending'" class="skeleton image-skeleton" />
      <figure v-for="(it, i) in v.items ?? []" :key="it.fact_id ?? i" class="image-item">
        <figcaption :class="{ 'has-cx': it.contradiction_ids?.length }">
          <span class="item-text">{{ it.text }}</span>
          <span v-if="meta(it.source?.image)" class="image-meta">{{ meta(it.source.image) }}</span>
          <span class="date-cell">{{ formatDate(it.date) }}</span>
          <span class="icon-cell">
            <ContradictionBadge :ids="it.contradiction_ids ?? []" />
            <SourceButton :sources="it.source ? [it.source] : []" />
          </span>
        </figcaption>
        <ImageViewer v-if="it.source?.image" :image="it.source.image" :title="it.text" />
      </figure>
    </section>
  </div>
</template>

<script setup>
import ContradictionBadge from './ContradictionBadge.vue'
import SourceButton from './SourceButton.vue'
import StateTag from './StateTag.vue'
import ImageViewer from './ImageViewer.vue'
import { formatDate } from './format.js'
import { unplacedContradictions } from './layout.js'

// Imaging studies with an image: one figure per study, newest first – a caption line like a
// list item, then the preview that opens into the slices.
defineProps({ variables: { type: Array, required: true } })

function meta(image) {
  return [image?.modality, image?.body_site].filter(Boolean).join(' · ')
}
</script>

<style scoped>
.image-var + .image-var {
  margin-top: 14px;
}
.image-head {
  display: flex;
  align-items: center;
  gap: 2px;
  margin: 0 0 4px;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text);
}
.image-item {
  margin: 0;
}
.image-item + .image-item {
  margin-top: 12px;
}
figcaption {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
  padding: 4px 6px;
  font-size: 0.9rem;
  line-height: 1.4;
}
.image-meta {
  font-size: 0.78rem;
  color: var(--muted);
  white-space: nowrap;
}
.image-skeleton {
  width: 100%;
  max-width: 420px;
  height: 140px;
  border-radius: 8px;
}

@media (max-width: 600px) {
  figcaption {
    grid-template-columns: minmax(0, 1fr) auto auto;
  }
  .image-meta {
    display: none;
  }
}
</style>
