const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// "2016-04-15" → "15 Apr 2016", "2016-04" → "Apr 2016". Anything else is returned as is.
export function formatDate(iso) {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(iso)
  if (!m) return iso
  const month = MONTHS[Number(m[2]) - 1]
  return m[3] ? `${Number(m[3])} ${month} ${m[1]}` : `${month} ${m[1]}`
}

// ISO date (day or month precision) → epoch ms, for chart axes.
export function dateValue(iso) {
  const [y, mo = '01', d = '15'] = iso.split('-')
  return Date.UTC(Number(y), Number(mo) - 1, Number(d))
}

// Sources of a variable or entry list, one per passage.
export function uniqueSources(sources) {
  const seen = new Set()
  return (sources ?? []).filter((s) => {
    if (!s) return false
    const key = s.passage_id ?? `${s.doc_id}:${s.start}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export const KIND_LABEL = {
  laterality: 'Left / right mismatch',
  date: 'Date mismatch',
  value: 'Value mismatch',
  calculation: 'Calculation mismatch',
  presence: 'Present vs. absent',
  copy_mismatch: 'Copied text changed',
}
