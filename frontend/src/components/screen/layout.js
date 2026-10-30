// Turns the screen's variables into a reading order: tiers top to bottom, and inside a tier
// a few blocks that aggregate variables of the same kind (all single values in one table,
// all tables merged into one, lists together, trends together, images together).

export const TIERS = ['critical', 'important', 'background']

// Order of table columns across merged tables (screen-api.md, section 3)
const COLUMN_ORDER = ['item', 'strength', 'schedule', 'route', 'class', 'status', 'since', 'last_change', 'value', 'date']

// Variables without detail render as one row in the value table, whatever their output
const ROW_ONLY_STATES = new Set(['not_documented', 'not_available'])

function blockKind(v) {
  if (ROW_ONLY_STATES.has(v.state)) return 'values'
  if (v.output === 'image') return v.state === 'pending' || v.items?.some((i) => i.source?.image) ? 'images' : 'values'
  if (v.output === 'table') return v.state === 'pending' || v.table?.rows?.length ? 'tables' : 'values'
  if (v.output === 'list') return v.state === 'pending' || v.items?.length ? 'lists' : 'values'
  if (v.output === 'trend') return v.state === 'pending' || v.series?.some((s) => s.points.length) ? 'trends' : 'values'
  return 'values'
}

export function tableColumns(variables) {
  const cols = new Set(variables.flatMap((v) => v.table?.columns ?? []))
  if (!cols.size) cols.add('item')
  return [
    ...COLUMN_ORDER.filter((c) => cols.has(c)),
    ...[...cols].filter((c) => !COLUMN_ORDER.includes(c)),
  ]
}

// Variables (already in reading order) → blocks of one kind each, in order of first appearance.
export function groupIntoBlocks(variables, keyPrefix) {
  const blocks = []
  for (const v of variables) {
    const kind = blockKind(v)
    let block = blocks.find((b) => b.kind === kind)
    if (!block) blocks.push((block = { kind, key: `${keyPrefix}:${kind}`, variables: [] }))
    block.variables.push(v)
  }
  return blocks
}

export function groupByTier(variables) {
  const sorted = [...variables].sort((a, b) => a.priority - b.priority)
  return TIERS.map((tier) => {
    const members = sorted.filter((v) => (v.tier ?? 'important') === tier)
    return { tier, variables: members, blocks: groupIntoBlocks(members, tier) }
  }).filter((t) => t.variables.length)
}

// Contradiction IDs on a variable that no entry, row or point carries – shown on the label.
export function unplacedContradictions(v) {
  const placed = new Set()
  const mark = (x) => x?.contradiction_ids?.forEach((id) => placed.add(id))
  v.latest?.forEach(mark)
  v.history?.forEach(mark)
  v.items?.forEach(mark)
  v.table?.rows?.forEach(mark)
  v.series?.forEach((s) => s.points.forEach(mark))
  return (v.contradictions ?? []).filter((c) => !placed.has(c.contradiction_id))
}
